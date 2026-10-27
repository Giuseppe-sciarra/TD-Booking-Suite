import json,secrets,time as wallclock
from datetime import datetime,date,time as clock,timedelta,timezone as UTCZ
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest,pyotp
from cryptography.exceptions import InvalidTag
from django.conf import settings
from django.db import connection,connections,transaction,IntegrityError
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import *
from core.booking import *
from core.calendar import *
from core.platform import seed_tenant
from core.identity import verify_totp
from core.mail import enqueue_campaign,worker_once,public_addresses
from core.forms import ServiceForm
from core.shifts import materialize
pytestmark=pytest.mark.django_db

@pytest.fixture
def sample():
    tenant=Tenant.objects.create(slug="salone",name="Salone Test")
    other=Tenant.objects.create(slug="altro",name="Altra attività")
    with scope(tenant):
        loc=Location.objects.create(tenant=tenant,name="Centro")
        service=Service.objects.create(tenant=tenant,location=loc,name="Servizio",kind="capacity",capacity=2,max_party=2,lead_minutes=0)
        for d in range(7):Schedule.objects.create(tenant=tenant,weekday=d,start=clock(9),end=clock(18))
    return tenant,other,service
def payload(service,hour=10,**extra):
    day=(timezone.now()+timedelta(days=3)).astimezone(ZoneInfo(service.tenant.timezone)).date()
    data={"service":str(service.pk),"start":datetime.combine(day,clock(hour),ZoneInfo(service.tenant.timezone)).isoformat(),"party":1,"first_name":"Mario","last_name":"Rossi","email":"mario@example.invalid","phone":"+393331234567","notes":"Prodotto delicato","request_key":secrets.token_urlsafe(24)}
    data.update(extra);return data
def client_as(tenant,staff=False,role="owner"):
    u=User.objects.create_user(secrets.token_hex(4)+"@example.invalid","A-test-password-12345",is_staff=staff,is_superuser=staff)
    Membership.objects.create(tenant=tenant,user=u,role=role,can_schedule=True)
    c=Client();c.force_login(u,backend="core.identity.EmailBackend")
    s=c.session;s["mfa"]=True;s["auth_version"]=u.auth_version;s["verified_at"]=wallclock.time();s["last_seen"]=wallclock.time();s.save()
    return c,u

def test_encryption_and_aad(sample):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s))
        assert "+393331234567" not in b.pii and "Mario" not in b.pii
        assert b.data["phone"]=="+393331234567"
        with pytest.raises(InvalidTag):decrypt(o,b.pii,"booking:"+str(b.pk))
        with pytest.raises(InvalidTag):decrypt(t,b.pii,"booking:"+str(secrets.token_hex(16)))
        assert "mario@" not in b.contact.pii
def test_scope_fails_closed(sample):
    t,o,s=sample
    assert Service.objects.count()==0
    with scope(o):assert Service.objects.count()==0
    with scope(t):assert Service.objects.get(pk=s.pk).name=="Servizio"
def test_idempotency_and_changed_request(sample):
    t,o,s=sample;data=payload(s)
    with scope(t):
        b,created=create_booking(t,data);again,created2=create_booking(t,data)
        assert created and not created2 and again.pk==b.pk and Booking.objects.count()==1
        with pytest.raises(BookingError):create_booking(t,{**data,"notes":"Diverso"})
@pytest.mark.parametrize("field,value",[("email","bad"),("phone","telefono"),("first_name",""),("last_name",""),("party",0),("party",3),("party",-1),("party",True),("party",[]),("start","2030-01-01T10:00:00")])
def test_invalid_input(sample,field,value):
    t,o,s=sample
    with scope(t):
        with pytest.raises((BookingError,ValidationError)):create_booking(t,payload(s,**{field:value}))
        assert Booking.objects.count()==0
def test_whole_duration_inside_hours(sample):
    t,o,s=sample
    with scope(t):
        create_booking(t,payload(s,17))
        p=payload(s,17);p["start"]=p["start"].replace("17:00","17:30")
        with pytest.raises(BookingError):create_booking(t,p)
def test_lunch_holiday_and_receive(sample):
    t,o,s=sample;day=datetime.fromisoformat(payload(s)["start"]).date()
    with scope(t):
        Schedule.objects.all().delete()
        for d in range(7):
            Schedule.objects.create(tenant=t,weekday=d,start=clock(9),end=clock(13))
            Schedule.objects.create(tenant=t,weekday=d,start=clock(14),end=clock(18))
        p=payload(s,12);p["start"]=p["start"].replace("12:00","12:30")
        with pytest.raises(BookingError):create_booking(t,p)
        ExceptionDay.objects.create(tenant=t,date=day,closed=True)
        with pytest.raises(BookingError):create_booking(t,payload(s))
        ExceptionDay.objects.all().delete();t.receive_schedule=True;t.save()
        with pytest.raises(BookingError):create_booking(t,payload(s))
        create_booking(t,payload(s),actor=User.objects.create_user("staff@example.invalid","Test-password-123"))
def test_resource_validation_and_shared_conflict(sample):
    t,o,s=sample
    with scope(t):
        resource=Resource.objects.create(tenant=t,location=s.location,name="Operatore")
        s.kind="resource";s.save()
        ServiceResource.objects.create(tenant=t,service=s,resource=resource)
        with pytest.raises(BookingError):create_booking(t,payload(s,resource=str(uuid.uuid4())))
        create_booking(t,payload(s,resource=str(resource.pk)))
        another=Service.objects.create(tenant=t,location=s.location,name="Altro servizio",kind="resource",lead_minutes=0)
        ServiceResource.objects.create(tenant=t,service=another,resource=resource)
        with pytest.raises(BookingError):create_booking(t,payload(another,resource=str(resource.pk)))
        resource.active=False;resource.save()
        with pytest.raises(BookingError):create_booking(t,payload(s,11,resource=str(resource.pk)))
def test_invalid_option(sample):
    t,o,s=sample
    with scope(t):
        option=Option.objects.create(tenant=t,service=s,name="Lunga",duration=120,active=False)
        with pytest.raises(BookingError):create_booking(t,payload(s,option=str(option.pk)))
        with pytest.raises(BookingError):create_booking(t,payload(s,option=str(uuid.uuid4())))
def test_peak_capacity_not_sum(sample):
    t,o,s=sample
    with scope(t):
        create_booking(t,payload(s,9));create_booking(t,payload(s,10))
        option=Option.objects.create(tenant=t,service=s,name="Due ore",duration=120)
        create_booking(t,payload(s,9,option=str(option.pk)))
        assert Booking.objects.count()==3
        with pytest.raises(BookingError):create_booking(t,payload(s,9))
def test_move_updates_location(sample):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s))
        loc=Location.objects.create(tenant=t,name="Nord")
        next_s=Service.objects.create(tenant=t,location=loc,name="Nord",kind="capacity",lead_minutes=0)
        b=move_booking(t,b.pk,payload(next_s,11),None)
        assert b.location_id==loc.pk and b.snapshot["location"]=="Nord"
def test_cancel_get_then_post(sample):
    t,o,s=sample
    with scope(t):b,_=create_booking(t,payload(s));token=b.data["cancel_token"]
    c=Client();url=f"/action/{t.slug}/{token}/"
    assert c.get(url).status_code==200
    with scope(t):assert Booking.objects.get(pk=b.pk).status=="confirmed"
    assert c.post(url).status_code==200
    with scope(t):assert Booking.objects.get(pk=b.pk).status=="cancelled"
def test_cross_tenant_and_roles(sample):
    t,o,s=sample
    c,u=client_as(t,role="viewer")
    assert c.get(f"/t/{o.slug}/").status_code==403
    assert c.get(f"/t/{t.slug}/export/").status_code==403
    assert c.get(f"/t/{t.slug}/smtp/").status_code==403
    assert c.get(f"/t/{t.slug}/booking-api/submit/").status_code==405
def test_export_available_when_suspended(sample):
    t,o,s=sample;c,u=client_as(t)
    t.status="suspended";t.save()
    assert c.get(f"/t/{t.slug}/").status_code==402
    assert c.get(f"/t/{t.slug}/export/").status_code==200
def test_pages_render_and_no_script_injection(sample):
    t,o,s=sample;c,u=client_as(t,staff=True)
    with scope(t):
        b,_=create_booking(t,payload(s,first_name='<img src=x onerror="alert(1)">'))
    paths=["/platform/","/platform/clienti/",f"/platform/clienti/{t.slug}/","/security/",f"/t/{t.slug}/",f"/t/{t.slug}/bookings/",f"/t/{t.slug}/contacts/",f"/t/{t.slug}/marketing/",f"/t/{t.slug}/settings/",f"/t/{t.slug}/smtp/",f"/t/{t.slug}/members/",f"/t/{t.slug}/audit/",f"/t/{t.slug}/catalog/services/",f"/t/{t.slug}/catalog/services/new/",f"/t/{t.slug}/catalog/turns/new/",f"/t/{t.slug}/bookings/{b.pk}/",f"/book/{t.slug}/"]
    for path in paths:
        r=c.get(path);assert r.status_code==200,(path,r.status_code)
        assert b'<img src=x onerror=' not in r.content
        assert "script-src 'self'" in r.headers["Content-Security-Policy"]
def test_csrf_protected(sample):
    t,o,s=sample
    c=Client(enforce_csrf_checks=True)
    assert c.post("/login/",{"email":"a@example.invalid","password":"x"}).status_code==403
    assert c.post(f"/book/{t.slug}/api/submit/",data="{}",content_type="application/json",HTTP_ORIGIN="https://evil.invalid").status_code==403
def test_totp_replay_and_password_reset(sample):
    t,o,s=sample;c,u=client_as(t)
    seed=pyotp.random_base32();u.totp=protect(seed,"totp:"+str(u.pk));u.save()
    code=pyotp.TOTP(seed).now()
    assert verify_totp(u,code)
    assert not verify_totp(u,code)
    u.set_password("Replacement-password-123");u.save()
    assert c.get(f"/t/{t.slug}/").status_code==302
def test_consent_and_worker_recheck(sample,monkeypatch):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s,marketing=True))
        c=b.contact;c.refresh_from_db();assert c.consent=="pending"
        c.consent="yes";c.save()
        t.marketing_enabled=True;t.smtp=encrypt(t,{"host":"smtp.example.invalid","port":587,"mode":"starttls","username":"","password":"","sender":"test@example.invalid","reply_to":""},"smtp");t.save()
        campaign=Campaign.objects.create(tenant=t,name="Novità",subject="Novità",body="Buongiorno")
        enqueue_campaign(t,campaign,None)
        c.consent="revoked";c.save()
    sent=[];monkeypatch.setattr("core.mail.send_message",lambda conf,payload,mid:sent.append(payload))
    worker_once()
    with scope(t):assert Delivery.objects.get(campaign=campaign).status=="skipped"
    assert all("unsubscribe" not in p for p in sent)
def test_csv_formula(sample):
    from core.portal import csv_cell
    assert csv_cell("=1+1","sheet")=="'=1+1"
    assert csv_cell("+393331234567","brevo")=="+393331234567"
def test_smtp_blocks_private(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo",lambda *a,**kw:[(2,1,6,"",("127.0.0.1",587))])
    with pytest.raises(ValueError):public_addresses("smtp.example.invalid",587)
def test_day_inventory_checkout_overlap(sample):
    t,o,s=sample
    with scope(t):
        s.kind="day";s.capacity=1;s.max_party=1;s.checkin=clock(9);s.checkout=clock(17);s.save()
        p=payload(s,9);day=datetime.fromisoformat(p["start"]).date();p["end_date"]=(day+timedelta(days=2)).isoformat()
        create_booking(t,p)
        q={**p,"request_key":secrets.token_urlsafe(24),"start":datetime.combine(day+timedelta(days=2),clock(9),ZoneInfo(t.timezone)).isoformat(),"end_date":(day+timedelta(days=3)).isoformat()}
        with pytest.raises(BookingError):create_booking(t,q)
def test_dst_and_overnight(sample):
    t,o,s=sample
    assert local_datetime(date(2027,3,28),clock(2,30),ZoneInfo("Europe/Rome")) is None
    assert local_datetime(date(2026,10,25),clock(2,30),ZoneInfo("Europe/Rome"),0).astimezone(UTC)!=local_datetime(date(2026,10,25),clock(2,30),ZoneInfo("Europe/Rome"),1).astimezone(UTC)
    with scope(t):
        Schedule.objects.all().delete()
        Schedule.objects.create(tenant=t,weekday=0,start=clock(22),end=clock(2))
        a=datetime(2030,1,8,0,30,tzinfo=ZoneInfo(t.timezone))
        assert fits(t,a,a+timedelta(minutes=60),s)
def test_recurring_restaurant_turns(sample):
    t,o,s=sample
    with scope(t):
        s.kind="shift";s.capacity=4;s.max_party=4;s.save()
        day=datetime.fromisoformat(payload(s)["start"]).date()
        rule=ShiftRule.objects.create(tenant=t,service=s,name="Pranzo 1",weekday=day.weekday(),start=clock(12),end=clock(14),capacity=4)
        materialize(rule)
        assert Occurrence.objects.filter(rule=rule).count()>1
        data=payload(s,12,party=4);create_booking(t,data)
        with pytest.raises(BookingError):create_booking(t,payload(s,12))
        with pytest.raises(BookingError):materialize(rule)
@pytest.mark.django_db(transaction=True)
def test_postgres_concurrency_last_place(sample):
    if connection.vendor!="postgresql":pytest.skip("Richiede PostgreSQL reale")
    t,o,s=sample
    with scope(t):s.capacity=1;s.save()
    gate=Barrier(2)
    def run():
        connections.close_all();gate.wait(timeout=10)
        try:
            with scope(t):
                return create_booking(t,payload(s))[0].pk
        except BookingError:return None
        finally:connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:run(),range(2)))
    assert sum(x is not None for x in results)==1
@pytest.mark.django_db(transaction=True)
def test_postgres_rls_and_composite_fk(sample):
    if connection.vendor!="postgresql":pytest.skip("Richiede PostgreSQL reale")
    t,o,s=sample
    with connection.cursor() as c:
        c.execute("GRANT USAGE ON SCHEMA public TO tdbooking_app")
        c.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO tdbooking_app")
    with transaction.atomic():
        with connection.cursor() as c:
            c.execute("SET LOCAL ROLE tdbooking_app")
            c.execute("SELECT count(*) FROM core_service");assert c.fetchone()[0]==0
            c.execute("SELECT set_config('app.tenant_id',%s,true)",[str(o.pk)])
            c.execute("SELECT count(*) FROM core_service");assert c.fetchone()[0]==0
            c.execute("SELECT set_config('app.tenant_id',%s,true)",[str(t.pk)])
            c.execute("SELECT count(*) FROM core_service");assert c.fetchone()[0]==1
    with scope(o):
        loc=Location.objects.create(tenant=o,name="Altra")
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Service.raw.filter(pk=s.pk).update(location=loc)
            with connection.cursor() as c:c.execute("SET CONSTRAINTS ALL IMMEDIATE")

