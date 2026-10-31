"""2.14.0: the waiting list (Lista d'attesa). A client asks "Avvisami se si libera" on the booking page, or the desk adds
him: a service, an operator or anyone, a day or some days, a time window and how many. When a place frees on those days
(a booking cancelled, moved away or closed as absent, a pause or a closure removed, the catalog changed) the entries
become due and the worker (mail_worker) looks at them under the business lock, with the rules of the online page:
- "Avvisa tutti" (default): every waiting client gets the free times of the first day with something new, once per
  time, and the link of the booking page already on that day; the first who books takes it, the others see what is left;
- "Uno alla volta": the first in line gets one place kept for him for wait_minutes (it counts as taken for the others,
  booking.capacity_available); not taken in time, it goes to the next.
Name and contacts are sealed like a booking's; an entry goes away when its last day has passed (privacy.apply_retention)."""
import hashlib
from contextvars import ContextVar
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Min,Q
from django.utils import timezone
from .models import Tenant,Service,Resource,Contact,WaitEntry,audit
from .crypto import encrypt
from .calendar import local_datetime
from .booking import BookingError,choice,details,integer,refused,available,bounds
from .middleware import operational
from .tenancy import scope

SALT="waitlist"
MAX_DAYS=31     # the days one entry may wait for
LINK_DAYS=35    # the link of an offer: the entry is gone by then anyway
WAIT_MINUTES=[15,60,120]
# the entry whose own kept place does not count against it, on the page of its link (public.slots_api)
OWN=ContextVar("own_hold",default=None)

def freed(tenant,start=None,end=None):
    """A place given up between start and end: the waiting entries on those days are looked at again; without dates
    (the catalog changed, a pause or a closure removed) all of them.
    shortcut: every service of those days, since an operator freed for one service is free for his others too."""
    rows=WaitEntry.objects.filter(status="waiting")
    if start:
        tz=ZoneInfo(tenant.timezone)
        rows=rows.filter(first__lte=(end-timedelta(microseconds=1)).astimezone(tz).date(),last__gte=start.astimezone(tz).date())
    if rows.update(due=True):Tenant.objects.filter(pk=tenant.pk).update(wait_check=timezone.now())

def holds(service,start,end,resource=None,occurrence=None):
    """The places kept for the first in line over start-end, as the booking sweep counts them: one operator, one date
    of an event, or the places of a service."""
    out=[];own=OWN.get()
    for e in WaitEntry.objects.filter(held_until__gt=timezone.now()).exclude(pk=own):
        h=e.snapshot.get("hold") or {}
        a,b=datetime.fromisoformat(h["busy_start"]),datetime.fromisoformat(h["busy_end"])
        if not (a<end and start<b):continue
        if resource:hit,n=h["resource"]==str(resource.pk),1
        elif occurrence:hit,n=h["occurrence"]==str(occurrence.pk),h["party"]
        else:hit,n=h["service"]==str(service.pk) and not h["resource"],h["party"]
        if hit:out+=[(max(a,start),n),(min(b,end),-n)]
    return out

def claim(tenant,wait):
    """The client of the entry books from its link: the place kept for him is his (in the booking's transaction: if
    the booking fails, it stays kept)."""
    e=WaitEntry.objects.select_for_update().filter(pk=wait,status="waiting").first()
    if e and e.held_until:
        e.held_until=None;e.snapshot.pop("hold",None);e.save(update_fields=["held_until","snapshot"])

def booked(tenant,booking,wait=""):
    """A booking made: the entries of the same client for that service and day are done, and the one of the link."""
    day=booking.start.astimezone(ZoneInfo(tenant.timezone)).date()
    q=Q(contact_id=booking.contact_id,service_id=booking.service_id,first__lte=day,last__gte=day) if booking.contact_id else Q(pk__in=[])
    if wait:q|=Q(pk=wait)
    WaitEntry.objects.filter(q,status="waiting").update(status="done",held_until=None,due=False)

def key(slot):return slot["start"]+"|"+slot["resource"]+"|"+slot["occurrence"]

def free(tenant,e,seen=(),now=None):
    """The free times of the first day of the entry with something it was not told yet, by the rules of the online
    page (lead time, horizon, people per booking), inside its time window; for a stay, the stay itself."""
    s=e.service;tz=ZoneInfo(tenant.timezone);now=now or timezone.now()
    if not (s.active and s.location.active):return []
    resource=str(e.resource_id or "")
    try:
        if s.kind=="day":
            start=local_datetime(e.first,s.checkin,tz)
            a,b,*_,unit,_,_=bounds(tenant,s,{"start":start.isoformat(),"end_date":e.last.isoformat(),"party":e.party,"resource":resource})
            slots=[{"start":a.astimezone(tz).isoformat(),"end":b.astimezone(tz).isoformat(),"resource":str(unit.pk) if unit else "","occurrence":""}]
            return [x for x in slots if key(x) not in seen]
        d=max(e.first,now.astimezone(tz).date())
        while d<=e.last:
            slots=[x for x in available(tenant,s.pk,d,None,resource or None,e.party) if key(x) not in seen and inside(e,datetime.fromisoformat(x["start"]).astimezone(tz).time())]
            if slots:return slots
            d+=timedelta(days=1)
    except BookingError:pass
    return []

def inside(e,t):return (e.time_from is None or t>=e.time_from) and (e.time_to is None or t<e.time_to)

def link(tenant,e,slot):
    token=signing.dumps({"e":str(e.pk),"d":slot["start"][:10]},salt=SALT)
    return f"{settings.PUBLIC_ORIGIN}/book/{tenant.slug}/?attesa={token}"

def run(now=None):
    """The worker's turn: the businesses with something due or a kept place that expires. Returns the emails queued."""
    now=now or timezone.now();sent=0
    for tenant in Tenant.objects.filter(wait_check__lte=now):
        if not operational(tenant):continue
        with scope(tenant),transaction.atomic():
            sent+=step(Tenant.objects.select_for_update().get(pk=tenant.pk),now)
    return sent

def step(tenant,now):
    from .client_mails import offer
    queue=tenant.wait_mode=="queue";sent=0;tz=ZoneInfo(tenant.timezone)
    # a kept place not taken in time goes back, and so to the next in line
    expired=list(WaitEntry.objects.filter(held_until__lte=now))
    for e in expired:
        e.held_until=None;e.snapshot.pop("hold",None);e.save(update_fields=["held_until","snapshot"])
    if expired:WaitEntry.objects.filter(status="waiting").update(due=True)
    rows=WaitEntry.objects.filter(status="waiting",due=True,last__gte=now.astimezone(tz).date()).select_related("service__location","resource","contact","tenant").order_by("created")
    for e in rows:
        e.due=False
        if not (queue and e.held_until):
            seen=set(e.snapshot.get("seen",[]))
            slots=free(tenant,e,seen,now)
            if queue and slots:
                slots=slots[:1];x=slots[0];s=e.service
                a=datetime.fromisoformat(x["start"]);b=datetime.fromisoformat(x["end"])
                e.held_until=now+timedelta(minutes=tenant.wait_minutes)
                e.snapshot["hold"]={"service":str(s.pk),"resource":x["resource"],"occurrence":x["occurrence"],"party":e.party,
                    "busy_start":(a-timedelta(minutes=s.buffer_before)).isoformat(),"busy_end":(b+timedelta(minutes=s.buffer_after)).isoformat()}
            if slots:
                # once per free time: the newest 300 are remembered, the entry lives a month at most
                e.snapshot["seen"]=(e.snapshot.get("seen",[])+[key(x) for x in slots])[-300:]
                e.snapshot["free"]=[x["start"] for x in slots[:6]];e.snapshot["told"]=now.isoformat()
                sent+=bool(offer(tenant,e,slots,link(tenant,e,slots[0]),e.held_until))
        e.save(update_fields=["due","held_until","snapshot"])
    nxt=WaitEntry.objects.filter(held_until__isnull=False).aggregate(m=Min("held_until"))["m"]
    Tenant.objects.filter(pk=tenant.pk).update(wait_check=nxt)
    return sent

def opened(tenant,token):
    """The entry and the day of an offer's link, or None (expired, done, or not of this business)."""
    try:data=signing.loads(token or "",salt=SALT,max_age=LINK_DAYS*86400);day=date.fromisoformat(data["d"])
    except (signing.BadSignature,KeyError,ValueError,TypeError):return None
    e=WaitEntry.objects.filter(pk=data["e"],status="waiting").select_related("service","resource").first()
    return (e,day) if e and e.tenant_id==tenant.pk else None

def clean(tenant,data,desk=False):
    """Service, operator, days, time window and people of an entry, checked."""
    s=choice(Service,data.get("service"),active=True,location__active=True)
    if s.kind not in ("resource","capacity","shift","event","day"):raise BookingError("Servizio non valido.")
    _,resource,_=details(s,None,data.get("resource") or None)
    party=integer(data.get("party",1),"Persone",1,1000 if desk else s.max_party)
    tz=ZoneInfo(tenant.timezone);today=timezone.now().astimezone(tz).date()
    try:
        first=date.fromisoformat(str(data.get("first","")))
        last=date.fromisoformat(str(data.get("last") or first))
    except ValueError:raise BookingError("Data non valida.")
    if first<today:raise BookingError("Scegli un giorno da oggi in poi.")
    if s.kind=="day" and last<=first:raise BookingError("Scegli la data di partenza.")
    if last<first:raise BookingError("Il giorno finale viene prima del primo.")
    if (last-first).days>MAX_DAYS:raise BookingError(f"Al massimo {MAX_DAYS} giorni.")
    window=[]
    for k in ("time_from","time_to"):
        try:window.append(time.fromisoformat(data[k]) if data.get(k) and s.kind!="day" else None)
        except ValueError:raise BookingError("Orario non valido.")
    if all(window) and window[0]>=window[1]:raise BookingError("L'orario finale viene prima di quello iniziale.")
    return {"service":s,"resource":resource,"first":first,"last":last,"time_from":window[0],"time_to":window[1],"party":party}

def public_contact(tenant,data,service):
    """Name and contacts typed online: email required, phone optional. The card is found as an online booking finds it,
    never rewritten; a new one is made otherwise. A client the business does not book online is refused."""
    from .clients import by_phone,email_index,phone_index,seal_card,clean
    pii=clean({k:v if isinstance(v,str) else "" for k,v in data.items() if k in ("first_name","last_name","email","phone")})
    if not pii["email"]:raise BookingError("Scrivi la tua email: l'avviso arriva lì.")
    contact=Contact.objects.filter(email_index=email_index(tenant,pii["email"])).first() or by_phone(tenant,pii["phone"],without_email=True)
    if (contact and contact.online_blocked) or (pii["phone"] and Contact.objects.filter(phone_index=phone_index(tenant,pii["phone"]),online_blocked=True).exists()):
        raise refused(tenant,service)
    if not contact:
        contact=Contact(tenant=tenant,email_index=email_index(tenant,pii["email"]))
        seal_card(tenant,contact,pii);contact.save()
    return contact,pii

def add(tenant,data,actor=None,contact=None):
    """A new entry, online (actor None: the notice box ticked, recorded as for a booking) or from the desk (the card
    chosen, or found or made from the data typed). The same client already waiting for that service on those days
    keeps his entry."""
    from .clients import clean as staff_clean,find_or_create
    with transaction.atomic():
        tenant=Tenant.objects.select_for_update().get(pk=tenant.pk)
        w=clean(tenant,data,desk=actor is not None)
        if actor is None:
            if data.get("consent") is not True:raise BookingError("Spunta la casella per ricevere l'avviso.")
            contact,pii=public_contact(tenant,data,w["service"])
        elif contact:
            d=contact.data;pii={k:d.get(k,"") for k in ("first_name","last_name","email","phone")}
        else:
            pii=staff_clean(data);contact,_=find_or_create(tenant,pii)
        same=WaitEntry.objects.filter(contact=contact,service=w["service"],status="waiting",first__lte=w["last"],last__gte=w["first"]).first()
        if same:return same,False
        e=WaitEntry(tenant=tenant,contact=contact,**w)
        text=tenant.privacy_text+"\n"+NOTICE
        e.snapshot={"consent":{"at":timezone.now().isoformat(),"channel":"desk" if actor else "public","text_sha256":hashlib.sha256(text.encode()).hexdigest()},
            **({"actor":str(actor.pk)} if actor else {})}
        e.pii=encrypt(tenant,pii,"wait:"+str(e.pk));e.save()
        audit(tenant,actor,"waitlist.added",e.pk,channel=e.snapshot["consent"]["channel"])
        return e,True
NOTICE="Avvisami per email se si libera un posto per questa richiesta."

def close(tenant,e,actor,remove=False):
    """Done (the client booked or does not want it any more) or removed: a place kept for him goes to the next."""
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        held=bool(e.held_until)
        if remove:e.delete()
        else:WaitEntry.objects.filter(pk=e.pk).update(status="done",held_until=None,due=False)
        if held:freed(tenant)
        audit(tenant,actor,"waitlist.removed" if remove else "waitlist.done",e.pk)
