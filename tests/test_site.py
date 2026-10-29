"""2.3.0 — portal-wide settings of the agency, and the step-up confirmation instead of a blank 403."""
import io,time as wall
import pyotp,pytest
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from core.models import *
from core.crypto import protect
from core.tenancy import scope
from tests.test_core import sample,client_as
from tests.test_phase2 import staff_client

pytestmark=pytest.mark.django_db

def png(color=(122,85,173),size=(80,80)):
    out=io.BytesIO();Image.new("RGB",size,color).save(out,format="PNG");return SimpleUploadedFile("logo.png",out.getvalue(),content_type="image/png")

def form_data(**extra):
    s=SiteSettings.current()
    data={f:getattr(s,f) for f in ("name","owner_name","brand_color","default_theme","login_title","login_text","support_email","support_phone","default_timezone","default_brand_color","default_privacy_text","default_consent_text","default_retention_days","default_daily_limit","default_hourly_limit","trial_enabled","trial_email","trial_days")}
    if not data["trial_enabled"]:data.pop("trial_enabled")
    data.update(extra);return data

def test_agency_sets_name_logo_favicon_and_texts_once():
    c,u=staff_client()
    r=c.post("/platform/settings/",form_data(name="Prenota TD",login_title="Entra nel portale",support_email="aiuto@example.invalid",logo_file=png(),favicon_file=png((224,86,155))))
    assert r.status_code==302
    s=SiteSettings.current();assert s.name=="Prenota TD" and s.logo and s.favicon
    assert Image.open(io.BytesIO(bytes(s.favicon))).size==(64,64)
    login=Client_anon().get("/login/").content.decode()
    assert "Entra nel portale" in login and "Prenota TD" in login and "aiuto@example.invalid" in login and "/site/favicon.png" in login
    assert Client_anon().get("/site/logo.png").status_code==200

def Client_anon():
    from django.test import Client;return Client()

def test_only_the_agency_changes_them(sample):
    t,o,s=sample;c,u=client_as(t)
    assert c.get("/platform/settings/").status_code==403 and c.post("/platform/settings/",form_data(name="X")).status_code==403
    assert SiteSettings.current().name=="Booking Suite"

def test_default_favicon_and_colour():
    c=Client_anon()
    r=c.get("/site/favicon.png");assert r.status_code==302 and r["Location"].endswith("favicon.svg")
    SiteSettings.objects.update_or_create(pk=1,defaults={"brand_color":"#cc0000"})
    css=c.get("/site/style.css").content.decode()
    assert "--brand:#cc0000" in css and "--pastel:#e88c8c" in css and "--soft:#f9e0e0" in css
    from core.branding import mix
    assert mix("#7a55ad","#ffffff",.55)=="#c3b2da"  # the default accent keeps its old menu highlight, #c5b4d9

def test_new_businesses_start_from_the_defaults():
    SiteSettings.objects.update_or_create(pk=1,defaults={"default_privacy_text":"Informativa dell'agenzia","default_retention_days":365})
    c,u=staff_client();page=c.get("/platform/clienti/nuovo/").content.decode()
    assert "Informativa dell&#x27;agenzia" in page and 'value="365"' in page

def test_agency_smtp_for_service_mail_never_for_campaigns(sample):
    from core.mail import smtp_config
    t,o,s=sample
    assert smtp_config(t) is None
    SiteSettings.objects.update_or_create(pk=1,defaults={"smtp":protect({"host":"smtp.agenzia.invalid","port":587,"mode":"starttls","username":"u","password":"p","sender":"noreply@agenzia.invalid","reply_to":""},"site-smtp")})
    assert smtp_config(t)["host"]=="smtp.agenzia.invalid" and smtp_config(t,marketing=True) is None

def totp_user(c,u):
    secret=pyotp.random_base32();u.totp=protect(secret,"totp:"+str(u.pk));u.save(update_fields=["totp"])
    return secret

def test_stale_confirmation_asks_for_the_code_and_comes_back():
    c,u=staff_client(fresh=False);secret=totp_user(c,u)
    r=c.get("/platform/settings/");assert r.status_code==302 and r["Location"].startswith("/verify/confirm/?next=%2Fplatform%2Fsettings%2F")
    page=c.get(r["Location"]).content.decode();assert "Conferma che sei tu" in page
    bad=c.post("/verify/confirm/",{"code":"000000","next":"/platform/settings/"});assert "Codice non valido" in bad.content.decode()
    good=c.post("/verify/confirm/",{"code":pyotp.TOTP(secret).now(),"next":"/platform/settings/"})
    assert good.status_code==302 and good["Location"]=="/platform/settings/"
    assert c.get("/platform/settings/").status_code==200

def test_confirmation_never_redirects_elsewhere():
    c,u=staff_client(fresh=False);secret=totp_user(c,u)
    r=c.post("/verify/confirm/",{"code":pyotp.TOTP(secret).now(),"next":"//evil.example/x"})
    assert r["Location"]=="/"

def test_members_page_asks_before_the_form(sample):
    t,o,s=sample;c,u=client_as(t,staff=True)
    sess=c.session;sess["verified_at"]=wall.time()-3600;sess.save()
    r=c.get(f"/t/{t.slug}/members/");assert r.status_code==302 and "/verify/confirm/" in r["Location"]

def test_forbidden_page_explains(sample):
    t,o,s=sample
    from tests.test_phase2 import member
    c,u=member(t,"viewer")
    r=c.get(f"/t/{t.slug}/members/");page=r.content.decode()
    assert r.status_code==403 and "Questa pagina non è disponibile per te" in page and "Chi vede cosa" in page

def test_booking_page_asks_only_what_is_needed(sample):
    from django.test import Client
    from core.public import first_step
    t,o,s=sample
    assert first_step({"services":[{"name":"Tavolo","location":"Centro"}]})=="none"
    assert first_step({"services":[{"name":"Tavolo","location":"Centro"},{"name":"Tavolo","location":"Mare"}]})=="location"
    assert first_step({"services":[{"name":"Taglio","location":"Centro"},{"name":"Colore","location":"Centro"}]})=="service"
    page=Client().get(f"/book/{t.slug}/").content.decode()
    assert "Scegli giorno e orario" in page and "1. Servizio" not in page

# 2.3.2 — the WordPress connector downloadable by the agency, Cloud edition only
def test_agency_downloads_the_wordpress_plugin(sample):
    import zipfile
    t,o,s=sample;c,u=staff_client()
    page=c.get("/platform/plugin/").content.decode()
    assert "Plugin WordPress" in page and t.slug in page and "/platform/plugin/download/" in page
    r=c.get("/platform/plugin/download/")
    version=(Path_root()/"VERSION").read_text().strip()
    assert r.status_code==200 and r["Content-Type"]=="application/zip" and f"td-booking-suite-{version}-wordpress.zip" in r["Content-Disposition"]
    z=zipfile.ZipFile(io.BytesIO(r.content))
    names=set(z.namelist());assert {"td-booking-suite/td-booking-suite.php","td-booking-suite/readme.txt","td-booking-suite/widget.js"}<=names
    assert f"Version: {version}" in z.read("td-booking-suite/td-booking-suite.php").decode()
    assert all(not n.startswith("/") and ".." not in n for n in names)

def Path_root():
    from django.conf import settings;return settings.BASE_DIR

def test_plugin_only_for_the_agency(sample):
    t,o,s=sample;c,u=client_as(t)
    assert c.get("/platform/plugin/download/").status_code==403
    assert "Plugin WordPress" not in c.get(f"/t/{t.slug}/").content.decode()

def test_no_plugin_page_in_the_private_edition(settings):
    settings.EDITION="private"
    c,u=staff_client()
    assert c.get("/platform/plugin/download/").status_code==404 and c.get("/platform/plugin/").status_code==404

def test_portal_default_theme_until_each_person_picks_one(sample):
    """2.8.1: the agency picks light, dark or automatic for everybody; public booking pages keep following the visitor."""
    t,o,s=sample
    c,u=staff_client()
    assert c.post("/platform/settings/",form_data(default_theme="dark")).status_code==302
    assert SiteSettings.current().default_theme=="dark"
    assert '<html lang="it" data-theme="dark">' in c.get("/platform/").content.decode()
    assert '<html lang="it" data-theme="dark">' in Client_anon().get("/login/").content.decode()
    assert "Tema di partenza" in c.get("/platform/settings/").content.decode()
    assert '<html lang="it" data-theme="auto">' in Client_anon().get(f"/book/{t.slug}/").content.decode()
