import csv,io,re,secrets
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError,transaction
from django.http import HttpResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.urls import reverse
from django.db.models import Q
from .models import *
from .forms import LocationForm,ResourceForm,ServiceForm,OptionForm,ScheduleForm,ExceptionForm,OccurrenceForm,CampaignForm,ShiftForm
from .tenancy import scope
from .middleware import require_role
from .permissions import can,require,visible_bookings,shown,check_new_booking
from .booking import choice,change_status,BookingError,queue,capacity_available,can_undo,OPEN,ACCEPTED,MOVABLE,REASONS
from .calendar import fits,blocked_stay,blocked,covering,holidays
from .crypto import index,tenant_key,encrypt
from .mail import enqueue_campaign,campaign_contacts
from .site import mail_ready
from .client_mails import whatsapp
from .waitlist import WAIT_MINUTES
CATALOG={
 "locations":(Location,LocationForm,"Sedi"),
 "schedules":(Schedule,ScheduleForm,"Orari di apertura"),
 "exceptions":(ExceptionDay,ExceptionForm,"Chiusure e giorni speciali"),
 "services":(Service,ServiceForm,"Servizi"),
 "resources":(Resource,ResourceForm,"Risorse"),
 "options":(Option,OptionForm,"Varianti dei servizi"),
 "turns":(ShiftRule,ShiftForm,"Turni del ristorante"),
 "occurrences":(Occurrence,OccurrenceForm,"Eventi con data"),
}
# One plain sentence per catalog page: what it is and when it is needed.
CATALOG_HELP={
 "locations":"I luoghi dove lavori. Ogni servizio e ogni risorsa appartiene a una sede: con più sedi, crea qui ognuna e poi i servizi di ciascuna",
 "schedules":"I giorni e gli orari in cui si può prenotare. Valgono per tutta l'attività, oppure solo per una sede, un servizio o una risorsa",
 "exceptions":"Ferie, festivi e aperture straordinarie, anche dal giorno al giorno: hanno la precedenza sugli orari di apertura",
 "services":"Quello che il cliente sceglie quando prenota in una sede: al ristorante il Tavolo, dal parrucchiere Taglio, Piega e Colore, in hotel la Camera. La sede è il luogo, il servizio è cosa si prenota lì, con posti, durata e preavviso",
 "resources":"Chi o cosa viene prenotato: operatori, cabine, camere, tavoli, campi. Ogni risorsa appartiene a una sede",
 "options":"Versioni dello stesso servizio con durata diversa, per esempio taglio corto o lungo",
 "turns":"I turni che si ripetono ogni settimana, per esempio pranzo e cena, con i coperti di ciascuno",
 "occurrences":"Le date con posti limitati: un evento, un corso, un tour. Una data si può ripetere ogni settimana o ogni N settimane",
}
EDIT_TITLES={"locations":"sede","schedules":"fascia oraria","exceptions":"chiusura o giorno speciale","services":"servizio","resources":"risorsa","options":"variante","turns":"turno","occurrences":"data dell'evento"}
EDIT_HELP={
 "locations":"Il nome con cui riconosci la sede, l'indirizzo e il telefono che compaiono nelle email di conferma",
 "schedules":"Un giorno della settimana con un orario di apertura. Per pranzo e cena crea due fasce. Le prenotazioni già fatte non vengono mai annullate",
 "exceptions":"Uno o più giorni di chiusura, o con un orario diverso dal solito: valgono più degli orari di apertura. Le prenotazioni già fatte non vengono mai annullate",
 "services":"Quello che il cliente sceglie quando prenota. Compaiono solo le impostazioni che servono a questo tipo di prenotazione",
 "resources":"Una persona o una cosa che si prenota: un operatore, una cabina, una camera, un campo",
 "options":"La stessa prestazione con una durata diversa, per esempio Taglio corto e Taglio lungo",
 "turns":"Un turno che si ripete ogni settimana, per esempio la cena del venerdì dalle 19:30 alle 23:00, con i suoi coperti",
 "occurrences":"Una data dell'evento o del corso, con i posti disponibili. Con Ripeti crei in un colpo tutte le date della stagione",
}
# 2.10.0, every edition: the agency (the program's administrator) sets up type, locations and the technical side;
# the business runs the day and keeps its opening hours, closing days and staff (operators, rooms) up to date.
# 2.11.0: also its services with their variants and prices; the kind of booking of a service stays the agency's
# 2.14.0: and the turns of the restaurant and the dates of the events, everyday work there
OWNER_CATALOG={"schedules","exceptions","resources","services","options","turns","occurrences"}
def catalog_allowed(request,kind):return request.user.is_staff or kind in OWNER_CATALOG
def catalog_kinds(tenant=None):
    """Pages appear only when a service actually uses them, with the names the business uses: a restaurant
    has no operators and no variants, a salon calls its resources Operatori, a hotel Camere."""
    kinds=set(Service.objects.filter(active=True).values_list("kind",flat=True))
    hidden=set()
    if "shift" not in kinds and not ShiftRule.objects.filter(active=True).exists():hidden.add("turns")
    if "event" not in kinds and not Occurrence.objects.filter(active=True).exists():hidden.add("occurrences")
    if not kinds & {"resource","day"} and not Resource.objects.filter(active=True).exists():hidden.add("resources")
    if "resource" not in kinds and not Option.objects.filter(active=True).exists():hidden.add("options")
    out={k:v for k,v in CATALOG.items() if k not in hidden}
    if "resources" in out and tenant is not None:
        from .presets import units
        out["resources"]=(Resource,ResourceForm,units(tenant.business_type)[1])
    return out
# 2.14.0: the links of the booking page for each channel: the bookings keep the tag (Booking.source)
CHANNELS=[("google","Google, profilo dell'attività"),("instagram","Instagram"),("facebook","Facebook"),("whatsapp","WhatsApp"),("newsletter","Newsletter")]
def setup(request,slug):
    """The configuration of a business as a sequence of steps, each with its state."""
    require_role(request,schedule=True)
    t=request.tenant
    services=list(Service.objects.filter(active=True).select_related("location").order_by("location__name","name"))
    kinds={s.kind for s in services}
    locations=list(Location.objects.filter(active=True).order_by("name"))
    step=lambda **kw:kw
    steps=[
     step(key="locations",title="Sedi",done=bool(locations),count=len(locations),text=", ".join(l.name for l in locations) or "Nessuna sede",hint=CATALOG_HELP["locations"]),
     step(key="schedules",title="Orari di apertura",done=Schedule.objects.exists(),count=Schedule.objects.count(),text=f"Orari impostati: {Schedule.objects.count()}. Chiusure e giorni speciali: {ExceptionDay.objects.count()}",hint=CATALOG_HELP["schedules"],extra=("exceptions","Chiusure e giorni speciali")),
     step(key="services",title="Servizi",done=bool(services),count=len(services),text=", ".join(s.name+(" ("+s.location.name+")" if len(locations)>1 else "") for s in services[:6]) or "Nessun servizio",hint=CATALOG_HELP["services"],extra=("options","Varianti dei servizi") if "options" in catalog_kinds() else None),
    ]
    from .presets import units
    one,many,unit_help=units(t.business_type)
    if kinds & {"resource","day"} or Resource.objects.filter(active=True).exists():
        n=Resource.objects.filter(active=True).count()
        steps.append(step(key="resources",title=many,done=n>0,count=n,text=f"{n} {one.lower() if n==1 else many.lower()}" if n else f"Ancora nessuno: servono ai servizi su appuntamento o con {many.lower()}",hint=unit_help))
    if "shift" in kinds:steps.append(step(key="turns",title="Turni del ristorante",done=ShiftRule.objects.exists(),count=ShiftRule.objects.count(),text=f"{ShiftRule.objects.count()} turni settimanali",hint=CATALOG_HELP["turns"]))
    if "event" in kinds:steps.append(step(key="occurrences",title="Eventi con data",done=Occurrence.objects.filter(start__gte=timezone.now()).exists(),count=Occurrence.objects.count(),text=f"{Occurrence.objects.filter(start__gte=timezone.now()).count()} date in programma",hint=CATALOG_HELP["occurrences"]))
    members=Membership.objects.filter(tenant=t).count()
    agency=[
     {"title":"Persone e permessi","done":members>1,"text":f"{members} person{'a' if members==1 else 'e'} con accesso","link":"members" if request.user.is_staff else ""},
     # 2.6.0: the WordPress site has its own card below, with the connection code
     {"title":"Invio email","done":mail_ready(t),"text":"Invio proprio dell'attività" if t.smtp else ("Invio dell'agenzia" if mail_ready() else "Nessun invio configurato: le email restano in attesa"),"link":"smtp" if request.user.is_staff else ""},
    ]
    # site and email are set by the agency: business users do not even see them
    agency=[a for a in agency if a["link"]]
    steps=[s for s in steps if catalog_allowed(request,s["key"])]
    done=sum(1 for s in steps if s["done"])+sum(1 for a in agency if a["done"])
    from .presets import PRESETS
    from .profile import describe,MAX_LOCATIONS,default_name
    profile=describe(t);page=f"{settings.PUBLIC_ORIGIN.rstrip('/')}/book/{t.slug}/"
    shown=max(5,len(profile["locations"]))
    names=[l.name for l in profile["locations"]]+[default_name(i) for i in range(len(profile["locations"]),shown)]
    # agency_steps: "agency" is the agency's names in every page (context.py), the menu included
    return render(request,"setup.html",{"steps":steps,"agency_steps":agency,"done":done,"total":len(steps)+len(agency),"agency_names":settings.AGENCY_NAMES,"many":many,
        "profile":profile,"can_profile":can_profile(request),"presets":[(k,v["label"],v["hint"]) for k,v in PRESETS.items()],
        "counts":range(1,6),"max_locations":MAX_LOCATIONS,"absences":ABSENCES,"wait_minutes":WAIT_MINUTES,"names":names,"default_name":"Sede","wp_code":request.session.pop("wp_code",None),
        "wp_pending":bool(t.connect_hash and t.connect_until and t.connect_until>timezone.now()),"portal":settings.PUBLIC_ORIGIN.rstrip("/"),
        # 2.8.0: what this kind of business usually asks, and the allergies list of restaurants
        # the questions stay the agency's (2.10.0); the allergies list is the owner's too, with the services (2.14.0)
        "recommended":recommended(t)[0] if can_profile(request) else [],"allergens_card":t.business_type=="ristorante" or t.allergens_on,
        # 2.13.0: ready colours of Logo e aspetto, the same in app.css
        "swatches":["#7a55ad","#be185d","#b91c1c","#c2410c","#a16207","#15803d","#0f766e","#1d4ed8","#334155"],
        # 2.14.0: a link for each channel, and the frame for a site without WordPress that grows with its content (embed.js)
        "channels":[(label,f"{page}?src={key}") for key,label in CHANNELS],
        "embed":f'<iframe src="{page}?src=sito" title="Prenota" width="100%" height="900" loading="lazy" style="border:0" data-tdbs></iframe>\n<script src="{settings.PUBLIC_ORIGIN.rstrip("/")}/static/embed.js" async></script>'})

def setup_brand(request,slug):
    """2.13.0: Logo e aspetto, in Configura l'attività: the business's own logo (the menu, the portal, the booking page),
    its colour, the line under its name and its site."""
    from django.http import Http404
    from .forms import BrandForm
    if request.method!="POST":raise Http404
    require(request,"catalog.write")
    form=BrandForm(request.POST,request.FILES,instance=Tenant.objects.get(pk=request.tenant.pk))
    if form.is_valid():
        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=request.tenant.pk)
            t=form.save();audit(t,request.user,"tenant.brand",t.pk,logo=bool(t.logo))
        messages.success(request,"Logo e aspetto salvati: li vedi già nel menu a sinistra.")
    else:messages.error(request," ".join(e for errors in form.errors.values() for e in errors))
    return redirect(reverse("setup",args=[slug])+"#aspetto")

ABSENCES=range(0,6)
def setup_desk(request,slug):
    """2.14.0: Forza at the desk also for reception and operators: the owner's choice (Tenant.force_all). With the same
    card: after how many absences a client's online bookings wait for a yes, and how the waiting list works."""
    from django.http import Http404
    if request.method!="POST":raise Http404
    require(request,"users.manage")
    t=request.tenant;on=request.POST.get("force_all")=="on"
    try:
        absences=int(request.POST.get("absences_request",t.absences_request));minutes=int(request.POST.get("wait_minutes",t.wait_minutes))
        mode=request.POST.get("wait_mode",t.wait_mode)
        if absences not in ABSENCES or minutes not in WAIT_MINUTES or mode not in ("all","queue"):raise ValueError
        from .clients import parse_categories
        cats=parse_categories(request.POST.get("client_categories","")) if "client_categories" in request.POST else t.client_categories
    except (ValueError,BookingError) as ex:
        messages.error(request,str(ex) if isinstance(ex,BookingError) else "Scelta non valida.");return redirect(reverse("setup",args=[slug])+"#banco")
    Tenant.objects.filter(pk=t.pk).update(force_all=on,absences_request=absences,wait_mode=mode,wait_minutes=minutes,client_categories=cats)
    audit(t,request.user,"tenant.force_all",t.pk,on=on,absences=absences,wait_mode=mode,wait_minutes=minutes)
    messages.success(request,("Ora anche reception e operatori possono forzare al banco." if on else "Al banco forzano solo gli amministratori.")+" Regole salvate.")
    return redirect(reverse("setup",args=[slug])+"#banco")

def booking_qr(request,slug):
    """2.14.0: the QR of the booking page, for the counter, a flyer or the menu: its bookings are tagged qr."""
    import qrcode
    require(request,"catalog.write")
    buf=io.BytesIO();qrcode.make(f"{settings.PUBLIC_ORIGIN.rstrip('/')}/book/{request.tenant.slug}/?src=qr",border=2).save(buf,format="PNG")
    response=HttpResponse(buf.getvalue(),content_type="image/png")
    if request.GET.get("scarica"):response["Content-Disposition"]=f'attachment; filename="prenota-{request.tenant.slug}.png"'
    return response

def recommended(tenant):
    """2.8.0: the usual questions of this kind of business that some active service does not ask yet."""
    from .presets import questions
    wanted=questions(tenant.business_type)
    services=list(Service.objects.filter(active=True).order_by("name")) if wanted else []
    missing=[q for q in wanted if any(q["key"] not in {f.get("key") for f in s.fields} for s in services)]
    return missing,services

def recommended_view(request,slug):
    """Adds the chosen usual questions to every active service that does not ask them yet. Nothing is removed."""
    from django.http import Http404
    if request.method!="POST":raise Http404
    require(request,"catalog.write")
    if not can_profile(request):raise PermissionDenied
    missing,services=recommended(request.tenant)
    chosen=[q for q in missing if q["key"] in request.POST.getlist("q")]
    changed=0
    with transaction.atomic():
        for svc in services:
            have={f.get("key") for f in svc.fields}
            new=[q for q in chosen if q["key"] not in have]
            if new and len(svc.fields)+len(new)<=12:
                svc.fields=list(svc.fields)+new;svc.save(update_fields=["fields"]);changed+=1
        audit(request.tenant,request.user,"catalog.questions",request.tenant.pk,questions=len(chosen),services=changed)
    if changed:messages.success(request,f"Domande aggiunte a {changed} serviz{'io' if changed==1 else 'i'}: compaiono subito nel modulo di prenotazione. Le modifichi in ogni servizio, alla voce Richieste aggiuntive.")
    else:messages.info(request,"Nessuna domanda da aggiungere.")
    return redirect(reverse("setup",args=[slug])+"#consigliati")

def allergens_view(request,slug):
    """2.8.0: the allergies and intolerances the booking form asks about, as the business wants them: since 2.14.0
    kept by whoever keeps the services (the owner, staff with the permission, the agency)."""
    require(request,"catalog.write")
    from .presets import ALLERGENS
    t=request.tenant
    if request.method=="POST":
        action=request.POST.get("action");items=list(t.allergens or [])
        try:
            if action=="toggle":
                t.allergens_on=request.POST.get("on")=="1"
                if t.allergens_on and not items:items=list(ALLERGENS)
                text="Il modulo di prenotazione ora chiede intolleranze e allergie." if t.allergens_on else "Il modulo non le chiede più. Quelle già segnalate restano nelle prenotazioni."
            elif action=="add":
                name=" ".join(request.POST.get("name","").split())
                if not 1<=len(name)<=60 or any(ord(c)<32 for c in name):raise BookingError("Scrivi un nome, al massimo 60 caratteri.")
                if name.casefold() in {i.casefold() for i in items}:raise BookingError(name+" è già nell'elenco.")
                if len(items)>=40:raise BookingError("Al massimo 40 voci.")
                items.append(name);text="Aggiunta: "+name+"."
            elif action=="remove":
                name=request.POST.get("name","")
                items=[i for i in items if i!=name];text="Tolta: "+name+". Resta nelle prenotazioni dove era già segnalata."
            elif action=="reset":items=list(ALLERGENS);text="Elenco ripristinato: i 14 allergeni di legge più il lattosio."
            else:raise BookingError("Operazione non valida.")
            t.allergens=items;t.save(update_fields=["allergens","allergens_on"])
            audit(t,request.user,"allergens."+action,t.pk,items=len(items),on=t.allergens_on)
            messages.success(request,text)
        except BookingError as ex:messages.error(request,str(ex))
        return redirect("allergens",slug)
    return render(request,"allergens.html",{"items":t.allergens or [],"on":t.allergens_on,"standard":ALLERGENS})

def can_profile(request):
    """Type of business, locations and the WordPress code: the agency, in every edition (2.10.0)."""
    return request.user.is_staff

def setup_action(request,slug):
    """2.6.0: the profile of the business in one go, and the code that connects the WordPress site."""
    from django.http import Http404
    from .identity import fresh
    if request.method!="POST":raise Http404
    require_role(request,schedule=True)
    if not can_profile(request):raise PermissionDenied
    fresh(request);t=request.tenant;action=request.POST.get("action")
    if action=="profile":
        from .profile import apply_profile,keep_profile,clean_names,MAX_LOCATIONS
        from .presets import PRESETS
        preset=request.POST.get("business_type","")
        try:
            raw=request.POST.get("locations","1")
            count=int(request.POST.get("locations_custom","") if raw=="custom" else raw)
            if not 1<=count<=MAX_LOCATIONS:raise ValueError(f"Le sedi vanno da 1 a {MAX_LOCATIONS}.")
            names=clean_names([request.POST.get(f"name_{i}","") for i in range(1,count+1)],count)
            # the same type keeps what the business made; another type replaces it, only once confirmed
            if preset and preset==t.business_type:r=keep_profile(t,names,request.user)
            elif t.business_type in PRESETS and not request.POST.get("confirm_type"):
                raise ValueError(f"Per passare a {PRESETS[preset]['label'] if preset in PRESETS else 'un altro tipo'} spunta la conferma: servizi, operatori e orari vengono sostituiti. Le prenotazioni restano.")
            else:r=apply_profile(t,preset,names,request.user)
        except ValueError as ex:
            messages.error(request,str(ex) or "Dati non validi.");return redirect(reverse("setup",args=[slug])+"#tipo")
        if r["kept"]:
            text=f"Sedi aggiornate: {r['locations']} {'sede' if r['locations']==1 else 'sedi'}. Servizi, operatori e orari restano come li hai impostati"
            if r["services"]:text+=f"; le sedi nuove partono con {r['services']} servizi del tipo"
            if r["archived"]:text+=f". {r['archived']} elementi usati da prenotazioni sono stati disattivati, non cancellati"
            messages.success(request,text+".");return redirect(reverse("setup",args=[slug])+"#tipo")
        text=f"Configurazione applicata: {r['label']}, {r['locations']} {'sede' if r['locations']==1 else 'sedi'}, {r['services']} servizi"
        if r["resources"]:
            from .presets import units
            text+=f", {r['resources']} {units(r['type'])[1].lower()}"
        if r["dates"]:text+=f", {r['dates']} date in programma"
        if r["archived"]:text+=f". {r['archived']} elementi usati da prenotazioni sono stati disattivati, non cancellati"
        messages.success(request,text+".");return redirect(reverse("setup",args=[slug])+"#tipo")
    if action=="wp_code":
        from .wpconnect import new_code
        request.session["wp_code"]=new_code(t,request.user)
        return redirect(reverse("setup",args=[slug])+"#wordpress")
    if action=="wp_remove":
        site=request.POST.get("site","")
        if site in (t.embed_origins or []):
            t.embed_origins=[o for o in t.embed_origins if o!=site];t.save(update_fields=["embed_origins"])
            audit(t,request.user,"wordpress.removed",t.pk,site=site)
            messages.success(request,site+" non può più mostrare il modulo di prenotazione.")
        return redirect(reverse("setup",args=[slug])+"#wordpress")
    raise Http404
def guide(request,slug=None):
    from .permissions import role_label
    role=role_label(request.member.role) if getattr(request,"member",None) else ""
    return render(request,"guide.html",{"role":role,"staff":request.user.is_staff})
def home(request):
    if request.user.is_staff:return redirect("platform")
    members=Membership.objects.filter(user=request.user).select_related("tenant")
    if members.count()==1:return redirect("dashboard",members.first().tenant.slug)
    return render(request,"home.html",{"memberships":members})
def outcome_rows(counts,total):
    # 2.14.0: done is what was closed as done; what nobody closed is counted apart, no longer as done
    labels=[("done","Svolte","ok"),("open","Da chiudere","open"),("cancelled","Disdette","danger"),("no_show","Assenze","warn")]
    return [{"label":l,"value":counts[k],"pct":round(100*counts[k]/total) if total else 0,"tone":"tone-"+tone} for k,l,tone in labels]
def dashboard(request,slug):
    """2.4.0: the business at a glance. Every number comes from visible_bookings, so each role counts
    only what it may see (an operator his resources or his location)."""
    from django.db.models import Count
    from .agency import bars,WEEKS,week_start
    tz=ZoneInfo(request.tenant.timezone);now=timezone.now()
    today=now.astimezone(tz).date()
    start=datetime.combine(today,time.min,tz)
    end=start+timedelta(days=1)
    rows=visible_bookings(request,Booking.objects.filter(start__lt=end,end__gt=start)).select_related("service","resource").order_by("start")
    mine=lambda qs:visible_bookings(request,qs)
    since=now-timedelta(days=30);before=now-timedelta(days=60)
    received=mine(Booking.objects.filter(created__gte=since))
    received_n=received.count();previous=mine(Booking.objects.filter(created__gte=before,created__lt=since)).count()
    cancelled_n=received.filter(status="cancelled").count()
    metrics={"today":rows.filter(status__in=ACCEPTED).count(),
        "week":mine(Booking.objects.filter(start__gte=start,start__lt=start+timedelta(days=7),status__in=ACCEPTED)).count(),
        "upcoming":mine(Booking.objects.filter(start__gte=end,status__in=ACCEPTED)).count(),
        "received":received_n,"trend":round((received_n-previous)*100/previous) if previous else None,
        "cancelled":cancelled_n,"cancel_rate":round(100*cancelled_n/received_n) if received_n else 0,
        "contacts":Contact.objects.count() if can(request,"contacts.read") else None,"eligible":campaign_contacts().count() if can(request,"contacts.read") else None,
        "new_contacts":Contact.objects.filter(created__gte=since).count() if can(request,"contacts.read") else None}
    stats=None
    if can(request,"booking.read"):
        # how the last 30 days went, by appointment date
        past=mine(Booking.objects.filter(start__gte=start-timedelta(days=30),start__lt=start))
        counts={"done":past.filter(status="completed").count(),"open":past.filter(status__in=OPEN).count(),"cancelled":past.filter(status="cancelled").count(),"no_show":past.filter(status="no_show").count()}
        total=sum(counts.values())
        services=list(received.exclude(status="cancelled").values("snapshot__service").annotate(n=Count("id")).order_by("-n")[:5])
        top=max([s["n"] for s in services]+[1])
        weekly=[0]*WEEKS;first=week_start(today)-timedelta(weeks=WEEKS-1)
        for created in mine(Booking.objects.filter(created__gte=datetime.combine(first,time.min,tz))).values_list("created",flat=True):
            i=(created.astimezone(tz).date()-first).days//7
            if 0<=i<WEEKS:weekly[i]+=1
        days=[0]*7
        for s in mine(Booking.objects.filter(start__gte=start,start__lt=start+timedelta(days=7),status__in=ACCEPTED)).values_list("start",flat=True):
            i=(s.astimezone(tz).date()-today).days
            if 0<=i<7:days[i]+=1
        next_days=bars(days,today,width=300,height=170)
        names=["Oggi","Domani"]+[(today+timedelta(days=i)).strftime("%d/%m") for i in range(2,7)]
        for i,b in enumerate(next_days["bars"]):
            b["label"]=names[i];b["show"]=True
            if b["v"]:b["href"]="#giorno-"+(today+timedelta(days=i)).isoformat()  # 2.13.0: the bar opens that day in the list
        next_days["daily"]=True
        stats={"outcome":outcome_rows(counts,total),"outcome_total":total,"weekly":bars(weekly,first,width=440,height=170),"next_days":next_days,
            "services":[{"name":s["snapshot__service"] or "Servizio","n":s["n"],"share":round(100*s["n"]/top)} for s in services]}
    from .clients import mark
    shown_rows=mark(request,rows.select_related("tenant")[:50])
    for row in shown_rows:row.shown=shown(request,row.data)
    # 2.13.0: the next 7 days too, day by day, who and at what time (the list behind "Prossimi 7 giorni")
    # shortcut: at most 300 bookings in the list, the agenda has all of them
    week=[]
    for row in mine(Booking.objects.filter(start__gte=start,start__lt=start+timedelta(days=7)).exclude(status="cancelled")).select_related("service","resource","tenant").order_by("start")[:300]:
        row.shown=shown(request,row.data);day=row.start.astimezone(tz).date()
        if not week or week[-1]["day"]!=day:week.append({"day":day,"iso":day.isoformat(),"name":{0:"Oggi",1:"Domani"}.get((day-today).days,""),"rows":[]})
        week[-1]["rows"].append(row)
    # 2.14.0: the requests to answer, and what already happened that nobody closed (30 days), each with its buttons
    asks=mine(Booking.objects.filter(status="pending",end__gt=now-timedelta(days=30))).select_related("service","tenant").order_by("start")
    closing=mine(Booking.objects.filter(status__in=("confirmed","arrived","in_progress"),end__lte=now,end__gt=now-timedelta(days=30))).select_related("service","tenant").order_by("-start")
    # 2.14.0: who had the reminder with Confermo in the next 48 hours and did not answer, or wrote Non vengo too late: a call
    calls=mine(Booking.objects.filter(status="confirmed",start__gt=now,start__lte=now+timedelta(hours=48),snapshot__has_key="asked").exclude(snapshot__has_key="client_confirmed")).select_related("service","tenant").order_by("start")
    todo={"asks":list(asks[:20]),"asks_n":asks.count(),"closing":list(closing[:20]),"closing_n":closing.count(),"calls":list(calls[:20]),"calls_n":calls.count()}
    for row in todo["asks"]+todo["closing"]+todo["calls"]:row.shown=shown(request,row.data)
    for row in todo["calls"]:row.whatsapp=whatsapp(request.tenant,row.shown.get("phone",""),row)
    # the last quick action, to take back for a few minutes
    undo=visible_bookings(request,Booking.objects.filter(pk=uid(request.session.pop("undo",None)))).first() if request.session.get("undo") else None
    if undo and can_undo(undo):undo.shown=shown(request,undo.data)
    else:undo=None
    # 2.14.0: the turns of a restaurant on a day (today, or the one picked), to close or open again for that day only
    turn_day,turn_list=today,None
    if can(request,"catalog.write"):
        from .agenda import turns
        try:turn_day=date.fromisoformat(request.GET.get("turni",""))
        except ValueError:pass
        turn_list=turns(request.tenant,turn_day)
    # 2.14.0: the waiting list, how many wait and how many have a place freed for them; the birthdays of the week
    from .models import WaitEntry
    waits=WaitEntry.objects.filter(status="waiting",last__gte=today) if can(request,"booking.read") else WaitEntry.objects.none()
    wait={"n":waits.count(),"free":waits.filter(snapshot__has_key="free").count()}
    born=None
    if can(request,"contacts.read") and can(request,"pii.read"):
        from .clients import birthdays
        born=birthdays(request.tenant,today)[:20]
    return render(request,"dashboard.html",{"wait":wait,"born":born,"rows":shown_rows,"week":week,"metrics":metrics,"stats":stats,"today":today,"pending":Delivery.objects.filter(status="pending").exclude(channel="webhook").count(),"mail_issues":Delivery.objects.filter(status__in=["failed","unknown"]).exclude(channel="webhook").count(),
        "todo":todo,"undo":undo,"walkin":Service.objects.filter(active=True,kind__in=("resource","capacity")).exists(),"turns":turn_list,"turn_day":turn_day,"many_locations":Location.objects.filter(active=True).count()>1})
def period(request,default_days=7):
    tz=ZoneInfo(request.tenant.timezone)
    today=timezone.now().astimezone(tz).date()
    try:
        first=date.fromisoformat(request.GET.get("from",today.isoformat()))
        last=date.fromisoformat(request.GET.get("to",(first+timedelta(days=default_days-1)).isoformat()))
        if not 0<=(last-first).days<=366:raise ValueError()
    except ValueError:raise ValidationError("Intervallo non valido, massimo 367 giorni.")
    return datetime.combine(first,time.min,tz),datetime.combine(last+timedelta(days=1),time.min,tz)
def bookings(request,slug):
    require(request,"booking.read")
    # 2.10.0: appointments open on the day, one column for each operator; the list stays with ?vista=elenco
    # 2.11.0: the week and the month too, for every business
    from .agenda import fits,day_view,week_view,month_view
    agenda=fits(request);view=request.GET.get("vista","")
    if view=="settimana":return week_view(request)
    if view=="mese":return month_view(request)
    if agenda and view!="elenco" and "from" not in request.GET and "to" not in request.GET:return day_view(request)
    try:a,b=period(request)
    except ValidationError as ex:return render(request,"message.html",{"title":"Filtro non valido","text":str(ex)},status=400)
    rows=visible_bookings(request,Booking.objects.filter(start__lt=b,end__gt=a)).select_related("service","resource","tenant").order_by("start")
    if request.GET.get("status"):rows=rows.filter(status=request.GET["status"])
    page=Paginator(rows,50).get_page(request.GET.get("page"))
    from .clients import mark
    for row in mark(request,page):row.shown=shown(request,row.data)
    return render(request,"bookings.html",{"page":page,"start":a.date(),"end":(b-timedelta(days=1)).date(),"agenda":agenda})
def booking_detail(request,slug,booking_id):
    require(request,"booking.read")
    booking=get_object_or_404(visible_bookings(request,Booking.objects.all()).select_related("service","resource"),pk=booking_id)
    from .presets import record
    spec=record(request.tenant) if booking.contact_id else None
    if request.method=="POST" and request.POST.get("action") in ("treatment","treatment_remove") and spec:
        try:messages.success(request,record_action(request,booking.contact,booking))
        except BookingError as ex:messages.error(request,str(ex))
        return redirect(reverse("booking_detail",args=[slug,booking_id])+"#scheda")
    plan=None
    if request.method=="POST" and request.POST.get("action") in ("series_check","series_make"):
        # 2.14.0: Ripeti from the desk: first the dates with the ones that cannot be booked, then the free ones made
        require(request,"booking.write")
        from .booking import series_plan,series_create
        try:
            every=int(request.POST.get("every") or 0);until=date.fromisoformat(request.POST.get("until") or "")
        except ValueError:every,until=0,None
        try:
            if request.POST["action"]=="series_make":
                made=series_create(request.tenant,booking,every,until,request.user)
                messages.success(request,f"Serie creata: {made} date in più." if made else "Nessuna data libera da aggiungere.")
                return redirect(reverse("booking_detail",args=[slug,booking_id])+"#serie")
            plan={"rows":series_plan(request.tenant,booking,every,until),"every":every,"until":until}
            plan["free"]=sum(1 for _,why in plan["rows"] if not why)
        except BookingError as ex:messages.error(request,str(ex))
    if request.method=="POST" and request.POST.get("action")=="participants":
        # 2.14.0: the names of who comes with the client, optional, sealed with the booking's data
        require(request,"booking.write","pii.read")
        names=[" ".join(x.split())[:100] for x in request.POST.get("participants","").splitlines() if x.strip()]
        if len(names)>max(booking.party,1) or any(any(ord(c)<32 for c in n) for n in names):messages.error(request,f"Al massimo {booking.party} nomi, uno per riga.")
        else:
            pii=booking.data;pii["participants"]=names
            booking.pii=encrypt(request.tenant,pii,"booking:"+str(booking.pk));booking.save(update_fields=["pii"])
            audit(request.tenant,request.user,"booking.participants",booking.pk,count=len(names))
        return redirect(reverse("booking_detail",args=[slug,booking_id])+"#partecipanti")
    if request.method=="POST" and plan is None and request.POST.get("action") not in ("series_check","series_make"):
        action=request.POST.get("action")
        require(request,*(("booking.write","notes.read") if action=="notes" else ("booking.cancel",)))
        back=request.POST.get("back")=="dashboard"  # 2.14.0: the quick actions of the dashboard go back there
        try:
            if action=="notes":
                pii=booking.data
                pii["internal_notes"]=request.POST.get("internal_notes","")[:4000]
                booking.pii=encrypt(request.tenant,pii,"booking:"+str(booking.pk));booking.save(update_fields=["pii"])
                audit(request.tenant,request.user,"booking.notes",booking.pk)
            elif action=="undo":change_status(request.tenant,booking,None,request.user,undo=True)
            elif action=="reject":change_status(request.tenant,booking,"cancelled",request.user,reason="rejected")
            else:
                change_status(request.tenant,booking,action,request.user,reason=request.POST.get("reason",""),note=request.POST.get("note",""))
                # 2.14.0: "anche le prossime date della serie": the others quietly, the client got the email of this one
                if action=="cancelled" and booking.series and request.POST.get("whole_series")=="on":
                    for other in Booking.objects.filter(series=booking.series,start__gt=booking.start,status__in=OPEN):
                        change_status(request.tenant,other,"cancelled",request.user,reason=request.POST.get("reason",""),quiet=True)
            if back:
                request.session["undo"]=str(booking.pk);return redirect("dashboard",slug)
            return redirect("booking_detail",slug,booking_id)
        except BookingError as ex:
            messages.error(request,str(ex))
            if back:return redirect("dashboard",slug)
    from .clients import mark
    mark(request,[booking])  # 2.14.0: VIP, tags, absences of the client, for who sees the client cards
    data=shown(request,booking.data)
    answers=[{"label":booking.snapshot.get("field_labels",{}).get(k,k),"value":"Sì" if v is True else v} for k,v in data.get("answers",{}).items() if v not in ("",None,False)]
    tz=ZoneInfo(request.tenant.timezone);day=booking.start.astimezone(tz).date()
    ctx={"booking":booking,"data":data,"answers":answers,"spec":spec,"back_day":day.isoformat(),
        # 2.8.0: the note kept on the client card shows on every booking, as the main booking software do
        "client_note":booking.contact.data.get("staff_notes","") if booking.contact_id and can(request,"notes.read") else "",
        # 2.12.0: a room is not "with": it has its own word, Camera, Alloggio, Mezzo
        "unit":__import__("core.presets",fromlist=["units"]).units(request.tenant.business_type)[0],
        # 2.14.0: a chat with the client about this booking, for whoever sees the phone (masked: no number, no button)
        "whatsapp":whatsapp(request.tenant,data.get("phone",""),booking),
        # 2.14.0: the statuses: arrived from the day of the booking, in progress where something is being done
        "undo":can_undo(booking),"reasons":REASONS,"progress":booking.service.kind in ("resource","capacity"),
        "today_ok":day<=timezone.now().astimezone(tz).date(),"test_due":test_due(booking),"plan":plan,
        "series_next":Booking.objects.filter(series=booking.series,start__gt=booking.start,status__in=OPEN).count() if booking.series else 0,
        "repeatable":booking.service.kind in ("resource","capacity") and bool(booking.contact_id) and booking.status in OPEN,
        "class_list":booking.service.kind in ("capacity","event")}
    if spec and can(request,"notes.read"):
        from .clients import treatments,from_booking
        entries=treatments(booking.contact);prefill=from_booking(booking,tz)
        # what was done before this appointment, the newest first: what the operator wants to know before starting
        ctx.update(linked=[t for t in entries if t.booking_id==booking.pk],earlier=[t for t in entries if t.booking_id!=booking.pk and t.date<prefill["date"]][:3],prefill=prefill,record_open=booking.start<=timezone.now() and booking.status in ACCEPTED,**record_options(spec))
    from .tables import names
    ctx["table_names"]=names(booking)
    return render(request,"booking_detail.html",ctx)
def test_due(booking):
    """2.14.0: the service wants a test done within so many days: what the desk must know, never a refusal."""
    days=booking.service.test_days
    if not days or not booking.contact_id or booking.status not in OPEN:return ""
    done=booking.contact.data.get("test_date")
    if not done:return "nella scheda del cliente non c'è un test"
    if date.fromisoformat(done)+timedelta(days=days)<booking.start.astimezone(ZoneInfo(booking.tenant.timezone)).date():return f"l'ultimo test è del {date.fromisoformat(done):%d/%m/%Y}, più di {days} giorni prima"
    return ""
def attendees(request,slug,booking_id):
    """2.14.0: Iscritti of a class or an event date: who is booked, how many, the participants' names, the tick of who
    came (Presente: done, Assente: absent, back with Togli); the substitute instructor written on every booking of the
    date, the original one stays where it was (the agenda does not move)."""
    require(request,"booking.read")
    ref=get_object_or_404(visible_bookings(request,Booking.objects.all()).select_related("service"),pk=booking_id)
    rows=visible_bookings(request,Booking.objects.filter(service=ref.service,start=ref.start,occurrence=ref.occurrence)).exclude(status="cancelled").order_by("created")
    if request.method=="POST":
        require(request,"booking.write")
        action=request.POST.get("action")
        try:
            if action=="substitute":
                r=Resource.objects.filter(active=True,pk=uid(request.POST.get("resource"))).first()
                for b in rows:
                    if r:b.snapshot["substitute"]=r.name
                    else:b.snapshot.pop("substitute",None)
                    b.save(update_fields=["snapshot"])
                audit(request.tenant,request.user,"booking.substitute",ref.pk,resource=str(r.pk) if r else "")
            else:
                b=get_object_or_404(rows,pk=uid(request.POST.get("booking")))
                change_status(request.tenant,b,{"present":"completed","absent":"no_show"}.get(action,"confirmed"),request.user)
        except BookingError as ex:messages.error(request,str(ex))
        return redirect("attendees",slug,booking_id)
    rows=list(rows)
    for b in rows:b.shown=shown(request,b.data)
    return render(request,"attendees.html",{"ref":ref,"rows":rows,"people":sum(b.party for b in rows),"started":ref.start<=timezone.now(),
        "resources":Resource.objects.filter(active=True,location=ref.location).order_by("name"),"substitute":next((b.snapshot.get("substitute") for b in rows if b.snapshot.get("substitute")),"")})
def uid(value):
    import uuid
    try:return uuid.UUID(str(value))
    except ValueError:return None
def record_options(spec):
    """Quick picks of the record form: the catalog services and people, plus what this kind of business records."""
    names=list(dict.fromkeys(list(Service.objects.filter(active=True).order_by("name").values_list("name",flat=True))+spec["suggest"]))
    return {"what_options":names,"staff_options":list(Resource.objects.filter(active=True).order_by("name").values_list("name",flat=True))}
def record_action(request,contact,booking=None):
    """2.8.0: add, edit or remove one entry of the client record. Returns the message for the page."""
    from .clients import clean_treatment,save_treatment
    require(request,"booking.write","notes.read")
    item=None
    if request.POST.get("treatment"):
        item=Treatment.objects.filter(contact=contact,pk=uid(request.POST["treatment"])).first()
        if not item:raise BookingError("Voce non trovata: forse è già stata eliminata.")
    with transaction.atomic():
        if request.POST.get("action")=="treatment_remove":
            if not item:raise BookingError("Voce non trovata.")
            item.delete();audit(request.tenant,request.user,"treatment.removed",contact.pk)
            return "Voce eliminata dalla scheda."
        d=clean_treatment(request.POST,timezone.now().astimezone(ZoneInfo(request.tenant.timezone)).date())
        if booking is None and not item and request.POST.get("booking"):
            # the booking it comes from: only one of this client, and only one this user may see
            booking=visible_bookings(request,Booking.objects.filter(contact=contact)).filter(pk=uid(request.POST["booking"])).first()
        t=save_treatment(request.tenant,contact,d,request.user,treatment=item,booking=None if item else booking)
        audit(request.tenant,request.user,"treatment.saved",contact.pk,entry=str(t.pk),edited=item is not None)
    return "Modifica salvata." if item else "Salvato nella scheda del cliente."
def contacts(request,slug):
    """2.7.0: search by name, phone or email; visits and last visit of each client; clients added by hand."""
    from .clients import search as find,clean,find_or_create,visit_counts
    require(request,"contacts.read")
    error=""
    if request.method=="POST":
        require(request,"booking.write")
        try:
            d=clean(request.POST)
            with transaction.atomic():
                c,created=find_or_create(request.tenant,d)
                audit(request.tenant,request.user,"contact.created" if created else "contact.found",c.pk)
            if not created:messages.info(request,"Questo cliente c'era già: ecco la sua scheda.")
            return redirect("contact_detail",slug,c.pk)
        except BookingError as ex:error=str(ex)
    q=request.GET.get("q",request.GET.get("email","")).strip()
    # 2.14.0: the clients with a tag, the VIPs
    from .clients import known_tags,with_tag,opener
    rows=Contact.objects.exclude(email_index__startswith="erased:")
    tag=request.GET.get("tag","").strip();vip=request.GET.get("vip")=="1"
    if tag:rows=with_tag(request.tenant,rows,tag)
    if vip:rows=rows.filter(vip=True)
    if q:page=Paginator(find(request.tenant,q,limit=200,rows=rows),50).get_page(request.GET.get("page"))
    else:page=Paginator(rows.order_by("-created"),50).get_page(request.GET.get("page"))
    counts=visit_counts(list(page));read=opener(request.tenant)
    for c in page:c.shown=shown(request,read(c));c.stats=counts.get(c.pk,{})
    from urllib.parse import urlencode
    keep="&"+urlencode({k:v for k,v in (("tag",tag),("vip","1" if vip else "")) if v}) if tag or vip else ""
    return render(request,"contacts.html",{"page":page,"search":q,"error":error,"form":request.POST if error else {},"tags":known_tags(request.tenant),"tag":tag,"vip":vip,
        "filtered":bool(q or tag or vip),"keep":keep})
def contacts_search(request,slug):
    """Quick search for the manual booking form: at most 8 clients, only for who sees names."""
    from .clients import search as find
    require(request,"contacts.read","pii.read")
    from django.http import JsonResponse
    return JsonResponse({"results":[{"id":str(c.pk),"first_name":c.found.get("first_name",""),"last_name":c.found.get("last_name",""),
        "email":c.found.get("email",""),"phone":c.found.get("phone","")} for c in find(request.tenant,request.GET.get("q",""),limit=8)]})
def contacts_import(request,slug):
    """Clients and treatment history from the old program (CSV or Excel): file, columns, preview, import.
    Needs what adding clients and treatments by hand needs; the marketing consents also the privacy permission."""
    from . import importer
    from .presets import record
    require(request,"contacts.read","pii.read","notes.read","booking.write")
    t=request.tenant;spec=record(t);consent_ok=can(request,"privacy.manage")
    key="import:"+slug;token=request.session.get(key)
    stash=importer.unstash(t,token) if token else None
    action=request.POST.get("action") if request.method=="POST" else None
    ctx={"spec":spec,"consent_ok":consent_ok,"error":""}
    if action=="upload":
        f=request.FILES.get("file")
        try:
            if not f:raise BookingError("Scegli il file da importare.")
            if f.size>importer.MAX_BYTES:raise BookingError("File troppo grande: al massimo 20 MB.")
            head,rows=importer.read(f.read())
            importer.drop(token)
            request.session[key]=importer.stash(t,f.name,head,rows)
            return redirect("contacts_import",slug)
        except BookingError as ex:ctx["error"]=str(ex)
    elif action=="reset" or (token and not stash):
        importer.drop(token);request.session.pop(key,None)
        if action=="reset":return redirect("contacts_import",slug)
    elif stash:
        head,rows=stash["head"],stash["rows"]
        choices=importer.fields(spec is not None,consent_ok);allowed=dict(choices)
        if action in ("preview","import"):mapping=[m if m in allowed else "" for m in (request.POST.get(f"col{i}","") for i in range(len(head)))]
        else:mapping=importer.guess(head,spec is not None,consent_ok)
        ask=request.POST.get("ask")=="1" and consent_ok and t.marketing_enabled and "consent" in mapping
        checked=token+"|"+",".join(mapping)+(":ask" if ask else "")  # bound to this very file, not only to its columns
        # the import runs only on the columns of the preview just seen
        if action=="import" and request.POST.get("checked")!=checked:action="preview";ctx["error"]="Hai cambiato qualche colonna: controlla di nuovo l'anteprima."
        try:
            if action=="import":
                with transaction.atomic():
                    # NO KEY UPDATE: bookings wait as with every writer, while a card saved meanwhile can still
                    # write its audit row; the cards themselves are locked by the plan
                    Tenant.objects.select_for_update(no_key=True).get(pk=t.pk)
                    done=importer.apply(t,importer.plan(t,head,rows,mapping,spec is not None,consent_ok,lock=True),request.user,ask=ask,batch=token)
                    audit(t,request.user,"contacts.imported",token,**{k:done[k] for k in ("rows","created","updated","treatments","consents","requested")})
                importer.drop(token);request.session.pop(key,None)
                messages.success(request,importer.summary(done))
                return redirect("contacts",slug)
            if action=="preview":ctx["plan"]=importer.plan(t,head,rows,mapping,spec is not None,consent_ok)
        except BookingError as ex:ctx["error"]=str(ex)
        except IntegrityError:ctx["error"]="Nel frattempo qualcuno ha aggiunto uno di questi clienti: premi di nuovo Importa, l'anteprima lo terrà in conto."
        ctx.update(name=stash["name"],count=len(rows),fields=choices,ask=ask,checked=checked,
            columns=[{"i":i,"head":h,"samples":importer.samples(rows,i),"field":mapping[i]} for i,h in enumerate(head)])
    return render(request,"contacts_import.html",ctx)
def contact_detail(request,slug,contact_id):
    from .privacy import history,export_contact,erase_contact
    require(request,"contacts.read")
    contact=get_object_or_404(Contact.objects.exclude(email_index__startswith="erased:"),pk=contact_id)
    if (request.method=="POST" and request.POST.get("action") not in ("notes","card","treatment","treatment_remove","discount","notices","profile","sign")) or request.GET.get("download"):
        require(request,"privacy.manage")
        if request.GET.get("download"):
            import json
            audit(request.tenant,request.user,"privacy.exported",contact.pk)
            response=HttpResponse(json.dumps(export_contact(request.tenant,contact),ensure_ascii=False,indent=2),content_type="application/json; charset=utf-8")
            response["Content-Disposition"]=f'attachment; filename="dati-cliente-{str(contact.pk)[:8]}.json"'
            return response
        from .identity import fresh
        fresh(request)
        try:
            if request.POST.get("confirm")!="CANCELLA":raise BookingError("Scrivi CANCELLA per confermare.")
            erase_contact(request.tenant,contact,request.user)
            messages.success(request,"Dati del cliente cancellati. Lo storico dei consensi resta, senza dati personali.")
            return redirect("contacts",slug)
        except BookingError as ex:messages.error(request,str(ex))
    from .clients import history as visits,update,treatments,from_booking
    from .presets import record
    spec=record(request.tenant)
    if request.method=="POST" and request.POST.get("action") in ("treatment","treatment_remove") and spec:
        try:messages.success(request,record_action(request,contact))
        except BookingError as ex:messages.error(request,str(ex))
        return redirect(reverse("contact_detail",args=[slug,contact.pk])+"#scheda")
    if request.method=="POST" and request.POST.get("action")=="discount":
        # 2.11.0: a price decision, like the price list: who edits services
        require(request,"catalog.write")
        from .clients import set_discount
        try:
            with transaction.atomic():
                changed=set_discount(contact,request.POST.get("discount_kind",""),request.POST.get("discount",""))
                audit(request.tenant,request.user,"contact.discount",contact.pk,kind=contact.discount_kind,value=str(contact.discount),bookings=changed)
            messages.success(request,("Sconto salvato." if contact.discount_kind else "Sconto tolto.")+((f" Aggiornata {changed} prenotazione in programma." if changed==1 else f" Aggiornate {changed} prenotazioni in programma.") if changed else ""))
        except BookingError as ex:messages.error(request,str(ex))
        return redirect(reverse("contact_detail",args=[slug,contact.pk])+"#sconto")
    if request.method=="POST" and request.POST.get("action")=="notices":
        # 2.13.0: what the client said at the desk or on the phone: emails about his bookings yes or no
        require(request,"booking.write")
        contact.notices=request.POST.get("notices")=="on";contact.save(update_fields=["notices"])
        audit(request.tenant,request.user,"contact.notices",contact.pk,on=contact.notices)
        messages.success(request,"Il cliente riceve le email delle sue prenotazioni." if contact.notices else "Il cliente non riceve più le email delle sue prenotazioni.")
        return redirect(reverse("contact_detail",args=[slug,contact.pk])+"#privacy")
    if request.method=="POST" and request.POST.get("action")=="sign":
        # 2.14.0: a consent signed on the tablet at the desk, sealed in the card
        require(request,"booking.write","pii.read")
        from .clients import sign_consent
        try:
            with transaction.atomic():
                sign_consent(request.tenant,contact,request.POST,request.user)
                audit(request.tenant,request.user,"contact.signed",contact.pk)
            messages.success(request,"Consenso firmato e salvato nella scheda.")
        except BookingError as ex:messages.error(request,str(ex))
        return redirect(reverse("contact_detail",args=[slug,contact.pk])+"#firme")
    if request.method=="POST" and request.POST.get("action")=="profile":
        # 2.14.0: tags, birthday, VIP and "not bookable online": who edits the card's data
        require(request,"booking.write","pii.read")
        from .clients import clean_profile,save_profile
        try:
            with transaction.atomic():
                p=clean_profile(request.POST,timezone.now().astimezone(ZoneInfo(request.tenant.timezone)).date(),request.tenant.client_categories)
                save_profile(request.tenant,contact,p)
                audit(request.tenant,request.user,"contact.profile",contact.pk,vip=p["vip"],online_blocked=p["online_blocked"],tags=len(p["tags"]),birthday=bool(p["birthday"]))
            messages.success(request,"Scheda aggiornata.")
        except BookingError as ex:messages.error(request,str(ex))
        return redirect(reverse("contact_detail",args=[slug,contact.pk])+"#profilo")
    if request.method=="POST" and request.POST.get("action") in ("notes","card"):
        require(request,"booking.write",*(("notes.read",) if request.POST["action"]=="notes" else ("pii.read",)))
        try:
            with transaction.atomic():
                if request.POST["action"]=="notes":update(request.tenant,contact,notes=request.POST.get("staff_notes",""))
                else:update(request.tenant,contact,fields=request.POST)
                audit(request.tenant,request.user,"contact."+request.POST["action"],contact.pk)
            messages.success(request,"Note salvate." if request.POST["action"]=="notes" else "Dati del cliente aggiornati.")
        except BookingError as ex:messages.error(request,str(ex))
        return redirect("contact_detail",slug,contact.pk)
    rows,stats=visits(contact,visible_bookings(request,Booking.objects.all()))
    page=Paginator(rows,100).get_page(request.GET.get("page"))
    for b in page:b.shown=shown(request,b.data)
    raw=contact.data
    notes_ok=can(request,"notes.read")
    # 2.11.0: the overview shows the next appointments; the client's discount sits on the card
    from .prices import discount_label
    upcoming=sorted((b for b in rows if b.start>timezone.now() and b.status in MOVABLE),key=lambda b:b.start)[:3]
    for b in upcoming:b.shown=shown(request,b.data)
    data=shown(request,raw)
    from .clients import reliability
    stats["late"]=reliability([contact.pk]).get(contact.pk,(0,0))[1]
    from .waitlist import WaitEntry
    ctx={"contact":contact,"data":data,"raw":raw if can(request,"pii.read") else {},"events":history(contact),"page":page,"stats":stats,
        "waits":list(WaitEntry.objects.filter(contact=contact,status="waiting").select_related("service").order_by("first")),
        "merge_ok":can(request,"privacy.manage") and can(request,"pii.read"),
        "tests":list(Service.objects.filter(active=True,test_days__gt=0).values_list("name",flat=True)),
        "consent_titles":__import__("core.clients",fromlist=["CONSENT_TITLES"]).CONSENT_TITLES,
        "upcoming":upcoming,"discount":discount_label(contact),
        # 2.14.0: WhatsApp with the text about the next booking, or an empty chat
        "whatsapp":whatsapp(request.tenant,data.get("phone",""),upcoming[0] if upcoming else None),
        # 2.12.0: prices (and so what was spent and the discount) exist only for appointments; a hotel counts stays
        "priced":Service.objects.filter(kind="resource").exists(),
        "visits_word":"Soggiorni" if set(Service.objects.filter(active=True).values_list("kind",flat=True))=={"day"} else "Visite",
        "notes":raw.get("staff_notes","") if notes_ok else None,"spec":spec if notes_ok else None,
        # 2.8.0: every allergy or intolerance the client ever ticked, newest bookings first
        "allergies":list(dict.fromkeys(a for b in rows for a in b.data.get("allergies",[]))) if notes_ok else []}
    if spec and notes_ok:
        now=timezone.now();tz=ZoneInfo(request.tenant.timezone)
        entries=treatments(contact)
        recorded={t.booking_id for t in entries if t.booking_id}
        for b in page:b.recorded=b.pk in recorded;b.recordable=b.start<=now and b.status in ACCEPTED
        prefill=None
        if request.GET.get("da"):
            b=visible_bookings(request,Booking.objects.filter(contact=contact)).filter(pk=uid(request.GET["da"])).first()
            if b:prefill=from_booking(b,tz)
        ctx.update(tpage=Paginator(entries,30).get_page(request.GET.get("tp")),entries_count=len(entries),prefill=prefill,today=now.astimezone(tz).date(),**record_options(spec))
    return render(request,"contact_detail.html",ctx)
def waitlist_view(request,slug):
    """2.14.0: Lista d'attesa: who waits for a place, from the booking page or added here, with what freed for each;
    Chiama, WhatsApp, Prenota, Fatta, Togli. Names and contacts as the role may see them, the entries of its scope."""
    from .waitlist import add,close
    require(request,"booking.read")
    t=request.tenant;tz=ZoneInfo(t.timezone);now=timezone.now();m=request.member
    rows=WaitEntry.objects.filter(status="waiting",last__gte=now.astimezone(tz).date()).select_related("service__location","resource","contact").order_by("first","created")
    if m and (m.locations or m.role=="operator" and not m.resources):rows=rows.filter(service__location_id__in=m.locations or [])
    if m and m.resources:rows=rows.filter(Q(resource_id__in=m.resources)|Q(resource__isnull=True))
    error="";form={}
    if request.method=="POST":
        require(request,"booking.write")
        action=request.POST.get("action")
        try:
            if action=="add":
                form=request.POST
                chosen=Contact.objects.exclude(email_index__startswith="erased:").filter(pk=uid(request.POST.get("contact"))).first() if can(request,"pii.read") else None
                if m:check_new_booking(request,choice(Service,request.POST.get("service"),active=True),request.POST.get("resource"))  # inside the user's scope
                e,created=add(t,request.POST,request.user,chosen)
                messages.success(request,"Aggiunto alla lista d'attesa." if created else "Era già in lista per questi giorni.")
                return redirect("waitlist",slug)
            e=get_object_or_404(rows,pk=uid(request.POST.get("entry")))
            close(t,e,request.user,remove=action=="remove")
            messages.success(request,"Tolto dalla lista." if action=="remove" else "Segnato come fatto.")
            return redirect("waitlist",slug)
        except BookingError as ex:error=str(ex)
    rows=list(rows)
    for e in rows:
        e.shown=shown(request,e.data);e.whatsapp=whatsapp(t,e.shown.get("phone","")) if not e.shown.get("masked") else ""
        e.free=[datetime.fromisoformat(x).astimezone(tz) for x in e.snapshot.get("free",[])]
        e.held=e.held_until.astimezone(tz) if e.held_until and e.held_until>now else None
    client=None
    if request.GET.get("cliente") and can(request,"pii.read"):
        client=Contact.objects.exclude(email_index__startswith="erased:").filter(pk=uid(request.GET["cliente"])).first()
        if client:client.shown=client.data
    from .presets import units
    return render(request,"waitlist.html",{"rows":rows,"error":error,"form":form,"client":client,"services":Service.objects.filter(active=True,location__active=True).select_related("location").order_by("name"),
        "resources":Resource.objects.filter(active=True,location__active=True,id__in=ServiceResource.objects.filter(service__active=True,service__kind__in=("resource","day")).values("resource_id")).order_by("name"),
        "one":units(t.business_type)[0],"today":now.astimezone(tz).date(),"many_locations":Location.objects.filter(active=True).count()>1})
def recalls_view(request,slug):
    """2.14.0: Richiami: the clients whose recall is due (a week ahead), without a booking to come; Chiama, WhatsApp,
    Prenota, Fatto. For who sees the client cards."""
    require(request,"contacts.read")
    from .clients import recalls
    t=request.tenant;now=timezone.now()
    if request.method=="POST":
        require(request,"booking.write")
        b=get_object_or_404(Booking.objects.filter(service__recall_days__gt=0,contact__isnull=False),pk=uid(request.POST.get("booking")))
        b.snapshot["recall_done"]=now.isoformat();b.save(update_fields=["snapshot"])
        audit(t,request.user,"contact.recalled",b.contact_id,booking=str(b.pk))
        messages.success(request,"Segnato come fatto.");return redirect("recalls",slug)
    rows=recalls(now)
    for r in rows:
        r["shown"]=shown(request,r["contact"].data);r["whatsapp"]=whatsapp(t,r["shown"].get("phone","")) if not r["shown"].get("masked") else ""
    return render(request,"recalls.html",{"rows":rows,"now":now})
def contact_merge(request,slug,contact_id):
    """2.14.0: two cards of the same person made one (clients.merge): the other card searched by name, phone or email,
    then field by field which value stays, which card is the main one, and a confirmation: it cannot be undone."""
    from .clients import search as find,merge,MERGE_FIELDS
    require(request,"contacts.read","pii.read","booking.write","privacy.manage")
    alive=Contact.objects.exclude(email_index__startswith="erased:")
    contact=get_object_or_404(alive,pk=contact_id)
    other=alive.exclude(pk=contact.pk).filter(pk=uid(request.GET.get("con") or request.POST.get("con"))).first()
    error=""
    if request.method=="POST" and other:
        from .identity import fresh
        fresh(request)
        try:
            if request.POST.get("confirm")!="UNISCI":raise BookingError("Scrivi UNISCI per confermare.")
            main,second=(other,contact) if request.POST.get("main")=="other" else (contact,other)
            # the choices are made on the page as "this card" and "the other one": turned to main and second here
            keep={f:("other" if (request.POST.get("keep_"+f)=="other")==(main.pk==contact.pk) else "main") for f,_ in MERGE_FIELDS}
            merge(request.tenant,main,second,keep,request.user)
            messages.success(request,"Schede unite: prenotazioni, scheda, lista d'attesa e consensi sono ora qui.")
            return redirect("contact_detail",slug,main.pk)
        except BookingError as ex:error=str(ex)
    found=[c for c in find(request.tenant,request.GET.get("q",""),limit=10) if c.pk!=contact.pk] if request.GET.get("q") and not other else []
    rows=[]
    if other:
        a,b=contact.data,other.data
        rows=[{"key":f,"label":l,"mine":a.get(f,""),"theirs":b.get(f,""),"pick":"this" if a.get(f) or not b.get(f) else "other"} for f,l in MERGE_FIELDS]
        for c in (contact,other):c.bookings=Booking.objects.filter(contact=c).count()
    return render(request,"contact_merge.html",{"contact":contact,"other":other,"found":found,"rows":rows,"error":error,"q":request.GET.get("q",""),
        "mine":contact.data,"theirs":other.data if other else {}})
def csv_cell(value,profile):
    s=str(value if value is not None else "")
    if profile=="sheet" and (s.lstrip().startswith(("=","+","-","@")) or s.startswith(("\t","\r","\n"))):s="'"+s
    return s
def export(request,slug):
    kind=request.GET.get("kind","contacts");profile=request.GET.get("profile","sheet")
    require(request,"export.operational" if kind=="bookings" else "export.marketing")
    if profile not in ("sheet","generic","brevo","mailchimp"):return HttpResponse("Formato non valido.",status=400)
    # Vendor files are only for eligible recipients: never a full address book with a "consent" column.
    if kind=="marketing" and not request.tenant.export_enabled:raise PermissionDenied("L'esportazione degli iscritti la attiva l'amministratore.")
    if profile in ("brevo","mailchimp") and kind!="marketing":return HttpResponse("I formati Brevo e Mailchimp esportano solo i destinatari con consenso confermato (kind=marketing).",status=400)
    rows=[];header=[]
    if kind=="bookings":
        try:a,b=period(request,30)
        except ValidationError:return HttpResponse("Intervallo non valido.",status=400)
        qs=visible_bookings(request,Booking.objects.filter(start__lt=b,end__gt=a)).order_by("start")
        notes=can(request,"notes.read")
        if qs.count()>50000:return HttpResponse("Restringi l'intervallo.",status=400)
        header=["ID","Nome","Cognome","Email","Telefono","Inizio","Fine","Stato","Servizio","Sede","Risorsa","Note cliente","Note interne","Campi aggiuntivi","Intolleranze","Origine","Fonte"]
        for booking in qs.iterator(chunk_size=200):
            d=booking.data
            rows.append([booking.pk,d.get("first_name"),d.get("last_name"),d.get("email"),d.get("phone"),booking.start.astimezone(ZoneInfo(request.tenant.timezone)).isoformat(),booking.end.astimezone(ZoneInfo(request.tenant.timezone)).isoformat(),booking.status,booking.snapshot.get("service"),booking.snapshot.get("location"),booking.snapshot.get("resource"),d.get("notes") if notes else "",d.get("internal_notes") if notes else "",__import__("json").dumps(d.get("answers",{}),ensure_ascii=False),", ".join(d.get("allergies",[])) if notes else "",booking.get_origin_display(),booking.source])
    elif kind in ("contacts","marketing"):
        qs=campaign_contacts() if kind=="marketing" else Contact.objects.exclude(email_index__startswith="erased:").order_by("created")
        if qs.count()>50000:return HttpResponse("Richiedi un export di portabilità da console per oltre 50.000 contatti.",status=400)
        if profile=="brevo":header=["EMAIL","FIRSTNAME","LASTNAME","SMS","CONSENT","CONSENT_DATE"]
        elif profile=="mailchimp":header=["Email Address","First Name","Last Name","Phone Number","Consent","Consent Date"]
        else:header=["email","first_name","last_name","phone","consent","consent_date"]
        for c in qs.iterator(chunk_size=200):
            d=c.data;rows.append([d.get("email"),d.get("first_name"),d.get("last_name"),d.get("phone"),c.consent,c.consent_at.isoformat() if c.consent_at else ""])
    else:return HttpResponse("Export non valido.",status=400)
    out=io.StringIO(newline="")
    writer=csv.writer(out);writer.writerow(header)
    for row in rows:writer.writerow([csv_cell(v,profile) for v in row])
    audit(request.tenant,request.user,"export."+kind,detail_count=len(rows),profile=profile)
    response=HttpResponse("\ufeff"+out.getvalue(),content_type="text/csv; charset=utf-8")
    response["Content-Disposition"]='attachment; filename="td-booking-suite-'+kind+'.csv"'
    return response
def overbooked(rows):
    """Bookings that take part in a peak above the capacity of their group."""
    groups={}
    for b in rows:
        if b.resource_id:keys=[(("resource",b.resource_id),1,1)]
        elif b.occurrence_id:
            keys=[(("occurrence",b.occurrence_id),b.occurrence.capacity,b.party)]
            if b.service.kind=="shift":keys.append((("service",b.service_id),b.service.capacity,b.party))
        else:keys=[(("service",b.service_id),b.service.capacity,b.party)]
        for key,cap,n in keys:
            group=groups.setdefault(key,{"cap":cap,"events":[]})
            group["events"]+= [(b.busy_start,1,n,b.pk),(b.busy_end,0,n,b.pk)]
    result=set()
    for group in groups.values():
        active={};occupied=0
        # Ends sort before starts at the same instant: touching intervals do not overlap.
        for _,starts,n,pk in sorted(group["events"],key=lambda e:(e[0],e[1])):
            if starts:
                active[pk]=n;occupied+=n
                if occupied>group["cap"]:result.update(active)
            else:occupied-=active.pop(pk,0)
    return result
def calendar_conflicts(tenant):
    """Complete impact check after a catalog change: no silent truncation."""
    from .models import ServiceResource
    rows=list(Booking.objects.filter(status__in=OPEN,end__gt=timezone.now()).select_related("service","resource","option","location","occurrence").order_by("start","pk"))
    links=set(ServiceResource.objects.values_list("service_id","resource_id"))
    conflicts=[]
    for b in rows:
        s=b.service
        valid=s.active and s.location_id==b.location_id and b.location.active and (not b.resource_id or b.resource.location_id==b.location_id) and (not b.resource_id or b.resource.active) and (not b.option_id or b.option.active)
        valid=valid and (not b.resource_id or (b.service_id,b.resource_id) in links)
        if s.kind=="day":
            valid=valid and fits(tenant,b.start,b.start+timedelta(microseconds=1),s,purpose="arrival") and fits(tenant,b.end,b.end+timedelta(microseconds=1),s,purpose="departure") and not blocked_stay(tenant,b.start,b.end,s,b.resource)
        else:valid=valid and fits(tenant,b.busy_start,b.busy_end,s,b.resource)
        # 2.14.0: a pause or a turn closed over a booking: to call back as the hours that change
        valid=valid and not blocked(tenant,b.busy_start,b.busy_end,b.resource,s)
        if not valid:conflicts.append(b.pk)
    over=overbooked(rows)
    conflicts+=[b.pk for b in rows if b.pk in over and b.pk not in conflicts]
    return [str(pk)[:8] for pk in conflicts]
def listed(refs):return ", ".join(refs[:10])+(" e altre" if len(refs)>10 else "")
def unsettled(conflicts):
    return f"La modifica lascia {len(conflicts)} prenotazioni da verificare: "+listed(conflicts)+". Conferma esplicitamente dopo averle controllate."
def catalog(request,slug,kind):
    if kind not in CATALOG:raise PermissionDenied
    require_role(request,schedule=True)
    if not catalog_allowed(request,kind):raise PermissionDenied
    model,_,title=CATALOG[kind]
    rows=model.objects.all()
    from .presets import PRESETS,units
    help_text=CATALOG_HELP[kind]
    if kind=="resources":
        one,many,help_text=units(request.tenant.business_type);title=many
    # 2.6.0: active items first, by name or by date; the ones kept for old bookings at the end
    order={"schedules":("weekday","start"),"exceptions":("-date",),"occurrences":("-active","start"),"services":("-active","location__name","name"),"resources":("-active","location__name","name")}.get(kind,("-active","name"))
    return render(request,"catalog.html",{"kind":kind,"title":title,"help":help_text,"page":Paginator(rows.order_by(*order,"pk"),50).get_page(request.GET.get("page")),"catalog":{k:v for k,v in catalog_kinds(request.tenant).items() if catalog_allowed(request,k)},
        "presets":PRESETS if kind=="services" and request.user.is_staff else {},"many_locations":Location.objects.filter(active=True).count()>1,"removable":kind in REMOVABLE,
        "can_profile":can_profile(request),"business_label":(PRESETS.get(request.tenant.business_type) or {}).get("label","")})
def catalog_edit(request,slug,kind,item_id=None):
    if kind not in CATALOG:raise PermissionDenied
    require(request,"catalog.write")
    if not catalog_allowed(request,kind):raise PermissionDenied
    model,formclass,title=CATALOG[kind]
    instance=get_object_or_404(model.objects,pk=item_id) if item_id else None
    extra={};need_confirm=False
    agency=request.user.is_staff
    if kind=="services":
        from .presets import initial,PRESETS
        # the agency may start from any template; the business always starts from its own type
        preset=request.GET.get("preset","") if agency and request.GET.get("preset","") in PRESETS else request.tenant.business_type
        extra={"agency":agency}
        if not instance:
            extra["initial"]=initial(preset)
            if request.method=="POST" and agency and request.POST.get("kind"):extra["initial"]={}
            # 2.14.0: a business whose type changed engine (garages went from places to lifts) keeps adding services
            # with the engine its catalog already uses: an old garage has no lifts to book
            # 2.14.0: also a business without a type: its turns (shift) or its dates (event), not an appointment
            kinds=list(Service.objects.filter(active=True).order_by("name").values_list("kind",flat=True))
            if not agency and kinds and extra["initial"].get("kind") not in kinds:extra["initial"]["kind"]=kinds[0]
    # 2.10.0: "Segna ferie" from an operator opens the closing day with that operator already chosen
    if kind in ("schedules","exceptions") and not instance and request.GET.get("resource"):
        res=Resource.objects.filter(pk=request.GET["resource"]).first() if re.fullmatch(r"[0-9a-f-]{36}",request.GET["resource"]) else None
        if res:extra["initial"]={"resource":res.pk,"location":res.location_id}
    form=formclass(request.POST or None,request.FILES or None,instance=instance,tenant=request.tenant,**extra)
    if kind=="services" and not instance:form.instance.preset=preset if preset in PRESETS else ""
    if request.method=="POST" and form.is_valid():
        try:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=request.tenant.pk)
                if kind=="occurrences" and instance:
                    # 2.14.0: with "the next ones" every date of the series that changes is checked
                    held=[str(pk)[:8] for pk in Booking.objects.filter(occurrence__in=form.targets(),status__in=OPEN,end__gt=timezone.now()).values_list("pk",flat=True)]
                    if held:raise BookingError("Gestisci prima le prenotazioni di queste date, spostale o annullale: "+listed(held)+".")
                obj=form.save()
                if kind=="turns":
                    from .shifts import materialize
                    materialize(obj)
                    # 2.14.0: the owner makes turns too: one outside the opening hours would never be offered, without a word
                    dates=list(Occurrence.objects.filter(rule=obj,active=True).order_by("start")[:8])
                    if dates and not any(fits(request.tenant,o.start,o.end,obj.service) for o in dates):
                        raise BookingError("Il turno è fuori dagli orari di apertura di quel giorno: cambia l'ora o allarga gli orari in Orari di apertura.")
                conflicts=calendar_conflicts(request.tenant)
                if conflicts and not request.POST.get("confirm_conflicts"):raise BookingError(unsettled(conflicts))
                from .waitlist import freed
                freed(request.tenant)  # 2.14.0: more hours, more places: the waiting list looks again
                audit(request.tenant,request.user,"catalog."+kind,obj.pk,conflicts=len(conflicts))
            messages.success(request,"Salvato."+(f" {len(conflicts)} prenotazioni da ricontattare." if conflicts else ""))
            return redirect("catalog",slug,kind)
        except (BookingError,ValidationError) as ex:
            form.add_error(None,str(ex));need_confirm="Conferma esplicitamente" in str(ex)
    edit_title,edit_help=EDIT_TITLES[kind],EDIT_HELP[kind]
    if kind=="resources":
        from .presets import units
        one,title,edit_help=units(request.tenant.business_type);edit_title=one.lower()
    heading=(("Modifica: " if instance else "Nuovo: ")+edit_title)
    return render(request,"form.html",{"form":form,"title":heading,"list_title":title,"description":edit_help,"kind":kind,"need_confirm":need_confirm,
        "custom_fields":getattr(instance,"fields",[]) if instance else []})
def google_return(request):
    """2.14.0: Google comes back to one address for the whole installation: on to the business that asked."""
    slug=(request.session.get("gcal_oauth") or {}).get("slug")
    if not slug:messages.error(request,"Collegamento a Google scaduto: riprova dalla scheda.");return redirect("/")
    return redirect(reverse("google_back",args=[slug])+"?"+request.GET.urlencode())
def google_view(request,slug,item_id=None):
    """2.14.0: Collega / Scollega Google Calendar from an operator's card (POST), and Google's answer coming back (GET)."""
    from . import gcal
    require(request,"catalog.write")
    if not catalog_allowed(request,"resources"):raise PermissionDenied
    if item_id is None:
        r=gcal.finish(request)
        if not r:messages.error(request,"Collegamento a Google non riuscito o scaduto: riprova dalla scheda.");return redirect("catalog",slug,"resources")
        audit(request.tenant,request.user,"google.connected" if not r.google_error else "google.failed",r.pk)
        if not r.google_error:messages.success(request,"Google Calendar collegato: entro 5 minuti si allinea.")
        return redirect("catalog_edit",slug,"resources",r.pk)
    if request.method!="POST":raise PermissionDenied
    r=get_object_or_404(Resource.objects.select_related("tenant"),pk=item_id)
    if request.POST.get("action")=="disconnect":
        gcal.disconnect(r);audit(request.tenant,request.user,"google.disconnected",r.pk)
        messages.success(request,"Google Calendar scollegato.");return redirect("catalog_edit",slug,"resources",r.pk)
    if not gcal.config(request.tenant):raise PermissionDenied
    return redirect(gcal.start(request,r))
REMOVABLE={"locations":"Sede","services":"Servizio","resources":"Risorsa","options":"Variante","turns":"Turno","occurrences":"Data"}
def calendar_remove(request,slug,kind,item_id):
    if request.method!="POST" or kind not in CATALOG:raise PermissionDenied
    require(request,"catalog.write")
    if not catalog_allowed(request,kind):raise PermissionDenied
    model=CATALOG[kind][0]
    if kind=="occurrences":return remove_dates(request,slug,item_id)
    if kind in REMOVABLE:
        # 2.6.0: every catalog item can be removed; what bookings point to is switched off and kept in the history
        from .profile import remove
        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=request.tenant.pk)
            obj=get_object_or_404(model.objects,pk=item_id)
            if kind=="locations" and not Location.objects.filter(active=True).exclude(pk=obj.pk).exists():
                messages.error(request,"Serve almeno una sede: aggiungine un'altra prima di eliminare questa.");return redirect("catalog",slug,kind)
            future=0
            if kind in ("services","resources","locations","occurrences"):
                future=Booking.objects.filter(status__in=OPEN,end__gt=timezone.now(),**{{"services":"service","resources":"resource","locations":"location","occurrences":"occurrence"}[kind]:obj}).count()
            name=str(getattr(obj,"name","") or "")
            kept=remove(kind,obj)
            audit(request.tenant,request.user,"catalog.removed",item_id,kind=kind,kept=kept)
        what=name or REMOVABLE[kind]
        if kept:
            text=f"Disattivato perché ha prenotazioni collegate: {what}. Resta nello storico ma non si prenota più"
            if future:text+=f". Le {future} prenotazioni già fatte restano valide" if future>1 else ". La prenotazione già fatta resta valida"
            messages.success(request,text+".")
        else:messages.success(request,f"Eliminato: {what}.")
        return redirect("catalog",slug,kind)
    try:
        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=request.tenant.pk)
            get_object_or_404(model.objects,pk=item_id).delete()
            conflicts=calendar_conflicts(request.tenant)
            if conflicts and request.POST.get("confirm_conflicts")!="on":raise BookingError("Rimozione non eseguita: esistono prenotazioni in conflitto. Modifica la fascia e conferma la gestione dei conflitti.")
            from .waitlist import freed
            freed(request.tenant)  # 2.14.0: a closure removed: the waiting list looks again
            audit(request.tenant,request.user,"calendar.deleted",item_id)
        messages.success(request,"Fascia rimossa.")
    except BookingError as ex:messages.error(request,str(ex))
    return redirect("catalog",slug,kind)
def to_call(request,rows):
    """2.14.0: the bookings a change leaves behind, as the Dashboard's Da chiamare: who, when, Chiama and WhatsApp."""
    rows=list(rows)
    for row in rows:
        row.shown=shown(request,row.data);row.whatsapp=whatsapp(request.tenant,row.shown.get("phone",""),row) if not row.shown.get("masked") else ""
    return rows
def remove_dates(request,slug,item_id):
    """2.14.0: an event date, or it and the next ones of its series. The bookings on them are listed before, to call
    back, and stay valid: a date with bookings is switched off, as every catalog item, never deleted in silence."""
    from .profile import remove
    obj=get_object_or_404(Occurrence.objects,pk=item_id)
    series=bool(obj.series and request.POST.get("series"))
    dates=[obj,*Occurrence.objects.filter(series=obj.series,start__gt=obj.start).order_by("start")] if series else [obj]
    held=Booking.objects.filter(occurrence__in=dates,status__in=OPEN,end__gt=timezone.now()).select_related("service","tenant").order_by("start")
    if held.exists() and not request.POST.get("confirm"):
        n=len(dates)
        return render(request,"remove_check.html",{"title":"Prima di togliere "+("queste date" if n>1 else "questa data"),"rows":to_call(request,held),
            "text":"Queste prenotazioni sono sulle date che stai togliendo. Restano valide e le date con prenotazioni restano nello storico, spente: chiama i clienti per spostarle o annullarle.",
            "action":reverse("calendar_remove",args=[slug,"occurrences",obj.pk]),"fields":[("series","1")] if series else [],"button":f"Togli {n} date" if n>1 else "Togli la data","back":reverse("catalog",args=[slug,"occurrences"])})
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=request.tenant.pk)
        kept=sum(remove("occurrences",o) for o in dates)
        audit(request.tenant,request.user,"catalog.removed",item_id,kind="occurrences",dates=len(dates),kept=kept)
    messages.success(request,(f"Tolte {len(dates)} date." if len(dates)>1 else "Data tolta.")+(f" {kept} con prenotazioni restano nello storico, spente: le prenotazioni restano valide." if kept else ""))
    return redirect("catalog",slug,"occurrences")
def holidays_view(request,slug):
    """2.14.0: the Italian public holidays of a year with Easter, Easter Monday and the patron saint, as closures of the
    whole business in one click. Again on the same year it adds only what is missing; a holiday left unticked, or its
    row removed afterwards, stays open (once a year is marked, the missing ones start unticked)."""
    require(request,"catalog.write")
    if not catalog_allowed(request,"exceptions"):raise PermissionDenied
    t=request.tenant;today=timezone.now().astimezone(ZoneInfo(t.timezone)).date();data=request.POST
    try:year=min(max(int(data.get("anno") or request.GET.get("anno") or today.year),today.year),today.year+2)
    except ValueError:year=today.year
    error="";need_confirm=False
    if request.method=="POST":
        try:patron=date.fromisoformat(data.get("patron_day","")) if data.get("patron_day") else None
        except ValueError:patron=None;error="Data del santo patrono non valida."
        t.patron_day=patron.strftime("%m-%d") if patron else ""
        t.patron_name=" ".join(data.get("patron_name","").split())[:60] if patron else ""
    whole=ExceptionDay.objects.filter(purpose="service",location=None,service=None,resource=None)
    rows=[{"day":d,"name":n,"iso":d.isoformat(),"there":whole.filter(covering(d)).exists()} for d,n in holidays(year,t.patron_day,t.patron_name)]
    chosen=set(data.getlist("day")) if request.method=="POST" else {r["iso"] for r in rows if not any(x["there"] for x in rows)}
    if request.method=="POST" and not error:
        try:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=t.pk)
                t.save(update_fields=["patron_day","patron_name"])
                made=[ExceptionDay.objects.create(tenant=t,date=r["day"],closed=True,label=r["name"]) for r in rows if r["iso"] in chosen and not r["there"]]
                conflicts=calendar_conflicts(t)
                if conflicts and not data.get("confirm_conflicts"):raise BookingError(unsettled(conflicts))
                audit(t,request.user,"catalog.holidays",year,created=len(made),conflicts=len(conflicts))
            messages.success(request,(f"Segnate {len(made)} festività del {year} come chiusure." if made else f"Le festività scelte del {year} erano già segnate.")+(f" {len(conflicts)} prenotazioni da ricontattare." if conflicts else ""))
            return redirect("catalog",slug,"exceptions")
        except BookingError as ex:
            error=str(ex);need_confirm=True
    for r in rows:r["ticked"]=r["iso"] in chosen
    patron=next((r["day"] for r in rows if t.patron_day and r["day"].strftime("%m-%d")==t.patron_day),None)
    return render(request,"holidays.html",{"rows":rows,"year":year,"years":range(today.year,today.year+3),"error":error,"need_confirm":need_confirm,"patron":patron,"patron_name":t.patron_name})
def marketing(request,slug):
    require(request,"campaign.manage")
    # 2.13.0: the agency turns it on, the campaigns, the CSV of the subscribers or both
    if not (request.tenant.marketing_enabled or request.tenant.export_enabled):raise PermissionDenied("Il marketing lo attiva l'amministratore.")
    if request.method=="POST" and not request.tenant.marketing_enabled:
        request.tenant.marketing_requested=True;request.tenant.save(update_fields=["marketing_requested"])
        audit(request.tenant,request.user,"marketing.requested")
        messages.success(request,"Richiesta registrata per l'amministratore.")
    return render(request,"marketing.html",{"campaigns":Campaign.objects.order_by("-created")[:100],"eligible":campaign_contacts().count(),"deliveries":Delivery.objects.exclude(channel="webhook").order_by("-created")[:30]})
def campaign_edit(request,slug,campaign_id=None):
    require(request,"campaign.manage")
    tenant=request.tenant
    if not tenant.marketing_enabled:raise PermissionDenied("Richiedi l'abilitazione marketing.")
    campaign=get_object_or_404(Campaign.objects,pk=campaign_id) if campaign_id else None
    if campaign and campaign.status!="draft":raise PermissionDenied("Una campagna accodata non è modificabile.")
    form=CampaignForm(request.POST or None,instance=campaign,tenant=tenant)
    if request.method=="POST" and form.is_valid():
        obj=form.save();audit(tenant,request.user,"campaign.saved",obj.pk)
        return redirect("campaign_preview",slug,obj.pk)
    return render(request,"form.html",{"form":form,"title":"Crea una comunicazione","description":"Messaggio semplice, senza HTML. Invio solo a contatti con consenso confermato."})
def campaign_preview(request,slug,campaign_id):
    require(request,"campaign.manage")
    campaign=get_object_or_404(Campaign.objects,pk=campaign_id)
    if request.method=="POST":
        try:
            action=request.POST.get("action")
            if action=="queue":
                enqueue_campaign(request.tenant,campaign,request.user)
                messages.success(request,"Campagna accodata. Lo stato degli invii è visibile nel marketing.")
            elif action=="test":
                if not request.tenant.marketing_enabled:raise BookingError("Marketing disabilitato.")
                if not request.user.email:raise BookingError("Il tuo account non ha un'email: la prova non ha dove arrivare.")
                queue(request.tenant,{"to":request.user.email,"subject":"[PROVA] "+campaign.subject,"body":campaign.body})
                audit(request.tenant,request.user,"campaign.test",campaign.pk)
                messages.success(request,"Prova accodata al tuo indirizzo di accesso.")
            elif action in ("pause","resume"):
                if not request.tenant.marketing_enabled:raise BookingError("Marketing disabilitato.")
                campaign.status="paused" if action=="pause" else "queued";campaign.save(update_fields=["status"])
                audit(request.tenant,request.user,"campaign."+action,campaign.pk)
            else:raise BookingError("Operazione non valida.")
            return redirect("marketing",slug)
        except BookingError as ex:messages.error(request,str(ex))
    return render(request,"campaign_preview.html",{"campaign":campaign,"eligible":campaign_contacts(request.tenant,campaign.filters).count()})
def audit_view(request,slug):
    require(request,"audit.read")
    return render(request,"audit.html",{"page":Paginator(Audit.objects.order_by("-created"),100).get_page(request.GET.get("page"))})
