"""2.8.0 — client record, allergies, questions per type of business, business logo, and the fixes of the audit."""
import io,json,secrets,time as wallclock
from datetime import datetime,timedelta,time as clock
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from PIL import Image
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,move_booking,available,BookingError
from core.crypto import encrypt,decrypt
from core.privacy import apply_retention,erase_contact,export_contact
from core.presets import ALLERGENS,questions
from tests.test_core import sample,payload,client_as
from tests.test_phase2 import member,staff_client

pytestmark=pytest.mark.django_db

def salon(t,operators=("Anna","Bruno")):
    """A salon service with its operators, open every day 9-18."""
    with scope(t):
        loc=Location.objects.first()
        svc=Service.objects.create(tenant=t,location=loc,name="Colore",kind="resource",duration=60,interval=30,lead_minutes=0)
        people=[Resource.objects.create(tenant=t,location=loc,name=n) for n in operators]
        for r in people:ServiceResource.objects.create(tenant=t,service=svc,resource=r)
    return svc,people

def at(service,days,hour):
    d=(timezone.now()+timedelta(days=days)).astimezone(ZoneInfo(service.tenant.timezone)).date()
    return datetime.combine(d,clock(hour),ZoneInfo(service.tenant.timezone)).isoformat()

# ---------- client record
def test_record_from_the_card_encrypted_edited_and_removed(sample):
    t,o,s=sample
    t.business_type="parrucchiere";t.save()
    owner,u=client_as(t)
    with scope(t):b,_=create_booking(t,payload(s));cid=b.contact_id
    url=f"/t/{t.slug}/contacts/{cid}/"
    page=owner.get(url).content.decode()
    assert "<h2>Trattamenti</h2>" in page and "La scheda è ancora vuota" in page and "Registra trattamento" in page
    today=timezone.now().astimezone(ZoneInfo(t.timezone)).date()
    r=owner.post(url,{"action":"treatment","date":today.isoformat(),"service":"Colore","staff":"Anna","details":"6.3 + ossigeno 20 vol\nposa 35'"},follow=True).content.decode()
    assert "Salvato nella scheda" in r and "6.3 + ossigeno 20 vol" in r and "con Anna" in r
    with scope(t):
        entry=Treatment.objects.get()
        assert "6.3" not in entry.pii and entry.data["details"].startswith("6.3") and entry.contact_id==cid
    # never in the future: the record is what was done
    r=owner.post(url,{"action":"treatment","date":(today+timedelta(days=1)).isoformat(),"service":"Taglio"},follow=True).content.decode()
    assert "nel futuro" in r
    with scope(t):assert Treatment.objects.count()==1
    owner.post(url,{"action":"treatment","treatment":str(entry.pk),"date":today.isoformat(),"service":"Colore e piega","staff":"Anna","details":"7.1"})
    with scope(t):assert Treatment.objects.get().data["service"]=="Colore e piega" and Treatment.objects.count()==1
    owner.post(url,{"action":"treatment_remove","treatment":str(entry.pk)})
    with scope(t):assert not Treatment.objects.exists()
    with scope(t):assert Audit.objects.filter(action__startswith="treatment.").count()==3

def test_record_permissions_and_types(sample):
    t,o,s=sample
    t.business_type="parrucchiere";t.save()
    with scope(t):b,_=create_booking(t,payload(s));cid=b.contact_id
    mk,_=client_as(t,role="marketing")  # sees contacts, not notes: no record, cannot write it
    assert "Trattamenti" not in mk.get(f"/t/{t.slug}/contacts/{cid}/").content.decode()
    assert mk.post(f"/t/{t.slug}/contacts/{cid}/",{"action":"treatment","date":"2026-01-01","service":"x"}).status_code==403
    # a restaurant has no record, a tattoo studio has its sittings
    owner,_=client_as(t)
    t.business_type="ristorante";t.save()
    assert "Scheda cliente" not in owner.get(f"/t/{t.slug}/contacts/{cid}/").content.decode()
    t.business_type="tattoo";t.save()
    assert "<h2>Sedute</h2>" in owner.get(f"/t/{t.slug}/contacts/{cid}/").content.decode()

def test_record_from_the_booking_for_the_operator(sample):
    t,o,s=sample
    t.business_type="parrucchiere";t.save()
    svc,(anna,bruno)=salon(t)
    with scope(t):
        b,_=create_booking(t,payload(svc,resource=str(anna.pk),start=at(svc,3,10)))
        other,_=create_booking(t,payload(svc,resource=str(bruno.pk),start=at(svc,3,11),request_key=secrets.token_urlsafe(24)))
        Booking.objects.filter(pk=b.pk).update(start=timezone.now()-timedelta(days=1),end=timezone.now()-timedelta(days=1)+timedelta(hours=1))
    op,_=member(t,"operator",resources=[str(anna.pk)])
    page=op.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    assert "Salva nella scheda" in page and 'value="Colore"' in page and 'value="Anna"' in page  # filled in from the booking
    day=(timezone.now()-timedelta(days=1)).astimezone(ZoneInfo(t.timezone)).date().isoformat()
    r=op.post(f"/t/{t.slug}/bookings/{b.pk}/",{"action":"treatment","date":day,"service":"Colore","staff":"Anna","details":"base 5"},follow=True).content.decode()
    assert "Registrato per questa prenotazione" in r
    with scope(t):assert Treatment.objects.get().booking_id==b.pk
    # the operator does not reach the bookings of the other operator
    assert op.post(f"/t/{t.slug}/bookings/{other.pk}/",{"action":"treatment","date":day,"service":"x"}).status_code==404

def test_record_follows_privacy_rules(sample):
    t,o,s=sample
    t.business_type="centro_estetico";t.save()
    owner,u=client_as(t)
    with scope(t):
        b,_=create_booking(t,payload(s));c=b.contact
        Booking.objects.filter(pk=b.pk).update(start=timezone.now()-timedelta(days=5),end=timezone.now()-timedelta(days=5)+timedelta(hours=1))
        from core.clients import save_treatment
        save_treatment(t,c,{"date":timezone.localdate()-timedelta(days=5),"service":"Pulizia viso","staff":"","details":"pelle sensibile"},u)
        assert export_contact(t,c)["treatments"][0]["details"]=="pelle sensibile"
        erase_contact(t,c,u)
        assert not Treatment.objects.exists()

def test_retention_keeps_new_cards_and_recent_records(sample):
    t,o,s=sample
    owner,u=client_as(t)
    from core.clients import find_or_create,save_treatment
    with scope(t):
        walk_in,_=find_or_create(t,{"first_name":"Lia","last_name":"","email":"","phone":"+393331112222"})
        regular,_=find_or_create(t,{"first_name":"Ugo","last_name":"","email":"","phone":"+393331113333"})
        Contact.objects.filter(pk__in=[walk_in.pk,regular.pk]).update(created=timezone.now()-timedelta(days=t.retention_days+60))
        save_treatment(t,regular,{"date":timezone.localdate()-timedelta(days=10),"service":"Taglio","staff":"","details":""},u)
        save_treatment(t,regular,{"date":timezone.localdate()-timedelta(days=t.retention_days+30),"service":"Colore","staff":"","details":""},u)
        fresh,_=find_or_create(t,{"first_name":"Nuovo","last_name":"","email":"","phone":"+393331114444"})
        report=apply_retention(t,apply=True)
        # before 2.8.0 a card added by hand, with no booking yet, was erased at the first run
        assert not Contact.objects.get(pk=fresh.pk).email_index.startswith("erased:")
        assert not Contact.objects.get(pk=regular.pk).email_index.startswith("erased:")  # a recent entry keeps the card
        assert Contact.objects.get(pk=walk_in.pk).email_index.startswith("erased:")
        assert report["treatments"]==1 and [x.data["service"] for x in Treatment.objects.all()]==["Taglio"]

# ---------- allergies and intolerances
def test_allergens_in_the_booking_form(sample):
    t,o,s=sample
    t.allergens=["Glutine","Lattosio"];t.allergens_on=True;t.save()
    page=Client().get(f"/book/{t.slug}/").content.decode()
    assert '"allergens": ["Glutine", "Lattosio"]' in page and "Intolleranze o allergie?" in page
    with scope(t):
        b,_=create_booking(t,payload(s,allergies=["Lattosio","Glutine"]))
        assert b.data["allergies"]==["Glutine","Lattosio"] and "Glutine" not in b.pii
        with pytest.raises(BookingError,match="Intolleranze"):create_booking(t,payload(s,hour=11,allergies=["Arsenico"]))
        with pytest.raises(BookingError):create_booking(t,payload(s,hour=11,allergies="Glutine"))
    owner,_=client_as(t)
    assert "Intolleranze e allergie segnalate" in owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    assert "Ha segnalato" in owner.get(f"/t/{t.slug}/contacts/{b.contact_id}/").content.decode()
    viewer,_=member(t,"viewer")  # health data: only who sees the notes
    assert "Glutine" not in viewer.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    r=owner.get(f"/t/{t.slug}/export/?kind=bookings&profile=generic").content.decode()
    assert "Intolleranze" in r and "Glutine, Lattosio" in r
    # switched off: nothing asked, nothing stored
    t.allergens_on=False;t.save()
    with scope(t):
        b2,_=create_booking(t,payload(s,hour=12,allergies=["Glutine"]))
        assert "allergies" not in b2.data

def test_allergens_page(sample):
    t,o,s=sample
    owner,_=client_as(t,staff=True)  # 2.10.0: what the form asks is set up by the agency
    url=f"/t/{t.slug}/setup/allergie/"
    owner.post(url,{"action":"toggle","on":"1"})
    t.refresh_from_db();assert t.allergens_on and t.allergens==ALLERGENS and len(ALLERGENS)==15
    owner.post(url,{"action":"add","name":"  Vegano  "});owner.post(url,{"action":"add","name":"vegano"})
    owner.post(url,{"action":"remove","name":"Lupini"})
    t.refresh_from_db();assert t.allergens[-1]=="Vegano" and t.allergens.count("Vegano")==1 and "Lupini" not in t.allergens
    reception,_=member(t,"reception")  # no catalog.write
    assert reception.post(url,{"action":"toggle","on":"0"}).status_code==403
    real_owner,_=client_as(t)
    assert real_owner.post(url,{"action":"toggle","on":"0"}).status_code==403

def test_restaurant_starts_with_allergens_and_questions(sample):
    t,o,s=sample
    from core.profile import apply_profile
    apply_profile(t,"ristorante",["Centro"])
    t.refresh_from_db()
    assert t.allergens_on and "Glutine" in t.allergens
    with scope(t):assert [f["key"] for f in Service.objects.get(active=True).fields]==["occasione","seggioloni","spazio","cane"]

# ---------- questions per type of business
def test_recommended_questions_added_without_removing(sample):
    t,o,s=sample
    t.business_type="officina";t.save()
    with scope(t):
        s.fields=[{"key":"targa","label":"La targa","type":"text","required":False},{"key":"mio","label":"Mia domanda","type":"text","required":False}];s.save()
    owner,_=client_as(t,staff=True)  # 2.10.0: the agency sets up the questions
    page=owner.get(f"/t/{t.slug}/setup/").content.decode()
    assert "Domande consigliate per Officina" in page and "Chilometri attuali" in page and "La targa" not in page
    owner.post(f"/t/{t.slug}/setup/consigliati/",{"q":["veicolo","km"]})
    with scope(t):
        keys=[f["key"] for f in Service.objects.get(pk=s.pk).fields]
        assert keys==["targa","mio","veicolo","km"]  # the business's own questions stay, auto_cortesia was not chosen
    assert all(len(q["label"])<=80 and q["key"].isidentifier() for k in ("ristorante","tattoo","hotel","autoscuola","tour") for q in questions(k))

def test_new_service_starts_with_questions_and_operators(sample):
    t,o,s=sample
    t.business_type="centro_estetico";t.save()
    svc,people=salon(t)
    owner,_=client_as(t,staff=True)
    page=owner.get(f"/t/{t.slug}/catalog/services/new/").content.decode()
    assert "prima_volta" in page and page.count("checked")>=len(people)

# ---------- business logo
def wide_png():
    out=io.BytesIO();im=Image.new("RGBA",(1200,300),(0,0,0,0));im.paste((122,85,173,255),(100,50,1100,250));im.save(out,format="PNG");out.seek(0);out.name="logo.png";return out

def test_wide_logo_keeps_its_shape_and_shows_in_the_portal(sample):
    t,o,s=sample
    c,u=staff_client()
    c.post(f"/platform/clienti/{t.slug}/",{"action":"logo","logo":wide_png()})
    t.refresh_from_db()
    assert t.logo_wide and 4.9<t.logo_ratio<5.1  # the transparent margins are cut: 1000 x 200
    owner,_=client_as(t)
    page=owner.get(f"/t/{t.slug}/").content.decode()
    assert 'class="workspace-logo wide"' in page and f"/brand/{t.slug}/logo.png" in page
    assert 'class="brand-logo wide"' in Client().get(f"/book/{t.slug}/").content.decode()

# ---------- fixes of the audit
def test_moving_a_booking_without_email_sends_nothing(sample):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):
        b,_=create_booking(t,payload(s,email="",last_name="",first_name="Lia"),actor=u)
        move_booking(t,b.pk,{"service":str(s.pk),"start":at(s,3,14),"party":1},u)
        # before: an email to "" was queued, bounced and excluded the client from email
        assert not Delivery.objects.filter(channel="email").exists()

def test_operator_cannot_move_to_another_operator(sample):
    t,o,s=sample
    svc,(anna,bruno)=salon(t)
    with scope(t):b,_=create_booking(t,payload(svc,resource=str(bruno.pk),start=at(svc,3,10)))
    op,_=member(t,"operator",resources=[str(bruno.pk)])
    r=op.post(f"/t/{t.slug}/bookings/{b.pk}/move-api/",json.dumps({"resource":str(anna.pk),"start":at(svc,3,11),"party":"1"}),content_type="application/json")
    assert r.status_code==403 and "risorse assegnate" in r.json()["error"]
    with scope(t):assert Booking.objects.get(pk=b.pk).resource_id==bruno.pk

def test_operator_books_with_the_first_available_among_his_own(sample):
    t,o,s=sample
    svc,(anna,bruno)=salon(t)
    op,_=member(t,"operator",resources=[str(bruno.pk)])
    page=op.get(f"/t/{t.slug}/bookings/new/").content.decode()
    assert "Bruno" in page and '"name": "Anna"' not in page
    day=(timezone.now()+timedelta(days=3)).astimezone(ZoneInfo(t.timezone)).date().isoformat()
    slots=op.get(f"/t/{t.slug}/booking-api/slots/",{"service":str(svc.pk),"date":day,"resource":"","party":1}).json()["slots"]
    assert slots and {x["resource"] for x in slots}=={str(bruno.pk)}

def test_first_available_does_not_hide_the_evening(sample):
    t,o,s=sample
    svc,people=salon(t,operators=[f"Op {i:02d}" for i in range(12)])
    with scope(t):
        Service.objects.filter(pk=svc.pk).update(duration=10,interval=10)
        day=(timezone.now()+timedelta(days=3)).astimezone(ZoneInfo(t.timezone)).date()
        slots=available(t,svc.pk,day)
    assert len(slots)==54 and slots[-1]["start"][11:16]=="17:50"  # before: cut at 500 rows, last slot 15:50

def test_limits_answer_the_form_in_json(sample):
    t,o,s=sample
    c=Client()
    ticket=c.get(f"/book/{t.slug}/").context["widget"]["ticket"]
    codes=[c.post(f"/book/{t.slug}/api/submit/",json.dumps(payload(s,hour=9+i)),content_type="application/json",HTTP_ORIGIN="http://localhost:8000",HTTP_X_TDBS_TICKET=ticket) for i in range(6)]
    assert codes[-1].status_code==403 and codes[-1]["Content-Type"]=="application/json" and codes[-1].json()["error"]

def test_shortcode_with_one_service(sample):
    t,o,s=sample
    with scope(t):other=Service.objects.create(tenant=t,location=s.location,name="Altro",kind="capacity",capacity=2,lead_minutes=0)
    widget=Client().get(f"/book/{t.slug}/?service={other.pk}").context["widget"]
    assert [x["name"] for x in widget["services"]]==["Altro"]
    assert Client().get(f"/book/{t.slug}/?service=nonvalido").status_code==200

def test_bad_client_id_in_manual_booking(sample):
    t,o,s=sample
    owner,_=client_as(t)
    assert owner.get(f"/t/{t.slug}/bookings/new/?cliente="+"z"*36).status_code==200

def test_owner_cannot_take_over_an_account_of_another_business(sample):
    t,o,s=sample
    victim=User.objects.create_user("vittima@example.invalid")  # invited by the other business, not yet active
    Membership.objects.create(tenant=o,user=victim,role="owner")
    owner,_=client_as(t)
    data={"action":"invite","email":"vittima@example.invalid","role":"reception","password":"Una-password-lunga-2026!"}
    assert owner.post(f"/t/{t.slug}/members/",data).status_code==403  # 2.13.0: people and permissions are the agency's
    agency,_=client_as(t,staff=True)
    r=agency.post(f"/t/{t.slug}/members/",data).content.decode()
    # not even from here: the account is activated from its own invitation
    assert "altra attività" in r and not Membership.objects.filter(tenant=t,user=victim).exists()
    victim.refresh_from_db();assert not victim.has_usable_password()

def test_agency_needs_a_grant_not_a_membership(sample):
    t,o,s=sample
    c,staff_user=staff_client()
    c.post(f"/t/{t.slug}/members/",{"action":"invite","email":staff_user.email,"role":"owner"})
    assert not Membership.objects.filter(tenant=t,user=staff_user).exists()
    _,owner=client_as(t)
    m=Membership.objects.get(tenant=t,user=owner)
    r=c.post(f"/t/{t.slug}/members/",{"action":"reset","member":m.pk},follow=True).content.decode()
    assert "/invite/" not in r and "Link per la nuova password inviato" in r
    with scope(t):assert Delivery.objects.exists()  # the link goes by email to the owner

def test_first_second_factor_needs_a_fresh_confirmation(sample):
    t,o,s=sample
    c,u=client_as(t)
    session=c.session;session["verified_at"]=wallclock.time()-3600;session.save()
    r=c.post("/security/",{"action":"totp_start"})
    assert r.status_code==302 and r["Location"].startswith("/verify/confirm/")
    r=c.post("/passkey/register/begin/",json.dumps({}),content_type="application/json")
    assert r.status_code==302 and r["Location"].startswith("/verify/confirm/")

def test_origins_cannot_inject_csp():
    from core.wpconnect import valid_origin
    assert valid_origin("https://shop.it https:")=="" and valid_origin("https://x.it;sandbox")=="" and valid_origin("https://Shop.it:8443")=="https://shop.it:8443"

def smtp_tenant(t,**conf):
    with scope(t):
        t.smtp=encrypt(t,{"host":"smtp.example.com","port":587,"mode":"starttls","username":"","password":"","sender":"info@example.com",**conf},"smtp");t.save()
        from core.booking import queue
        return queue(t,{"to":"cliente@example.com","subject":"Prova","body":"Ciao"})

def test_smtp_down_is_retried_not_lost(sample,monkeypatch):
    t,o,s=sample
    d=smtp_tenant(t)
    def refused(*a,**kw):raise ConnectionRefusedError("refused")
    monkeypatch.setattr("core.mail.PinnedSMTP.__init__",refused)
    from core.mail import worker_once
    worker_once()
    with scope(t):d.refresh_from_db();assert d.status=="pending" and d.last_code=="451"  # before: "unknown", never sent

def test_api_send_without_answer_is_not_sent_twice(sample,monkeypatch):
    t,o,s=sample
    d=smtp_tenant(t,method="graph",tenant_id="tid",client_id="cid",client_secret="sec",mailbox="info@example.com")
    monkeypatch.setattr("core.mailapi.graph_token",lambda config:"token")
    calls=[]
    def timeout(req,timeout=0):calls.append(req.full_url);raise TimeoutError("read timed out")
    monkeypatch.setattr("core.mailapi.urllib.request.urlopen",timeout)
    from core.mail import worker_once
    worker_once();worker_once()
    with scope(t):d.refresh_from_db();assert d.status=="unknown" and len(calls)==1

def test_online_booking_finds_the_client_by_phone(sample):
    t,o,s=sample
    owner,u=client_as(t)
    from core.clients import find_or_create
    with scope(t):
        desk,_=find_or_create(t,{"first_name":"Lia","last_name":"","email":"","phone":"+393331112222"})  # added at the desk, no email
        b,_=create_booking(t,payload(s,first_name="Lia",last_name="Bianchi",email="lia@example.invalid",phone="333 111 2222",marketing=True))
        assert b.contact_id==desk.pk and Contact.objects.count()==1
        # the card is not rewritten, and nobody confirms marketing for a card found by phone
        assert desk.data["email"]=="" and not ActionToken.objects.filter(kind="consent").exists()
        again,_=create_booking(t,payload(s,hour=11,email="altra@example.invalid",phone="+39 333 999 0000",request_key=secrets.token_urlsafe(24)))
        assert again.contact_id!=desk.pk  # another email and another phone: another person

def test_two_emails_on_one_phone_stay_two_people(sample):
    t,o,s=sample
    with scope(t):
        a,_=create_booking(t,payload(s,email="mamma@example.invalid"))
        b,_=create_booking(t,payload(s,hour=11,email="figlio@example.invalid",request_key=secrets.token_urlsafe(24)))
        assert a.contact_id!=b.contact_id

def test_client_note_on_every_booking(sample):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):b,_=create_booking(t,payload(s))
    owner.post(f"/t/{t.slug}/contacts/{b.contact_id}/",{"action":"notes","staff_notes":"Allergica al nichel"})
    assert "Allergica al nichel" in owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    viewer,_=member(t,"viewer")
    assert "nichel" not in viewer.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()

def test_minors_book_tattoos_in_person():
    q={x["key"]:x for x in questions("tattoo")}
    assert q["maggiorenne"]["required"] and "genitore" in q["maggiorenne"]["label"]
    assert not any("allerg" in x["label"].lower() for k in ("spa","centro_estetico","tattoo","medico") for x in questions(k))  # no health questions online

def test_passkeys_follow_the_address_of_the_page(sample,settings):
    # the same server by its office name and by its public name: each address keeps its own passkeys
    settings.ALLOWED_HOSTS=["localhost","booking.example.com"]
    t,o,s=sample
    c,u=client_as(t)
    for host in ("localhost","booking.example.com"):
        r=c.post("/passkey/register/begin/",json.dumps({}),content_type="application/json",HTTP_HOST=host)
        assert r.json()["options"]["rp"]["id"]==host
    assert c.post("/passkey/register/begin/",json.dumps({}),content_type="application/json",HTTP_HOST="evil.example").status_code==400

def test_booking_form_accepts_the_address_it_was_opened_with(sample,settings):
    settings.ALLOWED_HOSTS=["localhost","ufficio-pc"]
    t,o,s=sample
    c=Client()
    # past the origin check the request stops at the missing ticket: "Pagina scaduta"
    page=lambda origin,host:"Pagina scaduta" in c.post(f"/book/{t.slug}/api/submit/",data="{}",content_type="application/json",HTTP_ORIGIN=origin,HTTP_HOST=host).content.decode()
    assert page("http://ufficio-pc","ufficio-pc") and page("http://localhost:8000","ufficio-pc")
    assert not page("https://evil.invalid","ufficio-pc") and not page("http://ufficio-pc","localhost")
