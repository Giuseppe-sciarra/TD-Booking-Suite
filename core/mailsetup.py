"""2.6.0: the email page of the agency and of each business, with three ways to send.

SMTP (any provider), Google / Gmail with OAuth, Microsoft 365 / Exchange with Graph. The
configuration is one encrypted dict, as before; "method" says which way it sends (no method = SMTP,
the configurations saved before 2.6.0).
"""
import secrets,time
from email.utils import formataddr
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.shortcuts import redirect,render
from .crypto import protect,reveal,encrypt,decrypt
from .models import SiteSettings,Tenant,audit
from .tenancy import scope
from .forms import NO_AUTOFILL

def redirect_uri():return settings.PUBLIC_ORIGIN.rstrip("/")+"/oauth/google/"

def clean_name(value):
    value=(value or "").strip()
    if any(c in value for c in "\r\n<>\"@"):raise forms.ValidationError("Solo il nome, senza a capo, virgolette o indirizzi.")
    return value

class GoogleForm(forms.Form):
    def __init__(self,*a,**kw):kw.setdefault("label_suffix","");super().__init__(*a,**kw)
    client_id=forms.CharField(label="Client ID",max_length=200,help_text="Da Google Cloud, API e servizi, Credenziali: un ID client OAuth di tipo Applicazione web")
    client_secret=forms.CharField(label="Client secret (vuoto = mantieni)",widget=forms.PasswordInput(attrs=NO_AUTOFILL),required=False,max_length=200)
    sender_name=forms.CharField(label="Nome del mittente",max_length=80,required=False)
    sender=forms.EmailField(label="Email del mittente",required=False,help_text="Vuoto: l'account Google collegato. Un altro indirizzo deve essere un alias di Gmail, Invia messaggio come")
    reply_to=forms.EmailField(label="Rispondi a",required=False,help_text="Facoltativo: dove arrivano le risposte dei clienti")
    def clean_sender_name(self):return clean_name(self.cleaned_data["sender_name"])

class GraphForm(forms.Form):
    def __init__(self,*a,**kw):kw.setdefault("label_suffix","");super().__init__(*a,**kw)
    tenant_id=forms.CharField(label="ID della directory (tenant)",max_length=100,help_text="In Entra ID, Panoramica: ID directory (tenant), oppure il dominio, per esempio tuodominio.onmicrosoft.com")
    client_id=forms.CharField(label="ID dell'applicazione (client)",max_length=100)
    client_secret=forms.CharField(label="Valore del segreto client (vuoto = mantieni)",widget=forms.PasswordInput(attrs=NO_AUTOFILL),required=False,max_length=300)
    mailbox=forms.EmailField(label="Casella da cui partono le email",help_text="Per esempio prenotazioni@tuodominio.it: una casella di Microsoft 365 o di Exchange Online")
    sender_name=forms.CharField(label="Nome del mittente",max_length=80,required=False)
    sender=forms.EmailField(label="Email del mittente",required=False,help_text="Vuoto: la casella qui sopra. Un altro indirizzo richiede il permesso Invia come su quella casella")
    reply_to=forms.EmailField(label="Rispondi a",required=False)
    field_order=["tenant_id","client_id","client_secret","mailbox","sender_name","sender","reply_to"]
    def clean_sender_name(self):return clean_name(self.cleaned_data["sender_name"])
    def clean_tenant_id(self):
        v=self.cleaned_data["tenant_id"].strip()
        if not v or any(c in v for c in "/?#@ "):raise forms.ValidationError("ID non valido.")
        return v

class Target:
    """Where a configuration lives: the agency (SiteSettings) or one business."""
    def __init__(self,tenant=None):self.tenant=tenant;self.site=SiteSettings.current()
    @property
    def key(self):return self.tenant.slug if self.tenant else "site"
    @property
    def name(self):return self.tenant.name if self.tenant else self.site.name
    @property
    def url(self):return f"/t/{self.tenant.slug}/smtp/" if self.tenant else "/platform/smtp/"
    def load(self):
        if self.tenant:
            with scope(self.tenant):return decrypt(self.tenant,self.tenant.smtp,"smtp") if self.tenant.smtp else {}
        return reveal(self.site.smtp,"site-smtp") if self.site.smtp else {}
    def store(self,data,actor):
        if self.tenant:
            with scope(self.tenant):
                self.tenant.smtp=encrypt(self.tenant,data,"smtp") if data else "";self.tenant.save(update_fields=["smtp"])
                audit(self.tenant,actor,"smtp.updated" if data else "smtp.removed",self.tenant.pk,method=(data or {}).get("method","smtp"))
        else:
            self.site.smtp=protect(data,"site-smtp") if data else "";self.site.save(update_fields=["smtp","updated"])
    def effective(self):
        from .mail import smtp_config
        from .site import agency_smtp
        if self.tenant:
            with scope(self.tenant):return smtp_config(self.tenant)
        return agency_smtp(self.site)

TABS=[("smtp","SMTP","Qualsiasi casella: del dominio, Aruba, Register.it, Gmail o Microsoft con password"),
      ("google","Google / Gmail","Collegamento con l'account Google, senza password SMTP"),
      ("microsoft","Microsoft 365 / Exchange","Invio da una casella di Microsoft 365 con Microsoft Graph")]
TAB_OF={"smtp":"smtp","gmail":"google","graph":"microsoft"}

def page(request,target,title,description,saved_to=None):
    """The whole page. saved_to: where to go after saving (the agency returns to Impostazioni generali)."""
    from .forms import SMTPForm
    from .identity import fresh
    from .mail import public_addresses,send_test,describe,METHODS
    from .site import PROVIDERS
    fresh(request)
    current=target.load();method=current.get("method","smtp") if current else ""
    tab=request.GET.get("metodo") or TAB_OF.get(method,"smtp")
    if tab not in TAB_OF.values():tab="smtp"
    action=request.POST.get("action","save_smtp") if request.method=="POST" else ""
    done=lambda:redirect(saved_to or target.url)
    if action=="test":
        to=request.POST.get("test_to","").strip()
        try:validate_email(to);problem=send_test(target.effective(),to,target.name)
        except ValidationError:problem="Indirizzo del destinatario non valido."
        if problem:messages.error(request,"Email di prova non partita. "+problem)
        else:messages.success(request,"Email di prova inviata a "+to+". Se non la vedi, controlla anche lo spam.")
        return redirect(target.url)
    if action=="remove":
        if method=="gmail" and current.get("refresh_token"):
            from .mailapi import google_revoke
            google_revoke(current["refresh_token"])
        target.store(None,request.user)
        messages.success(request,"Invio email rimosso"+(": si usa l'SMTP dell'agenzia." if target.tenant else "."))
        return done()
    keep=lambda k,same:(request.POST.get(k) or (current.get(k,"") if method==same else ""))
    smtp_initial={"sender_name":target.name,**{k:v for k,v in current.items() if k!="password"}} if method in ("","smtp") else {"sender_name":current.get("sender_name") or target.name}
    form=SMTPForm(request.POST if action=="save_smtp" else None,initial=smtp_initial)
    google=GoogleForm(request.POST if action=="google_connect" else None,initial={**({k:current.get(k,"") for k in ("client_id","sender","reply_to")} if method=="gmail" else {}),"sender_name":current.get("sender_name") or target.name})
    graph=GraphForm(request.POST if action=="save_graph" else None,initial={**({k:current.get(k,"") for k in ("tenant_id","client_id","mailbox","sender","reply_to")} if method=="graph" else {}),"sender_name":current.get("sender_name") or target.name})
    if action=="save_smtp":
        tab="smtp"
        if form.is_valid():
            try:
                data=form.cleaned_data.copy();data["port"]=int(data["port"]);data["mode"]="ssl" if data["port"]==465 else "starttls";data["method"]="smtp"
                public_addresses(data["host"],data["port"])
                data["password"]=data["password"] or (current.get("password","") if method=="smtp" else "")
                target.store(data,request.user)
                messages.success(request,("SMTP salvato. Usa l'email di prova per controllarlo." if target.tenant else "SMTP dell'agenzia salvato: le email di servizio di tutte le attività senza un SMTP proprio partono da qui."))
                return done()
            except ValueError as ex:form.add_error(None,str(ex))
            except OSError:form.add_error("host","Server SMTP non trovato: controlla il nome, per esempio smtp.tuodominio.it.")
    elif action=="save_graph":
        tab="microsoft"
        if graph.is_valid():
            from .mailapi import graph_token,ApiError
            data={**graph.cleaned_data,"method":"graph"}
            data["client_secret"]=keep("client_secret","graph")
            data["sender"]=data["sender"] or data["mailbox"]
            if not data["client_secret"]:graph.add_error("client_secret","Inserisci il valore del segreto client.")
            else:
                try:
                    graph_token(data)
                    target.store(data,request.user)
                    messages.success(request,"Microsoft 365 collegato: le email partono da "+data["mailbox"]+". Manda un'email di prova per controllare il permesso di invio.")
                    return done()
                except ApiError as ex:graph.add_error(None,str(ex))
    elif action=="google_connect":
        tab="google"
        if google.is_valid():
            from .mailapi import google_auth_url
            data={**google.cleaned_data}
            data["client_secret"]=keep("client_secret","gmail")
            if not data["client_secret"]:google.add_error("client_secret","Inserisci il client secret.")
            else:
                state=secrets.token_urlsafe(24)
                request.session["google_oauth"]={"state":state,"target":target.key,"until":time.time()+900,"pending":protect(data,"oauth-pending")}
                return redirect(google_auth_url(data["client_id"],redirect_uri(),state))
    config=target.effective()
    own=bool(target.tenant.smtp) if target.tenant else bool(target.site.smtp)
    return render(request,"smtp.html",{"form":form,"google":google,"graph":graph,"title":title,"description":description,"providers":PROVIDERS,"tabs":TABS,"tab":tab,
        "method":method,"method_label":METHODS.get(method,""),"current":current,"redirect_uri":redirect_uri(),"target_url":target.url,
        "configured":config is not None,"own":own,"from_env":not target.tenant and not own and config is not None,"from_agency":bool(target.tenant) and not own and config is not None,
        "sender":config and formataddr((config.get("sender_name") or "",config["sender"])),"how":config and describe(config),
        "back":None if target.tenant else "site_settings"})

def google_callback(request):
    """Google comes back here after the consent page, for the agency or for a business."""
    if not request.user.is_authenticated:return redirect("login")
    if not request.user.is_staff:raise PermissionDenied
    pending=request.session.pop("google_oauth",None)
    if not pending or not secrets.compare_digest(str(request.GET.get("state","")),pending.get("state","")) or pending.get("until",0)<time.time():
        messages.error(request,"Collegamento con Google scaduto o non valido: riprova.");return redirect("platform")
    tenant=None
    if pending["target"]!="site":
        tenant=Tenant.objects.filter(slug=pending["target"]).first()
        if tenant is None:raise Http404
    target=Target(tenant)
    if request.GET.get("error"):
        messages.error(request,"Google non ha concesso l'accesso"+(": hai annullato il consenso." if request.GET.get("error")=="access_denied" else ": "+request.GET["error"][:80]+"."))
        return redirect(target.url+"?metodo=google")
    from .mailapi import google_exchange,ApiError
    data=reveal(pending["pending"],"oauth-pending")
    try:got=google_exchange(data["client_id"],data["client_secret"],request.GET.get("code",""),redirect_uri())
    except ApiError as ex:
        messages.error(request,str(ex));return redirect(target.url+"?metodo=google")
    config={"method":"gmail","client_id":data["client_id"],"client_secret":data["client_secret"],"refresh_token":got["refresh_token"],"account":got["account"],
        "sender":data.get("sender") or got["account"],"sender_name":data.get("sender_name",""),"reply_to":data.get("reply_to","")}
    if not config["sender"]:
        messages.error(request,"Google non ha indicato l'indirizzo dell'account: scrivilo in Email del mittente e ricollega.");return redirect(target.url+"?metodo=google")
    target.store(config,request.user)
    messages.success(request,"Google collegato: le email partono da "+(got["account"] or config["sender"])+". Manda un'email di prova.")
    return redirect(target.url)
