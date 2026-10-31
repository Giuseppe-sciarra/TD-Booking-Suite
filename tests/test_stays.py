"""2.14.0, fase 9: strutture ricettive. Calendari .ics per alloggio in uscita e dai portali in entrata come chiusure,
CIN e CIR, regole per periodo, la giornata con pulizie e colazioni, ospiti cifrati al banco e dal check-in online,
file Alloggiati Web e Ross1000, imposta di soggiorno."""
import secrets
from datetime import datetime,date,time,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import decrypt
from core.booking import create_booking,BookingError
from core import stays
from tests.test_core import client_as

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")
def today():return timezone.now().astimezone(ROME).date()

@pytest.fixture
def bnb():
    t=Tenant.objects.create(slug="bnb",name="B&B Aurora",business_type="hotel")
    with scope(t):
        loc=Location.objects.create(tenant=t,name="Aurora",cin="IT058091C2ABCDEFGH",cir="058091-BB-00001",tax_rate=Decimal("6"),tax_nights=10,tax_age=10,ross_code="A00001")
        for d in range(7):Schedule.objects.create(tenant=t,weekday=d,start=time(0),end=time(23,59))
        svc=Service.objects.create(tenant=t,location=loc,name="Doppia",kind="day",lead_minutes=0,min_days=1,max_days=30,max_party=4,checkin=time(15),checkout=time(10))
        units=[Resource.objects.create(tenant=t,location=loc,name=n,capacity=4) for n in ("Camera 1","Camera 2")]
        for u in units:ServiceResource.objects.create(tenant=t,service=svc,resource=u)
    return t,svc,units

def stay(t,svc,first,nights,party=2,**extra):
    data={"service":str(svc.pk),"start":datetime.combine(first,time(15),ROME).isoformat(),"end_date":(first+timedelta(days=nights)).isoformat(),"party":party,
        "first_name":"Mario","last_name":"Rossi","email":"mario@example.invalid","phone":"+393331234567","request_key":secrets.token_urlsafe(24),**extra}
    with scope(t):return create_booking(t,data)[0]

GUESTS={"g0-kind":"17","g0-last_name":"Rossì","g0-first_name":"Mario","g0-sex":"M","g0-birth_date":"1973-03-13","g0-birth_place":"Roma","g0-citizenship":"Italia",
    "g0-residence":"Roma","g0-doc_type":"IDELE","g0-doc_number":"CA12345AB","g0-doc_place":"Roma",
    "g1-kind":"19","g1-last_name":"Müller","g1-first_name":"Anna","g1-sex":"F","g1-birth_date":"2018-07-21","g1-birth_place":"Germania","g1-citizenship":"Germania","g1-residence":"Germania"}

def test_type_assigns_a_free_unit_and_rules_for_a_period(bnb):
    t,svc,units=bnb;first=today()+timedelta(days=10)
    a=stay(t,svc,first,2);b=stay(t,svc,first,2)
    assert {a.resource_id,b.resource_id}=={u.pk for u in units}  # the type books, the unit is assigned by itself
    with pytest.raises(BookingError,match="Nessuna unità"):stay(t,svc,first,2)
    with scope(t):
        svc.stay_rules=[{"from":(first+timedelta(days=20)).isoformat(),"until":(first+timedelta(days=40)).isoformat(),"min":3,
            "no_arrival":[(first+timedelta(days=21)).weekday()],"no_departure":[(first+timedelta(days=26)).weekday()]}];svc.save()
    with pytest.raises(BookingError,match="minimo è di 3 notti"):stay(t,svc,first+timedelta(days=20),2)
    with pytest.raises(BookingError,match="non si arriva"):stay(t,svc,first+timedelta(days=21),3)
    with pytest.raises(BookingError,match="non si parte"):stay(t,svc,first+timedelta(days=22),4)
    assert stay(t,svc,first+timedelta(days=20),3).pk

def test_feed_out_has_busy_nights_without_names_and_a_signed_link(bnb,client):
    t,svc,units=bnb;first=today()+timedelta(days=5)
    b=stay(t,svc,first,3,resource=str(units[0].pk))
    with scope(t):link=stays.feed_link(t,units[0])
    r=client.get("/"+link.split("://",1)[1].split("/",1)[1])
    body=r.content.decode()
    assert r.status_code==200 and f"DTSTART;VALUE=DATE:{first:%Y%m%d}" in body and f"DTEND;VALUE=DATE:{first+timedelta(days=3):%Y%m%d}" in body
    assert "Rossi" not in body and "mario" not in body
    assert client.get(f"/book/{t.slug}/calendario/{units[0].pk}/{'0'*32}.ics").status_code==404
    assert client.get(f"/book/{t.slug}/calendario/{units[1].pk}/{link.rsplit('/',1)[1]}").status_code==404  # one link, one unit

AIRBNB="BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nDTSTART;VALUE=DATE:{a:%Y%m%d}\r\nDTEND;VALUE=DATE:{z:%Y%m%d}\r\nSUMMARY:Reserved\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"

def test_portal_calendar_becomes_closures_and_survives_a_failed_reading(bnb,monkeypatch):
    t,svc,units=bnb;a=today()+timedelta(days=20);z=a+timedelta(days=3);u=units[0]
    monkeypatch.setattr(stays,"fetch",lambda url:AIRBNB.format(a=a,z=z))
    with scope(t):
        stays.set_feeds(t,u,"https://www.airbnb.it/calendar/ical/123.ics?s=secret")
        assert "secret" not in u.ical_in
        assert stays.refresh(t,u)
        assert list(ExceptionDay.objects.filter(resource=u).exclude(feed="").values_list("date","until"))==[(a,z-timedelta(days=1))]
    with pytest.raises(BookingError):stay(t,svc,a+timedelta(days=1),1,resource=str(u.pk))
    assert stay(t,svc,a-timedelta(days=2),2,resource=str(u.pk)).pk  # leaves the morning the portal's guest arrives
    assert stay(t,svc,z,2,resource=str(u.pk)).pk  # arrives the day the portal's guest leaves
    monkeypatch.setattr(stays,"fetch",lambda url:(_ for _ in ()).throw(OSError("down")))
    with scope(t):
        assert not stays.refresh(t,u)
        u.refresh_from_db();assert "airbnb" in u.ical_error and ExceptionDay.objects.filter(resource=u).exclude(feed="").count()==1
        stays.set_feeds(t,u,"")
        assert not ExceptionDay.objects.filter(resource=u).exclude(feed="").exists()

def test_fetch_refuses_plain_http_and_the_local_network():
    for url in ("http://example.com/a.ics","https://127.0.0.1/a.ics","https://10.0.0.5/a.ics","https://localhost/a.ics"):
        with pytest.raises(ValueError):stays.fetch(url)

def test_guests_at_desk_alloggiati_file_and_documents_deleted_after_sending(bnb):
    t,svc,units=bnb;first=today()
    with scope(t):
        b=stay(t,svc,first+timedelta(days=1),3)
        Booking.objects.filter(pk=b.pk).update(start=b.start-timedelta(days=1),end=b.end-timedelta(days=1),busy_start=b.busy_start-timedelta(days=1),busy_end=b.busy_end-timedelta(days=1))
        b.refresh_from_db()
    c,u=client_as(t)
    r=c.post(f"/t/{t.slug}/alloggi/ospiti/{b.pk}/",{**GUESTS,"g1-birth_place":"Atlantide"})
    assert "non trovato" in r.content.decode()
    r=c.post(f"/t/{t.slug}/alloggi/ospiti/{b.pk}/",GUESTS)
    assert r.status_code==302
    with scope(t):
        b.refresh_from_db();assert "CA12345AB" not in b.pii and b.data["guests"][1]["doc_number"]==""
        body,missing=stays.alloggiati(t,first)
    rows=body.split("\r\n")
    assert not missing and len(rows)==2 and all(len(x)==168 for x in rows) and not body.endswith("\r\n")
    assert rows[0].startswith(f"17{first:%d/%m/%Y}03ROSSI ") and "412058091RM100000100100000100IDELECA12345AB" in rows[0] and rows[0].endswith("412058091")
    assert rows[1].startswith("19") and "MULLER" in rows[1] and rows[1][105:134]==" "*11+"100000216100000216" and rows[1].endswith(" "*34)
    r=c.get(f"/t/{t.slug}/alloggi/file/alloggiati/?giorno={first:%Y-%m-%d}")
    assert r.status_code==200 and r.content.decode("ascii")==body
    assert c.get(f"/t/{t.slug}/alloggi/file/alloggiati/?giorno={first-timedelta(days=5):%Y-%m-%d}").status_code==302  # only today or yesterday
    c.post(f"/t/{t.slug}/alloggi/",{"action":"sent","day":first.isoformat()})
    with scope(t):
        b.refresh_from_db();g=b.data["guests"][0]
        assert g["doc_number"]=="" and g["birth_date"]=="1973-03-13" and b.snapshot["alloggiati"]
    rc,_=client_as(t,role="reception")
    assert rc.get(f"/t/{t.slug}/alloggi/file/alloggiati/?giorno={first:%Y-%m-%d}").status_code==403

def test_online_checkin_from_the_manage_link(bnb,client):
    t,svc,units=bnb
    b=stay(t,svc,today()+timedelta(days=4),2)
    with scope(t):token=b.data["cancel_token"]
    assert "check-in/" in client.get(f"/action/{t.slug}/{token}/").content.decode()
    assert client.get(f"/action/{t.slug}/{token}/check-in/").status_code==200
    r=client.post(f"/action/{t.slug}/{token}/check-in/",GUESTS)
    assert "Check-in online fatto" in r.content.decode()
    with scope(t):b.refresh_from_db();assert [g["first_name"] for g in b.data["guests"]]==["Mario","Anna"]
    assert client.get(f"/action/{t.slug}/{'x'*40}/check-in/").status_code==400

def test_tourist_tax_by_quarter_and_ross1000_month(bnb):
    t,svc,units=bnb;first=today()+timedelta(days=2)
    b=stay(t,svc,first,12,party=3)
    stay(t,svc,first,2,party=2)  # no guests' data: counted all taxed
    guests=stays.read_guests({**GUESTS,"g2-kind":"19","g2-last_name":"Rossi","g2-first_name":"Luca","g2-sex":"M","g2-birth_date":"1990-01-01","g2-birth_place":"Roma",
        "g2-citizenship":"Italia","g2-residence":"Roma","g2-exempt":"1"},3)
    with scope(t):
        stays.save_guests(t,b,guests)
        quarters,total,unknown=stays.tax(t,first.year)
    nights_in_year=sum(1 for i in range(10) if (first+timedelta(days=i)).year==first.year)
    # Mario pays up to 10 nights, Anna (8) is exempt by age, Luca declared exempt; the 2 of the other stay pay 2 nights
    assert unknown==1 and total["taxed"]==nights_in_year+sum(2 for i in range(2) if (first+timedelta(days=i)).year==first.year)
    assert total["amount"]==Decimal(6)*total["taxed"]
    with scope(t):xml,missing=stays.ross1000(t,b.location,first.year,first.month)
    days=((date(first.year,first.month,28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)).day
    assert xml.count("<movimento>")==days and "<codice>A00001</codice>" in xml and len(missing)==1
    assert f"<data>{first:%Y%m%d}</data>" in xml and "<sesso>F</sesso><cittadinanza>100000216</cittadinanza>" in xml and "ROSSI" not in xml

def test_day_page_cleaning_breakfast_and_codes(bnb,client):
    t,svc,units=bnb;first=today()+timedelta(days=1)
    b=stay(t,svc,first,2,allergies=[])
    c,u=client_as(t)
    page=c.get(f"/t/{t.slug}/alloggi/?giorno={first+timedelta(days=1):%Y-%m-%d}").content.decode()
    assert "Colazioni" in page and "Camera 1" in page and "senza dati degli ospiti" in page
    c.post(f"/t/{t.slug}/alloggi/",{"action":"room","unit":str(units[0].pk),"status":"dirty"})
    with scope(t):units[0].refresh_from_db();assert units[0].housekeeping=="dirty"
    assert "CIN IT058091C2ABCDEFGH · CIR 058091-BB-00001" in client.get(f"/book/{t.slug}/").content.decode()
    with scope(t):mails=[decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in Delivery.objects.all()]
    assert any("CIN IT058091C2ABCDEFGH" in m["body"] for m in mails)

def test_worker_reads_the_portals_every_hour(bnb,monkeypatch):
    t,svc,units=bnb;a=today()+timedelta(days=30)
    monkeypatch.setattr(stays,"fetch",lambda url:AIRBNB.format(a=a,z=a+timedelta(days=2)))
    with scope(t):stays.set_feeds(t,units[1],"https://ical.booking.com/v1/export?t=abc")
    assert stays.run()==1
    with scope(t):
        units[1].refresh_from_db();assert units[1].ical_at and not units[1].ical_error
        assert ExceptionDay.objects.filter(resource=units[1],date=a,until=a+timedelta(days=1)).exclude(feed="").exists()
