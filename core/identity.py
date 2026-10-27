import json,secrets,time,io
from datetime import timedelta
from urllib.parse import urlparse
import pyotp,qrcode
from django.conf import settings
from django.contrib.auth import login as sign_in,logout,update_session_auth_hash
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import make_password,check_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.http import JsonResponse,HttpResponse
from django.shortcuts import render,redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from webauthn import generate_registration_options,verify_registration_response,generate_authentication_options,verify_authentication_response,options_to_json
from webauthn.helpers.structs import AuthenticatorSelectionCriteria,UserVerificationRequirement,ResidentKeyRequirement,PublicKeyCredentialDescriptor
from .models import User,Passkey,Ceremony,Invitation,RateBucket
from .crypto import login_index,protect,reveal,token_hash,b64,unb64

DUMMY=make_password(secrets.token_urlsafe(32))
class EmailBackend(BaseBackend):
    def authenticate(self,request,email=None,password=None,**kwargs):
        u=User.objects.filter(login_index=login_index(email or "")).first()
        if not u:check_password(password or "",DUMMY);return None
        return u if u.is_active and u.check_password(password or "") else None
    def get_user(self,user_id):return User.objects.filter(pk=user_id,is_active=True).first()
def throttle(request,label,limit=30,seconds=900,identity=None):
    address=request.META.get("REMOTE_ADDR","unknown")
    if getattr(settings,"TRUST_PROXY",False):
        import ipaddress
        # The trusted proxy APPENDS the address it sees (nginx/NPMplus $proxy_add_x_forwarded_for): only the rightmost entry is reliable; anything before it is client-supplied.
        forwarded=request.META.get("HTTP_X_FORWARDED_FOR","").split(",")[-1].strip()
        try:address=str(ipaddress.ip_address(forwarded))
        except ValueError:raise PermissionDenied("Proxy non configurato correttamente.")
    key=token_hash(label+":"+(identity if identity is not None else address))
    now=timezone.now()
    with transaction.atomic():
        RateBucket.objects.get_or_create(key=key,defaults={"ends":now+timedelta(seconds=seconds)})
        bucket=RateBucket.objects.select_for_update().get(key=key)
        if bucket.ends<=now:bucket.count=0;bucket.ends=now+timedelta(seconds=seconds)
        if bucket.count>=limit:raise PermissionDenied("Troppi tentativi. Riprova più tardi.")
        bucket.count+=1;bucket.save()
def finish_login(request,user,remember=None):
    """remember: stay signed in on this device (2.6.0). None keeps the choice made at the password step."""
    if remember is None:remember=request.session.get("preauth",{}).get("remember",request.session.get("remember",False))
    sign_in(request,user,backend="core.identity.EmailBackend")
    request.session["remember"]=bool(remember)
    # remembered: a cookie that lasts and slides with every visit; otherwise it ends with the browser
    request.session.set_expiry(settings.SESSION_REMEMBER_DAYS*86400 if remember else 0)
    request.session["mfa"]=True
    request.session["auth_version"]=user.auth_version
    request.session["verified_at"]=time.time()
    request.session["last_seen"]=time.time()
    request.session.pop("preauth",None)
def login_view(request):
    if request.user.is_authenticated and request.session.get("mfa"):return redirect("home")
    error=""
    if request.method=="POST":
        email=request.POST.get("email","")[:254]
        throttle(request,"login-ip",50)
        throttle(request,"login-user",10,identity=login_index(email))
        from django.contrib.auth import authenticate
        u=authenticate(request,email=email,password=request.POST.get("password","")[:1024])
        if u:
            if u.mfa_ready:
                request.session.cycle_key()
                request.session["preauth"]={"user":str(u.pk),"until":time.time()+300,"version":u.auth_version,"remember":request.POST.get("remember")=="1"}
                return redirect("verify_mfa")
            # 2.4.0: the second factor is recommended, not required. Without one the password opens the
            # portal straight away and every page shows a reminder to add Authenticator or a passkey.
            finish_login(request,u,remember=request.POST.get("remember")=="1")
            return redirect("home")
        error="Credenziali non valide."
    from .trial import available,business_types
    from .site import current
    site=current(request)
    return render(request,"login.html",{"error":error,"trial_available":available(),"business_types":business_types(),"pricing_on":settings.EDITION=="cloud" and site.pricing_enabled,"why_on":settings.EDITION=="cloud" and site.why_enabled})
def verify_totp(u,code):
    if not u.totp or not code.isascii():return False  # compare_digest refuses non-ASCII text (a pasted "１２３")
    generator=pyotp.TOTP(reveal(u.totp,"totp:"+str(u.pk)))
    step=int(time.time())//30
    for candidate in (step,step-1,step+1):
        if candidate>u.last_totp and secrets.compare_digest(generator.at(candidate*30),code):
            u.last_totp=candidate;u.save(update_fields=["last_totp"]);return True
    return False
def verify_view(request):
    pre=request.session.get("preauth",{})
    if pre.get("until",0)<time.time():return redirect("login")
    error=""
    if request.method=="POST":
        throttle(request,"mfa",10)
        throttle(request,"mfa-user",10,identity=pre["user"])
        with transaction.atomic():
            u=User.objects.select_for_update().filter(pk=pre["user"],is_active=True,auth_version=pre["version"]).first()
            code=request.POST.get("code","").strip()
            valid=bool(u and verify_totp(u,code))
            if u and not valid and token_hash(code) in u.recovery:
                u.recovery.remove(token_hash(code));u.save(update_fields=["recovery"]);valid=True
            if valid:finish_login(request,u);return redirect("home")
        error="Codice non valido, scaduto o già utilizzato."
    return render(request,"verify.html",{"error":error})
@require_POST
def logout_view(request):logout(request);return redirect("login")
def new_recovery(request,u):
    codes=[secrets.token_urlsafe(16) for _ in range(10)]
    u.recovery=[token_hash(c) for c in codes];u.save(update_fields=["recovery"])
    request.session["recovery_once"]=protect(codes,"recovery:"+str(u.pk))
FRESH_SECONDS=900
class ConfirmNeeded(PermissionDenied):
    """Sensitive pages need the second factor confirmed in the last FRESH_SECONDS: the middleware turns this
    into the confirmation page, which then brings the user back."""
def is_fresh(request):
    return bool(request.session.get("mfa")) and time.time()-request.session.get("verified_at",0)<=FRESH_SECONDS
def fresh(request):
    if not is_fresh(request):raise ConfirmNeeded("Per questa operazione conferma che sei tu.")
def safe_next(value):
    return value if isinstance(value,str) and value.startswith("/") and not value.startswith("//") and "\\" not in value else "/"
def confirm_view(request):
    """Step-up: the code of the authenticator app, a recovery code or a passkey, then back to the page.
    An account without a second factor confirms with its password."""
    if not request.user.is_authenticated or not request.session.get("mfa"):return redirect("login")
    target=safe_next(request.POST.get("next") or request.GET.get("next"))
    request.session["after_confirm"]=target
    error="";use_password=not request.user.mfa_ready
    if request.method=="POST" and use_password:
        throttle(request,"mfa",10);throttle(request,"mfa-user",10,identity=str(request.user.pk))
        if request.user.check_password(request.POST.get("password","")[:1024]):
            request.session["verified_at"]=time.time();request.session.pop("after_confirm",None)
            return redirect(target)
        error="Password non valida."
    elif request.method=="POST":
        throttle(request,"mfa",10);throttle(request,"mfa-user",10,identity=str(request.user.pk))
        with transaction.atomic():
            u=User.objects.select_for_update().get(pk=request.user.pk)
            code=request.POST.get("code","").strip()
            valid=bool(code) and verify_totp(u,code)
            if not valid and code and token_hash(code) in u.recovery:
                u.recovery.remove(token_hash(code));u.save(update_fields=["recovery"]);valid=True
        if valid:
            request.session["verified_at"]=time.time();request.session.pop("after_confirm",None)
            return redirect(target)
        error="Codice non valido, scaduto o già utilizzato."
    return render(request,"confirm.html",{"error":error,"next":target,"use_password":use_password,"has_passkey":request.user.passkeys.exists(),"has_totp":bool(request.user.totp)})
def security_view(request):
    if not request.user.is_authenticated:return redirect("login")
    error="";notice=""
    u=request.user
    if request.method=="POST":
        throttle(request,"security",20)
        action=request.POST.get("action")
        try:
            if action=="totp_start":
                fresh(request)  # also the first factor: a forgotten open session must not let someone else add theirs
                request.session["totp_pending"]=protect({"seed":pyotp.random_base32(),"until":time.time()+600},"totp-pending:"+str(u.pk))
            elif action=="totp_confirm":
                pending=reveal(request.session.get("totp_pending",""),"totp-pending:"+str(u.pk))
                if pending["until"]<time.time():raise ValueError("Configurazione scaduta.")
                with transaction.atomic():
                    u=User.objects.select_for_update().get(pk=u.pk)
                    fresh(request)  # also the first factor: a forgotten open session must not let someone else add theirs
                    seed=pending["seed"];gen=pyotp.TOTP(seed);step=int(time.time())//30
                    if not gen.verify(request.POST.get("code",""),valid_window=0):raise ValueError("Codice non valido.")
                    u.totp=protect(seed,"totp:"+str(u.pk));u.last_totp=step;u.save()
                    new_recovery(request,u)
                    finish_login(request,u)
                request.session.pop("totp_pending",None)
                notice="Authenticator attivato."
            elif action=="recovery":
                fresh(request);new_recovery(request,u);notice="Codici precedenti revocati."
            elif action=="password":
                fresh(request)
                if not u.check_password(request.POST.get("current_password","")):raise ValueError("Password attuale non valida.")
                pw=request.POST.get("new_password","");validate_password(pw,u)
                u.set_password(pw);u.auth_version+=1;u.save()
                update_session_auth_hash(request,u);request.session["auth_version"]=u.auth_version
                notice="Password aggiornata. Le altre sessioni sono state revocate."
            elif action=="delete_key":
                fresh(request)
                with transaction.atomic():
                    u=User.objects.select_for_update().get(pk=u.pk)
                    if not u.totp and u.passkeys.count()<=1:raise ValueError("Mantieni almeno un metodo MFA.")
                    u.passkeys.filter(pk=request.POST.get("key")).delete()
            else:raise ValueError("Operazione non valida.")
        except (ValueError,ValidationError,KeyError) as ex:error=" ".join(ex.messages) if isinstance(ex,ValidationError) else str(ex)
    codes=None
    if request.session.get("recovery_once"):codes=reveal(request.session.pop("recovery_once"),"recovery:"+str(u.pk))
    seed=""
    if request.session.get("totp_pending"):
        p=reveal(request.session["totp_pending"],"totp-pending:"+str(u.pk))
        if p["until"]>time.time():seed=p["seed"]
    return render(request,"security.html",{"error":error,"notice":notice,"seed":seed,"codes":codes,"keys":u.passkeys.all(),"totp_enabled":bool(u.totp)})
def qr_view(request):
    if not request.user.is_authenticated or not request.session.get("totp_pending"):raise PermissionDenied
    p=reveal(request.session["totp_pending"],"totp-pending:"+str(request.user.pk))
    if p["until"]<time.time():raise PermissionDenied
    uri=pyotp.TOTP(p["seed"]).provisioning_uri(name=request.user.email,issuer_name="TD Booking Suite")
    image=qrcode.make(uri);buf=io.BytesIO();image.save(buf,format="PNG")
    return HttpResponse(buf.getvalue(),content_type="image/png")
def json_body(request):
    try:
        obj=json.loads(request.body)
        if not isinstance(obj,dict):raise ValueError
        return obj
    except (ValueError,TypeError):raise ValidationError("Richiesta non valida.")
@require_POST
def passkey_begin(request,kind):
    throttle(request,"passkey",30)
    rp=urlparse(settings.PUBLIC_ORIGIN).hostname
    data=json_body(request)
    if kind=="register":
        if not request.user.is_authenticated:raise PermissionDenied
        fresh(request)
        u=request.user
        if u.passkeys.count()>=20:return JsonResponse({"error":"Limite passkey raggiunto."},status=400)
        opts=generate_registration_options(rp_id=rp,rp_name="TD Booking Suite",user_id=u.pk.bytes,user_name=u.email,
            exclude_credentials=[PublicKeyCredentialDescriptor(id=unb64(k.credential_id)) for k in u.passkeys.all()],
            authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,user_verification=UserVerificationRequirement.REQUIRED))
    elif kind=="login":
        u=User.objects.filter(login_index=login_index(str(data.get("email",""))[:254]),is_active=True).first()
        opts=generate_authentication_options(rp_id=rp,user_verification=UserVerificationRequirement.REQUIRED,
            allow_credentials=[PublicKeyCredentialDescriptor(id=unb64(k.credential_id)) for k in u.passkeys.all()] if u else [])
    else:raise PermissionDenied
    ceremony=Ceremony.objects.create(user=u,kind=kind,challenge=b64(opts.challenge),expires=timezone.now()+timedelta(minutes=5))
    request.session["ceremony"]=str(ceremony.pk)
    return JsonResponse({"id":str(ceremony.pk),"options":json.loads(options_to_json(opts))})
@require_POST
def passkey_finish(request,kind):
    throttle(request,"passkey-finish",20)
    data=json_body(request);error="Passkey non valida o richiesta scaduta."
    if data.get("id")!=request.session.pop("ceremony",None):return JsonResponse({"error":error},status=400)
    with transaction.atomic():
        c=Ceremony.objects.select_for_update().filter(pk=data["id"],kind=kind,used=False,expires__gt=timezone.now()).first()
        if not c:return JsonResponse({"error":error},status=400)
        c.used=True;c.save(update_fields=["used"])
        try:
            u=User.objects.select_for_update().get(pk=c.user_id,is_active=True)
            credential=data["credential"]
            common={"credential":credential,"expected_challenge":unb64(c.challenge),"expected_rp_id":urlparse(settings.PUBLIC_ORIGIN).hostname,"expected_origin":settings.PUBLIC_ORIGIN,"require_user_verification":True}
            if kind=="register":
                if not request.user.is_authenticated or request.user.pk!=u.pk:raise ValueError()
                fresh(request)  # also the first factor: a forgotten open session must not let someone else add theirs
                result=verify_registration_response(**common)
                Passkey.objects.create(user=u,credential_id=b64(result.credential_id),public_key=b64(result.credential_public_key),sign_count=result.sign_count,name=str(data.get("name","Passkey"))[:80])
                if not u.recovery:new_recovery(request,u)
            elif kind=="login":
                # Normalise ID encoding once; verification also binds rawId/id.
                key=u.passkeys.select_for_update().get(credential_id=b64(unb64(credential["id"]+"="*((-len(credential["id"]))%4))))
                handle=credential.get("response",{}).get("userHandle")
                if handle and unb64(handle+"="*((-len(handle))%4))!=u.pk.bytes:raise ValueError()
                result=verify_authentication_response(**common,credential_public_key=unb64(key.public_key),credential_current_sign_count=key.sign_count)
                key.sign_count=result.new_sign_count;key.save(update_fields=["sign_count"])
            else:raise ValueError()
            # a passkey login stays signed in when the box is ticked; adding a passkey keeps the current choice
            # confirming who you are with a passkey (already signed in) keeps the "Resta collegato" choice
            again=request.user.is_authenticated and request.user.pk==u.pk
            finish_login(request,u,remember=bool(data.get("remember")) if kind=="login" and not again else None)
            return JsonResponse({"redirect":"/security/" if kind=="register" else safe_next(request.session.pop("after_confirm","/"))})
        except Exception:
            return JsonResponse({"error":error},status=400)
def invite_view(request,token):
    error=""
    obj=Invitation.objects.filter(token=token_hash(token),used=False,expires__gt=timezone.now()).first()
    if not obj:return render(request,"message.html",{"title":"Invito non valido","text":"Il collegamento è scaduto o è già stato utilizzato."},status=400)
    if request.method=="POST":
        throttle(request,"invite",10)
        try:
            pw=request.POST.get("password","")
            validate_password(pw,obj.user)
            with transaction.atomic():
                obj=Invitation.objects.select_for_update().get(pk=obj.pk)
                if obj.used or obj.expires<=timezone.now():raise ValueError("Invito scaduto.")
                u=User.objects.select_for_update().get(pk=obj.user_id)
                if not u.is_active:raise ValueError("Account disabilitato.")
                u.set_password(pw);u.auth_version+=1;u.save()
                obj.used=True;obj.save(update_fields=["used"])
            return redirect("login")
        except (ValidationError,ValueError) as ex:error=" ".join(ex.messages) if isinstance(ex,ValidationError) else str(ex)
    return render(request,"invite.html",{"error":error})
