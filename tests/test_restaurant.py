"""2.14.0, fase 7 (proposta 13): il ristorante. Il ritmo degli arrivi per orario, i posti online e quelli tenuti per il
telefono, i coperti di tutto il locale divisi tra i servizi (vince il limite più stretto), la durata che cresce con le
persone, i gruppi su richiesta nel tipo Ristorante."""
from datetime import datetime,date,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,available,length,BookingError,ForceWarning
from core.forms import ServiceForm
from core.presets import initial
from tests.test_core import sample,payload,client_as

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")

def day3():return (datetime.now(ROME)+timedelta(days=3)).date()
def at(h,m=0):return datetime.combine(day3(),time(h,m),ROME).isoformat()
def times(t,s,party=1,desk=False):
    with scope(t):return [x["start"][11:16] for x in available(t,s.pk,day3(),party=party,desk=desk)]
def book(t,s,h,m=0,party=1,actor=None,**kw):
    with scope(t):return create_booking(t,payload(s,start=at(h,m),party=party,email=f"c{h}{m}{party}{len(kw)}@example.invalid",**kw),actor)[0]

@pytest.fixture
def table(sample):
    t,o,s=sample
    with scope(t):Service.objects.filter(pk=s.pk).update(capacity=40,max_party=20,interval=15,turnover=False,lead_minutes=0)
    s.refresh_from_db();return t,s

def test_pacing_per_arrival_time_online_only(table):
    t,s=table
    with scope(t):Service.objects.filter(pk=s.pk).update(pace_covers=6,pace_bookings=2)
    s.refresh_from_db()
    book(t,s,11,party=4)
    assert "11:00" in times(t,s,2) and "11:00" not in times(t,s,3)  # 4+3 people over 6
    assert "11:15" in times(t,s,6)  # the next time has its own arrivals
    book(t,s,11,party=1)
    assert "11:00" not in times(t,s,1)  # two bookings already at 11:00
    with pytest.raises(BookingError,match="Orario non disponibile"):book(t,s,11,party=1)
    # the desk is not bound by the rhythm, unless the business says so
    owner,u=client_as(t)
    assert "11:00" in times(t,s,4,desk=True)
    with scope(t):Service.objects.filter(pk=s.pk).update(pace_desk=True)
    s.refresh_from_db()
    assert "11:00" not in times(t,s,1,desk=True)
    # with Forza the desk goes over it, said in words
    with pytest.raises(ForceWarning,match="oltre il ritmo degli arrivi delle 11:00"):book(t,s,11,party=2,actor=u,force=True)
    b=book(t,s,11,party=2,actor=u,force=True,force_ok=True)
    assert b.snapshot["forced"]==["oltre il ritmo degli arrivi delle 11:00"]

def test_online_places_and_the_ones_kept_for_the_phone(table):
    t,s=table
    with scope(t):Service.objects.filter(pk=s.pk).update(capacity=10,online_capacity=6)
    s.refresh_from_db()
    book(t,s,13,party=5)
    assert "13:00" in times(t,s,1) and "13:00" not in times(t,s,2)  # 5 of the 6 online places taken (the whole lunch)
    owner,u=client_as(t)
    assert "13:00" in times(t,s,5,desk=True) and "13:00" not in times(t,s,6,desk=True)  # the desk has all 10
    book(t,s,13,party=4,actor=u)
    assert times(t,s,1)==[] or "13:00" not in times(t,s,1)

def test_covers_of_the_whole_place_shared_by_its_services_tightest_wins(table):
    t,s=table
    with scope(t):
        group=Service.objects.create(tenant=t,location=s.location,name="Menu di gruppo",kind="capacity",capacity=30,max_party=20,interval=15,turnover=False,lead_minutes=0)
        Location.objects.filter(pk=s.location_id).update(capacity=12)
    s.refresh_from_db();group.refresh_from_db()
    book(t,s,12,party=8)
    assert "12:00" in times(t,group,4) and "12:00" not in times(t,group,5)  # 8 + 5 over the 12 of the place
    assert "12:00" not in times(t,s,5)
    owner,u=client_as(t)
    assert "12:00" not in times(t,group,5,desk=True)  # a real limit: the desk too
    with pytest.raises(ForceWarning,match=r"oltre i coperti del locale \(12\)"):book(t,group,12,party=5,actor=u,force=True)
    # the service's own places are tighter: they win
    with scope(t):Service.objects.filter(pk=group.pk).update(capacity=3)
    group.refresh_from_db()
    assert "12:00" not in times(t,group,4)

def test_minutes_grow_with_the_people_when_the_table_turns(table):
    t,s=table
    with scope(t):Service.objects.filter(pk=s.pk).update(turnover=True,duration=120,party_durations=[[2,90],[4,105]])
    s.refresh_from_db()
    assert [length(s,party=n) for n in (1,2,3,4,5,12)]==[90,90,105,105,120,120]
    b=book(t,s,12,party=3)
    assert b.end-b.start==timedelta(minutes=105)
    # without turnover the table is held until the window closes, whatever the people
    with scope(t):Service.objects.filter(pk=s.pk).update(turnover=False)
    s.refresh_from_db()
    assert length(s,party=2)==s.duration

def test_service_form_reads_the_minutes_and_checks_the_limits(table):
    t,s=table
    with scope(t):
        base={"name":"Cena","location":s.location_id,"capacity":40,"max_party":20,"turnover":"on","duration":120,"interval":15,"lead_minutes":0,"horizon_days":60,"cancel_cutoff_minutes":0,"last_arrival":0,"fields":"[]","active":"on","buffer_after":0}
        f=ServiceForm({**base,"party_durations":"4:105, 2 : 90","online_capacity":30,"pace_covers":10,"pace_bookings":0,"request_above":8},instance=s,tenant=t)
        assert f.is_valid(),f.errors
        assert f.cleaned_data["party_durations"]==[[2,90],[4,105]]
        f.save();s.refresh_from_db();assert s.party_durations==[[2,90],[4,105]] and s.online_capacity==30 and s.pace_covers==10
        assert ServiceForm(instance=s,tenant=t)["party_durations"].value()=="2:90, 4:105"
        bad=ServiceForm({**base,"party_durations":"due persone","online_capacity":50,"request_above":20},instance=s,tenant=t)
        assert not bad.is_valid() and {"party_durations","online_capacity","request_above"}<=set(bad.errors)

def test_restaurant_groups_above_eight_wait_for_a_yes(table):
    t,s=table
    p=initial("ristorante")
    assert p["request_above"]==8 and p["max_party"]==20
    with scope(t):Service.objects.filter(pk=s.pk).update(request_above=p["request_above"],max_party=p["max_party"])
    s.refresh_from_db()
    assert book(t,s,11,party=8).status=="confirmed"
    assert book(t,s,11,party=9).status=="pending"
    with pytest.raises(BookingError):book(t,s,11,party=21)  # above the online limit: the client calls
    owner,u=client_as(t)
    assert book(t,s,11,party=12,actor=u).status=="confirmed"  # the desk confirms at once
    page=owner.get(f"/book/{t.slug}/").content.decode()
    assert '"request_above": 8' in page and 'id="group-note"' in page
