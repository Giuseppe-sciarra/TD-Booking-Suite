"""2.14.0, fase p10: corsi e studi. Servizi a distanza con il link della videochiamata, categorie di clienti con i limiti
online, test richiesto con l'avviso al banco, nomi dei partecipanti cifrati, Richiami con l'email solo col consenso."""
from datetime import date,timedelta
import pytest
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,BookingError
from core.clients import parse_categories,save_profile,clean_profile,recalls
from core.client_mails import remind
from tests.test_core import sample,payload,client_as
from tests.test_reminders import mails

pytestmark=pytest.mark.django_db

def test_remote_service_puts_the_video_link_in_confirmation_and_reminder(sample):
    t,o,s=sample
    with scope(t):Service.objects.filter(pk=s.pk).update(remote_url="https://meet.example.invalid/stanza")
    with scope(t):b,_=create_booking(t,payload(s,hour=10))
    first=mails(t)[0]
    assert "Entra nella videochiamata: https://meet.example.invalid/stanza" in first["body"] and "meet.example.invalid/stanza" in first["html"]
    with scope(t):Booking.objects.filter(pk=b.pk).update(created=b.created-timedelta(days=5),start=timezone.now()+timedelta(hours=20),end=timezone.now()+timedelta(hours=21))
    remind()
    assert "videochiamata" in mails(t)[-1]["body"] and mails(t)[-1]["subject"].startswith("Promemoria")
    from core.forms import ServiceForm
    with scope(t):
        f=ServiceForm(data={"remote_url":"http://x.example.invalid"},instance=s,tenant=t)
        f.is_valid();assert "remote_url" in f.errors

def test_client_categories_limit_the_online_page_not_the_desk(sample):
    t,o,s=sample
    assert parse_categories("Socio, 14, 1\nOspite,2\n\nsocio, 3, 3")==[{"name":"Socio","ahead":14,"max":1},{"name":"Ospite","ahead":2,"max":0}]
    with pytest.raises(BookingError):parse_categories("Socio, tanti, 1")
    t.client_categories=parse_categories("Socio, 14, 1\nOspite, 2, 0");t.save()
    with scope(t):
        b,_=create_booking(t,payload(s,hour=9))
        c=b.contact
        with pytest.raises(BookingError):clean_profile({"category":"Nessuna così"},date.today(),t.client_categories)
        save_profile(t,c,clean_profile({"category":"Ospite"},date.today(),t.client_categories))
        assert "Ospite" not in Contact.objects.get(pk=c.pk).pii  # sealed in the card
        with pytest.raises(BookingError,match="2 giorni avanti"):create_booking(t,payload(s,hour=11))  # 3 days ahead
        save_profile(t,c,clean_profile({"category":"Socio"},date.today(),t.client_categories))
        with pytest.raises(BookingError,match="1 prenotazione in programma"):create_booking(t,payload(s,hour=11))
        assert create_booking(t,payload(s,hour=11,email="altro@example.invalid",phone="+393330000001"))[0].status=="confirmed"  # no category
    owner,u=client_as(t)
    with scope(t):
        from tests.test_clients import staff_payload
        assert create_booking(t,staff_payload(s,hour=12,contact=str(c.pk)),actor=u)[0].status=="confirmed"  # the desk is free
    r=owner.post(f"/t/{t.slug}/setup/banco/",{"absences_request":"0","wait_mode":"all","wait_minutes":"60","client_categories":"Socio, 7, 2"})
    assert r.status_code==302 and Tenant.objects.get(pk=t.pk).client_categories==[{"name":"Socio","ahead":7,"max":2}]

def test_required_test_warns_at_the_desk_and_participants_are_sealed(sample):
    t,o,s=sample
    with scope(t):
        Service.objects.filter(pk=s.pk).update(test_days=180)
        b,_=create_booking(t,payload(s,hour=10,party=2))
    owner,_=client_as(t)
    page=owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    assert "Test da fare" in page and "Partecipanti" in page
    r=owner.post(f"/t/{t.slug}/contacts/{b.contact_id}/",{"action":"profile","test_date":(date.today()-timedelta(days=10)).isoformat()})
    assert r.status_code==302 and "Ultimo test" in owner.get(f"/t/{t.slug}/contacts/{b.contact_id}/").content.decode()
    assert "Test da fare" not in owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    owner.post(f"/t/{t.slug}/bookings/{b.pk}/",{"action":"participants","participants":"Luca Bianchi\nSara Verdi"})
    with scope(t):
        b=Booking.objects.get(pk=b.pk)
        assert b.data["participants"]==["Luca Bianchi","Sara Verdi"] and "Luca" not in b.pii
    owner.post(f"/t/{t.slug}/bookings/{b.pk}/",{"action":"participants","participants":"A\nB\nC"})  # more than the party
    with scope(t):assert len(Booking.objects.get(pk=b.pk).data["participants"])==2

def test_recalls_worklist_and_email_only_with_consent_and_nothing_booked(sample):
    t,o,s=sample
    with scope(t):
        Service.objects.filter(pk=s.pk).update(recall_days=30)
        a,_=create_booking(t,payload(s,hour=10))
        z,_=create_booking(t,payload(s,hour=11,email="zeta@example.invalid",phone="+393330000002",first_name="Zeta"))
        past=timezone.now()-timedelta(days=40)
        Booking.objects.update(start=past,end=past+timedelta(hours=1),busy_start=past,busy_end=past+timedelta(hours=1),status="completed")
        Contact.objects.filter(pk=a.contact_id).update(consent="yes")
        assert {r["contact"].pk for r in recalls(timezone.now())}=={a.contact_id,z.contact_id}
        nxt,_=create_booking(t,payload(s,hour=12,email="zeta@example.invalid",phone="+393330000002"))  # Zeta booked again
        assert [r["contact"].pk for r in recalls(timezone.now())]==[a.contact_id]
        Delivery.objects.all().delete()
    owner,_=client_as(t)
    assert "Da richiamare" in owner.get(f"/t/{t.slug}/richiami/").content.decode()
    remind();assert not mails(t)  # the email is off until the business turns it on
    t.client_mails={"recall":{"on":True}};t.save()
    remind();remind()
    sent=mails(t);assert len(sent)==1 and sent[0]["to"]=="mario@example.invalid" and "/book/salone/" in sent[0]["body"]
    owner.post(f"/t/{t.slug}/richiami/",{"booking":str(a.pk)})
    with scope(t):assert recalls(timezone.now())==[]

def test_series_from_the_desk_conflicts_first_then_one_or_all_cancelled(sample):
    t,o,s=sample
    owner,u=client_as(t)
    from tests.test_clients import staff_payload
    with scope(t):
        b,_=create_booking(t,staff_payload(s,hour=10,days=3),actor=u)
        Delivery.objects.all().delete()
    until=(b.start+timedelta(weeks=3)).date().isoformat()
    with scope(t):
        # the second week is full: two others take both places
        wk2=(b.start+timedelta(weeks=2)).astimezone(t and __import__("zoneinfo").ZoneInfo(t.timezone))
        for i in range(2):create_booking(t,{**staff_payload(s,hour=10,days=3,phone=f"33300000{i}9"),"start":wk2.isoformat()},actor=u)
        Delivery.objects.all().delete()
    page=owner.post(f"/t/{t.slug}/bookings/{b.pk}/",{"action":"series_check","every":"1","until":until}).content.decode()
    assert page.count("libera")==2 and "non si può" in page and "Crea 2 date libere" in page
    owner.post(f"/t/{t.slug}/bookings/{b.pk}/",{"action":"series_make","every":"1","until":until})
    with scope(t):
        series=Booking.objects.get(pk=b.pk).series
        assert series and Booking.objects.filter(series=series).count()==3 and not Delivery.objects.exists()  # no email for each date
        second=Booking.objects.filter(series=series).order_by("start")[1]
    owner.post(f"/t/{t.slug}/bookings/{second.pk}/",{"action":"cancelled","reason":"client"})  # only this one
    with scope(t):assert Booking.objects.filter(series=series,status="cancelled").count()==1
    owner.post(f"/t/{t.slug}/bookings/{b.pk}/",{"action":"cancelled","reason":"client","whole_series":"on"})
    with scope(t):assert Booking.objects.filter(series=series,status="cancelled").count()==3

def test_attendees_tick_and_substitute(sample):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):
        a,_=create_booking(t,payload(s,hour=10))
        z,_=create_booking(t,payload(s,hour=10,email="z@example.invalid",phone="+393330000003",first_name="Zoe"))
        r=Resource.objects.create(tenant=t,location=s.location,name="Paola")
        past=timezone.now()-timedelta(minutes=30)
        Booking.objects.update(start=past,end=past+timedelta(hours=1),busy_start=past,busy_end=past+timedelta(hours=1))
    page=owner.get(f"/t/{t.slug}/bookings/{a.pk}/iscritti/").content.decode()
    assert "Zoe" in page and "Mario" in page and "Presente" in page
    owner.post(f"/t/{t.slug}/bookings/{a.pk}/iscritti/",{"action":"present","booking":str(a.pk)})
    owner.post(f"/t/{t.slug}/bookings/{a.pk}/iscritti/",{"action":"absent","booking":str(z.pk)})
    owner.post(f"/t/{t.slug}/bookings/{a.pk}/iscritti/",{"action":"substitute","resource":str(r.pk)})
    with scope(t):
        assert Booking.objects.get(pk=a.pk).status=="completed" and Booking.objects.get(pk=z.pk).status=="no_show"
        assert all(b.snapshot["substitute"]=="Paola" for b in Booking.objects.all())
    owner.post(f"/t/{t.slug}/bookings/{a.pk}/iscritti/",{"action":"reset","booking":str(a.pk)})
    with scope(t):assert Booking.objects.get(pk=a.pk).status=="confirmed"
    assert "Sostituto" in owner.get(f"/t/{t.slug}/bookings/{a.pk}/").content.decode()

def test_consent_signed_at_the_desk_is_sealed_in_the_card(sample):
    t,o,s=sample
    import base64,zlib,struct
    def png():
        raw=b"\x00"+b"\x00\x00\x00"*40
        chunk=lambda k,d:struct.pack(">I",len(d))+k+d+struct.pack(">I",zlib.crc32(k+d))
        return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",40,1,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(raw))+chunk(b"IEND",b"")
    sign="data:image/png;base64,"+base64.b64encode(png()*3).decode()
    with scope(t):b,_=create_booking(t,payload(s))
    owner,_=client_as(t)
    r=owner.post(f"/t/{t.slug}/contacts/{b.contact_id}/",{"action":"sign","title":"Consenso informato al tatuaggio","text":"Ho letto e accetto.","signature":sign})
    assert r.status_code==302
    owner.post(f"/t/{t.slug}/contacts/{b.contact_id}/",{"action":"sign","title":"Senza firma","text":"x","signature":"data:image/png;base64,AAAA"})
    with scope(t):
        c=Contact.objects.get(pk=b.contact_id)
        assert [x["title"] for x in c.data["signed"]]==["Consenso informato al tatuaggio"] and "tatuaggio" not in c.pii
    page=owner.get(f"/t/{t.slug}/contacts/{b.contact_id}/").content.decode()
    assert "Consensi firmati" in page and "firmato il "+timezone.localdate().strftime("%d/%m/%Y") in page
