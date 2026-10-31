"""2.14.0 (proposte 13 e 28, la parte che mancava): le sale con i loro coperti e la scelta del cliente (o Indifferente),
i tavoli con persone minime e massime, unioni e priorità, l'assegnazione spenta, a mano o proposta, online solo con un
tavolo adatto, la piantina con le posizioni, i tavoli dati con un clic e chi entra senza prenotare, e la scelta del
titolare tra un turno per servizio, turni fissi e rotazione."""
import json
import pytest
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,move_booking,BookingError,ForceWarning
from core.forms import ServiceForm
from core.tables import fit,taken
from tests.test_restaurant import day3,at,times,book
from tests.test_core import sample,payload,client_as

pytestmark=pytest.mark.django_db

@pytest.fixture
def room(sample):
    """A restaurant: lunch for 40 without time slots, a Sala with three tables (2, 4, 4 joinable with 2) and a Dehors of 6 covers."""
    t,o,s=sample
    with scope(t):
        Tenant.objects.filter(pk=t.pk).update(business_type="ristorante");t.refresh_from_db()
        Service.objects.filter(pk=s.pk).update(capacity=40,max_party=20,interval=15,turnover=False,lead_minutes=0)
        s.refresh_from_db()
        sala=Area.objects.create(tenant=t,location=s.location,name="Sala")
        dehors=Area.objects.create(tenant=t,location=s.location,name="Dehors",capacity=6)
        t2=Table.objects.create(tenant=t,area=sala,name="T2",min_covers=1,max_covers=2)
        t4=Table.objects.create(tenant=t,area=sala,name="T4",min_covers=3,max_covers=4,priority=2)
        t4b=Table.objects.create(tenant=t,area=sala,name="T4b",min_covers=3,max_covers=4,priority=1)
        t2.joins=[str(t4.pk)];t2.save();t4.joins=[str(t2.pk)];t4.save()
    return t,s,sala,dehors,t2,t4,t4b

def mode(t,s,how,online=False):
    with scope(t):Location.objects.filter(pk=s.location_id).update(tables=how,tables_online=online)
    s.refresh_from_db()

def test_area_chosen_by_client_with_its_own_covers(room):
    t,s,sala,dehors,*_=room
    from django.test import Client
    page=Client().get(f"/book/{t.slug}/").content.decode()
    assert '"areas": [{"id": "%s", "name": "Dehors"}' % dehors.pk in page and "Dove preferisci sederti?" in page  # the client's choice, never a table
    b=book(t,s,12,party=4,area=str(dehors.pk))
    assert b.area==dehors and b.tables==[]  # tables off: no table, the room asked for kept
    with scope(t):
        from core.booking import available
        free=lambda party,area:[x["start"][11:16] for x in available(t,s.pk,day3(),party=party,area=area)]
        assert "12:00" in free(2,str(dehors.pk)) and "12:00" not in free(3,str(dehors.pk))  # 4 of the 6 covers of the Dehors
        assert "12:00" in free(10,"") and "12:00" in free(10,str(sala.pk))  # Indifferente and the Sala are not bound by it
        with pytest.raises(BookingError,match="Sala non valida"):available(t,s.pk,day3(),party=1,area=str(Area.objects.exclude(pk__in=[sala.pk,dehors.pk]).first() or "00000000-0000-0000-0000-000000000000"))
    with pytest.raises(BookingError,match="Orario non disponibile"):book(t,s,12,party=3,area=str(dehors.pk))
    # the desk with Forza goes over it, said in words
    owner,u=client_as(t)
    with pytest.raises(ForceWarning,match="oltre i coperti di Dehors"):book(t,s,12,party=3,actor=u,force=True,area=str(dehors.pk))

def test_best_fit_smallest_then_priority_then_joined(room):
    t,s,sala,dehors,t2,t4,t4b=room
    with scope(t):
        b=book(t,s,12,party=2);start,end=b.busy_start,b.busy_end
        assert [x.name for x in fit(s.location,2,start,end)]==["T2"]  # the smallest that fits
        assert [x.name for x in fit(s.location,4,start,end)]==["T4b"]  # same size: the lower priority first
        assert sorted(x.name for x in fit(s.location,6,start,end))==["T2","T4"]  # none for 6: two that join
        assert fit(s.location,9,start,end)==[]
        assert fit(s.location,2,start,end,area=dehors)==[]  # only the tables of the room asked for

def test_assignment_off_manual_suggest(room):
    t,s,sala,dehors,t2,t4,t4b=room
    assert book(t,s,12,party=2).tables==[]  # off, the default
    mode(t,s,"manual")
    assert book(t,s,12,party=2).tables==[]  # online: the staff choose
    owner,u=client_as(t)
    b=book(t,s,12,party=2,actor=u,tables=[str(t4.pk)])  # the desk picks it
    assert b.tables==[str(t4.pk)] and b.area==sala
    with pytest.raises(BookingError,match="già occupato"):book(t,s,12,party=2,actor=u,tables=[str(t4.pk)])
    mode(t,s,"suggest")
    b=book(t,s,12,party=2)
    assert b.tables==[str(t2.pk)]  # suggested, online too
    b2=book(t,s,12,party=4)
    assert b2.tables==[str(t4b.pk)]
    assert book(t,s,12,party=4).tables==[]  # none left for 4: the booking stays, the staff seat it
    with scope(t):assert taken(s.location,b.busy_start,b.busy_end)=={str(t2.pk),str(t4.pk),str(t4b.pk)}

def test_online_only_with_a_table(room):
    t,s,sala,dehors,t2,t4,t4b=room
    mode(t,s,"manual",online=True)
    assert "12:00" in times(t,s,6)  # T2 and T4 joined
    assert "12:00" not in times(t,s,9)  # no table, no time, even with covers left
    owner,u=client_as(t)
    assert "12:00" in times(t,s,9,desk=True)  # the desk is not bound by it
    book(t,s,12,party=4,actor=u,tables=[str(t4.pk)]);book(t,s,12,party=4,actor=u,tables=[str(t4b.pk)])
    assert "12:00" in times(t,s,2) and "12:00" not in times(t,s,3)
    b=book(t,s,12,party=2)
    assert b.tables==[]  # manual: checked that one is there, the staff choose which

def test_move_keeps_or_refits_tables(room):
    t,s,sala,dehors,t2,t4,t4b=room
    mode(t,s,"suggest")
    with scope(t):Service.objects.filter(pk=s.pk).update(turnover=True,duration=60)
    s.refresh_from_db()
    a=book(t,s,12,party=2);assert a.tables==[str(t2.pk)]
    owner,u=client_as(t)
    b=book(t,s,14,party=2,actor=u,tables=[str(t4.pk)])
    with scope(t):
        move_booking(t,b.pk,{"service":str(s.pk),"party":2,"start":at(12)},u)  # T4 free at 12: kept
        b.refresh_from_db();assert b.tables==[str(t4.pk)]
    book(t,s,15,party=2,actor=u,tables=[str(t2.pk)])
    with scope(t):
        move_booking(t,a.pk,{"service":str(s.pk),"party":2,"start":at(15)},u)  # T2 taken at 15, no other table for 2
        a.refresh_from_db();assert a.tables==[]
        move_booking(t,a.pk,{"service":str(s.pk),"party":2,"start":at(17)},u)  # suggested again where one is free
        a.refresh_from_db();assert a.tables==[str(t2.pk)]

def test_floor_plan_place_assign_walkin_and_masking(room):
    t,s,sala,dehors,t2,t4,t4b=room
    mode(t,s,"manual")
    owner,u=client_as(t)
    b=book(t,s,12,party=2,first_name="Giulia",last_name="Bianchi")
    page=owner.get(f"/t/{t.slug}/tavoli/?sala={sala.pk}&data={day3().isoformat()}&ora=12:30")
    assert page.status_code==200 and b"Giulia Bianchi" in page.content and b"style=" not in page.content
    r=owner.post(f"/t/{t.slug}/tavoli/posto/",json.dumps({"table":str(t4.pk),"x":40.5,"y":120}),content_type="application/json")
    assert r.status_code==200
    with scope(t):t4.refresh_from_db();assert (t4.x,t4.y)==(40.5,95.0)
    assert b'data-x="40.5" data-y="95.0"' in owner.get(f"/t/{t.slug}/tavoli/?sala={sala.pk}").content  # a CSS number, never 40,5
    r=owner.post(f"/t/{t.slug}/tavoli/assegna/",json.dumps({"booking":str(b.pk),"tables":[str(t4.pk)]}),content_type="application/json")
    assert r.status_code==200
    with scope(t):b.refresh_from_db();assert b.tables==[str(t4.pk)] and b.area==sala
    detail=owner.get(f"/t/{t.slug}/bookings/{b.pk}/")
    assert b"T4" in detail.content and b"Piantina" in detail.content
    sheet=owner.get(f"/t/{t.slug}/agenda/stampa/?data={day3().isoformat()}")
    assert b"Tavolo T4" in sheet.content
    # a viewer sees the plan with the initials only and cannot move or assign
    viewer,_=client_as(t,role="viewer")
    page=viewer.get(f"/t/{t.slug}/tavoli/?sala={sala.pk}&data={day3().isoformat()}&ora=12:30")
    assert b"Giulia Bianchi" not in page.content and b"G. B." in page.content
    assert viewer.post(f"/t/{t.slug}/tavoli/assegna/",json.dumps({"booking":str(b.pk),"tables":[]}),content_type="application/json").status_code==403
    assert viewer.post(f"/t/{t.slug}/tavoli/posto/",json.dumps({"table":str(t4.pk),"x":1,"y":1}),content_type="application/json").status_code==403
    # taken table refused, empty list frees it
    other=book(t,s,12,party=2)
    r=owner.post(f"/t/{t.slug}/tavoli/assegna/",json.dumps({"booking":str(other.pk),"tables":[str(t4.pk)]}),content_type="application/json")
    assert r.status_code==400 and "occupato" in r.json()["error"]
    owner.post(f"/t/{t.slug}/tavoli/assegna/",json.dumps({"booking":str(b.pk),"tables":[]}),content_type="application/json")
    with scope(t):b.refresh_from_db();assert b.tables==[]

def test_walkin_seated_on_a_table(room):
    t,s,sala,dehors,t2,t4,t4b=room
    mode(t,s,"manual")
    with scope(t):Schedule.objects.all().update(start="00:00",end="23:59")
    owner,u=client_as(t)
    r=owner.post(f"/t/{t.slug}/tavoli/siedi/",{"sala":str(sala.pk),"service":str(s.pk),"party":"3","table":str(t4.pk)})
    assert r.status_code==302
    with scope(t):
        b=Booking.objects.get(origin="walkin")
        assert b.tables==[str(t4.pk)] and b.party==3 and b.status=="in_progress"
    # a seated client moved to another table
    r=owner.post(f"/t/{t.slug}/tavoli/assegna/",json.dumps({"booking":str(b.pk),"tables":[str(t4b.pk)]}),content_type="application/json")
    assert r.status_code==200
    with scope(t):b.refresh_from_db();assert b.tables==[str(t4b.pk)]

def test_tables_setup_pages_and_joins_both_ways(room):
    t,s,sala,dehors,t2,t4,t4b=room
    owner,u=client_as(t)
    assert owner.get(f"/t/{t.slug}/tavoli/sala/nuovo/").status_code==200
    assert owner.get(f"/t/{t.slug}/tavoli/sedia/nuovo/").status_code==404
    r=owner.post(f"/t/{t.slug}/tavoli/tavolo/nuovo/",{"area":str(sala.pk),"name":"T8","min_covers":"5","max_covers":"8","priority":"0","joins":[str(t4b.pk)]})
    assert r.status_code==302
    with scope(t):
        t8=Table.objects.get(name="T8");t4b.refresh_from_db()
        assert t8.joins==[str(t4b.pk)] and t4b.joins==[str(t8.pk)]
    bad=owner.post(f"/t/{t.slug}/tavoli/tavolo/nuovo/",{"area":str(sala.pk),"name":"X","min_covers":"6","max_covers":"2","priority":"0"})
    assert bad.status_code==200 and b"Persone minime" in bad.content
    owner.post(f"/t/{t.slug}/tavoli/tavolo/{t8.pk}/",{"remove":"1"})
    with scope(t):t4b.refresh_from_db();assert t4b.joins==[] and not Table.objects.filter(name="T8").exists()
    viewer,_=client_as(t,role="viewer")
    assert viewer.get(f"/t/{t.slug}/tavoli/sala/nuovo/").status_code==403

def test_owner_switches_how_the_tables_turn(room):
    t,s,*_=room
    base={"name":"Pranzo","location":str(s.location_id),"capacity":40,"max_party":20,"last_arrival":0,"interval":15,"duration":90,"lead_minutes":0,"horizon_days":60,"cancel_cutoff_minutes":0,"buffer_after":0,"fields":"[]","active":"on"}
    with scope(t):
        f=ServiceForm(instance=s,tenant=t)
        assert f["mode"].value()=="service" and "turnover" not in f.fields and "kind" not in f.fields
        for how,kind,turnover in (("rotation","capacity",True),("fixed","shift",False),("service","capacity",False)):
            f=ServiceForm({**base,"mode":how},instance=s,tenant=t)
            assert f.is_valid(),f.errors
            f.save();s.refresh_from_db();assert (s.kind,s.turnover)==(kind,turnover)
        from core.forms import LocationForm
        assert "tables" in LocationForm(instance=s.location,tenant=t).fields
        f=LocationForm({"name":"Centro","capacity":0,"tax_nights":0,"tax_age":0,"assign":"alpha"},instance=s.location,tenant=t)
        assert f.is_valid(),f.errors
        assert f.cleaned_data["tables"]=="off"  # a form without the choice keeps the tables off
        Tenant.objects.filter(pk=t.pk).update(business_type="corso");t.refresh_from_db()
        assert "mode" not in ServiceForm(instance=s,tenant=t).fields and "tables" not in LocationForm(instance=s.location,tenant=t).fields  # only restaurants
