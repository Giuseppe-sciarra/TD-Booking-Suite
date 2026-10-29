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
    assert "data-agenda" not in page and "Le tue prenotazioni" in page and "?vista=elenco" not in page
