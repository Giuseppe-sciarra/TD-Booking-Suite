"""2.16.0: Cassa e registratori telematici. RCH Print!F (rete e USB) ed Epson FP con la cassa finta, il ponte cassa,
un conto inviato una volta sola, l'esito incerto da verificare, la copia per email solo con il suo consenso."""
import importlib.util,json,secrets,threading
from datetime import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core import cassa
from core.booking import create_booking
from tests.test_core import client_as,payload

pytestmark=pytest.mark.django_db
spec=importlib.util.spec_from_file_location("ponte_cassa",Path(__file__).parent.parent/"scripts"/"ponte_cassa.py")
ponte=importlib.util.module_from_spec(spec);spec.loader.exec_module(ponte)
RCH_OK="<?xml version='1.0'?><Service><Request><errorCode>0</errorCode><printerError>0</printerError><paperEnd>0</paperEnd><coverOpen>0</coverOpen><lastCmd>2</lastCmd><busy>0</busy></Request></Service>"
RCH_BUSY=RCH_OK.replace("<busy>0</busy>","<busy>1</busy>")
EPSON_OK=('<?xml version="1.0"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body><response success="true" code="" status="2">'
    "<addInfo><elementList>lastCommand,printerStatus,fiscalReceiptNumber,zRepNumber</elementList><lastCommand>74</lastCommand><printerStatus>20010</printerStatus>"
    "<fiscalReceiptNumber>12</fiscalReceiptNumber><zRepNumber>345</zRepNumber></addInfo></response></soapenv:Body></soapenv:Envelope>")

@pytest.fixture
def shop():
    t=Tenant.objects.create(slug="salone",name="Salone Test",till_on=True)
    with scope(t):
        loc=Location.objects.create(tenant=t,name="Centro")
        svc=Service.objects.create(tenant=t,location=loc,name="Taglio donna",kind="capacity",capacity=5,max_party=1,lead_minutes=0,price=Decimal("25"))
        for d in range(7):Schedule.objects.create(tenant=t,weekday=d,start=time(9),end=time(18))
        loc.till={"device":"rch","connection":"net","address":"127.0.0.1:1","link":"bridge","serial":"72091137","departments":{str(svc.pk):{"dept":2,"vat":"22%"}},
            "default_dept":1,"payments":{"cash":1,"card":4,"other":2}};loc.save()
    return t,loc,svc

class Fake(BaseHTTPRequestHandler):
    answers=[];seen=[]
    def do_POST(self):
        Fake.seen.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
        body=Fake.answers.pop(0).encode();self.send_response(200);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*a):pass
@pytest.fixture
def register():
    server=ThreadingHTTPServer(("127.0.0.1",0),Fake);threading.Thread(target=server.serve_forever,daemon=True).start()
    Fake.answers,Fake.seen=[],[]
    yield server.server_address[1]
    server.shutdown()

def sell(c,loc,booking=None,**extra):
    data={"action":"sell","sede":str(loc.pk),"key":extra.pop("key",secrets.token_urlsafe(24)),"desc":["Taglio donna","Shampoo"],"qty":["2","1"],"price":["25,00","8"],
        "svc":[extra.pop("svc",""),""],"discount":"3","pay":"cash",**({"booking":str(booking.pk)} if booking else {}),**extra}
    return c.post("/t/salone/cassa/",data)

def test_rch_commands_and_answers(shop):
    t,loc,svc=shop
    with scope(t):
        s=Sale(tenant=t,location=loc)
        d={"lines":[{"desc":"Taglio (donna)","qty":2,"cents":2500,"dept":2},{"desc":"Shampoo","qty":1,"cents":800,"dept":1}],"discount":300,"payments":[["cash",5000],["card",500]],"cf":"RSSMRA80A01H501U","lottery":"AB12CD34"}
        cmds=cassa.rch(s,d,loc.till)
    # the discount spread on the pieces: the cents left on the dearest; parentheses out of the text
    assert cmds==["=K","=C1",'="/?C/(RSSMRA80A01H501U)','="/?L/$1/(AB12CD34)',"=R2/$2370/(Taglio donna)","=R2/$2371/(Taglio donna)","=R1/$759/(Shampoo)","=S","=T1/$5000","=T4/$500"]
    assert sum(int(c.split("$")[1].split("/")[0]) for c in cmds if c.startswith("=R"))==5500
    assert cassa.read("rch",200,RCH_OK)==("done",{})
    assert cassa.read("rch",200,RCH_BUSY)[0]=="busy"
    state,info=cassa.read("rch",200,RCH_OK.replace("<errorCode>0","<errorCode>12").replace("<paperEnd>0","<paperEnd>1"))
    assert state=="failed" and "carta finita" in info["error"]
    assert cassa.read("rch",500,"<html")[0]=="unknown"
    with pytest.raises(ValueError):cassa.lan("8.8.8.8")
    assert cassa.lan(" 192.168.1.50:80 ")=="192.168.1.50:80"

def test_epson_receipt_void_and_answer(shop):
    t,loc,svc=shop
    with scope(t):
        loc.till={**loc.till,"device":"epson","payments":{"cash":0,"card":2,"other":5},"serial":"99MEX012345"};loc.save()
        d={"lines":[{"desc":"Piega","qty":2,"cents":1250,"dept":1}],"discount":100,"payments":[["card",2400]],"cf":"","lottery":"AB12CD34"}
        xml=cassa.epson(Sale(tenant=t,location=loc,kind="sale"),d,loc.till)
        assert '<printRecItem operator="1" description="Piega" quantity="2" unitPrice="12,50" department="1"' in xml
        assert 'amount="1,00"' in xml and 'paymentType="2"' in xml and 'payment="24,00"' in xml and "printRecLotteryID" in xml and xml.index("beginFiscalReceipt")<xml.index("endFiscalReceipt")
        assert cassa.read("epson",200,EPSON_OK)==("done",{"number":"12","z":"345"})
        assert cassa.read("epson",200,EPSON_OK.replace('success="true" code=""','success="false" code="EPTR_REC_EMPTY"'))==("failed",{"error":"La cassa risponde: EPTR_REC_EMPTY (stato 2)."})
        o=Sale.objects.create(tenant=t,location=loc,key="k"*24,pii="",number="12",z="345",serial="99MEX012345",status="done",printed=timezone.now())
        assert "VOID 0345 0012 " in cassa.epson(Sale(tenant=t,location=loc,kind="void",voids=o),{},loc.till)

def test_direct_sale_once_busy_then_done_with_copy(shop,register,settings,django_capture_on_commit_callbacks,monkeypatch):
    t,loc,svc=shop;settings.TILL_DIRECT=True;monkeypatch.setattr(cassa.time,"sleep",lambda s:None)
    with scope(t):
        loc.till={**loc.till,"address":f"127.0.0.1:{register}","link":"direct"};loc.save()
        b,_=create_booking(t,payload(svc));Booking.objects.filter(pk=b.pk).update(status="arrived")
    c,_=client_as(t,role="reception")
    Fake.answers=[RCH_BUSY,RCH_OK]
    key=secrets.token_urlsafe(24)
    with django_capture_on_commit_callbacks(execute=True):r=sell(c,loc,b,key=key,svc=str(svc.pk),copy="on")
    assert r.status_code==302
    with django_capture_on_commit_callbacks(execute=True):sell(c,loc,b,key=key)  # pressed twice: sent once
    assert len(Fake.seen)==2 and Fake.seen[0]==Fake.seen[1] and "=R2/$" in Fake.seen[0]
    with scope(t):
        s=Sale.objects.get()
        assert s.status=="done" and s.total==Decimal("55.00") and s.serial=="72091137" and s.booking_id==b.pk and s.contact_id==b.contact_id and s.mailed
        assert Booking.objects.get(pk=b.pk).status=="completed"
        assert "mario@" not in s.pii and s.data["email"]=="mario@example.invalid"
        ev=ConsentEvent.objects.get(purpose="receipt_copy");assert ev.ref==str(s.pk) and ev.channel=="desk"
        assert Contact.objects.get(pk=b.contact_id).consent=="none"  # not the marketing one
        assert Delivery.objects.filter(contact_id=b.contact_id).count()>=1
    assert c.get(f"/t/salone/cassa/?copia={s.pk}").status_code==200

def test_unknown_is_checked_before_sending_again(shop,settings,django_capture_on_commit_callbacks,monkeypatch):
    t,loc,svc=shop;settings.TILL_DIRECT=True
    with scope(t):loc.till={**loc.till,"link":"direct"};loc.save()
    monkeypatch.setattr(cassa.ponte(),"deliver",lambda job,opener=None:{"error":"unknown"})
    c,_=client_as(t)
    with django_capture_on_commit_callbacks(execute=True):sell(c,loc)
    with scope(t):s=Sale.objects.get();assert s.status=="unknown"
    calls=[]
    monkeypatch.setattr(cassa.ponte(),"deliver",lambda job,opener=None:calls.append(job) or {"status":200,"body":RCH_OK})
    with django_capture_on_commit_callbacks(execute=True):c.post("/t/salone/cassa/",{"action":"printed","sale":str(s.pk),"sede":str(loc.pk),"number":"77"})
    with scope(t):s.refresh_from_db();assert s.status=="done" and s.number=="77" and not calls  # it came out: nothing sent again
    with django_capture_on_commit_callbacks(execute=True):c.post("/t/salone/cassa/",{"action":"retry","sale":str(s.pk),"sede":str(loc.pk)})
    assert not calls  # a document emitted is never sent again

def test_bridge_one_at_a_time_busy_and_late_answers(shop):
    t,loc,svc=shop
    with scope(t):
        loc.till_token=cassa.token_hash("tdcassa_secret");loc.save()
        a=cassa.enqueue(t,loc,total=Decimal(10),data={"lines":[{"desc":"X","qty":1,"cents":1000,"dept":1}],"discount":0,"payments":[["cash",1000]]})
        b=cassa.enqueue(t,loc,kind="probe")
    from django.test import Client
    bridge=Client(HTTP_AUTHORIZATION="Bearer tdcassa_secret")
    assert Client(HTTP_AUTHORIZATION="Bearer tdcassa_wrong").get("/book/salone/api/cassa/").status_code==403
    job=bridge.get("/book/salone/api/cassa/").json()["job"]
    assert job["id"]==str(a.pk) and job["url"]=="http://127.0.0.1:1/service.cgi" and "<cmd>=T1/$1000</cmd>" in job["body"]
    assert bridge.get("/book/salone/api/cassa/").json()["job"] is None  # the register takes one request at a time
    post=lambda **r:bridge.post("/book/salone/api/cassa/",json.dumps(r),content_type="application/json").json()
    assert post(id=job["id"],attempt=job["attempt"],status=200,body=RCH_BUSY)=={"status":"queued"}
    again=bridge.get("/book/salone/api/cassa/").json()["job"];assert again["id"]==str(a.pk)
    assert post(id=job["id"],attempt=job["attempt"],status=200,body=RCH_OK)=={"status":"sending"}  # an old attempt's answer is dropped
    assert post(id=again["id"],attempt=again["attempt"],status=200,body=RCH_OK)=={"status":"done"}
    nxt=bridge.get("/book/salone/api/cassa/").json()["job"];assert nxt["id"]==str(b.pk)
    assert post(id=nxt["id"],attempt=nxt["attempt"],error="unreachable")=={"status":"failed"}
    with scope(t):assert Location.objects.get(pk=loc.pk).till_seen

def test_bridge_script_network_and_usb(register):
    Fake.answers=[RCH_OK]
    assert ponte.deliver({"url":f"http://127.0.0.1:{register}/service.cgi","body":"<Service/>"})=={"status":200,"body":RCH_OK}
    assert ponte.deliver({"url":"http://8.8.8.8/service.cgi","body":""})["error"]=="refused"
    assert ponte.deliver({"url":"http://127.0.0.1:1/service.cgi","body":""})=={"error":"unreachable"}
    f=ponte.frame("=K","stx");assert f[:1]==b"\x02" and f[-1:]==b"\x03" and f[1:9]==b"01002N=K" and f[9:11]==f"{sum(b'01002N=K')%256:02X}".encode()
    class Port:
        def __init__(self,answers):self.answers=list(answers);self.out=[]
        def __call__(self,*a,**kw):return self
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def write(self,b):self.out.append(b)
        def read(self,n):return self.answers.pop(0) if self.answers else b""
    conf={"port":"COM3","baud":9600,"framing":"stx"}
    p=Port([b"\x06",b"\x15",b"\x06",b"\x06"])
    assert ponte.serial_send(conf,["=K","=R1/$100/(X)","=T1/$100"],p)=={"serial":"ok"} and len(p.out)==4  # the refused frame sent again
    assert ponte.serial_send(conf,["=K","=T1/$100"],Port([b"\x06"]))=={"error":"unknown"}  # silent after the first: it may have printed
    assert ponte.serial_send(conf,["=K"],Port([]))=={"error":"unreachable"}
    assert ponte.serial_send(conf,["=K"],Port([b"\x15"]*3))=={"serial":"nak","at":0}
    assert cassa.outcome("rch",{"serial":"ok"})==("done",{}) and cassa.outcome("rch",{"serial":"sent"})[0]=="unknown"

def test_who_closes_the_bill(shop):
    t,loc,svc=shop
    c,_=client_as(t,role="operator")
    assert c.get("/t/salone/cassa/").status_code==403
    Tenant.objects.filter(pk=t.pk).update(till_operators=True)
    r=c.get("/t/salone/cassa/");assert r.status_code==200 and b"Taglio donna" in r.content
    Tenant.objects.filter(pk=t.pk).update(till_on=False)
    assert client_as(t)[0].get("/t/salone/cassa/").status_code==403  # no register: no Cassa, not even for the owner

def test_settings_by_owner_when_allowed_and_receipts_on_booking(shop):
    t,loc,svc=shop
    c,_=client_as(t)
    assert c.get("/t/salone/registratore/").status_code==403
    Tenant.objects.filter(pk=t.pk).update(till_self=True)
    assert c.get("/t/salone/registratore/").status_code==200
    r=c.post("/t/salone/registratore/",{"location":str(loc.pk),"action":"save","device":"rch","connection":"usb","port":"com3","baud":"9600","framing":"stx","address":"","serial":"72091137",f"dept_{svc.pk}":"3",f"vat_{svc.pk}":"10%","default_dept":"1","pay_card":"5"})
    assert r.status_code==302
    with scope(t):
        conf=Location.objects.get(pk=loc.pk).till
        assert conf["connection"]=="usb" and conf["port"]=="COM3" and conf["link"]=="bridge" and conf["departments"][str(svc.pk)]=={"dept":3,"vat":"10%"} and conf["payments"]=={"cash":1,"card":5,"other":2}
    c.post("/t/salone/registratore/",{"location":str(loc.pk),"action":"save","device":"rch","connection":"net","address":"93.184.216.34"})
    with scope(t):assert Location.objects.get(pk=loc.pk).till["connection"]=="usb"  # a public address is refused, nothing changes
    r=c.post("/t/salone/registratore/",{"location":str(loc.pk),"action":"token"});assert b"tdcassa_" in r.content
    assert c.get("/t/salone/registratore/?ponte=1").status_code==200
    with scope(t):
        b,_=create_booking(t,payload(svc))
        Sale.objects.create(tenant=t,location=loc,booking=b,contact=b.contact,key="z"*24,pii="",total=Decimal("25"),status="done",number="9")
    r=c.get(f"/t/salone/bookings/{b.pk}/");assert b"Chiudi il conto" in r.content and b"n. 9" in r.content
    assert b"Scontrini" in c.get(f"/t/salone/contacts/{b.contact_id}/").content
    r=c.get(f"/t/salone/cassa/?prenotazione={b.pk}");assert b'value="25,00"' in r.content

def test_row_discount_in_percent_or_euro_on_rch_and_epson(shop):
    from django.http import QueryDict
    t,loc,svc=shop
    q=QueryDict(mutable=True)
    for desc,qty,price,off in (("Cucitura extension","1","430","") ,("Piega glam lunghi","1","21","100%"),("Color care","2","40","10,00")):
        q.appendlist("desc",desc);q.appendlist("qty",qty);q.appendlist("price",price);q.appendlist("svc","");q.appendlist("off",off)
    d,total=cassa.bill(q,loc.till)
    assert total==50000 and d["lines"][1]["off"]==2100 and d["lines"][1]["pct"]=="100" and d["lines"][2]["off"]==1000 and "off" not in d["lines"][0]
    with scope(t):
        s=Sale(tenant=t,location=loc)
        cmds=cassa.rch(s,d,loc.till)
        # the row's discount after its sale, split over the pieces; empty command: prices already discounted
        assert cmds[1]=="=C1" and cmds[2:8]==["=R1/$43000/(Cucitura extension)","=R1/$2100/(Piega glam lunghi)","=V-/$2100","=R1/$4000/(Color care)","=V-/$500","=R1/$4000/(Color care)"] and cmds[8]=="=V-/$500"
        assert [c for c in cassa.rch(s,d,{**loc.till,"rch_off":""}) if c.startswith("=R")][1:]==["=R1/$0/(Piega glam lunghi)","=R1/$3500/(Color care)","=R1/$3500/(Color care)"]
        xml=cassa.epson(Sale(tenant=t,location=loc,kind="sale"),d,{**loc.till,"payments":{"cash":0,"card":2,"other":5}})
        assert '<printRecItemAdjustment operator="1" adjustmentType="0" description="SCONTO 100%" amount="21,00" department="1"' in xml
    for bad in ("101%","50","abc%"):
        q.setlist("off",["",bad,""])
        with pytest.raises(ValueError):cassa.bill(q,loc.till)

def test_rch_closure_and_annullo_commands(shop):
    t,loc,svc=shop
    with scope(t):
        assert cassa.rch(Sale(tenant=t,location=loc,kind="close"),{},loc.till)==["=K","=C3","=C10","=C1"]
        assert cassa.rch(Sale(tenant=t,location=loc,kind="void"),{"z":"2026","number":"12","day":"2026-10-24"},loc.till)==["=K","=C1","=k/&241026/[2026/]0012"]
        assert cassa.rch(Sale(tenant=t,location=loc,kind="test"),{},loc.till)==["=K","=C1","=o",'="/(PROVA DI COLLEGAMENTO)','="/(TD BOOKING SUITE)','="/(DOCUMENTO NON FISCALE)',"=o"]
        assert cassa.rch(Sale(tenant=t,location=loc,kind="close"),{},{**loc.till,"rch_close":"=C2 =C10"})==["=K","=C2","=C10"]
        loc.till={**loc.till,"device":"epson","serial":"99MEX012345"}
        assert "<printZReport" in cassa.epson(Sale(tenant=t,location=loc,kind="close"),{},loc.till)
        assert "VOID 2026 0012 24102026 99MEX012345" in cassa.epson(Sale(tenant=t,location=loc,kind="void"),{"z":"2026","number":"12","day":"2026-10-24"},loc.till)

def test_close_and_annullo_by_hand_from_the_page(shop):
    from django.urls import reverse
    t,loc,svc=shop
    c,_=client_as(t,role="reception");url=reverse("till",args=[t.slug])
    assert c.post(url,{"sede":loc.pk,"action":"close"}).status_code==302
    assert c.post(url,{"sede":loc.pk,"action":"void","day":"2026-10-24","doc":"2026-0012"}).status_code==302
    c.post(url,{"sede":loc.pk,"action":"void","day":"2026-10-24","doc":"12"})  # no closure number: refused
    with scope(t):
        assert list(Sale.objects.order_by("created").values_list("kind",flat=True))==["close","void"]
        assert Sale.objects.get(kind="void").data=={"z":"2026","number":"0012","day":"2026-10-24"}

def test_services_imported_from_the_old_price_list_once(shop):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.urls import reverse
    t,loc,svc=shop
    with scope(t):op=Resource.objects.create(tenant=t,location=loc,name="Cristina")
    c,_=client_as(t,role="owner")
    csv="Servizio;Prezzo;Durata in minuti;Gruppo\nCOLOR CARE;34,00;30;Tecnico\nTaglio donna;25;30;\nPiega glam lunghi;21;;\n;;;\nBalayage;abc;50;\n".encode()
    for _ in range(2):
        r=c.post(reverse("services_import",args=[t.slug]),{"location":loc.pk,"staff":"on","file":SimpleUploadedFile("listino.csv",csv)})
        assert r.status_code==200
    with scope(t):
        made=Service.objects.filter(location=loc).exclude(pk=svc.pk).order_by("name")
        assert [(s.name,s.price,s.duration,s.kind) for s in made]==[("Color Care",Decimal("34.00"),30,"resource"),("Piega glam lunghi",Decimal("21.00"),30,"resource")]
        assert all(s.resource_links.filter(resource=op).exists() for s in made)
    assert "con errori: 1" in r.content.decode() or "già presenti" in r.content.decode()

def test_discover_finds_the_register_and_fills_the_form(shop,register,settings,monkeypatch):
    from django.urls import reverse
    t,loc,svc=shop
    Fake.answers=[RCH_OK]
    assert ponte.identify("127.0.0.1",register)=="rch" and Fake.seen==['<?xml version="1.0" encoding="UTF-8"?><Service></Service>']  # an empty request: nothing printed or cleared
    server=ThreadingHTTPServer(("127.0.0.1",0),BaseHTTPRequestHandler);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:assert ponte.identify("127.0.0.1",server.server_address[1]) is None and ponte.identify("127.0.0.1",1) is None  # another web server; nothing listening
    finally:server.shutdown()
    c,_=client_as(t,staff=True);url=reverse("till_settings",args=[t.slug])
    settings.TILL_DIRECT=False
    assert "ponte_cassa.py --cerca" in c.post(url,{"location":loc.pk,"action":"discover"},follow=True).content.decode()
    settings.TILL_DIRECT=True
    monkeypatch.setattr(cassa.ponte(),"discover",lambda:([{"device":"rch","address":"192.168.1.50"}],[{"port":"COM3","description":"USB Serial Device"}]))
    page=c.post(url,{"location":loc.pk,"action":"discover"}).content.decode()
    assert 'data-address="192.168.1.50"' in page and 'data-port="COM3"' in page and "RCH Print!F" in page
