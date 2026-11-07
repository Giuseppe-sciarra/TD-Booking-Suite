"""2.14.0, da Cal.com: limiti per servizio e operatore, servizi riservati con link, pagina di
ringraziamento, domande condizionali, Outlook / Microsoft 365 (Graph finto), fuori ufficio con sostituto."""
import io,json
from datetime import datetime,timedelta
from urllib.error import HTTPError
from urllib.parse import urlsplit,parse_qs
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import encrypt
from core.booking import create_booking,available,limited,extras,BookingError
from core import mscal
from tests.test_core import sample,payload,client_as
from tests.test_reminders import salon

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")
def day3():return (timezone.now()+timedelta(days=3)).astimezone(ROME).date()

def test_limits_online_per_operator_and_service(sample):
    t,o,s=sample;svc,marco,giulia=salon(t);day=day3()
    with scope(t):
        Resource.objects.filter(pk=marco.pk).update(limits={"day_n":1})
        create_booking(t,payload(svc,resource=str(marco.pk)))
        assert not available(t,svc.pk,day,resource_id=str(marco.pk))  # his one booking of the day is taken
        assert {x["resource_name"] for x in available(t,svc.pk,day)}=={"Giulia"}
        assert available(t,svc.pk,day,resource_id=str(marco.pk),desk=True)  # the desk is not bound
        Service.objects.filter(pk=svc.pk).update(limits={"week_m":30});svc.refresh_from_db()
        assert not available(t,svc.pk,day) and "oltre i 30 minuti prenotati a settimana di Taglio"==limited(t,svc,day,30)
        with pytest.raises(BookingError):create_booking(t,payload(svc,hour=14,email="b@example.invalid"))

def test_private_service_only_from_its_link(sample):
    t,o,s=sample
    with scope(t):
        hidden=Service.objects.create(tenant=t,location=s.location,name="Consulenza VIP",kind="capacity",capacity=2,lead_minutes=0,secret="s3cr3t-link-value-xyz")
    c=Client();page=c.get(f"/book/{t.slug}/")
    assert "Consulenza VIP" not in page.content.decode()
    r=c.post(f"/book/{t.slug}/api/submit/",json.dumps(payload(hidden)),content_type="application/json",HTTP_ORIGIN="http://localhost:8000",HTTP_X_TDBS_TICKET=page.context["widget"]["ticket"])
    assert r.status_code==400
    page=c.get(f"/book/{t.slug}/?riservato=s3cr3t-link-value-xyz");w=page.context["widget"]
    assert [x["name"] for x in w["services"]]==["Consulenza VIP"]
    r=c.post(f"/book/{t.slug}/api/submit/",json.dumps(payload(hidden)),content_type="application/json",HTTP_ORIGIN="http://localhost:8000",HTTP_X_TDBS_TICKET=w["ticket"])
    assert r.status_code==201

def test_thanks_page(sample):
    t,o,s=sample
    with scope(t):Service.objects.filter(pk=s.pk).update(thanks_url="https://example.com/grazie")
    c=Client();ticket=c.get(f"/book/{t.slug}/").context["widget"]["ticket"]
    r=c.post(f"/book/{t.slug}/api/submit/",json.dumps(payload(s,hour=12,email="c@example.invalid")),content_type="application/json",HTTP_ORIGIN="http://localhost:8000",HTTP_X_TDBS_TICKET=ticket)
    assert r.json()["thanks"]=="https://example.com/grazie"

def test_conditional_question(sample):
    t,o,s=sample
    with scope(t):
        s.fields=[{"key":"first","label":"Prima volta?","type":"checkbox","required":False},{"key":"how","label":"Come ci hai conosciuto?","type":"text","required":True,"show_if":{"key":"first","value":"sì"}}]
        assert "how" not in extras({"answers":{"first":False,"how":"x"}},s)["answers"]
        with pytest.raises(BookingError):extras({"answers":{"first":True}},s)
        assert extras({"answers":{"first":True,"how":"Instagram"}},s)["answers"]["how"]=="Instagram"
    from core.forms import ServiceForm
    bad=[{"key":"how","label":"X","type":"text","show_if":{"key":"later","value":"sì"}},{"key":"later","label":"Y","type":"checkbox"}]
    f=ServiceForm(tenant=t,instance=s);f.cleaned_data={"fields":bad}
    with pytest.raises(Exception):f.clean_fields()

class Answer:
    def __init__(self,data):self.raw=json.dumps(data).encode() if data is not None else b"";self.status=200
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self,n=-1):return self.raw
class Graph:
    def __init__(self):self.calls=[];self.events={};self.outside=[];self.n=0
    def __call__(self,req,timeout=None):
        m,url=req.get_method(),req.full_url;body=req.data.decode() if req.data else ""
        if body and url!=mscal.TOKEN:body=json.loads(body)
        self.calls.append((m,url,body))
        if url==mscal.TOKEN:self.n+=1;return Answer({"access_token":f"at{self.n}","expires_in":3600,"refresh_token":"r2"})
        if m=="GET" and url==mscal.GRAPH[:-1]:return Answer({"mail":"marco@outlook.com"})
        if m=="GET":return Answer({"value":self.outside+[{"id":i,**e} for i,e in self.events.items()],"@odata.deltaLink":mscal.GRAPH+"calendarView/delta?$deltatoken=d1"})
        if m=="POST":i=f"ev{len(self.events)}";self.events[i]=body;return Answer({"id":i,**body})
        eid=urlsplit(url).path.rsplit("/",1)[-1]
        if eid not in self.events:raise HTTPError(url,404,"no",{},io.BytesIO(b'{"error":{"code":"ErrorItemNotFound"}}'))
        if m=="PATCH":self.events[eid].update(body);return Answer(self.events[eid])
        if m=="DELETE":del self.events[eid];return Answer(None)

def test_outlook_connect_read_and_write(sample,monkeypatch):
    t,o,s=sample;svc,marco,giulia=salon(t);g=Graph();monkeypatch.setattr("urllib.request.urlopen",g)
    owner,_=client_as(t);agency,_=client_as(o,staff=True);card=f"/t/{t.slug}/catalog/resources/{marco.pk}/"
    assert agency.post(f"/t/{t.slug}/messaggi/",{"action":"microsoft","ms_client_id":"nope"}).status_code==200
    agency.post(f"/t/{t.slug}/messaggi/",{"action":"microsoft","ms_client_id":"1a2b3c4d-1234-5678-9abc-1234567890ab","ms_client_secret":"s3~cret"})
    t.refresh_from_db();assert "s3~cret" not in t.microsoft and mscal.config(t)["client_secret"]=="s3~cret"
    r=owner.post(card+"outlook/",{"action":"connect"});q=parse_qs(urlsplit(r["Location"]).query)
    assert r["Location"].startswith(mscal.AUTH) and q["code_challenge_method"]==["S256"] and "offline_access" in q["scope"][0]
    assert owner.get("/oauth/outlook-calendar/",{"state":"forged","code":"c"}).status_code==302 and not g.calls
    back=owner.get("/oauth/outlook-calendar/",{"state":q["state"][0],"code":"the-code"})
    assert owner.get(back["Location"])["Location"]==card
    with scope(t):marco.refresh_from_db();assert mscal.state(marco)["account"]=="marco@outlook.com" and "r2" not in marco.microsoft
    with scope(t):b=create_booking(t,payload(svc,resource=str(marco.pk)))[0]
    soon=(timezone.now()+timedelta(days=1)).replace(hour=10,minute=0,second=0,microsecond=0)  # a fixed hour: late in the evening the event would cross midnight in Rome and make two blocks
    g.outside=[{"id":"x","start":{"dateTime":soon.strftime("%Y-%m-%dT%H:%M:%S.0000000"),"timeZone":"UTC"},"end":{"dateTime":(soon+timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.0000000"),"timeZone":"UTC"},"showAs":"busy"},{"id":"f","showAs":"free","start":{},"end":{}}]
    assert mscal.refresh()==1
    with scope(t):
        assert [e["subject"] for e in g.events.values()]==["Taglio"]
        blocks=list(TimeBlock.objects.filter(resource=marco,reason="outlook"))
        assert len(blocks)==1  # the outside event only: ours and the free one are not busy blocks
        Booking.objects.filter(pk=b.pk).update(status="cancelled");marco.refresh_from_db();mscal.sync(t,marco,timezone.now())
        assert not g.events and not marco.microsoft_error

def test_out_of_office_stand_in(sample):
    t,o,s=sample;svc,marco,giulia=salon(t);day=day3()
    with scope(t):
        Resource.objects.filter(pk=marco.pk).update(away_from=day,away_until=day,away_by=giulia)
        assert not available(t,svc.pk,day,resource_id=str(marco.pk))
        assert {x["resource_name"] for x in available(t,svc.pk,day)}=={"Giulia"}
        Resource.objects.filter(pk=marco.pk).update(away_from=None,away_until=None,away_by=None)
        Resource.objects.filter(pk=giulia.pk).update(away_from=day,away_until=day,away_by=marco)
        assert available(t,svc.pk,day)[0]["resource_name"]=="Marco"  # the stand-in first in «Il primo disponibile»
    w=Client().get(f"/book/{t.slug}/").context["widget"]
    assert next(r for x in w["services"] if x["name"]=="Taglio" for r in x["resources"] if r["name"]=="Giulia")["away"][2]==str(marco.pk)
