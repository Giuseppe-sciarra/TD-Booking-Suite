"""Regression tests for the phase 1 findings of the 26 Oct 2026 dossier (N03-N16)."""
import os,secrets,smtplib,stat
from datetime import datetime,date,time as clock,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import encrypt
from core.booking import create_booking,move_booking,BookingError,queue,capacity_available
from core.calendar import windows
from core.mail import worker_once
from core.middleware import operational
from core.public import apply_action
from tests.test_core import sample,payload,client_as

pytestmark=pytest.mark.django_db
SMTP={"host":"smtp.example.invalid","port":587,"mode":"starttls","username":"","password":"","sender":"test@example.invalid","reply_to":""}

def future_day(tenant,days=3):
    return (timezone.now()+timedelta(days=days)).astimezone(ZoneInfo(tenant.timezone)).date()

def marketing_ready(t):
    t.marketing_enabled=True;t.smtp=encrypt(t,SMTP,"smtp");t.save()

def campaign_backlog(t,n,status="paused"):
    with scope(t):
        campaign=Campaign.objects.create(tenant=t,name="Promo",subject="Promo",body="Ciao",status=status)
        for i in range(n):
            c=Contact(tenant=t,email_index=secrets.token_hex(16),consent="yes")
            c.pii=encrypt(t,{"email":f"c{i}@example.invalid"},"contact:"+str(c.pk));c.save()
            queue(t,{"to":f"c{i}@example.invalid","subject":"Promo","body":"x"},c,campaign)
    return campaign

# N03 — a paused campaign never starves booking confirmations
def test_n03_paused_campaign_does_not_block_confirmations(sample,monkeypatch):
    t,o,s=sample;marketing_ready(t)
    campaign_backlog(t,80,status="paused")
    with scope(t):b,_=create_booking(t,payload(s))
    sent=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:sent.append(p["to"]))
    worker_once()
    assert sent==["mario@example.invalid"]
    with scope(t):
        assert Delivery.objects.filter(campaign__isnull=True,status="accepted").count()==1
        assert Delivery.objects.filter(campaign__isnull=False,status="pending").count()==80

def test_n03_queued_campaign_still_yields_to_notifications(sample,monkeypatch):
    t,o,s=sample;marketing_ready(t)
    campaign_backlog(t,60,status="queued")
    with scope(t):create_booking(t,payload(s))
    sent=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:sent.append(p["to"]))
    worker_once()
    assert sent[0]=="mario@example.invalid"

# N04 — 4xx recipient refusal retries without suppression; 5xx follows policy
@pytest.mark.parametrize("code,status,suppressed",[(450,"pending",False),(550,"failed",True)])
def test_n04_recipient_refusal_classification(sample,monkeypatch,code,status,suppressed):
    t,o,s=sample;t.smtp=encrypt(t,SMTP,"smtp");t.save()
    with scope(t):b,_=create_booking(t,payload(s))
    def refuse(conf,p,mid):raise smtplib.SMTPRecipientsRefused({p["to"]:(code,b"mailbox")})
    monkeypatch.setattr("core.mail.send_message",refuse)
    worker_once()
    with scope(t):
        d=Delivery.objects.get(contact=b.contact,campaign__isnull=True)
        assert d.status==status
        assert Contact.objects.get(pk=b.contact_id).suppressed is suppressed
        if code==450:assert d.next_attempt>timezone.now() and d.last_code=="recipient_temporary"

def test_n04_uncertain_delivery_is_never_resent(sample,monkeypatch):
    t,o,s=sample;t.smtp=encrypt(t,SMTP,"smtp");t.save()
    with scope(t):create_booking(t,payload(s))
    calls=[]
    def crash(conf,p,mid):calls.append(1);raise ConnectionResetError
    monkeypatch.setattr("core.mail.send_message",crash)
    worker_once();worker_once()
    assert len(calls)==1
    with scope(t):assert Delivery.objects.get().status=="unknown"

# N05 — move preview excludes the booking itself, resolved server-side
@pytest.fixture
def resource_service(sample):
    t,o,s=sample
    with scope(t):
        svc=Service.objects.create(tenant=t,location=s.location,name="Taglio",kind="resource",duration=60,interval=30,lead_minutes=0)
        res=Resource.objects.create(tenant=t,location=s.location,name="Sala 1")
        ServiceResource.objects.create(tenant=t,service=svc,resource=res)
    return t,o,svc,res

def test_n05_move_preview_and_commit_share_rules(resource_service):
    t,o,svc,res=resource_service
    day=future_day(t);tz=ZoneInfo(t.timezone)
    with scope(t):b,_=create_booking(t,payload(svc,hour=10,resource=str(res.pk)))
    c,u=client_as(t)
    q={"date":day.isoformat(),"service":str(svc.pk),"resource":str(res.pk),"party":1}
    target=datetime.combine(day,clock(10,30),tz).isoformat()
    generic=c.get(f"/t/{t.slug}/booking-api/slots/",q).json()["slots"]
    assert target not in [x["start"] for x in generic]
    moving=c.get(f"/t/{t.slug}/bookings/{b.pk}/move-slots/",q).json()["slots"]
    assert target in [x["start"] for x in moving]
    with scope(t):
        moved=move_booking(t,b.pk,{"service":str(svc.pk),"resource":str(res.pk),"start":target,"party":1},u)
        assert moved.start==datetime.fromisoformat(target)

def test_n05_move_preview_rejects_foreign_booking(resource_service):
    t,o,svc,res=resource_service
    with scope(o):
        loc=Location.objects.create(tenant=o,name="Altrove")
        other=Service.objects.create(tenant=o,location=loc,name="X",kind="capacity",capacity=5,max_party=2,lead_minutes=0)
        for d in range(7):Schedule.objects.create(tenant=o,weekday=d,start=clock(9),end=clock(18))
        foreign,_=create_booking(o,payload(other))
    c,u=client_as(t)
    q={"date":future_day(t).isoformat(),"service":str(svc.pk),"party":1}
    r=c.get(f"/t/{t.slug}/bookings/{foreign.pk}/move-slots/",q)
    assert r.status_code==400

def test_n05_viewer_cannot_use_move_preview(resource_service):
    t,o,svc,res=resource_service
    with scope(t):b,_=create_booking(t,payload(svc,hour=10,resource=str(res.pk)))
    c,u=client_as(t,role="viewer")
    r=c.get(f"/t/{t.slug}/bookings/{b.pk}/move-slots/",{"date":future_day(t).isoformat(),"service":str(svc.pk)})
    assert r.status_code==403

# N06 — inheritance: one service's arrival hours never alter another service
def test_n06_arrival_rule_of_a_does_not_change_b(sample):
    t,o,s=sample;day=future_day(t)
    with scope(t):
        a=Service.objects.create(tenant=t,location=s.location,name="Camera A",kind="day",lead_minutes=0)
        b=Service.objects.create(tenant=t,location=s.location,name="Camera B",kind="day",lead_minutes=0)
        before=windows(t,day,b,purpose="arrival")
        Schedule.objects.create(tenant=t,service=a,purpose="arrival",weekday=day.weekday(),start=clock(15),end=clock(16))
        assert windows(t,day,b,purpose="arrival")==before and before
        assert windows(t,day,a,purpose="arrival")!=before

def on_day(tenant,spans,day):
    tz=ZoneInfo(tenant.timezone)
    a=datetime.combine(day,clock.min,tz);b=datetime.combine(day+timedelta(days=1),clock.min,tz)
    return [(x,y) for x,y in spans if x<b and y>a]

def test_n06_exception_only_level_closes_only_its_day(sample):
    t,o,s=sample;day=future_day(t);other=day+timedelta(days=1)
    with scope(t):
        before=on_day(t,windows(t,other,s),other)
        ExceptionDay.objects.create(tenant=t,service=s,date=day,closed=True)
        assert on_day(t,windows(t,day,s),day)==[]
        assert on_day(t,windows(t,other,s),other)==before and before

# N07 — anonymous booking does not rewrite an existing contact card or consent
def test_n07_existing_contact_not_overwritten(sample):
    t,o,s=sample
    with scope(t):
        first,_=create_booking(t,payload(s))
        contact=Contact.objects.get(pk=first.contact_id)
        contact.consent="revoked";contact.save()
        original=contact.data
        second,_=create_booking(t,payload(s,hour=12,first_name="Intruso",last_name="X",phone="+390000000000",marketing=True))
        contact.refresh_from_db()
        assert second.contact_id==contact.pk
        assert contact.data==original and contact.consent=="revoked" and not contact.consent_proof
        assert second.data["first_name"]=="Intruso"

def test_n07_mailbox_owner_can_still_confirm(sample,rf):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s,marketing=True))
        c=Contact.objects.get(pk=b.contact_id);assert c.consent=="pending"
        token=ActionToken.objects.get(contact=c,kind="consent")
        request=rf.post("/");request.tenant=t
        apply_action(request,token)
        c.refresh_from_db()
        assert c.consent=="yes" and c.consent_proof

# N08 — typed validation of JSON values and extra fields
FIELDS=[{"key":"ok","label":"Accetto","type":"checkbox","required":True},{"key":"n","label":"Numero","type":"number","required":True},{"key":"t","label":"Testo","type":"text"}]
@pytest.mark.parametrize("change",[
    {"first_name":None},{"last_name":["x"]},{"email":{"a":1}},{"notes":5},
    {"answers":{"ok":"false","n":1}},{"answers":{"ok":"true","n":1}},{"answers":{"ok":False,"n":1}},
    {"answers":{"ok":True,"n":""}},{"answers":{"ok":True,"n":1,"t":{"x":1}}},{"answers":{"ok":True,"n":1,"t":["a"]}},{"answers":[]},
])
def test_n08_rejects_invalid_types(sample,change):
    t,o,s=sample
    with scope(t):s.fields=FIELDS;s.save()
    data=payload(s,answers={"ok":True,"n":1});data.update(change)
    with scope(t):
        with pytest.raises(BookingError):create_booking(t,data)
        assert Booking.objects.count()==0

def test_n08_zero_is_a_valid_required_number(sample):
    t,o,s=sample
    with scope(t):s.fields=FIELDS;s.save()
    with scope(t):
        b,_=create_booking(t,payload(s,answers={"ok":True,"n":0}))
        assert b.data["answers"]["n"]==0 and b.data["answers"]["ok"] is True

# N09 — complete impact analysis after catalog changes
def test_n09_capacity_reduction_is_detected(sample):
    from core.portal import calendar_conflicts
    t,o,s=sample
    with scope(t):
        a,_=create_booking(t,payload(s));b,_=create_booking(t,payload(s))
        assert calendar_conflicts(t)==[]
        s.capacity=1;s.save()
        assert set(calendar_conflicts(t))=={str(a.pk)[:8],str(b.pk)[:8]}

def test_n09_removed_resource_link_is_detected(resource_service):
    from core.portal import calendar_conflicts
    t,o,svc,res=resource_service
    with scope(t):
        b,_=create_booking(t,payload(svc,hour=10,resource=str(res.pk)))
        ServiceResource.objects.filter(service=svc).delete()
        assert calendar_conflicts(t)==[str(b.pk)[:8]]

def test_n09_no_truncation(sample,monkeypatch):
    from core import portal
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s))
        rows=[b]*2500  # same row repeated: the check must walk every element
        seen=[]
        monkeypatch.setattr(portal,"fits",lambda *a,**k:seen.append(1) or True)
        class Q(list):
            def select_related(self,*a):return self
            def order_by(self,*a):return self
        monkeypatch.setattr(portal.Booking.objects,"filter",lambda **k:Q(rows))
        portal.calendar_conflicts(t)
        assert len(seen)==2500

# N11 — Private edition has no commercial states; maintenance is separate
def test_n11_private_ignores_trial_and_suspension(sample,settings):
    t,o,s=sample
    settings.EDITION="private"
    t.status="trial";t.trial_until=date(2000,1,1);t.save()
    assert operational(t)
    t.status="suspended";t.save()
    assert operational(t)
    with scope(t):assert create_booking(t,payload(s))[1]
    t.maintenance=True;t.save()
    assert not operational(t)

def test_n11_cloud_keeps_commercial_states(sample,settings):
    t,o,s=sample
    settings.EDITION="cloud"
    t.status="trial";t.trial_until=date(2000,1,1);t.save()
    assert not operational(t)

def test_n11_private_form_hides_plan(sample,settings):
    from core.forms import TenantForm
    t,o,s=sample
    settings.EDITION="private"
    assert "status" not in TenantForm(instance=t).fields and "maintenance" in TenantForm(instance=t).fields
    settings.EDITION="cloud"
    assert "status" in TenantForm(instance=t).fields

def test_n11_maintenance_blocks_public_page(sample):
    from django.test import Client
    t,o,s=sample;t.maintenance=True;t.save()
    assert Client().get(f"/book/{t.slug}/").status_code==503

# N13 — expected RLS derived from models
def test_n13_expected_tables_from_models():
    from core.rls import tenant_tables
    tables=tenant_tables()
    assert {"core_contact","core_booking","core_delivery","core_actiontoken"}<=tables
    assert "core_membership" not in tables

def test_n13_missing_protection_is_reported():
    from core.rls import tenant_tables,problems
    ok={"rls":True,"force":True,"policy_using":"((tenant_id)::text = current_setting('app.tenant_id'::text, true))","policy_check":"((tenant_id)::text = current_setting('app.tenant_id'::text, true))","tenant_key":True}
    catalog={t:dict(ok) for t in tenant_tables()}
    assert problems(catalog)==[]
    catalog["core_contact"]["rls"]=False
    del catalog["core_delivery"]
    catalog["core_booking"]["policy_check"]=None
    found=" ".join(problems(catalog))
    assert "core_contact" in found and "core_delivery" in found and "core_booking" in found
    assert problems(catalog,expected=tenant_tables()|{"core_newmodel"})[-1].startswith("core_newmodel")

# N14 — backups: private from the first byte, never published unauthenticated
def test_n14_backup_roundtrip_and_tamper(tmp_path):
    from scripts.backup_crypto import transform
    key=os.urandom(32)
    src=tmp_path/"db.dump";src.write_bytes(os.urandom(300_000))
    enc=tmp_path/"db.enc";transform("encrypt",src,enc,key)
    assert stat.S_IMODE(enc.stat().st_mode)==0o600
    out=tmp_path/"db.out";transform("decrypt",enc,out,key)
    assert out.read_bytes()==src.read_bytes() and stat.S_IMODE(out.stat().st_mode)==0o600
    raw=bytearray(enc.read_bytes());raw[-40]^=1;bad=tmp_path/"bad.enc";bad.write_bytes(bytes(raw))
    with pytest.raises(Exception):transform("decrypt",bad,tmp_path/"never",key)
    assert not (tmp_path/"never").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".tdbs-backup-")]==[]
    with pytest.raises(ValueError):transform("decrypt",enc,out,key)

# N16 — separate quotas and SMTP selection for marketing and notifications
def test_n16_campaign_never_uses_global_smtp(sample,monkeypatch,settings):
    t,o,s=sample
    settings.EMAIL_HOST="global.example.invalid";settings.DEFAULT_FROM_EMAIL="noreply@example.invalid"
    marketing_ready(t)
    campaign_backlog(t,1,status="queued")
    t.smtp="";t.save()
    with scope(t):create_booking(t,payload(s))
    hosts=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:hosts.append((conf["host"],p["to"])))
    worker_once()
    assert hosts==[("global.example.invalid","mario@example.invalid")]
    with scope(t):assert Delivery.objects.get(campaign__isnull=False).last_code=="smtp_missing"

def test_n16_campaign_quota_does_not_consume_notifications(sample,monkeypatch):
    t,o,s=sample;marketing_ready(t)
    t.smtp_daily_limit=2;t.smtp_hourly_limit=2;t.save()
    campaign_backlog(t,5,status="queued")
    sent=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:sent.append(p["to"]))
    worker_once()
    assert len(sent)==2
    with scope(t):create_booking(t,payload(s))
    worker_once()
    assert sent[-1]=="mario@example.invalid" and len(sent)==3

def test_rename_legacy_backup_header_still_decrypts(tmp_path):
    """Backups written by the prototype under its old name stay restorable."""
    from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
    from scripts.backup_crypto import transform,LEGACY_HEADERS,HEADER
    key=os.urandom(32);data=os.urandom(5000);old=LEGACY_HEADERS[0];nonce=os.urandom(12)
    enc=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor();enc.authenticate_additional_data(old)
    body=enc.update(data)+enc.finalize()
    src=tmp_path/"old.enc";src.write_bytes(old+nonce+body+enc.tag)
    transform("decrypt",src,tmp_path/"old.out",key)
    assert (tmp_path/"old.out").read_bytes()==data and HEADER==b"TDBS-BACKUP-1\n"

def test_n13_real_catalog_on_postgres():
    """On a migrated PostgreSQL every tenant table is protected; removing FORCE is caught."""
    from django.db import connection
    from core.rls import tenant_tables,read_catalog,problems
    if connection.vendor!="postgresql":pytest.skip("Richiede PostgreSQL reale")
    tables=tenant_tables()
    with connection.cursor() as c:
        assert problems(read_catalog(c,tables),tables)==[]
        c.execute("ALTER TABLE core_contact NO FORCE ROW LEVEL SECURITY")
        assert any(p.startswith("core_contact") for p in problems(read_catalog(c,tables),tables))

# 2.0.1 — behind nginx/NPMplus the proxy appends the client address to X-Forwarded-For
@pytest.mark.parametrize("forwarded,expected",[("203.0.113.10",200),("198.51.100.7, 203.0.113.10",200),("bugia, 203.0.113.10",200),("",403),("203.0.113.10, non-un-ip",403)])
def test_forwarded_for_uses_proxy_entry(sample,settings,forwarded,expected):
    from django.test import Client
    t,o,s=sample
    settings.TRUST_PROXY=True
    headers={"HTTP_X_FORWARDED_FOR":forwarded} if forwarded else {}
    r=Client().get(f"/book/{t.slug}/api/slots/",{"service":str(s.pk),"date":future_day(t).isoformat()},**headers)
    assert r.status_code==expected

def test_forwarded_for_rate_limit_keys_on_proxy_entry(sample,settings):
    """A client cannot dodge the limit by forging the leftmost address."""
    from django.test import Client
    from core.identity import throttle
    from django.core.exceptions import PermissionDenied
    from django.test import RequestFactory
    settings.TRUST_PROXY=True
    rf=RequestFactory()
    for i in range(3):throttle(rf.get("/",HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 203.0.113.10"),"prova",limit=3)
    with pytest.raises(PermissionDenied):throttle(rf.get("/",HTTP_X_FORWARDED_FOR="10.9.9.9, 203.0.113.10"),"prova",limit=3)
