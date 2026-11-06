"""2.21.0: il sito gestisce prenotazioni ed email (core/wpsync.py): the program sends no email, it only gets the bookings
and what the clients did on the site's links; a booking from the site finds its card also by name."""
import json,time
from datetime import timedelta
import pytest
from django.utils import timezone
from core import wpsync
from core.booking import create_booking
from core.clients import find_or_create,seal_card,key_for
from core.models import *
from core.tenancy import scope
from tests.test_core import sample,payload
from tests.test_salon import shop,at
from tests.test_wpsync import SECRET,site_booking

pytestmark=pytest.mark.django_db

class Site:
    """The plugin's /sync with the site in charge: it answers with its bookings and the clients' changes."""
    def __init__(self,bookings=(),changes=()):self.calls=[];self.bookings=list(bookings);self.changes=list(changes)
    def __call__(self,req,timeout=None):
        assert wpsync.verify(SECRET,req.headers["X-tdbs-time"],req.data,req.headers["X-tdbs-signature"])
        self.calls.append((req.full_url,json.loads(req.data)))
        out={"ok":True,"acked":[o["seq"] for o in json.loads(req.data).get("ops",[])]} if req.full_url.endswith("/archive/push") else {"ok":True,"bookings":self.bookings,"changes":self.changes}
        body=json.dumps(out);ts=str(int(time.time()))
        raw=json.dumps({"body":body,"time":ts,"sig":wpsync.sign(SECRET,ts,body.encode())}).encode()
        class R:
            def __enter__(s):return s
            def __exit__(s,*a):return False
            def read(s,n=None):return raw
        return R()
def in_charge(t,site,monkeypatch,mails=True):
    wpsync.store(t,{"url":"https://www.salone.example","secret":SECRET,"days":5,"on":True,"mails":mails,"mails_since":1})
    Tenant.objects.filter(pk=t.pk).update(wp_archive=True);t.refresh_from_db()
    monkeypatch.setattr(wpsync.urllib.request,"urlopen",site)
def card(t,first,last,email="",phone=""):
    c=Contact(tenant=t,email_index=key_for(t,{"email":email,"phone":phone,"first_name":first,"last_name":last}))
    seal_card(t,c,{"first_name":first,"last_name":last,"email":email,"phone":phone});c.save();return c

def test_no_email_from_the_program_with_the_site_in_charge(sample,monkeypatch):
    t,o,s=sample
    t.notify_email="titolare@example.invalid";t.save()
    in_charge(t,Site(),monkeypatch)
    with scope(t):
        b,_=create_booking(t,payload(s))  # a confirmation and a notice to the owner, with the PC in charge
        assert not Delivery.objects.exists()
        from core.booking import queue
        queue(t,{"to":"collega@example.invalid","subject":"Invito","body":"Attiva il tuo accesso"})  # not about a booking: an invitation still goes
        assert Delivery.objects.count()==1;Delivery.objects.all().delete()
        wpsync.store(t,{**wpsync.conf(t),"mails":False})
        create_booking(t,payload(s,hour=12,email="z@example.invalid",phone="+393330000009"))
        assert Delivery.objects.count()==2  # the switch off: the program writes again

def test_the_site_bookings_cancellations_and_confirmations_reach_the_program(sample,monkeypatch):
    t,o,_=sample
    site=Site();in_charge(t,site,monkeypatch)
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        desk,_=create_booking(t,at(taglio,15,resource=str(bruno.pk)))
        other,_=create_booking(t,at(taglio,16,resource=str(bruno.pk),email="m@example.invalid",phone="+393330000004"))
    site.bookings=[site_booking(taglio,anna,10)];site.changes=[{"id":"7","ref":str(desk.pk),"action":"cancel","at":"2026-11-06T10:00:00+01:00"},
        {"id":"8","ref":str(other.pk),"action":"confirm","at":"2026-11-06T10:01:00+01:00"},{"id":"9","ref":"wp-"+"f"*32,"action":"cancel"}]
    assert wpsync.sync(t)==1
    with scope(t):
        new=Booking.objects.get(request_key="wp-"+"a"*32)
        assert new.status=="confirmed" and new.origin=="wordpress"
        assert Booking.objects.get(pk=desk.pk).status=="cancelled" and Booking.objects.get(pk=desk.pk).snapshot["cancel_reason"]=="client"
        assert "client_confirmed" in Booking.objects.get(pk=other.pk).snapshot
        assert not Delivery.objects.exists()  # no email: the site sent them
    # the next call acks the changes (also the one of a booking it does not know) and the site's booking
    site.changes=[];t.refresh_from_db();wpsync.sync(t)
    body=[b for u,b in site.calls if u.endswith("/sync")][-1]
    assert body["changes_ack"]==["7","8","9"] and body["ack"]==["a"*32]
    # a day later, a change of a booking already cancelled is acked and does nothing
    site.changes=[{"id":"10","ref":str(desk.pk),"action":"confirm"}];t.refresh_from_db();wpsync.sync(t)
    with scope(t):assert "client_confirmed" not in Booking.objects.get(pk=desk.pk).snapshot

def test_a_time_taken_at_the_desk_is_told_to_the_site(sample,monkeypatch):
    t,o,_=sample
    site=Site();in_charge(t,site,monkeypatch)
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        create_booking(t,at(taglio,10,resource=str(anna.pk)))
    site.bookings=[site_booking(taglio,anna,10)]
    wpsync.sync(t);t.refresh_from_db();wpsync.sync(t)
    body=[b for u,b in site.calls if u.endswith("/sync")][-1]
    assert body["refused"]==[{"id":"a"*32,"kept":True,"reason":"l'orario nel frattempo è stato occupato: l'attività ti ricontatta per confermarlo o spostarlo"}]
    with scope(t):assert Booking.objects.get(request_key="wp-"+"a"*32).status=="pending" and not Delivery.objects.exists()

def test_a_site_booking_finds_the_card_by_name_however_written(sample,monkeypatch):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        alina=card(t,"Alina","Rossi",phone="+393331112222")
        maria=card(t,"Maria","Bianchi",phone="+393331113333");card(t,"Maria","Bianchi",phone="+393331114444")
        nicolo=card(t,"Nicolò","D'Amico")
    rows=[site_booking(taglio,anna,9,id="1"*32,first_name="alina",last_name="ROSSI",email="alina.nuova@example.invalid",phone="347 000 0001"),
          site_booking(taglio,anna,10,id="2"*32,first_name="Rossi",last_name="Alina",email="r2@example.invalid",phone="347 000 0002"),
          site_booking(taglio,anna,11,id="3"*32,first_name="maria",last_name="bianchi",email="mb@example.invalid",phone="333 111 4444"),
          site_booking(taglio,anna,12,id="4"*32,first_name="Maria",last_name="Bianchi",email="altra@example.invalid",phone="347 000 0003"),
          site_booking(taglio,anna,13,id="5"*32,first_name="nicolo",last_name="d'amico",email="n@example.invalid",phone="347 000 0005"),
          site_booking(taglio,anna,14,id="6"*32,first_name="Alina",last_name="Verdi",email="a2@example.invalid",phone="347 000 0006")]
    in_charge(t,Site(bookings=rows),monkeypatch)
    wpsync.sync(t);t.refresh_from_db();assert not (t.wp_state or {}).get("error"),t.wp_state
    with scope(t):
        got=lambda i:Booking.objects.get(request_key="wp-"+i*32).contact_id
        assert got("1")==alina.pk and got("2")==alina.pk and got("5")==nicolo.pk
        maria2=Contact.objects.exclude(pk=maria.pk).filter(pk__in=[got("3")]).first()
        assert maria2 and maria2.data["phone"]=="+393331114444"  # two Maria Bianchi: the one with that phone
        assert got("4") not in (maria.pk,maria2.pk)  # two with the name, neither phone nor email: a card of her own
        assert got("6")!=alina.pk  # another surname, another person
        from core.clients import by_name
        assert by_name(t,{"first_name":"alina","last_name":""}) is None  # a first name alone is not a person
        assert Contact.objects.get(pk=alina.pk).data["email"]==""  # the card is never rewritten by an online request
    # at the desk nothing changes: a new name is a new card
    with scope(t):
        c,new=find_or_create(t,{"first_name":"ALINA","last_name":"rossi","email":"","phone":"+393470009999"})
        assert new and c.pk!=alina.pk

def test_the_switch_wants_the_archive_and_puts_the_bookings_to_come_on_the_site(sample,monkeypatch):
    t,o,s=sample
    monkeypatch.setattr(wpsync,"merge",lambda tenant,actor:{})
    with scope(t):b,_=create_booking(t,payload(s))
    wpsync.store(t,{"url":"https://www.salone.example","secret":SECRET,"days":5,"on":True})
    with pytest.raises(ValueError,match="archivio centrale"):wpsync.save(t,{"url":"https://www.salone.example","on":"1","mails":"1"},None)
    wpsync.save(t,{"url":"https://www.salone.example","on":"1","archive":"1","mails":"1"},None)
    t.refresh_from_db();c=wpsync.conf(t)
    assert c["mails"] and c["mails_since"]>0 and wpsync.by_site(t) and wpsync.master(t)=={"on":True,"since":c["mails_since"]}
    with scope(t):assert SiteOutbox.objects.filter(kind="booking",ref=str(b.pk)).exists()
    snap=wpsync.snapshot(t,3)
    assert snap["master"]["on"] and all("approval" in x and "cutoff" in x for x in snap["services"])
