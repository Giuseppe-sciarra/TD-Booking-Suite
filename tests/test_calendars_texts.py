"""2.14.0, fase 11: feed .ics privato per operatore, calendario esterno letto come Occupato, promemoria per SMS
(Twilio, Skebby) e WhatsApp Cloud API con ripiego su SMS, mai di notte, con un budget al giorno."""
import json
from datetime import datetime,timedelta
from urllib.error import HTTPError
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import encrypt
from core.booking import create_booking
from core.client_mails import remind
from core.mail import worker_once
from core import feeds,texts
from core.calendar import blocked
from tests.test_core import sample,payload,client_as
from tests.test_reminders import salon

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")

def booked(t,svc,marco):
    with scope(t):b,_=create_booking(t,payload(svc,resource=str(marco.pk)));return b

def test_operator_feed_without_client_data_and_renewed(sample):
    t,o,s=sample;svc,marco,giulia=salon(t);b=booked(t,svc,marco)
    assert b.resource_id==marco.pk
    with scope(t):marco.feed=7;marco.save()
    url=feeds.link(marco,t.slug).replace("http://localhost:8000","")
    body=Client().get(url).content.decode()
    assert "SUMMARY:Taglio" in body and f"UID:{b.pk}@" in body and "Mario" not in body and "mario@" not in body
    with scope(t):marco.feed_names=True;marco.save()
    assert "Taglio · Mario Rossi" in Client().get(url).content.decode()
    with scope(t):marco.feed=8;marco.save()  # renewed: the old link stops
    assert Client().get(url).status_code==404
    assert Client().get(f"/action/{t.slug}/feed/forged/calendario.ics").status_code==404
    with scope(t):giulia.feed=7;giulia.save()
    assert "Taglio" not in Client().get(feeds.link(giulia,t.slug).replace("http://localhost:8000","")).content.decode()

ICS="""BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
DTSTART:{day}T090000Z\r
DTEND:{day}T100000Z\r
SUMMARY:Dentista privato\r
BEGIN:VALARM\r
DURATION:PT15M\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;TZID=Europe/Rome:{day}T150000\r
DURATION:PT1H\r
TRANSP:TRANSPARENT\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;TZID=Europe/Rome:{day}T160000\r
DTEND;TZID=Europe/Rome:{day}T163000\r
RRULE:FREQ=WEEKLY;COUNT=3\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:{next}\r
DTEND;VALUE=DATE:{after}\r
END:VEVENT\r
END:VCALENDAR\r
"""

def test_outside_calendar_becomes_busy_blocks(sample,monkeypatch):
    t,o,s=sample;svc,marco,giulia=salon(t)
    day=(timezone.now()+timedelta(days=3)).astimezone(ROME).date()
    text=ICS.format(day=f"{day:%Y%m%d}",next=f"{day+timedelta(days=1):%Y%m%d}",after=f"{day+timedelta(days=2):%Y%m%d}")
    spans=feeds.events(text,ROME,timezone.now())
    assert len(spans)==5  # the free one out; the weekly one three times; the alarm does not change the first
    assert spans[0][1]-spans[0][0]==timedelta(hours=1)
    monkeypatch.setattr(feeds,"fetch",lambda url:text)
    with scope(t):
        marco.busy=encrypt(t,"webcal://calendar.example/secret.ics","busy:"+str(marco.pk));marco.save()
        TimeBlock.objects.create(tenant=t,resource=marco,day=day,start="08:00",end="08:30",reason="break")
    assert feeds.refresh()==1 and feeds.refresh()==0  # read once every 15 minutes
    with scope(t):
        rows=TimeBlock.objects.filter(resource=marco,reason="busy")
        assert rows.count()==5 and not any(r.pii for r in rows)
        at=lambda h,m=0:datetime.combine(day,datetime.min.time().replace(hour=h,minute=m),ROME)
        assert blocked(t,at(16,10),at(16,20),resource=marco) and not blocked(t,at(15),at(15,30),resource=marco)
        assert blocked(t,at(23)+timedelta(hours=2),at(23)+timedelta(hours=3),resource=marco)  # the all-day event
        assert not blocked(t,at(16,10),at(16,20),resource=giulia)
        assert TimeBlock.objects.filter(reason="break").exists()  # the owner's own blocks stay
    monkeypatch.setattr(feeds,"fetch",lambda url:(_ for _ in ()).throw(ValueError("Calendario non raggiungibile.")))
    assert feeds.refresh(timezone.now()+timedelta(minutes=16))==0
    with scope(t):
        marco.refresh_from_db();assert marco.busy_error=="Calendario non raggiungibile."
        assert TimeBlock.objects.filter(resource=marco,reason="busy").count()==5  # an error keeps the last ones read
    for bad in ("http://calendar.example/x.ics","https://127.0.0.1/x.ics","https://10.0.0.5/x.ics"):
        with pytest.raises(ValueError):feeds.check(bad)

def test_resource_form_feed_and_busy_url(sample):
    t,o,s=sample;svc,marco,giulia=salon(t)
    c,u=client_as(t)
    from core.forms import ResourceForm
    with scope(t):
        f=ResourceForm({"name":"Marco","location":str(marco.location_id),"active":"on","services":[str(svc.pk)],"feed_on":"on","busy_url":"webcal://calendar.example/s.ics"},instance=marco,tenant=t)
        assert f.is_valid(),f.errors
        f.save();marco.refresh_from_db();first=marco.feed
        assert first and marco.busy and "calendar.example" not in marco.busy and feeds.busy_url(marco)=="webcal://calendar.example/s.ics"
    page=c.get(f"/t/{t.slug}/catalog/resources/{marco.pk}/").content.decode()
    assert feeds.link(marco,t.slug) in page and "webcal://calendar.example/s.ics" in page and "Cambia il link del calendario" in page
    with scope(t):
        f=ResourceForm({"name":"Marco","location":str(marco.location_id),"active":"on","services":[str(svc.pk)],"busy_url":"ftp://x"},instance=marco,tenant=t)
        assert not f.is_valid()
        f=ResourceForm({"name":"Marco","location":str(marco.location_id),"active":"on","services":[str(svc.pk)]},instance=marco,tenant=t)
        assert f.is_valid();f.save();marco.refresh_from_db();assert marco.feed==0 and marco.busy==""

class Sent:
    def __init__(self,fail=None):self.calls=[];self.fail=fail or {}
    def __call__(self,request,timeout=None):
        self.calls.append((request.full_url,dict(request.header_items()),request.data))
        code=self.fail.get(request.full_url.split("/")[2])
        if code:raise HTTPError(request.full_url,code,"no",{},None)
        return self
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self,n=-1):return b"{}"

def configure(t,**conf):
    t.texts=encrypt(t,conf,"texts");t.save()

def test_reminder_by_sms_once_with_budget_and_quiet_night(sample,monkeypatch):
    t,o,s=sample;svc,marco,giulia=salon(t)
    configure(t,provider="twilio",user="AC"+"0"*32,secret="tok",sender="+390612345")
    b=booked(t,svc,marco)
    assert remind(b.start-timedelta(hours=23))==1
    with scope(t):d=Delivery.objects.get(channel="sms");assert d.status=="pending"
    sent=Sent();monkeypatch.setattr(texts,"urlopen",sent)
    night=datetime(2026,11,3,3,0,tzinfo=ROME)
    assert texts.later(t,night)==datetime(2026,11,3,7,0,tzinfo=ROME) and texts.later(t,night.replace(hour=7)) is None
    monkeypatch.setattr(texts,"later",lambda tenant:None)
    t.texts_daily_limit=0;t.save()
    worker_once()
    assert not sent.calls  # no budget left: it waits
    t.texts_daily_limit=10;t.save()
    worker_once()
    url,headers,data=sent.calls[0]
    assert url.endswith("/Accounts/AC"+"0"*32+"/Messages.json") and headers["Authorization"].startswith("Basic ")
    assert b"To=%2B393331234567" in data and b"Taglio" in data
    with scope(t):d.refresh_from_db();assert d.status=="accepted" and d.channel=="sms"
    assert remind(b.start-timedelta(hours=22))==0  # once

def test_whatsapp_falls_back_to_sms_and_waits_when_busy(sample,monkeypatch):
    t,o,s=sample
    p={"to":"+393331234567","body":"Ciao","params":["Mario","Taglio","domani","Salone"]}
    conf=dict(provider="skebby",user="uk",secret="at",sender="Salone",wa_phone="1234567",wa_token="E"*40,wa_template="promemoria",wa_lang="it")
    configure(t,**conf);assert texts.channel(texts.config(t))=="whatsapp"
    sent=Sent({"graph.facebook.com":400});monkeypatch.setattr(texts,"urlopen",sent)
    assert texts.deliver(t,"whatsapp",p)=="sms"
    wa=json.loads(sent.calls[0][2]);sms=json.loads(sent.calls[1][2])
    assert wa["to"]=="393331234567" and wa["template"]["name"]=="promemoria" and [x["text"] for x in wa["template"]["components"][0]["parameters"]][1]=="Taglio"
    assert sent.calls[1][0]=="https://api.skebby.it/API/v1.0/REST/sms" and sms["recipient"]==["+393331234567"] and sent.calls[1][1]["Access_token"]=="at"
    sent=Sent({"graph.facebook.com":503});monkeypatch.setattr(texts,"urlopen",sent)
    with pytest.raises(texts.TextError) as ex:texts.deliver(t,"whatsapp",p)
    assert ex.value.temporary and len(sent.calls)==1  # busy: retried later, no SMS meanwhile
    configure(t,provider="aruba",user="uk",secret="at",sender="Salone");sent=Sent();monkeypatch.setattr(texts,"urlopen",sent)
    assert texts.deliver(t,"sms",p)=="sms" and sent.calls[0][0]=="https://smspanel.aruba.it/API/v1.0/REST/sms" and json.loads(sent.calls[0][2])["message_type"]=="N"
    with pytest.raises(ValueError):texts.clean({"provider":"twilio","user":"AC12","secret":"x","sender":"Salone"},{})
    assert texts.clean({"provider":"","wa_phone":""},{})=={}

def test_texts_page_only_for_the_agency(sample):
    t,o,s=sample
    owner,_=client_as(t)
    assert owner.get(f"/t/{t.slug}/messaggi/").status_code==403
    agency,_=client_as(o,staff=True)
    assert agency.get(f"/t/{t.slug}/messaggi/").status_code==200
    r=agency.post(f"/t/{t.slug}/messaggi/",{"action":"save","provider":"skebby","user":"uk","secret":"at","sender":"Salone","texts_daily_limit":"50"})
    assert r.status_code==302
    t.refresh_from_db();assert t.texts_daily_limit==50 and "Salone" not in t.texts and texts.config(t)["provider"]=="skebby"
    agency.post(f"/t/{t.slug}/messaggi/",{"action":"save","provider":"skebby","user":"uk","secret":"","sender":"Salone","texts_daily_limit":"50"})
    t.refresh_from_db();assert texts.config(t)["secret"]=="at"  # an empty key keeps the saved one
