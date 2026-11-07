"""2.14.0: più servizi nella stessa visita: uno dopo l'altro, regola della sede sugli operatori, attesa massima,
una prenotazione per servizio legata dalla visita, una sola email e un solo avviso, spostate e annullate insieme."""
from datetime import datetime,timedelta
import pytest
from django.test import Client
from core.booking import create_booking,move_booking,change_status,visit_slots,BookingError
from core.client_mails import remind
from core.crypto import decrypt
from core.models import *
from core.tenancy import scope
from tests.test_core import sample,client_as
from tests.test_salon import shop,day_of,at

pytestmark=pytest.mark.django_db

def mails(t,to="mario@example.invalid"):
    out=[decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in Delivery.objects.filter(channel="email").order_by("created")]
    return [x for x in out if x["to"]==to]
def plans(t,items,**kw):
    return {datetime.fromisoformat(x["start"]).strftime("%H:%M"):[(p[2].name,p[3].astimezone(datetime.fromisoformat(x["start"]).tzinfo).strftime("%H:%M")) for p in x["plan"]] for x in visit_slots(t,items,day_of(items[0][0]),**kw)}
def three(t,rule):
    """Anna fa solo il colore, Bruno colore e taglio, Carla solo il taglio."""
    loc,anna,bruno,taglio,colore=shop(t)
    carla=Resource.objects.create(tenant=t,location=loc,name="Carla");ServiceResource.objects.create(tenant=t,service=taglio,resource=carla)
    ServiceResource.objects.filter(service=taglio,resource=anna).delete()
    Location.objects.filter(pk=loc.pk).update(visit_split=rule,visit_wait=0)
    return anna,bruno,carla,taglio,Service.objects.get(pk=colore.pk)

def test_visit_fits_one_service_after_the_other(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        Location.objects.filter(pk=loc.pk).update(visit_split="same")
        colore.refresh_from_db()
        p=plans(t,[(colore,None),(taglio,None)])
        assert p["10:00"]==[("Anna","10:00"),("Anna","12:00")]
        # the end of the slot is the end of the whole visit
        slot=next(x for x in visit_slots(t,[(colore,None),(taglio,None)],day_of(colore)) if "T10:00" in x["start"])
        assert "T12:30" in slot["end"]

@pytest.mark.parametrize("rule,first,second",[("same",[("Bruno","10:00"),("Bruno","12:00")],None),
    ("need",[("Bruno","10:00"),("Bruno","12:00")],[("Anna","10:00"),("Carla","12:00")]),
    ("split",[("Anna","10:00"),("Bruno","12:00")],[("Anna","10:00"),("Carla","12:00")])])
def test_split_rules(sample,rule,first,second):
    t,o,_=sample
    with scope(t):
        anna,bruno,carla,taglio,colore=three(t,rule)
        items=[(colore,None),(taglio,None)]
        assert plans(t,items)["10:00"]==first
        create_booking(t,at(taglio,12,resource=str(bruno.pk),email="altro@example.invalid"))
        assert plans(t,items).get("10:00")==second

def test_max_wait_between_services(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        Resource.objects.filter(pk=bruno.pk).update(active=False)
        create_booking(t,at(taglio,12,resource=str(anna.pk),email="altro@example.invalid"))
        Location.objects.filter(pk=loc.pk).update(visit_wait=0);colore.refresh_from_db()
        assert "10:00" not in plans(t,[(colore,None),(taglio,None)])
        Location.objects.filter(pk=loc.pk).update(visit_wait=30);colore.refresh_from_db()
        assert plans(t,[(colore,None),(taglio,None)])["10:00"]==[("Anna","10:00"),("Anna","12:30")]

def test_visit_books_each_service_with_one_email_and_one_notice(sample):
    t,o,_=sample
    Tenant.objects.filter(pk=t.pk).update(notify_email="banco@example.invalid");t.refresh_from_db()
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        first,created=create_booking(t,at(colore,10,extra=[str(taglio.pk)]))
        rows=list(Booking.objects.filter(visit=first.visit).order_by("start"))
        assert created and len(rows)==2 and rows[0]==first and [r.service for r in rows]==[colore,taglio]
        assert rows[1].start==rows[0].end and rows[0].resource==rows[1].resource
        sent=mails(t);assert len(sent)==1 and "Colore + Taglio" in sent[0]["body"]
        staff=mails(t,"banco@example.invalid");assert len(staff)==1 and "Servizi: Colore" in staff[0]["body"] and "Taglio 12:00" in staff[0]["body"]
        # the same request again is the same visit
        again=at(colore,10,extra=[str(taglio.pk)]);again["request_key"]=first.request_key
        assert create_booking(t,again)==(first,False) and Booking.objects.filter(visit=first.visit).count()==2
        # one reminder for the visit
        remind(now=rows[0].start-timedelta(hours=2))
        assert len(mails(t))==2 and "Colore + Taglio" in mails(t)[-1]["body"]
        # a single service books as before
        one,_=create_booking(t,at(taglio,15,email="solo@example.invalid"))
        assert one.visit is None and len(mails(t,"solo@example.invalid"))==1

def test_visit_moves_and_cancels_together(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        first,_=create_booking(t,at(colore,10,extra=[str(taglio.pk)]))
        second=Booking.objects.get(visit=first.visit,service=taglio)
        move_booking(t,second.pk,{"start":at(colore,13)["start"],"visit":True},None)
        rows=list(Booking.objects.filter(visit=first.visit).order_by("start"))
        assert rows[0].start==datetime.fromisoformat(at(colore,13)["start"]) and rows[1].start==rows[0].end
        assert [m["subject"].split(" ·")[0] for m in mails(t)]==["Prenotazione confermata","Prenotazione aggiornata"]
        # dragged alone in the agenda, one service moves alone
        move_booking(t,rows[1].pk,{"service":str(taglio.pk),"resource":str(rows[1].resource_id),"start":at(colore,16)["start"]},None)
        assert Booking.objects.get(pk=rows[0].pk).start==rows[0].start
        change_status(t,rows[0],"cancelled",reason="client")
        assert set(Booking.objects.filter(visit=first.visit).values_list("status",flat=True))=={"cancelled"}
        assert [m["subject"].split(" ·")[0] for m in mails(t)][2:]==["Prenotazione aggiornata","Prenotazione annullata"]
        # put back, together
        change_status(t,Booking.objects.get(pk=rows[1].pk),"confirmed")
        assert set(Booking.objects.filter(visit=first.visit).values_list("status",flat=True))=={"confirmed"}

def test_client_link_cancels_and_page_offers_the_visit(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        first,_=create_booking(t,at(colore,10,extra=[str(taglio.pk)]))
        token=first.data["cancel_token"]
    c=Client()
    r=c.get(f"/book/{t.slug}/api/slots/",{"service":str(colore.pk),"date":day_of(colore).isoformat(),"extra":str(taglio.pk)})
    assert r.status_code==200 and r.json()["slots"] and "plan" not in r.json()["slots"][0]
    r=c.get(f"/action/{t.slug}/{token}/sposta/orari/",{"date":day_of(colore).isoformat()})
    assert r.status_code==200 and r.json()["slots"]
    assert 'id="visit-list"' in c.get(f"/book/{t.slug}/").content.decode()  # 2.22.3: one list, the chips of the visit under it
    desk=client_as(t)[0];page=desk.get(f"/t/{t.slug}/bookings/{first.pk}/move/")
    assert page.status_code==200 and '"extra": [{"id": "'+str(taglio.pk) in page.content.decode()
    assert "Visita con 2 servizi" in desk.get(f"/t/{t.slug}/bookings/{first.pk}/").content.decode()
    c.post(f"/action/{t.slug}/{token}/")
    with scope(t):assert set(Booking.objects.filter(visit=first.visit).values_list("status",flat=True))=={"cancelled"}

def test_visit_only_for_appointments(sample):
    t,o,s=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        with pytest.raises(BookingError):create_booking(t,at(colore,10,extra=[str(s.pk)]))
        with pytest.raises(BookingError):create_booking(t,at(colore,10,extra=[str(taglio.pk)]*5))
