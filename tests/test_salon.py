"""2.14.0: tempo di posa, «Il primo disponibile» con strategia e filtro sui buchi, risorse richieste insieme all'operatore."""
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from django.test import RequestFactory
from django.utils import timezone
from core.booking import create_booking,available,change_status,holes,BookingError
from core.models import *
from core.tenancy import scope
from tests.test_core import sample,payload,client_as

pytestmark=pytest.mark.django_db

def shop(t,**colore):
    loc=Location.objects.get(name="Centro")
    anna=Resource.objects.create(tenant=t,location=loc,name="Anna");bruno=Resource.objects.create(tenant=t,location=loc,name="Bruno")
    mk=lambda name,minutes,**kw:Service.objects.create(tenant=t,location=loc,name=name,kind="resource",duration=minutes,interval=15,lead_minutes=0,**kw)
    taglio=mk("Taglio",30);colore=mk("Colore",120,**colore)
    for s in (taglio,colore):
        for r in (anna,bruno):ServiceResource.objects.create(tenant=t,service=s,resource=r)
    return loc,anna,bruno,taglio,colore
def day_of(s):return datetime.fromisoformat(payload(s)["start"]).date()
def times(t,s,**kw):return [datetime.fromisoformat(x["start"]).strftime("%H:%M") for x in available(t,s.pk,day_of(s),**kw)]
def at(s,hour,minute=0,**kw):
    p=payload(s,**kw);p["start"]=datetime.combine(day_of(s),time(hour,minute),ZoneInfo(s.tenant.timezone)).isoformat();return p

def test_posa_frees_the_operator_for_a_short_service(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t,pause_start=30,pause_minutes=60)
        Resource.objects.filter(pk=bruno.pk).update(active=False)
        b,_=create_booking(t,at(colore,10))
        assert b.resource==anna and b.gap_start==b.start+timedelta(minutes=30) and b.gap_end==b.start+timedelta(minutes=90)
        free=times(t,taglio,resource_id=str(anna.pk))
        # work 10:00-10:30 and finish 11:30-12:00 are Anna's; 10:30-11:30 is the posa
        assert {"10:30","10:45","11:00"}<=set(free) and not {"10:00","10:15","11:15","11:30"}&set(free)
        create_booking(t,at(taglio,10,30,resource=str(anna.pk),email="b@example.invalid"))
        assert "10:30" not in times(t,taglio,resource_id=str(anna.pk))
        # a cancelled colour comes back only if its own work and finish are still free
        change_status(t,b,"cancelled")
        create_booking(t,at(taglio,10,0,resource=str(anna.pk),email="c@example.invalid"))
        with pytest.raises(BookingError):change_status(t,b,"confirmed")

def test_service_needs_a_free_cabin_with_the_operator(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        cabina=Resource.objects.create(tenant=t,location=loc,name="Cabina",secondary=True)
        ServiceResource.objects.create(tenant=t,service=colore,resource=cabina)
        assert "10:00" in times(t,taglio)
        b,_=create_booking(t,at(colore,10))
        assert b.resource==anna and b.secondary==cabina and b.snapshot["secondary"]=="Cabina"
        # Bruno is free, the only cabin is not; the cut does not need it
        assert "10:00" not in times(t,colore) and "12:00" in times(t,colore) and "10:00" in times(t,taglio,resource_id=str(bruno.pk))
        with pytest.raises(BookingError):create_booking(t,at(colore,10,resource=str(bruno.pk),email="x@example.invalid"))
    # the cabin is never offered to the client
    r=client_as(t)[0].get(f"/book/{t.slug}/")
    assert "Cabina" not in r.content.decode() and "Bruno" in r.content.decode()

def test_first_available_follows_the_location_strategy(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        first=lambda h,**kw:create_booking(t,at(taglio,h,**kw))[0].resource.name
        assert first(9)=="Anna"  # alphabetical, as before
        Location.objects.filter(pk=loc.pk).update(assign="even")
        assert first(11,email="e@example.invalid")=="Bruno"  # Anna has one already that day
        Location.objects.filter(pk=loc.pk).update(assign="priority");Resource.objects.filter(pk=anna.pk).update(priority=2);Resource.objects.filter(pk=bruno.pk).update(priority=1)
        assert first(12,email="p@example.invalid")=="Bruno"
        Location.objects.filter(pk=loc.pk).update(assign="alpha");Resource.objects.filter(pk=anna.pk).update(auto_excluded=True)
        assert first(13,email="x@example.invalid")=="Bruno"
        assert "13:00" in times(t,taglio,resource_id=str(anna.pk))  # chosen by name she is still bookable
        Resource.objects.filter(pk=anna.pk).update(auto_excluded=False);Location.objects.filter(pk=loc.pk).update(assign="last")
        # the client of the 11:00 with Bruno gets Bruno again; a new client gets the first in name order
        assert first(15,email="e@example.invalid")=="Bruno" and first(16,email="new@example.invalid")=="Anna"

def test_online_hides_the_times_that_leave_short_holes(sample):
    t,o,_=sample
    assert holes([(datetime(2026,1,1,10),datetime(2026,1,1,10,30))],datetime(2026,1,1,10,45),datetime(2026,1,1,11,15),30)
    assert not holes([(datetime(2026,1,1,10),datetime(2026,1,1,10,30))],datetime(2026,1,1,11),datetime(2026,1,1,11,30),30)
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        Resource.objects.filter(pk=bruno.pk).update(active=False)
        create_booking(t,at(taglio,10))
        Location.objects.filter(pk=loc.pk).update(min_gap=30)
        online=times(t,taglio)
        assert {"9:30".zfill(5),"11:00"}<=set(online) and not {"09:15","10:45"}&set(online)
        assert {"09:15","10:45"}<=set(times(t,taglio,desk=True))  # the desk sees every free time

def test_garage_client_does_not_pick_the_lift(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        Service.objects.filter(pk=taglio.pk).update(client_picks=False)
    from core.public import widget_data
    req=RequestFactory().get(f"/book/{t.slug}/");req.tenant=t
    with scope(t):
        data={s["name"]:s for s in widget_data(req)["services"]}
    assert data["Taglio"]["resources"]==[] and len(data["Colore"]["resources"])==2
    from core.presets import initial
    assert initial("officina")["client_picks"] is False

def test_posa_must_sit_inside_the_duration(sample):
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        from core.forms import ServiceForm
        data=lambda **kw:{"name":"Colore","location":str(loc.pk),"description":"","duration":"120","interval":"15","buffer_before":"0","buffer_after":"0","lead_minutes":"0","horizon_days":"180",
            "cancel_cutoff_minutes":"0","resource_label":"Operatore","fields":"[]","active":"on","client_picks":"on","resources":[str(anna.pk)],**kw}
        assert "pause_minutes" in ServiceForm(data(pause_start="90",pause_minutes="30"),instance=colore,tenant=t).errors
        f=ServiceForm(data(pause_start="30",pause_minutes="60"),instance=colore,tenant=t)
        assert f.is_valid(),f.errors
        assert f.save().pause_minutes==60
        assert "resources" in ServiceForm(data(resources=[str(Resource.objects.create(tenant=t,location=loc,name="Cabina",secondary=True).pk)]),instance=colore,tenant=t).errors
