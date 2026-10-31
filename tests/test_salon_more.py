"""2.14.0: le semplificazioni tolte. Posa per operatore, più gruppi di cabine e macchinari, buchi contro apertura e
blocchi, serie spostata dal banco, sostituto vero in agenda, categorie anche nello spostamento dal link Gestisci."""
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from core.booking import create_booking,move_booking,change_status,BookingError
from core.clients import parse_categories,save_profile,clean_profile
from core.models import *
from core.tenancy import scope
from tests.test_core import sample,payload,client_as
from tests.test_salon import shop,times,at,day_of

pytestmark=pytest.mark.django_db

def test_operator_posa_overrides_the_service(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t,pause_start=30,pause_minutes=60)
        ServiceResource.objects.filter(service=colore,resource=bruno).update(pause_start=45,pause_minutes=30)
        a,_=create_booking(t,at(colore,10,resource=str(anna.pk)))
        b,_=create_booking(t,at(colore,10,resource=str(bruno.pk),email="b@example.invalid"))
        assert a.gap_start-a.start==timedelta(minutes=30) and b.gap_start-b.start==timedelta(minutes=45) and b.gap_end-b.gap_start==timedelta(minutes=30)
        ServiceResource.objects.filter(service=colore,resource=anna).update(pause_minutes=0)  # Anna: no posa at all
        c,_=create_booking(t,at(colore,14,resource=str(anna.pk),email="c@example.invalid"))
        assert c.gap_start is None
    from core.forms import ResourceForm
    with scope(t):
        f=ResourceForm(data={"name":"Bruno","location":str(loc.pk),"services":[str(colore.pk)],f"pa_{colore.pk}":"100",f"pm_{colore.pk}":"60"},instance=bruno,tenant=t)
        assert not f.is_valid() and "posa sta dentro" in str(f.errors)

def test_one_free_of_each_group_of_cabins_and_machines(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        cab=Resource.objects.create(tenant=t,location=loc,name="Cabina",secondary=True,group="Cabina")
        laser=Resource.objects.create(tenant=t,location=loc,name="Laser",secondary=True,group="Macchinario")
        for r in (cab,laser):ServiceResource.objects.create(tenant=t,service=colore,resource=r)
        b,_=create_booking(t,at(colore,10))
        assert b.secondary==cab and b.more==[str(laser.pk)] and b.snapshot["secondary"]=="Cabina, Laser"
        assert "10:00" not in times(t,colore)  # Bruno is free, the cabin and the laser are not
        # the laser alone, taken by another service, is enough to stop the colour
        epil=Service.objects.create(tenant=t,location=loc,name="Epilazione",kind="resource",duration=60,interval=15,lead_minutes=0)
        for r in (bruno,laser):ServiceResource.objects.create(tenant=t,service=epil,resource=r)
        create_booking(t,at(epil,14,email="e@example.invalid"))
        assert "14:00" not in times(t,colore) and "16:00" in times(t,colore)

def test_online_holes_count_opening_and_blocks(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        Resource.objects.filter(pk=bruno.pk).update(active=False)
        Location.objects.filter(pk=loc.pk).update(min_gap=30)
        TimeBlock.objects.create(tenant=t,resource=anna,day=day_of(taglio),start=time(13),end=time(14))
        online=set(times(t,taglio))
        # opening at 9:00: 9:15 leaves 15 minutes; the pause at 13:00: 12:15 ends 12:45; closing 18:00: 17:15 ends 17:45
        assert {"09:00","09:30","12:30","17:30"}<=online and not {"09:15","12:15","14:15","17:15"}&online
        assert {"09:15","12:15","17:15"}<=set(times(t,taglio,desk=True))

def test_series_moved_from_the_desk_this_and_following(sample):
    t,o,_=sample
    owner,u=client_as(t)
    from tests.test_clients import staff_payload
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        b,_=create_booking(t,staff_payload(taglio,hour=10,days=3,resource=str(anna.pk)),actor=u)
        Delivery.objects.all().delete()
    until=(b.start+timedelta(weeks=2)).date().isoformat()
    owner.post(f"/t/{t.slug}/bookings/{b.pk}/",{"action":"series_make","every":"1","until":until})
    tz=ZoneInfo(t.timezone)
    with scope(t):
        rows=list(Booking.objects.filter(series=Booking.objects.get(pk=b.pk).series).order_by("start"))
        assert len(rows)==3
        # the third week Bruno is busy at 15:00
        create_booking(t,{**staff_payload(taglio,phone="333 000 0009"),"resource":str(bruno.pk),"start":datetime.combine(rows[2].start.astimezone(tz).date()+timedelta(days=1),time(15),tz).isoformat()},actor=u)
        Delivery.objects.all().delete()
    new=datetime.combine(rows[1].start.astimezone(tz).date()+timedelta(days=1),time(15)).strftime("%Y-%m-%dT%H:%M")
    post={"start":new,"resource":str(bruno.pk),"following":"on"}
    page=owner.post(f"/t/{t.slug}/bookings/{rows[1].pk}/",{**post,"action":"shift_check"}).content.decode()
    plan=page.split('id="sposta-serie"')[1].split("</section>")[0]
    assert plan.count("· libera")==1 and plan.count("non si può")==1 and "Sposta 1 date" in plan
    owner.post(f"/t/{t.slug}/bookings/{rows[1].pk}/",{**post,"action":"shift_make"})
    with scope(t):
        moved=Booking.objects.get(pk=rows[1].pk);kept=Booking.objects.get(pk=rows[2].pk)
        assert moved.resource==bruno and moved.start.astimezone(tz).hour==15 and moved.start.date()==rows[1].start.date()+timedelta(days=1)
        assert kept.start==rows[2].start and Booking.objects.get(pk=rows[0].pk).start==rows[0].start
        assert Delivery.objects.count()<=1  # one email, not one per date

def test_substitute_takes_the_class_in_the_agenda_and_gives_it_back(sample):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):
        a,_=create_booking(t,payload(s,hour=10))
        z,_=create_booking(t,payload(s,hour=10,email="z@example.invalid",phone="+393330000003",first_name="Zoe"))
        paola=Resource.objects.create(tenant=t,location=s.location,name="Paola")
        Delivery.objects.all().delete()
    owner.post(f"/t/{t.slug}/bookings/{a.pk}/iscritti/",{"action":"substitute","resource":str(paola.pk),"notify":"on"})
    with scope(t):
        rows=list(Booking.objects.filter(pk__in=[a.pk,z.pk]))
        assert all(b.resource==paola and b.snapshot["resource"]=="Paola" and b.snapshot["original_id"]=="" for b in rows)
        assert Delivery.objects.count()==2
        change_status(t,Booking.objects.get(pk=z.pk),"cancelled");change_status(t,Booking.objects.get(pk=z.pk),"confirmed")  # the places are the date's
    owner.post(f"/t/{t.slug}/bookings/{a.pk}/iscritti/",{"action":"substitute","resource":""})
    with scope(t):
        assert all(b.resource_id is None and "substitute" not in b.snapshot and "original" not in b.snapshot for b in Booking.objects.filter(pk__in=[a.pk,z.pk]))

def test_category_limit_applies_when_the_client_moves_from_his_link(sample):
    t,o,s=sample
    t.client_categories=parse_categories("Ospite, 5, 1");t.save()
    with scope(t):
        b,_=create_booking(t,payload(s,hour=10))
        save_profile(t,b.contact,clean_profile({"category":"Ospite"},b.start.date(),t.client_categories))
        far=payload(s,hour=10);far["start"]=(datetime.fromisoformat(far["start"])+timedelta(days=7)).isoformat()
        with pytest.raises(BookingError,match="5 giorni avanti"):move_booking(t,b.pk,{**far,"service":str(s.pk)},None,desk=False)
        # within the days ahead it moves: the booking itself does not count as one more to come
        near=payload(s,hour=11)
        assert move_booking(t,b.pk,{**near,"service":str(s.pk)},None,desk=False).start==datetime.fromisoformat(near["start"])
        assert move_booking(t,b.pk,{**far,"service":str(s.pk)},None).start==datetime.fromisoformat(far["start"])  # the desk is free
