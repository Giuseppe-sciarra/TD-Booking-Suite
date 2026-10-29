"""2.6.0 — business profile in one go, WordPress plugin that connects itself, Gmail and Microsoft 365
sending, SMTP envelope, catalog forms per kind of booking, plans on one row, free months."""
import base64,json,smtplib
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from core.crypto import protect,reveal,decrypt,encrypt
from core.models import *
from core.presets import PRESETS
from core.profile import apply_profile,clean_names,PROFILES
from core.tenancy import scope
from tests.test_core import sample,client_as,payload
from tests.test_phase2 import staff_client

pytestmark=pytest.mark.django_db

# ---------- profile
def test_every_type_builds_a_bookable_catalog(sample):
    t,o,s=sample
    assert set(PROFILES)==set(PRESETS)
    for key in PRESETS:
        r=apply_profile(t,key,["Sede A"])
        with scope(t):
            services=list(Service.objects.filter(active=True))
            assert services and all(x.preset==key and x.kind==PRESETS[key]["kind"] for x in services),key
            assert Schedule.objects.exists() and Location.objects.filter(active=True).count()==1
            if PRESETS[key]["kind"] in ("resource","day"):
                assert all(ServiceResource.objects.filter(service=x).exists() for x in services),key
            if PRESETS[key]["kind"]=="event":assert Occurrence.objects.filter(start__gt=timezone.now()).count()==r["dates"]>0
        t.refresh_from_db();assert t.business_type==key

def test_restaurant_has_seats_and_no_nights(sample):
    t,o,s=sample
    apply_profile(t,"ristorante",["Centro","Mare"])
    with scope(t):
        tables=list(Service.objects.filter(active=True).select_related("location"))
        assert {x.location.name for x in tables}=={"Centro","Mare"} and all(x.kind=="capacity" and x.capacity==40 for x in tables)
        assert not Resource.objects.filter(active=True).exists()
    page=Client().get(f"/book/{t.slug}/").content.decode()
    assert "Scegli la sede" in page

def test_bookings_survive_a_change_of_type(sample):
    t,o,s=sample
    from core.booking import create_booking
    with scope(t):b,_=create_booking(t,payload(s))
    r=apply_profile(t,"parrucchiere",["Salone","Bis"])
    assert r["archived"]>=1
    with scope(t):
        old=Service.objects.get(pk=s.pk);assert not old.active
        assert Booking.objects.filter(pk=b.pk,status="confirmed").exists()
        assert Service.objects.filter(active=True,name="Taglio").count()==2 and Resource.objects.filter(active=True).count()==4
    apply_profile(t,"hotel",["Salone"])
    with scope(t):
        assert Location.objects.filter(active=True).count()==1 and Service.objects.filter(active=True,kind="day").count()==1
        assert not Location.objects.filter(name="Bis").exists()  # nothing pointed at it: gone

def test_location_names():
    assert clean_names(["", "  Mare "],2)==["Sede principale","Mare"]
    assert clean_names([],5)[-1]=="Quinta sede" and clean_names([],9)[-1]=="Sede 9" and len(clean_names([],80))==50
    with pytest.raises(ValueError):clean_names(["Uno","uno"],2)

def test_only_the_agency_changes_the_profile_in_cloud(sample):
    t,o,s=sample
    owner,_=client_as(t,role="owner")
    page=owner.get(f"/t/{t.slug}/setup/").content.decode()
    assert "li imposta l'agenzia" in page and 'name="business_type"' not in page and "Genera il codice" not in page
    assert owner.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"hotel","locations":"1"}).status_code==403
    assert owner.post(f"/t/{t.slug}/setup/apply/",{"action":"wp_code"}).status_code==403
    t.refresh_from_db();assert t.business_type=="" and not t.connect_hash
    c,_=staff_client()
    page=c.get(f"/t/{t.slug}/setup/").content.decode()
    assert 'name="business_type"' in page and "Genera il codice di collegamento" in page
    r=c.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"ristorante","locations":"2","name_1":"Centro","name_2":"Mare"},follow=True)
    assert "Configurazione applicata: Ristorante / Pizzeria, 2 sedi" in r.content.decode()
    t.refresh_from_db();assert t.business_type=="ristorante"
    assert Audit.raw.filter(tenant=t,action="tenant.profile").exists()

def test_more_than_five_locations(sample):
    t,o,s=sample
    c,_=staff_client()
    page=c.get(f"/t/{t.slug}/setup/").content.decode()
    assert 'value="custom"' in page and 'name="locations_custom"' in page and 'max="50"' in page
    names={f"name_{i}":f"Punto {i}" for i in range(1,9)}
    r=c.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"ristorante","locations":"custom","locations_custom":"8",**names},follow=True).content.decode()
    assert "8 sedi" in r
    with scope(t):assert Location.objects.filter(active=True).count()==8 and Service.objects.filter(active=True).count()==8
    page=c.get(f"/t/{t.slug}/setup/").content.decode()
    assert 'value="custom" checked' in page and 'value="8"' in page and 'value="Punto 8"' in page
    r=c.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"ristorante","locations":"custom","locations_custom":"99"},follow=True).content.decode()
    assert "Le sedi vanno da 1 a 50" in r

def test_the_administrator_sets_up_the_business_and_the_owner_runs_it(sample,settings):
    # 2.10.0, every edition: type, locations, services and the technical side are the agency's (the program's
    # administrator); the owner keeps opening hours, closing days and staff up to date
    settings.EDITION="private"
    t,o,s=sample
    owner,_=client_as(t,role="owner")
    page=owner.get(f"/t/{t.slug}/setup/").content.decode()
    assert 'name="business_type"' not in page and "li imposta l&#x27;agenzia" in page or "li imposta l'agenzia" in page
    assert "<h2>Orari di apertura</h2>" in page and "<h2>Servizi</h2>" not in page and "<h2>Sedi</h2>" not in page
    assert owner.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"medico","locations":"1","name_1":"Studio"}).status_code==403
    for kind in ("services","locations","options","turns","occurrences"):
        assert owner.get(f"/t/{t.slug}/catalog/{kind}/").status_code==403 and owner.get(f"/t/{t.slug}/catalog/{kind}/new/").status_code==403
    for kind in ("schedules","exceptions","resources"):assert owner.get(f"/t/{t.slug}/catalog/{kind}/new/").status_code==200
    tabs=owner.get(f"/t/{t.slug}/catalog/schedules/").content.decode()
    assert ">Chiusure e giorni speciali</a>" in tabs and ">Servizi</a>" not in tabs and ">Sedi</a>" not in tabs
    admin,_=client_as(t,staff=True)
    r=admin.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"medico","locations":"1","name_1":"Studio"},follow=True)
    assert "Studio medico / Fisioterapia" in r.content.decode()
    staffer,_=client_as(t,role="operator")
    assert staffer.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"hotel","locations":"1"}).status_code==403

def test_client_card_shows_type_and_links_to_setup(sample):
    t,o,s=sample
    apply_profile(t,"spa",["Centro"])
    c,_=staff_client()
    page=c.get(f"/platform/clienti/{t.slug}/").content.decode()
    assert "SPA / Massaggi" in page and f"/t/{t.slug}/setup/#tipo" in page and f"/t/{t.slug}/setup/#wordpress" in page

# ---------- WordPress
def code_for(t):
    from core.wpconnect import new_code
    return new_code(t)

def connect(code,site="https://www.salone.example"):
    return Client().post("/api/wordpress/connect/",json.dumps({"code":code,"site":site}),content_type="application/json")

def test_wordpress_connects_with_a_one_time_code(sample):
    t,o,s=sample
    code=code_for(t)
    assert len(code)==19 and code.count("-")==3
    r=connect(code.lower().replace("-"," "))
    data=r.json()
    assert r.status_code==200 and data["ok"] and data["tenant"]==t.slug and data["services"][0]["name"]=="Servizio" and data["locations"][0]["name"]=="Centro"
    t.refresh_from_db()
    assert t.embed_origins==["https://www.salone.example"] and not t.connect_hash
    assert connect(code).status_code==403  # one use only
    page=Client().get(f"/book/{t.slug}/")
    assert "https://www.salone.example" in page["Content-Security-Policy"]

def test_wordpress_code_expires_and_checks_the_site(sample):
    t,o,s=sample
    code=code_for(t)
    for bad in ("https://x.example/wp","ftp://x.example","https://*.example","javascript:alert(1)",""):
        assert connect(code,bad).status_code==400
    assert connect("AAAA-BBBB").status_code==400
    Tenant.objects.filter(pk=t.pk).update(connect_until=timezone.now()-timedelta(minutes=1))
    assert connect(code).status_code==403
    t.refresh_from_db();assert t.embed_origins==[]

def test_wordpress_code_from_the_setup_page_and_removal(sample):
    t,o,s=sample
    c,_=staff_client()
    page=c.post(f"/t/{t.slug}/setup/apply/",{"action":"wp_code"},follow=True).content.decode()
    import re
    code=re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}",page).group(0)
    assert "valido 24 ore" in page
    assert re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}",c.get(f"/t/{t.slug}/setup/").content.decode()) is None  # shown once
    assert connect(code).status_code==200
    c.post(f"/t/{t.slug}/setup/apply/",{"action":"wp_remove","site":"https://www.salone.example"})
    t.refresh_from_db();assert t.embed_origins==[]

def test_public_config_and_location_filter(sample):
    t,o,s=sample
    apply_profile(t,"ristorante",["Centro","Mare"])
    data=Client().get(f"/book/{t.slug}/api/config/").json()
    assert data["business_label"]=="Ristorante / Pizzeria" and len(data["locations"])==2 and "embed" not in json.dumps(data)
    mare=[l for l in data["locations"] if l["name"]=="Mare"][0]["id"]
    page=Client().get(f"/book/{t.slug}/?location={mare}").content.decode()
    assert '"location": "Mare"' in page and '"location": "Centro"' not in page
    page=Client().get(f"/book/{t.slug}/?location=nonsense").content.decode()
    assert '"location": "Centro"' in page

def test_plugin_has_the_connection_flow():
    from pathlib import Path
    php=(Path(__file__).resolve().parents[1]/"wordpress/td-booking-suite/td-booking-suite.php").read_text()
    for needle in ("admin_post_tdbs_connect","/api/wordpress/connect/","/api/config/","location=","TDBS_ALLOW_HTTP","tdbs_admin_check('tdbs_connect')","check_admin_referer($action)","wp_insert_post"):
        assert needle in php
    # 2.6.0: TD Booking is gone, and so is the switch from it
    for gone in ("td_bk_","legacy","rollback","[td_booking]","rest_pre_dispatch"):
        assert gone not in php

# ---------- email
class FakeSMTP:
    sent=[]
    def __init__(self,*a,**kw):pass
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def ehlo(self):pass
    def starttls(self,**kw):pass
    def login(self,u,p):pass
    def send_message(self,m,from_addr=None):FakeSMTP.sent.append((m,from_addr));return {}

def test_smtp_envelope_is_the_authenticated_mailbox(monkeypatch):
    from core import mail
    monkeypatch.setattr(mail,"PinnedSMTP",FakeSMTP);FakeSMTP.sent=[]
    cfg={"host":"authsmtp.example","port":587,"mode":"starttls","username":"smtp@salone.example","password":"p","sender":"info@salone.example","sender_name":"Salone","reply_to":""}
    mail.send_message(cfg,{"to":"cliente@example.invalid","subject":"Ciao","body":"x"},"<1@x>")
    m,envelope=FakeSMTP.sent[-1]
    assert envelope=="smtp@salone.example" and m["From"]=="Salone <info@salone.example>"
    mail.send_message({**cfg,"username":"utente"},{"to":"c@example.invalid","subject":"s","body":"b"},"<2@x>")
    assert FakeSMTP.sent[-1][1]=="info@salone.example"

class FakeHTTP:
    """Google and Microsoft endpoints, recorded."""
    def __init__(self,send_status=202):self.calls=[];self.send_status=send_status
    def __call__(self,url,form=None,body=None,headers=None,timeout=15):
        self.calls.append((url,form,body,headers))
        if "oauth2.googleapis.com/token" in url:
            if form.get("grant_type")=="authorization_code":return 200,{"access_token":"at","refresh_token":"rt","scope":"openid email https://www.googleapis.com/auth/gmail.send"}
            return 200,{"access_token":"at2","expires_in":3600}
        if "userinfo" in url:return 200,{"email":"salone@gmail.example"}
        if "login.microsoftonline.com" in url:
            return (200,{"access_token":"ms","expires_in":3600}) if form["client_secret"]=="giusto" else (401,{"error":"invalid_client","error_description":"AADSTS7000215: Invalid client secret"})
        if "gmail.googleapis.com" in url or "graph.microsoft.com" in url:return self.send_status,{}
        return 404,{}

def test_gmail_connects_and_sends(sample,monkeypatch):
    from core import mailapi
    from django.core.cache import cache
    cache.clear()
    fake=FakeHTTP(200);monkeypatch.setattr(mailapi,"http",fake)
    t,o,s=sample
    c,_=staff_client()
    page=c.get(f"/t/{t.slug}/smtp/?metodo=google").content.decode()
    assert "http://localhost:8000/oauth/google/" in page and "Collega con Google" in page
    r=c.post(f"/t/{t.slug}/smtp/?metodo=google",{"action":"google_connect","client_id":"cid.apps.googleusercontent.com","client_secret":"sec","sender_name":"Salone","sender":"","reply_to":""})
    assert r.status_code==302 and r["Location"].startswith("https://accounts.google.com/") and "gmail.send" in r["Location"] and "access_type=offline" in r["Location"]
    state=c.session["google_oauth"]["state"]
    assert c.get("/oauth/google/?state=wrong&code=x",follow=True).status_code==200 and not Tenant.objects.get(pk=t.pk).smtp
    r=c.post(f"/t/{t.slug}/smtp/?metodo=google",{"action":"google_connect","client_id":"cid.apps.googleusercontent.com","client_secret":"sec","sender_name":"Salone"})
    state=c.session["google_oauth"]["state"]
    r=c.get(f"/oauth/google/?state={state}&code=abc",follow=True)
    assert "Google collegato: le email partono da salone@gmail.example" in r.content.decode()
    t.refresh_from_db()
    with scope(t):cfg=decrypt(t,t.smtp,"smtp")
    assert cfg["method"]=="gmail" and cfg["refresh_token"]=="rt" and cfg["sender"]=="salone@gmail.example"
    from core.mail import send_message
    send_message(cfg,{"to":"cliente@example.invalid","subject":"Conferma","body":"Ciao","unsubscribe":"https://x.example/u"},"<9@x>")
    url,form,body,headers=fake.calls[-1]
    raw=json.loads(body)["raw"];mime=base64.urlsafe_b64decode(raw+"="*(-len(raw)%4)).decode()
    assert url.endswith("/messages/send") and headers["Authorization"]=="Bearer at2" and "From: Salone <salone@gmail.example>" in mime and "List-Unsubscribe" in mime
    page=c.get(f"/t/{t.slug}/smtp/").content.decode()
    assert "Collegato con l&#x27;account" in page or "Collegato con l'account" in page

def test_microsoft_graph_checks_the_secret_and_sends(sample,monkeypatch):
    from core import mailapi
    from django.core.cache import cache
    cache.clear()
    fake=FakeHTTP(202);monkeypatch.setattr(mailapi,"http",fake)
    t,o,s=sample
    c,_=staff_client()
    data={"action":"save_graph","tenant_id":"contoso.onmicrosoft.com","client_id":"app","client_secret":"sbagliato","mailbox":"prenotazioni@salone.example","sender_name":"Salone","sender":"","reply_to":""}
    r=c.post(f"/t/{t.slug}/smtp/?metodo=microsoft",data).content.decode()
    assert "Invalid client secret" in r and not Tenant.objects.get(pk=t.pk).smtp
    r=c.post(f"/t/{t.slug}/smtp/?metodo=microsoft",{**data,"client_secret":"giusto"},follow=True).content.decode()
    assert "Microsoft 365 collegato" in r
    t.refresh_from_db()
    with scope(t):cfg=decrypt(t,t.smtp,"smtp")
    assert cfg["method"]=="graph" and cfg["sender"]=="prenotazioni@salone.example"
    r=c.post(f"/t/{t.slug}/smtp/",{"action":"test","test_to":"io@example.invalid"},follow=True).content.decode()
    assert "Email di prova inviata" in r
    url,form,body,headers=fake.calls[-1]
    assert url=="https://graph.microsoft.com/v1.0/users/prenotazioni@salone.example/sendMail" and headers["Content-Type"]=="text/plain"
    assert b"Subject: Email di prova" in base64.b64decode(body)
    # a refused send is a configuration problem, a busy service is retried
    fake.send_status=403
    from core.mail import send_message
    with pytest.raises(ValueError):send_message(cfg,{"to":"a@example.invalid","subject":"s","body":"b"},"<1@x>")
    fake.send_status=503
    with pytest.raises(smtplib.SMTPResponseException) as ex:send_message(cfg,{"to":"a@example.invalid","subject":"s","body":"b"},"<1@x>")
    assert ex.value.smtp_code==451

def test_email_page_is_agency_only_and_removable(sample):
    t,o,s=sample
    owner,_=client_as(t,role="owner")
    assert owner.get(f"/t/{t.slug}/smtp/").status_code==403
    c,_=staff_client()
    with scope(t):Tenant.objects.filter(pk=t.pk).update(smtp=encrypt(t,{"method":"smtp","host":"h.example","port":587,"mode":"starttls","username":"","password":"","sender":"a@b.example","sender_name":"","reply_to":""},"smtp"))
    r=c.post(f"/t/{t.slug}/smtp/",{"action":"remove"},follow=True).content.decode()
    assert "Invio email rimosso" in r and not Tenant.objects.get(pk=t.pk).smtp

# ---------- catalog forms
def test_service_form_follows_the_kind(sample):
    t,o,s=sample
    owner,_=client_as(t,role="owner")
    Tenant.objects.filter(pk=t.pk).update(business_type="ristorante")
    # 2.10.0: services are set up by the agency only
    assert owner.get(f"/t/{t.slug}/catalog/services/new/").status_code==403
    c,_=staff_client()
    # the agency's form has every kind's fields, shown by the kind chosen; it starts from the restaurant's
    form=c.get(f"/t/{t.slug}/catalog/services/new/").content.decode()
    assert 'name="kind"' in form and "data-kinds" in form and 'name="capacity"' in form and 'name="turnover"' in form and "Passo degli orari" not in form

def test_location_form_is_in_italian(sample):
    t,o,s=sample
    owner,_=client_as(t,staff=True)
    form=owner.get(f"/t/{t.slug}/catalog/locations/new/").content.decode()
    assert ">Indirizzo<" in form and ">Telefono<" in form and ">Address<" not in form

# ---------- public pages
def test_plans_on_one_row_with_free_months():
    SiteSettings.objects.update_or_create(pk=1,defaults={"pricing_enabled":True})
    PricingPlan.objects.filter(name="Pro").update(active=False)
    PricingPlan.objects.filter(name="Base").update(free_months=2)
    page=Client().get("/prezzi/").content.decode()
    assert page.count('class="plans cols-2"')==1 and "I primi 2 mesi sono gratis" in page and "plan-edition private" in page
    PricingPlan.objects.filter(name="Base").update(free_months=1)
    assert "Il primo mese è gratis" in Client().get("/prezzi/").content.decode()

def test_free_months_in_the_plan_form():
    c,_=staff_client()
    plan=PricingPlan.objects.get(name="Base")
    base={"edition":"cloud","name":"Base","price":"29 €","order":1,"active":"on"}
    c.post(f"/platform/prezzi/{plan.pk}/",{**base,"free_months":"3"});plan.refresh_from_db();assert plan.free_months==3
    assert "Al massimo 24 mesi" in c.post(f"/platform/prezzi/{plan.pk}/",{**base,"free_months":"99"}).content.decode()
    c.post(f"/platform/prezzi/{plan.pk}/",base);plan.refresh_from_db();assert plan.free_months==0

def test_company_name_links_to_its_site():
    login=Client().get("/login/").content.decode()
    assert 'href="https://www.tastieredigitali.it" target="_blank" rel="noopener">di Tastiere Digitali' in login
    SiteSettings.objects.update_or_create(pk=1,defaults={"owner_url":""})
    login=Client().get("/login/").content.decode()
    assert "di Tastiere Digitali</small>" in login and "tastieredigitali.it" not in login

# ---------- sessions that last
def test_remember_keeps_the_device_signed_in(settings):
    import time
    from core.models import User
    u=User.objects.create_user("gestore@example.invalid","A-test-password-12345")
    c=Client()
    c.post("/login/",{"email":"gestore@example.invalid","password":"A-test-password-12345","remember":"1"})
    s=c.session
    assert s["remember"] and s.get_expiry_age()==settings.SESSION_REMEMBER_DAYS*86400 and not s.get_expire_at_browser_close()
    s["last_seen"]=time.time()-3*3600;s.save()  # three hours away: still inside
    assert c.get("/").status_code in (200,302) and "_auth_user_id" in c.session
    s=c.session;s["last_seen"]=time.time()-(settings.SESSION_REMEMBER_DAYS*86400+60);s.save()
    c.get("/");assert "_auth_user_id" not in c.session
    other=Client()
    other.post("/login/",{"email":"gestore@example.invalid","password":"A-test-password-12345"})
    s=other.session
    assert not s["remember"] and s.get_expire_at_browser_close()
    s["last_seen"]=time.time()-3600;s.save()
    other.get("/");assert "_auth_user_id" not in other.session
    assert 'name="remember" value="1" id="remember" checked' in Client().get("/login/").content.decode()

def test_password_managers_stay_out_of_the_portal(sample):
    t,o,s=sample
    body=lambda html:html.split("<body",1)[1].split(">",1)[0]
    login=Client().get("/login/").content.decode()
    assert 'data-autofill="all"' in body(login)
    assert 'data-autofill="contact"' in body(Client().get(f"/book/{t.slug}/").content.decode())  # browser suggestions for the customer, no managers
    c,_=staff_client()
    for page in (f"/t/{t.slug}/smtp/",f"/t/{t.slug}/catalog/locations/new/","/platform/settings/"):
        body=c.get(page).content.decode().split("<body",1)[1].split(">",1)[0]
        assert "data-autofill" not in body,page
    page=c.get(f"/t/{t.slug}/smtp/").content.decode()
    assert 'name="password"' in page and 'autocomplete="new-password" data-1p-ignore' in page and 'data-lpignore="true"' in page
    from pathlib import Path
    js=(Path(__file__).resolve().parents[1]/"static/app.js").read_text()
    assert js.count("data-1p-ignore")==1 and 'fill!=="all"' in js and 'fill!=="contact"' in js
    assert 'autocomplete="current-password"' in login and "data-1p-ignore" not in login

def test_catalog_items_can_be_removed(sample):
    t,o,s=sample
    from core.booking import create_booking
    owner,_=client_as(t,staff=True)
    apply_profile(t,"parrucchiere",["Centro","Mare"])
    with scope(t):
        taglio=Service.objects.get(name="Taglio",location__name="Centro");piega=Service.objects.get(name="Piega",location__name="Centro")
        op=Resource.objects.filter(location=taglio.location).first()
        day=timezone.localdate()+timedelta(days=3)
        while day.weekday() in (0,6):day+=timedelta(days=1)  # the salon is open Tuesday to Saturday: no Sunday or Monday
        start=datetime.combine(day,time(10),ZoneInfo(t.timezone)).isoformat()
        create_booking(t,payload(taglio,resource=str(op.pk),start=start))
    r=owner.post(f"/t/{t.slug}/catalog/services/{piega.pk}/remove/",follow=True).content.decode()
    assert "Eliminato: Piega" in r
    r=owner.post(f"/t/{t.slug}/catalog/services/{taglio.pk}/remove/",follow=True).content.decode()
    assert "Disattivato perché ha prenotazioni collegate: Taglio" in r and "La prenotazione già fatta resta valida" in r
    assert "Disattivato, resta per le prenotazioni passate" in r
    with scope(t):
        assert not Service.objects.filter(pk=piega.pk).exists() and not Service.objects.get(pk=taglio.pk).active
        assert Booking.objects.filter(service=taglio,status="confirmed").count()==1
        mare=Location.objects.get(name="Mare")
    r=owner.post(f"/t/{t.slug}/catalog/locations/{mare.pk}/remove/",follow=True).content.decode()
    assert "Eliminato: Mare" in r
    with scope(t):
        assert not Location.objects.filter(name="Mare").exists() and not Service.objects.filter(location__name="Mare").exists()
        centro=Location.objects.get(name="Centro")
    r=owner.post(f"/t/{t.slug}/catalog/locations/{centro.pk}/remove/",follow=True).content.decode()
    assert "Serve almeno una sede" in r
    from tests.test_phase2 import member
    reception,_=member(t,"reception")
    with scope(t):colore=Service.objects.get(name="Colore")
    assert reception.post(f"/t/{t.slug}/catalog/services/{colore.pk}/remove/").status_code==403

def test_restaurant_catalog_speaks_its_language(sample):
    t,o,s=sample
    apply_profile(t,"ristorante",["Centro"])
    c,_=client_as(t,staff=True)
    page=c.get(f"/t/{t.slug}/catalog/services/").content.decode()
    assert "La sede è il luogo, il servizio è cosa si prenota" in page and "Sede: Centro" in page
    # the tabs speak the restaurant's language (the agency's template picker lists every type, operators included)
    assert ">Risorse</a>" not in page and ">Varianti dei servizi</a>" not in page and ">Operatori</a>" not in page
    setup=c.get(f"/t/{t.slug}/setup/").content.decode()
    assert "<h2>Risorse</h2>" not in setup and "<h2>Operatori</h2>" not in setup
    apply_profile(t,"parrucchiere",["Centro"])
    page=c.get(f"/t/{t.slug}/catalog/resources/").content.decode()
    assert "<h1>Operatori</h1>" in page and "ogni operatore ha la sua agenda" in page
    form=c.get(f"/t/{t.slug}/catalog/services/new/").content.decode()
    assert "Quali operatori si possono prenotare" in form

def test_booking_times_are_italian_24_hours(sample):
    from pathlib import Path
    t,o,s=sample
    js=(Path(__file__).resolve().parents[1]/"static/booking.js").read_text()
    assert 'hourCycle:"h23"' in js and "timeZoneName" not in js
    page=Client().get(f"/book/{t.slug}/").content.decode()
    assert "Europe/Rome" not in page.split('<script',1)[0] and "fuso" not in page

def test_restaurant_counts_covers_per_lunch_and_dinner(sample):
    """2.6.1: no time slots by default: arrival time only, the 40 covers last for the whole lunch or dinner."""
    from datetime import datetime,time as clock
    from zoneinfo import ZoneInfo
    from core.booking import available,create_booking,BookingError
    t,o,s=sample
    apply_profile(t,"ristorante",["Centro"])
    tz=ZoneInfo(t.timezone)
    day=timezone.now().astimezone(tz).date()+timedelta(days=7)
    while day.weekday()==0:day+=timedelta(days=1)  # closed on Monday
    with scope(t):
        tavolo=Service.objects.get(active=True)
        assert tavolo.turnover is False
        slots=available(t,tavolo.pk,day,party=2)
        lunch=[x for x in slots if x["start"][11:16]<"16:00"]
        assert lunch[0]["start"][11:16]=="12:00" and {x["end"][11:16] for x in lunch}=={"15:00"}
        book=lambda h,m,party:create_booking(t,{"service":str(tavolo.pk),"start":datetime.combine(day,clock(h,m),tz).isoformat(),"party":party,"first_name":"A","last_name":"B",
            "email":secrets_mail(),"phone":"333 123 4567","request_key":__import__("secrets").token_urlsafe(24)})
        book(12,0,12);book(12,0,12);book(12,0,12);book(14,0,4)   # 40 covers at lunch
        with pytest.raises(BookingError):book(13,30,1)
        assert book(20,0,12)[0].pk  # dinner has its own covers
    page=Client().get(f"/book/{t.slug}/").content.decode()
    assert '"turnover": false' in page
    c,_=client_as(t,staff=True)
    form=c.get(f"/t/{t.slug}/catalog/services/{tavolo.pk}/").content.decode()
    assert 'name="turnover"' in form and "A turni: il posto si libera dopo un tempo fisso" in form
    assert "coperti per ogni pranzo e cena" in c.get(f"/t/{t.slug}/catalog/services/").content.decode()

def secrets_mail():
    import secrets;return secrets.token_hex(6)+"@example.invalid"

def test_time_slots_can_be_switched_on(sample):
    t,o,s=sample
    apply_profile(t,"ristorante",["Centro"])
    with scope(t):
        tavolo=Service.objects.get(active=True);tavolo.turnover=True;tavolo.duration=90;tavolo.save()
        from core.booking import available
        from zoneinfo import ZoneInfo
        day=timezone.now().astimezone(ZoneInfo(t.timezone)).date()+timedelta(days=7)
        while day.weekday()==0:day+=timedelta(days=1)
        first=available(t,tavolo.pk,day,party=2)[0]
        assert first["start"][11:16]=="12:00" and first["end"][11:16]=="13:30"

def test_phone_without_international_prefix():
    from core.booking import normalize_phone
    assert normalize_phone("333 123 4567")=="+393331234567" and normalize_phone("06-1234567")=="+39061234567"
    assert normalize_phone("0039 333.1234567")=="+393331234567" and normalize_phone("+44 20 7946 0958")=="+442079460958"
    assert normalize_phone("ciao")=="" and normalize_phone("12")==""

def test_restaurant_setup_has_no_variants_link(sample):
    t,o,s=sample
    apply_profile(t,"ristorante",["Centro"])
    c,_=client_as(t,staff=True)
    page=c.get(f"/t/{t.slug}/setup/").content.decode()
    assert "Varianti dei servizi" not in page
    apply_profile(t,"parrucchiere",["Centro"])
    assert "Varianti dei servizi" in c.get(f"/t/{t.slug}/setup/").content.decode()

def test_the_same_type_again_keeps_what_the_business_made(sample):
    t,o,s=sample
    c,_=staff_client()
    apply_profile(t,"parrucchiere",["Centro"])
    with scope(t):
        Resource.objects.filter(name="Operatore 1").update(name="Giulia")
        Service.objects.filter(name="Taglio").update(duration=40)
        hours=Schedule.objects.count()
    # the tile clicked again, a second location added: nothing the salon set changes, the new location can be booked
    r=c.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"parrucchiere","locations":"2","name_1":"Centro storico","name_2":"Mare"},follow=True).content.decode()
    assert "Sedi aggiornate: 2 sedi. Servizi, operatori e orari restano come li hai impostati" in r
    with scope(t):
        centro=Location.objects.get(name="Centro storico");mare=Location.objects.get(name="Mare")
        assert Resource.objects.filter(location=centro,name="Giulia",active=True).exists()
        assert Service.objects.get(location=centro,name="Taglio").duration==40 and Schedule.objects.count()==hours
        assert set(Service.objects.filter(location=mare).values_list("name",flat=True))=={"Taglio","Piega","Colore"}
        assert Resource.objects.filter(location=mare).count()==2
    # another type is not applied by a stray click: it needs the confirmation
    r=c.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"ristorante","locations":"1","name_1":"Centro storico"},follow=True).content.decode()
    assert "spunta la conferma" in r
    t.refresh_from_db();assert t.business_type=="parrucchiere"
    with scope(t):assert Resource.objects.filter(name="Giulia",active=True).exists()
    page=c.get(f"/t/{t.slug}/setup/").content.decode()
    assert 'data-current="parrucchiere"' in page and 'name="confirm_type"' in page
    r=c.post(f"/t/{t.slug}/setup/apply/",{"action":"profile","business_type":"ristorante","locations":"1","name_1":"Centro storico","confirm_type":"1"},follow=True).content.decode()
    assert "Configurazione applicata: Ristorante / Pizzeria" in r
    with scope(t):assert not Resource.objects.filter(active=True).exists() and Service.objects.filter(active=True,name="Tavolo").count()==1

def test_an_operator_has_what_she_does_and_her_own_days(sample):
    from core.booking import available
    t,o,s=sample
    owner,_=client_as(t,role="owner")
    apply_profile(t,"parrucchiere",["Centro"])
    with scope(t):
        loc=Location.objects.get();taglio=Service.objects.get(name="Taglio");colore=Service.objects.get(name="Colore")
    form=owner.get(f"/t/{t.slug}/catalog/resources/new/").content.decode()
    # a person takes one client at a time: no places; a new operator starts with every service ticked
    assert "Posti disponibili" not in form and "Persone massime" not in form and "Cosa fa" in form and "Ha giorni e orari suoi" in form
    assert form.count('name="services"')==3 and form.count("checked")>=3
    data={"name":"Giulia","location":str(loc.pk),"active":"on","services":[str(taglio.pk)],"own_hours":"on",
          "day_2":"on","start_2":"14:00","end_2":"19:00"}
    r=owner.post(f"/t/{t.slug}/catalog/resources/new/",data,follow=True).content.decode()
    assert "Salvato." in r
    with scope(t):
        giulia=Resource.objects.get(name="Giulia")
        assert list(ServiceResource.objects.filter(resource=giulia).values_list("service__name",flat=True))==["Taglio"]
        assert list(Schedule.objects.filter(resource=giulia).values_list("weekday","start","end"))==[(2,time(14),time(19))]
        # her own days narrow the salon's: only Wednesday afternoon, and Colore is not hers
        day=timezone.localdate()+timedelta(days=1)
        while day.weekday()!=2:day+=timedelta(days=1)
        hours={x["start"][11:16] for x in available(t,taglio.pk,day,resource_id=giulia.pk)}
        assert hours and min(hours)=="14:00" and max(hours)=="18:30"
        with pytest.raises(Exception):available(t,colore.pk,day,resource_id=giulia.pk)
    # back to the salon's hours, and a day that ends before it starts is refused
    edit=f"/t/{t.slug}/catalog/resources/{giulia.pk}/"
    page=owner.get(edit).content.decode()
    assert "Segna ferie o un giorno libero" in page and f"?resource={giulia.pk}" in page
    r=owner.post(edit,{**data,"start_2":"19:00","end_2":"14:00"}).content.decode()
    assert "l&#x27;orario di fine viene dopo quello di inizio" in r or "l'orario di fine viene dopo quello di inizio" in r
    owner.post(edit,{"name":"Giulia","location":str(loc.pk),"active":"on","services":[str(taglio.pk),str(colore.pk)]})
    with scope(t):
        assert not Schedule.objects.filter(resource=giulia).exists() and ServiceResource.objects.filter(resource=giulia).count()==2
    # a closing day for her alone starts from her card
    assert f'value="{giulia.pk}" selected' in owner.get(f"/t/{t.slug}/catalog/exceptions/new/?resource={giulia.pk}").content.decode()
