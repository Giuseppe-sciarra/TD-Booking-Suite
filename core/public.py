import re
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render,redirect
from django.utils import timezone,translation
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Service,Option,Resource,ServiceResource,Location,Booking,ActionToken,Contact,ConsentEvent,Tenant,audit
from .identity import throttle,json_body,page_origin
from .booking import available,create_booking,move_booking,change_status,BookingError,ForceWarning,choice,bounds,has_units,free_units,MOVABLE,ACCEPTED,details,candidates,linked
from .calendar import local_datetime
from .crypto import token_hash,encrypt
from .middleware import require_role
from .permissions import visible_bookings,check_new_booking,can
from .privacy import record

def human_minutes(m):
    if m%1440==0:return _("{n} giorni").format(n=m//1440) if m>=2880 else _("1 giorno")
    if m%60==0:return _("{n} ore").format(n=m//60) if m>=120 else _("1 ora")
    return _("{n} minuti").format(n=m)
def source_tag(value):
    """2.14.0: the tag of a link per channel (?src=instagram): a few plain characters, otherwise none."""
    value=str(value or "").strip().lower()
    return value if re.fullmatch(r"[a-z0-9_-]{1,30}",value) else ""
def widget_data(request,booking=None,wait=None):
    """wait (2.14.0): the waiting-list entry of an offer's link: its booking takes the place kept for it."""
    tenant=request.tenant
    services=[]
    rows=Service.objects.filter(active=True,location__active=True).select_related("location").order_by("name")
    # 2.6.0: [td_booking_suite location="..."] shows one location only
    import uuid
    try:place=uuid.UUID(request.GET.get("location",""))
    except ValueError:place=None
    if place and rows.filter(location_id=place).exists():rows=rows.filter(location_id=place)
    # 2.8.0: [td_booking_suite service="..."] shows that service only
    try:only=uuid.UUID(request.GET.get("service",""))
    except ValueError:only=None
    if only and rows.filter(pk=only).exists():rows=rows.filter(pk=only)
    internal=request.path.startswith("/t/")
    m=getattr(request,"member",None) if internal else None
    # staff with a scope book only inside it: their locations and their own resources
    if m and m.locations:rows=rows.filter(location_id__in=m.locations)
    mine=staff_resources(request)
    # 2.11.0: prices in the staff's own booking form only; the public page shows none
    from .prices import euro
    cost=lambda p:euro(p) if internal and p is not None else ""
    # 2.14.0: the cabins and machines are never chosen; with client_picks off the client does not choose with whom (garage lifts)
    for s in rows:
        services.append({"id":str(s.pk),"name":s.name,"kind":s.kind,"turnover":s.turnover,"resource_label":s.resource_label,"description":s.description,"location":s.location.name,"max_party":s.max_party,"request_above":s.request_above,"fields":s.fields,"price":cost(s.price),
            "options":[{"id":str(o.pk),"name":o.name,"duration":o.duration,"price":cost(o.price)} for o in Option.objects.filter(service=s,active=True)],
            "resources":[{"id":str(r.pk),"name":r.name,"capacity":r.capacity,"description":r.description,"photo":r.photo_link(tenant.slug)} for r in Resource.objects.filter(active=True,secondary=False,location=s.location,id__in=ServiceResource.objects.filter(service=s).values("resource_id")) if (mine is None or str(r.pk) in mine) and (internal or s.client_picks)]})
    prefix=f"/t/{tenant.slug}/booking-api" if internal else f"/book/{tenant.slug}/api"
    from .presets import service_word
    result={"service_label":_(service_word(tenant.business_type)),"services":services,"today":timezone.now().astimezone(ZoneInfo(tenant.timezone)).date().isoformat(),"timezone":tenant.timezone,"slots":prefix+"/slots/","submit":prefix+"/submit/",
        # 2.14.0: where the page was opened, signed with the ticket, so the booking keeps its channel.
        # shortcut: the WordPress plugin is told apart by the theme= its frame always adds; a link with theme= counts as WordPress
        "ticket":signing.dumps({"tenant":str(tenant.pk),"origin":"wordpress" if "theme" in request.GET else "public","src":source_tag(request.GET.get("src")),**({"wait":str(wait.pk)} if wait else {})},salt="public-booking"),"internal":internal,
        "allergens":tenant.asked_allergens,"can_force":internal and can(request,"booking.force"),"wait":"" if internal else f"/book/{tenant.slug}/api/attesa/"}
    # 2.14.0: the page's language also for its answers and for the booking it makes
    if not internal and translation.get_language()!="it":result.update({k:result[k]+"?lang="+translation.get_language() for k in ("slots","submit","wait")})
    if booking:
        result["submit"]=f"/t/{tenant.slug}/bookings/{booking.pk}/move-api/"
        result["editing"]={"service":str(booking.service_id),"option":str(booking.option_id or ""),"resource":str(booking.resource_id or ""),"party":booking.party}
        result["slots"]=f"/t/{tenant.slug}/bookings/{booking.pk}/move-slots/"
    return result
def staff_resources(request):
    """Resource ids a staff member is limited to in the portal, None when there is no such limit."""
    m=getattr(request,"member",None)
    return list(m.resources) if request.path.startswith("/t/") and m and m.resources else None
def json_denied(view):
    """Throttles and permissions answer the booking form in JSON, so it shows the reason instead of "riprova"."""
    from functools import wraps
    @wraps(view)
    def wrapper(request,*a,**kw):
        try:return view(request,*a,**kw)
        except PermissionDenied as ex:return JsonResponse({"error":str(ex) or _("Operazione non consentita.")},status=403)
    return wrapper
def first_step(widget):
    """What the customer chooses first: nothing with a single service, the location when the same service
    exists in several locations, otherwise the service."""
    services=widget["services"]
    if len(services)<=1:return "none"
    if len({s["name"] for s in services})==1 and len({s["location"] for s in services})>1:return "location"
    return "service"
INTRO={"none":"Scegli giorno e orario","location":"Scegli la sede, poi giorno e orario","service":"Scegli il servizio, poi giorno e orario"}
def booking_page(request,slug):
    # 2.14.0: from the link of "Si è liberato un posto": the day, the service, with whom, how many and who, already chosen;
    # the rules of the page stay the same, so a place taken meanwhile is simply not there
    from .waitlist import opened
    offer=opened(request.tenant,request.GET.get("attesa"))
    widget=widget_data(request,wait=offer[0] if offer else None)
    if offer:
        e,day=offer;d=e.data
        widget["prefill"]={"service":str(e.service_id),"resource":str(e.resource_id or ""),"date":e.first.isoformat() if e.service.kind=="day" else day.isoformat(),
            "end_date":e.last.isoformat() if e.service.kind=="day" else "","party":e.party,"contact":{k:d.get(k,"") for k in ("first_name","last_name","email","phone")}}
        widget["slots"]+=("&" if "?" in widget["slots"] else "?")+"attesa="+request.GET["attesa"]
    from .client_mails import current,codes_line
    return render(request,"booking.html",{"widget":widget,"public":True,"intro":_(INTRO[first_step(widget)]),"reminder":current(request.tenant)["reminder"]["on"],
        "waitlist":True,"offer":bool(offer),"codes":[x for x in (codes_line(l) for l in Location.objects.filter(active=True).order_by("name")) if x]})
def manual_booking(request,slug):
    require_role(request,write=True)
    widget=widget_data(request)
    # 2.7.0: from the client card, the booking starts with that client already chosen
    from .permissions import can
    widget["client_search"]=can(request,"contacts.read") and can(request,"pii.read")
    if request.GET.get("cliente") and can(request,"pii.read"):
        from .models import Contact
        import uuid
        try:c=Contact.objects.exclude(email_index__startswith="erased:").filter(pk=uuid.UUID(request.GET["cliente"])).first()
        except ValueError:c=None
        if c:
            d=c.data;widget["client"]={"id":str(c.pk),**{k:d.get(k,"") for k in ("first_name","last_name","email","phone")}}
    # 2.10.0: from a free space of the agenda, the booking starts with that operator, day and time
    prefill={}
    if re.fullmatch(r"[0-9a-f-]{36}",request.GET.get("operatore","")):prefill["resource"]=request.GET["operatore"]
    try:prefill["date"]=date.fromisoformat(request.GET.get("data","")).isoformat()
    except ValueError:pass
    if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d",request.GET.get("ora","")):prefill["time"]=request.GET["ora"]
    if prefill:widget["prefill"]=prefill
    # 2.14.0: a client without appointment, now: appointments and places only (an event or a stay has its date)
    if request.GET.get("adesso"):
        widget["walkin"]=True;widget["services"]=[x for x in widget["services"] if x["kind"] in ("resource","capacity")]
        return render(request,"booking.html",{"widget":widget,"walkin":True,"intro":"Chi entra adesso: servizio e con chi. Nome e recapiti sono facoltativi"})
    return render(request,"booking.html",{"widget":widget,"intro":INTRO[first_step(widget)].replace("il servizio","il "+widget["service_label"].lower())})
@json_denied
def slots_api(request,slug,booking_id=None):
    throttle(request,"availability:"+str(request.tenant.pk),120,60)
    exclude=None
    if booking_id is not None:require_role(request,write=True)
    try:
        # The excluded booking is resolved server-side inside the tenant scope.
        if booking_id is not None:exclude=choice(visible_bookings(request,Booking.objects.all()),booking_id,status__in=MOVABLE).pk
        # 2.14.0: in the portal the desk's rules; Forza lists every date of an event of the day, also full
        desk=request.path.startswith("/t/");force=desk and request.GET.get("forza")=="1" and can(request,"booking.force")
        # 2.14.0: the page of an offer's link sees the place kept for its client as free
        from .waitlist import opened,OWN
        offer=None if desk else opened(request.tenant,request.GET.get("attesa"))
        token=OWN.set(offer[0].pk if offer else None)
        try:slots=free_slots(request,request.GET,exclude,desk,force)
        finally:OWN.reset(token)
        # 2.14.0: no time at all that day (closed, a holiday, the hours gone): nothing to wait for, no "Avvisami"
        return JsonResponse({"slots":slots,**({} if slots or desk else {"closed":closed(request.tenant,request.GET)})})
    except (ValueError,ValidationError,OverflowError) as ex:return JsonResponse({"error":str(ex) or _("Richiesta non valida.")},status=400)
def closed(tenant,params):
    """No time on the page that day, booked or free: the opening hours, closures and dates of the event say none."""
    service=choice(Service,params.get("service"),active=True,location__active=True)
    if service.kind=="day":return False  # a stay the calendar does not allow is an error already, without the box
    option,resource=details(service,params.get("option") or None,params.get("resource") or None)[:2]
    day=date.fromisoformat(params.get("date",""))
    return not any(next(candidates(tenant,service,day,option,r),None) for r in ([resource] if resource or service.kind!="resource" else linked(service)))
def free_slots(request,params,exclude=None,desk=False,force=False):
    """The free times of a day, or the units free for a stay, as the booking form asks for them (params: its query)."""
    day=date.fromisoformat(params.get("date",""))
    service=choice(Service,params.get("service"),active=True,location__active=True)
    if service.kind=="day":
        start=local_datetime(day,service.checkin,ZoneInfo(request.tenant.timezone))
        if not start:raise BookingError("Data non disponibile.")
        data={"start":start.isoformat(),"end_date":params.get("end_date"),"party":params.get("party",1)}
        if has_units(service):
            from .booking import details,integer
            _,unit,_=details(service,None,params.get("resource") or None)
            party=integer(params.get("party",1),"Quantità",1,1000 if desk else service.max_party)
            start,end,bs,be,*_=bounds(request.tenant,service,{**data,"resource":str(unit.pk) if unit else ""},exclude=exclude,desk=desk)
            units=free_units(request.tenant,service,bs,be,party,unit,exclude)
            tz=ZoneInfo(request.tenant.timezone)
            return [{"start":start.astimezone(tz).isoformat(),"end":end.astimezone(tz).isoformat(),"resource":str(u.pk),"resource_name":u.name,
                "description":u.description,"photo":u.photo_link(request.tenant.slug),"capacity":u.capacity,"occurrence":""} for u in units]
        start,end,*_=bounds(request.tenant,service,data,exclude=exclude,desk=desk)
        return [{"start":start.astimezone(ZoneInfo(request.tenant.timezone)).isoformat(),"end":end.isoformat(),"resource":"","resource_name":"","occurrence":""}]
    return available(request.tenant,service.pk,day,params.get("option"),params.get("resource"),params.get("party",1),exclude=exclude,allowed=staff_resources(request),desk=desk,force=force)
@csrf_exempt
@require_POST
@json_denied
def public_submit(request,slug):
    # Anonymous capability, independent of third-party cookies in the embedded widget.
    # This endpoint NEVER inherits an authenticated user's privileges.
    # the portal's address, or the allowed one the page was opened with (the office name of the same server)
    if request.headers.get("Origin") not in (settings.PUBLIC_ORIGIN,page_origin(request)):raise PermissionDenied
    try:
        ticket=signing.loads(request.headers.get("X-TDBS-Ticket",""),salt="public-booking",max_age=3600)
        if ticket.get("tenant")!=str(request.tenant.pk):raise PermissionDenied
    except signing.BadSignature:return JsonResponse({"error":_("Pagina scaduta. Ricarica e riprova.")},status=403)
    throttle(request,"booking:"+str(request.tenant.pk),20,3600)
    return submit(request,actor=None,ticket=ticket)
@csrf_exempt
@require_POST
@json_denied
def public_wait(request,slug):
    """2.14.0: Avvisami se si libera, from the booking page: the same checks as a booking sent from it (origin, signed
    ticket, limits per address and per email)."""
    if request.headers.get("Origin") not in (settings.PUBLIC_ORIGIN,page_origin(request)):raise PermissionDenied
    try:
        ticket=signing.loads(request.headers.get("X-TDBS-Ticket",""),salt="public-booking",max_age=3600)
        if ticket.get("tenant")!=str(request.tenant.pk):raise PermissionDenied
    except signing.BadSignature:return JsonResponse({"error":_("Pagina scaduta. Ricarica e riprova.")},status=403)
    throttle(request,"booking:"+str(request.tenant.pk),20,3600)
    from .waitlist import add
    try:
        data=json_body(request)
        throttle(request,"book-email:"+str(request.tenant.pk),5,3600,identity=token_hash(str(data.get("email","")).casefold()))
        add(request.tenant,data)
        return JsonResponse({"ok":True},status=201)
    except (ValueError,ValidationError,TypeError) as ex:return JsonResponse({"error":str(ex) or _("Controlla i dati.")},status=400)
@require_POST
@json_denied
def internal_submit(request,slug):
    require_role(request,write=True)
    try:
        data=json_body(request)
        check_force(request,data)
        check_new_booking(request,choice(Service,data.get("service"),active=True),data.get("resource"))
    except (ValueError,ValidationError,TypeError,AttributeError):pass  # submit() reports the validation error
    return submit(request,actor=request.user)
def check_force(request,data):
    if isinstance(data,dict) and data.get("force") is True and not can(request,"booking.force"):raise PermissionDenied("Il tuo ruolo non consente di forzare: chiedi al titolare.")
def warned(ex):
    """2.14.0: Forza: what is being exceeded, for the desk to confirm (the form sends force_ok)."""
    return JsonResponse({"error":str(ex),"force":True},status=409)
def submit(request,actor,ticket=None):
    try:
        data=json_body(request)
        if actor is None:throttle(request,"book-email:"+str(request.tenant.pk),5,3600,identity=token_hash(str(data.get("email","")).casefold()))
        booking,created=create_booking(request.tenant,data,actor,**({"origin":ticket.get("origin","public"),"source":source_tag(ticket.get("src")),"wait":ticket.get("wait","")} if ticket else {}))
        if actor:return JsonResponse({"redirect":f"/t/{request.tenant.slug}/bookings/{booking.pk}/"})
        own=f"/action/{request.tenant.slug}/{booking.data['cancel_token']}/"
        # 2.14.0: the confirmed booking into the client's calendar (a request waits for the business's yes)
        from .ics import links
        calendar=None if booking.status=="pending" else {**links(booking),"ics":own+"calendario.ics"}
        return JsonResponse({"ok":True,"created":created,"pending":booking.status=="pending","reference":str(booking.pk)[:8],"cancel":own,"calendar":calendar},status=201 if created else 200)
    except ForceWarning as ex:return warned(ex)
    except (ValueError,ValidationError,TypeError) as ex:return JsonResponse({"error":str(ex) or _("Controlla i dati.")},status=400)
def get_visible(request,booking_id):
    from django.http import Http404
    try:return choice(visible_bookings(request,Booking.objects.all()),booking_id,status__in=MOVABLE)
    except BookingError:raise Http404("Prenotazione non disponibile.")
def move_page(request,slug,booking_id):
    require_role(request,write=True)
    booking=get_visible(request,booking_id)
    return render(request,"booking.html",{"widget":widget_data(request,booking),"moving":True,"booking":booking})
@require_POST
@json_denied
def move_api(request,slug,booking_id):
    require_role(request,write=True)
    booking=get_visible(request,booking_id)
    try:
        data=json_body(request)
        check_force(request,data)
        # the new place must be inside the scope, also when only the resource changes
        if isinstance(data,dict):check_new_booking(request,choice(Service,data.get("service") or booking.service_id),data.get("resource"))
        move_booking(request.tenant,booking_id,data,request.user)
        return JsonResponse({"redirect":f"/t/{slug}/bookings/{booking_id}/"})
    except ForceWarning as ex:return warned(ex)
    except (ValueError,ValidationError,TypeError) as ex:return JsonResponse({"error":str(ex)},status=400)
TITLES={"cancel":"La tua prenotazione","confirm":"Ci sarai?","decline":"Non puoi venire?","consent":"Conferma iscrizione","unsubscribe":"Disiscriviti"}
def own_lang(request,b):
    """2.14.0: the client's links open in the language he booked in, unless he switches (?lang=); the middleware's
    override puts the language back after the request."""
    if b and "lang" not in request.GET and b.snapshot.get("lang"):translation.activate(b.snapshot["lang"])
def action_view(request,slug,token):
    throttle(request,"action",60,60)
    obj=ActionToken.objects.filter(digest=token_hash(token),expires__gt=timezone.now()).first()
    if not obj:return render(request,"message.html",{"title":_("Collegamento scaduto"),"text":_("Contatta l'attività per assistenza."),"public":True},status=400)
    own_lang(request,obj.booking)
    if request.method=="POST":
        try:
            title,text=apply_action(request,obj) or (_("Operazione completata"),_("La tua richiesta è stata registrata."))
            return render(request,"message.html",{"title":title,"text":text,"public":True})
        except BookingError as ex:return render(request,"message.html",{"title":_("Operazione non disponibile"),"text":str(ex),"public":True},status=400)
    ctx={"title":_(TITLES.get(obj.kind,"Conferma")),"done":obj.used,"kind":obj.kind,"public":True}
    if obj.booking_id and obj.kind in ("cancel","confirm","decline"):ctx.update(manage(obj.booking),moved="spostata" in request.GET)
    return render(request,"action.html",ctx)
def too_late(booking):
    """The minutes before the start when the client stops cancelling and moving by himself, once that moment has come
    (0 otherwise): the policy in force when the booking was made, not today's setting."""
    cutoff=booking.snapshot.get("policy",{}).get("cancel_cutoff_minutes",booking.service.cancel_cutoff_minutes)
    return cutoff if cutoff and timezone.now()>booking.start-timedelta(minutes=cutoff) else 0
def manage(b):
    """2.14.0: the page of the client's link: the booking, the calendar links, Sposta and Annulla while he may, otherwise
    why not and the business's phone."""
    from .client_mails import values
    from .ics import links,place
    cutoff=too_late(b)
    late=(_("La prenotazione è annullata") if b.status=="cancelled" else _("La prenotazione è già iniziata o conclusa") if b.status not in MOVABLE or b.start<=timezone.now()
        else _("Il termine per annullare o spostare online è scaduto ({tempo} prima dell'inizio)").format(tempo=human_minutes(cutoff)) if cutoff else "")
    if late:late+=_(": per qualsiasi cosa chiama {telefono}.").format(telefono=b.location.phone) if b.location.phone else _(": per qualsiasi cosa contatta l'attività.")
    return {"booking":b,"what":values(b.tenant,b,{}),"where":place(b),"late":late,"calendar":links(b) if b.status in ACCEPTED else None,
        "can_move":not late and b.service.active and b.service.location.active,
        "checkin":b.service.kind=="day" and b.status in ("pending",)+ACCEPTED and b.end>timezone.now()}  # 2.14.0: stays.checkin_view
def apply_action(request,obj):
    """Returns the title and text of the page that follows, when they are not the usual ones."""
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=request.tenant.pk)
        obj=ActionToken.objects.select_for_update().get(pk=obj.pk)
        if obj.expires<=timezone.now():raise BookingError(_("Collegamento scaduto."))
        if obj.used and obj.kind!="unsubscribe":return None
        result=None
        if obj.kind=="cancel":
            if obj.booking.start<=timezone.now():raise BookingError(_("La prenotazione è già iniziata. Contatta l'attività."))
            if cutoff:=too_late(obj.booking):
                raise BookingError(_("Il termine per annullare online è scaduto ({tempo} prima dell'inizio). Contatta direttamente l'attività.").format(tempo=human_minutes(cutoff)))
            change_status(request.tenant,obj.booking,"cancelled",reason="client")
        elif obj.kind in ("confirm","decline"):
            # 2.14.0: the buttons of the reminder
            b=Booking.objects.select_for_update().select_related("location").get(pk=obj.booking_id)
            if b.status not in MOVABLE or b.start<=timezone.now():raise BookingError(_("Questa prenotazione non è più attiva o è già iniziata. Contatta l'attività."))
            now=timezone.now().isoformat()
            if obj.kind=="confirm":
                b.snapshot.pop("declined",None);b.snapshot["client_confirmed"]=now;b.save(update_fields=["snapshot"])
                audit(request.tenant,None,"booking.client_confirmed",b.pk)
                result=(_("Grazie, ti aspettiamo"),_("Abbiamo detto all'attività che ci sarai."))
            elif too_late(b):
                # inside the cutoff nothing is cancelled by itself: the business sees it and decides
                b.snapshot.pop("client_confirmed",None);b.snapshot["declined"]=now;b.save(update_fields=["snapshot"])
                from .notify import notify_staff
                notify_staff(request.tenant,b,"declined");audit(request.tenant,None,"booking.declined",b.pk)
                result=(_("Abbiamo avvisato l'attività"),_("Il termine per annullare online è scaduto, quindi la prenotazione la annulla l'attività.")+(_(" Per essere sicuro chiama {telefono}.").format(telefono=b.location.phone) if b.location.phone else _(" Per essere sicuro chiamala.")))
            else:
                change_status(request.tenant,b,"cancelled",reason="client")
                result=(_("Prenotazione annullata"),_("Grazie di averci avvisato: il posto torna libero per altri."))
        elif obj.kind in ("consent","unsubscribe"):
            c=Contact.objects.select_for_update().get(pk=obj.contact_id)
            if obj.kind=="consent":
                if c.suppressed or c.consent=="yes":raise BookingError(_("Questa conferma non è più valida."))
                request_proof=(obj.booking.snapshot.get("consent_request") if obj.booking_id else None) or {"text":request.tenant.consent_text,"source":"import" if ConsentEvent.objects.filter(contact=c,kind="imported").exists() else "unknown"}
                c.consent="yes";c.consent_at=timezone.now()
                # Proof is written only when the mailbox owner confirms.
                c.consent_proof=encrypt(request.tenant,{**request_proof,"confirmed":c.consent_at.isoformat(),"booking":str(obj.booking_id or "")},"consent:"+str(c.pk))
                record(request.tenant,c,"confirmed","email_link",text=request_proof.get("text"),ref=obj.booking_id)
            else:
                already=c.consent=="revoked"
                c.consent="revoked";c.consent_at=timezone.now()
                ActionToken.objects.filter(contact=c,kind="consent").update(used=True)
                if not already:record(request.tenant,c,"revoked","one_click" if request.POST.get("List-Unsubscribe")=="One-Click" else "email_link")
            c.save()
            audit(request.tenant,None,"contact."+obj.kind,c.pk)
        obj.used=True;obj.save(update_fields=["used"])
        return result
def manage_token(token):
    """2.14.0: the booking of a "Gestisci" link that the client may still move, or why not."""
    obj=ActionToken.objects.filter(digest=token_hash(token),kind="cancel",used=False,expires__gt=timezone.now()).select_related("booking").first()
    if not obj:raise BookingError(_("Collegamento scaduto. Contatta l'attività."))
    page=manage(obj.booking)
    if not page["can_move"]:raise BookingError(page["late"] or _("Questa prenotazione non si sposta online. Contatta l'attività."))
    return obj.booking
def same(b,params):
    """What the client keeps when he moves: service, variant, operator (or whoever is free, if he chose so), people;
    he chooses only the day, the time and, for a stay, the dates."""
    return {"service":str(b.service_id),"option":str(b.option_id or ""),"resource":"" if b.snapshot.get("any_resource") else str(b.resource_id or ""),
        "party":b.party,**{k:params.get(k) or "" for k in ("date","start","end_date","occurrence")}}
def move_public(request,slug,token):
    """2.14.0: Sposta from the client's link: the public page's rules, the checks of move_booking, until the cutoff."""
    throttle(request,"action",60,60)
    if request.method=="POST":
        throttle(request,"booking:"+str(request.tenant.pk),20,3600)
        try:
            b=manage_token(token)
            move_booking(request.tenant,b.pk,same(b,json_body(request)),None,desk=False)
            return JsonResponse({"redirect":f"/action/{slug}/{token}/?spostata=1"})
        except (ValueError,ValidationError,TypeError) as ex:return JsonResponse({"error":str(ex)},status=400)
    try:b=manage_token(token)
    except BookingError as ex:return render(request,"message.html",{"title":_("Spostamento non disponibile"),"text":str(ex),"public":True},status=400)
    own_lang(request,b)
    widget=widget_data(request)
    widget["services"]=[{**x,"options":[],"resources":[]} for x in widget["services"] if x["id"]==str(b.service_id)]
    widget.update(submit=request.path,slots=request.path+"orari/",editing={"service":str(b.service_id),"option":"","resource":"","party":b.party},locked=True)
    return render(request,"booking.html",{"widget":widget,"moving":True,"public":True,"booking":b,"manage":f"/action/{slug}/{token}/","intro":_("Stesso servizio: scegli il nuovo giorno e l'orario")})
@json_denied
def move_public_slots(request,slug,token):
    throttle(request,"availability:"+str(request.tenant.pk),120,60)
    try:
        b=manage_token(token)
        return JsonResponse({"slots":free_slots(request,same(b,request.GET),b.pk)})
    except (ValueError,ValidationError,OverflowError) as ex:return JsonResponse({"error":str(ex) or _("Richiesta non valida.")},status=400)
def booking_ics(request,slug,token):
    """2.14.0: the booking as a calendar file, from the client's link."""
    from django.http import Http404,HttpResponse
    from .ics import ics
    throttle(request,"action",60,60)
    obj=ActionToken.objects.filter(digest=token_hash(token),kind="cancel",expires__gt=timezone.now()).select_related("booking").first()
    if not obj or obj.booking.status not in ACCEPTED:raise Http404("Prenotazione non disponibile.")
    response=HttpResponse(ics(obj.booking),content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"]='attachment; filename="prenotazione.ics"'
    return response
def operator_feed(request,slug,token):
    """2.14.0: the private calendar of an operator, from its signed link (feeds.py); renewed or switched off, 404."""
    from django.http import Http404,HttpResponse
    from .feeds import opened,feed
    throttle(request,"feed",120,3600)
    key=opened(token);r=Resource.objects.filter(pk=key[0]).first() if key else None
    if not r or not r.feed or r.feed!=key[1]:raise Http404("Calendario non disponibile.")
    return HttpResponse(feed(request.tenant,r),content_type="text/calendar; charset=utf-8")
@csrf_exempt
@require_POST
def unsubscribe_oneclick(request,slug,token):
    obj=ActionToken.objects.filter(digest=token_hash(token),kind="unsubscribe",expires__gt=timezone.now()).first()
    if not obj:return JsonResponse({"ok":False},status=400)
    if request.POST.get("List-Unsubscribe")!="One-Click":return JsonResponse({"ok":False},status=400)
    apply_action(request,obj)
    return JsonResponse({"ok":True})
