"""2.14.0, fase c2: canale preferito del cliente, testi SMS/WhatsApp del titolare, esiti di consegna letti da Twilio e
Skebby, calendari Google scelti e modifiche fatte su Google segnalate, API e webhook al titolare con indirizzo fissato,
testi in inglese, cambio di operatore dal link Gestisci. Tutto l'HTTP è finto."""
import json
from datetime import datetime,timedelta,time
from unittest import mock
from zoneinfo import ZoneInfo
import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import decrypt,encrypt
from core.booking import create_booking
from core import gcal,texts,api
from tests.test_core import sample,payload,client_as
from tests.test_reminders import salon
from tests.test_calendars_texts import booked,configure,Sent
from tests.test_google_calendar import google,connect,Answer
from tests.test_client_mails import to_clients

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")
WA=dict(provider="twilio",user="AC"+"0"*32,secret="tok",sender="Salone",wa_phone="1234567",wa_token="E"*40,wa_template="promemoria",wa_lang="it")

def queued(t,b,kind="reminder"):
    with scope(t):b=Booking.objects.get(pk=b.pk);d=texts.remind(t,b,kind);return d and (d.channel,decrypt(t,d.payload,"delivery:"+str(d.pk)),d.booking_id)

def test_client_channel_and_business_words(sample):
    t,o,s=sample;svc,marco,giulia=salon(t);configure(t,**WA)
    with scope(t):b,_=create_booking(t,payload(svc,resource=str(marco.pk),channel="sms"))
    assert b.snapshot["channel"]=="sms" and b.contact.channel=="sms"
    ch,p,bid=queued(t,b);assert ch=="sms" and bid==b.pk and "Taglio" in p["body"]
    with scope(t):Booking.objects.filter(pk=b.pk).update(snapshot={**b.snapshot,"channel":""});Contact.objects.filter(pk=b.contact_id).update(channel="email")
    assert queued(t,b) is None  # only emails for him
    with scope(t):Contact.objects.filter(pk=b.contact_id).update(channel="")
    t.client_mails={"texts":{"sms":"Ciao {nome}, {ora}","wa":"{servizio} per {nome} da {attivita}"}};t.save()
    ch,p,_=queued(t,b);assert ch=="whatsapp" and p["body"].startswith("Ciao Mario, ") and p["params"]==["Taglio","Mario","Salone Test"]
    with pytest.raises(ValueError):texts.clean_texts({"text_sms":"Ciao {cognome}"})
    assert texts.clean_texts({"text_sms":texts.TEXT,"text_wa":""})=={}

def test_notifications_page_keeps_sms_words_with_the_emails(sample):
    t,o,s=sample;configure(t,**WA);c,_=client_as(t)
    from core.client_mails import DEFAULTS
    post={"action":"client_mails","text_sms":"Promemoria {servizio} {quando}","reminder_hours":"24","reminder2_hours":"2","arrival_hours":"48","review_hours":"3","reminder_autocancel":"0"}
    with mock.patch("core.platform.fresh",lambda r:None):c.post(f"/t/{t.slug}/notifications/",post)
    t.refresh_from_db();assert t.client_mails["texts"]=={"sms":"Promemoria {servizio} {quando}"}
    assert "data-sms-count" in c.get(f"/t/{t.slug}/notifications/").content.decode()

class Status(Sent):
    def __init__(self,answers):super().__init__();self.answers=answers
    def __call__(self,request,timeout=None):
        super().__call__(request,timeout);self.body=next(v for k,v in self.answers.items() if k in request.full_url);return self
    def read(self,n=-1):return json.dumps(self.body).encode()

def test_sms_id_kept_and_delivery_read_from_twilio_and_skebby(sample,monkeypatch):
    from core.mail import worker_once
    t,o,s=sample;svc,marco,giulia=salon(t);configure(t,provider="twilio",user="AC"+"0"*32,secret="tok",sender="Salone")
    b=booked(t,svc,marco);queued(t,b)
    monkeypatch.setattr(texts,"later",lambda tenant:None)
    sid="SM"+"a"*32;monkeypatch.setattr(texts,"urlopen",Status({"Messages.json":{"sid":sid,"status":"queued"}}))
    worker_once()
    with scope(t):d=Delivery.objects.get(channel="sms");assert d.status=="accepted" and d.ref==sid and d.booking_id==b.pk
    sent=Status({sid:{"status":"sent"}});monkeypatch.setattr(texts,"urlopen",sent)
    assert texts.receipts()==0 and sent.calls[0][0].endswith(f"/Messages/{sid}.json") and sent.calls[0][2] is None
    monkeypatch.setattr(texts,"urlopen",Status({sid:{"status":"delivered"}}))
    assert texts.receipts()==1 and texts.receipts()==0
    c,_=client_as(t);page=c.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    assert "SMS del" in page and "consegnato" in page
    configure(t,provider="skebby",user="uk",secret="at",sender="Salone")
    with scope(t):Delivery.objects.filter(pk=d.pk).update(receipt="",ref="12345")
    monkeypatch.setattr(texts,"urlopen",Status({"/sms/12345":{"recipients":[{"status":"UNKNRCPT"}]}}))
    assert texts.receipts()==1
    with scope(t):assert Delivery.objects.get(pk=d.pk).receipt=="failed"

def test_google_calendars_chosen_and_changes_in_google_warned(sample,google):
    t,o,s=sample;svc,marco,giulia=salon(t);b=booked(t,svc,marco)
    cals=[{"id":"primary@gmail.com","summary":"Marco","accessRole":"owner"},{"id":"team@group.calendar.google.com","summary":"Salone","accessRole":"writer"},{"id":"feste@holiday","summary":"Feste","accessRole":"reader"}]
    real=google.__class__.__call__
    def call(self,req,timeout=None):
        if "calendarList" in req.full_url:return Answer({"items":cals})
        return real(self,req,timeout)
    google.__class__.__call__=call
    try:
        connect(t,marco);now=timezone.now();gcal.refresh(now)
        with scope(t):marco.refresh_from_db();st=gcal.state(marco)
        assert [c[0] for c in st["calendars"]]==[c["id"] for c in cals] and st["calendars"][2][2] is False
        with pytest.raises(ValueError):gcal.choose(marco,"primary","feste@holiday")  # read only
        with scope(t):gcal.choose(marco,"feste@holiday","team@group.calendar.google.com")
        gcal.refresh(now+timedelta(minutes=5))
        posts=google.made("POST");deletes=google.made("DELETE")
        assert "/calendars/team%40group.calendar.google.com/events" in posts[-1][1] and "/calendars/primary/events/" in deletes[-1][1]
        gets=[c[1] for c in google.made("GET")];assert any("/calendars/feste%40holiday/events" in u for u in gets)
        eid=gcal.event_id(t,b.pk,marco);later=(b.start+timedelta(hours=2)).isoformat()
        google.changes=[{"id":eid,"status":"confirmed","start":{"dateTime":later},"end":{"dateTime":(b.end+timedelta(hours=2)).isoformat()}}]
        gcal.refresh(now+timedelta(minutes=10))
        with scope(t):b.refresh_from_db();assert b.snapshot["google"]=="moved:"+later and b.start<datetime.fromisoformat(later)  # warned, not applied
        page=client_as(t)[0].get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode();assert "Spostata su Google Calendar" in page
        google.changes=[{"id":eid,"status":"cancelled"}];gcal.refresh(now+timedelta(minutes=15))
        with scope(t):b.refresh_from_db();assert b.snapshot["google"]=="deleted" and b.status=="confirmed"
        google.changes=[{"id":eid,"status":"confirmed","start":{"dateTime":b.start.isoformat()},"end":{"dateTime":b.end.isoformat()}}];gcal.refresh(now+timedelta(minutes=20))
        with scope(t):b.refresh_from_db();assert "google" not in b.snapshot
    finally:google.__class__.__call__=real

def test_api_for_the_owner_when_allowed_and_webhook_pinned(sample):
    t,o,s=sample;c,_=client_as(t)
    with mock.patch("core.identity.fresh",lambda r:None):
        assert c.get(f"/t/{t.slug}/api/").status_code==403
        t.api_enabled=True;t.save();assert c.get(f"/t/{t.slug}/api/").status_code==200
        assert client_as(t,role="reception")[0].get(f"/t/{t.slug}/api/").status_code==403
    assert any(isinstance(h,api.PinnedHandler) for h in api.OPENER.handlers)
    with pytest.raises(ValueError):api.Pinned("localhost",443,timeout=2).connect()  # resolves to a local address: refused at connect
    with scope(t):t.webhook=encrypt(t,{"url":"https://hooks.example.com/x","secret":"s"},"webhook");t.save()
    with mock.patch("core.mail.public_addresses",side_effect=[[1],ValueError("locale")]),pytest.raises(api.WebhookError) as ex:api.send_webhook(t,{"event":"booking.created"})
    assert ex.value.code=="webhook_address"

def test_english_words_of_the_business(sample):
    t,o,s=sample
    t.consent_text_en="I agree to receive news by email.";t.privacy_text_en="We use your data for the booking.";t.client_mails_enabled=True
    t.client_mails={"confirmed":{"subject":"Ci vediamo","body":"Testo nostro","subject_en":"See you · {attivita}","body_en":"Our text {servizio}"}};t.save()
    with scope(t):s.name_en="Haircut";s.save()
    c=Client();r=c.get(f"/book/{t.slug}/?lang=en");page=r.content.decode()
    assert "I agree to receive news" in page and "We use your data" in page and r.context["widget"]["services"][0]["name"]=="Haircut"
    assert Client().get(f"/book/{t.slug}/").context["widget"]["services"][0]["name"]==s.name
    c.post(f"/book/{t.slug}/api/submit/?lang=en",payload(s,marketing=True),content_type="application/json",HTTP_ORIGIN=settings.PUBLIC_ORIGIN,HTTP_X_TDBS_TICKET=r.context["widget"]["ticket"])
    m=to_clients(t)[0];assert m["subject"]=="See you · Salone Test" and m["body"].startswith("Our text Haircut")
    with scope(t):b=Booking.objects.get();assert b.snapshot["consent_request"]["text"]==t.consent_text_en

def test_manage_link_changes_operator_same_service(sample):
    t,o,s=sample;svc,marco,giulia=salon(t)
    day=(timezone.now()+timedelta(days=3)).astimezone(ROME).date()
    with scope(t):b,_=create_booking(t,payload(svc,resource=str(marco.pk),start=datetime.combine(day,time(10),ROME).isoformat()))
    base=f"/action/{t.slug}/{b.data['cancel_token']}/";c=Client()
    assert '"pick_resource": true' in c.get(base+"sposta/").content.decode()
    slots=c.get(base+"sposta/orari/",{"date":day.isoformat(),"resource":str(giulia.pk)}).json()["slots"]
    assert slots and {x["resource"] for x in slots}=={str(giulia.pk)}
    new=datetime.combine(day,time(15),ROME)
    r=c.post(base+"sposta/",{"start":new.isoformat(),"resource":str(giulia.pk)},content_type="application/json",HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)
    assert r.status_code==200
    with scope(t):b.refresh_from_db();assert b.resource_id==giulia.pk and b.service_id==svc.pk and b.start==new
    with scope(t):Service.objects.filter(pk=svc.pk).update(client_picks=False)
    r=c.post(base+"sposta/",{"start":(new+timedelta(hours=1)).isoformat(),"resource":str(marco.pk)},content_type="application/json",HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)
    with scope(t):b.refresh_from_db();assert b.resource_id==giulia.pk  # the business picks the operator: he keeps his
