"""2.13.0: the emails to the clients (chosen and written by the business once the agency allows it, the reminder),
the client's own choice, Marketing switched on by the agency, the dashboard lists, Logo e aspetto."""
import io
from datetime import timedelta
import pytest
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import decrypt
from core.booking import create_booking,move_booking,change_status
from core.client_mails import remind,fill
from tests.test_core import sample,payload,client_as
from tests.test_clients import staff_payload

pytestmark=pytest.mark.django_db

def to_clients(t):
    with scope(t):
        out=[decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in Delivery.objects.filter(channel="email").order_by("created")]
    return [m for m in out if m["to"]=="mario@example.invalid"]

def test_standard_emails_without_the_agencys_switch(sample):
    t,o,s=sample
    t.client_mails={"confirmed":{"on":False}};t.save()  # written while allowed: ignored once the agency turns it off
    with scope(t):
        b,_=create_booking(t,payload(s))
        change_status(t,Booking.objects.get(pk=b.pk),"cancelled")
    sent=to_clients(t)
    assert [m["subject"].split(" · ")[0] for m in sent]==["Prenotazione confermata","Prenotazione annullata"]
    assert "/action/"+t.slug+"/" in sent[0]["body"] and " alle " in sent[0]["body"]

def test_the_business_writes_its_emails(sample):
    t,o,s=sample
    owner,u=client_as(t)
    url=f"/t/{t.slug}/notifications/"
    page=owner.get(url).content.decode()
    assert "chiedi all" in page and "data-mails" not in page
    assert owner.post(url,{"action":"client_mails"}).status_code==403
    t.client_mails_enabled=True;t.save()
    page=owner.get(url).content.decode()
    assert "data-mails" in page and "Promemoria" in page and 'id="mail-sample"' in page
    data={"confirmed_on":"on","confirmed_subject":"Ci vediamo {data} · {attivita}","confirmed_body":"Ciao {nome}, {servizio} {quando}.\n{boh}",
          "moved_subject":"","moved_body":"","cancelled_subject":"x","cancelled_body":"y",
          "reminder_on":"on","reminder_hours":"24","reminder_subject":"","reminder_body":""}
    r=owner.post(url,{"action":"client_mails",**data});assert r.status_code==302
    t.refresh_from_db()
    assert t.client_mails["moved"]["on"] is False and t.client_mails["reminder"]["hours"]==24 and t.client_mails["moved"]["subject"].startswith("Prenotazione aggiornata")
    assert "scegli quando" in owner.post(url,{"action":"client_mails",**data,"reminder_hours":"5"},follow=True).content.decode().lower()
    with scope(t):
        b,_=create_booking(t,payload(s))
        move_booking(t,b.pk,{"service":str(s.pk),"start":payload(s,hour=12)["start"],"party":1},None)
    sent=to_clients(t)
    assert len(sent)==1 and sent[0]["subject"].startswith("Ci vediamo ") and sent[0]["subject"].endswith(" · "+t.name)
    assert sent[0]["body"].startswith("Ciao Mario, ") and "{boh}" in sent[0]["body"]  # an unknown field stays as written

def test_reminder_once_and_again_when_moved(sample):
    t,o,s=sample
    t.client_mails_enabled=True;t.client_mails={"reminder":{"on":True,"hours":24}};t.save()
    with scope(t):
        b,_=create_booking(t,payload(s))  # in three days
        late,_=create_booking(t,payload(s,hour=14,email="anna@example.invalid"))
    soon=late.start-timedelta(hours=23)  # both start within the next 24 hours
    with scope(t):Booking.objects.filter(pk=late.pk).update(created=late.start-timedelta(hours=2))  # booked inside the window
    assert remind(soon)==1 and remind(soon)==0
    with scope(t):
        assert Booking.objects.get(pk=late.pk).snapshot.get("reminded")  # looked at once, never sent
        move_booking(t,b.pk,{"service":str(s.pk),"start":payload(s,hour=12)["start"],"party":1},None)
        assert "reminded" not in Booking.objects.get(pk=b.pk).snapshot
    assert remind(soon)==1
    reminders=[m for m in to_clients(t) if m["subject"].startswith("Promemoria")]
    assert len(reminders)==2 and "Ciao Mario" in reminders[0]["body"]

def test_the_client_says_no(sample):
    t,o,s=sample
    with scope(t):
        create_booking(t,payload(s,notices=False))
        contact=Contact.objects.get();assert contact.notices is False  # a new card takes the choice
    assert to_clients(t)==[]
    owner,u=client_as(t)
    page=owner.get(f"/t/{t.slug}/contacts/{contact.pk}/").content.decode()
    assert "Email delle prenotazioni" in page and "Non le riceve" in page
    owner.post(f"/t/{t.slug}/contacts/{contact.pk}/",{"action":"notices","notices":"on"})
    with scope(t):
        contact.refresh_from_db();assert contact.notices
        create_booking(t,payload(s,hour=11))
        b,_=create_booking(t,payload(s,hour=12,notices=False))  # this booking only: the card stays as it is
        contact.refresh_from_db();assert contact.notices and b.snapshot["notices"] is False
    assert len(to_clients(t))==1
    owner.post(f"/t/{t.slug}/contacts/{contact.pk}/",{"action":"notices"})
    with scope(t):create_booking(t,payload(s,hour=13))
    assert len(to_clients(t))==1
    page=owner.get(f"/book/{t.slug}/").content.decode()
    assert 'id="notices" type="checkbox" checked' in page

def test_marketing_is_switched_on_by_the_agency(sample):
    t,o,s=sample
    owner,u=client_as(t)
    assert ">Marketing<" not in owner.get(f"/t/{t.slug}/").content.decode().replace("</svg>",">")
    assert owner.get(f"/t/{t.slug}/marketing/").status_code==403
    t.export_enabled=True;t.save()
    page=owner.get(f"/t/{t.slug}/marketing/").content.decode()
    assert "CSV Brevo" in page and 'profile=brevo" download' in page and "Nuova comunicazione" not in page and "Chiedi di attivarle" in page
    assert "Marketing</a>" in owner.get(f"/t/{t.slug}/").content.decode()
    t.export_enabled=False;t.marketing_enabled=True;t.save()
    page=owner.get(f"/t/{t.slug}/marketing/").content.decode()
    assert "CSV Brevo" not in page and "Nuova comunicazione" in page
    assert owner.get(f"/t/{t.slug}/export/?kind=marketing&profile=sheet").status_code==403

def test_dashboard_lists_who_comes_in_the_next_days(sample):
    t,o,s=sample
    with scope(t):b,_=create_booking(t,payload(s,first_name="Ginevra"))  # in three days
    owner,u=client_as(t)
    page=owner.get(f"/t/{t.slug}/").content.decode()
    day=timezone.localtime(b.start).date().isoformat()
    assert 'data-tab-link="settimana"' in page and 'id="settimana"' in page and f'id="giorno-{day}"' in page and f'href="#giorno-{day}"' in page
    week=page[page.index('id="settimana"'):]
    assert "Ginevra" in week and timezone.localtime(b.start).strftime("%H:%M") in week

def test_the_owner_sets_logo_and_look(sample,settings):
    from PIL import Image
    t,o,s=sample
    owner,u=client_as(t)
    assert 'id="aspetto"' in owner.get(f"/t/{t.slug}/setup/").content.decode()
    buf=io.BytesIO();Image.new("RGBA",(300,100),(200,30,30,255)).save(buf,format="PNG");buf.seek(0);buf.name="logo.png"
    r=owner.post(f"/t/{t.slug}/setup/aspetto/",{"brand_color":"#0f766e","tagline":"Dal 1998","website":"https://salone.example","logo_file":buf})
    assert r.status_code==302
    t.refresh_from_db();assert t.logo and t.logo_wide and t.brand_color=="#0f766e" and t.tagline=="Dal 1998"
    settings.EDITION="private"
    page=owner.get(f"/t/{t.slug}/").content.decode()
    assert 'class="tenant-mark wide"' in page and 'class="workspace-logo' not in page
    owner.post(f"/t/{t.slug}/setup/aspetto/",{"brand_color":"nero","tagline":"","website":""})
    t.refresh_from_db();assert t.brand_color=="#0f766e" and t.logo  # nothing saved with a wrong colour
    owner.post(f"/t/{t.slug}/setup/aspetto/",{"brand_color":"#0f766e","tagline":"","website":"","logo_remove":"on"})
    t.refresh_from_db();assert not t.logo
    from tests.test_phase2 import member
    rec,_=member(t,"reception")
    assert rec.post(f"/t/{t.slug}/setup/aspetto/",{"brand_color":"#000000"}).status_code==403

def test_fill_keeps_unknown_fields():
    assert fill("{nome} {x}",{"nome":"Ada"})=="Ada {x}"
