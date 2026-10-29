"""Release sweep: every portal page, for every role, never a server error; markup sanity on what renders."""
import re,secrets
from datetime import timedelta
from html.parser import HTMLParser
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking
from tests.test_core import sample,payload,client_as
from tests.test_phase1 import resource_service
from tests.test_phase2 import staff_client

pytestmark=pytest.mark.django_db

class Markup(HTMLParser):
    def __init__(self):
        super().__init__();self.ids=[];self.labels=[];self.inputs_without_label=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if "id" in a:self.ids.append(a["id"])
        if tag=="label" and a.get("for"):self.labels.append(a["for"])

def check_markup(html,path):
    m=Markup();m.feed(html)
    dupes={i for i in m.ids if m.ids.count(i)>1}
    assert not dupes,(path,"id duplicati",dupes)
    missing=[f for f in m.labels if f not in m.ids]
    assert not missing,(path,"etichette senza campo",missing)
    assert html.count("<main")<=1 and '<html lang="it"' in html,(path,"struttura pagina")

@pytest.fixture
def world(resource_service):
    t,o,svc,res=resource_service
    with scope(t):
        b,_=create_booking(t,payload(svc,hour=10,resource=str(res.pk),marketing=True))
        campaign=Campaign.objects.create(tenant=t,name="Promo",subject="Promo",body="Ciao")
        occ=Occurrence.objects.create(tenant=t,service=svc,start=timezone.now()+timedelta(days=5),end=timezone.now()+timedelta(days=5,hours=1),capacity=10)
        ids={"booking":b.pk,"contact":b.contact_id,"campaign":campaign.pk,"service":svc.pk,"resource":res.pk,"location":svc.location_id,
             "schedule":Schedule.objects.filter(service__isnull=True).first().pk,"occurrence":occ.pk}
    return t,svc,res,ids

def pages(t,ids):
    p=f"/t/{t.slug}"
    out=["/","/security/","/platform/","/platform/clienti/","/platform/clienti/nuovo/",f"/platform/clienti/{t.slug}/",f"{p}/",f"{p}/bookings/",f"{p}/bookings/new/",f"{p}/bookings/{ids['booking']}/",f"{p}/bookings/{ids['booking']}/move/",
         f"{p}/contacts/",f"{p}/contacts/{ids['contact']}/",f"{p}/marketing/",f"{p}/marketing/new/",f"{p}/marketing/{ids['campaign']}/",f"{p}/audit/",
         f"{p}/members/",f"{p}/support/",f"{p}/notifications/",f"{p}/settings/",f"{p}/smtp/",f"{p}/setup/",f"{p}/guida/","/guida/",f"/book/{t.slug}/",f"/book/{t.slug}/?location=x"]
    for kind,key in (("services","service"),("resources","resource"),("locations","location"),("schedules","schedule"),("occurrences","occurrence")):
        out+=[f"{p}/catalog/{kind}/",f"{p}/catalog/{kind}/new/",f"{p}/catalog/{kind}/{ids[key]}/"]
    for kind in ("options","exceptions","turns"):out+=[f"{p}/catalog/{kind}/",f"{p}/catalog/{kind}/new/"]
    out.append(f"{p}/catalog/services/new/?preset=hotel")
    return out

@pytest.mark.parametrize("role",["owner","manager","reception","operator","marketing","viewer"])
def test_every_page_for_every_role(world,role):
    t,svc,res,ids=world
    c,u=client_as(t,role=role)
    if role=="operator":
        m=Membership.objects.get(tenant=t,user=u);m.resources=[str(res.pk)];m.save()
    t.marketing_enabled=t.export_enabled=t.client_mails_enabled=True;t.save()
    seen={}
    for path in pages(t,ids):
        r=c.get(path)
        assert r.status_code in (200,302,403,404),(role,path,r.status_code)
        seen[path]=r.status_code
        if r.status_code==200 and r["Content-Type"].startswith("text/html"):check_markup(r.content.decode(),path)
    p=f"/t/{t.slug}"
    if role=="owner":
        # 2.10.0: the owner keeps hours, closing days and staff, 2.11.0 services and prices too; type and locations are the agency's
        agency_only=lambda x:any(f"{p}/catalog/{k}/" in x for k in ("locations","turns","occurrences"))
        # 2.13.0: people and permissions too
        must=[x for x in pages(t,ids) if not x.startswith("/platform/") and x not in (f"{p}/settings/",f"{p}/smtp/",f"{p}/members/") and not agency_only(x)]
        assert all(seen[x] in (200,302) for x in must),{x:seen[x] for x in must if seen[x] not in (200,302)}
        assert all(seen[x]==403 for x in pages(t,ids) if agency_only(x) and x in seen) and seen[f"{p}/members/"]==403
    if role=="marketing":assert seen[f"{p}/bookings/"]==403 and seen[f"{p}/contacts/"]==200
    if role=="viewer":assert seen[f"{p}/bookings/"]==200 and seen[f"{p}/members/"]==403

def test_every_page_for_platform_staff(world):
    t,svc,res,ids=world
    c,u=staff_client()
    p=f"/t/{t.slug}"
    for path in pages(t,ids):
        r=c.get(path)
        assert r.status_code in (200,302,403,404),(path,r.status_code)
        if path.startswith(p) and path.split("/")[3] not in ("settings","smtp","members","guida","setup","catalog"):  # 2.6.0: the agency configures
            assert r.status_code==403,(path,"contenuto senza concessione")
        if r.status_code==200 and r["Content-Type"].startswith("text/html"):check_markup(r.content.decode(),path)
    with scope(t):SupportGrant.objects.create(tenant=t,user=u,reason="Verifica completa",expires=timezone.now()+timedelta(hours=1),created_by="owner")
    for path in pages(t,ids):
        r=c.get(path);assert r.status_code in (200,302,403,404),(path,r.status_code)
        if r.status_code==200 and r["Content-Type"].startswith("text/html"):check_markup(r.content.decode(),path)

def test_anonymous_never_sees_portal(world):
    t,svc,res,ids=world
    c=Client()
    for path in pages(t,ids):
        r=c.get(path)
        if path.startswith("/book/"):assert r.status_code==200
        else:assert r.status_code==302 and "/login/" in r["Location"],(path,r.status_code)
    for path in ("/login/","/book/"+t.slug+"/"):check_markup(c.get(path).content.decode(),path)
