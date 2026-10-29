import secrets
from datetime import timedelta,time
from django.conf import settings
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.validators import validate_email
from django.contrib.auth.password_validation import validate_password
from django.db import transaction,connection
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.contrib import messages
from .models import Tenant,User,Membership,Invitation,Location,Resource,Service,ServiceResource,Schedule,audit
from .forms import TenantForm,SMTPForm
from .identity import fresh
from .crypto import login_index,token_hash,encrypt,decrypt
from .tenancy import scope
from .mail import public_addresses
from .booking import queue,BookingError
from .permissions import can,ROLES,ROLE_CHOICES,active_grant
from .site import tenant_defaults,mail_ready as site_mail_ready

def staff(request):
    if not request.user.is_staff:raise PermissionDenied
def invitation(user):
    Invitation.objects.filter(user=user,used=False).update(used=True)
    token=secrets.token_urlsafe(32)
    Invitation.objects.create(token=token_hash(token),user=user,expires=timezone.now()+timedelta(hours=24))
    return settings.PUBLIC_ORIGIN+"/invite/"+token+"/"
def grant_access(user,password=""):
    """Two ways in: an invitation link (sent by email when SMTP works, or handed over by hand), or a password
    the administrator types now. Either way the second factor is set up at the first sign-in."""
    if not password:return "" if user.has_usable_password() else invitation(user)
    if user.has_usable_password():raise ValueError("Questo utente ha già un accesso: la sua password non viene cambiata. Usa Ripristina password.")
    validate_password(password,user)
    user.set_password(password);user.auth_version+=1;user.save(update_fields=["password","auth_version"])
    return ""
def account_for(post,tenant=None):
    """The account a form names: its email or, where the edition has them, its username with the email optional.
    An existing one is returned as it is, a new one is created without password. Returns (user, created).
    Found by the username alone, an account that works elsewhere (another business, the agency) is refused:
    short usernames repeat ("cassa", "admin"), the email says who it is."""
    from .models import clean_username
    username=clean_username(post.get("username","")) if settings.LOGIN_USERNAMES else ""
    email=post.get("email","").strip().casefold()
    if not email and not username and settings.LOGIN_USERNAMES:raise ValueError("Scrivi lo username o l'email.")
    if email or not username:validate_email(email)
    by_mail=User.objects.filter(login_index=login_index(email)).first() if email else None
    found=by_mail or (User.objects.filter(name_index=login_index(username)).first() if username else None)
    if found and (username and found.username!=username or email and found.email!=email):
        raise ValueError("Username ed email non sono dello stesso account: per una persona che ha già un accesso basta uno dei due.")
    if found and not by_mail and elsewhere(found,tenant):
        raise ValueError("Username già usato da un'altra persona: scegline un altro, oppure scrivi l'email di questa persona.")
    if found:return found,False
    if settings.LOGIN_USERNAMES and not username:raise ValueError("Per una persona nuova scegli lo username: l'email da sola basta per chi ha già un accesso.")
    return User.objects.create_user(email,username=username),True
def mail_ready(tenant):
    from .site import mail_ready as ready
    return ready(tenant)
def tenant_limit():return 1 if settings.EDITION=="private" else settings.TENANT_LIMIT
def private_guard():
    limit=tenant_limit()
    if limit is not None:
        if connection.vendor=="postgresql":
            with connection.cursor() as c:c.execute("SELECT pg_advisory_xact_lock(80561239)")
        if Tenant.objects.count()>=limit:
            raise ValidationError("L'edizione Private gestisce una sola attività con più utenti." if settings.EDITION=="private" else f"Puoi gestire al massimo {limit} attività. {settings.MORE_TENANTS_HINT}")
def seed_tenant(tenant,preset=""):
    """First configuration of a new business: the type's real catalog (for a salon Taglio, Piega and Colore, two
    operators, Tuesday to Saturday), the same that Configura l'attività applies; a generic appointment without a type."""
    from .presets import PRESETS,initial
    from .profile import PROFILES,apply_profile
    if preset in PROFILES:
        apply_profile(tenant,preset,["Sede principale"]);return
    p=PRESETS.get(preset)
    with scope(tenant):
        location=Location.objects.create(tenant=tenant,name="Sede principale")
        if p is None:
            resource=Resource.objects.create(tenant=tenant,location=location,name="Operatore 1")
            service=Service.objects.create(tenant=tenant,location=location,name="Appuntamento",kind="resource")
            ServiceResource.objects.create(tenant=tenant,resource=resource,service=service)
        else:
            service=Service.objects.create(tenant=tenant,location=location,name=p["label"].split(" / ")[0],**initial(preset))
            if p["kind"] in ("resource","day"):
                for n in (1,2):
                    resource=Resource.objects.create(tenant=tenant,location=location,name=f"{p['resource_label'] or 'Risorsa'} {n}")
                    ServiceResource.objects.create(tenant=tenant,resource=resource,service=service)
        days=range(7) if p and p["kind"] in ("day","capacity","event") else range(5)
        for d in days:Schedule.objects.create(tenant=tenant,weekday=d,start=time(9) if not p or p["kind"]!="day" else time(0),end=time(18) if not p or p["kind"]!="day" else time(23,59,59,999999))
def tenant_settings(request,slug):
    staff(request);fresh(request);tenant=request.tenant;error=""
    form=TenantForm(request.POST or None,request.FILES or None,instance=tenant)
    if request.method=="POST":
        fresh(request)
        if form.is_valid():
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=tenant.pk)
                form.save();audit(tenant,request.user,"tenant.settings",tenant.pk)
            messages.success(request,"Impostazioni aggiornate.");return redirect("tenant_settings",slug)
    return render(request,"form.html",{"form":form,"title":"Impostazioni dell'attività","description":"Brand, piano, privacy e autorizzazioni di invio. Riservato all'amministratore.","error":error})
def smtp_view(request,slug):
    from .mailsetup import page,Target
    staff(request);tenant=request.tenant
    return page(request,Target(tenant),"Invio email","La casella da cui partono le conferme delle prenotazioni, le notifiche e gli inviti di "+tenant.name+": Gmail, Microsoft 365 o l'email del suo dominio. Se il marketing è attivo, serve anche per le campagne. Senza, si usa l'invio dell'agenzia. Le credenziali restano cifrate e gli utenti dell'attività non le vedono")
def team_admin(request):
    """2.13.0: who has access to a business and with which role is the agency's, like its type and locations:
    the owner does not even see the page."""
    if not request.user.is_staff:raise PermissionDenied
def scope_values(request,tenant):
    locations=[str(x) for x in Location.objects.filter(pk__in=request.POST.getlist("locations")).values_list("pk",flat=True)]
    resources=[str(x) for x in Resource.objects.filter(pk__in=request.POST.getlist("resources")).values_list("pk",flat=True)]
    return locations,resources
def elsewhere(user,tenant):
    """Someone who also works in another business (or for the agency): the access of that person is not this business's to hand out."""
    return user.is_staff or Membership.objects.filter(user=user).exclude(tenant=tenant).exists()
def members_view(request,slug):
    team_admin(request);fresh(request);tenant=request.tenant;link="";error=""
    if request.method=="POST":
        fresh(request)
        try:
            action=request.POST.get("action")
            if action=="invite":
                role=request.POST.get("role")
                if role not in ROLES:raise ValueError("Ruolo non valido.")
                locations,resources=scope_values(request,tenant)
                if role in ("manager","operator") and not locations and not resources:
                    # a single-location business: the location is implicit
                    only=list(Location.objects.values_list("pk",flat=True)[:2])
                    if len(only)==1:locations=[str(only[0])]
                    elif role=="operator":raise ValueError("Assegna all'operatore sede almeno una sede o una risorsa.")
                with transaction.atomic():
                    u,created=account_for(request.POST,tenant)
                    # an account that already exists is only added: activated or given a password from here only by the
                    # business it belongs to alone, never by the agency (the link or the password would be its own)
                    if not created and (request.POST.get("password") or not u.has_usable_password()):
                        if elsewhere(u,tenant):raise ValueError("Questa persona ha già un accesso in un'altra attività, non ancora attivato: deve attivarlo dal suo invito, poi puoi aggiungerla.")
                    if u.pk==request.user.pk and not request.member:raise ValueError("L'agenzia entra nei contenuti con l'accesso di assistenza concesso dall'attività, non aggiungendo se stessa.")
                    member,_=Membership.objects.get_or_create(tenant=tenant,user=u)
                    if member.user_id==request.user.id and role!="owner":raise ValueError("Non puoi togliere a te stesso il ruolo di amministratore.")
                    if member.role=="owner" and role!="owner" and not Membership.objects.filter(tenant=tenant,role="owner").exclude(pk=member.pk).exists():raise ValueError("L'attività deve avere almeno un amministratore.")
                    member.role=role;member.can_schedule=role=="owner" or request.POST.get("can_schedule")=="on"
                    member.locations=[] if role=="owner" else locations;member.resources=[] if role=="owner" else resources
                    member.save()
                    direct=bool(request.POST.get("password"))
                    link=grant_access(u,request.POST.get("password",""))
                    if link and u.email:queue(tenant,{"to":u.email,"subject":"Invito · "+tenant.name,"body":"Attiva il tuo accesso:\n"+link})
                    if direct:messages.success(request,u.login_name+" può già accedere con la password che hai scelto.")
                    audit(tenant,request.user,"member.invited",u.pk,role=role,locations=len(member.locations),resources=len(member.resources))
            else:
                member=get_object_or_404(Membership,tenant=tenant,pk=request.POST.get("member"))
                u=member.user
                if action in ("quick_on","quick_off"):
                    # 2.13.0: one click from the program's PC and the registered workstations, for an owner only
                    from .quick import available
                    if not available():raise ValueError("Questa edizione non ha postazioni registrate: si entra con la password.")
                    if member.role!="owner" or u.is_staff:raise ValueError("Senza password entra solo un titolare.")
                    u.quick_login=action=="quick_on";u.save(update_fields=["quick_login"])
                    audit(tenant,request.user,"member.quick_login",u.pk,on=u.quick_login)
                    messages.success(request,(u.login_name+" entra senza password dal PC del programma e dalle postazioni registrate.") if u.quick_login else (u.login_name+" entra di nuovo con la password."))
                elif action=="remove":
                    with transaction.atomic():
                        owners=list(Membership.objects.select_for_update().filter(tenant=tenant,role="owner"))
                        if member.role=="owner" and len(owners)==1:raise ValueError("L'attività deve avere almeno un amministratore.")
                        member.delete();audit(tenant,request.user,"member.removed",u.pk)
                elif action=="reset":
                    if u.is_staff:raise ValueError("Usa la procedura di recupero amministratore da console.")
                    # the reset link opens the person's account in every business: only the agency resets someone who works elsewhere too
                    if elsewhere(u,tenant) and not request.user.is_staff:raise ValueError("Questa persona lavora anche in un'altra attività: il ripristino della password lo fa l'agenzia.")
                    agency=request.user.is_staff and not request.member
                    u.auth_version+=1;u.set_unusable_password();u.save(update_fields=["auth_version","password"])
                    link=invitation(u)
                    if u.email:queue(tenant,{"to":u.email,"subject":"Ripristino accesso · "+tenant.name,"body":"Imposta una nuova password:\n"+link+"\nIl metodo MFA rimane attivo."})
                    audit(tenant,request.user,"member.password_reset",u.pk)
                    if agency and u.email:
                        # the agency enters the contents only with a support grant: the link goes to the person, not on this
                        # screen; without an email the agency hands it over, since only the agency manages the people (2.13.0)
                        link="";messages.success(request,"Link per la nuova password inviato a "+u.email+".")
                else:raise ValueError("Operazione non valida.")
        except (ValueError,ValidationError) as ex:error=" ".join(ex.messages) if isinstance(ex,ValidationError) else str(ex)
    names={str(x.pk):x.name for x in list(Location.objects.all())+list(Resource.objects.all())}
    members=list(Membership.objects.filter(tenant=tenant).select_related("user"))
    for m in members:m.scope_names=[names.get(x,"?") for x in m.locations+m.resources]
    from .quick import available
    return render(request,"members.html",{"members":members,"invite_link":link,"error":error,"quick_available":available(),"roles":ROLE_CHOICES,"mail_ready":mail_ready(tenant),
        "locations":Location.objects.order_by("name"),"resources":Resource.objects.order_by("name")})
def support_view(request,slug):
    """The owner grants time-boxed content access to one platform staff user."""
    from .models import SupportGrant
    if not can(request,"users.manage"):raise PermissionDenied
    tenant=request.tenant;error=""
    if request.method=="POST":
        fresh(request)
        try:
            action=request.POST.get("action")
            if action=="grant":
                staff_user=User.objects.by_login(request.POST.get("email","").strip()[:254]).filter(is_staff=True).first()
                if not staff_user:raise ValueError("Nessun amministratore della piattaforma con questa email." if not settings.LOGIN_USERNAMES else "Nessun amministratore con questo username o email.")
                reason=request.POST.get("reason","").strip()
                if not 10<=len(reason)<=300:raise ValueError("Descrivi il motivo dell'assistenza (10-300 caratteri).")
                try:hours=int(request.POST.get("hours","4"))
                except ValueError:hours=0
                if not 1<=hours<=72:raise ValueError("Durata tra 1 e 72 ore.")
                grant=SupportGrant.objects.create(tenant=tenant,user=staff_user,reason=reason,expires=timezone.now()+timedelta(hours=hours),created_by=str(request.user.pk))
                audit(tenant,request.user,"support.granted",grant.pk,hours=hours)
                messages.success(request,f"Accesso concesso per {hours} ore.")
            elif action=="revoke":
                try:grant=get_object_or_404(SupportGrant.objects,pk=request.POST.get("grant"))
                except ValidationError:raise ValueError("Accesso non trovato.")
                grant.revoked=True;grant.save(update_fields=["revoked"])
                audit(tenant,request.user,"support.revoked",grant.pk)
                messages.success(request,"Accesso revocato.")
            else:raise ValueError("Operazione non valida.")
            return redirect("support_grants",slug)
        except ValueError as ex:error=str(ex)
    from .models import Audit
    grants=SupportGrant.objects.select_related("user").order_by("-created")[:50]
    reads=Audit.objects.filter(action="support.access").order_by("-created")[:100]
    return render(request,"support.html",{"grants":grants,"reads":reads,"now":timezone.now(),"error":error})

def notifications_view(request,slug):
    """Owner settings for business notifications: email address and Telegram bot."""
    from .notify import TOKEN,CHAT,summary
    if not can(request,"users.manage"):raise PermissionDenied
    tenant=request.tenant;error=""
    current=decrypt(tenant,tenant.telegram,"telegram") if tenant.telegram else {}
    if request.method=="POST":
        fresh(request)
        try:
            action=request.POST.get("action","save")
            if action=="save":
                email=request.POST.get("notify_email","").strip()
                if email:validate_email(email)
                token=request.POST.get("telegram_token","").strip() or current.get("token","")
                chat=request.POST.get("telegram_chat","").strip()
                if request.POST.get("telegram_off")=="on":token=chat=""
                if bool(token)!=bool(chat):raise ValueError("Per Telegram servono sia il token del bot sia la chat.")
                if token and (not TOKEN.fullmatch(token) or not CHAT.fullmatch(chat)):raise ValueError("Token del bot o chat Telegram non validi.")
                with transaction.atomic():
                    t=Tenant.objects.select_for_update().get(pk=tenant.pk)
                    t.notify_email=email;t.telegram=encrypt(t,{"token":token,"chat":chat},"telegram") if token else ""
                    t.save(update_fields=["notify_email","telegram"])
                    audit(t,request.user,"notifications.saved",telegram=bool(token),email=bool(email))
                messages.success(request,"Notifiche salvate.")
            elif action=="client_mails":
                # 2.13.0: which emails reach the clients and with which words, once the agency allows it
                from .client_mails import clean
                if not tenant.client_mails_enabled:raise PermissionDenied
                try:mails=clean(request.POST)
                except BookingError as ex:raise ValueError(str(ex))
                with transaction.atomic():
                    t=Tenant.objects.select_for_update().get(pk=tenant.pk)
                    t.client_mails=mails;t.save(update_fields=["client_mails"])
                    audit(t,request.user,"client_mails.saved",on=[k for k,v in mails.items() if v["on"]])
                messages.success(request,"Email ai clienti salvate.")
                return redirect(reverse("notifications",args=[slug])+"#clienti")
            elif action=="test":
                if not (tenant.notify_email or tenant.telegram):raise ValueError("Configura prima un indirizzo email o Telegram.")
                text="Prova notifiche · "+tenant.name+"\nSe leggi questo messaggio, le notifiche funzionano."
                if tenant.notify_email:queue(tenant,{"to":tenant.notify_email,"subject":"Prova notifiche · "+tenant.name,"body":text})
                if tenant.telegram:
                    d=queue(tenant,{"text":text});d.channel="telegram";d.save(update_fields=["channel"])
                messages.success(request,"Prova accodata: arriva al prossimo giro di invii.")
            return redirect("notifications",slug)
        except (ValueError,ValidationError) as ex:error=" ".join(ex.messages) if isinstance(ex,ValidationError) else str(ex)
    from . import client_mails as cm
    now=cm.current(tenant)
    mails=[{"key":k,"label":label,"help":help,**now[k],"default":cm.DEFAULTS[k]} for k,label,help in cm.KINDS]
    return render(request,"notifications.html",{"notify_email":tenant.notify_email,"chat":current.get("chat",""),"has_token":bool(current.get("token")),"error":error,
        "mails":mails,"mail_fields":cm.FIELDS,"hours":cm.HOURS,"sample":cm.sample(tenant)})
