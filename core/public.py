from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render,redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Service,Option,Resource,ServiceResource,Location,Booking,ActionToken,Contact,ConsentEvent,Tenant,audit
from .identity import throttle,json_body
from .booking import available,create_booking,move_booking,change_status,BookingError,choice,bounds,has_units,free_units
from .calendar import local_datetime
from .crypto import token_hash,encrypt
from .middleware import require_role
from .permissions import visible_bookings,check_new_booking
from .privacy import record

def human_minutes(m):
    if m%1440==0:return f"{m//1440} giorni" if m>=2880 else "1 giorno"
    if m%60==0:return f"{m//60} ore" if m>=120 else "1 ora"
    return f"{m} minuti"
def widget_data(request,booking=None):
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
    for s in rows:
        services.append({"id":str(s.pk),"name":s.name,"kind":s.kind,"turnover":s.turnover,"resource_label":s.resource_label,"description":s.description,"location":s.location.name,"max_party":s.max_party,"fields":s.fields,
            "options":[{"id":str(o.pk),"name":o.name,"duration":o.duration} for o in Option.objects.filter(service=s,active=True)],
            "resources":[{"id":str(r.pk),"name":r.name,"capacity":r.capacity,"description":r.description,"photo":r.photo_url} for r in Resource.objects.filter(active=True,location=s.location,id__in=ServiceResource.objects.filter(service=s).values("resource_id")) if mine is None or str(r.pk) in mine]})
    prefix=f"/t/{tenant.slug}/booking-api" if internal else f"/book/{tenant.slug}/api"
    result={"services":services,"today":timezone.now().astimezone(ZoneInfo(tenant.timezone)).date().isoformat(),"timezone":tenant.timezone,"slots":prefix+"/slots/","submit":prefix+"/submit/",
        "ticket":signing.dumps({"tenant":str(tenant.pk)},salt="public-booking"),"internal":internal,
        "allergens":tenant.asked_allergens}
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
        except PermissionDenied as ex:return JsonResponse({"error":str(ex) or "Operazione non consentita."},status=403)
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
    widget=widget_data(request)
    return render(request,"booking.html",{"widget":widget,"public":True,"intro":INTRO[first_step(widget)]})
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
    return render(request,"booking.html",{"widget":widget,"intro":INTRO[first_step(widget)]})
@json_denied
def slots_api(request,slug,booking_id=None):
    throttle(request,"availability:"+str(request.tenant.pk),120,60)
    exclude=None
    if booking_id is not None:require_role(request,write=True)
    try:
        # The excluded booking is resolved server-side inside the tenant scope.
        if booking_id is not None:exclude=choice(visible_bookings(request,Booking.objects.all()),booking_id,status="confirmed").pk
        day=date.fromisoformat(request.GET.get("date",""))
        service=choice(Service,request.GET.get("service"),active=True,location__active=True)
        if service.kind=="day":
            start=local_datetime(day,service.checkin,ZoneInfo(request.tenant.timezone))
            if not start:raise BookingError("Data non disponibile.")
            data={"start":start.isoformat(),"end_date":request.GET.get("end_date"),"party":request.GET.get("party",1)}
            if has_units(service):
                from .booking import details,integer
                _,unit,_=details(service,None,request.GET.get("resource") or None)
                party=integer(request.GET.get("party",1),"Quantità",1,service.max_party)
                start,end,bs,be,*_=bounds(request.tenant,service,{**data,"resource":str(unit.pk) if unit else ""},exclude=exclude)
                units=free_units(request.tenant,service,bs,be,party,unit,exclude)
                tz=ZoneInfo(request.tenant.timezone)
                slots=[{"start":start.astimezone(tz).isoformat(),"end":end.astimezone(tz).isoformat(),"resource":str(u.pk),"resource_name":u.name,
                    "description":u.description,"photo":u.photo_url,"capacity":u.capacity,"occurrence":""} for u in units]
            else:
                start,end,*_=bounds(request.tenant,service,data,exclude=exclude)
                slots=[{"start":start.astimezone(ZoneInfo(request.tenant.timezone)).isoformat(),"end":end.isoformat(),"resource":"","resource_name":"","occurrence":""}]
        else:
            slots=available(request.tenant,service.pk,day,request.GET.get("option"),request.GET.get("resource"),request.GET.get("party",1),exclude=exclude,allowed=staff_resources(request))
        return JsonResponse({"slots":slots})
    except (ValueError,ValidationError,OverflowError) as ex:return JsonResponse({"error":str(ex) or "Richiesta non valida."},status=400)
@csrf_exempt
@require_POST
@json_denied
def public_submit(request,slug):
    # Anonymous capability, independent of third-party cookies in the embedded widget.
    # This endpoint NEVER inherits an authenticated user's privileges.
    if request.headers.get("Origin")!=settings.PUBLIC_ORIGIN:raise PermissionDenied
    try:
        ticket=signing.loads(request.headers.get("X-TDBS-Ticket",""),salt="public-booking",max_age=3600)
        if ticket.get("tenant")!=str(request.tenant.pk):raise PermissionDenied
    except signing.BadSignature:return JsonResponse({"error":"Pagina scaduta. Ricarica e riprova."},status=403)
    throttle(request,"booking:"+str(request.tenant.pk),20,3600)
    return submit(request,actor=None)
@require_POST
@json_denied
def internal_submit(request,slug):
    require_role(request,write=True)
    try:
        data=json_body(request)
        check_new_booking(request,choice(Service,data.get("service"),active=True),data.get("resource"))
    except (ValueError,ValidationError,TypeError,AttributeError):pass  # submit() reports the validation error
    return submit(request,actor=request.user)
def submit(request,actor):
    try:
        data=json_body(request)
        if actor is None:throttle(request,"book-email:"+str(request.tenant.pk),5,3600,identity=token_hash(str(data.get("email","")).casefold()))
        booking,created=create_booking(request.tenant,data,actor)
        if actor:return JsonResponse({"redirect":f"/t/{request.tenant.slug}/bookings/{booking.pk}/"})
        return JsonResponse({"ok":True,"created":created,"reference":str(booking.pk)[:8],"cancel":f"/action/{request.tenant.slug}/{booking.data['cancel_token']}/"},status=201 if created else 200)
    except (ValueError,ValidationError,TypeError) as ex:return JsonResponse({"error":str(ex) or "Controlla i dati."},status=400)
def get_visible(request,booking_id):
    from django.http import Http404
    try:return choice(visible_bookings(request,Booking.objects.all()),booking_id,status="confirmed")
    except BookingError:raise Http404("Prenotazione non disponibile.")
def move_page(request,slug,booking_id):
    require_role(request,write=True)
    booking=get_visible(request,booking_id)
    return render(request,"booking.html",{"widget":widget_data(request,booking),"moving":True})
@require_POST
@json_denied
def move_api(request,slug,booking_id):
    require_role(request,write=True)
    booking=get_visible(request,booking_id)
    try:
        data=json_body(request)
        # the new place must be inside the scope, also when only the resource changes
        if isinstance(data,dict):check_new_booking(request,choice(Service,data.get("service") or booking.service_id),data.get("resource"))
        move_booking(request.tenant,booking_id,data,request.user)
        return JsonResponse({"redirect":f"/t/{slug}/bookings/{booking_id}/"})
    except (ValueError,ValidationError,TypeError) as ex:return JsonResponse({"error":str(ex)},status=400)
def action_view(request,slug,token):
    throttle(request,"action",60,60)
    obj=ActionToken.objects.filter(digest=token_hash(token),expires__gt=timezone.now()).first()
    if not obj:return render(request,"message.html",{"title":"Collegamento scaduto","text":"Contatta l'attività per assistenza.","public":True},status=400)
    title={"cancel":"Annulla prenotazione","consent":"Conferma iscrizione","unsubscribe":"Disiscriviti"}.get(obj.kind,"Conferma")
    if request.method=="POST":
        try:
            apply_action(request,obj)
            return render(request,"message.html",{"title":"Operazione completata","text":"La tua richiesta è stata registrata.","public":True})
        except BookingError as ex:return render(request,"message.html",{"title":"Operazione non disponibile","text":str(ex),"public":True},status=400)
    return render(request,"action.html",{"title":title,"done":obj.used,"public":True})
def apply_action(request,obj):
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=request.tenant.pk)
        obj=ActionToken.objects.select_for_update().get(pk=obj.pk)
        if obj.expires<=timezone.now():raise BookingError("Collegamento scaduto.")
        if obj.used and obj.kind!="unsubscribe":return
        if obj.kind=="cancel":
            if obj.booking.start<=timezone.now():raise BookingError("La prenotazione è già iniziata. Contatta l'attività.")
            # The policy in force when the booking was made, not today's setting.
            cutoff=obj.booking.snapshot.get("policy",{}).get("cancel_cutoff_minutes",obj.booking.service.cancel_cutoff_minutes)
            if cutoff and timezone.now()>obj.booking.start-timedelta(minutes=cutoff):
                raise BookingError(f"Il termine per annullare online è scaduto ({human_minutes(cutoff)} prima dell'inizio). Contatta direttamente l'attività.")
            change_status(request.tenant,obj.booking,"cancelled")
        elif obj.kind in ("consent","unsubscribe"):
            c=Contact.objects.select_for_update().get(pk=obj.contact_id)
            if obj.kind=="consent":
                if c.suppressed or c.consent=="yes":raise BookingError("Questa conferma non è più valida.")
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
@csrf_exempt
@require_POST
def unsubscribe_oneclick(request,slug,token):
    obj=ActionToken.objects.filter(digest=token_hash(token),kind="unsubscribe",expires__gt=timezone.now()).first()
    if not obj:return JsonResponse({"ok":False},status=400)
    if request.POST.get("List-Unsubscribe")!="One-Click":return JsonResponse({"ok":False},status=400)
    apply_action(request,obj)
    return JsonResponse({"ok":True})
