"""2.10.0: the day agenda of appointments, one column for each operator, and the booking started from a free space."""
from datetime import datetime,time,timedelta
import pytest
from zoneinfo import ZoneInfo
from django.utils import timezone
from core.booking import create_booking
from core.models import *
from core.profile import apply_profile
from core.tenancy import scope
from tests.test_core import sample,client_as,payload
pytestmark=pytest.mark.django_db

def next_open_day():
    day=timezone.localdate()+timedelta(days=2)
    while day.weekday() in (0,6):day+=timedelta(days=1)  # the salon works Tuesday to Saturday
    return day

def test_the_day_has_a_column_for_each_operator(sample):
    t,o,s=sample
    apply_profile(t,"parrucchiere",["Centro"])
    owner,_=client_as(t,role="owner")
    day=next_open_day();tz=ZoneInfo(t.timezone)
    with scope(t):
        taglio=Service.objects.get(name="Taglio");anna,bruno=Resource.objects.order_by("name")
        Resource.objects.filter(pk=anna.pk).update(name="Anna");Resource.objects.filter(pk=bruno.pk).update(name="Bruno")
        create_booking(t,payload(taglio,resource=str(anna.pk),start=datetime.combine(day,time(10),tz).isoformat(),first_name="Giulia",last_name="Rossi"))
        # Bruno works only in the afternoon that day
        Schedule.objects.create(tenant=t,resource=bruno,weekday=day.weekday(),start=time(14),end=time(19))
    page=owner.get(f"/t/{t.slug}/bookings/?data={day.isoformat()}").content.decode()
    assert "data-agenda" in page and ">Anna</strong>" in page and ">Bruno</strong>" in page
    assert "10:00–10:30 Giulia Rossi" in page and "<small>Taglio</small>" in page
    # free spaces book with that operator, day and time; Anna's 10:00 is taken, Bruno's morning is closed
    new=f"/t/{t.slug}/bookings/new/?operatore={anna.pk}&amp;data={day.isoformat()}&amp;ora="
    assert new+"09:00" in page and new+"10:30" in page and new+"10:00" not in page
    bruno_new=f"/t/{t.slug}/bookings/new/?operatore={bruno.pk}&amp;data={day.isoformat()}&amp;ora="
    assert bruno_new+"14:00" in page and bruno_new+"09:00" not in page and 'class="agenda-closed"' in page
    # the list is one click away, and the booking form takes what the agenda chose
    assert 'href="?vista=elenco&amp;from=' in page
    assert "Giulia Rossi" in owner.get(f"/t/{t.slug}/bookings/?vista=elenco&from={day.isoformat()}").content.decode()
    form=owner.get(f"/t/{t.slug}/bookings/new/?operatore={bruno.pk}&data={day.isoformat()}&ora=15:30").content.decode()
    assert f'"prefill": {{"resource": "{bruno.pk}", "date": "{day.isoformat()}", "time": "15:30"}}' in form and '"service_label": "Trattamento"' in form
    assert f'href="/t/{t.slug}/bookings/">Agenda e prenotazioni</a>' in form
    # an operator sees only her own column
    staff,u=client_as(t,role="operator")
    with scope(t):Membership.objects.filter(tenant=t,user=u).update(resources=[str(bruno.pk)])
    mine=staff.get(f"/t/{t.slug}/bookings/?data={day.isoformat()}").content.decode()
    assert ">Bruno</strong>" in mine and ">Anna</strong>" not in mine and "Giulia" not in mine

def test_a_restaurant_keeps_the_list(sample):
    t,o,s=sample
    apply_profile(t,"ristorante",["Centro"])
    owner,_=client_as(t,role="owner")
    page=owner.get(f"/t/{t.slug}/bookings/").content.decode()
    # no grid of operators: the list, with the week and the month a click away (2.11.0)
    assert "data-agenda" not in page and "Le tue prenotazioni" in page and "?vista=settimana" in page and "?vista=giorno" not in page

def test_week_and_month_of_a_salon(sample):
    t,o,s=sample
    apply_profile(t,"parrucchiere",["Centro"])
    owner,_=client_as(t,role="owner")
    day=next_open_day();tz=ZoneInfo(t.timezone)
    with scope(t):
        taglio=Service.objects.get(name="Taglio");anna,bruno=Resource.objects.order_by("name")
        Resource.objects.filter(pk=anna.pk).update(name="Anna");Resource.objects.filter(pk=bruno.pk).update(name="Bruno")
        at=lambda h:datetime.combine(day,time(h),tz).isoformat()
        create_booking(t,payload(taglio,resource=str(anna.pk),start=at(10),first_name="Giulia",last_name="Rossi"))
        create_booking(t,payload(taglio,resource=str(bruno.pk),start=at(10),first_name="Marta",last_name="Bianchi",email="marta@example.invalid",phone="+393339998877"))
        create_booking(t,payload(taglio,resource=str(anna.pk),start=at(15),first_name="Elena",last_name="Verdi",email="elena@example.invalid",phone="+393331112233"))
    week=owner.get(f"/t/{t.slug}/bookings/?vista=settimana&data={day.isoformat()}").content.decode()
    assert 'class="agenda week"' in week and "3 prenotazioni" in week
    # Giulia and Marta at 10 side by side, each with her operator's colour; Elena alone at 15 takes the width
    assert 'data-lanes="2"' in week and "10:00 Giulia Rossi" in week and "10:00 Marta Bianchi" in week and 'data-col="1 / -1"' in week
    assert 'class="agenda-item op-1 confirmed" data-col="1"' in week and 'class="agenda-item op-2 confirmed" data-col="2"' in week
    assert '<li class="op-1">Anna</li>' in week and 'name="operatore"' in week
    assert f"?vista=giorno&amp;data={day.isoformat()}" in week and f"/t/{t.slug}/bookings/new/?data={day.isoformat()}&amp;ora=" in week
    # one operator: only hers, and a free space books with her
    anna_week=owner.get(f"/t/{t.slug}/bookings/?vista=settimana&data={day.isoformat()}&operatore={anna.pk}").content.decode()
    assert "Giulia Rossi" in anna_week and "Marta" not in anna_week and f"&amp;operatore={anna.pk}" in anna_week and "agenda-legend" not in anna_week
    month=owner.get(f"/t/{t.slug}/bookings/?vista=mese&data={day.isoformat()}").content.decode()
    assert 'class="month-grid"' in month and '<span class="month-count">3</span>' in month and "<b>10:00</b> Giulia Rossi" in month
    assert f'href="?vista=giorno&amp;data={day.isoformat()}' in month
    # an operator with her own scope sees only her bookings in the week too
    staff,u=client_as(t,role="operator")
    with scope(t):Membership.objects.filter(tenant=t,user=u).update(resources=[str(bruno.pk)])
    mine=staff.get(f"/t/{t.slug}/bookings/?vista=settimana&data={day.isoformat()}").content.decode()
    assert "Marta Bianchi" in mine and "Giulia" not in mine

def test_week_and_month_of_a_restaurant_list_the_days(sample):
    t,o,s=sample
    apply_profile(t,"ristorante",["Centro"])
    owner,_=client_as(t,role="owner")
    with scope(t):
        tavolo=Service.objects.get(kind__in=("capacity","shift"))
    from core.booking import choice
    import json
    day=timezone.localdate()+timedelta(days=3)
    slots=owner.get(f"/t/{t.slug}/booking-api/slots/",{"service":str(tavolo.pk),"date":day.isoformat(),"party":2})
    for _ in range(7):
        found=json.loads(slots.content).get("slots") if slots.status_code==200 else []
        if found:break
        day+=timedelta(days=1);slots=owner.get(f"/t/{t.slug}/booking-api/slots/",{"service":str(tavolo.pk),"date":day.isoformat(),"party":2})
    assert found
    with scope(t):create_booking(t,payload(tavolo,start=found[0]["start"],party=2,occurrence=found[0].get("occurrence") or ""))
    week=owner.get(f"/t/{t.slug}/bookings/?vista=settimana&data={day.isoformat()}").content.decode()
    assert 'class="week-list"' in week and "Mario Rossi" in week and "2 persone" in week and "data-agenda" not in week and "?vista=giorno" not in week
    month=owner.get(f"/t/{t.slug}/bookings/?vista=mese&data={day.isoformat()}").content.decode()
    assert '<span class="month-count">1</span>' in month and f"?vista=elenco&amp;from={day.isoformat()}&amp;to={day.isoformat()}" in month


def test_the_day_shows_who_works_and_the_allergies(sample):
    t,o,s=sample
    apply_profile(t,"parrucchiere",["Centro"])
    owner,_=client_as(t,role="owner")
    day=next_open_day();tz=ZoneInfo(t.timezone)
    with scope(t):
        taglio=Service.objects.get(name="Taglio");anna,bruno=Resource.objects.order_by("name")
        Resource.objects.filter(pk=anna.pk).update(name="Anna");Resource.objects.filter(pk=bruno.pk).update(name="Bruno")
        ExceptionDay.objects.create(tenant=t,resource=bruno,date=day,closed=True,label="Ferie")
        Tenant.objects.filter(pk=t.pk).update(allergens=["Nichel","Lattosio"],allergens_on=True);t.refresh_from_db()
        create_booking(t,payload(taglio,resource=str(anna.pk),start=datetime.combine(day,time(10),tz).isoformat(),allergies=["Nichel"]))
    page=owner.get(f"/t/{t.slug}/bookings/?data={day.isoformat()}").content.decode()
    # Bruno is on holiday with nothing booked: no column, named above; Anna's client ticked an allergy
    assert ">Anna</strong>" in page and ">Bruno</strong>" not in page and "Non lavora oggi: Bruno" in page
    assert 'class="agenda-alert"' in page and "Ha segnalato: Nichel" in page
