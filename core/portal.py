import csv,io,secrets
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
from .models import *
from .forms import LocationForm,ResourceForm,ServiceForm,OptionForm,ScheduleForm,ExceptionForm,OccurrenceForm,CampaignForm,ShiftForm
from .tenancy import scope
from .middleware import require_role
from .permissions import can,require,visible_bookings,shown
from .booking import choice,change_status,BookingError,queue,capacity_available
from .calendar import fits,blocked_stay
from .crypto import index,tenant_key,encrypt
from .mail import enqueue_campaign,campaign_contacts
from .site import mail_ready
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
 "exceptions":"Ferie, festivi e aperture straordinarie: hanno la precedenza sugli orari di apertura",
 "services":"Quello che il cliente sceglie quando prenota in una sede: al ristorante il Tavolo, dal parrucchiere Taglio, Piega e Colore, in hotel la Camera. La sede è il luogo, il servizio è cosa si prenota lì, con posti, durata e preavviso",
 "resources":"Chi o cosa viene prenotato: operatori, cabine, camere, tavoli, campi. Ogni risorsa appartiene a una sede",
 "options":"Versioni dello stesso servizio con durata diversa, per esempio taglio corto o lungo",
 "turns":"I turni che si ripetono ogni settimana, per esempio pranzo e cena, con i coperti di ciascuno",
 "occurrences":"Date singole con posti limitati: un evento, un corso, un tour",
}
EDIT_TITLES={"locations":"sede","schedules":"fascia oraria","exceptions":"chiusura o giorno speciale","services":"servizio","resources":"risorsa","options":"variante","turns":"turno","occurrences":"data dell'evento"}
EDIT_HELP={
 "locations":"Il nome con cui riconosci la sede, l'indirizzo e il telefono che compaiono nelle email di conferma",
 "schedules":"Un giorno della settimana con un orario di apertura. Per pranzo e cena crea due fasce. Le prenotazioni già fatte non vengono mai annullate",
 "exceptions":"Un giorno di chiusura o con un orario diverso dal solito: vale più degli orari di apertura. Le prenotazioni già fatte non vengono mai annullate",
 "services":"Quello che il cliente sceglie quando prenota. Compaiono solo le impostazioni che servono a questo tipo di prenotazione",
 "resources":"Una persona o una cosa che si prenota: un operatore, una cabina, una camera, un campo",
 "options":"La stessa prestazione con una durata diversa, per esempio Taglio corto e Taglio lungo",
 "turns":"Un turno che si ripete ogni settimana, per esempio la cena del venerdì dalle 19:30 alle 23:00, con i suoi coperti",
 "occurrences":"Una data dell'evento o del corso, con i posti disponibili",
}
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
     {"title":"Persone e permessi","done":members>1,"text":f"{members} person{'a' if members==1 else 'e'} con accesso","link":"members" if can(request,"users.manage") else ""},
     # 2.6.0: the WordPress site has its own card below, with the connection code
     {"title":"Invio email","done":mail_ready(t),"text":"Invio proprio dell'attività" if t.smtp else ("Invio dell'agenzia" if mail_ready() else "Nessun invio configurato: le email restano in attesa"),"link":"smtp" if request.user.is_staff else ""},
    ]
    # site and email are set by the agency: business users do not even see them
    agency=[a for a in agency if a["link"]]
    done=sum(1 for s in steps if s["done"])+sum(1 for a in agency if a["done"])
    from .presets import PRESETS
    from .profile import describe,MAX_LOCATIONS,default_name
    profile=describe(t)
    shown=max(5,len(profile["locations"]))
    names=[l.name for l in profile["locations"]]+[default_name(i) for i in range(len(profile["locations"]),shown)]
    return render(request,"setup.html",{"steps":steps,"agency":agency,"done":done,"total":len(steps)+len(agency),
        "profile":profile,"can_profile":can_profile(request),"presets":[(k,v["label"],v["hint"]) for k,v in PRESETS.items()],
        "counts":range(1,6),"max_locations":MAX_LOCATIONS,"names":names,"default_name":"Sede","wp_code":request.session.pop("wp_code",None),
        "wp_pending":bool(t.connect_hash and t.connect_until and t.connect_until>timezone.now()),"portal":settings.PUBLIC_ORIGIN.rstrip("/"),
        # 2.8.0: what this kind of business usually asks, and the allergies list of restaurants
        "recommended":recommended(t)[0],"allergens_card":t.business_type=="ristorante" or t.allergens_on})

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
    """2.8.0: the allergies and intolerances the booking form asks about, as the business wants them."""
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
    """Type of business, locations and the WordPress code: the agency, or the administrator of a Private installation."""
    return request.user.is_staff or (settings.EDITION=="private" and can(request,"users.manage"))

def setup_action(request,slug):
    """2.6.0: the profile of the business in one go, and the code that connects the WordPress site."""
    from django.http import Http404
    from .identity import fresh
    if request.method!="POST":raise Http404
    require_role(request,schedule=True)
    if not can_profile(request):raise PermissionDenied
    fresh(request);t=request.tenant;action=request.POST.get("action")
    if action=="profile":
        from .profile import apply_profile,clean_names,MAX_LOCATIONS
        try:
            raw=request.POST.get("locations","1")
            count=int(request.POST.get("locations_custom","") if raw=="custom" else raw)
            if not 1<=count<=MAX_LOCATIONS:raise ValueError(f"Le sedi vanno da 1 a {MAX_LOCATIONS}.")
            names=clean_names([request.POST.get(f"name_{i}","") for i in range(1,count+1)],count)
            r=apply_profile(t,request.POST.get("business_type",""),names,request.user)
        except ValueError as ex:
            messages.error(request,str(ex) or "Dati non validi.");return redirect(reverse("setup",args=[slug])+"#tipo")
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
    role=request.member.get_role_display() if getattr(request,"member",None) else ""
    return render(request,"guide.html",{"role":role,"staff":request.user.is_staff})
def home(request):
    if request.user.is_staff:return redirect("platform")
    members=Membership.objects.filter(user=request.user).select_related("tenant")
    if members.count()==1:return redirect("dashboard",members.first().tenant.slug)
    return render(request,"home.html",{"memberships":members})
def outcome_rows(counts,total):
    labels=[("done","Svolte o confermate","ok"),("cancelled","Disdette","danger"),("no_show","Assenze","warn")]
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
    metrics={"today":rows.filter(status="confirmed").count(),
        "week":mine(Booking.objects.filter(start__gte=start,start__lt=start+timedelta(days=7),status="confirmed")).count(),
        "upcoming":mine(Booking.objects.filter(start__gte=end,status="confirmed")).count(),
        "received":received_n,"trend":round((received_n-previous)*100/previous) if previous else None,
        "cancelled":cancelled_n,"cancel_rate":round(100*cancelled_n/received_n) if received_n else 0,
        "contacts":Contact.objects.count() if can(request,"contacts.read") else None,"eligible":campaign_contacts().count() if can(request,"contacts.read") else None,
        "new_contacts":Contact.objects.filter(created__gte=since).count() if can(request,"contacts.read") else None}
    stats=None
    if can(request,"booking.read"):
        # how the last 30 days went, by appointment date
        past=mine(Booking.objects.filter(start__gte=start-timedelta(days=30),start__lt=start))
        counts={"done":past.filter(status__in=["confirmed","completed"]).count(),"cancelled":past.filter(status="cancelled").count(),"no_show":past.filter(status="no_show").count()}
        total=sum(counts.values())
        services=list(received.exclude(status="cancelled").values("snapshot__service").annotate(n=Count("id")).order_by("-n")[:5])
        top=max([s["n"] for s in services]+[1])
        weekly=[0]*WEEKS;first=week_start(today)-timedelta(weeks=WEEKS-1)
        for created in mine(Booking.objects.filter(created__gte=datetime.combine(first,time.min,tz))).values_list("created",flat=True):
            i=(created.astimezone(tz).date()-first).days//7
            if 0<=i<WEEKS:weekly[i]+=1
        days=[0]*7
        for s in mine(Booking.objects.filter(start__gte=start,start__lt=start+timedelta(days=7),status="confirmed")).values_list("start",flat=True):
            i=(s.astimezone(tz).date()-today).days
            if 0<=i<7:days[i]+=1
        next_days=bars(days,today,width=300,height=170)
        names=["Oggi","Domani"]+[(today+timedelta(days=i)).strftime("%d/%m") for i in range(2,7)]
        for i,b in enumerate(next_days["bars"]):b["label"]=names[i];b["show"]=True
        next_days["daily"]=True
        stats={"outcome":outcome_rows(counts,total),"outcome_total":total,"weekly":bars(weekly,first,width=440,height=170),"next_days":next_days,
            "services":[{"name":s["snapshot__service"] or "Servizio","n":s["n"],"share":round(100*s["n"]/top)} for s in services]}
    shown_rows=list(rows[:50])
    for row in shown_rows:row.shown=shown(request,row.data)
    return render(request,"dashboard.html",{"rows":shown_rows,"metrics":metrics,"stats":stats,"today":today,"pending":Delivery.objects.filter(status="pending").count(),"mail_issues":Delivery.objects.filter(status__in=["failed","unknown"]).count()})
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
    try:a,b=period(request)
    except ValidationError as ex:return render(request,"message.html",{"title":"Filtro non valido","text":str(ex)},status=400)
    rows=visible_bookings(request,Booking.objects.filter(start__lt=b,end__gt=a)).select_related("service","resource").order_by("start")
    if request.GET.get("status"):rows=rows.filter(status=request.GET["status"])
    page=Paginator(rows,50).get_page(request.GET.get("page"))
    for row in page:row.shown=shown(request,row.data)
    return render(request,"bookings.html",{"page":page,"start":a.date(),"end":(b-timedelta(days=1)).date()})
def booking_detail(request,slug,booking_id):
    require(request,"booking.read")
    booking=get_object_or_404(visible_bookings(request,Booking.objects.all()).select_related("service","resource"),pk=booking_id)
    from .presets import record
    spec=record(request.tenant) if booking.contact_id else None
    if request.method=="POST" and request.POST.get("action") in ("treatment","treatment_remove") and spec:
        try:messages.success(request,record_action(request,booking.contact,booking))
        except BookingError as ex:messages.error(request,str(ex))
        return redirect(reverse("booking_detail",args=[slug,booking_id])+"#scheda")
    if request.method=="POST":
        action=request.POST.get("action")
        require(request,*(("booking.write","notes.read") if action=="notes" else ("booking.cancel",)))
        try:
            if action=="notes":
                pii=booking.data
                pii["internal_notes"]=request.POST.get("internal_notes","")[:4000]
                booking.pii=encrypt(request.tenant,pii,"booking:"+str(booking.pk));booking.save(update_fields=["pii"])
                audit(request.tenant,request.user,"booking.notes",booking.pk)
            else:change_status(request.tenant,booking,action,request.user)
            return redirect("booking_detail",slug,booking_id)
        except BookingError as ex:messages.error(request,str(ex))
    data=shown(request,booking.data)
    answers=[{"label":booking.snapshot.get("field_labels",{}).get(k,k),"value":"Sì" if v is True else v} for k,v in data.get("answers",{}).items() if v not in ("",None,False)]
    ctx={"booking":booking,"data":data,"answers":answers,"spec":spec,
        # 2.8.0: the note kept on the client card shows on every booking, as the main booking software do
        "client_note":booking.contact.data.get("staff_notes","") if booking.contact_id and can(request,"notes.read") else ""}
    if spec and can(request,"notes.read"):
        from .clients import treatments,from_booking
        entries=treatments(booking.contact);prefill=from_booking(booking,ZoneInfo(request.tenant.timezone))
        # what was done before this appointment, the newest first: what the operator wants to know before starting
        ctx.update(linked=[t for t in entries if t.booking_id==booking.pk],earlier=[t for t in entries if t.booking_id!=booking.pk and t.date<prefill["date"]][:3],prefill=prefill,record_open=booking.start<=timezone.now() and booking.status in ("confirmed","completed"),**record_options(spec))
    return render(request,"booking_detail.html",ctx)
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
    if q:
        found=find(request.tenant,q,limit=200)
        page=Paginator(found,50).get_page(request.GET.get("page"))
    else:
        page=Paginator(Contact.objects.exclude(email_index__startswith="erased:").order_by("-created"),50).get_page(request.GET.get("page"))
    counts=visit_counts(list(page))
    for c in page:c.shown=shown(request,c.data);c.stats=counts.get(c.pk,{})
    return render(request,"contacts.html",{"page":page,"search":q,"error":error,"form":request.POST if error else {}})
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
    if (request.method=="POST" and request.POST.get("action") not in ("notes","card","treatment","treatment_remove")) or request.GET.get("download"):
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
    ctx={"contact":contact,"data":shown(request,raw),"raw":raw if can(request,"pii.read") else {},"events":history(contact),"page":page,"stats":stats,
        "notes":raw.get("staff_notes","") if notes_ok else None,"spec":spec if notes_ok else None,
        # 2.8.0: every allergy or intolerance the client ever ticked, newest bookings first
        "allergies":list(dict.fromkeys(a for b in rows for a in b.data.get("allergies",[]))) if notes_ok else []}
    if spec and notes_ok:
        now=timezone.now();tz=ZoneInfo(request.tenant.timezone)
        entries=treatments(contact)
        recorded={t.booking_id for t in entries if t.booking_id}
        for b in page:b.recorded=b.pk in recorded;b.recordable=b.start<=now and b.status in ("confirmed","completed")
        prefill=None
        if request.GET.get("da"):
            b=visible_bookings(request,Booking.objects.filter(contact=contact)).filter(pk=uid(request.GET["da"])).first()
            if b:prefill=from_booking(b,tz)
        ctx.update(tpage=Paginator(entries,30).get_page(request.GET.get("tp")),entries_count=len(entries),prefill=prefill,today=now.astimezone(tz).date(),**record_options(spec))
    return render(request,"contact_detail.html",ctx)
def csv_cell(value,profile):
    s=str(value if value is not None else "")
    if profile=="sheet" and (s.lstrip().startswith(("=","+","-","@")) or s.startswith(("\t","\r","\n"))):s="'"+s
    return s
def export(request,slug):
    kind=request.GET.get("kind","contacts");profile=request.GET.get("profile","sheet")
    require(request,"export.operational" if kind=="bookings" else "export.marketing")
    if profile not in ("sheet","generic","brevo","mailchimp"):return HttpResponse("Formato non valido.",status=400)
    # Vendor files are only for eligible recipients: never a full address book with a "consent" column.
    if profile in ("brevo","mailchimp") and kind!="marketing":return HttpResponse("I formati Brevo e Mailchimp esportano solo i destinatari con consenso confermato (kind=marketing).",status=400)
    rows=[];header=[]
    if kind=="bookings":
        try:a,b=period(request,30)
        except ValidationError:return HttpResponse("Intervallo non valido.",status=400)
        qs=visible_bookings(request,Booking.objects.filter(start__lt=b,end__gt=a)).order_by("start")
        notes=can(request,"notes.read")
        if qs.count()>50000:return HttpResponse("Restringi l'intervallo.",status=400)
        header=["ID","Nome","Cognome","Email","Telefono","Inizio","Fine","Stato","Servizio","Sede","Risorsa","Note cliente","Note interne","Campi aggiuntivi","Intolleranze"]
        for booking in qs.iterator(chunk_size=200):
            d=booking.data
            rows.append([booking.pk,d.get("first_name"),d.get("last_name"),d.get("email"),d.get("phone"),booking.start.astimezone(ZoneInfo(request.tenant.timezone)).isoformat(),booking.end.astimezone(ZoneInfo(request.tenant.timezone)).isoformat(),booking.status,booking.snapshot.get("service"),booking.snapshot.get("location"),booking.snapshot.get("resource"),d.get("notes") if notes else "",d.get("internal_notes") if notes else "",__import__("json").dumps(d.get("answers",{}),ensure_ascii=False),", ".join(d.get("allergies",[])) if notes else ""])
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
    rows=list(Booking.objects.filter(status="confirmed",end__gt=timezone.now()).select_related("service","resource","option","location","occurrence").order_by("start","pk"))
    links=set(ServiceResource.objects.values_list("service_id","resource_id"))
    conflicts=[]
    for b in rows:
        s=b.service
        valid=s.active and s.location_id==b.location_id and b.location.active and (not b.resource_id or b.resource.location_id==b.location_id) and (not b.resource_id or b.resource.active) and (not b.option_id or b.option.active)
        valid=valid and (not b.resource_id or (b.service_id,b.resource_id) in links)
        if s.kind=="day":
            valid=valid and fits(tenant,b.start,b.start+timedelta(microseconds=1),s,purpose="arrival") and fits(tenant,b.end,b.end+timedelta(microseconds=1),s,purpose="departure") and not blocked_stay(tenant,b.start,b.end,s)
        else:valid=valid and fits(tenant,b.busy_start,b.busy_end,s,b.resource)
        if not valid:conflicts.append(b.pk)
    over=overbooked(rows)
    conflicts+=[b.pk for b in rows if b.pk in over and b.pk not in conflicts]
    return [str(pk)[:8] for pk in conflicts]
def catalog(request,slug,kind):
    if kind not in CATALOG:raise PermissionDenied
    require_role(request,schedule=True)
    model,_,title=CATALOG[kind]
    rows=model.objects.all()
    from .presets import PRESETS,units
    help_text=CATALOG_HELP[kind]
    if kind=="resources":
        one,many,help_text=units(request.tenant.business_type);title=many
    # 2.6.0: active items first, by name or by date; the ones kept for old bookings at the end
    order={"schedules":("weekday","start"),"exceptions":("-date",),"occurrences":("-active","start"),"services":("-active","location__name","name"),"resources":("-active","location__name","name")}.get(kind,("-active","name"))
    return render(request,"catalog.html",{"kind":kind,"title":title,"help":help_text,"page":Paginator(rows.order_by(*order,"pk"),50).get_page(request.GET.get("page")),"catalog":catalog_kinds(request.tenant),
        "presets":PRESETS if kind=="services" and request.user.is_staff else {},"many_locations":Location.objects.filter(active=True).count()>1,"removable":kind in REMOVABLE,
        "can_profile":can_profile(request),"business_label":(PRESETS.get(request.tenant.business_type) or {}).get("label","")})
def catalog_edit(request,slug,kind,item_id=None):
    if kind not in CATALOG:raise PermissionDenied
    require(request,"catalog.write")
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
    form=formclass(request.POST or None,instance=instance,tenant=request.tenant,**extra)
    if kind=="services" and not instance:form.instance.preset=preset if preset in PRESETS else ""
    if request.method=="POST" and form.is_valid():
        try:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=request.tenant.pk)
                if kind=="occurrences" and instance and Booking.objects.filter(occurrence=instance,status="confirmed",end__gt=timezone.now()).exists():
                    raise BookingError("Gestisci le prenotazioni future prima di modificare questo evento o turno.")
                obj=form.save()
                if kind=="turns":
                    from .shifts import materialize
                    materialize(obj)
                conflicts=calendar_conflicts(request.tenant)
                if conflicts and not request.POST.get("confirm_conflicts"):
                    raise BookingError(f"La modifica lascia {len(conflicts)} prenotazioni da verificare: "+", ".join(conflicts[:10])+(" e altre" if len(conflicts)>10 else "")+". Conferma esplicitamente dopo averle controllate.")
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
REMOVABLE={"locations":"Sede","services":"Servizio","resources":"Risorsa","options":"Variante","turns":"Turno","occurrences":"Data"}
def calendar_remove(request,slug,kind,item_id):
    if request.method!="POST" or kind not in CATALOG:raise PermissionDenied
    require(request,"catalog.write")
    model=CATALOG[kind][0]
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
                future=Booking.objects.filter(status="confirmed",end__gt=timezone.now(),**{{"services":"service","resources":"resource","locations":"location","occurrences":"occurrence"}[kind]:obj}).count()
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
            audit(request.tenant,request.user,"calendar.deleted",item_id)
        messages.success(request,"Fascia rimossa.")
    except BookingError as ex:messages.error(request,str(ex))
    return redirect("catalog",slug,kind)
def marketing(request,slug):
    require(request,"campaign.manage")
    if request.method=="POST":
        request.tenant.marketing_requested=True;request.tenant.save(update_fields=["marketing_requested"])
        audit(request.tenant,request.user,"marketing.requested")
        messages.success(request,"Richiesta registrata per l'amministratore.")
    return render(request,"marketing.html",{"campaigns":Campaign.objects.order_by("-created")[:100],"eligible":campaign_contacts().count(),"deliveries":Delivery.objects.order_by("-created")[:30]})
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
    return render(request,"campaign_preview.html",{"campaign":campaign,"eligible":campaign_contacts().count()})
def audit_view(request,slug):
    require(request,"audit.read")
    return render(request,"audit.html",{"page":Paginator(Audit.objects.order_by("-created"),100).get_page(request.GET.get("page"))})
