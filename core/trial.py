"""2.4.0: "Non sei ancora cliente? Prova il servizio" on the sign-in page (Cloud edition).

A business leaves name, email and business type; the agency gets an email (to the address set in
Impostazioni generali) and finds the request in the agency area, where one click opens Nuovo cliente
already filled in, business type included. Who asked is sealed with the master key; the email to the
agency is sent by the mail worker, so the public page never waits on SMTP."""
import secrets,smtplib
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.validators import validate_email
from django.db import transaction
from django.http import Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from .models import TrialRequest,SiteSettings
from .crypto import protect
from .presets import PRESETS,CHOICES

MAX_ATTEMPTS=5
FIELDS=[("first_name","Nome",60,True),("last_name","Cognome",60,True),("email","Email",160,True),("phone","Telefono",40,False),
        ("business","Nome dell'attività",120,False),("message","Messaggio",600,False)]

def available():
    return settings.EDITION=="cloud" and SiteSettings.current().trial_enabled
def business_types():
    return [("","Altro, appuntamenti generici")]+CHOICES[1:]
def type_label(key):return PRESETS[key]["label"] if key in PRESETS else "Altro, appuntamenti generici"

def clean(post):
    data={}
    for key,label,limit,required in FIELDS:
        value=post.get(key,"").strip()
        if required and not value:raise ValidationError(f"Indica {label.lower()}.")
        if len(value)>limit:raise ValidationError(f"{label}: massimo {limit} caratteri.")
        data[key]=value
    validate_email(data["email"])
    preset=post.get("preset","")
    if preset and preset not in PRESETS:raise ValidationError("Tipo di attività non valido.")
    if post.get("privacy")!="on":raise ValidationError("Serve il consenso per essere ricontattato.")
    return data,preset

def request_view(request):
    """Public form; also reachable without JavaScript at /prova/."""
    from .identity import throttle
    if not available():raise Http404
    error="";sent=False
    if request.method=="POST":
        if request.POST.get("website"):sent=True  # filled only by bots: answer as if it went through
        else:
            throttle(request,"trial-ip",5,3600)
            try:
                data,preset=clean(request.POST)
                if TrialRequest.objects.filter(status="new").count()>=500:raise ValidationError("Al momento non riusciamo a ricevere altre richieste. Riprova tra qualche giorno.")
                with transaction.atomic():
                    r=TrialRequest(preset=preset)
                    r.pii=protect({**data,"privacy_at":timezone.now().isoformat()},"trial:"+str(r.pk));r.save()
                sent=True
            except ValidationError as ex:error=" ".join(ex.messages)
    return render(request,"login.html",{"pricing_on":SiteSettings.current().pricing_enabled,"trial_open":True,"trial_sent":sent,"trial_error":error,"business_types":business_types(),"form_values":request.POST if error else {}})

# the email to the agency, sent by the mail worker
def recipient(site=None):
    site=site or SiteSettings.current()
    return site.trial_email or site.support_email
def notify_pending():
    from .site import agency_smtp
    from .mail import send_message
    site=SiteSettings.current();config=agency_smtp(site);to=recipient(site);sent=0
    if not config or not to:return 0
    for r in TrialRequest.objects.filter(notified__isnull=True,attempts__lt=MAX_ATTEMPTS,created__gte=timezone.now()-timedelta(days=7)).order_by("created")[:20]:
        # the worker loops every 10 seconds: wait 2, 4, 8, 16 minutes after a failure, so an outage does not use up the attempts
        if r.attempts and timezone.now()<r.created+timedelta(minutes=2**r.attempts):continue
        d=r.data
        body=("Nuova richiesta di prova dal portale "+site.name+"\n\n"
              f"Nome: {d['first_name']} {d['last_name']}\nEmail: {d['email']}\nTelefono: {d.get('phone') or 'non indicato'}\n"
              f"Attività: {d.get('business') or 'non indicata'}\nTipo di attività: {type_label(r.preset)}\n"
              +(f"Messaggio: {d['message']}\n" if d.get("message") else "")
              +f"\nPer attivarla: {settings.PUBLIC_ORIGIN}/platform/richieste/\n")
        try:
            send_message(config,{"to":to,"subject":"Richiesta di prova: "+(d.get("business") or d["first_name"]+" "+d["last_name"]),"body":body},
                         f"<trial-{r.pk}-{secrets.token_hex(4)}@{settings.PUBLIC_ORIGIN.split('//')[-1]}>")
            TrialRequest.objects.filter(pk=r.pk).update(notified=timezone.now(),attempts=r.attempts+1);sent+=1
        except (OSError,smtplib.SMTPException,ValueError):
            TrialRequest.objects.filter(pk=r.pk).update(attempts=r.attempts+1)
    return sent

# agency area
def staff(request):
    if not request.user.is_staff:raise PermissionDenied
def annotate(rows):
    for r in rows:
        r.info=r.data;r.type_label=type_label(r.preset)
    return rows
def requests_view(request):
    from .identity import fresh
    staff(request);fresh(request)
    if settings.EDITION!="cloud":raise Http404
    if request.method=="POST":
        try:r=get_object_or_404(TrialRequest,pk=request.POST.get("request"))
        except ValidationError:raise Http404
        action=request.POST.get("action")
        if action=="archive":r.status="archived";r.save(update_fields=["status"]);messages.success(request,"Richiesta archiviata.")
        elif action=="restore":r.status="new";r.save(update_fields=["status"]);messages.success(request,"Richiesta riportata tra quelle da gestire.")
        elif action=="delete":r.delete();messages.success(request,"Richiesta eliminata, con i dati di chi l'ha inviata.")
        return redirect("trial_requests")
    show=request.GET.get("stato","new")
    if show not in ("new","done","archived"):show="new"
    rows=annotate(list(TrialRequest.objects.filter(status=show).select_related("client").order_by("-created")[:200]))
    counts={k:TrialRequest.objects.filter(status=k).count() for k in ("new","done","archived")}
    site=SiteSettings.current()
    return render(request,"trial_requests.html",{"rows":rows,"show":show,"counts":counts,"recipient":recipient(site),"enabled":site.trial_enabled})
def prefill(request_id):
    """Initial values of Nuovo cliente for a trial request."""
    from django.utils.text import slugify
    r=TrialRequest.objects.filter(pk=request_id,status="new").first()
    if not r:return None,{}
    d=r.data;site=SiteSettings.current()
    name=d.get("business") or (d["first_name"]+" "+d["last_name"])
    return r,{"name":name,"slug":slugify(name)[:60],"status":"trial","trial_until":timezone.localdate()+timedelta(days=site.trial_days),
              "email":d["email"],"preset":r.preset,"contact_name":(d["first_name"]+" "+d["last_name"]).strip(),"contact_phone":d.get("phone","")}
def new_count():
    from django.db import DatabaseError
    try:
        with transaction.atomic():return TrialRequest.objects.filter(status="new").count()
    except DatabaseError:return 0  # database not migrated yet
