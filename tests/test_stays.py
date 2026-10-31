"""2.14.0, fase 9: strutture ricettive. Calendari .ics per alloggio in uscita e dai portali in entrata come chiusure,
CIN e CIR, regole per periodo, la giornata con pulizie e colazioni, ospiti cifrati al banco e dal check-in online,
file Alloggiati Web e Ross1000, imposta di soggiorno."""
import base64,secrets
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
    stay(t,svc,first,2,party=2)  # no guests' data: counted all at the ordinary rate
    post={**GUESTS,"g2-kind":"19","g2-last_name":"Rossi","g2-first_name":"Luca","g2-sex":"M","g2-birth_date":"1990-01-01","g2-birth_place":"Roma",
        "g2-citizenship":"Italia","g2-residence":"Roma","g2-exemption":"resident","tourism":"CULTURALE","transport":"TRENO"}
    guests=stays.read_guests(post,3)
    closed=date(first.year,first.month,1) if first.day!=1 else date(first.year,first.month,28)
    with scope(t):
        stays.save_guests(t,b,guests,trip=stays.read_trip(post))
        [x]=stays.tax(t,first.year)
        ExceptionDay.objects.create(tenant=t,location=b.location,date=closed,label="Chiuso per ferie")
    nights_in_year=sum(1 for i in range(10) if (first+timedelta(days=i)).year==first.year)
    # Mario pays up to 10 nights, Anna (8) is exempt by age, Luca as a resident; the 2 of the other stay pay 2 nights
    assert x["unknown"]==1 and x["total"]["ordinary"]==nights_in_year+sum(2 for i in range(2) if (first+timedelta(days=i)).year==first.year)
    assert x["total"]["amount"]==Decimal(6)*x["total"]["ordinary"] and x["total"]["exempt"]==2*nights_in_year
    assert x["codes"]=={"Età":nights_in_year,"Residente nel comune":nights_in_year} if nights_in_year else True
    with scope(t):xml,missing=stays.ross1000(t,b.location,first.year,first.month)
    days=((date(first.year,first.month,28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)).day
    assert xml.count("<movimento>")==days and "<codice>A00001</codice>" in xml and len(missing)==1
    assert f"<data>{first:%Y%m%d}</data>" in xml and "<sesso>F</sesso><cittadinanza>100000216</cittadinanza>" in xml and "ROSSI" not in xml
    assert "<tipoturismo>CULTURALE</tipoturismo><mezzotrasporto>TRENO</mezzotrasporto>" in xml
    assert f"<data>{closed:%Y%m%d}</data><struttura><apertura>NO</apertura><camereoccupate>0</camereoccupate>" in xml and xml.count("<apertura>NO</apertura>")==1

def moved(t,b,first,nights):
    a=datetime.combine(first,time(15),ROME);z=datetime.combine(first+timedelta(days=nights),time(10),ROME)
    with scope(t):Booking.objects.filter(pk=b.pk).update(start=a,end=z,busy_start=a,busy_end=z);b.refresh_from_db()
    return b

def person(kind,name,born,code=""):return {"kind":kind,"last_name":"Rossi","first_name":name,"sex":"M","birth_date":born,"birth_place":"Roma","citizenship":"Italia","residence":"Roma","exemption":code}

def test_tax_rules_by_season_and_category_and_reasons_per_guest(bnb):
    t,svc,units=bnb;soon=today()+timedelta(days=40)
    with scope(t):
        loc=svc.location;loc.tax_category="bb";loc.tax_year_nights=2;loc.tax_escort=3;loc.tax_reduction=50
        loc.tax_rules=[{"category":"cav1","from":"2025-01-01","until":"2025-12-31","yearly":False,"rate":"9.00","nights":0},  # another category
            {"category":"bb","from":"2024-03-12","until":"2024-05-31","yearly":True,"rate":"7.00","nights":3},  # spring, every year
            {"category":"","from":"2024-12-01","until":"2024-02-28","yearly":True,"rate":"5.00","nights":0}];loc.save()  # winter, across the new year
    winter=moved(t,stay(t,svc,soon,1,party=1),date(2025,1,5),1)
    spring=moved(t,stay(t,svc,soon+timedelta(days=3),4,party=4),date(2025,3,10),4)
    june=moved(t,stay(t,svc,soon+timedelta(days=9),2,party=1),date(2025,6,1),2)
    with scope(t):
        stays.save_guests(t,spring,[person("17","Mario","1980-01-01","worker"),person("19","Anna","1980-01-01","escort"),person("19","Luca","1980-01-01","reduced"),person("19","Pia","2020-01-01")])
        stays.save_guests(t,june,[person("16","Mario","1980-01-01","worker")])
        [x]=stays.tax(t,2025)
    q1,q2=x["quarters"][:2]
    # winter 5 €; spring 6 € twice then 7 €, the 4th night over the 3 of the season; Mario works: 2 nights a year, then exempt;
    # one escort every 3 guests; Luca at half; Pia under 10
    assert q1["rates"]=={Decimal(5):1,Decimal(6):2} and q1["rates_text"]=="5,00; 6,00" and q1["nights_text"]=="1; 2"
    assert q1["reduced"]=={Decimal("3.00"):2,Decimal("3.50"):1} and q1["exempt"]==1+3+3 and q1["amount"]==Decimal("5")+12+Decimal("9.5")
    assert q2["exempt"]==2 and q2["amount"]==0 and not q2["rates"]
    assert x["codes"]=={"Lavoro (notti massime all'anno)":3,"Accompagnatore turistico del gruppo":3,"Età":3}
    with scope(t):
        loc.tax_escort=4;loc.save()  # 3 guests, one escort every 4: Anna pays
        [x]=stays.tax(t,2025)
    assert x["quarters"][0]["rates"]=={Decimal(5):1,Decimal(6):4,Decimal(7):1}

def test_tax_rules_and_settings_from_the_page(bnb):
    t,svc,units=bnb;c,u=client_as(t)
    loc=svc.location
    assert c.post(f"/t/{t.slug}/alloggi/",{"action":"tax_location","location":str(loc.pk),"tax_category":"bb","tax_year_nights":"10","tax_escort":"25","tax_reduction":"0"}).status_code==302
    c.post(f"/t/{t.slug}/alloggi/",{"action":"tax_add","location":str(loc.pk),"from":"2026-06-01","until":"2026-09-30","category":"bb","rate":"6,50","nights":"10","yearly":"on"})
    r=c.post(f"/t/{t.slug}/alloggi/",{"action":"tax_add","location":str(loc.pk),"from":"2026-06-01","until":"2026-09-30","category":"bb","rate":"tanto"})
    assert "tariffa" in r.content.decode()
    with scope(t):
        loc.refresh_from_db();assert loc.tax_category=="bb" and loc.tax_year_nights==10
        assert loc.tax_rules==[{"category":"bb","from":"2026-06-01","until":"2026-09-30","yearly":True,"rate":"6.50","nights":10}]
        assert stays.tax_rule(loc,date(2031,7,1))==(Decimal("6.50"),10) and stays.tax_rule(loc,date(2031,10,1))==(Decimal(6),10)
    page=c.get(f"/t/{t.slug}/alloggi/").content.decode()
    assert "ogni anno dal 01/06 al 30/09" in page and "Bed and breakfast" in page
    c.post(f"/t/{t.slug}/alloggi/",{"action":"tax_del","location":str(loc.pk),"rule":"0"})
    with scope(t):loc.refresh_from_db();assert loc.tax_rules==[]

def test_out_of_service_unit_is_not_booked_and_its_stays_are_listed(bnb):
    t,svc,units=bnb;first=today()+timedelta(days=8)
    b=stay(t,svc,first,2,resource=str(units[0].pk))
    c,u=client_as(t)
    r=c.post(f"/t/{t.slug}/alloggi/",{"action":"room","unit":str(units[0].pk),"status":"out"},follow=True)
    page=r.content.decode()
    assert "1 prenotazioni da spostare" in page and str(b.pk) in page and "Da spostare" in page
    with pytest.raises(BookingError):stay(t,svc,first+timedelta(days=20),1,resource=str(units[0].pk))
    assert stay(t,svc,first+timedelta(days=20),1).resource_id==units[1].pk
    with pytest.raises(BookingError):stay(t,svc,first+timedelta(days=20),1)  # the other one is taken, the one out does not count
    c.post(f"/t/{t.slug}/alloggi/",{"action":"room","unit":str(units[0].pk),"status":"dirty"})
    assert stay(t,svc,first+timedelta(days=20),1).resource_id==units[0].pk

ENV='<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>{}</soap:Body></soap:Envelope>'
def token_answer(ok=True):
    return ENV.format('<GenerateTokenResponse xmlns="AlloggiatiService"><GenerateTokenResult><issued>2026-10-31T10:00:00</issued><expires>2026-10-31T11:00:00</expires>'
        f'<token>{"TK" if ok else ""}</token></GenerateTokenResult><result><esito>{"true" if ok else "false"}</esito><ErroreDes>{"" if ok else "Utente o password errati"}</ErroreDes></result></GenerateTokenResponse>')
def rows_answer(method,oks):
    det="".join(f'<EsitoOperazioneServizio><esito>{"true" if ok else "false"}</esito><ErroreCod>{"" if ok else "12"}</ErroreCod><ErroreDes>{"" if ok else "Data di Nascita errata"}</ErroreDes></EsitoOperazioneServizio>' for ok in oks)
    return ENV.format(f'<{method}Response xmlns="AlloggiatiService"><{method}Result><esito>true</esito></{method}Result><result><SchedineValide>{sum(oks)}</SchedineValide><Dettaglio>{det}</Dettaglio></result></{method}Response>')

def test_alloggiati_web_service_send_receipt_and_automatic(bnb,monkeypatch):
    t,svc,units=bnb;now=today()
    a=moved(t,stay(t,svc,now+timedelta(days=3),2),now,2)
    b=moved(t,stay(t,svc,now+timedelta(days=3),2,party=1),now,2)
    with scope(t):
        Booking.objects.filter(pk=b.pk).update(start=datetime.combine(now,time(0,1),ROME));b.refresh_from_db()
        stays.save_guests(t,a,stays.read_guests(GUESTS,2))
        stays.save_guests(t,b,[{**person("16","Gino","1970-02-02"),"doc_type":"PASOR","doc_number":"YA1234567","doc_place":"Roma"}])
    c,u=client_as(t)
    conf={"action":"ws_conf","ws_user":"RM012345","ws_password":"segreta-123","ws_wskey":"AbCdEf123456=="}
    assert c.post(f"/t/{t.slug}/alloggi/",conf).status_code==403  # the agency sets it, unless it allows the owner
    Tenant.objects.filter(pk=t.pk).update(alloggiati_self=True)
    assert c.post(f"/t/{t.slug}/alloggi/",conf).status_code==302
    t.refresh_from_db()
    assert "segreta" not in t.alloggiati and stays.ws_conf(t)=={"user":"RM012345","password":"segreta-123","wskey":"AbCdEf123456==","auto":False}
    calls=[]
    def fake(method,body):
        calls.append((method,body.count("<string>")))
        if method=="GenerateToken":return token_answer()
        if method=="Test":return rows_answer("Test",[ "GINO" not in x for x in body.split("<string>")[1:]])
        if method=="Send":return rows_answer("Send",[True]*body.count("<string>"))
        if method=="Ricevuta":return ENV.format('<RicevutaResponse xmlns="AlloggiatiService"><RicevutaResult><esito>true</esito></RicevutaResult><PDF>'+base64.b64encode(b"%PDF-1.4 ricevuta").decode()+'</PDF></RicevutaResponse>')
    monkeypatch.setattr(stays,"ws_post",fake)
    page=c.post(f"/t/{t.slug}/alloggi/",{"action":"ws_send","day":now.isoformat()},follow=True).content.decode()
    assert "Inviati ad Alloggiati Web: 1 soggiorni" in page and "Data di Nascita errata" in page
    assert calls==[("GenerateToken",0),("Test",3),("Send",2)]  # the family whole, the refused stay not sent
    monkeypatch.setattr(stays,"ws_post",fake)
    with scope(t):
        a.refresh_from_db();b.refresh_from_db()
        assert a.snapshot["alloggiati"] and a.data["guests"][0]["doc_number"]=="" and "alloggiati" not in b.snapshot and b.data["guests"][0]["doc_number"]=="YA1234567"
        assert "ospite 1: Data di Nascita errata" in b.snapshot["alloggiati_error"] and "Gino" not in str(b.snapshot)
        receipt=Receipt.objects.get();assert receipt.day==now and receipt.rows==2 and receipt.pdf is None
        assert stays.receipts(t,stays.ws_conf(t),timezone.now()+timedelta(days=1))==1
        receipt.refresh_from_db();assert bytes(receipt.pdf)==b"%PDF-1.4 ricevuta"
    r=c.get(f"/t/{t.slug}/alloggi/file/ricevuta/?giorno={now:%Y-%m-%d}")
    assert r.status_code==200 and r.content==b"%PDF-1.4 ricevuta" and r["Content-Type"]=="application/pdf"
    with scope(t):stays.ws_save(t,{**stays.ws_conf(t),"auto":True})
    calls.clear();stays.run()
    assert ("Send",0) not in calls and not any(m=="Test" for m,_ in calls)  # the refused one waits for its data to be corrected
    with scope(t):stays.save_guests(t,b,[{**b.data["guests"][0],"first_name":"Gina"}])
    calls.clear();stays.run()
    assert calls[:3]==[("GenerateToken",0),("Test",1),("Send",1)]
    with scope(t):b.refresh_from_db();assert b.snapshot["alloggiati"] and b.data["guests"][0]["doc_number"]==""
    monkeypatch.setattr(stays,"ws_post",lambda method,body:token_answer(False))
    with scope(t):Receipt.objects.update(pdf=None)
    stays.run(timezone.now()+timedelta(days=1));t.refresh_from_db()
    assert "Utente o password errati" in stays.ws_conf(t)["error"]
    assert "Ultimo invio automatico non riuscito" in c.get(f"/t/{t.slug}/alloggi/").content.decode()

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
