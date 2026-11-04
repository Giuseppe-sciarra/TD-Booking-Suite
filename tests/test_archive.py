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
    """The plugin's PC routes with the rules of tdbs_archive_push / _pull: the last number of a record wins, a deletion
    stays as an empty row; pages of two records to go through the paging."""
    def __init__(self):self.rows={};self.ops=[];self.down=False
    def __call__(self,req,timeout=None):
        path=urlsplit(req.full_url).path
        assert req.get_method()=="POST" and wpsync.verify(SECRET,req.headers["X-tdbs-time"],req.data,req.headers["X-tdbs-signature"])
        p=json.loads(req.data)
        if path=="/wp-json/tdbs/v1/sync":inner={"ok":True,"bookings":[]}
        elif path=="/wp-json/tdbs/v1/archive/push":
            assert len(req.data)<=512*1024
            if self.down:raise urllib.error.URLError("timeout")
            for op in p["ops"]:
                self.ops.append(op);old=self.rows.get((op["kind"],op["ref"]))
                if not old or op["seq"]>old["seq"]:self.rows[(op["kind"],op["ref"])]=op
            inner={"ok":True,"acked":[op["seq"] for op in p["ops"]],"records":sum(1 for r in self.rows.values() if r["data"]),"size":1}
        else:
            assert path=="/wp-json/tdbs/v1/archive/pull"
            rows=sorted((r for (k,ref),r in self.rows.items() if k==p["kind"] and ref>p["after"] and r["data"]),key=lambda r:r["ref"])[:2]
            inner={"ok":True,"records":[{"ref":r["ref"],"data":r["data"]} for r in rows],"next":rows[-1]["ref"] if len(rows)==2 else ""}
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
    with scope(t):b,_=create_booking(t,payload(s))
    wpsync.store(t,{"url":"https://www.salone.example","secret":SECRET,"days":5,"on":True})
    form={"action":"wps_save","url":"https://www.salone.example","days":"5","on":"1","archive":"1"}
    owner,_=client_as(t)
    assert owner.post(f"/t/{t.slug}/setup/apply/",form).status_code==403
    t.refresh_from_db();assert not t.wp_archive and queued(t)==[]
    c,_=staff_client();c.post(f"/t/{t.slug}/setup/apply/",form)
    t.refresh_from_db();assert t.wp_archive and {(k,r) for k,r,q in queued(t)}=={("booking",str(b.pk)),("contact",str(b.contact_id))}
    assert "Ripristina dal sito" in c.get(f"/t/{t.slug}/setup/").content.decode()
    c.post(f"/t/{t.slug}/setup/apply/",{**form,"archive":""})
    t.refresh_from_db();assert not t.wp_archive and queued(t)==[]

def test_plugin_has_the_archive_and_only_two_pc_prefixes():
    root=Path(__file__).resolve().parents[1]
    php=(root/"wordpress/td-booking-suite/td-booking-suite.php").read_text()
    routes=dict(re.findall(r"register_rest_route\('tdbs/v1','([^']+)',array\('methods'=>'(\w+)'",php))
    # 2.19.0: /agenda/book is the browser's (the agenda's Nuovo appuntamento), outside the PC's two prefixes
    assert routes=={"/sync":"POST","/slots":"GET","/book":"POST","/agenda/book":"POST","/archive/push":"POST","/archive/pull":"POST"}
    for needle in ("TDBS_ARCHIVE_BODY=524288","ON DUPLICATE KEY UPDATE data=IF(VALUES(seq)>seq","tdbs_archive_bookings(","DELETE FROM '.tdbs_archive_table()","Per il firewall del sito","const TDBS_VERSION='2.19.0'"):
        assert needle in php
    for doc in ("templates/guide.html","wordpress/td-booking-suite/readme.txt"):
        assert "/wp-json/tdbs/v1/archive/" in (root/doc).read_text() and "512 KB" in (root/doc).read_text()
