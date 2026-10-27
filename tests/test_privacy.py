"""N12 consent history, access, erasure and retention; N15 vendor exports; MFA recovery codes."""
import json,secrets,time as wallclock
from datetime import timedelta
import pytest
from django.db import connection,transaction
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import index,tenant_key,token_hash
from core.booking import create_booking,BookingError,change_status
from core.privacy import history,export_contact,erase_contact,apply_retention
from core.public import apply_action
from tests.test_core import sample,payload,client_as

pytestmark=pytest.mark.django_db

def confirm(t,contact,rf,kind="consent"):
    token=ActionToken.objects.filter(contact=contact,kind=kind,used=False).first()
    request=rf.post("/");request.tenant=t
    apply_action(request,token)

def test_consent_history_is_chronological(sample,rf):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s,marketing=True))
        c=Contact.objects.get(pk=b.contact_id)
        confirm(t,c,rf)
        ActionToken.objects.create(tenant=t,digest=token_hash("u"),kind="unsubscribe",contact=c,expires=timezone.now()+timedelta(days=1))
        confirm(t,c,rf,"unsubscribe")
        b2,_=create_booking(t,payload(s,hour=12,marketing=True))
        confirm(t,c,rf)
        kinds=[e.kind for e in history(c)]
        assert kinds==["requested","confirmed","revoked","requested","confirmed"]
        assert all(e.text_hash==history(c)[0].text_hash for e in history(c) if e.kind in ("requested",))

def test_consent_history_is_append_only(sample):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s,marketing=True))
        e=ConsentEvent.objects.get()
        with pytest.raises(ValueError):e.kind="confirmed";e.save()
        with pytest.raises(ValueError):e.delete()

def test_consent_history_append_only_in_database(sample):
    if connection.vendor!="postgresql":pytest.skip("Richiede PostgreSQL reale")
    t,o,s=sample
    with scope(t):
        create_booking(t,payload(s,marketing=True))
        with pytest.raises(Exception):
            with transaction.atomic():ConsentEvent.objects.update(kind="confirmed")
        with pytest.raises(Exception):
            with transaction.atomic():ConsentEvent.raw.all()._raw_delete(ConsentEvent.raw.db)

def test_export_contains_only_this_tenant(sample):
    t,o,s=sample
    with scope(o):
        loc=Location.objects.create(tenant=o,name="Altrove")
        other=Service.objects.create(tenant=o,location=loc,name="Altro servizio",kind="capacity",capacity=5,max_party=2,lead_minutes=0)
        from datetime import time as clock
        for d in range(7):Schedule.objects.create(tenant=o,weekday=d,start=clock(9),end=clock(18))
        create_booking(o,payload(other,notes="Segreto altra attività"))
    with scope(t):
        b,_=create_booking(t,payload(s,marketing=True))
        data=export_contact(t,Contact.objects.get(pk=b.contact_id))
    text=json.dumps(data,ensure_ascii=False)
    assert "Prodotto delicato" in text and "Segreto altra attività" not in text and "Altro servizio" not in text
    assert data["consent_history"][0]["event"]=="requested" and "cancel_token" not in text

def test_erasure_leaves_nothing_reidentifiable(sample):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s,marketing=True))
        c=Contact.objects.get(pk=b.contact_id)
        with pytest.raises(BookingError):erase_contact(t,c,None)  # future booking still open
        change_status(t,b,"cancelled")
        erase_contact(t,c,None)
        c.refresh_from_db();b.refresh_from_db()
        assert b.contact_id is None and b.data["email"]=="" and b.data["first_name"]=="Anonimo" and b.request_fingerprint==""
        assert c.data["email"]=="" and c.suppressed and c.email_index.startswith("erased:")
        assert not Contact.objects.filter(email_index=index("mario@example.invalid",tenant_key(t),"email")).exists()
        assert not ActionToken.objects.filter(contact=c).exists() and not ActionToken.objects.filter(booking=b).exists()
        assert [e.kind for e in history(c)][-1]=="erased"
        for d in Delivery.objects.all():assert "mario" not in json.dumps(d.payload)
        # the same person can book again as a brand-new contact
        again,_=create_booking(t,payload(s,hour=13))
        assert again.contact_id!=c.pk

def test_retention_unlinks_old_bookings(sample):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s))
        keep,_=create_booking(t,payload(s,hour=12,email="lucia@example.invalid",marketing=True))
        k=Contact.objects.get(pk=keep.contact_id);k.consent="yes";k.save()
        later=timezone.now()+timedelta(days=t.retention_days+30)
        report=apply_retention(t,apply=False,now=later)
        assert report["bookings"]==2 and report["contacts"]==1
        assert Booking.objects.get(pk=b.pk).contact_id is not None  # simulation changes nothing
        apply_retention(t,apply=True,now=later)
        b.refresh_from_db();keep.refresh_from_db();k.refresh_from_db()
        assert b.contact_id is None and keep.contact_id is None and b.data["email"]==""
        assert k.data["email"]=="lucia@example.invalid"  # active consent: relationship kept
        assert Contact.objects.filter(email_index__startswith="erased:").count()==1
        assert apply_retention(t,apply=False,now=later)["bookings"]==0

def test_contact_page_permissions(sample):
    t,o,s=sample
    with scope(t):b,_=create_booking(t,payload(s));cid=b.contact_id
    owner,_=client_as(t,role="owner")
    page=owner.get(f"/t/{t.slug}/contacts/{cid}/").content.decode()
    assert "Consenso alle comunicazioni" in page and "Cancella i dati" in page
    r=owner.get(f"/t/{t.slug}/contacts/{cid}/?download=1")
    assert r.status_code==200 and json.loads(r.content)["contact"]["email"]=="mario@example.invalid"
    mk,_=client_as(t,role="marketing")
    assert mk.get(f"/t/{t.slug}/contacts/{cid}/").status_code==200
    assert mk.get(f"/t/{t.slug}/contacts/{cid}/?download=1").status_code==403
    assert mk.post(f"/t/{t.slug}/contacts/{cid}/",{"confirm":"CANCELLA"}).status_code==403

def test_n15_vendor_profiles_only_for_eligible_recipients(sample):
    t,o,s=sample
    with scope(t):
        a,_=create_booking(t,payload(s,first_name="Zoë, \"la\"",notes="riga1\nriga2"))
        b,_=create_booking(t,payload(s,hour=12,email="revocato@example.invalid"))
        ca=Contact.objects.get(pk=a.contact_id);ca.consent="yes";ca.save()
        cb=Contact.objects.get(pk=b.contact_id);cb.consent="revoked";cb.save()
    c,_=client_as(t,role="owner")
    assert c.get(f"/t/{t.slug}/export/?kind=contacts&profile=brevo").status_code==400
    r=c.get(f"/t/{t.slug}/export/?kind=marketing&profile=brevo")
    body=r.content.decode("utf-8-sig")
    assert r.status_code==200 and "mario@example.invalid" in body and "revocato@" not in body
    import csv,io
    rows=list(csv.reader(io.StringIO(body)))
    assert rows[0][0]=="EMAIL" and rows[1][1]=='Zoë, "la"' and rows[1][3]=="+393331234567"

def test_mfa_recovery_code_works_once(sample):
    from core.identity import new_recovery
    t,o,s=sample
    u=User.objects.create_user("rec@example.invalid","A-test-password-12345")
    class R:session={}
    new_recovery(R,u)
    from core.crypto import reveal
    codes=reveal(R.session["recovery_once"],"recovery:"+str(u.pk))
    assert len(codes)==10 and len(set(codes))==10
    def attempt(code):
        c=Client();s_=c.session;s_["preauth"]={"user":str(u.pk),"version":u.auth_version,"until":wallclock.time()+300};s_.save()
        return c.post("/verify/",{"code":code})
    assert attempt(codes[0]).status_code==302
    assert attempt(codes[0]).status_code==200  # second use refused
    assert attempt("non-un-codice").status_code==200
