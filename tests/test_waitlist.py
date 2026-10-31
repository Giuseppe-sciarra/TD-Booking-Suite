"""2.14.0, fase 6: lista d'attesa. Avvisami se si libera dalla pagina online e dal banco, l'avviso a ogni modo in cui si
libera un posto, una volta sola per orario, il link che apre la pagina già sul giorno, la coda con il posto tenuto,
scadenza e privacy."""
import json,re,secrets
from datetime import datetime,date,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import decrypt
from core.booking import create_booking,move_booking,change_status,action_token,available,BookingError
from core.waitlist import add,run,OWN
from core.privacy import export_contact,erase_contact,apply_retention
from tests.test_core import client_as

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")
DAY=lambda:(timezone.now()+timedelta(days=3)).astimezone(ROME).date()

@pytest.fixture
def salon():
    """One operator, open only 9-10: one appointment fills the day."""
    t=Tenant.objects.create(slug="attesa",name="Salone Attesa")
    with scope(t):
        loc=Location.objects.create(tenant=t,name="Centro",phone="06 1234567")
        for d in range(7):Schedule.objects.create(tenant=t,weekday=d,start=time(9),end=time(10))
        svc=Service.objects.create(tenant=t,location=loc,name="Taglio",kind="resource",duration=60,interval=60,lead_minutes=60)
        marco=Resource.objects.create(tenant=t,location=loc,name="Marco")
        ServiceResource.objects.create(tenant=t,service=svc,resource=marco)
    return t,svc,marco

def book(t,svc,day=None,email="mario@example.invalid",**extra):
    data={"service":str(svc.pk),"start":datetime.combine(day or DAY(),time(9),ROME).isoformat(),"party":1,"first_name":"Mario","last_name":"Rossi",
        "email":email,"phone":"+393331234567","request_key":secrets.token_urlsafe(24),**extra}
    with scope(t):return create_booking(t,data)[0]

def waiting(t,svc,email="giulia@example.invalid",**extra):
    data={"service":str(svc.pk),"first":DAY().isoformat(),"first_name":"Giulia","last_name":"Bianchi","email":email,"phone":"","consent":True,**extra}
    with scope(t):return add(t,data)[0]

def offers(t):
    with scope(t):out=[decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in Delivery.objects.filter(channel="email").order_by("created")]
    return [m for m in out if m["subject"].startswith("Si è liberato")]

def public_ticket(c,t,query=""):return c.get(f"/book/{t.slug}/"+query).context["widget"]["ticket"]

def test_full_day_offers_the_waiting_list_and_records_consent(salon):
    t,svc,_=salon;book(t,svc)
    c=Client();page=c.get(f"/book/{t.slug}/")
    assert b'id="wait-box"' in page.content and page.context["widget"]["wait"]==f"/book/{t.slug}/api/attesa/"
    with scope(t):assert available(t,svc.pk,DAY())==[]  # the day is full: the page shows "Tutto pieno" and the box
    body={"service":str(svc.pk),"first":DAY().isoformat(),"first_name":"Giulia","last_name":"","email":"Giulia@Example.invalid","phone":"","consent":True}
    post=lambda b,ticket:c.post(f"/book/{t.slug}/api/attesa/",json.dumps(b),content_type="application/json",HTTP_ORIGIN="http://localhost:8000",HTTP_X_TDBS_TICKET=ticket)
    ticket=public_ticket(c,t)
    assert post({**body,"consent":False},ticket).status_code==400  # the box for the notice is required
    assert post({**body,"email":""},ticket).status_code==400  # the email is where the notice goes
    assert c.post(f"/book/{t.slug}/api/attesa/",json.dumps(body),content_type="application/json",HTTP_ORIGIN="https://evil.invalid",HTTP_X_TDBS_TICKET=ticket).status_code==403
    assert post(body,ticket).status_code==201 and post(body,ticket).status_code==201
    with scope(t):
        e=WaitEntry.objects.get()  # the same client on the same days keeps one entry
        assert "giulia" not in e.pii and e.data["email"]=="giulia@example.invalid" and e.data["phone"]==""
        assert e.snapshot["consent"]["channel"]=="public" and len(e.snapshot["consent"]["text_sha256"])==64
        assert e.contact.data["email"]=="giulia@example.invalid" and e.first==e.last==DAY()

def test_no_waiting_list_on_a_closed_day(salon):
    # the page offered "Avvisami se si libera" also on the days the business is closed: nothing can free up there
    t,svc,_=salon;book(t,svc);c=Client()
    slots=lambda:c.get(f"/book/{t.slug}/api/slots/",{"service":str(svc.pk),"date":DAY().isoformat()}).json()
    assert slots()=={"slots":[],"closed":False}  # full: the box is offered
    with scope(t):ExceptionDay.objects.create(tenant=t,date=DAY(),closed=True)
    assert slots()=={"slots":[],"closed":True}

def cancel_desk(t,b):
    with scope(t):change_status(t,b,"cancelled",reason="business")
def cancel_link(t,b):
    assert Client().post(f"/action/{t.slug}/{b.data['cancel_token']}/").status_code==200
def decline(t,b):
    with scope(t):token=action_token(t,"decline",b.contact,b,days=4)
    assert Client().post(f"/action/{t.slug}/{token}/").status_code==200
def unconfirmed(t,b):
    from core.client_mails import unconfirmed as auto
    with scope(t):
        b.snapshot["asked"]=(b.start-timedelta(hours=30)).isoformat();b.save()
        assert auto(t,12,b.start-timedelta(hours=6))==1
def rejected(t,b):
    with scope(t):
        b.status="pending";b.save()
        change_status(t,b,"cancelled",reason="rejected")
def moved(t,b):
    with scope(t):move_booking(t,b.pk,{"service":str(b.service_id),"start":datetime.combine(DAY()+timedelta(days=1),time(9),ROME).isoformat(),"party":1},None)
@pytest.mark.parametrize("free_it",[cancel_desk,cancel_link,decline,unconfirmed,rejected,moved])
def test_every_way_of_freeing_a_place_tells_the_waiting_list(salon,free_it):
    t,svc,_=salon;b=book(t,svc);e=waiting(t,svc)
    assert run()==0 and offers(t)==[]  # nothing freed yet
    with scope(t):b=Booking.objects.select_related("service","contact","location").get(pk=b.pk)
    free_it(t,b)
    assert run()==1
    (mail,)=offers(t)
    assert mail["to"]=="giulia@example.invalid" and "Ciao Giulia" in mail["body"] and "alle 09:00" in mail["body"] and "lo prende chi prenota per primo" in mail["body"]
    assert f"/book/{t.slug}/?attesa=" in mail["body"] and "Prenota" in mail["html"]
    assert run()==0  # once per free time

@pytest.mark.parametrize("what",["block","closure"])
def test_a_pause_or_a_closure_removed_tells_the_waiting_list(salon,what):
    t,svc,marco=salon;e=waiting(t,svc)
    owner,_=client_as(t)
    with scope(t):
        if what=="block":
            item=TimeBlock.objects.create(tenant=t,resource=marco,day=DAY(),start=time(9),end=time(10))
            url=f"/t/{t.slug}/agenda/blocca/{item.pk}/";data={"remove":"1"}
        else:
            item=ExceptionDay.objects.create(tenant=t,date=DAY(),closed=True)
            url=f"/t/{t.slug}/catalog/exceptions/{item.pk}/remove/";data={}
        assert available(t,svc.pk,DAY())==[]
    assert owner.post(url,data).status_code==302 and run()==1 and len(offers(t))==1

def test_once_per_free_time_and_none_in_the_past_or_inside_the_lead_time(salon,monkeypatch):
    t,svc,_=salon;b=book(t,svc);waiting(t,svc)
    cancel_desk(t,b);assert run()==1
    again=book(t,svc,email="anna@example.invalid");cancel_desk(t,again)
    assert run()==0 and len(offers(t))==1  # the same 9:00 again: not told twice
    # the day comes: inside the lead time (60 minutes before) the place is not offered any more
    later=book(t,svc,email="luca@example.invalid")
    start=datetime.combine(DAY(),time(9),ROME)
    monkeypatch.setattr(timezone,"now",lambda:start-timedelta(minutes=30))
    with scope(t):WaitEntry.objects.update(snapshot={})  # even forgetting what it was told
    cancel_desk(t,later);assert run()==0

def test_the_link_opens_the_day_and_the_booking_closes_the_entry(salon):
    t,svc,marco=salon;b=book(t,svc);e=waiting(t,svc,resource=str(marco.pk));cancel_desk(t,b);run()
    link=re.search(r"(/book/\S+)",offers(t)[0]["body"])[1]
    c=Client();page=c.get(link);w=page.context["widget"]
    assert w["prefill"]["date"]==DAY().isoformat() and w["prefill"]["service"]==str(svc.pk) and w["prefill"]["resource"]==str(marco.pk)
    assert w["prefill"]["contact"]["email"]=="giulia@example.invalid" and "attesa=" in w["slots"]
    slots=c.get(w["slots"]+f"&service={svc.pk}&date={DAY()}&party=1").json()["slots"]
    assert [s["start"][11:16] for s in slots]==["09:00"]
    data={"service":str(svc.pk),"resource":str(marco.pk),"start":slots[0]["start"],"party":1,"first_name":"Giulia","last_name":"Bianchi","email":"giulia@example.invalid","phone":"+393339998877","request_key":secrets.token_urlsafe(24)}
    r=c.post(f"/book/{t.slug}/api/submit/",json.dumps(data),content_type="application/json",HTTP_ORIGIN="http://localhost:8000",HTTP_X_TDBS_TICKET=w["ticket"])
    assert r.status_code==201
    with scope(t):assert WaitEntry.objects.get(pk=e.pk).status=="done"
    assert Client().get(link).context["widget"].get("prefill") is None  # done: the link opens the plain page
    assert Client().get(f"/book/{t.slug}/?attesa=forged").status_code==200

def test_queue_keeps_the_place_for_the_first_then_the_next(salon):
    t,svc,marco=salon;t.wait_mode="queue";t.wait_minutes=15;t.save()
    b=book(t,svc);first=waiting(t,svc);second=waiting(t,svc,email="anna@example.invalid")
    cancel_desk(t,b)
    now=timezone.now();assert run(now)==1
    (mail,)=offers(t);assert mail["to"]=="giulia@example.invalid" and "tenuto per te fino alle" in mail["body"]
    with scope(t):
        assert WaitEntry.objects.get(pk=first.pk).held_until and available(t,svc.pk,DAY())==[]  # kept: not free for the others
        token=OWN.set(first.pk)
        try:assert len(available(t,svc.pk,DAY()))==1  # ...but free on the page of its link
        finally:OWN.reset(token)
    with pytest.raises(BookingError):book(t,svc,email="luca@example.invalid")
    assert run(now+timedelta(minutes=5))==0  # still his
    assert run(now+timedelta(minutes=16))==1  # not taken in time: to the next one
    assert [m["to"] for m in offers(t)]==["giulia@example.invalid","anna@example.invalid"]
    with scope(t):
        assert not WaitEntry.objects.get(pk=first.pk).held_until and WaitEntry.objects.get(pk=second.pk).held_until
        mine=create_booking(t,{"service":str(svc.pk),"start":datetime.combine(DAY(),time(9),ROME).isoformat(),"party":1,"first_name":"Anna","last_name":"Verdi",
            "email":"anna@example.invalid","phone":"+393331112222","request_key":secrets.token_urlsafe(24)},wait=str(second.pk))[0]
        assert mine.status=="confirmed" and WaitEntry.objects.get(pk=second.pk).status=="done" and not WaitEntry.objects.get(pk=second.pk).held_until

def test_desk_list_adds_contacts_and_closes(salon):
    t,svc,marco=salon;owner,_=client_as(t)
    r=owner.post(f"/t/{t.slug}/attesa/",{"action":"add","first_name":"Pino","phone":"333 555 6666","service":str(svc.pk),"first":DAY().isoformat(),"last":(DAY()+timedelta(days=2)).isoformat(),"time_from":"09:00","party":"1"})
    assert r.status_code==302
    with scope(t):e=WaitEntry.objects.get();assert e.data["phone"]=="+393335556666" and e.snapshot["consent"]["channel"]=="desk" and e.time_from==time(9)
    page=owner.get(f"/t/{t.slug}/attesa/")
    assert b"Pino" in page.content and b"tel:+393335556666" in page.content and b"wa.me/393335556666" in page.content
    viewer,_=client_as(t,role="viewer")
    masked=viewer.get(f"/t/{t.slug}/attesa/");assert masked.status_code==200 and b"+393335556666" not in masked.content
    assert viewer.post(f"/t/{t.slug}/attesa/",{"action":"remove","entry":str(e.pk)}).status_code==403
    assert b'id="attesa"' in owner.get(f"/t/{t.slug}/").content  # the count on the dashboard
    assert owner.post(f"/t/{t.slug}/attesa/",{"action":"done","entry":str(e.pk)}).status_code==302
    with scope(t):assert WaitEntry.objects.get().status=="done"

def test_expired_entries_go_and_privacy_includes_them(salon):
    t,svc,_=salon;e=waiting(t,svc)
    with scope(t):
        c=e.contact
        assert export_contact(t,c)["waiting_list"][0]["data"]["email"]=="giulia@example.invalid"
        assert apply_retention(t,now=timezone.now())["waits"]==0
        assert apply_retention(t,now=timezone.now()+timedelta(days=5))["waits"]==1
        apply_retention(t,apply=True,now=timezone.now()+timedelta(days=5));assert not WaitEntry.objects.exists()
    e=waiting(t,svc,email="anna@example.invalid")
    with scope(t):
        erase_contact(t,e.contact,None);assert not WaitEntry.objects.exists()

def test_blocked_client_cannot_wait_online(salon):
    t,svc,_=salon;e=waiting(t,svc)
    with scope(t):Contact.objects.filter(pk=e.contact_id).update(online_blocked=True)
    with pytest.raises(BookingError,match="contatta l'attività: 06 1234567"):waiting(t,svc,last=(DAY()+timedelta(days=1)).isoformat())
