"""2.14.0, fase 2: stati completi e reversibili con le richieste da confermare, regole del banco separate da quelle
online con Forza e Senza appuntamento, preavviso dei preset ad appuntamento, origine di ogni prenotazione."""
import json,csv,io
from datetime import datetime,date,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import decrypt
from core.booking import create_booking,move_booking,change_status,available,BookingError,ForceWarning,UNDO_MINUTES
from tests.test_core import sample,payload,client_as
from tests.test_clients import staff_payload

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")

@pytest.fixture
def clock(monkeypatch):
    """Tomorrow at 10:07 in Rome, inside the 9-18 of the sample: "now" stays the same for the whole test."""
    now=[datetime.combine(date.today()+timedelta(days=1),time(10,7),ROME)]
    monkeypatch.setattr(timezone,"now",lambda:now[0])
    return now

def at(day,hour,minute=0):return datetime.combine(day,time(hour,minute),ROME).isoformat()

def salon(t,lead=60):
    with scope(t):
        loc=Location.objects.first()
        svc=Service.objects.create(tenant=t,location=loc,name="Taglio",kind="resource",duration=30,interval=30,lead_minutes=lead)
        marco=Resource.objects.create(tenant=t,location=loc,name="Marco")
        ServiceResource.objects.create(tenant=t,service=svc,resource=marco)
    return svc,marco

def sent(t):
    with scope(t):return [decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in Delivery.objects.filter(channel="email").order_by("created")]

def test_statuses_go_forward_and_back(sample,clock):
    t,o,s=sample
    today=clock[0].date()
    with scope(t):
        b,_=create_booking(t,payload(s,start=at(today,11)),None)
        later,_=create_booking(t,payload(s,email="anna@example.invalid"),None)  # in three days
        with pytest.raises(BookingError,match="non è di oggi"):change_status(t,later,"arrived")
        with pytest.raises(BookingError,match="non consentito"):change_status(t,b,"pending")
        change_status(t,b,"arrived");change_status(t,b,"in_progress")
        assert change_status(t,b,"completed").status=="completed"  # before its time: the client is here
        change_status(t,b,"confirmed")
        with pytest.raises(BookingError,match="non è ancora iniziato"):change_status(t,b,"no_show")
        assert Audit.objects.filter(action="booking.confirmed",target=str(b.pk)).first().detail["from"]=="completed"

def test_undo_within_minutes_where_it_was_made(sample,clock):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):
        b,_=create_booking(t,payload(s),None)
        nine=datetime.fromisoformat(at(clock[0].date(),9))  # this morning, never closed
        Booking.objects.filter(pk=b.pk).update(start=nine,end=nine+timedelta(hours=1),busy_start=nine,busy_end=nine+timedelta(hours=1))
    url=f"/t/{t.slug}/bookings/{b.pk}/"
    # from the dashboard: the quick button goes back there, with the change to take back
    page=owner.get(f"/t/{t.slug}/").content.decode()
    assert "Da chiudere" in page and 'value="completed"' in page
    r=owner.post(url,{"action":"completed","back":"dashboard"});assert r.status_code==302 and r["Location"].endswith(f"/t/{t.slug}/")
    page=owner.get(r["Location"]).content.decode()
    assert "Torna indietro" in page and "ora è <strong>completata</strong>" in page
    owner.post(url,{"action":"undo","back":"dashboard"})
    with scope(t):assert Booking.objects.get(pk=b.pk).status=="confirmed"
    owner.post(url,{"action":"no_show"})
    clock[0]+=timedelta(minutes=UNDO_MINUTES+1)
    assert "Torna indietro" not in owner.get(url).content.decode()
    assert "entro" in owner.post(url,{"action":"undo"},follow=True).content.decode()
    with scope(t):assert Booking.objects.get(pk=b.pk).status=="no_show" and Audit.objects.filter(action="booking.undo").count()==1

def test_cancelled_comes_back_only_if_the_place_is_free(sample,clock):
    t,o,s=sample
    with scope(t):
        Service.objects.filter(pk=s.pk).update(capacity=1)
        b,_=create_booking(t,payload(s,hour=12),None)
        change_status(t,b,"cancelled",reason="weather",note="Grandine, il cliente richiama")
        b.refresh_from_db()
        assert b.snapshot["cancel_reason"]=="weather" and b.data["cancel_note"].startswith("Grandine") and "Grandine" not in b.pii
        other,_=create_booking(t,payload(s,hour=12,email="anna@example.invalid"),None)
        with pytest.raises(BookingError,match="preso"):change_status(t,b,"confirmed")
        change_status(t,other,"cancelled",reason="duplicate")
        assert change_status(t,b,"confirmed").status=="confirmed"
        assert Audit.objects.filter(action="booking.cancelled",target=str(other.pk)).first().detail["reason"]=="duplicate"
    owner,u=client_as(t)
    page=owner.get(f"/t/{t.slug}/bookings/{other.pk}/").content.decode()
    assert "Annullata: prenotazione doppia" in page and "Riporta a confermata" in page
    assert "booking.cancelled · maltempo" in owner.get(f"/t/{t.slug}/audit/").content.decode()

def test_the_clients_link_cancels_with_reason_client(sample):
    t,o,s=sample
    with scope(t):b,_=create_booking(t,payload(s),None)
    Client().post(f"/action/{t.slug}/{b.data['cancel_token']}/")
    with scope(t):assert Booking.objects.get(pk=b.pk).snapshot["cancel_reason"]=="client"

def test_requests_hold_the_place_and_mail_the_client(sample):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):
        Service.objects.filter(pk=s.pk).update(capacity=2,request_above=1)
        s.refresh_from_db()
        ask,_=create_booking(t,payload(s,party=2),None)
        assert ask.status=="pending"
        with pytest.raises(BookingError):create_booking(t,payload(s,email="anna@example.invalid"),None)  # the request holds both places
        assert "10:00" not in [x["start"][11:16] for x in available(t,s.pk,ask.start.astimezone(ROME).date())]
        desk,_=create_booking(t,staff_payload(s,hour=14,party=2),actor=u)
        assert desk.status=="confirmed"  # the desk is never a request
        change_status(t,ask,"confirmed")
        small,_=create_booking(t,payload(s,hour=15,email="anna@example.invalid"),None)
        assert small.status=="confirmed"  # one person: confirmed at once
        big,_=create_booking(t,payload(s,hour=16,party=2,email="bea@example.invalid"),None)
        change_status(t,big,"cancelled",reason="rejected")
    subjects=[m["subject"].split(" · ")[0] for m in sent(t) if m["to"]=="mario@example.invalid"]
    assert subjects==["Richiesta ricevuta","Prenotazione confermata"]
    assert [m["subject"].split(" · ")[0] for m in sent(t) if m["to"]=="bea@example.invalid"]==["Richiesta ricevuta","Richiesta non confermata"]
    assert "/action/" in [m for m in sent(t) if m["subject"].startswith("Richiesta ricevuta")][0]["body"]
    with scope(t):assert Booking.objects.get(pk=big.pk).snapshot["cancel_reason"]=="rejected"

def test_requests_by_rule_and_the_businesss_texts(sample):
    t,o,s=sample
    t.allergens_on=True;t.allergens=["Glutine"];t.business_type="ristorante";t.client_mails_enabled=True;t.client_mails={"requested":{"on":False}};t.save()
    with scope(t):
        Service.objects.filter(pk=s.pk).update(request_allergies=True);s.refresh_from_db()
        plain,_=create_booking(t,payload(s),None)
        ask,_=create_booking(t,payload(s,hour=11,email="anna@example.invalid",allergies=["Glutine"]),None)
        assert plain.status=="confirmed" and ask.status=="pending"
        Service.objects.filter(pk=s.pk).update(request_allergies=False,request_all=True);s.refresh_from_db()
        assert create_booking(t,payload(s,hour=12,email="bea@example.invalid"),None)[0].status=="pending"
    assert not [m for m in sent(t) if m["subject"].startswith("Richiesta ricevuta")]  # switched off by the business

def test_public_page_says_request_and_dashboard_answers(sample):
    t,o,s=sample
    with scope(t):Service.objects.filter(pk=s.pk).update(request_all=True)
    c=Client()
    ticket=c.get(f"/book/{t.slug}/").context["widget"]["ticket"]
    r=c.post(f"/book/{t.slug}/api/submit/",json.dumps(payload(s)),content_type="application/json",HTTP_ORIGIN="http://localhost:8000",HTTP_X_TDBS_TICKET=ticket)
    assert r.status_code==201 and r.json()["pending"] is True
    owner,u=client_as(t)
    page=owner.get(f"/t/{t.slug}/").content.decode()
    assert "Richieste da confermare" in page and 'value="reject"' in page
    with scope(t):b=Booking.objects.get()
    owner.post(f"/t/{t.slug}/bookings/{b.pk}/",{"action":"reject","back":"dashboard"})
    with scope(t):assert Booking.objects.get().status=="cancelled"

def test_desk_books_inside_the_lead_time_the_public_does_not(sample,clock):
    t,o,s=sample
    svc,marco=salon(t,lead=120)
    today=clock[0].date();owner,u=client_as(t)
    with scope(t):
        assert available(t,svc.pk,today)[0]["start"][11:16]=="12:30"
        assert available(t,svc.pk,today,desk=True)[0]["start"][11:16]=="10:15"  # 2.22.1: the desk books every quarter, from now
        with pytest.raises(BookingError,match="Orario non disponibile"):create_booking(t,payload(svc,start=at(today,11),resource=str(marco.pk)),None)
        assert create_booking(t,staff_payload(svc,start=at(today,11),resource=str(marco.pk)),actor=u)[0].origin=="desk"
        # horizon and the people of one booking are the online page's too
        Service.objects.filter(pk=s.pk).update(horizon_days=1,capacity=20,max_party=2)
        far=(clock[0]+timedelta(days=10)).date()
        assert available(t,s.pk,far)==[] and available(t,s.pk,far,desk=True)
        assert create_booking(t,staff_payload(s,start=at(far,10),party=8),actor=u)[0].party==8
    assert owner.get(f"/t/{t.slug}/booking-api/slots/?service={svc.pk}&date={today}").json()["slots"][0]["start"][11:16]=="10:15"
    assert Client().get(f"/book/{t.slug}/api/slots/?service={svc.pk}&date={today}").json()["slots"][0]["start"][11:16]=="12:30"

def test_force_names_what_it_exceeds_and_needs_the_role(sample,clock):
    t,o,s=sample
    svc,marco=salon(t)
    today=clock[0].date()
    owner,u=client_as(t)
    with scope(t):create_booking(t,staff_payload(svc,start=at(today,15),resource=str(marco.pk)),actor=u)
    url=f"/t/{t.slug}/booking-api/submit/"
    data=staff_payload(svc,start=f"{today}T19:30",resource=str(marco.pk),force=True)
    r=owner.post(url,json.dumps(data),content_type="application/json")
    assert r.status_code==409 and r.json()["force"] and "fuori dall'orario di apertura" in r.json()["error"]
    r=owner.post(url,json.dumps({**data,"force_ok":True}),content_type="application/json");assert r.status_code==200
    with scope(t):
        b=Booking.objects.get(start=datetime.fromisoformat(at(today,19,30)))
        assert b.snapshot["forced"]==["fuori dall'orario di apertura"] and Audit.objects.get(action="booking.created",target=str(b.pk)).detail["forced"]
    # over another appointment and in the past, by name
    r=owner.post(url,json.dumps(staff_payload(svc,start=f"{today}T15:00",resource=str(marco.pk),force=True)),content_type="application/json")
    assert "sovrapposta a un altro appuntamento di Marco" in r.json()["error"]
    r=owner.post(url,json.dumps(staff_payload(svc,start=f"{today}T09:00",resource=str(marco.pk),force=True)),content_type="application/json")
    assert "nel passato" in r.json()["error"]
    # "Il primo disponibile" at a free time: nothing to force
    r=owner.post(url,json.dumps(staff_payload(svc,start=f"{today}T16:00",force=True)),content_type="application/json");assert r.status_code==200
    # the role: the operator and the reception do not force, unless the owner says so
    late=json.dumps(staff_payload(svc,start=f"{today}T20:00",resource=str(marco.pk),force=True,force_ok=True))
    for role in ("operator","reception"):
        c,_=client_as(t,role=role)
        assert c.post(url,late,content_type="application/json").status_code==403
    page=owner.get(f"/t/{t.slug}/setup/").content.decode();assert 'name="force_all"' in page
    assert c.post(f"/t/{t.slug}/setup/banco/",{"force_all":"on"}).status_code==403
    owner.post(f"/t/{t.slug}/setup/banco/",{"force_all":"on"})
    assert c.post(url,late,content_type="application/json").status_code==200
    assert 'id="force"' in c.get(f"/t/{t.slug}/bookings/new/").content.decode()

def test_force_lists_full_events_and_moves(sample,clock):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):
        ev=Service.objects.create(tenant=t,location=s.location,name="Serata",kind="event",max_party=5,lead_minutes=0)
        day=clock[0].date()+timedelta(days=2)
        occ=Occurrence.objects.create(tenant=t,service=ev,start=datetime.combine(day,time(11),ROME),end=datetime.combine(day,time(12),ROME),capacity=1)
        create_booking(t,staff_payload(ev,start=occ.start.isoformat()),actor=u)
    q=f"/t/{t.slug}/booking-api/slots/?service={ev.pk}&date={day}"
    assert owner.get(q).json()["slots"]==[] and len(owner.get(q+"&forza=1").json()["slots"])==1
    with scope(t):
        b,_=create_booking(t,staff_payload(ev,start=occ.start.isoformat(),occurrence=str(occ.pk),phone="333 777 8888",force=True,force_ok=True),actor=u)
        assert b.snapshot["forced"]==["oltre i posti disponibili (1)"]
        moved=move_booking(t,create_booking(t,payload(s),None)[0].pk,{"service":str(s.pk),"start":f"{clock[0].date()}T20:00","party":1,"force":True,"force_ok":True},u)
        assert moved.snapshot["forced"]==["fuori dall'orario di apertura"]
        with pytest.raises(ForceWarning):move_booking(t,moved.pk,{"service":str(s.pk),"start":f"{clock[0].date()}T21:00","party":1,"force":True},u)
    c,_=client_as(t,role="reception")
    r=c.post(f"/t/{t.slug}/bookings/{moved.pk}/move-api/",json.dumps({"service":str(s.pk),"start":f"{clock[0].date()}T21:00","party":1,"force":True,"force_ok":True}),content_type="application/json")
    assert r.status_code==403

def test_walk_in_now(sample,clock):
    t,o,s=sample
    svc,marco=salon(t)
    owner,u=client_as(t)
    url=f"/t/{t.slug}/booking-api/submit/"
    page=owner.get(f"/t/{t.slug}/bookings/new/?adesso=1").content.decode()
    assert "Senza appuntamento, adesso" in page and "Inizia adesso" in page
    r=owner.post(url,json.dumps({"service":str(svc.pk),"walkin":True,"party":1,"request_key":"walk-in-request-key-000001"}),content_type="application/json")
    assert r.status_code==200
    with scope(t):
        b=Booking.objects.get()
        assert b.status=="in_progress" and b.origin=="walkin" and b.contact_id is None and b.resource==marco
        assert b.data["first_name"]=="Cliente di passaggio" and b.start==clock[0].replace(second=0,microsecond=0)
    assert "Senza appuntamento" in owner.get(f"/t/{t.slug}/").content.decode()
    # Marco is busy now: without Forza a refusal that says why, with Forza the warning first
    c,_=client_as(t,role="reception")
    data={"service":str(svc.pk),"resource":str(marco.pk),"walkin":True,"party":1,"first_name":"Luca","phone":"333 111 2222","request_key":"walk-in-request-key-000002"}
    r=c.post(url,json.dumps(data),content_type="application/json")
    assert r.status_code==400 and "Adesso non si può: sovrapposta a un altro appuntamento di Marco" in r.json()["error"]
    assert owner.post(url,json.dumps({**data,"force":True}),content_type="application/json").status_code==409
    assert owner.post(url,json.dumps({**data,"force":True,"force_ok":True}),content_type="application/json").status_code==200
    with scope(t):
        luca=Booking.objects.exclude(pk=b.pk).get()
        assert luca.contact and luca.contact.data["phone"]=="+393331112222" and luca.status=="in_progress"
    assert not [m for m in sent(t)]  # nobody to confirm to: the client is here

def test_origin_of_every_path_and_the_tag(sample):
    t,o,s=sample
    owner,u=client_as(t)
    def book(query,**extra):
        c=Client();ticket=c.get(f"/book/{t.slug}/"+query).context["widget"]["ticket"]
        r=c.post(f"/book/{t.slug}/api/submit/",json.dumps(payload(s,**extra)),content_type="application/json",HTTP_ORIGIN="http://localhost:8000",HTTP_X_TDBS_TICKET=ticket)
        with scope(t):return next(b for b in Booking.objects.all() if str(b.pk).startswith(r.json()["reference"]))
    b=book("?src=Instagram");assert (b.origin,b.source)==("public","instagram")
    b=book("?theme=auto",hour=11,email="a@example.invalid");assert (b.origin,b.source)==("wordpress","")
    for i,bad in enumerate(("%3Cscript%3E","a%20b","x"*31,"sito.it")):assert book("?src="+bad,hour=12+i,email=f"n{i}@example.invalid").source==""
    with scope(t):
        assert create_booking(t,staff_payload(s,hour=15),actor=u)[0].origin=="desk"
        old,_=create_booking(t,payload(s,hour=16,email="old@example.invalid"),None);Booking.objects.filter(pk=old.pk).update(origin="",source="")
    rows=list(csv.reader(io.StringIO(owner.get(f"/t/{t.slug}/export/?kind=bookings&profile=generic").content.decode("utf-8-sig"))))
    assert rows[0][-2:]==["Origine","Fonte"] and ["Pagina pubblica","instagram"] in [r[-2:] for r in rows] and ["Banco",""] in [r[-2:] for r in rows] and ["",""] in [r[-2:] for r in rows]
    page=owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    assert "Arrivata da" in page and "Sito WordPress" in page
    assert 'title="Pagina pubblica · instagram"' in owner.get(f"/t/{t.slug}/bookings/?vista=elenco").content.decode()

def test_dashboard_counts_done_only_what_was_closed(sample,clock):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):
        a,_=create_booking(t,payload(s),None);b,_=create_booking(t,payload(s,hour=11,email="anna@example.invalid"),None)
        past=clock[0]-timedelta(days=2)
        Booking.objects.filter(pk__in=[a.pk,b.pk]).update(start=past,end=past+timedelta(hours=1),busy_start=past,busy_end=past+timedelta(hours=1))
        change_status(t,Booking.objects.get(pk=a.pk),"completed")
    stats=owner.get(f"/t/{t.slug}/").context["stats"]["outcome"]
    assert {o["label"]:o["value"] for o in stats}=={"Svolte":1,"Da chiudere":1,"Disdette":0,"Assenze":0}

def test_appointment_presets_book_online_an_hour_ahead(sample):
    from core.profile import apply_profile
    t,o,s=sample
    with scope(t):
        apply_profile(t,"parrucchiere",["Centro"])
        assert set(Service.objects.filter(active=True).values_list("lead_minutes",flat=True))=={60}
        apply_profile(t,"ristorante",["Centro"])
        assert set(Service.objects.filter(active=True).values_list("lead_minutes",flat=True))=={0}

def test_links_per_channel_qr_and_frame(sample):
    t,o,s=sample
    owner,u=client_as(t)
    page=owner.get(f"/t/{t.slug}/setup/").content.decode()
    assert f"/book/{t.slug}/?src=instagram" in page and f"/book/{t.slug}/?src=google" in page and "/static/embed.js" in page and "&lt;iframe" in page
    r=owner.get(f"/t/{t.slug}/setup/qr.png");assert r["Content-Type"]=="image/png" and r.content[:4]==b"\x89PNG"
    c,_=client_as(t,role="reception")
    with scope(t):Membership.objects.filter(user=_).update(can_schedule=False)
    assert c.get(f"/t/{t.slug}/setup/qr.png").status_code==403
