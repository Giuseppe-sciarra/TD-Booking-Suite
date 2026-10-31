"""2.14.0, fase 12: report del periodo con i CSV, import degli appuntamenti futuri, API in sola lettura e webhook firmati."""
import csv,hashlib,hmac,io,json
from datetime import datetime,date,time,timedelta
from zoneinfo import ZoneInfo
from unittest import mock
import pytest
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import decrypt,encrypt
from core.booking import create_booking,move_booking,change_status
from core.mail import worker_once
from tests.test_core import sample,client_as,payload
from tests.test_desk import clock,salon,at,sent

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")

def table(response):return list(csv.reader(io.StringIO(response.content.decode().lstrip("﻿"))))

def test_reports_measure_occupancy_absences_rebooking_origin_and_value(sample,clock):
    t,o,s=sample;svc,marco=salon(t);owner,u=client_as(t)
    today=clock[0].date();yesterday=today-timedelta(days=1)
    with scope(t):
        Service.objects.filter(pk=svc.pk).update(price=30)
        TimeBlock.objects.create(tenant=t,resource=marco,day=yesterday,start=time(13),end=time(14))
        first,_=create_booking(t,payload(svc,resource=str(marco.pk),start=at(today,11)),u)  # Mario, new
        nxt,_=create_booking(t,payload(svc,resource=str(marco.pk),start=at(today+timedelta(days=7),11)),u)  # booked the same day
        back=datetime.fromisoformat(at(yesterday,10))
        Booking.objects.filter(pk=first.pk).update(start=back,end=back+timedelta(minutes=30),busy_start=back,busy_end=back+timedelta(minutes=30),status="completed",created=back-timedelta(days=3))
        Booking.objects.filter(pk=nxt.pk).update(created=back+timedelta(minutes=20))
        gone,_=create_booking(t,payload(svc,resource=str(marco.pk),start=at(today,15),email="anna@example.invalid",phone="+393339999999"),None,source="instagram")
        change_status(t,gone,"cancelled",reason="client")  # within 24 hours: late
    r=owner.get(f"/t/{t.slug}/report/?from={yesterday}&to={today}")
    page=r.content.decode();assert r.status_code==200 and "Occupazione per operatore" in page and "stima" in page
    occ=table(owner.get(f"/t/{t.slug}/report/?from={yesterday}&to={today}&csv=occupazione"))
    assert occ[0][0]=="Risorsa" and occ[1][:4]==["Marco","Centro",str(2*540-60),"30"]  # 9-18 on two days, minus the block
    absent={r[0]:r for r in table(owner.get(f"/t/{t.slug}/report/?from={yesterday}&to={today}&csv=assenze"))}
    assert absent["Taglio"][1:5]==["2","0","0","1"] and absent["Clienti nuovi"][4]=="1" and absent["Totale"][1]=="2"
    clients=dict(table(owner.get(f"/t/{t.slug}/report/?from={yesterday}&to={today}&csv=clienti"))[1:])
    assert clients["Clienti nuovi"]=="1" and clients["Visite fatte"]=="1" and clients["Uscite con il prossimo appuntamento già preso"]=="1"
    origin=table(owner.get(f"/t/{t.slug}/report/?from={yesterday}&to={today}&csv=origine"))
    assert ["Pagina pubblica","instagram","1"] in [r[:3] for r in origin] and ["Banco","","1"] in [r[:3] for r in origin]
    value=table(owner.get(f"/t/{t.slug}/report/?from={yesterday}&to={today}&csv=valore"))
    assert value[-1][0]=="Totale" and value[-1][1]=="1" and value[-1][2].startswith("30")  # the cancelled one does not count
    # an operator sees only his own
    op,_=client_as(t,role="operator")
    with scope(t):Membership.objects.filter(user=_).update(resources=[])
    assert "Marco" not in op.get(f"/t/{t.slug}/report/").content.decode()

def appointments_file(svc,day,rows):
    out=io.StringIO();w=csv.writer(out,delimiter=";")
    w.writerow(["Data","Ora","Durata","Servizio","Operatore","Cliente","Cellulare"])
    for r in rows:w.writerow(r)
    f=io.BytesIO(out.getvalue().encode());f.name="agenda.csv";return f

def test_import_future_appointments_with_conflicts_and_forza(sample,clock):
    t,o,s=sample;svc,marco=salon(t);owner,u=client_as(t)
    d=clock[0].date()+timedelta(days=2);ds=d.strftime("%d/%m/%Y")
    with scope(t):create_booking(t,payload(svc,resource=str(marco.pk),start=at(d,11)),u)  # already there at 11
    mails=len(sent(t))
    url=f"/t/{t.slug}/bookings/import/"
    f=appointments_file(svc,d,[[ds,"10:00","30","Taglio","Marco","Rossi Anna","333 111 2222"],[ds,"11:00","30","taglio","marco","Neri Luca","333 111 3333"],
        ["01/01/2020","10:00","30","Taglio","Marco","Vecchio","333 111 4444"],[ds,"12:00","30","Massaggio","Marco","Bianchi","333 111 5555"]])
    assert owner.post(url,{"action":"upload","file":f}).status_code==302
    page=owner.get(url).content.decode()
    assert 'value="date" selected' in page and 'value="staff" selected' in page and 'value="full_name" selected' in page
    cols={"col0":"date","col1":"time","col2":"minutes","col3":"service","col4":"staff","col5":"full_name","col6":"phone"}
    r=owner.post(url,{"action":"preview",**cols});page=r.content.decode()
    assert "in conflitto: <strong>1</strong>" in page and "Pronti da importare: <strong>1</strong>" in page and "Massaggio" in page and "già passati: 1" in page
    with scope(t):assert Booking.objects.count()==1  # the preview wrote nothing
    checked=r.context["checked"]
    owner.post(url,{"action":"import","checked":checked,"force":"1",**cols})
    with scope(t):
        imported=list(Booking.objects.filter(origin="import").order_by("start"))
        assert len(imported)==2 and imported[1].snapshot.get("forced") and imported[0].data["first_name"]=="Rossi"
        assert imported[0].end-imported[0].start==timedelta(minutes=30) and imported[0].resource_id==marco.pk
    assert len(sent(t))==mails  # no email to the clients
    owner.post(url,{"action":"upload","file":appointments_file(svc,d,[[ds,"10:00","30","Taglio","Marco","Rossi Anna","333 111 2222"]])})
    assert "già importati prima: 1" in owner.post(url,{"action":"preview",**cols}).content.decode()  # the same row, loaded again
    owner.post(url,{"action":"reset"})
    # without the right to force (reception), the conflicting rows are skipped
    rec,_=client_as(t,role="reception")
    rec.post(url,{"action":"upload","file":appointments_file(svc,d,[[ds,"10:00","30","Taglio","Marco","Verdi","333 111 6666"]])})
    r=rec.post(url,{"action":"preview",**cols});assert "serve chi può forzare" in r.content.decode()
    rec.post(url,{"action":"import","checked":r.context["checked"],"force":"1",**cols})
    with scope(t):assert Booking.objects.filter(origin="import").count()==2

def test_api_reads_with_the_key_only(sample,clock):
    t,o,s=sample;svc,marco=salon(t)
    agency,_=client_as(t,staff=True)
    r=agency.post(f"/t/{t.slug}/api/",{"action":"key"});key=r.context["key"]
    assert key.startswith("tdbs_")
    t.refresh_from_db();assert t.api_key and key not in t.api_key
    with scope(t):b,_=create_booking(t,payload(svc,resource=str(marco.pk)),None)
    from django.test import Client
    c=Client()
    assert c.get(f"/book/{t.slug}/api/v1/bookings/").status_code==403
    assert c.get(f"/book/{t.slug}/api/v1/bookings/",HTTP_AUTHORIZATION="Bearer nope").status_code==403
    assert c.get(f"/book/{o.slug}/api/v1/bookings/",HTTP_AUTHORIZATION="Bearer "+key).status_code==403  # another business
    data=c.get(f"/book/{t.slug}/api/v1/bookings/",HTTP_AUTHORIZATION="Bearer "+key).json()
    assert data["bookings"][0]["id"]==str(b.pk) and data["bookings"][0]["client"]["first_name"]=="Mario"
    assert any(x["name"]=="Taglio" and x["resources"][0]["name"]=="Marco" for x in c.get(f"/book/{t.slug}/api/v1/services/",HTTP_AUTHORIZATION="Bearer "+key).json()["services"])
    day=clock[0].date()+timedelta(days=1)
    slots=c.get(f"/book/{t.slug}/api/v1/availability/?service={svc.pk}&day={day}",HTTP_AUTHORIZATION="Bearer "+key).json()["slots"]
    assert slots and slots[0]["start"].startswith(day.isoformat())
    assert c.post(f"/book/{t.slug}/api/v1/bookings/",HTTP_AUTHORIZATION="Bearer "+key).status_code in (403,405)
    agency.post(f"/t/{t.slug}/api/",{"action":"revoke"})
    assert c.get(f"/book/{t.slug}/api/v1/services/",HTTP_AUTHORIZATION="Bearer "+key).status_code==403
    owner,_=client_as(t)
    assert owner.get(f"/t/{t.slug}/api/").status_code==403  # the agency's

def test_dates_of_a_series_cancelled_together_reach_the_webhook(sample):
    # each date of a series sends "created": cancelled quietly with the first, the receiver kept them as booked
    t,o,s=sample;t.webhook=encrypt(t,{"url":"https://hooks.example.com/td","secret":"x"},"webhook");t.save()
    with scope(t):
        b,_=create_booking(t,payload(s));change_status(t,b,"cancelled",reason="client",quiet=True)
        events=[decrypt(t,d.payload,"delivery:"+str(d.pk))["event"] for d in Delivery.objects.filter(channel="webhook").order_by("created")]
    assert events==["booking.created","booking.cancelled"]

def test_webhooks_signed_without_client_data_and_retried(sample,clock):
    t,o,s=sample;svc,marco=salon(t)
    agency,_=client_as(t,staff=True)
    with mock.patch("core.mail.public_addresses",side_effect=ValueError("SMTP verso reti locali o riservate non consentito.")):
        assert "reti locali" in agency.post(f"/t/{t.slug}/api/",{"action":"webhook","url":"https://10.0.0.1/x"}).content.decode()
    with mock.patch("core.mail.public_addresses",return_value=[1]):
        agency.post(f"/t/{t.slug}/api/",{"action":"webhook","url":"https://hooks.example.com/td"})
    t.refresh_from_db();secret=decrypt(t,t.webhook,"webhook")["secret"]
    with scope(t):
        b,_=create_booking(t,payload(svc,resource=str(marco.pk),start=at(clock[0].date()+timedelta(days=2),11)),None)
        move_booking(t,b.pk,{"start":at(clock[0].date()+timedelta(days=2),12),"resource":str(marco.pk)},None)
        change_status(t,Booking.objects.get(pk=b.pk),"cancelled",reason="client")
        hooks=Delivery.objects.filter(channel="webhook").order_by("created")
        events=[decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in hooks]
    assert [e["event"] for e in events]==["booking.created","booking.moved","booking.cancelled"]
    assert "Mario" not in json.dumps(events) and "mario@" not in json.dumps(events)
    calls=[]
    class Resp:
        def __enter__(self):return self
        def __exit__(self,*a):pass
    def fake(request,timeout):
        calls.append(request)
        if len(calls)==1:
            import urllib.error;raise urllib.error.HTTPError(request.full_url,503,"down",{},None)
        return Resp()
    with mock.patch("core.mail.public_addresses",return_value=[1]),mock.patch("core.api.OPENER.open",fake):
        worker_once()
        with scope(t):
            assert sorted(Delivery.objects.filter(channel="webhook").values_list("status","last_code"))==[("accepted",""),("accepted",""),("pending","webhook_503")]  # retried later
            Delivery.objects.filter(channel="webhook").update(next_attempt=timezone.now())
        worker_once()
    with scope(t):assert set(Delivery.objects.filter(channel="webhook").values_list("status",flat=True))=={"accepted"}
    req=calls[-1];stamp=req.headers["X-tdbs-timestamp"]
    assert req.headers["X-tdbs-signature"]=="sha256="+hmac.new(secret.encode(),stamp.encode()+b"."+req.data,hashlib.sha256).hexdigest()
