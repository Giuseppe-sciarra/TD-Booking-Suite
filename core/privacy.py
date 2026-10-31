"""Consent history, access, erasure and retention (finding N12).

ConsentEvent rows hold no personal data and are append-only: the history of a
contact survives erasure, but points to an anonymous record.
"""
import hashlib,json,secrets
from datetime import timedelta
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Tenant,Contact,Booking,ActionToken,Delivery,ConsentEvent,Treatment,WaitEntry,audit
from .crypto import encrypt
from .booking import BookingError,OPEN

ANON_CONTACT={"first_name":"Anonimo","last_name":"","email":"","phone":""}
ANON_BOOKING={"first_name":"Anonimo","last_name":"","email":"","phone":"","notes":"","internal_notes":"","answers":{}}

def text_hash(text):return hashlib.sha256((text or "").encode()).hexdigest()

def record(tenant,contact,kind,channel,actor=None,text=None,ref=""):
    return ConsentEvent.objects.create(tenant=tenant,contact=contact,kind=kind,channel=channel,
        actor=str(getattr(actor,"pk","") or actor or ""),text_hash=text_hash(text) if text is not None else "",ref=str(ref or ""))

def history(contact):
    """2.14.0: with the history of the cards merged into this one, which stay (without data) only for it."""
    return list(ConsentEvent.objects.filter(Q(contact=contact)|Q(contact__merged_into=contact)).order_by("at","pk"))

def export_contact(tenant,contact):
    """Access request: everything held about one contact, this tenant only."""
    bookings=[]
    for b in Booking.objects.filter(contact=contact).order_by("start"):
        d=b.data;d.pop("cancel_token",None)
        bookings.append({"id":str(b.pk),"start":b.start.isoformat(),"end":b.end.isoformat(),"status":b.status,
            "service":b.snapshot.get("service"),"location":b.snapshot.get("location"),"resource":b.snapshot.get("resource"),"data":d})
    return {"tenant":tenant.name,"generated":timezone.now().isoformat(),"contact":{"id":str(contact.pk),**contact.data,
        "consent":contact.consent,"consent_at":contact.consent_at.isoformat() if contact.consent_at else None,"suppressed":contact.suppressed},
        "bookings":bookings,"waiting_list":[{"service":w.service.name,"first":w.first.isoformat(),"last":w.last.isoformat(),"status":w.status,"data":w.data,"consent":w.snapshot.get("consent")}
            for w in WaitEntry.objects.filter(contact=contact).select_related("service").order_by("created")],"treatments":[{"date":t.date.isoformat(),**t.data,"booking":str(t.booking_id or "")} for t in Treatment.objects.filter(contact=contact).order_by("date","created")],
        "consent_history":[{"at":e.at.isoformat(),"event":e.kind,"channel":e.channel,"text_sha256":e.text_hash} for e in history(contact)]}

def _anonymize_booking(tenant,b):
    b.pii=encrypt(tenant,ANON_BOOKING,"booking:"+str(b.pk))
    b.request_fingerprint="";b.contact=None
    b.snapshot.pop("consent_request",None);b.snapshot["anonymized"]=timezone.now().isoformat()
    b.save(update_fields=["pii","request_fingerprint","contact","snapshot"])
    ActionToken.objects.filter(booking=b).delete()

def _anonymize_contact(tenant,c,forget=False):
    keys=[]
    if forget:
        # the person asked to be erased: the keyed indexes of their email and phone stay, so an import from
        # the old program cannot bring them back (the indexes cannot be turned back into the email or phone)
        from .clients import email_index,phone_index
        d=c.data
        keys=([email_index(tenant,d["email"])] if d.get("email") else [])+([phone_index(tenant,d["phone"])] if d.get("phone") else [])
    c.pii=encrypt(tenant,{**ANON_CONTACT,**({"erased_keys":keys} if keys else {})},"contact:"+str(c.pk))
    c.email_index="erased:"+secrets.token_hex(24)  # the keyed email index can no longer match anyone
    c.consent="revoked";c.consent_proof="";c.suppressed=True
    c.phone_index=c.name_index=c.tag_index=c.birthday_index="";c.vip=c.online_blocked=False;c.save()
    ActionToken.objects.filter(contact=c).delete()
    Treatment.objects.filter(contact=c).delete()  # 2.8.0: the client record goes with the person
    WaitEntry.objects.filter(contact=c).delete()  # 2.14.0: and the waiting list
    for d in Delivery.objects.filter(contact=c).exclude(payload=""):
        d.payload=encrypt(tenant,{"redacted":True},"delivery:"+str(d.pk))
        if d.status in ("pending","sending"):d.status="skipped"
        d.save(update_fields=["payload","status"])

def erase_contact(tenant,contact,actor):
    """Erasure request. Future confirmed bookings must be handled first."""
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        contact=Contact.objects.select_for_update().get(pk=contact.pk)
        if Booking.objects.filter(contact=contact,status__in=OPEN,end__gt=timezone.now()).exists():
            raise BookingError("Il cliente ha prenotazioni future: annullale o completale prima della cancellazione.")
        for b in Booking.objects.filter(contact=contact):_anonymize_booking(tenant,b)
        _anonymize_contact(tenant,contact,forget=True)
        record(tenant,contact,"erased","operator",actor)
        audit(tenant,actor,"privacy.erased",contact.pk)

def apply_retention(tenant,apply=False,now=None):
    """Bookings past retention lose personal data and their link to the contact;
    contacts left without bookings and without active consent are erased.
    Contacts with confirmed marketing consent are kept: that is an active relationship."""
    now=now or timezone.now()
    cutoff=now-timedelta(days=tenant.retention_days)
    old=Booking.objects.filter(end__lt=cutoff).exclude(snapshot__has_key="anonymized")
    report={"bookings":old.count(),"contacts":0,"deliveries":0}
    # a client added by hand has no booking yet: only cards older than the period count, and a recent
    # entry of the client record (2.8.0) keeps the card as much as a recent booking
    from zoneinfo import ZoneInfo
    since=cutoff.astimezone(ZoneInfo(tenant.timezone)).date()
    candidates=Contact.objects.exclude(consent="yes").exclude(email_index__startswith="erased:").filter(created__lt=cutoff)
    stale=[c for c in candidates if not Booking.objects.filter(contact=c,end__gte=cutoff).exists() and not Treatment.objects.filter(contact=c,date__gte=since).exists()
        and not WaitEntry.objects.filter(contact=c).exists()]
    # 2.14.0: the waiting list whose last day has passed, done or not: nothing to wait for any more
    gone=WaitEntry.objects.filter(last__lt=now.astimezone(ZoneInfo(tenant.timezone)).date())
    report["waits"]=gone.count()
    report["contacts"]=len(stale)
    old_records=Treatment.objects.filter(date__lt=since)
    report["treatments"]=old_records.count()
    report["deliveries"]=Delivery.objects.filter(created__lt=cutoff).exclude(payload="").count()
    if not apply:return report
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        for b in old.iterator(chunk_size=200):_anonymize_booking(tenant,b)
        for c in stale:
            _anonymize_contact(tenant,c);record(tenant,c,"erased","retention")
        old_records.delete();gone.delete()
        for d in Delivery.objects.filter(created__lt=cutoff).exclude(payload=""):
            d.payload="";d.status="skipped" if d.status in ("pending","sending") else d.status
            d.save(update_fields=["payload","status"])
        ActionToken.objects.filter(expires__lt=now).delete()
        if any(report.values()):audit(tenant,None,"retention.applied",**report)  # 2.14.0: daily from the worker, a line only when it changed something
    return report

def run_retention(now=None):
    """2.14.0: the retention of every business once a day, from the worker (mail_worker), instead of a cron the installer
    had to remember: the same command as by hand. When it last went through, or why not, stays on the business for the
    agency's dashboard; a failed one is tried again at the next round. Returns how many went through."""
    import io,logging
    from django.core.management import call_command
    from django.db.models import Q
    now=now or timezone.now();done=0
    for t in Tenant.objects.filter(Q(retention_at__isnull=True)|Q(retention_at__lte=now-timedelta(days=1))).order_by("created"):
        try:
            call_command("retention",tenant=t.slug,apply=True,stdout=io.StringIO())
            Tenant.objects.filter(pk=t.pk).update(retention_at=now,retention_error="");done+=1
        except Exception as ex:  # one business that fails does not stop the others
            # the dashboard gets the kind of error only: the details, maybe with data, stay in the worker's log
            logging.getLogger(__name__).exception("Conservazione dei dati non riuscita: %s",t.slug)
            Tenant.objects.filter(pk=t.pk).update(retention_error=type(ex).__name__[:200])
    return done
