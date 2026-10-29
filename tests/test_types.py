"""2.12.0, release sweep: every type of business, with real bookings, every page for its owner and for the agency:
never a server error, sound markup. Then what a stay (hotel, flat, rental) shows that an appointment does not."""
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo
import pytest
from django.utils import timezone
from core.booking import create_booking,available,bounds,BookingError
from core.calendar import local_datetime
from core.models import *
from core.presets import PRESETS
from core.profile import apply_profile
from core.tenancy import scope
from tests.test_core import client_as
from tests.test_pages import pages,check_markup
from tests.test_phase2 import staff_client

pytestmark=pytest.mark.django_db

def first_slot(t,svc,skip=0):
    """The first free time from tomorrow, or for a stay the first arrival with three nights."""
    tz=ZoneInfo(t.timezone);found=0
    for dd in range(1,60):
        day=timezone.localdate()+timedelta(days=dd)
        if svc.kind=="day":
            data={"start":local_datetime(day,svc.checkin,tz).isoformat(),"end_date":(day+timedelta(days=max(3,svc.min_days))).isoformat(),"party":1}
            try:bounds(t,svc,data)
            except BookingError:continue
            found+=1
            if found>skip:return data
        else:
            for s in available(t,svc.pk,day,party=1)[skip:skip+1]:return {"start":s["start"],"resource":s["resource"],"occurrence":s["occurrence"]}
    return None

def book(t,svc,n=0,**extra):
    slot=first_slot(t,svc,skip=n)
    assert slot,f"{t.business_type}: {svc.name} senza orari liberi"
    answers={f["key"]:(True if f["type"]=="checkbox" else (f.get("choices") or ["x"])[0] if f["type"]=="select" else "1" if f["type"]=="number" else "AB123CD") for f in svc.fields if f.get("required")}
    data={"service":str(svc.pk),"first_name":"Cliente","last_name":f"Prova{n}","email":f"c{n}@example.invalid","phone":f"+3933300{n}{secrets.randbelow(99999):05d}",
          "request_key":secrets.token_urlsafe(24),"party":min(2,svc.max_party),"answers":answers,**slot,**extra}
    return create_booking(t,data)[0]

@pytest.mark.parametrize("key",list(PRESETS))
def test_every_type_every_page(key):
    t=Tenant.objects.create(slug="t-"+key.replace("_","-"),name="Prova "+PRESETS[key]["label"],business_type=key)
    apply_profile(t,key,["Centro"])
    owner,_=client_as(t,role="owner");admin,_=staff_client()
    with scope(t):
        svc=Service.objects.filter(active=True).order_by("name").first()
        b=book(t,svc)
        res=Resource.objects.first();sch=Schedule.objects.first();occ=Occurrence.objects.first()
    ids={"booking":b.pk,"contact":b.contact_id,"campaign":"","service":svc.pk,"resource":(res or svc).pk,"location":b.location_id,"schedule":(sch or svc).pk,"occurrence":(occ or svc).pk}
    day=b.start.astimezone(ZoneInfo(t.timezone)).date().isoformat();p=f"/t/{t.slug}"
    extra=[f"{p}/bookings/?vista={v}&data={day}" for v in ("giorno","settimana","mese")]+[f"{p}/bookings/?vista=elenco&from={day}"]
    for c in (owner,admin):
        for path in pages(t,ids)+extra:
            r=c.get(path)
            assert r.status_code<500,(key,path,r.status_code)
            if r.status_code==200 and r["Content-Type"].startswith("text/html"):check_markup(r.content.decode(),f"{key} {path}")

def test_a_stay_shows_on_each_of_its_days():
    t=Tenant.objects.create(slug="albergo",name="Albergo",business_type="hotel")
    apply_profile(t,"hotel",["Centro"])
    owner,_=client_as(t,role="owner")
    with scope(t):
        camera=Service.objects.get(kind="day");b=book(t,camera)
        room=Resource.objects.get(pk=b.resource_id)
    tz=ZoneInfo(t.timezone);a=b.start.astimezone(tz).date();p=f"/t/{t.slug}"
    week=owner.get(f"{p}/bookings/?vista=settimana&data={(a+timedelta(days=1)).isoformat()}").content.decode()
    month=owner.get(f"{p}/bookings/?vista=mese&data={a.isoformat()}").content.decode()
    # arrival, the nights in the house and the departure, each on its day; counted once in the month
    assert "Arrivo 15:00" in week+month and "In casa" in week+month and "Partenza 10:00" in owner.get(f"{p}/bookings/?vista=settimana&data={(a+timedelta(days=3)).isoformat()}").content.decode()
    assert "1 prenotazione nel mese" in month or a.month!=(a+timedelta(days=3)).month
    detail=owner.get(f"{p}/bookings/{b.pk}/").content.decode()
    assert "3 notti" in detail and "<dt>Camera</dt>" in detail and "<dt>Con</dt>" not in detail
    # a hotel has no prices: no spent and no discount on the card; it counts stays
    card=owner.get(f"{p}/contacts/{b.contact_id}/").content.decode()
    assert "<span>Soggiorni</span>" in card and "Speso" not in card and 'id="sconto"' not in card and "3 notti" in card
    # a room has no working hours and no durations; its closing days stay one click away
    form=owner.get(f"{p}/catalog/resources/{room.pk}/").content.decode()
    assert "Giorni e orari di lavoro" not in form and "Si prenota per" in form and "Segna i giorni in cui non si prenota" in form
    assert "un camera" not in form and "Persone massime (0 = nessun limite)" in form

def test_one_person_is_not_said():
    t=Tenant.objects.create(slug="salone-uno",name="Salone",business_type="parrucchiere")
    apply_profile(t,"parrucchiere",["Centro"])
    owner,_=client_as(t,role="owner")
    with scope(t):b=book(t,Service.objects.get(name="Taglio"))
    p=f"/t/{t.slug}"
    for page in (f"{p}/bookings/{b.pk}/",f"{p}/contacts/{b.contact_id}/",f"{p}/bookings/?vista=elenco&from={b.start.date().isoformat()}"):
        html=owner.get(page).content.decode()
        assert "1 persona" not in html and "<dt>Persone</dt>" not in html,page
