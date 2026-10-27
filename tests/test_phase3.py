"""Stays and rentals with named units (rooms, vehicles): whole stays, capacity, minimum nights, closures,
cancellation policy, concurrency, moves. Synthetic data only."""
import json,secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import date,datetime,time as clock,timedelta
from zoneinfo import ZoneInfo
import pytest
from django.db import connection,connections
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,move_booking,BookingError,free_units,change_status
from core.crypto import token_hash
from tests.test_core import sample,payload,client_as

pytestmark=pytest.mark.django_db
TZ=ZoneInfo("Europe/Rome")

def next_weekday(weekday,weeks=1):
    """A date at least `weeks` weeks ahead falling on Python weekday (0 = Monday)."""
    d=timezone.now().astimezone(TZ).date()+timedelta(days=7*weeks)
    return d+timedelta(days=(weekday-d.weekday())%7)

@pytest.fixture
def tenant():
    return Tenant.objects.create(slug="camere",name="Hotel di prova")

DAY_END=clock(23,59,59,999999)
def imported(t):
    """Two rooms in one location, two nights minimum, check-in 14 and check-out 10, cancellation up to 48
    hours before; Camera Blu is already taken for three nights from next_weekday(2,3)."""
    with scope(t):
        mare=Location.objects.create(tenant=t,name="Mare")
        for d in range(7):Schedule.objects.create(tenant=t,weekday=d,start=clock(0),end=DAY_END)
        camere=Service.objects.create(tenant=t,location=mare,name="Camere",kind="day",max_party=4,min_days=2,max_days=366,checkin=clock(14),checkout=clock(10),
            lead_minutes=0,horizon_days=730,cancel_cutoff_minutes=2880,resource_label="Camera")
        blu=Resource.objects.create(tenant=t,location=mare,name="Camera Blu",capacity=2,description="Vista giardino",photo_url="https://example.invalid/blu.jpg")
        rossa=Resource.objects.create(tenant=t,location=mare,name="Camera Rossa",capacity=4,description="Quadrupla")
        for r in (blu,rossa):ServiceResource.objects.create(tenant=t,service=camere,resource=r)
        create_booking(t,stay_payload(camere,next_weekday(2,3),3,resource=str(blu.pk)))

# ---------------- parity of the engine (N01)
def stay_payload(svc,arrival,nights,party=2,resource="",**extra):
    data={"service":str(svc.pk),"start":datetime.combine(arrival,svc.checkin,TZ).isoformat(),"end_date":(arrival+timedelta(days=nights)).isoformat(),
        "party":party,"resource":resource,"first_name":"Ospite","last_name":"Test","email":secrets.token_hex(3)+"@example.invalid","phone":"+393331234567","request_key":secrets.token_urlsafe(24)}
    data.update(extra);return data

def test_unit_kept_for_whole_stay(tenant):
    imported(tenant)
    with scope(tenant):
        camere=Service.objects.get(name="Camere");blu=Resource.objects.get(name="Camera Blu");rossa=Resource.objects.get(name="Camera Rossa")
        stay=next_weekday(2,3)  # Blu is taken by the imported booking 502 for 3 nights
        b,_=create_booking(tenant,stay_payload(camere,stay+timedelta(days=1),2))
        assert b.resource==rossa and b.snapshot["resource"]=="Camera Rossa"
        with pytest.raises(BookingError):create_booking(tenant,stay_payload(camere,stay,2))  # both taken
        later,_=create_booking(tenant,stay_payload(camere,stay+timedelta(days=3),2))
        assert later.resource==blu  # free again from the checkout day

def test_unit_capacity_min_days_and_choice(tenant):
    imported(tenant)
    with scope(tenant):
        camere=Service.objects.get(name="Camere");blu=Resource.objects.get(name="Camera Blu")
        arrival=next_weekday(0,6)
        with pytest.raises(BookingError,match="minima"):create_booking(tenant,stay_payload(camere,arrival,1))
        with pytest.raises(BookingError):create_booking(tenant,stay_payload(camere,arrival,2,party=3,resource=str(blu.pk)))  # Blu holds 2
        four,_=create_booking(tenant,stay_payload(camere,arrival,2,party=4))
        assert four.resource.name=="Camera Rossa"

def test_unit_slots_api(tenant):
    imported(tenant)
    arrival=next_weekday(0,6)
    with scope(tenant):camere=Service.objects.get(name="Camere")
    r=Client().get(f"/book/{tenant.slug}/api/slots/",{"service":str(camere.pk),"date":arrival.isoformat(),"end_date":(arrival+timedelta(days=2)).isoformat(),"party":2})
    slots=r.json()["slots"]
    assert [s["resource_name"] for s in slots]==["Camera Blu","Camera Rossa"]
    assert slots[0]["description"]=="Vista giardino" and slots[0]["photo"].startswith("https://")
    r=Client().get(f"/book/{tenant.slug}/api/slots/",{"service":str(camere.pk),"date":arrival.isoformat(),"end_date":(arrival+timedelta(days=2)).isoformat(),"party":3})
    assert [s["resource_name"] for s in r.json()["slots"]]==["Camera Rossa"]

def test_unit_blocked_by_resource_closure(tenant):
    imported(tenant)
    with scope(tenant):
        camere=Service.objects.get(name="Camere");blu=Resource.objects.get(name="Camera Blu")
        arrival=next_weekday(0,8)
        ExceptionDay.objects.create(tenant=tenant,resource=blu,date=arrival+timedelta(days=1),closed=True,label="Manutenzione")
        b,_=create_booking(tenant,stay_payload(camere,arrival,2,party=2))
        assert b.resource.name=="Camera Rossa"

def test_cancellation_cutoff_uses_booking_policy(tenant):
    imported(tenant)
    with scope(tenant):
        camere=Service.objects.get(name="Camere")
        arrival=(timezone.now()+timedelta(days=1)).astimezone(TZ).date()  # inside the 48h limit
        b,_=create_booking(tenant,stay_payload(camere,arrival,2))
        token=b.data["cancel_token"]
        camere.cancel_cutoff_minutes=0;camere.save()  # a later change does not rewrite the agreed policy
    c=Client()
    r=c.post(f"/action/{tenant.slug}/{token}/")
    assert r.status_code==400 and "termine" in r.content.decode()
    with scope(tenant):
        assert Booking.objects.get(pk=b.pk).status=="confirmed"
        change_status(tenant,Booking.objects.get(pk=b.pk),"cancelled")  # the operator can still cancel
        assert Booking.objects.get(pk=b.pk).status=="cancelled"

@pytest.mark.django_db(transaction=True)
def test_concurrent_requests_never_share_a_unit(tenant):
    if connection.vendor!="postgresql":pytest.skip("Richiede PostgreSQL reale")
    imported(tenant)
    with scope(tenant):
        camere=Service.objects.get(name="Camere")
        Resource.objects.filter(name="Camera Rossa").update(active=False)  # one unit left
    arrival=next_weekday(0,9);gate=Barrier(2)
    def run():
        connections.close_all();gate.wait(timeout=10)
        try:
            with scope(tenant):return create_booking(tenant,stay_payload(camere,arrival,2))[0].resource_id
        except BookingError:return None
        finally:connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
    assert sum(x is not None for x in results)==1

def test_move_keeps_unit_rules(tenant):
    imported(tenant)
    with scope(tenant):
        camere=Service.objects.get(name="Camere")
        arrival=next_weekday(0,10)
        b,_=create_booking(tenant,stay_payload(camere,arrival,2))
        moved=move_booking(tenant,b.pk,{"service":str(camere.pk),"start":datetime.combine(arrival+timedelta(days=1),camere.checkin,TZ).isoformat(),"end_date":(arrival+timedelta(days=3)).isoformat(),"party":2,"resource":str(b.resource_id)},None)
        assert moved.resource_id==b.resource_id
        with pytest.raises(BookingError,match="minima"):
            move_booking(tenant,b.pk,{"service":str(camere.pk),"start":datetime.combine(arrival,camere.checkin,TZ).isoformat(),"end_date":(arrival+timedelta(days=1)).isoformat(),"party":2},None)
