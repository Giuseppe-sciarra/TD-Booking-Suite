"""2.7.0: the client card. History of everything a client booked, notes kept by the staff (the colour
formula, an allergy), clients added by hand (also without email), quick search for manual bookings.

Contact data stay encrypted like the rest: the search decrypts in memory, in the business's own scope.
"""
from collections import Counter
from django.db.models import Count,Max,Q
from django.utils import timezone
from .booking import BookingError,normalize_phone
from .crypto import encrypt,index,tenant_key
from .models import Contact,Booking,Treatment
from .prices import spent

ERASED="erased:"

def phone_index(tenant,phone):return index("tel:"+phone,tenant_key(tenant),"email")
def email_index(tenant,email):return index(email.casefold(),tenant_key(tenant),"email")

def clean(data,require_contact=True):
    """Name, surname, email and phone typed by the staff. Email optional, phone or email needed."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    out={}
    for k,limit in (("first_name",100),("last_name",100),("email",254),("phone",40)):
        v=str(data.get(k,"") or "").strip()
        if len(v)>limit or any(ord(c)<32 for c in v):raise BookingError("Dati del cliente non validi.")
        out[k]=v
    if not out["first_name"]:raise BookingError("Scrivi almeno il nome del cliente.")
    if out["email"]:
        try:validate_email(out["email"])
        except ValidationError:raise BookingError("Email non valida.")
        out["email"]=out["email"].casefold()
    if out["phone"]:
        p=normalize_phone(out["phone"])
        if not p:raise BookingError("Numero di telefono non valido, per esempio 333 123 4567.")
        out["phone"]=p
    if require_contact and not out["email"] and not out["phone"]:raise BookingError("Serve almeno il telefono o l'email del cliente.")
    return out

def key_for(tenant,d,shared=False):
    """The card's key: its email, otherwise its phone; a phone already on another person's card (2.13.0: a mother
    and her daughter) is told apart by the name."""
    if d.get("email"):return email_index(tenant,d["email"])
    if shared:return index("tel:"+d["phone"]+"|"+full_name(d),tenant_key(tenant),"email")
    return phone_index(tenant,d["phone"])

def full_name(d):return " ".join((d.get("first_name","")+" "+d.get("last_name","")).casefold().split())

def same_person(card,d):
    """2.13.0: the card found by phone is this client only if one name holds the other ("Maria" and "Maria Rossi");
    a new name on a known phone is another person (a mother and her daughter), with a card of their own."""
    short,long=sorted((full_name(card.data).split(),full_name(d).split()),key=len)
    return all(w in long for w in short)

def by_phone(tenant,phone,without_email=False):
    """The card of a phone number: keyed by it (added at the desk without email) or with it among its data
    (booked online, keyed by the email). The same phone means the same person; without_email keeps only cards
    with no email, for an online request that brings its own (two emails are two people, even on one phone).
    shortcut: reads up to 5000 cards, most recent first; add a phone index column past that size."""
    if not phone:return None
    c=Contact.objects.filter(email_index=phone_index(tenant,phone)).first()
    if c:return c
    for c in Contact.objects.exclude(email_index__startswith=ERASED).order_by("-created")[:5000]:
        d=c.data
        if d.get("phone")==phone and not (without_email and d.get("email")):return c
    return None

def find_or_create(tenant,d):
    """The contact of a manual booking or of a client added by hand: by email, then by phone."""
    c=Contact.objects.filter(email_index=email_index(tenant,d["email"])).first() if d.get("email") else None
    if c:return c,False
    known=by_phone(tenant,d.get("phone"))
    if known and same_person(known,d):return known,False
    # the same phone with another name, already with its own card: that one
    if known and not d.get("email"):
        mine=Contact.objects.filter(email_index=key_for(tenant,d,shared=True)).first()
        if mine:return mine,False
    c=Contact(tenant=tenant,email_index=key_for(tenant,d,shared=bool(known)))
    c.pii=encrypt(tenant,{k:d.get(k,"") for k in ("first_name","last_name","email","phone")},"contact:"+str(c.pk));c.save()
    return c,True

def update(tenant,contact,fields=None,notes=None):
    """Edit the card. A new email or phone moves the contact's key, unless another client already has it."""
    data=dict(contact.data)
    if fields is not None:
        d=clean(fields)
        new_key=key_for(tenant,d)
        taken=lambda key:Contact.objects.filter(email_index=key).exclude(pk=contact.pk).exists()
        # a phone shared with another person keeps both cards, told apart by the name
        if not d.get("email") and new_key!=contact.email_index and taken(new_key):new_key=key_for(tenant,d,shared=True)
        if new_key!=contact.email_index and taken(new_key):
            raise BookingError("Un altro cliente ha già questa email o questo telefono.")
        data.update(d);contact.email_index=new_key
    if notes is not None:data["staff_notes"]=str(notes)[:4000]
    contact.pii=encrypt(tenant,data,"contact:"+str(contact.pk));contact.save(update_fields=["pii","email_index"])
    return data

def set_discount(contact,kind,value):
    """2.11.0: the client's own discount, a percentage (up to 100) or euro off; nothing to remove it. His confirmed
    future bookings take it at once; the past ones stay as they were paid. Returns how many bookings changed."""
    from decimal import Decimal,InvalidOperation
    from .prices import stamp
    if kind not in ("","percent","amount"):raise BookingError("Tipo di sconto non valido.")
    try:amount=Decimal(str(value or "0").strip().replace("€","").replace(" ","").replace(",",".")) if kind else Decimal(0)
    except InvalidOperation:raise BookingError("Scrivi lo sconto con un numero, per esempio 10 o 5,50.")
    if kind and not (Decimal("0.01")<=amount<=(Decimal(100) if kind=="percent" else Decimal(100000))):
        raise BookingError("Lo sconto in percentuale va da 0,01 a 100." if kind=="percent" else "Lo sconto in euro va da 0,01 a 100.000.")
    contact.discount_kind=kind;contact.discount=amount.quantize(Decimal("0.01"))
    contact.save(update_fields=["discount_kind","discount"])
    changed=0
    for b in Booking.objects.filter(contact=contact,status="confirmed",start__gt=timezone.now()).select_related("service","option","resource"):
        before=dict(b.snapshot);b.contact=contact;stamp(b)
        if b.snapshot!=before:b.save(update_fields=["snapshot"]);changed+=1
    return changed

def search(tenant,q,limit=10):
    """Name, surname, phone or email containing q. Up to 5000 cards are read, most recent first."""
    q=(q or "").strip().casefold()
    if len(q)<2:return []
    digits="".join(ch for ch in q if ch.isdigit())
    out=[]
    for c in Contact.objects.exclude(email_index__startswith=ERASED).order_by("-created")[:5000]:
        d=c.data
        name=(d.get("first_name","")+" "+d.get("last_name","")).casefold()
        if q in name or q in (d.get("last_name","")+" "+d.get("first_name","")).casefold() or q in d.get("email","").casefold() or (len(digits)>=3 and digits in d.get("phone","").replace("+","")):
            c.found=d;out.append(c)
            if len(out)>=limit:break
    return out

def history(contact,visible):
    """All the bookings of a client the user may see, newest first, and the numbers of the card."""
    rows=list(visible.filter(contact=contact).select_related("service","resource","option","location","tenant").order_by("-start"))
    now=timezone.now()
    done=[b for b in rows if b.start<=now and b.status in ("completed","confirmed")]
    upcoming=sorted([b for b in rows if b.start>now and b.status=="confirmed"],key=lambda b:b.start)
    services=Counter(b.snapshot.get("service") or b.service.name for b in done)
    staff=Counter(b.snapshot.get("resource") for b in done if b.snapshot.get("resource"))
    stats={"visits":len(done),"last":done[0].start if done else None,"next":upcoming[0] if upcoming else None,
        "cancelled":sum(1 for b in rows if b.status=="cancelled"),"no_show":sum(1 for b in rows if b.status=="no_show"),
        "service":services.most_common(1)[0] if services else None,"staff":staff.most_common(1)[0] if staff else None,"total":len(rows),
        "spent":spent(done)}
    return rows,stats

def clean_treatment(data,today):
    """2.8.0: one entry of the client record typed by the staff: a past or today's date, what was done (required),
    who did it and the details, line breaks included."""
    from datetime import date
    try:day=date.fromisoformat(str(data.get("date","")))
    except ValueError:raise BookingError("Data non valida.")
    if day>today:raise BookingError("La data non può essere nel futuro: qui si scrive quello che è stato fatto.")
    if day.year<1900:raise BookingError("Data non valida.")
    out={"date":day}
    for k,limit in (("service",120),("staff",100)):
        v=str(data.get(k,"") or "").strip()
        if len(v)>limit or any(ord(c)<32 for c in v):raise BookingError("Dati non validi o troppo lunghi.")
        out[k]=v
    if not out["service"]:raise BookingError("Scrivi cosa è stato fatto.")
    details=str(data.get("details","") or "").replace("\r\n","\n").strip()
    if len(details)>4000 or any(ord(c)<32 and c not in "\n\t" for c in details):raise BookingError("Dettagli non validi o troppo lunghi.")
    out["details"]=details
    return out

def save_treatment(tenant,contact,d,actor,treatment=None,booking=None):
    t=treatment or Treatment(tenant=tenant,contact=contact,booking=booking,author=str(getattr(actor,"pk","") or ""))
    t.date=d["date"];t.pii=encrypt(tenant,{k:d[k] for k in ("service","staff","details")},"treatment:"+str(t.pk))
    t.save();return t

def treatments(contact):
    """The record of a client, newest first, each entry with its decrypted info."""
    rows=list(Treatment.objects.filter(contact=contact).select_related("booking").order_by("-date","-created"))
    for r in rows:r.info=r.data
    return rows

def from_booking(booking,tz):
    """What a booking already says about the treatment: day, service with its variant, who."""
    s=booking.snapshot
    return {"date":booking.start.astimezone(tz).date(),"service":(s.get("service") or booking.service.name)+(" · "+s["option"] if s.get("option") else ""),
        "staff":s.get("resource") or "","details":"","booking":str(booking.pk)}

def visit_counts(contacts):
    """Visits and last visit for a page of the client list."""
    now=timezone.now()
    rows=Booking.objects.filter(contact__in=contacts).values("contact_id").annotate(
        visits=Count("id",filter=Q(start__lte=now,status__in=("completed","confirmed"))),last=Max("start",filter=Q(start__lte=now,status__in=("completed","confirmed"))),
        next=Count("id",filter=Q(start__gt=now,status="confirmed")))
    return {r["contact_id"]:r for r in rows}
