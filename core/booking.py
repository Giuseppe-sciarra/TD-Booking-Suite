import json,re,secrets
from collections import Counter
from datetime import datetime,date,timedelta,time,timezone as TZ
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext,get_language
from .models import Tenant,Service,Resource,ServiceResource,Option,Occurrence,Booking,Contact,ActionToken,Delivery,audit
from .crypto import encrypt,index,tenant_key,token_hash
from .calendar import windows,fits,local_datetime,blocked_stay,blocked
from .middleware import operational
UTC=TZ.utc
class BookingError(ValueError):pass
class ForceWarning(BookingError):
    """2.14.0: Forza at the desk: what the booking goes beyond, said before it is saved (force_ok confirms it)."""
# 2.14.0: the statuses. A request waiting for the business's yes holds its place as a confirmed booking does
HOLDING=("pending","confirmed","arrived","in_progress","completed","no_show")
OPEN=("pending","confirmed","arrived","in_progress")  # not closed yet
ACCEPTED=("confirmed","arrived","in_progress","completed")  # the business said yes and the client did not give up
MOVABLE=("pending","confirmed")
# where each status may go; a closed one goes back to confirmed (a click on the wrong row, a client who comes after all)
MOVES={"pending":{"confirmed","cancelled"},"confirmed":{"arrived","in_progress","completed","no_show","cancelled"},
    "arrived":{"in_progress","completed","cancelled"},"in_progress":{"completed","cancelled"},
    "completed":{"confirmed"},"no_show":{"confirmed"},"cancelled":{"confirmed"}}
UNDO_MINUTES=5
REASONS=[("client","Disdetta del cliente"),("business","Imprevisto dell'attività"),("weather","Maltempo"),("duplicate","Prenotazione doppia"),("other","Altro")]
REASON_LABELS={**dict(REASONS),"rejected":"Richiesta rifiutata","unconfirmed":"Non confermata dal cliente"}
# 2.14.0: what a booking that changes day or time gets again: reminders, message before the arrival, the client's answer
REARM=("reminded","reminded2","arrival","asked","client_confirmed","declined")
def integer(value,label,minimum=1,maximum=10000):
    if isinstance(value,bool) or not re.fullmatch(r"\d+",str(value)):raise BookingError(label+" non valido.")
    val=int(value)
    if not minimum<=val<=maximum:raise BookingError(label+" fuori intervallo.")
    return val
def choice(model,value,**filters):
    source=model.objects if hasattr(model,"objects") else model  # model or pre-scoped queryset
    try:obj=source.filter(pk=value,**filters).first()
    except (ValidationError,ValueError):obj=None
    if not obj:raise BookingError(gettext("Selezione non valida o non disponibile."))
    return obj
def details(service,option_id=None,resource_id=None):
    option=choice(Option,option_id,service=service,active=True) if option_id else None
    # 2.14.0: a cabin or machine is never the one booked, only taken with him (support)
    resource=choice(Resource,resource_id,active=True,secondary=False,location=service.location) if resource_id else None
    if resource and not ServiceResource.objects.filter(service=service,resource=resource).exists():raise BookingError("La risorsa non appartiene al servizio.")
    if service.kind not in ("resource","day") and resource:raise BookingError("Risorsa non prevista.")
    return option,resource,length(service,option,resource)
def length(service,option=None,resource=None,party=1):
    """Minutes of an appointment: the variant's; otherwise the operator's own for this service (2.11.0, Giulia does
    the colour in 75 minutes); otherwise the service's. 2.14.0: a table for 2 frees sooner than one for 6 (party_durations)."""
    if option:return option.duration
    if resource is not None and service.kind=="resource":
        own=ServiceResource.objects.filter(service=service,resource=resource).values_list("duration",flat=True).first()
        if own:return own
    if service.kind=="capacity" and service.turnover:
        return next((m for n,m in sorted(service.party_durations or []) if party<=n),service.duration)
    return service.duration
def over(events,party,cap):
    """The sweep of the places: (moment, +people at the start, -people at the end); True when party does not fit."""
    occupied=0
    for _,delta in sorted(events,key=lambda x:(x[0],x[1])):
        occupied+=delta
        if occupied+party>cap:return True
    return party>cap
def spans(rows,start,end,each=None):
    return [e for r in rows for n in (each or r.party,) for e in ((max(r.busy_start,start),n),(min(r.busy_end,end),-n))]
def shared(service,start,end,party,exclude=None):
    """2.14.0: the covers of the whole place, taken together by every restaurant service there (lunch and the group
    menu): the tightest limit wins. The words of the limit passed, empty when it fits."""
    place=service.location
    if service.kind not in ("capacity","shift") or not place.capacity:return ""
    rows=Booking.objects.filter(status__in=HOLDING,busy_start__lt=end,busy_end__gt=start,location=place,service__kind__in=("capacity","shift")).exclude(pk=exclude)
    return f"oltre i coperti del locale ({place.capacity})" if over(spans(rows,start,end),party,place.capacity) else ""
def paced(service,start,party,exclude=None):
    """2.14.0: the rhythm of the arrivals, for the kitchen: at each time of the page (every `interval` minutes from
    midnight) at most pace_covers people and pace_bookings tables. The words of the limit passed, empty when it fits."""
    if service.kind!="capacity" or not (service.pace_covers or service.pace_bookings):return ""
    local=start.astimezone(ZoneInfo(service.tenant.timezone))
    a=start-timedelta(minutes=(local.hour*60+local.minute)%service.interval,seconds=local.second,microseconds=local.microsecond)
    rows=list(Booking.objects.filter(service=service,status__in=HOLDING,start__gte=a,start__lt=a+timedelta(minutes=service.interval)).exclude(pk=exclude).values_list("party",flat=True))
    if service.pace_covers and sum(rows)+party>service.pace_covers or service.pace_bookings and len(rows)+1>service.pace_bookings:
        return f"oltre il ritmo degli arrivi delle {a.astimezone(local.tzinfo):%H:%M}"
    return ""
def capacity_available(service,start,end,party,resource=None,occurrence=None,exclude=None,blocks=True,online=False,rooms=True,gap=None):
    """online (2.14.0): the places sold online (online_capacity), the rest kept for the desk; rooms: the covers of the
    whole place (shared), left out where the caller says them apart (Forza). gap: the posa of the booking being
    checked: the operator only needs to be free before and after it."""
    if gap and resource:return all(capacity_available(service,x,y,party,resource,occurrence,exclude,blocks,online,rooms) for x,y in ((start,gap[0]),(gap[1],end)))
    # 2.14.0: an operator's pause, a turn closed for the night: the time is taken as by a booking (Forza says it apart)
    if blocks and blocked(service.tenant,start,end,resource,service):return False
    rows=Booking.objects.filter(status__in=HOLDING,busy_start__lt=end,busy_end__gt=start)
    if exclude:rows=rows.exclude(pk=exclude)
    # 2.14.0: a cabin or a machine is also taken by the bookings that need it next to their operator
    if resource:rows=rows.filter(Q(resource=resource)|Q(secondary=resource));cap=1;party=1
    elif occurrence:rows=rows.filter(occurrence=occurrence);cap=occurrence.capacity
    else:
        rows=rows.filter(service=service);cap=service.capacity
        if online and service.online_capacity:cap=min(cap,service.online_capacity)
    events=[]
    for row in rows:
        n=1 if resource else row.party
        lo,hi=max(row.busy_start,start),min(row.busy_end,end)
        # 2.14.0: inside another client's posa the operator is free (the cabin is not: the client is still there)
        if resource and row.gap_start and row.resource_id==resource.pk and row.gap_start<=lo and hi<=row.gap_end:continue
        events.extend([(lo,n),(hi,-n)])
    if service.tenant.wait_mode=="queue":
        from .waitlist import holds
        events+=holds(service,start,end,resource,occurrence)  # 2.14.0: places kept for the first in the waiting list
    if over(events,party,cap):return False
    if occurrence and service.kind=="shift":return capacity_available(service,start,end,party,exclude=exclude,blocks=False,online=online,rooms=rooms)
    return bool(resource) or not rooms or not shared(service,start,end,party,exclude)
def linked(service,secondary=False):
    """The active operators, rooms or units of a service, in name order; secondary (2.14.0): the cabins, machines,
    cars it also needs, any one of them."""
    return Resource.objects.filter(active=True,secondary=secondary,location=service.location,id__in=ServiceResource.objects.filter(service=service).values("resource_id")).order_by("name")
def posa(service,start,end,resource):
    """2.14.0: the posa of an appointment (work, posa, finish): the client stays, the operator is free for another."""
    if not (resource and service.kind=="resource" and service.pause_minutes):return None
    a=start+timedelta(minutes=service.pause_start);b=a+timedelta(minutes=service.pause_minutes)
    return (a,b) if start<a and b<end else None
def support(service,bs,be,exclude=None,pool=None):
    """2.14.0: the first free cabin or machine the service needs; None when it needs none, False when all are taken."""
    pool=list(linked(service,True)) if pool is None else pool
    if not pool:return None
    return next((r for r in pool if capacity_available(service,bs,be,1,r,exclude=exclude)),False)
def ranked(service,resources,day,prefer=None):
    """2.14.0: who gets "Il primo disponibile", as its location says; those excluded from it stay out.
    prefer: the operator of the client's last visit (last_operator), first when the location assigns so."""
    way=service.location.assign;resources=[r for r in resources if not r.auto_excluded]
    if way=="even":
        a=local_datetime(day,time(0),ZoneInfo(service.tenant.timezone))
        load=Counter(Booking.objects.filter(resource__in=resources,status__in=HOLDING,start__gte=a,start__lt=a+timedelta(days=1)).values_list("resource_id",flat=True))
        return sorted(resources,key=lambda r:(load[r.pk],r.name))
    if way=="priority":return sorted(resources,key=lambda r:(r.priority,r.name))
    if way=="last" and prefer:return sorted(resources,key=lambda r:(r.pk!=prefer,r.name))
    return resources
def last_operator(tenant,service,pii,data,staff):
    """2.14.0: the operator of the client's last visit, for the locations that give it back to him."""
    if service.location.assign!="last" or data.get("resource"):return None
    rows=Booking.objects.filter(resource__isnull=False,status__in=ACCEPTED)
    try:
        if staff and data.get("contact"):return rows.filter(contact_id=data["contact"]).order_by("-start").values_list("resource_id",flat=True).first()
        if pii.get("email"):return rows.filter(contact__email_index=index(pii["email"],tenant_key(tenant),"email")).order_by("-start").values_list("resource_id",flat=True).first()
    except (ValidationError,ValueError):pass
    return None
def kit(service,start,end,bs,be,resource,issues,exclude=None):
    """2.14.0: the posa and the cabin or machine of an appointment; none of them free is said as Forza says the rest."""
    second=support(service,bs,be,exclude) if service.kind=="resource" else None
    if second is False:issues.append("nessuna risorsa libera tra "+", ".join(linked(service,True).values_list("name",flat=True)));second=None
    return posa(service,start,end,resource) or (None,None),second
def holes(rows,bs,be,minutes):
    """2.14.0: a time that would leave less than `minutes` free next to another appointment of the operator.
    shortcut: the opening hours' edges do not count, only the appointments; upgrade if holes at opening matter."""
    n=timedelta(minutes=minutes)
    return any(timedelta(0)<bs-e<n or timedelta(0)<s-be<n for s,e in rows)
def has_units(service):
    return service.kind=="day" and ServiceResource.objects.filter(service=service).exists()
def free_units(tenant,service,bs,be,party,resource=None,exclude=None):
    """Named units of a day service free for the whole interval, in name order."""
    units=linked(service)
    if resource:units=units.filter(pk=resource.pk)
    return [u for u in units if (u.capacity==0 or u.capacity>=party)
        and capacity_available(service,bs,be,1,resource=u,exclude=exclude)
        and not blocked_stay(tenant,bs,be,service,u)]
def candidates(tenant,service,day,option=None,resource=None,desk=False,force=False,party=1):
    """desk (2.14.0): lead time, horizon and last arrival are the public page's, the desk books from now on;
    force: Forza lists every date of an event of that day, also started, full or outside the hours."""
    now=timezone.now()
    duration=timedelta(minutes=length(service,option,resource,party))
    before=timedelta(minutes=service.buffer_before);after=timedelta(minutes=service.buffer_after)
    lead=now if desk else now+timedelta(minutes=service.lead_minutes)
    horizon=now+timedelta(days=730 if desk else service.horizon_days)
    tz=ZoneInfo(tenant.timezone)
    if service.kind in ("event","shift"):
        rows=Occurrence.objects.filter(service=service,active=True,start__lte=horizon)
        for event in (rows if force else rows.filter(start__gte=lead)).order_by("start"):
            if event.start.astimezone(tz).date()==day and (force or fits(tenant,event.start-before,event.end+after,service)):
                yield event.start,event.end,event
        return
    if service.kind=="day":return
    whole=service.kind=="capacity" and not service.turnover
    last=timedelta(minutes=service.last_arrival if service.kind=="capacity" and not desk else 0)  # 2.14.0: no table offered at closing time
    for a,b in windows(tenant,day,service,resource):
        tick=a
        while tick<b:
            # without time slots a booking holds its places until the window closes: lunch and dinner each have their seats
            end=b-after if whole else tick+duration
            if tick.astimezone(tz).date()==day and lead<=tick<=horizon and tick-before>=a and end+after<=b and tick<=b-last:
                yield tick,end,None
            tick+=timedelta(minutes=service.interval)
def available(tenant,service_id,day,option_id=None,resource_id=None,party=1,exclude=None,allowed=None,desk=False,force=False,prefer=None):
    """Free slots of a day. allowed: resource ids a scoped staff member may book ("Il primo disponibile" stays among them).
    desk and force: see candidates; the desk also books groups bigger than the online limit (max_party), within the places.
    2.14.0, restaurants: the covers of the whole place; online the places sold online and the rhythm of the arrivals
    (at the desk too with pace_desk). prefer: see ranked."""
    service=choice(Service,service_id,active=True,location__active=True)
    option,resource,_=details(service,option_id,resource_id)
    party=integer(party,"Quantità",1,1000 if desk else service.max_party)
    pace=not desk or service.pace_desk
    tz=ZoneInfo(tenant.timezone)
    today=timezone.now().astimezone(tz).date()
    if (day<today and not force) or day>today+timedelta(days=730 if desk else min(service.horizon_days,730)):return []
    resources=[resource]
    if service.kind=="resource" and not resource:
        resources=list(linked(service))
        if allowed is not None:resources=[r for r in resources if str(r.pk) in allowed]
        resources=ranked(service,resources,day,prefer)
    rank={str(r.pk) if r else "":i for i,r in enumerate(resources)}
    pool=list(linked(service,True)) if service.kind=="resource" else []
    gap=service.location.min_gap if service.kind=="resource" and not desk else 0
    if gap:a=local_datetime(day,time(0),tz);a,b=a-timedelta(days=1),a+timedelta(days=2)
    result=[]
    for res in resources:
        near=list(Booking.objects.filter(resource=res,status__in=HOLDING,busy_start__lt=b,busy_end__gt=a).exclude(pk=exclude).values_list("busy_start","busy_end")) if gap else []
        for start,end,event in candidates(tenant,service,day,option,res,desk,force,party):
            bs=start-timedelta(minutes=service.buffer_before);be=end+timedelta(minutes=service.buffer_after)
            if force or (capacity_available(service,bs,be,party,res,event,exclude,online=not desk,gap=posa(service,start,end,res)) and not (pace and paced(service,start,party,exclude)) and support(service,bs,be,exclude,pool) is not False and not (gap and holes(near,bs,be,gap))):
                result.append({"start":start.astimezone(tz).isoformat(),"end":end.astimezone(tz).isoformat(),"resource":str(res.pk) if res else "","resource_name":res.name if res else "","occurrence":str(event.pk) if event else ""})
    result=sorted(result,key=lambda x:(x["start"],rank[x["resource"]]))
    if service.kind=="resource" and not resource:
        # "Il primo disponibile": one slot per time, the first free resource; cut after this, or many operators hide the evening
        seen=set();result=[x for x in result if not (x["start"] in seen or seen.add(x["start"]))]
    return result[:500]
def normalize_phone(value):
    """2.6.1: Italian numbers as people write them. 333 123 4567, 06 1234567, 0039..., +39... all become +39...;
    a foreign number keeps its own prefix. Empty when it cannot be a phone number."""
    raw=re.sub(r"[\s\-./()]","",value or "")
    if raw.startswith("00"):raw="+"+raw[2:]
    if not raw.startswith("+"):raw="+39"+raw
    return raw if re.fullmatch(r"\+[1-9]\d{6,14}",raw) else ""
def personal(data,service,staff=False,walkin=False):
    if staff:
        # 2.7.0: a booking taken at the desk or on the phone: surname and email are optional, phone or email is enough
        # 2.14.0: a client without appointment may leave nothing at all
        from .clients import clean
        result=clean({**data,"first_name":data.get("first_name") or "Cliente di passaggio"} if walkin else data,require_contact=not walkin)
        return {**result,**extras(data,service)}
    result={}
    for key in ("first_name","last_name","email","phone"):
        value=data.get(key,"")
        if not isinstance(value,str):raise BookingError("Recapito non valido: "+key)
        val=value.strip()
        if not val or len(val)>(254 if key=="email" else 100) or any(ord(c)<32 for c in val):raise BookingError(gettext("Compila correttamente nome, cognome, email e telefono."))
        result[key]=val
    try:validate_email(result["email"])
    except ValidationError:raise BookingError(gettext("Email non valida."))
    result["email"]=result["email"].casefold()
    result["phone"]=normalize_phone(result["phone"])
    if not result["phone"]:raise BookingError(gettext("Numero di telefono non valido, per esempio 333 123 4567."))
    return {**result,**extras(data,service)}
def extras(data,service):
    """Notes and the service's own questions, the same for online and staff bookings."""
    result={}
    if not isinstance(data.get("notes",""),str):raise BookingError("Note non valide.")
    result["notes"]=data.get("notes","").strip()
    if len(result["notes"])>4000:raise BookingError("Note troppo lunghe.")
    # 2.8.0: allergies and intolerances, only from the list the business asks about
    asked=service.tenant.asked_allergens
    picked=(data.get("allergies") or []) if asked else []
    if not isinstance(picked,list) or any(not isinstance(a,str) or a not in asked for a in picked):raise BookingError("Intolleranze non valide: ricarica la pagina e riprova.")
    if picked:result["allergies"]=[a for a in asked if a in picked]
    answers=data.get("answers",{})
    if not isinstance(answers,dict):raise BookingError("Campi aggiuntivi non validi.")
    result["answers"]={}
    for field in service.fields:
        val=answers.get(field["key"],"")
        if field["type"]=="checkbox":
            if val=="" or val is None:val=False
            if type(val) is not bool:raise BookingError("Valore sì/no non valido: "+field["label"])
            if field.get("required") and not val:raise BookingError("Compila: "+field["label"])
        elif field.get("required") and (val is None or val==""):
            raise BookingError("Compila: "+field["label"])
        if field["type"]=="checkbox":pass
        elif field["type"]=="select":
            if val and val not in field["choices"]:raise BookingError("Scelta non valida: "+field["label"])
        elif field["type"]=="number":
            if val not in ("",None):val=integer(val,field["label"],0,100000)
        else:
            if not isinstance(val,str):raise BookingError("Testo non valido: "+field["label"])
            if len(val)>500:raise BookingError("Campo troppo lungo.")
        result["answers"][field["key"]]=val
    return result
def bounds(tenant,service,data,exclude=None,desk=False,force=False,prefer=None):
    """Where and when a booking goes, checked. desk (2.14.0): the rules of the public page (lead time, horizon, last
    arrival, people per booking) do not bind the staff. force: Forza at the desk, any time typed in (in the business's
    time); what it goes beyond comes back in the last value, in words, instead of a refusal (stays excluded)."""
    option,resource,duration=details(service,data.get("option"),data.get("resource"))
    party=integer(data.get("party",1),"Quantità",1,1000 if desk else service.max_party)
    tz=ZoneInfo(tenant.timezone);now=timezone.now()
    try:start=datetime.fromisoformat(str(data["start"]))
    except (KeyError,ValueError):raise BookingError(gettext("Data o ora non valida."))
    if force and start.tzinfo is None:start=local_datetime(start.date(),start.time(),tz)
    if start is None:raise BookingError(gettext("Data o ora non valida."))
    if start.tzinfo is None:raise BookingError("Fuso orario mancante.")
    start=start.astimezone(UTC)
    day=start.astimezone(tz).date();occurrence=None;issues=[]
    before=timedelta(minutes=service.buffer_before);after=timedelta(minutes=service.buffer_after)
    # 2.14.0: an appointment dragged or stretched in the agenda keeps its own length: every rule checked one by one,
    # as with Forza, so a refusal says why (slots of the standard length would not match)
    minutes=integer(data["minutes"],"Durata",5,1440) if desk and service.kind=="resource" and data.get("minutes") not in (None,"") else None
    if service.kind=="day":
        try:end_day=date.fromisoformat(str(data["end_date"]))
        except (ValueError,KeyError):raise BookingError(gettext("Seleziona la data di partenza."))
        nights=(end_day-day).days
        # 2.14.0: 0 nights minimum is the rental of a day, picked up and returned the same day (end>start below)
        if 0<=nights<service.min_days:raise BookingError(f"Durata minima: {service.min_days} giorni.")
        if nights>service.max_days:raise BookingError(f"Durata massima: {service.max_days} giorni.")
        from .stays import restricted
        if why:=restricted(service,day,end_day):raise BookingError(why)
        clock_in=service.checkin if isinstance(service.checkin,time) else time.fromisoformat(service.checkin)
        clock_out=service.checkout if isinstance(service.checkout,time) else time.fromisoformat(service.checkout)
        expected=local_datetime(day,clock_in,tz);end=local_datetime(end_day,clock_out,tz)
        if not expected or not end or start!=expected.astimezone(UTC):raise BookingError("Ora di arrivo non valida.")
        end=end.astimezone(UTC)
        if end<=start:raise BookingError(gettext("La partenza viene prima dell'arrivo: scegli un'altra data di partenza."))
        if (day<now.astimezone(tz).date()) if desk else not now+timedelta(minutes=service.lead_minutes)<=start<=now+timedelta(days=service.horizon_days):raise BookingError(gettext("Data fuori dall'intervallo prenotabile."))
        if not fits(tenant,start,start+timedelta(microseconds=1),service,purpose="arrival") or not fits(tenant,end,end+timedelta(microseconds=1),service,purpose="departure") or blocked_stay(tenant,start,end,service):
            raise BookingError(gettext("Arrivo, partenza o soggiorno non consentiti dal calendario."))
        bs=start-before;be=end+after
        if has_units(service):
            # The same named unit for every night of the stay.
            free=free_units(tenant,service,bs,be,party,resource,exclude)
            if not free:raise BookingError("Nessuna unità disponibile per le date scelte." if not resource else "L'unità scelta non è disponibile per queste date.")
            resource=free[0]
        elif not capacity_available(service,bs,be,party,exclude=exclude):raise BookingError(gettext("Disponibilità esaurita."))
    elif force or minutes:
        if service.kind in ("event","shift"):
            occurrence=choice(Occurrence,data.get("occurrence"),service=service,active=True);start=occurrence.start
        elif service.kind=="resource" and not resource:
            # "Il primo disponibile" at the time typed in: the first one free then, otherwise the desk chooses with whom
            for r in ranked(service,list(linked(service)),day,prefer):
                e=start+timedelta(minutes=length(service,option,r))
                if fits(tenant,start-before,e+after,service,r) and capacity_available(service,start-before,e+after,party,r,exclude=exclude,gap=posa(service,start,e,r)):resource=r;break
            else:raise BookingError(gettext("Nessuno è libero a quell'ora: scegli con chi."))
        if occurrence:end=occurrence.end
        elif service.kind=="capacity" and not service.turnover:
            # a table holds its places until that opening window closes, as on the page
            end=next((b-after for a,b in windows(tenant,day,service) if a<=start<b),start+timedelta(minutes=service.duration))
        else:end=start+timedelta(minutes=minutes or length(service,option,resource,party))
        bs=start-before;be=end+after
        if start<now-timedelta(minutes=1):issues.append("nel passato")
        if not fits(tenant,bs,be,service,resource):issues.append("fuori dall'orario di apertura")
        block=blocked(tenant,bs,be,resource,service)
        if block:issues.append(f"nell'orario bloccato di {resource.name} ({block.get_reason_display().lower()})" if block.resource_id else "in un turno chiuso")
        if not capacity_available(service,bs,be,party,resource,occurrence,exclude,blocks=False,rooms=False,gap=posa(service,start,end,resource)):
            issues.append(f"sovrapposta a un altro appuntamento di {resource.name}" if resource else f"oltre i posti disponibili ({occurrence.capacity if occurrence else service.capacity})")
        if not resource:issues+=[x for x in (shared(service,bs,be,party,exclude),service.pace_desk and paced(service,start,party,exclude)) if x]
    else:
        slots=available(tenant,service.pk,day,data.get("option"),data.get("resource"),party,exclude,desk=desk,prefer=prefer)
        match=next((s for s in slots if datetime.fromisoformat(s["start"]).astimezone(UTC)==start and (not data.get("occurrence") or s["occurrence"]==str(data["occurrence"]))),None)
        if not match:raise BookingError(gettext("Orario non disponibile. Aggiorna le disponibilità."))
        resource=choice(Resource,match["resource"]) if match["resource"] else None
        occurrence=choice(Occurrence,match["occurrence"]) if match["occurrence"] else None
        end=datetime.fromisoformat(match["end"]).astimezone(UTC)
        bs=start-before;be=end+after
    return start,end,bs,be,party,option,resource,occurrence,issues
def overrun(issues,data):
    """2.14.0: what a forced booking or a client without appointment goes beyond: without Forza a refusal, with Forza
    a warning until the desk confirms it."""
    if not issues:return
    if data.get("force") is not True:raise BookingError("Adesso non si può: "+", ".join(issues)+". Scegli con chi o chiedi a chi può forzare.")
    if data.get("force_ok") is not True:raise ForceWarning("Stai forzando: "+", ".join(issues)+". Se va bene, conferma.")
def needs_request(service,party,pii,contact):
    """2.14.0: an online booking waits for the business's yes when its service says so (immediate confirmation stays
    the default), or when its client did not come as many times as the business says (Al banco)."""
    n=service.tenant.absences_request
    return (service.request_all or 0<service.request_above<party or (service.request_allergies and bool(pii.get("allergies")))
        or bool(n and contact and contact.pk and Booking.objects.filter(contact=contact,status="no_show").count()>=n))
def refused(tenant,service):
    """2.14.0: the words for a client the business made not bookable online: neutral, with the phone to call."""
    phone=service.location.phone
    return BookingError("Per prenotare contatta l'attività"+(f": {phone}." if phone else "."))
def action_token(tenant,kind,contact=None,booking=None,days=30):
    token=secrets.token_urlsafe(32)
    ActionToken.objects.create(tenant=tenant,digest=token_hash(token),kind=kind,contact=contact,booking=booking,expires=timezone.now()+timedelta(days=days))
    return token
def queue(tenant,payload,contact=None,campaign=None):
    delivery=Delivery(tenant=tenant,contact=contact,campaign=campaign,next_attempt=timezone.now())
    delivery.payload=encrypt(tenant,payload,"delivery:"+str(delivery.pk));delivery.save()
    return delivery
def create_booking(tenant,data,actor=None,origin="public",source="",wait="",series=None):
    """origin and source (2.14.0): the channel of an online booking, from the signed ticket of its page (public.py);
    the staff's bookings are the desk's, or of a client without appointment (walkin: now, already in progress).
    wait: the waiting-list entry whose link opened the page: the place kept for it is its own (waitlist.claim).
    series (2.14.0): a date repeated from the desk: tied to the others, without its own email and notice."""
    # All inventory writers take the same stable tenant row lock.
    with transaction.atomic():
        tenant=Tenant.objects.select_for_update().get(pk=tenant.pk)
        if not operational(tenant):raise BookingError(gettext("Prenotazioni non disponibili."))
        if actor is None and tenant.receive_schedule and not fits(tenant,timezone.now(),timezone.now()+timedelta(seconds=1),purpose="receive"):
            raise BookingError(gettext("Le richieste online sono accettate soltanto negli orari indicati dall'attività."))
        key=str(data.get("request_key",""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}",key):raise BookingError("Identificativo richiesta mancante.")
        fingerprint=index(json.dumps(data,sort_keys=True,separators=(",",":")),tenant_key(tenant),"booking-request")
        existing=Booking.objects.filter(request_key=key).first()
        if existing:
            if existing.request_fingerprint!=fingerprint:raise BookingError("Richiesta già usata con dati differenti.")
            return existing,False
        service=choice(Service,data.get("service"),active=True,location__active=True)
        staff=actor is not None
        walkin=staff and data.get("walkin") is True
        pii=personal(data,service,staff,walkin)
        from .waitlist import claim,booked
        if wait:claim(tenant,wait)
        when={**data,"start":timezone.now().replace(second=0,microsecond=0).isoformat()} if walkin else data
        start,end,bs,be,party,option,resource,event,issues=bounds(tenant,service,when,desk=staff,force=staff and (walkin or data.get("force") is True),prefer=last_operator(tenant,service,pii,data,staff))
        gap,second=kit(service,start,end,bs,be,resource,issues)
        overrun(issues,data)
        known_by_phone=None
        if staff:
            # 2.7.0: the client picked from the card, or found by email or phone, or a new card
            from .clients import find_or_create
            if data.get("contact"):
                contact=choice(Contact.objects.exclude(email_index__startswith="erased:"),data.get("contact"));new_contact=False
            elif walkin and not (pii["email"] or pii["phone"]):contact,new_contact=None,False  # passing by: no card
            else:contact,new_contact=find_or_create(tenant,pii)
            if not pii.get("email"):data={**data,"marketing":False}
        else:
            idx=index(pii["email"],tenant_key(tenant),"email")
            contact=Contact.objects.filter(email_index=idx).first()
            # 2.8.0: not found by email, the same phone is the client added at the desk without email: one card,
            # one history. A card with another email stays another person. The card is never rewritten here
            from .clients import by_phone
            if contact is None:known_by_phone=by_phone(tenant,pii["phone"],without_email=True)
            contact=contact or known_by_phone
            # 2.14.0: Non prenotabile online, also with another email on the phone of a card the business marked
            from .clients import phone_index
            if (contact and contact.online_blocked) or Contact.objects.filter(phone_index=phone_index(tenant,pii["phone"]),online_blocked=True).exists():raise refused(tenant,service)
            from .clients import category_refusal
            why=category_refusal(tenant,contact,start,timezone.now())  # 2.14.0: Socio, Ospite: days ahead, bookings at once
            if why:raise BookingError(why)
            new_contact=contact is None
            if new_contact:
                from .clients import seal_card
                contact=Contact(tenant=tenant,email_index=idx)
                seal_card(tenant,contact,{k:pii[k] for k in ("first_name","last_name","email","phone")})
        # An unverified public request never rewrites an existing contact card,
        # its consent state or its proof: the declared data stay in the booking.
        # a card found by phone has no email: this request cannot confirm marketing for it
        consent_requested=(data.get("marketing") is True and contact.consent!="yes" and not contact.suppressed and not known_by_phone
            and not ActionToken.objects.filter(contact_id=contact.pk,kind="consent",used=False,expires__gt=timezone.now()).exists())
        if consent_requested and new_contact:contact.consent="pending"
        # 2.13.0: the box "send me the emails about this booking"; missing (older forms, the plugin) means yes
        notices=data.get("notices") is not False
        if new_contact:contact.notices=notices
        if contact:contact.save()
        status="in_progress" if walkin else "pending" if not staff and needs_request(service,party,pii,contact) else "confirmed"
        booking=Booking(tenant=tenant,service=service,location=service.location,resource=resource,option=option,occurrence=event,contact=contact,start=start,end=end,busy_start=bs,busy_end=be,party=party,request_key=key,request_fingerprint=fingerprint,
            status=status,origin=("walkin" if walkin else "import" if origin=="import" else "desk") if staff else origin,source="" if staff else source,series=series,snapshot={"service":service.name,"location":service.location.name,"resource":resource.name if resource else "","option":option.name if option else "","field_labels":{x["key"]:x["label"] for x in service.fields},
            "policy":{"cancel_cutoff_minutes":service.cancel_cutoff_minutes,"min_days":service.min_days}})
        if not notices:booking.snapshot["notices"]=False
        # 2.14.0: the language of the client's page, for the emails that follow (client_mails.send)
        if not staff and get_language()!="it":booking.snapshot["lang"]=get_language()
        if issues:booking.snapshot["forced"]=issues
        (booking.gap_start,booking.gap_end),booking.secondary=gap,second
        if second:booking.snapshot["secondary"]=second.name
        # 2.14.0: "Il primo disponibile": the client may move it to whoever is free then (public.py)
        if resource and not data.get("resource"):booking.snapshot["any_resource"]=True
        from .prices import stamp
        stamp(booking)  # 2.11.0: price, client discount and what he pays, as at this moment
        booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk));booking.save()
        token=action_token(tenant,"cancel",contact,booking,days=max(30,service.horizon_days+service.max_days+30))
        pii["cancel_token"]=token;booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk));booking.save(update_fields=["pii"])
        from django.conf import settings
        from .client_mails import send
        # 2.14.0: the appointments imported from the old program were already agreed: no email, no notice to the staff
        if not walkin and not series and booking.origin!="import":send(tenant,booking,"requested" if status=="pending" else "confirmed")
        if consent_requested:
            token=action_token(tenant,"consent",contact=contact,booking=booking,days=7)
            booking.snapshot["consent_request"]={"text":tenant.consent_text,"requested":timezone.now().isoformat(),"source":"booking"}
            booking.save(update_fields=["snapshot"])
            from .privacy import record
            record(tenant,contact,"requested","booking",actor,tenant.consent_text,booking.pk)
            queue(tenant,{"to":pii["email"],"subject":gettext("Conferma iscrizione")+" · "+tenant.name,"body":gettext("Conferma la richiesta di iscrizione alle comunicazioni di {attivita}:").format(attivita=tenant.name)+f"\n{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/\n"+gettext("Se non hai richiesto l'iscrizione, ignora il messaggio.")},contact)
        from .notify import notify_staff
        if not series and booking.origin!="import":notify_staff(tenant,booking,"requested" if status=="pending" else "created")
        booked(tenant,booking,wait)
        from .api import emit
        emit(tenant,booking,"created")
        audit(tenant,actor,"booking.created",booking.pk,origin=booking.origin,**({"forced":issues} if issues else {}))
        return booking,True
def move_booking(tenant,booking_id,data,actor,desk=True):
    """desk=False (2.14.0): the client from his link, with the rules of the public page and no Forza."""
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        booking=choice(Booking,booking_id,status__in=MOVABLE)
        service=choice(Service,data.get("service",booking.service_id),active=True,location__active=True)
        # 2.14.0: moving is the desk's, with the desk's rules and Forza
        *values,issues=bounds(tenant,service,data,exclude=booking.pk,desk=desk,force=desk and data.get("force") is True)
        gap,second=kit(service,*values[:4],values[6],issues,booking.pk)
        overrun(issues,data)
        key=lambda b:(b.start,b.party,b.option_id,b.resource_id,b.occurrence_id,b.service_id)
        before=key(booking);left=(booking.start,booking.end)
        booking.start,booking.end,booking.busy_start,booking.busy_end,booking.party,booking.option,booking.resource,booking.occurrence=values
        booking.service=service
        (booking.gap_start,booking.gap_end),booking.secondary=gap,second
        booking.snapshot["secondary"]=second.name if second else ""
        # 2.14.0: only the end changed (stretched in the agenda): for the client nothing moved, no email, the reminders
        # and his Confermo stay
        moved=key(booking)!=before
        booking.snapshot.pop("forced",None)
        if issues:booking.snapshot["forced"]=issues
        booking.location=service.location
        booking.snapshot.update(service=service.name,location=service.location.name,resource=booking.resource.name if booking.resource else "",option=booking.option.name if booking.option else "")
        from .prices import stamp
        stamp(booking)
        if moved:
            for name in REARM:booking.snapshot.pop(name,None)  # 2.13.0: the new day gets its own reminder
        booking.save()
        ActionToken.objects.filter(booking=booking,kind="cancel",used=False).update(expires=booking.end+timedelta(days=30))
        if not moved:
            audit(tenant,actor,"booking.resized",booking.pk,minutes=int((booking.end-booking.start).total_seconds()//60),**({"forced":issues} if issues else {}))
            return booking
        # Confermo / Non vengo of the old day must not answer for the new one
        ActionToken.objects.filter(booking=booking,kind__in=("confirm","decline"),used=False).update(expires=timezone.now())
        from .waitlist import freed
        freed(tenant,*left)  # 2.14.0: the place it left, to the waiting list
        from .client_mails import send
        send(tenant,booking,"moved")
        from .notify import notify_staff
        notify_staff(tenant,booking,"moved")
        from .api import emit
        emit(tenant,booking,"moved")
        audit(tenant,actor,"booking.moved",booking.pk,**({"forced":issues} if issues else {}))
        return booking
def can_undo(booking,now=None):
    """2.14.0: the last change of status can be taken back for a few minutes, where it was made."""
    last=booking.snapshot.get("undo")
    return bool(last) and last["to"]==booking.status and (now or timezone.now())<=datetime.fromisoformat(last["at"])+timedelta(minutes=UNDO_MINUTES)
def change_status(tenant,booking,status,actor=None,reason="",note="",undo=False,quiet=False):
    """2.14.0: a request accepted or refused, arrived, in progress, done, absent, cancelled with its reason (the free
    note sealed with the booking's data), back to confirmed; undo takes back the last change. quiet: the other dates of
    a series cancelled together, without an email and a notice each."""
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        booking=Booking.objects.select_for_update().get(pk=booking.pk)
        old=booking.status;now=timezone.now();tz=ZoneInfo(tenant.timezone)
        if undo:
            if not can_undo(booking,now):raise BookingError(f"Si torna indietro solo entro {UNDO_MINUTES} minuti: cambia lo stato da qui sotto.")
            status=booking.snapshot["undo"]["from"]
        elif booking.status==status:return booking
        elif status not in MOVES.get(old,()):raise BookingError("Cambio di stato non consentito.")
        elif status in ("completed","no_show") and booking.start>now and old not in ("arrived","in_progress"):raise BookingError("L'appuntamento non è ancora iniziato.")
        elif status in ("arrived","in_progress") and booking.start.astimezone(tz).date()>now.astimezone(tz).date():raise BookingError("La prenotazione non è di oggi.")
        # a place given up may have been taken meanwhile
        if old=="cancelled" and (not capacity_available(booking.service,booking.busy_start,booking.busy_end,booking.party,booking.resource,booking.occurrence,exclude=booking.pk,gap=booking.gap_start and (booking.gap_start,booking.gap_end))
            or booking.secondary and not capacity_available(booking.service,booking.busy_start,booking.busy_end,1,booking.secondary,exclude=booking.pk)):
            raise BookingError("Il posto nel frattempo è stato preso: sposta la prenotazione su un altro orario.")
        booking.status=status
        if undo:booking.snapshot.pop("undo")
        else:booking.snapshot["undo"]={"from":old,"to":status,"at":now.isoformat()}
        if status=="cancelled" and not undo:
            booking.snapshot["cancel_reason"]=reason if reason in REASON_LABELS else "other"
            note=" ".join(str(note or "").split())[:300]
            pii=booking.data;pii.pop("cancel_note",None)
            if note:pii["cancel_note"]=note
            booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk))
        # back from cancelled: the client's link to cancel works again
        if old=="cancelled":ActionToken.objects.filter(booking=booking,kind="cancel").update(used=False)
        booking.save(update_fields=["status","snapshot","pii"])
        if old in HOLDING and status not in HOLDING:
            from .waitlist import freed
            freed(tenant,booking.start,booking.end)  # 2.14.0: the place given up, to the waiting list
        if not undo and not quiet:
            from .client_mails import send
            mail={"cancelled":"rejected" if reason=="rejected" else "cancelled","confirmed":"accepted" if old=="pending" else ""}.get(status)
            if mail:send(tenant,booking,mail)
            if status=="cancelled":
                from .notify import notify_staff
                notify_staff(tenant,booking,"cancelled")
        if status=="cancelled":
            from .api import emit
            emit(tenant,booking,"cancelled")  # also the dates of a series cancelled together, and an undo: each one was "created"
        audit(tenant,actor,"booking.undo" if undo else "booking."+status,booking.pk,**{"from":old},**({"reason":booking.snapshot["cancel_reason"]} if status=="cancelled" else {}))
        return booking
def series_base(booking):
    """What the other dates of a series copy from the booking: the service, with whom, the client and his answers."""
    d=booking.data
    return {"service":str(booking.service_id),"resource":str(booking.resource_id or ""),"option":str(booking.option_id or ""),"party":booking.party,
        "contact":str(booking.contact_id),**{k:d.get(k,"") for k in ("first_name","last_name","email","phone","notes")},"answers":d.get("answers",{}),"allergies":d.get("allergies",[])}
def series_plan(tenant,booking,every,until):
    """2.14.0: Ripeti from the desk: the same time every N weeks until a day (a year at most), each date checked as the
    desk would book it: [(start, None or why not)]. Nothing is saved."""
    if booking.service.kind not in ("resource","capacity") or not booking.contact_id:raise BookingError("Si ripetono gli appuntamenti e le fasce orarie di un cliente con la scheda.")
    tz=ZoneInfo(tenant.timezone);first=booking.start.astimezone(tz)
    if every not in (1,2,3,4) or not until or not first.date()<until<=first.date()+timedelta(days=366):raise BookingError("Scegli ogni quante settimane e fino a quando, al massimo un anno.")
    out=[];d=first.date()+timedelta(weeks=every);base=series_base(booking)
    while d<=until:
        start=local_datetime(d,first.time(),tz);why=None
        if start is None:why="ora che non esiste per il cambio dell'ora"
        else:
            try:bounds(tenant,booking.service,{**base,"start":start.isoformat()},desk=True)
            except BookingError as ex:why=str(ex).split(".")[0].lower()
        out.append((start or datetime.combine(d,first.time(),tz),why));d+=timedelta(weeks=every)
    return out
def series_create(tenant,booking,every,until,actor):
    """The free dates of the plan, tied to the booking (series); the ones taken meanwhile are skipped. How many made."""
    import uuid
    sid=booking.series or uuid.uuid4();made=0
    if not booking.series:Booking.objects.filter(pk=booking.pk).update(series=sid);booking.series=sid
    for start,why in series_plan(tenant,booking,every,until):
        if why:continue
        try:create_booking(tenant,{**series_base(booking),"start":start.isoformat(),"request_key":secrets.token_urlsafe(24)},actor,series=sid);made+=1
        except BookingError:pass
    audit(tenant,actor,"booking.series",booking.pk,made=made,every=every)
    return made
