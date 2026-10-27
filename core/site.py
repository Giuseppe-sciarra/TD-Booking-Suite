"""Portal-wide settings of the agency: name, logo, favicon, colour, login texts, support contacts,
agency SMTP and the defaults every new business starts with."""
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render,redirect
from django.views.decorators.http import require_GET
from .models import SiteSettings
from .crypto import protect,reveal
from email.utils import formataddr
from .branding import clean_logo,contrast_color

def current(request=None):
    if request is not None:
        cached=getattr(request,"_site",None)
        if cached is None:cached=request._site=SiteSettings.current()
        return cached
    return SiteSettings.current()

def agency_smtp(site=None):
    site=site or SiteSettings.current()
    if site.smtp:
        config=reveal(site.smtp,"site-smtp")
        config["sender_name"]=config.get("sender_name") or site.name
        return config
    if settings.EMAIL_HOST and settings.DEFAULT_FROM_EMAIL:
        return {"host":settings.EMAIL_HOST,"port":settings.EMAIL_PORT,"mode":"starttls","username":settings.EMAIL_HOST_USER,"password":settings.EMAIL_HOST_PASSWORD,"sender":settings.DEFAULT_FROM_EMAIL,"reply_to":settings.DEFAULT_FROM_EMAIL,"sender_name":site.name}
    return None

def mail_ready(tenant=None):
    return bool(tenant is not None and tenant.smtp) or agency_smtp() is not None

def tenant_defaults():
    s=SiteSettings.current()
    return {"timezone":s.default_timezone,"brand_color":s.default_brand_color,"privacy_text":s.default_privacy_text,"consent_text":s.default_consent_text,
            "retention_days":s.default_retention_days,"smtp_daily_limit":s.default_daily_limit,"smtp_hourly_limit":s.default_hourly_limit,"status":"active"}

def clean_icon(upload):
    """Favicon: same checks as the logos, stored as a 64 px PNG."""
    import io
    from PIL import Image
    data=clean_logo(upload)
    im=Image.open(io.BytesIO(data));im.thumbnail((64,64))
    out=io.BytesIO();im.save(out,format="PNG");return out.getvalue()

def clean_brand(upload):
    """2.5.0: the portal logo keeps its shape, wide wordmarks included (up to 800 x 300 px)."""
    import io
    from PIL import Image,UnidentifiedImageError
    data=upload.read(1024*1024+1)
    if len(data)>1024*1024:raise forms.ValidationError("Logo massimo 1 MB.")
    try:
        im=Image.open(io.BytesIO(data))
        if im.format not in ("PNG","JPEG","WEBP") or im.width*im.height>8000000:raise ValueError()
        im.load();im=im.convert("RGBA")
        box=im.getchannel("A").getbbox()  # empty transparent margins would make the logo look tiny
        if box:im=im.crop(box)
        im.thumbnail((800,300))
        out=io.BytesIO();im.save(out,format="PNG");return out.getvalue(),round(im.width/max(im.height,1),3)
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):raise forms.ValidationError("Usa un'immagine PNG, JPEG o WebP valida.")

COLOR=forms.TextInput(attrs={"type":"color"})
class SiteForm(forms.ModelForm):
    logo_file=forms.FileField(required=False,label="Logo per sfondi chiari: PNG trasparente, JPEG o WebP, massimo 1 MB")
    logo_dark_file=forms.FileField(required=False,label="Logo per sfondi scuri (menu laterale, pagina di accesso, tema scuro)")
    remove_logo_dark=forms.BooleanField(required=False,label="Togli il logo per sfondi scuri")
    favicon_file=forms.FileField(required=False,label="Icona del browser (favicon): PNG, quadrata")
    remove_logo=forms.BooleanField(required=False,label="Togli il logo e usa il marchio TD")
    remove_favicon=forms.BooleanField(required=False,label="Togli l'icona e usa quella TD")
    class Meta:
        model=SiteSettings
        fields=["name","owner_name","owner_url","brand_color","login_title","login_text","support_email","support_phone","trial_enabled","trial_email","trial_days",
                "legal_name","vat_number","legal_address","privacy_email","data_location","privacy_policy",
                "default_timezone","default_brand_color","default_privacy_text","default_consent_text","default_retention_days","default_daily_limit","default_hourly_limit"]
        labels={"name":"Nome del portale","owner_name":"Chi lo offre (in fondo alle pagine)","owner_url":"Sito di chi lo offre","brand_color":"Colore principale del portale","login_title":"Titolo della pagina di accesso","login_text":"Testo sotto il titolo",
                "support_email":"Email di assistenza","support_phone":"Telefono di assistenza",
                "legal_name":"Ragione sociale","vat_number":"Partita IVA","legal_address":"Sede legale","privacy_email":"Email per le richieste sulla privacy",
                "data_location":"Dove sono i server, per esempio Roma, Italia","privacy_policy":"Testo della privacy policy (vuoto: testo standard compilato con i dati qui sopra)",
                "trial_enabled":"Mostra \"Prova il servizio\" nella pagina di accesso","trial_email":"Email che riceve le richieste di prova (vuota: quella di assistenza)","trial_days":"Durata della prova in giorni","default_timezone":"Fuso orario","default_brand_color":"Colore della pagina prenotazioni",
                "default_privacy_text":"Informativa prenotazioni","default_consent_text":"Testo consenso marketing","default_retention_days":"Conservazione prenotazioni (giorni)",
                "default_daily_limit":"Limite email al giorno","default_hourly_limit":"Limite email all'ora"}
        widgets={"privacy_policy":forms.Textarea(attrs={"rows":10,"placeholder":"## Titolo di sezione\nTesto del paragrafo\n- voce di elenco"}),"brand_color":COLOR,"default_brand_color":COLOR,"login_text":forms.Textarea(attrs={"rows":2}),"default_privacy_text":forms.Textarea(attrs={"rows":3}),"default_consent_text":forms.Textarea(attrs={"rows":3})}
    def __init__(self,*a,**kw):
        kw.setdefault("label_suffix","");super().__init__(*a,**kw)
        if settings.EDITION!="cloud":
            for name in ("trial_enabled","trial_email","trial_days"):self.fields.pop(name,None)
    def clean_logo_file(self):
        f=self.cleaned_data.get("logo_file");return clean_brand(f) if f else None
    def clean_logo_dark_file(self):
        f=self.cleaned_data.get("logo_dark_file");return clean_brand(f) if f else None
    def clean_favicon_file(self):
        f=self.cleaned_data.get("favicon_file");return clean_icon(f) if f else None
    def _color(self,name):
        import re
        value=self.cleaned_data[name]
        if not re.fullmatch(r"#[0-9a-fA-F]{6}",value or ""):raise forms.ValidationError("Colore non valido, per esempio #7a55ad.")
        return value
    # an invalid colour would break the style sheet of every page
    def clean_brand_color(self):return self._color("brand_color")
    def clean_default_brand_color(self):return self._color("default_brand_color")
    def clean_trial_days(self):
        value=self.cleaned_data["trial_days"]
        if not 1<=value<=90:raise forms.ValidationError("Da 1 a 90 giorni.")
        return value
    def clean_default_timezone(self):
        from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
        value=self.cleaned_data["default_timezone"]
        try:ZoneInfo(value)
        except (ZoneInfoNotFoundError,ValueError):raise forms.ValidationError("Fuso orario non valido, per esempio Europe/Rome.")
        return value
    def save(self,commit=True):
        obj=super().save(commit=False)
        if self.cleaned_data.get("remove_logo"):obj.logo=None;obj.logo_ratio=1
        elif self.cleaned_data.get("logo_file"):obj.logo,obj.logo_ratio=self.cleaned_data["logo_file"]
        if self.cleaned_data.get("remove_logo_dark"):obj.logo_dark=None;obj.logo_dark_ratio=1
        elif self.cleaned_data.get("logo_dark_file"):obj.logo_dark,obj.logo_dark_ratio=self.cleaned_data["logo_dark_file"]
        if {"legal_name","vat_number","legal_address","privacy_email","data_location","privacy_policy"}&set(self.changed_data):
            from django.utils import timezone
            obj.privacy_updated=timezone.localdate()
        if self.cleaned_data.get("remove_favicon"):obj.favicon=None
        elif self.cleaned_data.get("favicon_file"):obj.favicon=self.cleaned_data["favicon_file"]
        if commit:obj.save()
        return obj

GROUPS=[("Il portale",["name","owner_name","owner_url","logo_file","logo_dark_file","remove_logo","remove_logo_dark","favicon_file","remove_favicon","brand_color"]),
        ("Pagina di accesso e assistenza",["login_title","login_text","support_email","support_phone"]),
        ("Richieste di prova",["trial_enabled","trial_email","trial_days"]),
        ("Dati dell'azienda e privacy",["legal_name","vat_number","legal_address","privacy_email","data_location","privacy_policy"]),
        ("Ogni nuova attività parte con",["default_timezone","default_brand_color","default_privacy_text","default_consent_text","default_retention_days","default_daily_limit","default_hourly_limit"])]

def staff_only(request):
    if not request.user.is_staff:raise PermissionDenied
def fresh_check(request):
    from .identity import fresh
    fresh(request)

def settings_view(request):
    from .identity import fresh
    from .models import audit
    staff_only(request);fresh_check(request);site=SiteSettings.current()
    form=SiteForm(request.POST or None,request.FILES or None,instance=site)
    if request.method=="POST":
        fresh(request)
        if form.is_valid():
            with transaction.atomic():form.save()
            messages.success(request,"Impostazioni generali salvate.")
            return redirect("site_settings")
    groups=[(title,[form[n] for n in names if n in form.fields]) for title,names in GROUPS if settings.EDITION=="cloud" or title!="Richieste di prova"]
    return render(request,"site_settings.html",{"form":form,"groups":groups,"site_obj":site,"smtp":agency_smtp(site),"smtp_from_env":not site.smtp and bool(settings.EMAIL_HOST)})

# 2.6.0: common providers, the page fills server and port and shows what to use as user and password
PROVIDERS=[
    ("gmail","Gmail o Google Workspace","smtp.gmail.com","587","Utente: l'indirizzo Gmail completo. Password: una password per le app, da Account Google, Sicurezza, Verifica in due passaggi, Password per le app. Mittente: lo stesso indirizzo o un alias aggiunto in Gmail, Invia messaggio come"),
    ("microsoft","Microsoft 365 o Outlook","smtp.office365.com","587","Utente: l'indirizzo completo. L'amministratore deve abilitare l'SMTP autenticato per la casella. Mittente: la stessa casella o una con il permesso Invia come"),
    ("aruba","Aruba","smtps.aruba.it","465","Utente e password della casella Aruba. Mittente: un indirizzo dello stesso dominio"),
    ("register","Register.it","authsmtp.securemail.pro","465","Utente: la casella SMTP autenticata, per esempio smtp@tuodominio.it. Mittente: qualunque indirizzo del dominio, per esempio info@"),
    ("ovh","OVHcloud","ssl0.ovh.net","465","Utente e password della casella OVH. Mittente: un indirizzo dello stesso dominio"),
    ("brevo","Brevo","smtp-relay.brevo.com","587","Utente e chiave SMTP dalla sezione SMTP e API di Brevo. Mittente: un indirizzo verificato in Brevo"),
    ("other","Altro","","","Chiedi a chi gestisce le email del dominio server, porta, utente e password SMTP"),
]
def smtp_view(request):
    from .mailsetup import page,Target
    staff_only(request)
    return page(request,Target(),"Invio email dell'agenzia","Serve per inviti, conferme, notifiche e richieste di prova, per tutte le attività che non hanno un invio proprio. Le campagne marketing usano sempre e solo l'invio dell'attività",saved_to="/platform/settings/")

def _image(data):
    if not data:return HttpResponse(status=404)
    r=HttpResponse(bytes(data),content_type="image/png");r["X-Content-Type-Options"]="nosniff";r["Cache-Control"]="public, max-age=86400"
    return r
@require_GET
def logo(request):return _image(SiteSettings.current().logo)
def logo_dark(request):
    s=SiteSettings.current();return _image(s.logo_dark or s.logo)
@require_GET
def favicon(request):
    site=SiteSettings.current()
    if site.favicon:return _image(site.favicon)
    return redirect(settings.STATIC_URL+"favicon.svg")
@require_GET
def css(request):
    color=SiteSettings.current().brand_color
    light=contrast_color(color);dark=contrast_color(color,True)
    body=(f":root,:root[data-theme=light]{{--brand:{light};--violet:{color}}}"
          f":root[data-theme=dark]{{--brand:{dark};--violet:{color}}}"
          f"@media(prefers-color-scheme:dark){{:root:not([data-theme=light]){{--brand:{dark};--violet:{color}}}}}")
    r=HttpResponse(body,content_type="text/css");r["Cache-Control"]="public, max-age=86400";return r

# The WordPress connector, downloadable by the agency (Cloud edition only: in Private the
# business installs what it receives with its own package).
def plugin_allowed(request):
    from django.http import Http404
    if settings.EDITION!="cloud":raise Http404
    staff_only(request)

def plugin_version():
    return settings.VERSION

def plugin_zip():
    import io,zipfile
    root=settings.BASE_DIR/"wordpress"/"td-booking-suite"
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("td-booking-suite/"),b"")
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                info=zipfile.ZipInfo("td-booking-suite/"+path.relative_to(root).as_posix())
                info.external_attr=0o644<<16;info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,path.read_bytes())
    return out.getvalue()

def plugin_view(request):
    from .models import Tenant
    plugin_allowed(request)
    tenants=list(Tenant.objects.order_by("name"))
    return render(request,"plugin.html",{"version":plugin_version(),"origin":settings.PUBLIC_ORIGIN,"tenants":tenants})

def plugin_download(request):
    plugin_allowed(request)
    r=HttpResponse(plugin_zip(),content_type="application/zip")
    r["Content-Disposition"]=f'attachment; filename="td-booking-suite-{plugin_version()}-wordpress.zip"'
    return r
