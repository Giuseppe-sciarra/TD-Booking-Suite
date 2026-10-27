"""2.4.0: agency dashboard, client list and client card; a second factor that can wait."""
import re,time as wallclock
from datetime import timedelta
import pyotp,pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import protect
from core.booking import create_booking
from core.agency import load_profile,usage,attention,bars
from tests.test_core import sample,payload,client_as
from tests.test_phase2 import staff_client,member
from tests.test_site import png

pytestmark=pytest.mark.django_db
PASSWORD="A-test-password-12345"

def card(c,slug,**data):
    return c.post(f"/platform/clienti/{slug}/",data,follow=True)

# the agency's view
def test_agency_pages_are_for_the_agency_only(sample):
    t,o,s=sample
    for role in ("owner","manager","operator"):
        c,u=member(t,role)
        for path in ("/platform/","/platform/clienti/","/platform/clienti/nuovo/",f"/platform/clienti/{t.slug}/"):
            assert c.get(path).status_code==403,(role,path)
    sc,su=staff_client()
    nav=sc.get("/platform/").content.decode()
    assert ">Dashboard</a>" in nav and ">Clienti</a>" in nav and "Utilizzo per cliente" in nav

def test_dashboard_counts_usage_and_flags_what_needs_attention(sample):
    t,o,s=sample
    with scope(t):
        for h in (10,11,12):create_booking(t,payload(s,hour=h))
    o.status="trial";o.trial_until=timezone.localdate()+timedelta(days=3);o.save()
    c,u=staff_client();page=c.get("/platform/").content.decode()
    assert "<span>In arrivo</span><strong>3</strong>" in page
    assert "Prova in scadenza tra 3 giorni" in page and "Nessuno ha ancora fatto accesso" in page
    assert "Mario" not in page and "mario@example.invalid" not in page  # counts only
    u2=usage(t);assert u2["bookings_30"]==3 and u2["weeks"][-1]==3 and sum(u2["weeks"])==3

def test_charts_use_plain_decimal_points(sample):
    t,o,s=sample
    with scope(t):create_booking(t,payload(s))
    c,u=staff_client()
    for path in ("/platform/",f"/platform/clienti/{t.slug}/","/platform/clienti/"):
        page=c.get(path).content.decode()
        attrs=re.findall(r'(?:x|y|width|height|points)="([^"]*)"',page)
        assert attrs and not [a for a in attrs if re.search(r"\d,\d",a) and " " not in a],path
    chart=bars([0,2,5],timezone.localdate())
    assert all("," not in b["x"] for b in chart["bars"])

def test_list_has_grid_and_list_with_search_data(sample):
    t,o,s=sample;c,u=staff_client()
    card(c,t.slug,action="contact",name="Giulia Bianchi",role="Titolare",phone="+39 06 1234567",primary="on")
    card(c,t.slug,action="profile",company="Salone Bianchi Srl",city="Roma",sector="Parrucchiere")
    page=c.get("/platform/clienti/").content.decode()
    assert 'data-pane="grid"' in page and 'data-pane="list"' in page and 'id="client-search"' in page
    assert "Giulia Bianchi" in page and "Parrucchiere, Roma" in page
    assert 'data-search="salone test salone salone bianchi srl roma parrucchiere giulia bianchi"' in page

# the client card
def test_profile_is_sealed_and_never_reaches_the_client(sample):
    t,o,s=sample;c,u=staff_client()
    r=card(c,t.slug,action="profile",company="Salone Bianchi Srl",vat="01234567890",pec="salone@pec.invalid",city="Roma",website="https://salone.example",notes="Paga a 30 giorni")
    assert r.status_code==200 and "Anagrafica salvata" in r.content.decode()
    with scope(t):raw=ClientProfile.objects.get(tenant=t).data
    assert "Bianchi" not in raw and "01234567890" not in raw and "30 giorni" not in raw
    assert load_profile(t)["vat"]=="01234567890"
    t.refresh_from_db();assert t.website=="https://salone.example"
    owner,_=client_as(t)
    for path in (f"/t/{t.slug}/",f"/t/{t.slug}/audit/",f"/t/{t.slug}/setup/"):
        assert "Bianchi Srl" not in owner.get(path).content.decode()
    with scope(o):assert not ClientProfile.objects.exists()

def test_profile_rejects_bad_values(sample):
    t,o,s=sample;c,u=staff_client()
    assert "Inserisci un indirizzo email valido" in card(c,t.slug,action="profile",email="non-una-email").content.decode()
    assert "Cliente dal: data non valida" in card(c,t.slug,action="profile",since="31/02/2026").content.decode()
    assert "massimo 7 caratteri" in card(c,t.slug,action="profile",sdi="12345678").content.decode()
    assert load_profile(t)["email"]==""

def test_contacts_add_edit_primary_remove(sample):
    t,o,s=sample;c,u=staff_client()
    card(c,t.slug,action="contact",name="Anna Verdi",role="Titolare",email="anna@example.invalid")
    card(c,t.slug,action="contact",name="Luca Neri",role="Marketing",primary="on")
    contacts=load_profile(t)["contacts"]
    assert [x["name"] for x in contacts]==["Anna Verdi","Luca Neri"] and [x["primary"] for x in contacts]==[False,True]
    card(c,t.slug,action="contact",index="0",name="Anna Verdi",role="Amministrazione",primary="on")
    contacts=load_profile(t)["contacts"]
    assert contacts[0]["role"]=="Amministrazione" and [x["primary"] for x in contacts]==[True,False]
    card(c,t.slug,action="contact_remove",index="0")
    contacts=load_profile(t)["contacts"];assert [x["name"] for x in contacts]==["Luca Neri"] and contacts[0]["primary"]
    assert "Indica il nome del referente" in card(c,t.slug,action="contact",name="").content.decode()
    assert "Referente non trovato" in card(c,t.slug,action="contact_remove",index="9").content.decode()
    page=c.get(f"/platform/clienti/{t.slug}/").content.decode()
    assert "Luca Neri" in page and "Principale" in page and "Anna Verdi" not in page

def test_logo_from_the_card(sample):
    t,o,s=sample;c,u=staff_client()
    r=c.post(f"/platform/clienti/{t.slug}/",{"action":"logo","logo":png()},follow=True)
    assert "Logo aggiornato" in r.content.decode()
    t.refresh_from_db();assert t.logo
    assert Client().get(f"/brand/{t.slug}/logo.png")["Content-Type"]=="image/png"
    assert f'src="/brand/{t.slug}/logo.png"' in c.get("/platform/clienti/").content.decode()
    card(c,t.slug,action="logo_remove");t.refresh_from_db();assert not t.logo

def test_new_client_with_its_first_contact():
    c,u=staff_client()
    data={"name":"Pizzeria Nuova","slug":"pizzeria-nuova","timezone":"Europe/Rome","status":"active","smtp_daily_limit":200,"smtp_hourly_limit":50,
          "brand_color":"#7a55ad","retention_days":730,"privacy_text":"Informativa","consent_text":"Consenso","email":"titolare@example.invalid",
          "contact_name":"Marco Esposito","contact_phone":"+39 333 0000000"}
    page=c.post("/platform/clienti/nuovo/",data,follow=True).content.decode()
    t=Tenant.objects.get(slug="pizzeria-nuova")
    assert "/platform/clienti/pizzeria-nuova/" in page and "/invite/" in page
    contact=load_profile(t)["contacts"][0]
    assert contact=={"name":"Marco Esposito","role":"Titolare","email":"titolare@example.invalid","phone":"+39 333 0000000","notes":"","primary":True}

def test_client_card_shows_users_and_their_second_factor(sample):
    t,o,s=sample;owner,ou=client_as(t)
    c,u=staff_client();page=c.get(f"/platform/clienti/{t.slug}/").content.decode()
    assert ou.email in page and "Solo password" in page and "Impostazioni e sito" in page

def test_client_profile_table_is_tenant_bound():
    from core import rls
    assert "core_clientprofile" in rls.tenant_tables()

# 2.4.0: Authenticator and passkey are optional
def login(c,email,password=PASSWORD):
    return c.post("/login/",{"email":email,"password":password})

def test_password_only_goes_straight_in_with_a_reminder(sample):
    t,o,s=sample
    u=User.objects.create_user("nuovo@example.invalid",PASSWORD);Membership.objects.create(tenant=t,user=u,role="owner")
    c=Client();r=login(c,"nuovo@example.invalid")
    assert r.status_code==302 and r["Location"]=="/"
    page=c.get(f"/t/{t.slug}/").content.decode()
    assert "Proteggi il tuo account" in page and 'href="/security/">Attiva ora' in page
    assert "Proteggi il tuo account" not in c.get("/security/").content.decode()

def test_staff_without_second_factor_confirms_with_the_password():
    u=User.objects.create_user("agenzia@example.invalid",PASSWORD,is_staff=True)
    c=Client();login(c,"agenzia@example.invalid")
    assert c.get("/platform/").status_code==200
    s=c.session;s["verified_at"]=wallclock.time()-3600;s.save()
    r=c.get("/platform/clienti/");assert r.status_code==302 and r["Location"].startswith("/verify/confirm/")
    page=c.get(r["Location"]).content.decode();assert 'name="password"' in page and 'name="code"' not in page
    assert "Password non valida" in c.post("/verify/confirm/",{"password":"sbagliata","next":"/platform/clienti/"}).content.decode()
    ok=c.post("/verify/confirm/",{"password":PASSWORD,"next":"/platform/clienti/"})
    assert ok.status_code==302 and ok["Location"]=="/platform/clienti/" and c.get("/platform/clienti/").status_code==200

def test_with_authenticator_the_code_is_still_required(sample):
    t,o,s=sample
    u=User.objects.create_user("protetto@example.invalid",PASSWORD);Membership.objects.create(tenant=t,user=u,role="owner")
    seed=pyotp.random_base32();u.totp=protect(seed,"totp:"+str(u.pk));u.save()
    c=Client();r=login(c,"protetto@example.invalid")
    assert r["Location"]=="/verify/" and c.get(f"/t/{t.slug}/").status_code==302
    c.post("/verify/",{"code":pyotp.TOTP(seed).now()})
    page=c.get(f"/t/{t.slug}/").content.decode()
    assert "Salone Test" in page and "Proteggi il tuo account" not in page

def test_sessions_opened_before_the_change_are_let_in(sample):
    t,o,s=sample
    u=User.objects.create_user("vecchia@example.invalid",PASSWORD);Membership.objects.create(tenant=t,user=u,role="owner")
    c=Client();c.force_login(u,backend="core.identity.EmailBackend")
    sess=c.session;sess["auth_version"]=u.auth_version;sess["last_seen"]=wallclock.time();sess["verified_at"]=wallclock.time();sess.save()
    assert c.get(f"/t/{t.slug}/").status_code==200

# 2.4.0: the business dashboard, for every role within what it may see
from tests.test_phase2 import two_rooms,resource_service
from core.booking import change_status

def test_business_dashboard_counts_bookings_and_cancellations(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    with scope(t):
        extra,_=create_booking(t,payload(svc,hour=12,resource=str(res.pk)))
        change_status(t,extra,"cancelled")
    c,u=client_as(t);page=c.get(f"/t/{t.slug}/").content.decode()
    assert "<h1>Dashboard</h1>" in page and ">Dashboard</a>" in page
    assert "<span>Ricevute in 30 giorni</span><strong>3</strong>" in page
    assert "<span>Disdette in 30 giorni</span><strong>1</strong><small>33% delle ricevute" in page
    assert "<span>Prossimi 7 giorni</span><strong>2</strong>" in page
    assert "Servizi più richiesti" in page and f"<span>{svc.name}</span><strong>2</strong>" in page
    assert "Come sono andate" in page and "nella rubrica" in page

def test_operator_dashboard_counts_only_his_bookings(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,u=member(t,"operator",resources=[str(res.pk)])
    page=c.get(f"/t/{t.slug}/").content.decode()
    assert "<span>Ricevute in 30 giorni</span><strong>1</strong>" in page and "<span>Prossimi 7 giorni</span><strong>1</strong>" in page
    assert "nella rubrica" not in page and "Configura l&#x27;attività" not in page
    assert "Anna" not in page

def test_marketing_dashboard_has_no_booking_numbers(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,u=member(t,"marketing")
    page=c.get(f"/t/{t.slug}/").content.decode()
    assert "Ricevute in 30 giorni" not in page and "Disdette" not in page and "nella rubrica" in page

def test_login_page_animated_even_with_reduced_motion():  # 2.7.0: Windows with animations off no longer freezes the login
    page=Client().get("/login/").content.decode()
    assert 'data-reveal="#password"' in page and 'id="passkey-login"' in page and "login-stage" in page
    assert page.index('name="password"')<page.index('id="passkey-login"')  # password first, passkey right below
    css=open("static/app.css").read();js=open("static/app.js").read()
    assert "reduced-motion" not in css and "reduced-motion" not in js and "style=" not in page

# 2.4.0: "Non sei ancora cliente? Prova il servizio"
from core.models import TrialRequest
from core import trial as trial_module
TRIAL={"first_name":"Lina","last_name":"Ferri","email":"lina@example.invalid","phone":"+39 333 1112223","business":"Trattoria da Lina","preset":"ristorante","message":"Due sale","privacy":"on"}
def preset_key():
    from core.presets import PRESETS
    return "ristorante" if "ristorante" in PRESETS else next(iter(PRESETS))

def test_login_offers_the_trial_only_when_enabled(settings):
    page=Client().get("/login/").content.decode()
    assert "Non sei ancora cliente?" in page and 'href="/prova/"' in page and 'id="trial-box"' in page
    SiteSettings.objects.update_or_create(pk=1,defaults={"trial_enabled":False})
    assert "Non sei ancora cliente?" not in Client().get("/login/").content.decode() and Client().get("/prova/").status_code==404
    SiteSettings.objects.update_or_create(pk=1,defaults={"trial_enabled":True})
    settings.EDITION="private"
    assert Client().get("/prova/").status_code==404 and "Non sei ancora cliente?" not in Client().get("/login/").content.decode()

def test_trial_request_is_stored_sealed():
    data={**TRIAL,"preset":preset_key()}
    page=Client().post("/prova/",data).content.decode()
    assert "Richiesta inviata" in page
    r=TrialRequest.objects.get()
    assert r.status=="new" and r.preset==preset_key() and "Lina" not in r.pii and "lina@example" not in r.pii
    assert r.data["business"]=="Trattoria da Lina" and r.data["privacy_at"]

def test_trial_request_checks_and_bots():
    c=Client()
    assert "Serve il consenso" in c.post("/prova/",{**TRIAL,"preset":"","privacy":""}).content.decode()
    assert "Tipo di attività non valido" in c.post("/prova/",{**TRIAL,"preset":"astronave"}).content.decode()
    assert "Indica nome" in c.post("/prova/",{**TRIAL,"preset":"","first_name":""}).content.decode()
    assert 'value="lina@example.invalid"' in c.post("/prova/",{**TRIAL,"preset":"","first_name":""}).content.decode()  # nothing to type again
    assert "Richiesta inviata" in c.post("/prova/",{**TRIAL,"preset":"","website":"http://spam.invalid"}).content.decode()
    assert not TrialRequest.objects.exists()

def test_trial_requests_are_rate_limited():
    c=Client()
    for _ in range(5):c.post("/prova/",{**TRIAL,"preset":""})
    assert c.post("/prova/",{**TRIAL,"preset":""}).status_code==403 and TrialRequest.objects.count()==5

def test_agency_is_told_by_email(monkeypatch):
    sent=[]
    monkeypatch.setattr("core.mail.send_message",lambda config,payload,mid:sent.append((config,payload)))
    Client().post("/prova/",{**TRIAL,"preset":preset_key()})
    assert trial_module.notify_pending()==0  # no SMTP yet: the request waits, nothing is lost
    SiteSettings.objects.update_or_create(pk=1,defaults={"smtp":protect({"host":"smtp.agenzia.invalid","port":587,"mode":"starttls","username":"u","password":"p","sender":"info@agenzia.invalid","reply_to":""},"site-smtp"),
        "support_email":"assistenza@agenzia.invalid","trial_email":"vendite@agenzia.invalid","name":"Booking Suite TD"})
    assert trial_module.notify_pending()==1 and trial_module.notify_pending()==0
    config,payload=sent[0]
    assert payload["to"]=="vendite@agenzia.invalid" and "Trattoria da Lina" in payload["subject"] and "lina@example.invalid" in payload["body"]
    assert config["sender_name"]=="Booking Suite TD" and TrialRequest.objects.get().notified

def test_activate_a_request_into_a_client():
    Client().post("/prova/",{**TRIAL,"preset":preset_key()});r=TrialRequest.objects.get()
    c,u=staff_client()
    listing=c.get("/platform/richieste/").content.decode()
    assert "Trattoria da Lina" in listing and f"?richiesta={r.pk}" in listing
    assert "Richieste di prova" in c.get("/platform/").content.decode()
    form=c.get(f"/platform/clienti/nuovo/?richiesta={r.pk}").content.decode()
    assert 'value="Trattoria da Lina"' in form and 'value="trattoria-da-lina"' in form and 'value="lina@example.invalid"' in form
    assert f'value="{preset_key()}" selected' in form and 'value="Lina Ferri"' in form and '<option value="trial" selected>' in form
    data={"name":"Trattoria da Lina","slug":"trattoria-da-lina","timezone":"Europe/Rome","status":"trial","trial_until":(timezone.localdate()+timedelta(days=14)).isoformat(),
          "smtp_daily_limit":200,"smtp_hourly_limit":50,"brand_color":"#7a55ad","retention_days":730,"privacy_text":"Informativa","consent_text":"Consenso",
          "email":"lina@example.invalid","preset":preset_key(),"contact_name":"Lina Ferri","contact_phone":"+39 333 1112223","richiesta":str(r.pk)}
    page=c.post("/platform/clienti/nuovo/",data).content.decode()
    t=Tenant.objects.get(slug="trattoria-da-lina");r.refresh_from_db()
    assert r.status=="done" and r.client==t and t.status=="trial" and "/platform/clienti/trattoria-da-lina/" in page
    assert load_profile(t)["contacts"][0]["name"]=="Lina Ferri"
    from core.presets import PRESETS
    with scope(t):assert Service.objects.filter(name=PRESETS[preset_key()]["label"].split(" / ")[0]).exists()

def test_requests_are_agency_only_and_can_be_dropped(sample):
    t,o,s=sample
    Client().post("/prova/",{**TRIAL,"preset":""});r=TrialRequest.objects.get()
    owner,_=client_as(t)
    assert owner.get("/platform/richieste/").status_code==403 and owner.post("/platform/richieste/",{"request":r.pk,"action":"delete"}).status_code==403
    c,u=staff_client()
    c.post("/platform/richieste/",{"request":r.pk,"action":"archive"});r.refresh_from_db();assert r.status=="archived"
    c.post("/platform/richieste/",{"request":r.pk,"action":"delete"});assert not TrialRequest.objects.exists()

# 2.4.0: sender name and test email
class FakeSMTP:
    sent=[]
    def __init__(self,*a,**kw):pass
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def ehlo(self):pass
    def starttls(self,**kw):pass
    def login(self,u,p):pass
    envelopes=[]
    def send_message(self,m,from_addr=None):FakeSMTP.sent.append(m);FakeSMTP.envelopes.append(from_addr);return {}

def test_sender_name_and_test_email(monkeypatch):
    from core import mail
    monkeypatch.setattr(mail,"PinnedSMTP",FakeSMTP);monkeypatch.setattr(mail,"public_addresses",lambda h,p:["192.0.2.1"]);FakeSMTP.sent=[]
    c,u=staff_client()
    page=c.get("/platform/smtp/").content.decode()
    assert "Nome del mittente" in page and "Salva prima la configurazione" in page
    SiteSettings.objects.update_or_create(pk=1,defaults={"smtp":protect({"host":"smtp.agenzia.invalid","port":587,"mode":"starttls","username":"u","password":"p","sender":"info@agenzia.invalid","reply_to":"","sender_name":"Booking Suite TD"},"site-smtp")})
    page=c.get("/platform/smtp/").content.decode()
    assert "Booking Suite TD &lt;info@agenzia.invalid&gt;" in page
    r=c.post("/platform/smtp/",{"action":"test","test_to":"prova@example.invalid"},follow=True).content.decode()
    assert "Email di prova inviata a prova@example.invalid" in r
    m=FakeSMTP.sent[-1];assert m["From"]=="Booking Suite TD <info@agenzia.invalid>" and m["To"]=="prova@example.invalid"
    assert "Indirizzo del destinatario non valido" in c.post("/platform/smtp/",{"action":"test","test_to":"nope"},follow=True).content.decode()
    class Refusing(FakeSMTP):
        def login(self,u,p):
            import smtplib;raise smtplib.SMTPAuthenticationError(535,b"no")
    monkeypatch.setattr(mail,"PinnedSMTP",Refusing)
    assert "rifiutato utente o password" in c.post("/platform/smtp/",{"action":"test","test_to":"prova@example.invalid"},follow=True).content.decode()

def test_sender_name_rejects_header_tricks():
    from core.forms import SMTPForm
    f=SMTPForm({"host":"smtp.x.invalid","port":"587","sender":"a@x.invalid","sender_name":"Evil\r\nBcc: x@y.invalid","verified":"on"})
    assert not f.is_valid() and "sender_name" in f.errors
