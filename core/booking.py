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
    duration=option.duration if option else service.duration
    return option,resource,duration
def capacity_available(service,start,end,party,resource=None,occurrence=None,exclude=None):
    rows=Booking.objects.filter(status__in=["confirmed","completed","no_show"],busy_start__lt=end,busy_end__gt=start)
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
def has_units(service):
    return service.kind=="day" and ServiceResource.objects.filter(service=service).exists()
def free_units(tenant,service,bs,be,party,resource=None,exclude=None):
    """Named units of a day service free for the whole interval, in name order."""
    units=Resource.objects.filter(active=True,location=service.location,id__in=ServiceResource.objects.filter(service=service).values("resource_id")).order_by("name")
    if resource:units=units.filter(pk=resource.pk)
    return [u for u in units if (u.capacity==0 or u.capacity>=party)
        and capacity_available(service,bs,be,1,resource=u,exclude=exclude)
        and not blocked_stay(tenant,bs,be,service,u)]
def candidates(tenant,service,day,option=None,resource=None):
    now=timezone.now()
    duration=timedelta(minutes=option.duration if option else service.duration)
    before=timedelta(minutes=service.buffer_before);after=timedelta(minutes=service.buffer_after)
    lead=now+timedelta(minutes=service.lead_minutes)
    horizon=now+timedelta(days=service.horizon_days)
    tz=ZoneInfo(tenant.timezone)
    if service.kind in ("event","shift"):
        for event in Occurrence.objects.filter(service=service,active=True,start__gte=lead,start__lte=horizon).order_by("start"):
            if event.start.astimezone(tz).date()==day and fits(tenant,event.start-before,event.end+after,service):
                yield event.start,event.end,event
        return
    if service.kind=="day":return
    whole=service.kind=="capacity" and not service.turnover
    for a,b in windows(tenant,day,service,resource):
        tick=a
        while tick<b:
            # without time slots a booking holds its places until the window closes: lunch and dinner each have their seats
            end=b-after if whole else tick+duration
            if tick.astimezone(tz).date()==day and lead<=tick<=horizon and tick-before>=a and end+after<=b:
                yield tick,end,None
            tick+=timedelta(minutes=service.interval)
def available(tenant,service_id,day,option_id=None,resource_id=None,party=1,exclude=None,allowed=None):
    """Free slots of a day. allowed: resource ids a scoped staff member may book ("Il primo disponibile" stays among them)."""
    service=choice(Service,service_id,active=True,location__active=True)
    option,resource,_=details(service,option_id,resource_id)
    party=integer(party,"Quantità",1,service.max_party)
    tz=ZoneInfo(tenant.timezone)
    today=timezone.now().astimezone(tz).date()
    if day<today or day>today+timedelta(days=min(service.horizon_days,730)):return []
    resources=[resource]
    if service.kind=="resource" and not resource:
        resources=list(Resource.objects.filter(active=True,location=service.location,id__in=ServiceResource.objects.filter(service=service).values("resource_id")).order_by("name"))
        if allowed is not None:resources=[r for r in resources if str(r.pk) in allowed]
    result=[]
    for res in resources:
        for start,end,event in candidates(tenant,service,day,option,res):
            bs=start-timedelta(minutes=service.buffer_before);be=end+timedelta(minutes=service.buffer_after)
            if capacity_available(service,bs,be,party,res,event,exclude):
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
def personal(data,service,staff=False):
    if staff:
        # 2.7.0: a booking taken at the desk or on the phone: surname and email are optional, phone or email is enough
        from .clients import clean
        result=clean(data)
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
def bounds(tenant,service,data,exclude=None):
    option,resource,duration=details(service,data.get("option"),data.get("resource"))
    party=integer(data.get("party",1),"Quantità",1,service.max_party)
    try:start=datetime.fromisoformat(str(data["start"]))
    except (KeyError,ValueError):raise BookingError("Data o ora non valida.")
    if start.tzinfo is None:raise BookingError("Fuso orario mancante.")
    start=start.astimezone(UTC)
    tz=ZoneInfo(tenant.timezone);day=start.astimezone(tz).date();occurrence=None
    if service.kind=="day":
        try:end_day=date.fromisoformat(str(data["end_date"]))
        except (ValueError,KeyError):raise BookingError("Seleziona la data di partenza.")
        nights=(end_day-day).days
        if nights<max(1,service.min_days):raise BookingError(f"Durata minima: {service.min_days} giorni.")
        if nights>service.max_days:raise BookingError(f"Durata massima: {service.max_days} giorni.")
        clock_in=service.checkin if isinstance(service.checkin,time) else time.fromisoformat(service.checkin)
        clock_out=service.checkout if isinstance(service.checkout,time) else time.fromisoformat(service.checkout)
        expected=local_datetime(day,clock_in,tz);end=local_datetime(end_day,clock_out,tz)
        if not expected or not end or start!=expected.astimezone(UTC):raise BookingError("Ora di arrivo non valida.")
        end=end.astimezone(UTC)
        if not timezone.now()+timedelta(minutes=service.lead_minutes)<=start<=timezone.now()+timedelta(days=service.horizon_days):raise BookingError("Data fuori dall'intervallo prenotabile.")
        if not fits(tenant,start,start+timedelta(microseconds=1),service,purpose="arrival") or not fits(tenant,end,end+timedelta(microseconds=1),service,purpose="departure") or blocked_stay(tenant,start,end,service):
            raise BookingError("Arrivo, partenza o soggiorno non consentiti dal calendario.")
        bs=start-timedelta(minutes=service.buffer_before);be=end+timedelta(minutes=service.buffer_after)
        if has_units(service):
            # The same named unit for every night of the stay.
            free=free_units(tenant,service,bs,be,party,resource,exclude)
            if not free:raise BookingError("Nessuna unità disponibile per le date scelte." if not resource else "L'unità scelta non è disponibile per queste date.")
            resource=free[0]
        elif not capacity_available(service,bs,be,party,exclude=exclude):raise BookingError("Disponibilità esaurita.")
    else:
        slots=available(tenant,service.pk,day,data.get("option"),data.get("resource"),party,exclude)
        match=next((s for s in slots if datetime.fromisoformat(s["start"]).astimezone(UTC)==start and (not data.get("occurrence") or s["occurrence"]==str(data["occurrence"]))),None)
        if not match:raise BookingError("Orario non disponibile. Aggiorna le disponibilità.")
        resource=choice(Resource,match["resource"]) if match["resource"] else None
        occurrence=choice(Occurrence,match["occurrence"]) if match["occurrence"] else None
        end=datetime.fromisoformat(match["end"]).astimezone(UTC)
        bs=start-timedelta(minutes=service.buffer_before);be=end+timedelta(minutes=service.buffer_after)
    return start,end,bs,be,party,option,resource,occurrence
def action_token(tenant,kind,contact=None,booking=None,days=30):
    token=secrets.token_urlsafe(32)
    ActionToken.objects.create(tenant=tenant,digest=token_hash(token),kind=kind,contact=contact,booking=booking,expires=timezone.now()+timedelta(days=days))
    return token
def queue(tenant,payload,contact=None,campaign=None):
    delivery=Delivery(tenant=tenant,contact=contact,campaign=campaign,next_attempt=timezone.now())
    delivery.payload=encrypt(tenant,payload,"delivery:"+str(delivery.pk));delivery.save()
    return delivery
def create_booking(tenant,data,actor=None):
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
        pii=personal(data,service,staff)
        start,end,bs,be,party,option,resource,event=bounds(tenant,service,data)
        known_by_phone=None
        if staff:
            # 2.7.0: the client picked from the card, or found by email or phone, or a new card
            from .clients import find_or_create
            if data.get("contact"):
                contact=choice(Contact.objects.exclude(email_index__startswith="erased:"),data.get("contact"));new_contact=False
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
        contact.save()
        booking=Booking(tenant=tenant,service=service,location=service.location,resource=resource,option=option,occurrence=event,contact=contact,start=start,end=end,busy_start=bs,busy_end=be,party=party,request_key=key,request_fingerprint=fingerprint,snapshot={"service":service.name,"location":service.location.name,"resource":resource.name if resource else "","option":option.name if option else "","field_labels":{x["key"]:x["label"] for x in service.fields},
            "policy":{"cancel_cutoff_minutes":service.cancel_cutoff_minutes,"min_days":service.min_days}})
        from .prices import stamp
        stamp(booking)  # 2.11.0: price, client discount and what he pays, as at this moment
        booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk));booking.save()
        token=action_token(tenant,"cancel",contact,booking,days=max(30,service.horizon_days+service.max_days+30))
        pii["cancel_token"]=token;booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk));booking.save(update_fields=["pii"])
        from django.conf import settings
        cancel=f"{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/"
        if pii.get("email"):queue(tenant,{"to":pii["email"],"subject":"Prenotazione confermata · "+tenant.name,"body":f"La prenotazione è confermata.\n{service.name}\n{start.astimezone(ZoneInfo(tenant.timezone)):%d/%m/%Y %H:%M}\nGestisci o annulla: {cancel}"},contact)
        if consent_requested:
            token=action_token(tenant,"consent",contact=contact,booking=booking,days=7)
            booking.snapshot["consent_request"]={"text":tenant.consent_text,"requested":timezone.now().isoformat(),"source":"booking"}
            booking.save(update_fields=["snapshot"])
            from .privacy import record
            record(tenant,contact,"requested","booking",actor,tenant.consent_text,booking.pk)
            queue(tenant,{"to":pii["email"],"subject":"Conferma iscrizione · "+tenant.name,"body":f"Conferma la richiesta di iscrizione alle comunicazioni di {tenant.name}:\n{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/\nSe non hai richiesto l'iscrizione, ignora il messaggio."},contact)
        from .notify import notify_staff
        notify_staff(tenant,booking,"created")
        audit(tenant,actor,"booking.created",booking.pk)
        return booking,True
def move_booking(tenant,booking_id,data,actor):
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        booking=choice(Booking,booking_id,status="confirmed")
        service=choice(Service,data.get("service",booking.service_id),active=True,location__active=True)
        values=bounds(tenant,service,data,exclude=booking.pk)
        booking.start,booking.end,booking.busy_start,booking.busy_end,booking.party,booking.option,booking.resource,booking.occurrence=values
        booking.service=service;booking.location=service.location
        booking.snapshot.update(service=service.name,location=service.location.name,resource=booking.resource.name if booking.resource else "",option=booking.option.name if booking.option else "")
        from .prices import stamp
        stamp(booking)
        booking.save()
        ActionToken.objects.filter(booking=booking,kind="cancel",used=False).update(expires=booking.end+timedelta(days=30))
        # a booking taken at the desk may have no email (2.7.0): an empty address would bounce and exclude the client from email
        if booking.data.get("email"):queue(tenant,{"to":booking.data["email"],"subject":"Prenotazione aggiornata · "+tenant.name,"body":f"Nuovo orario: {booking.start.astimezone(ZoneInfo(tenant.timezone)):%d/%m/%Y %H:%M}.\nContatta l'attività per informazioni."},booking.contact)
        from .notify import notify_staff
        notify_staff(tenant,booking,"moved")
        audit(tenant,actor,"booking.moved",booking.pk)
        return booking
def change_status(tenant,booking,status,actor=None):
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        booking=Booking.objects.select_for_update().get(pk=booking.pk)
        if booking.status==status:return booking
        if booking.status!="confirmed" or status not in ("cancelled","completed","no_show"):raise BookingError("Cambio di stato non consentito.")
        if status in ("completed","no_show") and booking.start>timezone.now():raise BookingError("L'appuntamento non è ancora iniziato.")
        booking.status=status;booking.save(update_fields=["status"])
        if status=="cancelled":
            if booking.data.get("email"):queue(tenant,{"to":booking.data["email"],"subject":"Prenotazione annullata · "+tenant.name,"body":"La prenotazione è stata annullata. Contatta l'attività per prenotare nuovamente."},booking.contact)
            from .notify import notify_staff
            notify_staff(tenant,booking,"cancelled")
        audit(tenant,actor,"booking."+status,booking.pk)
        return booking
