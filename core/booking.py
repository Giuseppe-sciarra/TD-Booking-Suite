import json,re,secrets
from datetime import datetime,date,timedelta,time,timezone as TZ
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from .models import Tenant,Service,Resource,ServiceResource,Option,Occurrence,Booking,Contact,ActionToken,Delivery,audit
from .crypto import encrypt,index,tenant_key,token_hash
from .calendar import windows,fits,local_datetime,blocked_stay
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
REASON_LABELS={**dict(REASONS),"rejected":"Richiesta rifiutata"}
def integer(value,label,minimum=1,maximum=10000):
    if isinstance(value,bool) or not re.fullmatch(r"\d+",str(value)):raise BookingError(label+" non valido.")
    val=int(value)
    if not minimum<=val<=maximum:raise BookingError(label+" fuori intervallo.")
    return val
def choice(model,value,**filters):
    source=model.objects if hasattr(model,"objects") else model  # model or pre-scoped queryset
    try:obj=source.filter(pk=value,**filters).first()
    except (ValidationError,ValueError):obj=None
    if not obj:raise BookingError("Selezione non valida o non disponibile.")
    return obj
def details(service,option_id=None,resource_id=None):
    option=choice(Option,option_id,service=service,active=True) if option_id else None
    resource=choice(Resource,resource_id,active=True,location=service.location) if resource_id else None
    if resource and not ServiceResource.objects.filter(service=service,resource=resource).exists():raise BookingError("La risorsa non appartiene al servizio.")
    if service.kind not in ("resource","day") and resource:raise BookingError("Risorsa non prevista.")
    return option,resource,length(service,option,resource)
def length(service,option=None,resource=None):
    """Minutes of an appointment: the variant's; otherwise the operator's own for this service (2.11.0, Giulia does
    the colour in 75 minutes); otherwise the service's."""
    if option:return option.duration
    if resource is not None and service.kind=="resource":
        own=ServiceResource.objects.filter(service=service,resource=resource).values_list("duration",flat=True).first()
        if own:return own
    return service.duration
def capacity_available(service,start,end,party,resource=None,occurrence=None,exclude=None):
    rows=Booking.objects.filter(status__in=HOLDING,busy_start__lt=end,busy_end__gt=start)
    if exclude:rows=rows.exclude(pk=exclude)
    if resource:rows=rows.filter(resource=resource);cap=1;party=1
    elif occurrence:rows=rows.filter(occurrence=occurrence);cap=occurrence.capacity
    else:rows=rows.filter(service=service);cap=service.capacity
    events=[]
    for row in rows:
        n=1 if resource else row.party
        events.extend([(max(row.busy_start,start),n),(min(row.busy_end,end),-n)])
    occupied=0
    for _,delta in sorted(events,key=lambda x:(x[0],x[1])):
        occupied+=delta
        if occupied+party>cap:return False
    return party<=cap and (not occurrence or service.kind!="shift" or capacity_available(service,start,end,party,exclude=exclude))
def linked(service):
    """The active operators, rooms or units of a service, in name order."""
    return Resource.objects.filter(active=True,location=service.location,id__in=ServiceResource.objects.filter(service=service).values("resource_id")).order_by("name")
def has_units(service):
    return service.kind=="day" and ServiceResource.objects.filter(service=service).exists()
def free_units(tenant,service,bs,be,party,resource=None,exclude=None):
    """Named units of a day service free for the whole interval, in name order."""
    units=linked(service)
    if resource:units=units.filter(pk=resource.pk)
    return [u for u in units if (u.capacity==0 or u.capacity>=party)
        and capacity_available(service,bs,be,1,resource=u,exclude=exclude)
        and not blocked_stay(tenant,bs,be,service,u)]
def candidates(tenant,service,day,option=None,resource=None,desk=False,force=False):
    """desk (2.14.0): lead time, horizon and last arrival are the public page's, the desk books from now on;
    force: Forza lists every date of an event of that day, also started, full or outside the hours."""
    now=timezone.now()
    duration=timedelta(minutes=length(service,option,resource))
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
def available(tenant,service_id,day,option_id=None,resource_id=None,party=1,exclude=None,allowed=None,desk=False,force=False):
    """Free slots of a day. allowed: resource ids a scoped staff member may book ("Il primo disponibile" stays among them).
    desk and force: see candidates; the desk also books groups bigger than the online limit (max_party), within the places."""
    service=choice(Service,service_id,active=True,location__active=True)
    option,resource,_=details(service,option_id,resource_id)
    party=integer(party,"Quantità",1,1000 if desk else service.max_party)
    tz=ZoneInfo(tenant.timezone)
    today=timezone.now().astimezone(tz).date()
    if (day<today and not force) or day>today+timedelta(days=730 if desk else min(service.horizon_days,730)):return []
    resources=[resource]
    if service.kind=="resource" and not resource:
        resources=list(linked(service))
        if allowed is not None:resources=[r for r in resources if str(r.pk) in allowed]
    result=[]
    for res in resources:
        for start,end,event in candidates(tenant,service,day,option,res,desk,force):
            bs=start-timedelta(minutes=service.buffer_before);be=end+timedelta(minutes=service.buffer_after)
            if force or capacity_available(service,bs,be,party,res,event,exclude):
                result.append({"start":start.astimezone(tz).isoformat(),"end":end.astimezone(tz).isoformat(),"resource":str(res.pk) if res else "","resource_name":res.name if res else "","occurrence":str(event.pk) if event else ""})
    result=sorted(result,key=lambda x:(x["start"],x["resource_name"]))
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
        if not val or len(val)>(254 if key=="email" else 100) or any(ord(c)<32 for c in val):raise BookingError("Compila correttamente nome, cognome, email e telefono.")
        result[key]=val
    try:validate_email(result["email"])
    except ValidationError:raise BookingError("Email non valida.")
    result["email"]=result["email"].casefold()
    result["phone"]=normalize_phone(result["phone"])
    if not result["phone"]:raise BookingError("Numero di telefono non valido, per esempio 333 123 4567.")
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
def bounds(tenant,service,data,exclude=None,desk=False,force=False):
    """Where and when a booking goes, checked. desk (2.14.0): the rules of the public page (lead time, horizon, last
    arrival, people per booking) do not bind the staff. force: Forza at the desk, any time typed in (in the business's
    time); what it goes beyond comes back in the last value, in words, instead of a refusal (stays excluded)."""
    option,resource,duration=details(service,data.get("option"),data.get("resource"))
    party=integer(data.get("party",1),"Quantità",1,1000 if desk else service.max_party)
    tz=ZoneInfo(tenant.timezone);now=timezone.now()
    try:start=datetime.fromisoformat(str(data["start"]))
    except (KeyError,ValueError):raise BookingError("Data o ora non valida.")
    if force and start.tzinfo is None:start=local_datetime(start.date(),start.time(),tz)
    if start is None:raise BookingError("Data o ora non valida.")
    if start.tzinfo is None:raise BookingError("Fuso orario mancante.")
    start=start.astimezone(UTC)
    day=start.astimezone(tz).date();occurrence=None;issues=[]
    before=timedelta(minutes=service.buffer_before);after=timedelta(minutes=service.buffer_after)
    if service.kind=="day":
        try:end_day=date.fromisoformat(str(data["end_date"]))
        except (ValueError,KeyError):raise BookingError("Seleziona la data di partenza.")
        nights=(end_day-day).days
        # 2.14.0: 0 nights minimum is the rental of a day, picked up and returned the same day (end>start below)
        if 0<=nights<service.min_days:raise BookingError(f"Durata minima: {service.min_days} giorni.")
        if nights>service.max_days:raise BookingError(f"Durata massima: {service.max_days} giorni.")
        clock_in=service.checkin if isinstance(service.checkin,time) else time.fromisoformat(service.checkin)
        clock_out=service.checkout if isinstance(service.checkout,time) else time.fromisoformat(service.checkout)
        expected=local_datetime(day,clock_in,tz);end=local_datetime(end_day,clock_out,tz)
        if not expected or not end or start!=expected.astimezone(UTC):raise BookingError("Ora di arrivo non valida.")
        end=end.astimezone(UTC)
        if end<=start:raise BookingError("La partenza viene prima dell'arrivo: scegli un'altra data di partenza.")
        if (day<now.astimezone(tz).date()) if desk else not now+timedelta(minutes=service.lead_minutes)<=start<=now+timedelta(days=service.horizon_days):raise BookingError("Data fuori dall'intervallo prenotabile.")
        if not fits(tenant,start,start+timedelta(microseconds=1),service,purpose="arrival") or not fits(tenant,end,end+timedelta(microseconds=1),service,purpose="departure") or blocked_stay(tenant,start,end,service):
            raise BookingError("Arrivo, partenza o soggiorno non consentiti dal calendario.")
        bs=start-before;be=end+after
        if has_units(service):
            # The same named unit for every night of the stay.
            free=free_units(tenant,service,bs,be,party,resource,exclude)
            if not free:raise BookingError("Nessuna unità disponibile per le date scelte." if not resource else "L'unità scelta non è disponibile per queste date.")
            resource=free[0]
        elif not capacity_available(service,bs,be,party,exclude=exclude):raise BookingError("Disponibilità esaurita.")
    elif force:
        if service.kind in ("event","shift"):
            occurrence=choice(Occurrence,data.get("occurrence"),service=service,active=True);start=occurrence.start
        elif service.kind=="resource" and not resource:
            # "Il primo disponibile" at the time typed in: the first one free then, otherwise the desk chooses with whom
            for r in linked(service):
                e=start+timedelta(minutes=length(service,option,r))
                if fits(tenant,start-before,e+after,service,r) and capacity_available(service,start-before,e+after,party,r,exclude=exclude):resource=r;break
            else:raise BookingError("Nessuno è libero a quell'ora: scegli con chi.")
        if occurrence:end=occurrence.end
        elif service.kind=="capacity" and not service.turnover:
            # a table holds its places until that opening window closes, as on the page
            end=next((b-after for a,b in windows(tenant,day,service) if a<=start<b),start+timedelta(minutes=service.duration))
        else:end=start+timedelta(minutes=length(service,option,resource))
        bs=start-before;be=end+after
        if start<now-timedelta(minutes=1):issues.append("nel passato")
        if not fits(tenant,bs,be,service,resource):issues.append("fuori dall'orario di apertura")
        if not capacity_available(service,bs,be,party,resource,occurrence,exclude):
            issues.append(f"sovrapposta a un altro appuntamento di {resource.name}" if resource else f"oltre i posti disponibili ({occurrence.capacity if occurrence else service.capacity})")
    else:
        slots=available(tenant,service.pk,day,data.get("option"),data.get("resource"),party,exclude,desk=desk)
        match=next((s for s in slots if datetime.fromisoformat(s["start"]).astimezone(UTC)==start and (not data.get("occurrence") or s["occurrence"]==str(data["occurrence"]))),None)
        if not match:raise BookingError("Orario non disponibile. Aggiorna le disponibilità.")
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
    the default). Phase 6 adds here the clients the business flags."""
    return service.request_all or 0<service.request_above<party or (service.request_allergies and bool(pii.get("allergies")))
def action_token(tenant,kind,contact=None,booking=None,days=30):
    token=secrets.token_urlsafe(32)
    ActionToken.objects.create(tenant=tenant,digest=token_hash(token),kind=kind,contact=contact,booking=booking,expires=timezone.now()+timedelta(days=days))
    return token
def queue(tenant,payload,contact=None,campaign=None):
    delivery=Delivery(tenant=tenant,contact=contact,campaign=campaign,next_attempt=timezone.now())
    delivery.payload=encrypt(tenant,payload,"delivery:"+str(delivery.pk));delivery.save()
    return delivery
def create_booking(tenant,data,actor=None,origin="public",source=""):
    """origin and source (2.14.0): the channel of an online booking, from the signed ticket of its page (public.py);
    the staff's bookings are the desk's, or of a client without appointment (walkin: now, already in progress)."""
    # All inventory writers take the same stable tenant row lock.
    with transaction.atomic():
        tenant=Tenant.objects.select_for_update().get(pk=tenant.pk)
        if not operational(tenant):raise BookingError("Prenotazioni non disponibili.")
        if actor is None and tenant.receive_schedule and not fits(tenant,timezone.now(),timezone.now()+timedelta(seconds=1),purpose="receive"):
            raise BookingError("Le richieste online sono accettate soltanto negli orari indicati dall'attività.")
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
        when={**data,"start":timezone.now().replace(second=0,microsecond=0).isoformat()} if walkin else data
        start,end,bs,be,party,option,resource,event,issues=bounds(tenant,service,when,desk=staff,force=staff and (walkin or data.get("force") is True))
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
            new_contact=contact is None
            if new_contact:
                contact=Contact(tenant=tenant,email_index=idx)
                contact.pii=encrypt(tenant,{k:pii[k] for k in ("first_name","last_name","email","phone")},"contact:"+str(contact.pk))
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
            status=status,origin=("walkin" if walkin else "desk") if staff else origin,source="" if staff else source,snapshot={"service":service.name,"location":service.location.name,"resource":resource.name if resource else "","option":option.name if option else "","field_labels":{x["key"]:x["label"] for x in service.fields},
            "policy":{"cancel_cutoff_minutes":service.cancel_cutoff_minutes,"min_days":service.min_days}})
        if not notices:booking.snapshot["notices"]=False
        if issues:booking.snapshot["forced"]=issues
        from .prices import stamp
        stamp(booking)  # 2.11.0: price, client discount and what he pays, as at this moment
        booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk));booking.save()
        token=action_token(tenant,"cancel",contact,booking,days=max(30,service.horizon_days+service.max_days+30))
        pii["cancel_token"]=token;booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk));booking.save(update_fields=["pii"])
        from django.conf import settings
        from .client_mails import send
        if not walkin:send(tenant,booking,"requested" if status=="pending" else "confirmed")
        if consent_requested:
            token=action_token(tenant,"consent",contact=contact,booking=booking,days=7)
            booking.snapshot["consent_request"]={"text":tenant.consent_text,"requested":timezone.now().isoformat(),"source":"booking"}
            booking.save(update_fields=["snapshot"])
            from .privacy import record
            record(tenant,contact,"requested","booking",actor,tenant.consent_text,booking.pk)
            queue(tenant,{"to":pii["email"],"subject":"Conferma iscrizione · "+tenant.name,"body":f"Conferma la richiesta di iscrizione alle comunicazioni di {tenant.name}:\n{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/\nSe non hai richiesto l'iscrizione, ignora il messaggio."},contact)
        from .notify import notify_staff
        notify_staff(tenant,booking,"requested" if status=="pending" else "created")
        audit(tenant,actor,"booking.created",booking.pk,origin=booking.origin,**({"forced":issues} if issues else {}))
        return booking,True
def move_booking(tenant,booking_id,data,actor):
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        booking=choice(Booking,booking_id,status__in=MOVABLE)
        service=choice(Service,data.get("service",booking.service_id),active=True,location__active=True)
        # 2.14.0: moving is the desk's, with the desk's rules and Forza
        *values,issues=bounds(tenant,service,data,exclude=booking.pk,desk=True,force=data.get("force") is True)
        overrun(issues,data)
        booking.start,booking.end,booking.busy_start,booking.busy_end,booking.party,booking.option,booking.resource,booking.occurrence=values
        booking.snapshot.pop("forced",None)
        if issues:booking.snapshot["forced"]=issues
        booking.service=service;booking.location=service.location
        booking.snapshot.update(service=service.name,location=service.location.name,resource=booking.resource.name if booking.resource else "",option=booking.option.name if booking.option else "")
        from .prices import stamp
        stamp(booking)
        booking.snapshot.pop("reminded",None)  # 2.13.0: the new day gets its own reminder
        booking.save()
        ActionToken.objects.filter(booking=booking,kind="cancel",used=False).update(expires=booking.end+timedelta(days=30))
        from .client_mails import send
        send(tenant,booking,"moved")
        from .notify import notify_staff
        notify_staff(tenant,booking,"moved")
        audit(tenant,actor,"booking.moved",booking.pk,**({"forced":issues} if issues else {}))
        return booking
def can_undo(booking,now=None):
    """2.14.0: the last change of status can be taken back for a few minutes, where it was made."""
    last=booking.snapshot.get("undo")
    return bool(last) and last["to"]==booking.status and (now or timezone.now())<=datetime.fromisoformat(last["at"])+timedelta(minutes=UNDO_MINUTES)
def change_status(tenant,booking,status,actor=None,reason="",note="",undo=False):
    """2.14.0: a request accepted or refused, arrived, in progress, done, absent, cancelled with its reason (the free
    note sealed with the booking's data), back to confirmed; undo takes back the last change."""
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
        if old=="cancelled" and not capacity_available(booking.service,booking.busy_start,booking.busy_end,booking.party,booking.resource,booking.occurrence,exclude=booking.pk):
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
        if not undo:
            from .client_mails import send
            mail={"cancelled":"rejected" if reason=="rejected" else "cancelled","confirmed":"accepted" if old=="pending" else ""}.get(status)
            if mail:send(tenant,booking,mail)
            if status=="cancelled":
                from .notify import notify_staff
                notify_staff(tenant,booking,"cancelled")
        audit(tenant,actor,"booking.undo" if undo else "booking."+status,booking.pk,**{"from":old},**({"reason":booking.snapshot["cancel_reason"]} if status=="cancelled" else {}))
        return booking
