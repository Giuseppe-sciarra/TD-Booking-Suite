"""2.14.0: Google Calendar per operatore, nei due sensi: client OAuth dell'agenzia cifrato, collegamento con state e PKCE,
token cifrati, impegni letti con events.list (syncToken, 410 = tutto da capo) come blocchi Occupato, prenotazioni scritte
con id fisso (insert idempotente con 409, patch allo spostamento, delete all'annullamento). Google è finto: urlopen."""
import base64,hashlib,io,json
from datetime import datetime,timedelta
from urllib.error import HTTPError
from urllib.parse import urlsplit,parse_qs
from zoneinfo import ZoneInfo
import pytest
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import encrypt
from core import gcal
from core.calendar import blocked
from tests.test_core import sample,client_as
from tests.test_reminders import salon
from tests.test_calendars_texts import booked

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")
CLIENT={"client_id":"123-abc.apps.googleusercontent.com","client_secret":"GOCSPX-secret"}

class Answer:
    def __init__(self,data):self.raw=json.dumps(data).encode() if data is not None else b"";self.status=200
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self,n=-1):return self.raw

class Google:
    """Just enough of Google: tokens, the event list with its sync token, and events by id."""
    def __init__(self):self.calls=[];self.events={};self.items=[];self.changes=[];self.revoked=False;self.expired=False;self.n=0
    def fail(self,url,code,data):raise HTTPError(url,code,"no",{},io.BytesIO(json.dumps(data).encode()))
    def __call__(self,req,timeout=None):
        m,url=req.get_method(),req.full_url;body=req.data.decode() if req.data else ""
        self.calls.append((m,url,body));q=parse_qs(urlsplit(url).query)
        if url==gcal.TOKEN:
            if self.revoked:self.fail(url,400,{"error":"invalid_grant","error_description":"Token has been expired or revoked."})
            self.n+=1;return Answer({"access_token":f"at{self.n}","expires_in":3600,"refresh_token":"1//refresh" if "authorization_code" in body else None})
        if url==gcal.REVOKE:return Answer({})
        path=urlsplit(url).path;eid=path.rsplit("/",1)[-1]
        if m=="GET":
            if "syncToken" in q:
                if self.expired:self.fail(url,410,{"error":{"code":410,"message":"Sync token is no longer valid, a full sync is required."}})
                items=self.changes
            else:items=self.items
            return Answer({"summary":"marco@gmail.com","items":items,"nextSyncToken":f"s{len(self.calls)}"})
        if m=="POST":
            e=json.loads(body)
            if e["id"] in self.events:self.fail(url,409,{"error":{"code":409,"message":"The requested identifier already exists."}})
            self.events[e["id"]]=e;return Answer(e)
        if m=="PATCH":
            if eid not in self.events:self.fail(url,404,{"error":{"code":404,"message":"Not Found"}})
            self.events[eid].update(json.loads(body));return Answer(self.events[eid])
        if m=="DELETE":
            if self.events.get(eid,{}).get("status")!="confirmed":self.fail(url,410,{"error":{"code":410,"message":"Resource has been deleted"}})
            self.events[eid]["status"]="cancelled";return Answer(None)
    def made(self,method):return [c for c in self.calls if c[0]==method and "calendar/v3" in c[1]]

@pytest.fixture
def google(monkeypatch):
    g=Google();monkeypatch.setattr("urllib.request.urlopen",g);return g

def connect(t,r,**st):
    with scope(t):
        t.google=encrypt(t,CLIENT,"google");t.save()
        r.refresh_from_db();gcal.keep(r,{"refresh":"1//refresh","access":"","expires":0,**st})

def test_agency_client_and_operator_connects_with_state_and_pkce(sample,google):
    t,o,s=sample;svc,marco,giulia=salon(t)
    owner,_=client_as(t);agency,_=client_as(o,staff=True)
    card=f"/t/{t.slug}/catalog/resources/{marco.pk}/"
    assert "Collega Google Calendar" not in owner.get(card).content.decode()  # nothing until the agency sets a client
    assert agency.post(f"/t/{t.slug}/messaggi/",{"action":"google","client_id":"nope","client_secret":"x"}).status_code==200
    assert agency.post(f"/t/{t.slug}/messaggi/",{"action":"google",**CLIENT}).status_code==302
    t.refresh_from_db();assert "GOCSPX" not in t.google and gcal.config(t)==CLIENT
    agency.post(f"/t/{t.slug}/messaggi/",{"action":"google","client_id":CLIENT["client_id"],"client_secret":""})
    t.refresh_from_db();assert gcal.config(t)["client_secret"]=="GOCSPX-secret"  # an empty secret keeps the saved one
    assert "Collega Google Calendar" in owner.get(card).content.decode()
    r=owner.post(card+"google/",{"action":"connect"})
    q=parse_qs(urlsplit(r["Location"]).query);saved=owner.session["gcal_oauth"]
    assert r["Location"].startswith(gcal.AUTH) and q["redirect_uri"]==["http://localhost:8000/oauth/google-calendar/"] and q["access_type"]==["offline"]
    assert q["code_challenge"]==[base64.urlsafe_b64encode(hashlib.sha256(saved["verifier"].encode()).digest()).rstrip(b"=").decode()] and q["code_challenge_method"]==["S256"]
    # a forged state connects nothing
    back=owner.get("/oauth/google-calendar/",{"state":"forged","code":"c"})
    assert back.status_code==302 and owner.get(back["Location"]).status_code==302 and not google.calls
    with scope(t):marco.refresh_from_db();assert not marco.google
    r=owner.post(card+"google/",{"action":"connect"});state=parse_qs(urlsplit(r["Location"]).query)["state"][0];verifier=owner.session["gcal_oauth"]["verifier"]
    back=owner.get("/oauth/google-calendar/",{"state":state,"code":"the-code"})
    assert back["Location"].startswith(f"/t/{t.slug}/google/")
    assert owner.get(back["Location"])["Location"]==card
    m,url,body=google.calls[-1];sent=parse_qs(body)
    assert url==gcal.TOKEN and sent["code_verifier"]==[verifier] and sent["code"]==["the-code"] and sent["redirect_uri"]==["http://localhost:8000/oauth/google-calendar/"]
    with scope(t):
        marco.refresh_from_db();assert "1//refresh" not in marco.google and gcal.state(marco)["refresh"]=="1//refresh" and not marco.google_error
    page=owner.get(card).content.decode();assert "Scollega" in page and "Collega Google Calendar" not in page
    # refused on Google's page: the error on the card
    r=owner.post(card+"google/",{"action":"connect"});state=parse_qs(urlsplit(r["Location"]).query)["state"][0]
    owner.get(owner.get("/oauth/google-calendar/",{"state":state,"error":"access_denied"})["Location"])
    assert "access_denied" in owner.get(card).content.decode()
    with scope(t):TimeBlock.objects.create(tenant=t,resource=marco,day=timezone.now().date(),start="08:00",end="09:00",reason="google")
    owner.post(card+"google/",{"action":"disconnect"})
    assert google.calls[-1][1]==gcal.REVOKE
    with scope(t):marco.refresh_from_db();assert not marco.google and not TimeBlock.objects.filter(reason="google").exists()
    assert owner.get(f"/t/{giulia.tenant.slug}/catalog/resources/{giulia.pk}/google/").status_code==403  # only POST
    connect(t,giulia)
    with scope(t):TimeBlock.objects.create(tenant=t,resource=giulia,day=timezone.now().date(),start="08:00",end="09:00",reason="google")
    agency.post(f"/t/{t.slug}/messaggi/",{"action":"google","google_off":"on"})
    t.refresh_from_db();assert not t.google
    with scope(t):
        giulia.refresh_from_db();assert not giulia.google and "spento" in giulia.google_error and not TimeBlock.objects.filter(reason="google").exists()

def test_busy_read_with_sync_token_and_full_again_on_410(sample,google):
    t,o,s=sample;svc,marco,giulia=salon(t)
    day=(timezone.now()+timedelta(days=3)).astimezone(ROME).date()
    at=lambda h,m=0:datetime.combine(day,datetime.min.time().replace(hour=h,minute=m),ROME)
    google.items=[{"id":"dent","status":"confirmed","start":{"dateTime":at(16).isoformat()},"end":{"dateTime":at(17).isoformat()}},
        {"id":"free","status":"confirmed","transparency":"transparent","start":{"dateTime":at(12).isoformat()},"end":{"dateTime":at(13).isoformat()}},
        {"id":"ours","status":"confirmed","extendedProperties":{"private":{"tdb_booking_id":"x"}},"start":{"dateTime":at(14).isoformat()},"end":{"dateTime":at(15).isoformat()}},
        {"id":"ferie","status":"confirmed","start":{"date":str(day+timedelta(days=1))},"end":{"date":str(day+timedelta(days=2))}}]
    connect(t,marco)
    now=timezone.now()
    assert gcal.refresh(now)==1 and gcal.refresh(now+timedelta(minutes=2))==0  # every 5 minutes, not more
    assert [c[2] for c in google.calls if c[1]==gcal.TOKEN][0].count("grant_type=refresh_token")==1
    first=google.made("GET")[0][1];assert "timeMin" in first and "singleEvents=true" in first and "syncToken" not in first
    with scope(t):
        assert TimeBlock.objects.filter(resource=marco,reason="google").count()==2
        assert blocked(t,at(16,10),at(16,20),resource=marco) and not blocked(t,at(12),at(12,30),resource=marco) and not blocked(t,at(14),at(14,30),resource=marco)
        assert blocked(t,at(23)+timedelta(hours=12),at(23)+timedelta(hours=13),resource=marco)  # all day
        assert not blocked(t,at(16,10),at(16,20),resource=giulia)
        marco.refresh_from_db();assert gcal.state(marco)["account"]=="marco@gmail.com" and not marco.google_error
    google.changes=[{"id":"dent","status":"cancelled"}]
    assert gcal.refresh(now+timedelta(minutes=5))==1
    last=google.made("GET")[-1][1];assert "syncToken=s" in last and "timeMin" not in last
    with scope(t):assert TimeBlock.objects.filter(resource=marco,reason="google").count()==1 and not blocked(t,at(16,10),at(16,20),resource=marco)
    google.expired=True;google.items=google.items[:1]
    assert gcal.refresh(now+timedelta(minutes=10))==1
    assert "syncToken" in google.made("GET")[-2][1] and "timeMin" in google.made("GET")[-1][1]  # 410: everything again
    with scope(t):assert blocked(t,at(16,10),at(16,20),resource=marco) and TimeBlock.objects.filter(resource=marco,reason="google").count()==1
    assert sum(c[1]==gcal.TOKEN for c in google.calls)==1  # the access token lasts an hour
    google.revoked=True
    with scope(t):marco.refresh_from_db();st=gcal.state(marco);st["expires"]=0;gcal.keep(marco,st)
    assert gcal.refresh(now+timedelta(minutes=15))==0
    with scope(t):
        marco.refresh_from_db();assert not marco.google and "collegalo di nuovo" in marco.google_error
        assert not TimeBlock.objects.filter(resource=marco,reason="google").exists()

def test_bookings_written_idempotent_moved_and_cancelled(sample,google):
    t,o,s=sample;svc,marco,giulia=salon(t);b=booked(t,svc,marco)
    connect(t,marco);now=timezone.now()
    gcal.refresh(now)
    eid=gcal.event_id(t,b.pk,marco);e=google.events[eid]
    assert e["summary"]=="Taglio" and "Mario" not in json.dumps(e) and e["extendedProperties"]["private"]["tdb_booking_id"]==str(b.pk)
    assert "sendUpdates=none" in google.made("POST")[0][1] and len(google.made("POST"))==1
    gcal.refresh(now+timedelta(minutes=5));assert len(google.made("POST"))==1 and not google.made("PATCH")  # nothing changed, nothing sent
    with scope(t):
        b.refresh_from_db();b.start+=timedelta(hours=1);b.end+=timedelta(hours=1);b.save()
        Resource.objects.filter(pk=marco.pk).update(feed_names=True)
    gcal.refresh(now+timedelta(minutes=10))
    assert len(google.made("PATCH"))==1 and google.events[eid]["start"]["dateTime"]==b.start.isoformat() and google.events[eid]["summary"]=="Taglio · Mario Rossi"
    # the state lost (a retry after a timeout): the insert answers 409 and the patch brings it up to date
    with scope(t):marco.refresh_from_db();st=gcal.state(marco);st["written"]={};gcal.keep(marco,st)
    gcal.refresh(now+timedelta(minutes=15))
    assert len(google.made("POST"))==2 and len(google.made("PATCH"))==2 and len(google.events)==1
    with scope(t):b.refresh_from_db();b.status="cancelled";b.save()
    gcal.refresh(now+timedelta(minutes=20))
    assert google.events[eid]["status"]=="cancelled" and len(google.made("DELETE"))==1
    with scope(t):marco.refresh_from_db();assert gcal.state(marco)["written"]=={} and not marco.google_error
    # deleted in Google already: 410 is done, not an error
    with scope(t):st=gcal.state(marco);st["written"]={str(b.pk):"x"};gcal.keep(marco,st)
    gcal.refresh(now+timedelta(minutes=25))
    with scope(t):marco.refresh_from_db();assert gcal.state(marco)["written"]=={} and not marco.google_error
    # Google down half way: the error on the card, the worker goes on
    google.events.clear()
    with scope(t):b.status="confirmed";b.save()
    import urllib.request
    urllib.request.urlopen=lambda req,timeout=None:(_ for _ in ()).throw(OSError("timed out"))
    assert gcal.refresh(now+timedelta(minutes=30))==0
    with scope(t):marco.refresh_from_db();assert "non ha risposto" in marco.google_error and gcal.state(marco)["refresh"]

def test_redirect_on_this_pc_goes_to_loopback(sample,settings):
    from django.test import RequestFactory
    t,o,s=sample;settings.ALLOWED_HOSTS=["*"]
    for host,secure,want in (("127.0.0.1:8443",True,"https://127.0.0.1:8443"),("[::1]:8000",False,"http://[::1]:8000"),("salone.example.com",True,"http://localhost:8000")):
        rq=RequestFactory().get("/",HTTP_HOST=host,secure=secure);rq.tenant=t
        assert gcal.redirect_uri(rq)==want+"/oauth/google-calendar/"
