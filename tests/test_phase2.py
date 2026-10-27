"""Phase 2 — granular roles, scope and platform support access (finding N10)."""
import secrets,time as wallclock
from datetime import timedelta
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking
from tests.test_core import sample,payload,client_as
from tests.test_phase1 import resource_service,future_day

pytestmark=pytest.mark.django_db

@pytest.fixture
def two_rooms(resource_service):
    """Two resources of the same service, one booking each, with notes."""
    t,o,svc,res=resource_service
    with scope(t):
        other=Resource.objects.create(tenant=t,location=svc.location,name="Sala 2")
        ServiceResource.objects.create(tenant=t,service=svc,resource=other)
        mine,_=create_booking(t,payload(svc,hour=10,resource=str(res.pk),notes="Allergia nichel"))
        theirs,_=create_booking(t,payload(svc,hour=11,resource=str(other.pk),email="anna@example.invalid",first_name="Anna",last_name="Bianchi"))
    return t,svc,res,other,mine,theirs

def member(t,role,**scope_fields):
    c,u=client_as(t,role=role)
    m=Membership.objects.get(tenant=t,user=u)
    for k,v in scope_fields.items():setattr(m,k,v)
    m.can_schedule=False;m.save()
    return c,u

def staff_client(fresh=True):
    u=User.objects.create_user(secrets.token_hex(4)+"@example.invalid","A-test-password-12345",is_staff=True,is_superuser=True)
    c=Client();c.force_login(u,backend="core.identity.EmailBackend")
    s=c.session;s["mfa"]=True;s["auth_version"]=u.auth_version;s["last_seen"]=wallclock.time()
    s["verified_at"]=wallclock.time() if fresh else wallclock.time()-3600;s.save()
    return c,u

# Operator: only the bookings of the assigned resources
def test_operator_sees_only_assigned(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,u=member(t,"operator",resources=[str(res.pk)])
    day=future_day(t).isoformat()
    page=c.get(f"/t/{t.slug}/bookings/?from={day}&to={day}").content.decode()
    assert "Mario" in page and "Anna" not in page
    assert c.get(f"/t/{t.slug}/bookings/{mine.pk}/").status_code==200
    assert c.get(f"/t/{t.slug}/bookings/{theirs.pk}/").status_code==404
    assert c.get(f"/t/{t.slug}/bookings/{theirs.pk}/move/").status_code==404
    assert c.get(f"/t/{t.slug}/bookings/{theirs.pk}/move-slots/",{"date":day,"service":str(svc.pk)}).status_code==400
    assert c.get(f"/t/{t.slug}/contacts/").status_code==403
    assert c.get(f"/t/{t.slug}/export/?kind=bookings").status_code==403

def test_operator_cannot_book_other_resource(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,u=member(t,"operator",resources=[str(res.pk)])
    data=payload(svc,hour=14,resource=str(other.pk))
    r=c.post(f"/t/{t.slug}/booking-api/submit/",data=data,content_type="application/json")
    assert r.status_code==403
    with scope(t):assert Booking.objects.count()==2

# Marketing: contacts and campaigns, never the agenda or operational notes
def test_marketing_never_sees_notes(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,u=member(t,"marketing")
    assert c.get(f"/t/{t.slug}/bookings/").status_code==403
    assert c.get(f"/t/{t.slug}/bookings/{mine.pk}/").status_code==403
    assert c.get(f"/t/{t.slug}/export/?kind=bookings").status_code==403
    page=c.get(f"/t/{t.slug}/contacts/").content.decode()
    assert "mario@example.invalid" in page and "nichel" not in page
    assert c.get(f"/t/{t.slug}/export/?kind=contacts").status_code==200
    assert "nichel" not in c.get(f"/t/{t.slug}/").content.decode()

# Viewer: agenda with masked personal data, no changes
def test_viewer_sees_masked_data(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,u=member(t,"viewer")
    detail=c.get(f"/t/{t.slug}/bookings/{mine.pk}/").content.decode()
    assert "Rossi" not in detail and "mario@example.invalid" not in detail and "+393331234567" not in detail and "nichel" not in detail
    assert "M." in detail
    assert c.post(f"/t/{t.slug}/bookings/{mine.pk}/",{"action":"cancelled"}).status_code==403
    with scope(t):assert Booking.objects.get(pk=mine.pk).status=="confirmed"

def test_location_scope(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    with scope(t):
        far=Location.objects.create(tenant=t,name="Altra sede")
    c,u=member(t,"manager",locations=[str(far.pk)])
    assert c.get(f"/t/{t.slug}/bookings/{mine.pk}/").status_code==404
    c2,u2=member(t,"manager",locations=[str(svc.location_id)])
    assert c2.get(f"/t/{t.slug}/bookings/{mine.pk}/").status_code==200

# Platform staff: technical pages only, content through a time-boxed grant
def test_staff_without_grant_sees_no_content(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,u=staff_client()
    assert c.get(f"/t/{t.slug}/").status_code==403
    assert c.get(f"/t/{t.slug}/bookings/{mine.pk}/").status_code==403
    assert c.get(f"/t/{t.slug}/export/?kind=contacts").status_code==403
    assert c.get(f"/t/{t.slug}/settings/").status_code==200
    for path in ("/platform/","/platform/clienti/",f"/platform/clienti/{t.slug}/"):
        platform=c.get(path).content.decode()
        assert t.name in platform and "mario@example.invalid" not in platform and "Mario" not in platform,path
    card=c.get(f"/platform/clienti/{t.slug}/").content.decode()
    assert "<span>In arrivo</span><strong>2</strong>" in card

def test_support_grant_is_scoped_timed_and_audited(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,u=staff_client()
    with scope(t):grant=SupportGrant.objects.create(tenant=t,user=u,reason="Verifica orari errati",expires=timezone.now()+timedelta(hours=1),created_by="owner")
    assert c.get(f"/t/{t.slug}/bookings/{mine.pk}/").status_code==200
    assert c.get(f"/t/{t.slug}/members/").status_code==200
    assert c.get(f"/t/{t.slug}/export/?kind=contacts").status_code==403
    with scope(t):
        assert Audit.objects.filter(action="support.access",target=str(grant.pk)).count()>=1
        grant.revoked=True;grant.save()
    assert c.get(f"/t/{t.slug}/bookings/{mine.pk}/").status_code==403

def test_support_grant_needs_recent_mfa_and_not_expired(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    stale,u=staff_client(fresh=False)
    with scope(t):SupportGrant.objects.create(tenant=t,user=u,reason="Verifica orari errati",expires=timezone.now()+timedelta(hours=1),created_by="owner")
    r=stale.get(f"/t/{t.slug}/")  # 2.3.0: the confirmation page instead of a dead end
    assert r.status_code==302 and r["Location"].startswith("/verify/confirm/")
    c,u2=staff_client()
    with scope(t):SupportGrant.objects.create(tenant=t,user=u2,reason="Verifica orari errati",expires=timezone.now()-timedelta(minutes=1),created_by="owner")
    assert c.get(f"/t/{t.slug}/").status_code==403

def test_owner_grants_and_revokes_support(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,owner=client_as(t,role="owner")
    s,staff_user=staff_client()
    r=c.post(f"/t/{t.slug}/support/",{"action":"grant","email":"nobody@example.invalid","reason":"Controllo agenda","hours":"4"})
    assert "Nessun amministratore" in r.content.decode()
    r=c.post(f"/t/{t.slug}/support/",{"action":"grant","email":staff_user.email,"reason":"Controllo agenda","hours":"4"})
    assert r.status_code==302
    with scope(t):
        grant=SupportGrant.objects.get(user=staff_user)
        assert Audit.objects.filter(action="support.granted").exists()
    assert s.get(f"/t/{t.slug}/").status_code==200
    page=c.get(f"/t/{t.slug}/support/").content.decode()
    assert staff_user.email in page and "Attivo fino" in page and f"/t/{t.slug}/" in page
    c.post(f"/t/{t.slug}/support/",{"action":"revoke","grant":str(grant.pk)})
    assert s.get(f"/t/{t.slug}/").status_code==403
    op,_=member(t,"operator",resources=[str(res.pk)])
    assert op.get(f"/t/{t.slug}/support/").status_code==403

# Owner manages the team; guard rails on roles and scope
def test_owner_manages_team(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    c,owner=client_as(t,role="owner")
    with scope(t):Location.objects.create(tenant=t,name="Seconda sede")  # with two locations the scope is mandatory
    r=c.post(f"/t/{t.slug}/members/",{"action":"invite","email":"op@example.invalid","role":"operator"})
    assert "almeno una sede o una risorsa" in r.content.decode()
    c.post(f"/t/{t.slug}/members/",{"action":"invite","email":"op@example.invalid","role":"operator","resources":[str(res.pk)]})
    m=Membership.objects.get(tenant=t,user__login_index=User.objects.get(pk=Membership.objects.filter(tenant=t,role="operator").first().user_id).login_index)
    assert m.resources==[str(res.pk)]
    own=Membership.objects.get(tenant=t,user=owner)
    r=c.post(f"/t/{t.slug}/members/",{"action":"remove","member":str(own.pk)})
    assert "almeno un amministratore" in r.content.decode()
    r=c.post(f"/t/{t.slug}/members/",{"action":"invite","email":"x@example.invalid","role":"superadmin"})
    assert "Ruolo non valido" in r.content.decode()

def test_foreign_scope_ids_are_dropped(two_rooms):
    t,svc,res,other,mine,theirs=two_rooms
    o=Tenant.objects.get(slug="altro")
    with scope(o):foreign=Location.objects.create(tenant=o,name="Estranea")
    c,owner=client_as(t,role="owner")
    c.post(f"/t/{t.slug}/members/",{"action":"invite","email":"rec@example.invalid","role":"reception","locations":[str(foreign.pk)]})
    m=Membership.objects.filter(tenant=t,role="reception").first()
    assert m.locations==[]
