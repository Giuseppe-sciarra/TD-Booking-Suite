"""2.18.0: il sito come archivio centrale (core/wpsync.py, plugin td-booking-suite)."""
import json,re,time,uuid,urllib.error
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from core import wpsync
from core.booking import create_booking,change_status
from core.crypto import encrypt
from core.models import *
from core.privacy import erase_contact,record
from core.tenancy import scope
from tests.test_core import sample,payload,client_as
from tests.test_phase2 import staff_client
from tests.test_salon import shop

pytestmark=pytest.mark.django_db
SECRET="s"*43

class Site:
    """The plugin's PC routes with the rules of tdbs_archive_push / _pull: 2.20.0 the higher v of a record wins, on a tie
    the higher number, an older one is acked and kept out; a deletion stays as an empty row with its v; pages of two
    records to go through the paging, the empty rows only with gone. offline: no call gets through."""
    def __init__(self):self.rows={};self.ops=[];self.down=False;self.offline=False;self.syncs=[]
    def __call__(self,req,timeout=None):
        path=urlsplit(req.full_url).path
        assert req.get_method()=="POST" and wpsync.verify(SECRET,req.headers["X-tdbs-time"],req.data,req.headers["X-tdbs-signature"])
        if self.offline:raise urllib.error.URLError("timeout")
        p=json.loads(req.data)
        if path=="/wp-json/tdbs/v1/sync":self.syncs.append(p);inner={"ok":True,"bookings":[]}
        elif path=="/wp-json/tdbs/v1/archive/push":
            assert len(req.data)<=512*1024
            if self.down:raise urllib.error.URLError("timeout")
            for op in p["ops"]:
                self.ops.append(op);old=self.rows.get((op["kind"],op["ref"]));v=op.get("v",0)
                if not old or v>old["v"] or v==old["v"] and op["seq"]>old["seq"]:self.rows[(op["kind"],op["ref"])]={**op,"v":v}
            inner={"ok":True,"acked":[op["seq"] for op in p["ops"]],"records":sum(1 for r in self.rows.values() if r["data"]),"size":1}
        else:
            assert path=="/wp-json/tdbs/v1/archive/pull"
            rows=sorted((r for (k,ref),r in self.rows.items() if k==p["kind"] and ref>p["after"] and (r["data"] or p.get("gone"))),key=lambda r:r["ref"])[:2]
            inner={"ok":True,"records":[{"ref":r["ref"],"v":r["v"],"data":r["data"]} for r in rows],"next":rows[-1]["ref"] if len(rows)==2 else ""}
        body=json.dumps(inner);ts=str(int(time.time()))
        raw=json.dumps({"body":body,"time":ts,"sig":wpsync.sign(SECRET,ts,body.encode())}).encode()
        class R:
            def __enter__(s):return s
            def __exit__(s,*a):return False
            def read(s,n=None):return raw
        return R()
    def get(self,kind,ref):
        r=self.rows.get((kind,str(ref)));return wpsync.unseal(SECRET,r["data"]) if r and r["data"] else None
def linked(t,site,monkeypatch,archive=True):
    wpsync.store(t,{"url":"https://www.salone.example","secret":SECRET,"days":5,"on":True})
    Tenant.objects.filter(pk=t.pk).update(wp_archive=archive);t.wp_archive=archive
    monkeypatch.setattr(wpsync.urllib.request,"urlopen",site)
def queued(t):
    with scope(t):return list(SiteOutbox.objects.order_by("seq").values_list("kind","ref","seq"))

def test_outbox_is_ordered_once_per_record_and_kept_until_acked(sample,monkeypatch):
    t,o,s=sample;site=Site();linked(t,site,monkeypatch)
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        b,_=create_booking(t,{**payload(taglio,hour=10),"resource":str(anna.pk)})
        first=dict((k,q) for k,r,q in queued(t))
        change_status(t,b,"cancelled")
    rows=queued(t);assert {k for k,r,q in rows}=={"booking","contact"} and len(rows)==2  # changed twice: one row, a new number
    assert dict((k,q) for k,r,q in rows)["booking"]>first["booking"]
    site.down=True;wpsync.sync(t)
    assert queued(t)==rows and "non raggiungibile" in t.wp_state["archive"]["error"]  # not acked: kept, again next time
    site.down=False;wpsync.sync(t)
    assert queued(t)==[] and [op["seq"] for op in site.ops]==sorted(op["seq"] for op in site.ops)
    got=site.get("booking",b.pk)
    assert got["status"]=="cancelled" and "view" not in got and got["data"]["first_name"]=="Mario" and "Mario" not in site.rows[("booking",str(b.pk))]["data"]
    wpsync.sync(t);assert len(site.ops)==2  # nothing changed, nothing sent

def test_erasure_deletes_on_the_site(sample,monkeypatch):
    t,o,s=sample;site=Site();linked(t,site,monkeypatch)
    with scope(t):
        b,_=create_booking(t,payload(s));c=b.contact;change_status(t,b,"cancelled")
        ev=record(t,c,"requested","public",None,"testo")
    wpsync.sync(t)
    assert site.get("contact",c.pk)["data"]["email"]=="mario@example.invalid" and site.get("consent",ev.pk)["contact_id"]==str(c.pk)
    with scope(t):erase_contact(t,Contact.objects.get(pk=c.pk),None)
    wpsync.sync(t)
    assert site.rows[("contact",str(c.pk))]["data"]=="" and site.rows[("consent",str(ev.pk))]["data"]==""
    assert site.get("booking",b.pk)["data"]["first_name"]=="Anonimo"

def rekeyed(site):
    """The archive of another install: the same records with ids this database has never seen."""
    ids={ref:str(uuid.uuid4()) for (k,ref) in site.rows}
    for key,r in list(site.rows.items()):
        text=json.dumps(site.get(*key)) if r["data"] else ""
        for old,new in ids.items():text=text.replace(old,new)
        site.rows[(key[0],ids[key[1]])]={**r,"ref":ids[key[1]],"data":wpsync.seal(SECRET,json.loads(text)) if text else ""};del site.rows[key]
    return ids

def test_restore_into_an_empty_business_with_the_client_record(sample,monkeypatch):
    t,o,s=sample;site=Site();linked(t,site,monkeypatch)
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        b,_=create_booking(t,{**payload(taglio,hour=10),"resource":str(anna.pk)})
        create_booking(t,{**payload(colore,hour=14),"resource":str(bruno.pk)})
        tr=Treatment(tenant=t,contact=b.contact,booking=b,date=date(2026,1,5));tr.pii=encrypt(t,{"service":"Taglio","details":"formula 7.1"},"treatment:"+str(tr.pk));tr.save()
        record(t,b.contact,"requested","public",None,"testo")
    wpsync.sync(t);ids=rekeyed(site)
    with scope(o):
        Location.objects.create(tenant=o,name="Centro");loc2,anna2,bruno2,taglio2,colore2=shop(o)
        Service.objects.filter(pk=colore2.pk).update(name="Colore e piega")
    linked(o,site,monkeypatch)
    r=wpsync.restore(o,None)
    assert (r["contacts"],r["bookings"],r["treatments"],r["consents"],r["lost"])==(1,1,1,1,1) and r["missing"]==["servizio Colore"]
    with scope(o):
        nb=Booking.objects.get();card=Contact.objects.get();x=Treatment.objects.get()
        assert (nb.pk,nb.resource_id,nb.service_id,nb.start,nb.contact_id)==(uuid.UUID(ids[str(b.pk)]),anna2.pk,taglio2.pk,b.start,card.pk)
        assert nb.data["notes"]=="Prodotto delicato" and card.data["email"]=="mario@example.invalid" and card.email_index!=b.contact.email_index
        assert (x.booking_id,x.contact_id,x.data["details"],x.date)==(nb.pk,card.pk,"formula 7.1",date(2026,1,5)) and ConsentEvent.objects.get().contact_id==card.pk
        assert not SiteOutbox.objects.exists()  # already on the site: not sent back
    with pytest.raises(ValueError):wpsync.restore(o,None)  # no longer empty
    again=wpsync.restore(o,None,confirm=True)
    assert again["contacts"]+again["bookings"]+again["treatments"]+again["consents"]==0 and again["skipped"]==4
    with scope(o):assert Booking.objects.count()==1 and Contact.objects.count()==1

def test_switch_is_for_the_agency_and_uploads_everything(sample,monkeypatch):
    t,o,s=sample
    def unreachable(req,timeout=None):raise urllib.error.URLError("timeout")
    monkeypatch.setattr(wpsync.urllib.request,"urlopen",unreachable)  # 2.20.0: the site does not answer: everything queued as before
    with scope(t):b,_=create_booking(t,payload(s))
    wpsync.store(t,{"url":"https://www.salone.example","secret":SECRET,"days":5,"on":True})
    form={"action":"wps_save","url":"https://www.salone.example","days":"5","on":"1","archive":"1"}
    owner,_=client_as(t)
    assert owner.post(f"/t/{t.slug}/setup/apply/",form).status_code==403
    t.refresh_from_db();assert not t.wp_archive and queued(t)==[]
    c,_=staff_client();c.post(f"/t/{t.slug}/setup/apply/",form)
    t.refresh_from_db();assert t.wp_archive and {(k,r) for k,r,q in queued(t)}=={("booking",str(b.pk)),("contact",str(b.contact_id))}
    page=c.get(f"/t/{t.slug}/settings/").content.decode()
    assert "Ripristina dal sito" in page and "Sincronizza con il sito" in page and 'value="wps_merge"' in page and "style=" not in page.split('id="wordpress-pc"')[1].split("</section>")[0]
    c.post(f"/t/{t.slug}/setup/apply/",{**form,"archive":""})
    t.refresh_from_db();assert not t.wp_archive and queued(t)==[]

def test_plugin_has_the_archive_and_only_two_pc_prefixes():
    root=Path(__file__).resolve().parents[1]
    php=(root/"wordpress/td-booking-suite/td-booking-suite.php").read_text()
    routes=dict(re.findall(r"register_rest_route\('tdbs/v1','([^']+)',array\('methods'=>'(\w+)'",php))
    # 2.19.0: /agenda/book is the browser's (the agenda's Nuovo appuntamento), outside the PC's two prefixes
    assert routes=={"/sync":"POST","/slots":"GET","/book":"POST","/agenda/book":"POST","/archive/push":"POST","/archive/pull":"POST"}
    for needle in ("TDBS_ARCHIVE_BODY=524288","tdbs_archive_bookings(","DELETE FROM '.tdbs_archive_table()","Per il firewall del sito","const TDBS_VERSION='2.22.0'"," * Version: 2.22.0","require_once __DIR__.'/mails.php'"):
        assert needle in php
    assert "Stable tag: 2.22.0" in (root/"wordpress/td-booking-suite/readme.txt").read_text()
    for doc in ("templates/guide.html","wordpress/td-booking-suite/readme.txt"):
        assert "/wp-json/tdbs/v1/archive/" in (root/doc).read_text() and "512 KB" in (root/doc).read_text()

# ---------- 2.20.0 the newest wins, between the PC and the site
def test_version_is_the_same_in_every_time_zone():
    from datetime import datetime,timezone as utc
    from zoneinfo import ZoneInfo
    at=datetime(2026,11,4,9,30,15,123456,tzinfo=utc.utc)
    v=lambda d:wpsync.ver(type("R",(),{"updated":d})())
    assert v(at)==v(at.astimezone(ZoneInfo("Asia/Tokyo")))==v(at.astimezone(ZoneInfo("America/Los_Angeles")))==1793784615123456
    assert wpsync.moment(v(at))==at

def test_pc_alone_then_archive_on_keeps_the_newest_on_both_sides(sample,monkeypatch):
    t,o,s=sample;site=Site();linked(t,site,monkeypatch)
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        b1,_=create_booking(t,{**payload(taglio,hour=10),"resource":str(anna.pk)})
        b2,_=create_booking(t,{**payload(taglio,hour=11),"resource":str(anna.pk)})
        b3,_=create_booking(t,{**payload(taglio,hour=12),"resource":str(anna.pk)})
    wpsync.sync(t);assert queued(t)==[] and site.get("booking",b3.pk)
    # the PC used alone: the archive off here, changes keep their moment anyway
    Tenant.objects.filter(pk=t.pk).update(wp_archive=False);t.wp_archive=False
    with scope(t):
        change_status(t,Booking.objects.get(pk=b2.pk),"cancelled");Booking.objects.filter(pk=b3.pk).delete()
        here2=Booking.objects.get(pk=b2.pk);assert here2.updated and wpsync.ver(here2)>site.rows[("booking",str(b2.pk))]["v"]
    assert queued(t)==[]
    # meanwhile on the site (another PC, the agenda): b1 changed after; b2 changed before the change made here
    later=wpsync.ver(here2)+5_000_000
    for b,notes,v in ((b1,"cambiata dopo, dal sito",later),(b2,"vecchia, dal sito",site.rows[("booking",str(b2.pk))]["v"])):
        rec=site.get("booking",b.pk);rec["data"]={**rec["data"],"notes":notes}
        site.rows[("booking",str(b.pk))].update(data=wpsync.seal(SECRET,rec),v=v)
    n=len(site.ops);wpsync.save(t,{"url":"https://www.salone.example","days":"5","on":"1","archive":"1"},None)  # the archive on: merge
    with scope(t):
        x1,x2=Booking.objects.get(pk=b1.pk),Booking.objects.get(pk=b2.pk)
        assert x1.data["notes"]=="cambiata dopo, dal sito" and wpsync.ver(x1)==later  # the site's was newer: here now
        assert x2.status=="cancelled" and x2.data["notes"]=="Prodotto delicato"  # the older one of the site did not overwrite it
        assert Booking.objects.filter(pk=b3.pk).exists()  # only on the site: back here
    assert site.get("booking",b2.pk)["status"]=="cancelled" and site.rows[("booking",str(b2.pk))]["v"]==wpsync.ver(x2)  # and went to the site
    assert site.rows[("booking",str(b1.pk))]["v"]==later and queued(t)==[]
    assert {op["ref"] for op in site.ops[n:]}=={str(b2.pk)}  # only what was newer here went up, nothing bounced back
    c,_=staff_client()
    r=c.post(f"/t/{t.slug}/setup/apply/",{"action":"wps_merge"},follow=True).content.decode()
    assert "Presi dal sito: 0 nuovi qui, 0 aggiornati" in r and "Mandati al sito: 0" in r  # lined up: nothing more to do
    # an older copy arriving late on the site is acked and kept out
    old=dict(site.rows[("booking",str(b1.pk))]);wpsync.call(wpsync.conf(t),"/archive/push",{"ops":[{**old,"seq":old["seq"]+1,"v":old["v"]-1,"data":""}]})
    assert site.rows[("booking",str(b1.pk))]["data"]==old["data"]

def test_site_down_bookings_and_free_times_go_at_the_next_sync(sample,monkeypatch):
    t,o,s=sample;site=Site();linked(t,site,monkeypatch)
    with scope(t):loc,anna,bruno,taglio,colore=shop(t)
    wpsync.sync(t);assert "snapshot" in site.syncs[-1]
    site.offline=True
    with scope(t):b,_=create_booking(t,{**payload(taglio,hour=11),"resource":str(anna.pk)})
    wpsync.sync(t);wpsync.sync(t)
    assert t.wp_state["down"] and "non raggiungibile" in t.wp_state["error"] and ("booking",str(b.pk)) in {(k,r) for k,r,q in queued(t)}
    site.offline=False;wpsync.sync(t)
    day=b.start.astimezone(wpsync.ZoneInfo(t.timezone)).date().isoformat()
    cut=next(x for x in site.syncs[-1]["snapshot"]["services"] if x["name"]=="Taglio")
    assert "11:00-11:30" not in cut["slots"][""][str(anna.pk)][day] and "agenda" in site.syncs[-1]  # the free times without it
    assert site.get("booking",b.pk)["status"]=="confirmed" and queued(t)==[] and not t.wp_state["down"]
    wpsync.sync(t);assert "snapshot" not in site.syncs[-1]
    site.offline=True;wpsync.sync(t);site.offline=False;wpsync.sync(t)
    assert "snapshot" in site.syncs[-1]  # right after the site is back, the free times again, also with nothing new

def test_plugin_keeps_the_newest_version():
    import shutil,subprocess
    root=Path(__file__).resolve().parents[1];path=root/"wordpress/td-booking-suite/td-booking-suite.php";php=path.read_text()
    for needle in ("$win='(VALUES(v)>v OR (VALUES(v)=v AND VALUES(seq)>seq))'","data=IF($win,VALUES(data),data),day=IF($win,VALUES(day),day),seq=IF($win,VALUES(seq),seq),v=GREATEST(v,VALUES(v))",
            "$v=$op['v']??0","!is_int($v) || $v<0","v bigint(20) unsigned NOT NULL DEFAULT 0","get_option('tdbs_archive_db')!=='2'","update_option('tdbs_archive_db','2')",
            "'v'=>(int)$r['v']","$gone=!empty($data['gone'])","($gone?'':\" AND data<>''\")","if ($done!==false) $acked[]=$seq;"):
        assert needle in php,needle
    if shutil.which("php"):assert subprocess.run(["php","-l",str(path)],capture_output=True).returncode==0
