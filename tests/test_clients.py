"""2.7.0 — manual bookings and the client card: history, staff notes, clients added by hand, search."""
import json,secrets
from datetime import datetime,timedelta,time as clock
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,change_status,BookingError
from tests.test_core import sample,payload,client_as
from tests.test_phase2 import member

pytestmark=pytest.mark.django_db

def staff_payload(service,hour=10,days=3,**extra):
    day=(timezone.now()+timedelta(days=days)).astimezone(ZoneInfo(service.tenant.timezone)).date()
    data={"service":str(service.pk),"start":datetime.combine(day,clock(hour),ZoneInfo(service.tenant.timezone)).isoformat(),"party":1,
        "first_name":"Maria","last_name":"","email":"","phone":"333 444 5555","notes":"","request_key":secrets.token_urlsafe(24)}
    data.update(extra);return data

def test_desk_booking_with_phone_only(sample):
    t,o,s=sample
    owner,u=client_as(t,role="owner")
    with scope(t):
        b,_=create_booking(t,staff_payload(s),actor=u)
        assert b.data["phone"]=="+393334445555" and b.data["email"]=="" and not Delivery.objects.exists()  # no email, no confirmation to send
        again,_=create_booking(t,staff_payload(s,hour=11,first_name="Maria Rosa"),actor=u)
        assert again.contact_id==b.contact_id and Contact.objects.count()==1
        with pytest.raises(BookingError,match="telefono o l'email"):create_booking(t,staff_payload(s,hour=12,phone=""),actor=u)
        with pytest.raises(BookingError):create_booking(t,staff_payload(s,hour=12,email="",phone=""))  # online: email and phone required

def test_phone_finds_the_client_who_booked_online(sample):
    t,o,s=sample
    owner,u=client_as(t,role="owner")
    with scope(t):
        online,_=create_booking(t,payload(s,phone="+393331234567"))
        desk,_=create_booking(t,staff_payload(s,hour=12,phone="333 123 4567"),actor=u)
        assert desk.contact_id==online.contact_id

def test_booking_from_the_client_card(sample):
    t,o,s=sample
    owner,u=client_as(t,role="owner")
    with scope(t):online,_=create_booking(t,payload(s));cid=online.contact_id
    page=owner.get(f"/t/{t.slug}/bookings/new/?cliente={cid}").content.decode()
    assert '"client": {"id": "'+str(cid)+'"' in page and "Cerca un cliente già in rubrica" in page and "Nuova prenotazione" in page
    with scope(t):
        b,_=create_booking(t,staff_payload(s,hour=13,contact=str(cid),first_name="Mario",phone="",email="mario@example.invalid"),actor=u)
        assert b.contact_id==cid
        with pytest.raises(BookingError):create_booking(t,staff_payload(s,hour=14,contact=str(cid)))  # online requests cannot pick a card
    r=owner.post(f"/t/{t.slug}/booking-api/submit/",json.dumps(staff_payload(s,hour=15,contact=str(cid),email="mario@example.invalid")),content_type="application/json")
    assert r.status_code==200 and "redirect" in r.json()

def test_client_card_history_notes_and_edit(sample):
    t,o,s=sample
    owner,u=client_as(t,role="owner")
    with scope(t):
        past,_=create_booking(t,payload(s,hour=10))
        Booking.objects.filter(pk=past.pk).update(start=timezone.now()-timedelta(days=10),end=timezone.now()-timedelta(days=10)+timedelta(hours=1),status="completed")
        nxt,_=create_booking(t,payload(s,hour=11,request_key=secrets.token_urlsafe(24)))
        cid=past.contact_id
    page=owner.get(f"/t/{t.slug}/contacts/{cid}/").content.decode()
    assert "<h2>Storico</h2>" in page and "2 prenotazioni" in page and "In arrivo" not in page and "Nuova prenotazione" in page
    assert ">1</strong><small>ultima il" in page
    owner.post(f"/t/{t.slug}/contacts/{cid}/",{"action":"notes","staff_notes":"Colore 6.3, ossigeno 20 vol"})
    page=owner.get(f"/t/{t.slug}/contacts/{cid}/").content.decode()
    assert "Colore 6.3, ossigeno 20 vol" in page
    with scope(t):assert "Colore" not in Contact.objects.get(pk=cid).pii  # encrypted
    r=owner.post(f"/t/{t.slug}/contacts/{cid}/",{"action":"card","first_name":"Mario","last_name":"Rossi","phone":"340 1112233","email":"mario@example.invalid"},follow=True).content.decode()
    assert "Dati del cliente aggiornati" in r and "+393401112233" in r
    # a role without notes does not see them; a role without booking.write cannot write
    mk,_=client_as(t,role="marketing")
    assert "Colore 6.3" not in mk.get(f"/t/{t.slug}/contacts/{cid}/").content.decode()
    assert mk.post(f"/t/{t.slug}/contacts/{cid}/",{"action":"notes","staff_notes":"x"}).status_code==403

def test_clients_added_by_hand_and_search(sample):
    t,o,s=sample
    owner,u=client_as(t,role="owner")
    r=owner.post(f"/t/{t.slug}/contacts/",{"first_name":"Giulia","last_name":"Verdi","phone":"339 777 8888","email":""},follow=True).content.decode()
    assert "Giulia Verdi" in r and "+393397778888" in r
    r=owner.post(f"/t/{t.slug}/contacts/",{"first_name":"Giulia","phone":"3397778888"},follow=True).content.decode()
    assert "Questo cliente c&#x27;era già" in r or "Questo cliente c'era già" in r
    with scope(t):assert Contact.objects.count()==1
    assert "almeno il telefono" in owner.post(f"/t/{t.slug}/contacts/",{"first_name":"Senza"}).content.decode()
    assert "Giulia Verdi" in owner.get(f"/t/{t.slug}/contacts/?q=verdi").content.decode()
    assert "Giulia Verdi" in owner.get(f"/t/{t.slug}/contacts/?q=7778888").content.decode()
    assert "Nessun cliente trovato" in owner.get(f"/t/{t.slug}/contacts/?q=bianchi").content.decode()
    res=owner.get(f"/t/{t.slug}/contacts/search/?q=giu").json()["results"]
    assert res[0]["first_name"]=="Giulia" and res[0]["phone"]=="+393397778888"
    op,_=member(t,"operator")
    assert op.get(f"/t/{t.slug}/contacts/search/?q=giu").status_code==403
    assert "client-search" not in op.get(f"/t/{t.slug}/bookings/new/").content.decode()
