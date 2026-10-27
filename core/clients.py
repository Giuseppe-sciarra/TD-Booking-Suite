"""2.7.0: the client card. History of everything a client booked, notes kept by the staff (the colour
formula, an allergy), clients added by hand (also without email), quick search for manual bookings.

Contact data stay encrypted like the rest: the search decrypts in memory, in the business's own scope.
"""
from collections import Counter
from django.db.models import Count,Max,Q
from django.utils import timezone
from .booking import BookingError,normalize_phone
from .crypto import encrypt,index,tenant_key
from .models import Contact,Booking

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

def key_for(tenant,d):return email_index(tenant,d["email"]) if d.get("email") else phone_index(tenant,d["phone"])

def find_or_create(tenant,d):
    """The contact of a manual booking or of a client added by hand: by email, then by phone."""
    for idx in ([email_index(tenant,d["email"])] if d.get("email") else [])+([phone_index(tenant,d["phone"])] if d.get("phone") else []):
        c=Contact.objects.filter(email_index=idx).first()
        if c:return c,False
    if d.get("phone"):
        # a client who booked online has the email as key: the same phone means the same person
        for c in Contact.objects.exclude(email_index__startswith=ERASED).order_by("-created")[:5000]:
            if c.data.get("phone")==d["phone"]:return c,False
    c=Contact(tenant=tenant,email_index=key_for(tenant,d))
    c.pii=encrypt(tenant,{k:d.get(k,"") for k in ("first_name","last_name","email","phone")},"contact:"+str(c.pk));c.save()
    return c,True

def update(tenant,contact,fields=None,notes=None):
    """Edit the card. A new email or phone moves the contact's key, unless another client already has it."""
    data=dict(contact.data)
    if fields is not None:
        d=clean(fields)
        new_key=key_for(tenant,d)
        if new_key!=contact.email_index and Contact.objects.filter(email_index=new_key).exclude(pk=contact.pk).exists():
            raise BookingError("Un altro cliente ha già questa email o questo telefono.")
        data.update(d);contact.email_index=new_key
    if notes is not None:data["staff_notes"]=str(notes)[:4000]
    contact.pii=encrypt(tenant,data,"contact:"+str(contact.pk));contact.save(update_fields=["pii","email_index"])
    return data

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
    rows=list(visible.filter(contact=contact).select_related("service","resource","option","location").order_by("-start"))
    now=timezone.now()
    done=[b for b in rows if b.start<=now and b.status in ("completed","confirmed")]
    upcoming=sorted([b for b in rows if b.start>now and b.status=="confirmed"],key=lambda b:b.start)
    services=Counter(b.snapshot.get("service") or b.service.name for b in done)
    staff=Counter(b.snapshot.get("resource") for b in done if b.snapshot.get("resource"))
    stats={"visits":len(done),"last":done[0].start if done else None,"next":upcoming[0] if upcoming else None,
        "cancelled":sum(1 for b in rows if b.status=="cancelled"),"no_show":sum(1 for b in rows if b.status=="no_show"),
        "service":services.most_common(1)[0] if services else None,"staff":staff.most_common(1)[0] if staff else None,"total":len(rows)}
    return rows,stats

def visit_counts(contacts):
    """Visits and last visit for a page of the client list."""
    now=timezone.now()
    rows=Booking.objects.filter(contact__in=contacts).values("contact_id").annotate(
        visits=Count("id",filter=Q(start__lte=now,status__in=("completed","confirmed"))),last=Max("start",filter=Q(start__lte=now,status__in=("completed","confirmed"))),
        next=Count("id",filter=Q(start__gt=now,status="confirmed")))
    return {r["contact_id"]:r for r in rows}
