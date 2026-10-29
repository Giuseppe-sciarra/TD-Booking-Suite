"""2.11.0: prices of services and variants, the client's own discount, and who keeps them."""
from datetime import datetime,time,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from django.utils import timezone
from core.booking import create_booking
from core.clients import set_discount
from core.booking import BookingError
from core.models import *
from core.prices import euro,amounts
from core.profile import apply_profile
from core.tenancy import scope
from tests.test_core import sample,client_as,payload
from tests.test_agenda import next_open_day

pytestmark=pytest.mark.django_db

def test_euro_and_amounts():
    assert euro(25)=="25,00 €" and euro("1234.5")=="1.234,50 €" and euro(None)==""
    service=Service(price=Decimal("40"));longer=Option(price=Decimal("55"));same=Option(price=None)
    assert amounts(Service(price=None),None,None)=={}
    assert amounts(service,same,None)=={"price":"40","total":"40"}
    assert amounts(service,longer,None)=={"price":"55","total":"55"}
    ten=Contact(discount_kind="percent",discount=Decimal("10"))
    assert amounts(service,longer,ten)=={"price":"55","total":"49.50","discount":"10%"}
    # a fixed discount never takes the price below zero
    assert amounts(service,None,Contact(discount_kind="amount",discount=Decimal("50")))=={"price":"40","total":"0.00","discount":"50,00 €"}

def test_booking_keeps_its_price_and_the_discount_reaches_future_bookings(sample):
    t,o,s=sample
    apply_profile(t,"parrucchiere",["Centro"])
    owner,_=client_as(t,role="owner")
    day=next_open_day();tz=ZoneInfo(t.timezone)
    with scope(t):
        taglio=Service.objects.get(name="Taglio");op=Resource.objects.order_by("name").first()
    # 2.11.0: the owner keeps the price list himself
    r=owner.post(f"/t/{t.slug}/catalog/services/{taglio.pk}/",{"name":"Taglio","location":str(taglio.location_id),"description":"","price":"30","duration":"30","interval":"15","buffer_before":"0","buffer_after":"0",
        "lead_minutes":"60","horizon_days":"180","cancel_cutoff_minutes":"0","resource_label":"Operatore","fields":"[]","active":"on","resources":[str(op.pk)]},follow=True)
    assert "Salvato." in r.content.decode()
    with scope(t):
        taglio.refresh_from_db();assert taglio.price==Decimal("30")
        b,_=create_booking(t,payload(taglio,resource=str(op.pk),start=datetime.combine(day,time(10),tz).isoformat()))
        assert b.snapshot["price"]=="30.00" and b.snapshot["total"]=="30.00" and "discount" not in b.snapshot
        contact=b.contact
    detail=owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    assert "30,00 €" in detail
    # a discount on the card: the booking in programme takes it, the card shows it
    r=owner.post(f"/t/{t.slug}/contacts/{contact.pk}/",{"action":"discount","discount_kind":"percent","discount":"10"},follow=True).content.decode()
    assert "Sconto salvato. Aggiornata 1 prenotazione in programma." in r and "Sconto 10%" in r
    with scope(t):
        b.refresh_from_db()
        assert b.snapshot["total"]=="27.00" and b.snapshot["discount"]=="10%"
        # a new price list does not touch a booking already taken
        Service.objects.filter(pk=taglio.pk).update(price=Decimal("35"))
        b.refresh_from_db();assert b.snapshot["price"]=="30.00"
    detail=owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    assert "27,00 €" in detail and "sconto 10%" in detail
    # the staff's own booking form shows the prices, the public page does not
    assert '"price": "35,00' in owner.get(f"/t/{t.slug}/bookings/new/").content.decode()
    assert '"price": ""' in owner.get(f"/book/{t.slug}/").content.decode() and "35,00" not in owner.get(f"/book/{t.slug}/").content.decode()

def test_discount_is_checked_and_kept_by_who_keeps_prices(sample):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s))
        contact=b.contact
        with pytest.raises(BookingError):set_discount(contact,"percent","120")
        with pytest.raises(BookingError):set_discount(contact,"amount","dieci")
        assert set_discount(contact,"amount","5,50")==0  # no price, nothing to update
        contact.refresh_from_db();assert contact.discount_kind=="amount" and contact.discount==Decimal("5.50")
        set_discount(contact,"","")
        contact.refresh_from_db();assert contact.discount_kind=="" and contact.discount==0
    from tests.test_phase2 import member
    reception,_=member(t,"reception")
    assert reception.post(f"/t/{t.slug}/contacts/{contact.pk}/",{"action":"discount","discount_kind":"percent","discount":"10"}).status_code==403
