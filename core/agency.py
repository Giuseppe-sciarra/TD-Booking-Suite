"""Area agenzia, 2.4.0: a dashboard with how every client uses the portal, the client list (grid or list)
and one card per client with logo, company data, contacts and internal notes.

Usage numbers are counts only: names, contacts and messages of the client's customers never leave the
client's portal. The card is the agency's own record of its client, sealed with the client's key."""
from datetime import date,timedelta
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.validators import validate_email,URLValidator
from django.db import transaction
from django.db.models import Max
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from .models import Tenant,User,Membership,Booking,Contact,Delivery,Location,Service,ClientProfile,audit
from .crypto import encrypt,decrypt,login_index
from .tenancy import scope
from .identity import fresh
from .permissions import active_grant

WEEKS=12
LABEL="client-profile"
FIELDS=[("company","Ragione sociale",160),("vat","Partita IVA",20),("tax_code","Codice fiscale",20),("sdi","Codice destinatario SDI",7),
        ("pec","PEC",160),("email","Email dell'attività",160),("phone","Telefono",40),("address","Indirizzo",200),("city","Città",80),
        ("sector","Settore",80),("since","Cliente dal",10)]
CONTACT_FIELDS=[("name","Nome e cognome",100),("role","Ruolo",80),("email","Email",160),("phone","Telefono",40),("notes","Note",300)]
MAX_CONTACTS=20

def staff(request):
    if not request.user.is_staff:raise PermissionDenied

# the agency's record of the client
def load_profile(t):
    with scope(t):
        row=ClientProfile.objects.filter(tenant=t).first()
        data=decrypt(t,row.data,LABEL) if row and row.data else {}
    data.setdefault("contacts",[]);data.setdefault("notes","")
    for key,_,_ in FIELDS:data.setdefault(key,"")
    return data
def store_profile(t,data):
    with scope(t),transaction.atomic():
        row,_=ClientProfile.objects.select_for_update().get_or_create(tenant=t)
        row.data=encrypt(t,data,LABEL);row.save()
def primary(profile):
    contacts=profile["contacts"]
    return next((c for c in contacts if c.get("primary")),contacts[0] if contacts else None)
def clean_text(post,key,limit,label):
    value=post.get(key,"").strip()
    if len(value)>limit:raise ValidationError(f"{label}: massimo {limit} caratteri.")
    return value
def clean_profile(post):
    data={key:clean_text(post,key,limit,label) for key,label,limit in FIELDS}
    if data["email"]:validate_email(data["email"])
    if data["pec"]:validate_email(data["pec"])
    if data["since"]:
        try:date.fromisoformat(data["since"])
        except ValueError:raise ValidationError("Cliente dal: data non valida.")
    data["notes"]=clean_text(post,"notes",4000,"Note interne")
    return data
def clean_contact(post):
    c={key:clean_text(post,key,limit,label) for key,label,limit in CONTACT_FIELDS}
    if not c["name"]:raise ValidationError("Indica il nome del referente.")
    if c["email"]:validate_email(c["email"])
    c["primary"]=post.get("primary")=="on"
    return c

# usage: counts only
def week_start(day):return day-timedelta(days=day.weekday())
def usage(t,now=None):
    now=now or timezone.now();since=now-timedelta(days=30);before=now-timedelta(days=60)
    first=week_start(now.date())-timedelta(weeks=WEEKS-1)
    with scope(t):
        b=Booking.objects
        u={"bookings_30":b.filter(created__gte=since).count(),"bookings_prev":b.filter(created__gte=before,created__lt=since).count(),
           "upcoming":b.filter(status="confirmed",start__gte=now).count(),"bookings_total":b.count(),
           "last_booking":b.aggregate(x=Max("created"))["x"],"contacts":Contact.objects.count(),
           "sent_30":Delivery.objects.filter(status="accepted",sent_at__gte=since).count(),
           "queued":Delivery.objects.filter(status="pending").count(),"issues":Delivery.objects.filter(status__in=["failed","unknown"]).count(),
           "locations":Location.objects.filter(active=True).count(),"services":Service.objects.filter(active=True).count()}
        weeks=[0]*WEEKS
        for created in b.filter(created__date__gte=first).values_list("created",flat=True):
            i=(created.date()-first).days//7
            if 0<=i<WEEKS:weeks[i]+=1
    u["weeks"]=weeks;u["first_week"]=first
    members=list(Membership.objects.filter(tenant=t).select_related("user"))
    u["users"]=len(members)
    logins=[m.user.last_login for m in members if m.user.last_login]
    u["last_login"]=max(logins) if logins else None
    u["trend"]=round((u["bookings_30"]-u["bookings_prev"])*100/u["bookings_prev"]) if u["bookings_prev"] else None
    return u

def attention(t,u,now=None):
    """What the agency should look at, most urgent first."""
    now=now or timezone.now();today=now.date();items=[]
    if settings.EDITION!="private":
        if t.status=="suspended":items.append(("danger","Servizio sospeso"))
        elif t.status=="trial" and t.trial_until:
            days=(t.trial_until-today).days
            if days<0:items.append(("danger","Prova scaduta il "+t.trial_until.strftime("%d/%m/%Y")))
            elif days<=14:items.append(("warn","Prova in scadenza "+("oggi" if days==0 else "domani" if days==1 else f"tra {days} giorni")))
    if t.maintenance:items.append(("warn","In manutenzione tecnica"))
    if u["issues"]:items.append(("danger",f"{u['issues']} invi{'o' if u['issues']==1 else 'i'} email da verificare"))
    if t.marketing_requested and not t.marketing_enabled:items.append(("info","Ha chiesto di attivare il marketing"))
    if not u["last_login"]:items.append(("info","Nessuno ha ancora fatto accesso"))
    elif not u["bookings_30"] and (now-t.created).days>=30:items.append(("info","Nessuna prenotazione negli ultimi 30 giorni"))
    return items

# small SVG charts, drawn with attributes because the CSP allows no inline style
n=lambda v:f"{v:.1f}"  # plain dots: templates would localise floats to "12,5" inside SVG attributes
def bars(values,first,width=640,height=170):
    top=max(values) or 1;slot=width/len(values);bar=slot*.6;base=height-22;out=[]
    for i,v in enumerate(values):
        h=(base-18)*v/top if v else 0
        day=first+timedelta(weeks=i)
        out.append({"x":n(i*slot+(slot-bar)/2),"w":n(bar),"y":n(base-h),"h":n(h),"v":v,"ty":n(base-h-6),"zy":n(base-3),
                    "label":day.strftime("%d/%m"),"lx":n(i*slot+slot/2),"show":i%2==(len(values)-1)%2})
    return {"bars":out,"width":width,"height":height,"base":base,"ly":height-4,"empty":not any(values)}
def spark(values,width=120,height=32):
    top=max(values) or 1;step=width/(len(values)-1)
    points=[(n(i*step),n(height-3-(height-6)*v/top)) for i,v in enumerate(values)]
    line=" ".join(f"{x},{y}" for x,y in points)
    return {"line":line,"area":f"0,{height} "+line+f" {width},{height}","width":width,"height":height,"empty":not any(values)}

def annotate(tenants,user,now):
    for t in tenants:
        t.usage=usage(t,now);t.attention=attention(t,t.usage,now);t.spark=spark(t.usage["weeks"])
        with scope(t):t.granted=active_grant(t,user) is not None
    top=max([t.usage["bookings_30"] for t in tenants]+[1])
    for t in tenants:t.share=round(100*t.usage["bookings_30"]/top)
    return tenants

def dashboard(request):
    staff(request);fresh(request);now=timezone.now()
    tenants=annotate(list(Tenant.objects.order_by("name")),request.user,now)
    weeks=[sum(t.usage["weeks"][i] for t in tenants) for i in range(WEEKS)]
    first=week_start(now.date())-timedelta(weeks=WEEKS-1)
    total=lambda k:sum(t.usage[k] for t in tenants)
    b30,prev=total("bookings_30"),total("bookings_prev")
    totals={"clients":len(tenants),"active":sum(t.status=="active" for t in tenants),"trial":sum(t.status=="trial" for t in tenants),
            "suspended":sum(t.status=="suspended" for t in tenants),"bookings_30":b30,"trend":round((b30-prev)*100/prev) if prev else None,
            "upcoming":total("upcoming"),"sent_30":total("sent_30"),"users":total("users"),"contacts":total("contacts")}
    rank={"danger":0,"warn":1,"info":2}
    # one entry per client, the most urgent first
    alerts=sorted(((min((l for l,_ in t.attention),key=rank.get),t,[x for _,x in t.attention]) for t in tenants if t.attention),key=lambda x:(rank[x[0]],x[1].name))
    ranking=sorted(tenants,key=lambda t:(-t.usage["bookings_30"],t.name))
    trials=[]
    if settings.EDITION=="cloud":
        from .models import TrialRequest
        from .trial import annotate as describe
        trials=describe(list(TrialRequest.objects.filter(status="new").order_by("-created")[:4]))
    return render(request,"agency_dashboard.html",{"tenants":tenants,"ranking":ranking[:8],"totals":totals,"chart":bars(weeks,first),
        "trials":trials,"alerts":alerts[:6],"more_alerts":max(0,len(alerts)-6),"today":now,"can_create":can_create()})

def can_create():return settings.EDITION!="private" or not Tenant.objects.exists()

def clients(request):
    staff(request);fresh(request);now=timezone.now()
    tenants=annotate(list(Tenant.objects.order_by("name")),request.user,now)
    for t in tenants:
        t.profile=load_profile(t);t.contact=primary(t.profile)
        words=[t.name,t.slug,t.profile["company"],t.profile["city"],t.profile["sector"]]+[c.get("name","") for c in t.profile["contacts"]]
        t.search=" ".join(w for w in words if w).casefold()
    counts={"all":len(tenants),"active":sum(t.status=="active" for t in tenants),"trial":sum(t.status=="trial" for t in tenants),"suspended":sum(t.status=="suspended" for t in tenants)}
    return render(request,"clients.html",{"tenants":tenants,"counts":counts,"can_create":can_create()})

def client_new(request):
    """Create the client's portal, its administrator and, if given, the first contact on the card."""
    from .forms import TenantForm
    from .platform import private_guard,seed_tenant,grant_access
    from .booking import queue
    from .site import tenant_defaults,mail_ready
    from .presets import CHOICES
    staff(request);fresh(request);link="";error="";created=None;direct=False
    from .trial import prefill
    try:trial,values=prefill(request.POST.get("richiesta") or request.GET.get("richiesta") or "")
    except (ValueError,ValidationError):trial,values=None,{}
    if request.method=="POST":
        try:
            email=request.POST.get("email","").strip().casefold();validate_email(email)
            contact={"name":clean_text(request.POST,"contact_name",100,"Referente"),"role":"Titolare","email":email,
                     "phone":clean_text(request.POST,"contact_phone",40,"Telefono"),"notes":"","primary":True}
            form=TenantForm(request.POST,request.FILES)
            if form.is_valid():
                with transaction.atomic():
                    private_guard()
                    tenant=form.save()
                    from .presets import PRESETS,first_settings
                    preset=request.POST.get("preset","")
                    if preset in PRESETS:tenant.save(update_fields=first_settings(tenant,preset))
                    seed_tenant(tenant,preset)
                    user=User.objects.filter(login_index=login_index(email)).first() or User.objects.create_user(email)
                    # the owner runs the business: services and opening hours included
                    Membership.objects.create(tenant=tenant,user=user,role="owner",can_schedule=True)
                    direct=bool(request.POST.get("password"))
                    link=grant_access(user,request.POST.get("password",""))
                    with scope(tenant):
                        audit(tenant,request.user,"tenant.created",tenant.pk)
                        if link:queue(tenant,{"to":email,"subject":"Il tuo accesso a "+tenant.name,"body":"Imposta la password per entrare nel portale:\n"+link})
                    if contact["name"]:store_profile(tenant,{**{k:"" for k,_,_ in FIELDS},"since":timezone.localdate().isoformat(),"notes":"","contacts":[contact]})
                    if trial:
                        # the request becomes this client: business type and contact already in place
                        trial.status="done";trial.client=tenant;trial.save(update_fields=["status","client"])
                created=tenant;trial,values=None,{}
                messages.success(request,"Cliente creato. "+("L'amministratore dell'attività può già accedere con la password che hai scelto." if direct else "L'invito è valido per 24 ore." if link else "L'amministratore dell'attività aveva già un accesso: trova la nuova attività nel suo portale."))
                form=TenantForm(initial=tenant_defaults())
            else:error="Controlla i campi."
        except (ValueError,ValidationError) as ex:
            form=TenantForm(request.POST,request.FILES);error=" ".join(ex.messages) if isinstance(ex,ValidationError) else str(ex)
    else:form=TenantForm(initial={**tenant_defaults(),**{k:values[k] for k in ("name","slug","status","trial_until") if k in values}})
    return render(request,"client_new.html",{"form":form,"invite_link":link,"error":error,"created":created,"presets":CHOICES[1:],"trial":trial,"values":values,
        "mail_ready":mail_ready(),"can_create":can_create() or created is not None})

def client(request,slug):
    staff(request);fresh(request);t=get_object_or_404(Tenant,slug=slug);error="";now=timezone.now()
    profile=load_profile(t)
    if request.method=="POST":
        action=request.POST.get("action");anchor=""
        try:
            if action=="profile":
                website=request.POST.get("website","").strip()
                if website:URLValidator(schemes=["https","http"])(website)
                if len(website)>200:raise ValidationError("Sito web troppo lungo.")
                profile.update(clean_profile(request.POST));store_profile(t,profile)
                if website!=t.website:t.website=website;t.save(update_fields=["website"])
                messages.success(request,"Anagrafica salvata.");anchor="anagrafica"
            elif action=="contact":
                contact=clean_contact(request.POST);index=request.POST.get("index","")
                contacts=profile["contacts"]
                if index=="":
                    if len(contacts)>=MAX_CONTACTS:raise ValidationError(f"Massimo {MAX_CONTACTS} referenti.")
                    if not contacts:contact["primary"]=True
                    contacts.append(contact)
                else:
                    i=int(index)
                    if not 0<=i<len(contacts):raise ValidationError("Referente non trovato.")
                    contacts[i]=contact
                if contact["primary"]:
                    for c in contacts:c["primary"]=c is contact
                store_profile(t,profile);messages.success(request,"Referente salvato.");anchor="referenti"
            elif action=="contact_remove":
                i=int(request.POST.get("index",""))
                contacts=profile["contacts"]
                if not 0<=i<len(contacts):raise ValidationError("Referente non trovato.")
                was=contacts.pop(i).get("primary")
                if was and contacts:contacts[0]["primary"]=True
                store_profile(t,profile);messages.success(request,"Referente rimosso.");anchor="referenti"
            elif action=="logo":
                from .site import clean_brand
                upload=request.FILES.get("logo")
                if not upload:raise ValidationError("Scegli un'immagine.")
                # 2.8.0: the shape is kept (wide wordmarks too) and the empty transparent margins are cut
                t.logo,t.logo_ratio=clean_brand(upload);t.save(update_fields=["logo","logo_ratio"])
                with scope(t):audit(t,request.user,"tenant.logo",t.pk)
                messages.success(request,"Logo aggiornato: compare nel portale dell'attività e nella pagina prenotazioni.")
            elif action=="logo_remove":
                t.logo=None;t.logo_ratio=1;t.save(update_fields=["logo","logo_ratio"])
                with scope(t):audit(t,request.user,"tenant.logo",t.pk,removed=True)
                messages.success(request,"Logo rimosso.")
            else:raise ValidationError("Operazione non valida.")
            return redirect(request.path+("#"+anchor if anchor else ""))
        except ValueError:error="Richiesta non valida."
        except ValidationError as ex:error=" ".join(ex.messages)
    t.usage=usage(t,now);t.attention=attention(t,t.usage,now)
    with scope(t):t.granted=active_grant(t,request.user) is not None
    members=list(Membership.objects.filter(tenant=t).select_related("user").order_by("role"))
    chart=bars(t.usage["weeks"],t.usage["first_week"],width=560,height=160)
    try:since=date.fromisoformat(profile["since"]) if profile["since"] else t.created.date()
    except ValueError:since=t.created.date()
    kinds={"email":"email","pec":"email","phone":"tel","since":"date"}
    rows=[{"key":k,"label":label,"limit":limit,"value":profile.get(k,""),"type":kinds.get(k,"text")} for k,label,limit in FIELDS]
    return render(request,"client.html",{"client":t,"profile":profile,"rows":rows,"since":since,"members":members,"chart":chart,"error":error,
        "contact_full":len(profile["contacts"])>=MAX_CONTACTS,"mail":mail_status(t),"business_label":business_label(t)})

def business_label(t):
    from .presets import PRESETS
    p=PRESETS.get(t.business_type);return p["label"] if p else ""

def mail_status(t):
    """2.6.0: how the confirmations of this business leave, for the client card."""
    from email.utils import formataddr
    from .site import agency_smtp
    if t.smtp:
        try:
            with scope(t):c=decrypt(t,t.smtp,"smtp")
            from .mail import describe
            return {"own":True,"sender":formataddr((c.get("sender_name") or "",c["sender"])),"how":describe(c)}
        except Exception:return {"own":True,"sender":"","how":""}
    c=agency_smtp()
    return {"own":False,"agency":c is not None,"sender":c and formataddr((c.get("sender_name") or "",c["sender"]))}
