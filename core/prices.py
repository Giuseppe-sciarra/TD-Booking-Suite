"""2.11.0: the price of a service or of its variant, and the client's own discount.

The amounts are written into the booking when it is made or moved, like a receipt: a new price list does not change
the bookings already taken. A discount set on the client counts from then on, his confirmed future bookings included."""
from decimal import Decimal,ROUND_HALF_UP
CENT=Decimal("0.01")

def euro(value):
    """25 -> "25,00 €", 1234.5 -> "1.234,50 €"."""
    if value in (None,""):return ""
    text=f"{Decimal(value).quantize(CENT,ROUND_HALF_UP):,.2f}".replace(",","~").replace(".",",").replace("~",".")
    return text+" €"

def discount_label(contact):
    """"10%" or "5,00 €", empty without a discount."""
    if not contact or not contact.discount_kind or not contact.discount:return ""
    if contact.discount_kind=="percent":return f"{contact.discount.normalize():f}".replace(".",",")+"%"
    return euro(contact.discount)

def amounts(service,option,contact):
    """List price, discount and what the client pays, as text for the booking. Empty when there is no price."""
    price=option.price if option is not None and option.price is not None else service.price
    if price is None:return {}
    total=price
    label=discount_label(contact)
    if label:
        total=price*(100-contact.discount)/100 if contact.discount_kind=="percent" else price-contact.discount
        total=max(Decimal(0),total).quantize(CENT,ROUND_HALF_UP)
    out={"price":str(price),"total":str(total)}
    if total!=price:out["discount"]=label
    return out

def stamp(booking):
    """The amounts of the booking as it is now: service, variant and client."""
    for key in ("price","total","discount"):booking.snapshot.pop(key,None)
    booking.snapshot.update(amounts(booking.service,booking.option,booking.contact))

def spent(bookings):
    """What the client paid over these bookings, None when none of them had a price."""
    values=[Decimal(b.snapshot["total"]) for b in bookings if b.snapshot.get("total")]
    return sum(values,Decimal(0)) if values else None
