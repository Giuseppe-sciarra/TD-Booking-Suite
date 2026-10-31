"""2.14.0, fase 4: chiusure dal … al … a ogni livello, festività italiane con Pasqua e il santo patrono, Blocca orario
dall'agenda, due fasce e settimane alterne per l'operatore; turni e date evento al titolare, date ripetute in serie,
chiudi e riapri un turno per un giorno."""
import secrets
from datetime import datetime,date,time,timedelta
from django.utils import timezone
import pytest
from core.models import *
from core.tenancy import scope
from core.calendar import easter,holidays,windows
from core.booking import create_booking,move_booking,available,BookingError,ForceWarning
from core.agenda import turns
from core.portal import calendar_conflicts
from core.templatetags.presentation import row_label
from tests.test_core import sample,payload,client_as
from tests.test_clients import staff_payload
from tests.test_desk import clock,salon,at,ROME

pytestmark=pytest.mark.django_db

def times(t,svc,day,res=None):return [x["start"][11:16] for x in available(t,svc.pk,day,resource_id=str(res.pk) if res else None)]
def key():return secrets.token_urlsafe(24)
def open_on(t,day):return [a for a,b in windows(t,day) if a.astimezone(ROME).date()==day]

def test_closure_ranges_at_every_level_and_single_dates(sample,clock):
    t,o,s=sample;svc,marco=salon(t,lead=0)
    day=clock[0].date()+timedelta(days=3)
    with scope(t):
        assert times(t,svc,day,marco)
        for level in ({},{"location":svc.location},{"service":svc},{"resource":marco}):
            row=ExceptionDay.objects.create(tenant=t,date=day-timedelta(days=2),until=day+timedelta(days=1),label="Ferie",**level)
            assert times(t,svc,day,marco)==[] and times(t,svc,day-timedelta(days=2),marco)==[],level
            assert times(t,svc,day+timedelta(days=2),marco),level  # the day after the range is open again
            row.delete()
        # a single date, as before: that day only
        ExceptionDay.objects.create(tenant=t,date=day,resource=marco)
        assert times(t,svc,day,marco)==[] and times(t,svc,day+timedelta(days=1),marco)
        ExceptionDay.objects.all().delete()
        # special hours for every day of a range
        ExceptionDay.objects.create(tenant=t,date=day,until=day+timedelta(days=1),closed=False,start=time(9),end=time(12))
        assert max(times(t,svc,day+timedelta(days=1),marco))=="11:30" and max(times(t,svc,day+timedelta(days=2),marco))=="17:30"
        ExceptionDay.objects.all().delete()
        # a stay across a closed range is refused, one after it is fine
        hotel=Service.objects.create(tenant=t,location=svc.location,name="Camera",kind="day",capacity=2,max_party=2,lead_minutes=0,checkin=time(15),checkout=time(10))
        ExceptionDay.objects.create(tenant=t,date=day+timedelta(days=1),until=day+timedelta(days=2),service=hotel)
        stay=lambda d,n:{**payload(hotel),"start":at(d,15),"end_date":(d+timedelta(days=n)).isoformat(),"request_key":key()}
        with pytest.raises(BookingError,match="non consentiti"):create_booking(t,stay(day,4))
        assert create_booking(t,stay(day+timedelta(days=3),2))[1]

def test_closure_form_one_row_from_day_to_day_and_bookings_to_call(sample,clock):
    t,o,s=sample;svc,marco=salon(t,lead=0)
    owner,u=client_as(t)
    day=clock[0].date()+timedelta(days=3)
    with scope(t):b,_=create_booking(t,payload(svc,start=at(day,10),resource=str(marco.pk)),None)
    url=f"/t/{t.slug}/catalog/exceptions/new/"
    form={"date":(day-timedelta(days=3)).isoformat(),"until":(day+timedelta(days=10)).isoformat(),"closed":"on","label":"Ferie di Marco","resource":str(marco.pk)}
    page=owner.post(url,form).content.decode()
    # the booking inside the range is listed to call back, as for every catalog change
    assert "prenotazioni da verificare" in page and str(b.pk)[:8] in page
    with scope(t):assert not ExceptionDay.objects.exists()
    assert owner.post(url,{**form,"confirm_conflicts":"on"}).status_code==302
    with scope(t):
        row=ExceptionDay.objects.get()
        assert row.until==day+timedelta(days=10) and Booking.objects.get(pk=b.pk).status=="confirmed"
        assert row_label(row).startswith(f"Dal {day-timedelta(days=3):%d/%m/%Y} al {day+timedelta(days=10):%d/%m/%Y}")
        assert str(b.pk)[:8] in calendar_conflicts(t)
    assert "Ferie di Marco" in owner.get(f"/t/{t.slug}/catalog/exceptions/").content.decode()
    # the same day twice is a single date; the end before the start is refused
    owner.post(url,{"date":(day+timedelta(days=20)).isoformat(),"until":(day+timedelta(days=20)).isoformat(),"closed":"on","confirm_conflicts":"on"})
    with scope(t):assert ExceptionDay.objects.get(date=day+timedelta(days=20)).until is None
    assert "viene prima" in owner.post(url,{"date":day.isoformat(),"until":(day-timedelta(days=1)).isoformat(),"closed":"on"}).content.decode()

@pytest.mark.parametrize("year,sunday",[(1818,"03-22"),(2000,"04-23"),(2019,"04-21"),(2024,"03-31"),(2025,"04-20"),(2026,"04-05"),(2027,"03-28"),(2038,"04-25"),(2285,"03-22")])
def test_easter(year,sunday):
    assert easter(year)==date.fromisoformat(f"{year}-{sunday}")
    days=dict((d,n) for d,n in holidays(year))
    assert days[easter(year)]=="Pasqua" and days[easter(year)+timedelta(days=1)]=="Lunedì dell'Angelo"

def test_holidays_list():
    days={d.strftime("%m-%d") for d,_ in holidays(2027,"06-29","Santi Pietro e Paolo")}
    assert {"01-01","01-06","04-25","05-01","06-02","08-15","10-04","11-01","12-08","12-25","12-26","03-28","03-29","06-29"}==days
    assert date(2025,10,4) not in dict(holidays(2025))  # San Francesco is a holiday again from 2026
    assert len(holidays(2027,"02-29","X"))==13  # no 29 February that year: no patron

def test_holidays_in_one_click_idempotent_with_one_left_open(sample):
    t,o,s=sample
    owner,u=client_as(t)
    year=timezone.now().year+1;liberation=date(year,4,25);patron=date(year,6,29)
    url=f"/t/{t.slug}/catalog/exceptions/festivita/"
    page=owner.get(f"{url}?anno={year}").content.decode()
    assert "Pasqua" in page and f'value="{liberation}" checked' in page  # the first time all are ticked
    chosen=[d.isoformat() for d,_ in holidays(year) if d!=liberation]
    form={"anno":year,"day":chosen+[patron.isoformat()],"patron_day":patron.isoformat(),"patron_name":"Santi Pietro e Paolo"}
    assert owner.post(url,form).status_code==302
    with scope(t):
        rows=ExceptionDay.objects.filter(date__year=year,location=None,service=None,resource=None)
        assert rows.count()==len(chosen)+1 and rows.get(date=patron).label=="Santi Pietro e Paolo" and rows.get(date=easter(year)).label=="Pasqua"
        assert open_on(t,patron)==[] and open_on(t,easter(year)+timedelta(days=1))==[] and open_on(t,liberation)  # 25 April stays open
        t.refresh_from_db();assert (t.patron_day,t.patron_name)==("06-29","Santi Pietro e Paolo")
    owner.post(url,form)  # again: nothing twice
    with scope(t):assert ExceptionDay.objects.filter(date__year=year).count()==len(chosen)+1
    page=owner.get(f"{url}?anno={year}").content.decode()
    # the patron stays for the next years; the one left open starts unticked
    assert f'value="{liberation}"' in page and f'value="{liberation}" checked' not in page and 'value="Santi Pietro e Paolo"' in page
    with scope(t):Audit.objects.filter(action="catalog.holidays").exists()

def test_blocks_take_the_time_but_are_not_bookings(sample,clock):
    t,o,s=sample;svc,marco=salon(t,lead=0)
    owner,u=client_as(t)
    day=clock[0].date()+timedelta(days=1)
    assert "Blocca orario" in owner.get(f"/t/{t.slug}/bookings/?vista=giorno&data={day}").content.decode()
    page=owner.get(f"/t/{t.slug}/agenda/blocca/?operatore={marco.pk}&data={day}&ora=12:00").content.decode()
    assert 'value="12:00"' in page and 'value="13:00"' in page and "Formazione" in page
    r=owner.post(f"/t/{t.slug}/agenda/blocca/",{"resource":str(marco.pk),"day":day.isoformat(),"start":"12:00","end":"13:00","reason":"break","note":"Pranzo con il fornitore","weekly":"on","until":(day+timedelta(days=7)).isoformat()})
    assert r.status_code==302
    with scope(t):
        blk=TimeBlock.objects.get()
        assert blk.note=="Pranzo con il fornitore" and "fornitore" not in blk.pii  # sealed
        assert "11:30" in times(t,svc,day,marco) and "12:00" not in times(t,svc,day,marco) and "12:30" not in times(t,svc,day,marco) and "13:00" in times(t,svc,day,marco)
        assert "12:00" not in times(t,svc,day+timedelta(days=7),marco) and "12:00" in times(t,svc,day+timedelta(days=14),marco)  # weekly, until that day
        assert "12:00" in times(t,svc,day+timedelta(days=1),marco)
        assert not Booking.objects.exists()  # not a booking: counted nowhere
    agenda=owner.get(f"/t/{t.slug}/bookings/?vista=giorno&data={day}").content.decode()
    assert 'class="agenda-block"' in agenda and "Pausa" in agenda and "Nessun appuntamento" in agenda and f"?operatore={marco.pk}&amp;data={day}&amp;ora=12:00" not in agenda
    # over a booking: listed first, then it stays
    with scope(t):b,_=create_booking(t,payload(svc,start=at(day,15),resource=str(marco.pk)),None)
    meeting={"resource":str(marco.pk),"day":day.isoformat(),"start":"15:00","end":"16:00","reason":"meeting"}
    page=owner.post(f"/t/{t.slug}/agenda/blocca/",meeting).content.decode()
    assert str(b.pk)[:8] in page and "confirm_conflicts" in page
    assert owner.post(f"/t/{t.slug}/agenda/blocca/",{**meeting,"confirm_conflicts":"on"}).status_code==302
    with scope(t):assert TimeBlock.objects.count()==2 and Booking.objects.get(pk=b.pk).status=="confirmed" and str(b.pk)[:8] in calendar_conflicts(t)
    # an operator blocks only his own time
    op,ou=client_as(t,role="operator")
    with scope(t):
        other=Resource.objects.create(tenant=t,location=svc.location,name="Luca")
        Membership.objects.filter(user=ou).update(resources=[str(other.pk)])
    assert op.post(f"/t/{t.slug}/agenda/blocca/",{**meeting,"start":"17:00","end":"17:30"}).status_code==200
    assert op.get(f"/t/{t.slug}/agenda/blocca/{blk.pk}/").status_code==404
    owner.post(f"/t/{t.slug}/agenda/blocca/{blk.pk}/",{"remove":"1"})
    with scope(t):assert TimeBlock.objects.count()==1 and "12:00" in times(t,svc,day,marco)

def test_forza_goes_over_a_block_with_a_warning(sample,clock):
    t,o,s=sample;svc,marco=salon(t,lead=0)
    owner,u=client_as(t)
    day=clock[0].date()+timedelta(days=1)
    with scope(t):
        TimeBlock.objects.create(tenant=t,resource=marco,day=day,start=time(15),end=time(16),reason="training")
        data=staff_payload(svc,start=at(day,15),resource=str(marco.pk))
        with pytest.raises(BookingError,match="non disponibile"):create_booking(t,data,actor=u)
        with pytest.raises(ForceWarning,match=r"nell'orario bloccato di Marco \(formazione\)"):create_booking(t,{**data,"force":True,"request_key":key()},actor=u)
        b,_=create_booking(t,{**data,"force":True,"force_ok":True,"request_key":key()},actor=u)
        assert b.snapshot["forced"]==["nell'orario bloccato di Marco (formazione)"]
        # the client moving from his link meets the block as any other busy time
        mine,_=create_booking(t,payload(svc,start=at(day,10),resource=str(marco.pk)),None)
        with pytest.raises(BookingError):move_booking(t,mine.pk,{"start":at(day,15,30),"resource":str(marco.pk)},None,desk=False)
        ExceptionDay.objects.create(tenant=t,date=day+timedelta(days=1),until=day+timedelta(days=3),resource=marco)
        with pytest.raises(BookingError):move_booking(t,mine.pk,{"start":at(day+timedelta(days=2),10),"resource":str(marco.pk)},None,desk=False)
        assert move_booking(t,mine.pk,{"start":at(day+timedelta(days=4),10),"resource":str(marco.pk)},None,desk=False).start.astimezone(ROME).date()==day+timedelta(days=4)

def test_two_bands_and_alternate_weeks(sample,clock):
    t,o,s=sample;svc,marco=salon(t,lead=0)
    owner,u=client_as(t)
    day=clock[0].date()+timedelta(days=1)
    form={"name":"Marco","location":str(marco.location_id),"active":"on","services":[str(svc.pk)],"own_hours":"on"}
    for d in range(7):form.update({f"day_{d}":"on",f"start_{d}":"09:00",f"end_{d}":"13:00",f"start2_{d}":"14:30",f"end2_{d}":"18:00"})
    url=f"/t/{t.slug}/catalog/resources/{marco.pk}/"
    assert owner.post(url,form).status_code==302
    with scope(t):
        assert Schedule.objects.filter(resource=marco).count()==14
        hours=times(t,svc,day,marco)
        assert "12:30" in hours and "13:00" not in hours and "14:00" not in hours and "14:30" in hours
    page=owner.get(url).content.decode()
    assert 'name="start2_0" value="14:30"' in page and "Ha più di due fasce" not in page
    assert "si sovrappongono" in owner.post(url,{**form,"start2_3":"12:00"}).content.decode()
    # week A and week B: every two weeks from their first day
    with scope(t):
        Schedule.objects.filter(resource=marco).delete()
        Schedule.objects.create(tenant=t,resource=marco,weekday=day.weekday(),start=time(9),end=time(13),valid_from=day,every_weeks=2)
        Schedule.objects.create(tenant=t,resource=marco,weekday=day.weekday(),start=time(14),end=time(18),valid_from=day+timedelta(days=7),every_weeks=2)
        a,b,c=times(t,svc,day,marco),times(t,svc,day+timedelta(days=7),marco),times(t,svc,day+timedelta(days=14),marco)
        assert (min(a),max(a),min(b),max(b))==("09:00","12:30","14:00","17:30") and a==c
        assert "ogni 2 settimane dal" in row_label(Schedule.objects.filter(resource=marco).first())
    assert "Ha più di due fasce nello stesso giorno o settimane alterne" in owner.get(url).content.decode()
    # the agency's page asks from when to count
    r=owner.post(f"/t/{t.slug}/catalog/schedules/new/",{"weekday":"0","start":"09:00","end":"12:00","every_weeks":"2"}).content.decode()
    assert "Valido dal il giorno da cui contare" in r

def test_owner_runs_turns_and_dates_and_new_services_keep_the_engine(sample,clock):
    t,o,s=sample
    with scope(t):Service.objects.filter(pk=s.pk).update(kind="shift",capacity=40,max_party=8)
    owner,u=client_as(t)
    day=clock[0].date()+timedelta(days=2)
    url=f"/t/{t.slug}/catalog/turns/new/"
    turn={"name":"Pranzo 1","service":str(s.pk),"weekday":str(day.weekday()),"start":"19:00","end":"21:00","capacity":"20","active":"on"}
    assert "fuori dagli orari di apertura" in owner.post(url,turn).content.decode()
    assert "tra 1 e la capienza" in owner.post(url,{**turn,"start":"12:00","end":"14:00","capacity":"50"}).content.decode()
    assert owner.post(url,{**turn,"start":"12:00","end":"14:00"}).status_code==302
    with scope(t):assert any(x["occurrence"] for x in available(t,s.pk,day))
    assert owner.get(f"/t/{t.slug}/catalog/occurrences/new/").status_code==200
    # a new service of the owner follows the engine of the catalog: here the turns
    r=owner.post(f"/t/{t.slug}/catalog/services/new/",{"name":"Cena","location":str(s.location_id),"capacity":"30","max_party":"6","lead_minutes":"60","horizon_days":"60","cancel_cutoff_minutes":"0","active":"on","fields":"[]"})
    assert r.status_code==302
    with scope(t):assert Service.objects.get(name="Cena").kind=="shift"
    # who does not edit the catalog does not see them
    rec,ru=client_as(t,role="reception");Membership.objects.filter(user=ru).update(can_schedule=False)
    assert rec.get(f"/t/{t.slug}/catalog/turns/").status_code==403 and rec.get(f"/t/{t.slug}/catalog/occurrences/").status_code==403

def test_event_dates_repeat_and_change_as_a_series(sample,clock):
    t,o,s=sample
    with scope(t):Service.objects.filter(pk=s.pk).update(kind="event",capacity=10,max_party=4)
    owner,u=client_as(t)
    first=clock[0].date()+timedelta(days=1)
    url=f"/t/{t.slug}/catalog/occurrences/new/"
    new={"service":str(s.pk),"start":f"{first}T10:00","end":f"{first}T12:00","capacity":"10","active":"on","repeat":"1","repeat_until":(first+timedelta(days=20)).isoformat()}
    assert "Fino al" in owner.post(url,{**new,"repeat_until":""}).content.decode() and owner.post(url,new).status_code==302
    with scope(t):
        dates=list(Occurrence.objects.order_by("start"))
        assert [o.start.astimezone(ROME).date() for o in dates]==[first,first+timedelta(days=7),first+timedelta(days=14)]
        assert len({o.series for o in dates})==1 and dates[0].series and {o.start.astimezone(ROME).time() for o in dates}=={time(10)}
    # some weekdays, every two weeks
    monday=first+timedelta(days=7-first.weekday())
    owner.post(url,{**new,"start":f"{monday}T15:00","end":f"{monday}T16:00","repeat":"2","repeat_days":["0","3"],"repeat_until":(monday+timedelta(days=17)).isoformat()})
    with scope(t):
        other=Occurrence.objects.exclude(series=dates[0].series)
        assert sorted(o.start.astimezone(ROME).date() for o in other)==[monday,monday+timedelta(days=3),monday+timedelta(days=14),monday+timedelta(days=17)]
    # this and the next ones: the new hours and places; the earlier ones stay
    edit=f"/t/{t.slug}/catalog/occurrences/{dates[1].pk}/"
    assert "Questa e le successive della serie" in owner.get(edit).content.decode()
    second=dates[1].start.astimezone(ROME).date()
    assert owner.post(edit,{"service":str(s.pk),"start":f"{second}T11:00","end":f"{second}T13:00","capacity":"8","active":"on","scope":"series"}).status_code==302
    with scope(t):
        got=[(o.start.astimezone(ROME).time(),o.capacity) for o in Occurrence.objects.filter(series=dates[0].series).order_by("start")]
        assert got==[(time(10),10),(time(11),8),(time(11),8)]
        last=Occurrence.objects.filter(series=dates[0].series).order_by("start").last()
        b,_=create_booking(t,{**payload(s),"start":last.start.isoformat(),"occurrence":str(last.pk)},None)
    # a booked date of the series is not moved under the client
    page=owner.post(edit,{"service":str(s.pk),"start":f"{second}T12:00","end":f"{second}T13:00","capacity":"8","active":"on","scope":"series"}).content.decode()
    assert str(b.pk)[:8] in page
    # removing this and the next ones: the booked client is listed first
    remove=f"/t/{t.slug}/catalog/occurrences/{dates[1].pk}/remove/"
    page=owner.post(remove,{"series":"1"}).content.decode()
    assert "Prima di togliere queste date" in page and "Mario Rossi" in page and "Togli 2 date" in page
    with scope(t):assert Occurrence.objects.filter(series=dates[0].series,active=True).count()==3
    owner.post(remove,{"series":"1","confirm":"1"})
    with scope(t):
        assert not Occurrence.objects.filter(pk=dates[1].pk).exists() and Occurrence.objects.get(pk=last.pk).active is False
        assert Booking.objects.get(pk=b.pk).status=="confirmed" and Occurrence.objects.filter(pk=dates[0].pk,active=True).exists()
    # a single date, as before
    assert owner.post(url,{**new,"repeat":"0","start":f"{first}T18:00","end":f"{first}T19:00"}).status_code==302
    with scope(t):assert Occurrence.objects.get(start=datetime.fromisoformat(at(first,18))).series is None
    # a date without bookings goes at once
    assert owner.post(f"/t/{t.slug}/catalog/occurrences/{dates[0].pk}/remove/").status_code==302
    with scope(t):assert not Occurrence.objects.filter(pk=dates[0].pk).exists()

def test_close_and_reopen_a_turn_for_one_day(sample,clock):
    t,o,s=sample;today=clock[0].date()
    with scope(t):
        Schedule.objects.all().delete()
        for d in range(7):
            Schedule.objects.create(tenant=t,weekday=d,start=time(12),end=time(15))
            Schedule.objects.create(tenant=t,weekday=d,start=time(19),end=time(23))
        Service.objects.filter(pk=s.pk).update(turnover=False,capacity=20,max_party=8)
        b,_=create_booking(t,payload(s,start=at(today,20)),None)
    owner,u=client_as(t)
    dash=owner.get(f"/t/{t.slug}/").content.decode()
    assert "Turni di oggi" in dash and "Pranzo" in dash and "Cena" in dash
    with scope(t):dinner=next(x for x in turns(t,today) if x["name"]=="Cena")
    post={"service":str(s.pk),"day":today.isoformat(),"start":dinner["start"].isoformat(),"action":"close"}
    page=owner.post(f"/t/{t.slug}/turni/",post).content.decode()
    assert "Prima di chiudere" in page and "Mario Rossi" in page and "Chiudi il turno" in page
    with scope(t):assert not TimeBlock.objects.exists()
    assert owner.post(f"/t/{t.slug}/turni/",{**post,"confirm":"1"}).status_code==302
    with scope(t):
        slots=times(t,s,today)
        assert "12:00" in slots and not [x for x in slots if x>="19:00"] and Booking.objects.get(pk=b.pk).status=="confirmed"
        assert next(x for x in turns(t,today) if x["name"]=="Cena")["closed"] and [x for x in times(t,s,today+timedelta(days=1)) if x>="19:00"]
        assert Audit.objects.filter(action="agenda.turn_closed").exists()
    assert "Riapri" in owner.get(f"/t/{t.slug}/").content.decode()
    owner.post(f"/t/{t.slug}/turni/",{**post,"action":"open"})
    with scope(t):assert "19:00" in times(t,s,today) and not TimeBlock.objects.exists()
    # another day, picked on the card
    assert "Turni di " in owner.get(f"/t/{t.slug}/?turni={today+timedelta(days=2)}").content.decode()

def test_close_a_turn_of_the_shift_engine(sample,clock):
    from core.shifts import materialize
    t,o,s=sample;today=clock[0].date()
    owner,u=client_as(t)
    with scope(t):
        Service.objects.filter(pk=s.pk).update(kind="shift",capacity=40,max_party=8);s.refresh_from_db()
        rule=ShiftRule.objects.create(tenant=t,service=s,name="Primo turno",weekday=today.weekday(),start=time(12),end=time(14),capacity=20)
        materialize(rule)
        turn=next(x for x in turns(t,today) if x["name"]=="Primo turno")
    owner.post(f"/t/{t.slug}/turni/",{"service":str(s.pk),"day":today.isoformat(),"start":turn["start"].isoformat(),"action":"close"})
    with scope(t):
        assert available(t,s.pk,today)==[] and available(t,s.pk,today+timedelta(days=7))
        occ=Occurrence.objects.filter(rule=rule,start=turn["start"]).get()
        data=staff_payload(s,start=occ.start.isoformat(),occurrence=str(occ.pk),force=True)
        with pytest.raises(ForceWarning,match="in un turno chiuso"):create_booking(t,data,actor=u)
