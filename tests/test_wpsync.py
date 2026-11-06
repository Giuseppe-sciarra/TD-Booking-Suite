"""2.17.0: il sito WordPress collegato al PC anche spento (core/wpsync.py)."""
import json,time
from datetime import datetime,time as clock
from zoneinfo import ZoneInfo
import pytest
from django.utils import timezone
from core import wpsync
from core.booking import create_booking
from core.crypto import decrypt
from core.models import *
from core.tenancy import scope
from tests.test_core import sample,payload,client_as
from tests.test_phase2 import staff_client
from tests.test_salon import shop,day_of

pytestmark=pytest.mark.django_db
SECRET="s"*43

class Site:
    """The plugin's /wp-json/tdbs/v1/sync: the requests it got, and the bookings it answers with, signed."""
    def __init__(self,bookings=(),secret=SECRET):self.calls=[];self.bookings=list(bookings);self.secret=secret
    def __call__(self,req,timeout=None):
        assert req.full_url=="https://www.salone.example/wp-json/tdbs/v1/sync"
        assert wpsync.verify(SECRET,req.headers["X-tdbs-time"],req.data,req.headers["X-tdbs-signature"])
        self.calls.append(json.loads(req.data))
        body=json.dumps({"ok":True,"bookings":self.bookings});ts=str(int(time.time()))
        raw=json.dumps({"body":body,"time":ts,"sig":wpsync.sign(self.secret,ts,body.encode())}).encode()
        class R:
            def __enter__(s):return s
            def __exit__(s,*a):return False
            def read(s,n=None):return raw
        return R()
def linked_site(t,site,monkeypatch):
    wpsync.store(t,{"url":"https://www.salone.example","secret":SECRET,"days":5,"on":True})
    monkeypatch.setattr(wpsync.urllib.request,"urlopen",site)
def site_booking(s,r,hour,id="a"*32,**kw):
    return {"id":id,"service":str(s.pk),"option":"","resource":str(r.pk),"any":False,"date":day_of(s).isoformat(),"time":f"{hour:02d}:00",
        "first_name":"Lucia","last_name":"Bianchi","email":"lucia@example.invalid","phone":"333 765 4321","notes":"","marketing":False,"notices":True,**kw}

def test_signature_and_replay_window():
    body=b'{"ack":[]}';ts=str(int(time.time()));sig=wpsync.sign(SECRET,ts,body)
    assert wpsync.verify(SECRET,ts,body,sig)
    assert not wpsync.verify(SECRET,ts,body+b" ",sig) and not wpsync.verify("x"*43,ts,body,sig) and not wpsync.verify(SECRET,"nope",body,sig)
    old=str(int(time.time())-wpsync.WINDOW-5)
    assert not wpsync.verify(SECRET,old,body,wpsync.sign(SECRET,old,body))  # an old request replayed

def test_snapshot_has_the_free_times_of_each_operator(sample):
    t,o,s=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        create_booking(t,{**payload(taglio,hour=10),"resource":str(anna.pk)})
        snap=wpsync.snapshot(t,5)
    names=[x["name"] for x in snap["services"]]
    assert names==["Colore","Taglio"]  # the places service is not on the site
    cut=next(x for x in snap["services"] if x["name"]=="Taglio");day=day_of(taglio).isoformat()
    assert "10:00-10:30" not in cut["slots"][""][str(anna.pk)][day] and "10:00-10:30" in cut["slots"][""][str(bruno.pk)][day]
    assert snap["texts"]["privacy"]==t.privacy_text and "Lucia" not in json.dumps(snap)

def test_site_bookings_are_created_acked_and_once(sample,monkeypatch):
    t,o,s=sample
    with scope(t):loc,anna,bruno,taglio,colore=shop(t)
    site=Site([site_booking(taglio,anna,11)]);linked_site(t,site,monkeypatch)
    assert wpsync.sync(t)==1
    with scope(t):b=Booking.objects.get(request_key="wp-"+"a"*32)
    assert (b.origin,b.status,b.resource_id,b.data["phone"])==("wordpress","confirmed",anna.pk,"+393337654321")
    assert t.wp_state["ack"]==["a"*32] and t.wp_state["new"]==1 and not t.wp_state["error"]
    assert wpsync.sync(t)==0  # answered again before the ack arrived: not twice
    assert site.calls[1]["ack"]==["a"*32] and site.calls[0]["snapshot"]["name"]==t.name and "snapshot" in site.calls[1]  # a new booking: the times again
    wpsync.sync(t);assert "snapshot" not in site.calls[2]  # nothing changed: only the ack and the bookings
    with scope(t):assert Booking.objects.filter(origin="wordpress").count()==1

def test_time_taken_at_the_desk_becomes_a_request_with_email(sample,monkeypatch):
    t,o,s=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        create_booking(t,{**payload(taglio,hour=11),"resource":str(anna.pk)})
        sent=Delivery.objects.count()
    linked_site(t,Site([site_booking(taglio,anna,11)]),monkeypatch)
    assert wpsync.sync(t)==1
    with scope(t):
        b=Booking.objects.get(origin="wordpress")
        assert b.status=="pending" and b.resource_id==anna.pk and "forced" not in b.snapshot
        assert Delivery.objects.count()>sent  # the client's email "richiesta ricevuta" from the program

def test_wrong_signature_is_an_error_and_nothing_is_created(sample,monkeypatch):
    t,o,s=sample
    with scope(t):loc,anna,bruno,taglio,colore=shop(t)
    linked_site(t,Site([site_booking(taglio,anna,11)],secret="x"*43),monkeypatch)
    assert wpsync.sync(t)==0 and "firma" in t.wp_state["error"]
    with scope(t):assert not Booking.objects.filter(origin="wordpress").exists()

def test_settings_are_for_the_agency_only(sample):
    t,o,s=sample
    owner,_=client_as(t)
    assert owner.post(f"/t/{t.slug}/setup/apply/",{"action":"wps_secret"}).status_code==403
    assert "wordpress-pc" not in owner.get(f"/t/{t.slug}/setup/").content.decode()
    c,_=staff_client()
    page=c.post(f"/t/{t.slug}/setup/apply/",{"action":"wps_secret"},follow=True).content.decode()
    t.refresh_from_db();secret=wpsync.conf(t)["secret"]
    assert secret in page and secret not in c.get(f"/t/{t.slug}/setup/").content.decode()  # shown once
    assert secret not in t.wp_sync  # sealed
    c.post(f"/t/{t.slug}/setup/apply/",{"action":"wps_save","url":"https://www.salone.example/","days":"20","on":"1"})
    t.refresh_from_db();assert {k:v for k,v in wpsync.conf(t).items() if k!="secret"}=={"url":"https://www.salone.example","days":20,"on":True,"mails":False}
    c.post(f"/t/{t.slug}/setup/apply/",{"action":"wps_save","url":"http://x.example/wp","days":"20"})
    t.refresh_from_db();assert wpsync.conf(t)["url"]=="https://www.salone.example"

def test_banner_on_dashboard_and_agenda(sample):
    t,o,s=sample
    owner,_=client_as(t)
    assert "wpsync-bar" not in owner.get(f"/t/{t.slug}/").content.decode()
    wpsync.store(t,{"url":"https://www.salone.example","secret":SECRET,"days":5,"on":True})
    wpsync.state(t,running=timezone.now().isoformat())
    assert "Sincronizzazione con il sito in corso" in owner.get(f"/t/{t.slug}/").content.decode()
    with scope(t):b,_=create_booking(t,payload(s));shop(t)
    wpsync.state(t,running="",at=timezone.now().isoformat(),new=1,ids=[str(b.pk)],error="")
    page=owner.get(f"/t/{t.slug}/bookings/").content.decode()  # the agenda of the day
    assert 'data-refresh="agenda"' in page and "Sincronizzato con il sito alle" in page and f"/t/{t.slug}/bookings/{b.pk}/" in page and "1 prenotazione nuova" in page
    wpsync.state(t,error="sito non raggiungibile (timeout)")
    assert "non riuscita" in owner.get(f"/t/{t.slug}/").content.decode()

def test_plugin_zip_in_every_edition_and_pc_mode_in_it(sample,settings):
    import io,zipfile
    from pathlib import Path
    t,o,s=sample;settings.EDITION="private"
    c,_=staff_client();owner,_=client_as(t)
    r=c.get("/platform/plugin/download/")
    names=zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert r.status_code==200 and "td-booking-suite/pc.js" in names and owner.get("/platform/plugin/download/").status_code==403
    assert c.get("/platform/plugin/").status_code==404  # the agency's page stays the Cloud's
    php=(Path(__file__).resolve().parents[1]/"wordpress/td-booking-suite/td-booking-suite.php").read_text()
    for needle in ("const TDBS_VERSION='","Version: ","'/sync'","hash_equals(tdbs_pc_sign($ts,$body),$sig)","abs(time()-(int)$ts)>300","aes-256-gcm","wp_verify_nonce","tdbs_pc_throttle()","GET_LOCK","tdbs_pc_on()) return tdbs_pc_shortcode",
        # 2.17.1: the agenda password set by the site's admin only, hashed; HttpOnly session, lockout, never cached nor indexed
        "tdbs_admin_check('tdbs_pc_save')","wp_hash_password($password)","wp_check_password(","'httponly'=>true","$fails>=5","DONOTCACHEPAGE","'noindex'=>true","add_shortcode('tdbs_agenda'"):
        assert needle in php

# 2.17.1: the private agenda on the site, sealed with the shared secret as the plugin seals its own data (tdbs_pc_open)
def opened(sealed,secret=SECRET):
    import base64
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw=base64.b64decode(sealed);key=HKDF(hashes.SHA256(),32,None,b"tdbs-wp-data").derive(secret.encode())  # PHP hash_hkdf, empty salt
    return json.loads(AESGCM(key).decrypt(raw[:12],raw[28:]+raw[12:28],None))

def test_agenda_goes_sealed_with_the_snapshot(sample,monkeypatch):
    from cryptography.exceptions import InvalidTag
    t,o,s=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        create_booking(t,{**payload(taglio,hour=10),"resource":str(anna.pk)})
        b,_=create_booking(t,{**payload(taglio,hour=11,first_name="Gino"),"resource":str(bruno.pk)});Booking.objects.filter(pk=b.pk).update(status="pending")
        b,_=create_booking(t,{**payload(taglio,hour=12,first_name="Ugo"),"resource":str(bruno.pk)});Booking.objects.filter(pk=b.pk).update(status="cancelled")
    site=Site();linked_site(t,site,monkeypatch);wpsync.sync(t)
    sealed=site.calls[0]["agenda"];assert "Mario" not in sealed and "Prodotto" not in json.dumps(site.calls[0])
    a=opened(sealed);rows={x["client"]:x for x in a["bookings"]}
    assert set(rows)=={"Mario Rossi","Gino Rossi"}  # the cancelled one stays out
    assert rows["Mario Rossi"]["operator"]=="Anna" and rows["Mario Rossi"]["notes"]=="Prodotto delicato" and rows["Mario Rossi"]["phone"]
    assert rows["Gino Rossi"]["pending"] and rows["Gino Rossi"]["status"]=="In attesa di conferma" and {"Anna","Bruno"}<={x["name"] for x in a["operators"]}
    with pytest.raises(InvalidTag):opened(sealed,"x"*43)
    wpsync.sync(t);assert "agenda" not in site.calls[1]  # nothing changed: it goes again with the free times

# 2.19.0: Nuovo appuntamento in the site's agenda: the staff's, a booking of the desk with its own origin
def mails(t,to="lucia@example.invalid"):
    with scope(t):return [m["subject"].split(" · ")[0] for m in (decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in Delivery.objects.filter(channel="email").order_by("created")) if m.get("to")==to]

def test_agenda_appointment_is_confirmed_with_the_desk_email(sample,monkeypatch):
    t,o,s=sample
    with scope(t):loc,anna,bruno,taglio,colore=shop(t)
    linked_site(t,Site([site_booking(taglio,anna,11,source="agenda",email="",notes="Chiamata"),site_booking(taglio,bruno,12,id="b"*32,source="agenda")]),monkeypatch)
    assert wpsync.sync(t)==2 and not t.wp_state["error"]
    with scope(t):
        b=Booking.objects.get(request_key="wp-"+"a"*32)
        assert (b.origin,b.get_origin_display(),b.status,b.resource_id,b.data["phone"])==("agenda","Agenda del sito","confirmed",anna.pk,"+393337654321")
        assert b.contact and b.contact.data["phone"]=="+393337654321"  # the card, as at the desk: by phone, the email can be missing
        assert Audit.objects.filter(action="booking.created",target=str(b.pk),detail__origin="agenda").exists()
        assert Booking.objects.get(request_key="wp-"+"b"*32).status=="confirmed"
    assert mails(t)==["Prenotazione confermata"]  # the desk's email, never "Richiesta ricevuta"

def test_agenda_appointment_not_free_now_goes_to_the_requests(sample,monkeypatch):
    t,o,s=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        create_booking(t,{**payload(taglio,hour=11),"resource":str(anna.pk)})  # the desk took it meanwhile
    linked_site(t,Site([site_booking(taglio,anna,11,source="agenda"),site_booking(taglio,bruno,11,id="b"*32,source="agenda",free=True,time="11:10",email="ugo@example.invalid")]),monkeypatch)
    assert wpsync.sync(t)==2
    with scope(t):
        taken=Booking.objects.get(request_key="wp-"+"a"*32)
        assert (taken.origin,taken.status,taken.resource_id)==("agenda","pending",anna.pk)  # in Richieste da confermare
        other=Booking.objects.get(request_key="wp-"+"b"*32)  # Altro orario, off the grid, Bruno free then
        assert (other.status,other.resource_id,other.start.astimezone(ZoneInfo(t.timezone)).strftime("%H:%M"))==("confirmed",bruno.pk,"11:10")
    assert mails(t)==[] and mails(t,"ugo@example.invalid")==["Prenotazione confermata"]  # no "Richiesta ricevuta" for the pending one
