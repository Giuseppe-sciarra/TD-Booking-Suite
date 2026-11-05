"""2.16.0: the fiscal register (registratore telematico) of a location. The desk closes the bill, the register prints and
transmits the documento commerciale, TD keeps what it answered (on the booking and on the client card) and, only when
the client asks, emails him a copy. TD never emits a fiscal document by itself.

Two protocols: RCH (Print!F: XML commands POSTed to /service.cgi on the shop's network, one request at a time, "busy"
sent again; or the same commands through USB, a virtual COM port) and Epson (FP-81II, FP-90III RT: Fiscal ePOS-Print
POSTed to /cgi-bin/fpmate.cgi, network only). The Windows edition, on the shop's PC, calls the register itself
(settings.TILL_DIRECT); elsewhere the bridge (scripts/ponte_cassa.py) on a PC of the shop asks for the waiting requests
with the location's token, sends them and gives back the answer, read here. Both send with the bridge's own deliver.
A request is marked "sending" and committed before it leaves: an answer lost on the way leaves it "unknown", and the
operator checks the register before sending it again, so a bill is never printed twice."""
import importlib.util,ipaddress,json,re,secrets,threading,time,unicodedata
from datetime import datetime,timedelta,date
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape,quoteattr
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse,JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .clients import receipt_code
from .crypto import encrypt,token_hash
from .models import Booking,ConsentEvent,Contact,Location,Option,Resource,Sale,Service,Tenant,audit
from .permissions import can,require,visible_bookings
from .prices import euro
from .tenancy import scope

DEVICES=[("","Nessuno"),("rch","RCH Print!F"),("epson","Epson FP-81II / FP-90III RT")]
PAYMENTS=[("cash","Contanti"),("card","Carta o bancomat"),("other","Altro")]
# shortcut: RCH 1 contanti, 4 carta, 2 non riscosso as Spiagge.it's setup asks; Epson paymentType 0 contanti, 2 carta,
# 5 non riscosso, always index 1: change them in Registratore di cassa to what the register has
DEFAULT_PAY={"rch":{"cash":1,"card":4,"other":2},"epson":{"cash":0,"card":2,"other":5}}
COPY_TEXT="Il cliente vuole la copia dello scontrino per email."
TIMEOUT=20
STALE=timedelta(seconds=120)  # longer than a direct send (busy retries, one timeout): past it nobody is still waiting
CF=re.compile(r"[A-Z0-9]{16}|[0-9]{11}")  # codice fiscale, or the partita IVA
LOTTERY=re.compile(r"[A-Z0-9]{8}")

# ---------- the protocols
def lan(address):
    """192.168.1.50 or 192.168.1.50:8080: an address of the shop's own network, never a name or a public address."""
    host,_,port=(address or "").strip().partition(":")
    try:
        ip=ipaddress.IPv4Address(host)
        if port and not 0<int(port)<65536:raise ValueError
    except ValueError:raise ValueError("Scrivi l'indirizzo IP della cassa nella rete del negozio, per esempio 192.168.1.50.")
    if not ip.is_private or ip.is_unspecified:raise ValueError("L'indirizzo deve essere della rete del negozio (192.168…, 10…, 172.16…).")
    return f"{ip}:{int(port)}" if port else str(ip)
PORT=re.compile(r"COM[0-9]{1,3}|/dev/tty[A-Za-z0-9]{1,20}")
BAUDS=(9600,19200,38400,57600,115200)
def url(conf):
    return f"http://{conf['address']}/service.cgi" if conf["device"]=="rch" else f"http://{conf['address']}/cgi-bin/fpmate.cgi?devid=local_printer&timeout=10000"
def text(value,n=32):
    """Printable on the register: no accents, no control characters, no parentheses (they close RCH's text)."""
    value=unicodedata.normalize("NFKD",str(value or "")).encode("ascii","ignore").decode()
    return " ".join(re.sub(r"[()\x00-\x1f\x7f]","",value).split())[:n]
def money(c):return f"{c//100},{c%100:02d}"
# shortcut: RCH's commands for the row discount, the annullo and the closure are guessed (its manual needs an RCH login,
# the annullo from the forum, the closure from the keys Z + 10): the agency can change them in Registratore di cassa
RCH_OFF="=V-/${importo}"
RCH_CMDS={"rch_off":(RCH_OFF,"{importo}"),"rch_void":("=k/&{data}/[{z}/]{numero}","{numero}"),"rch_close":("=C3 =C10 =C1","")}
def rch_cmd(conf,key):return conf.get(key,RCH_CMDS[key][0])
def units(d,line=True):
    """RCH: one sale line a piece, with the discount of its row split over its pieces (line=False: taken off the price);
    the discount on the whole bill spread over the pieces in proportion, the cents left on the dearest.
    shortcut: no subtotal discount on RCH (its command is not documented), the prices printed for it are already discounted"""
    rows=[]
    for l in d["lines"]:
        o=l.get("off",0);q=l["qty"];cut=[o//q]*q;cut[-1]+=o-sum(cut)
        rows+=[[l["desc"],l["dept"],l["cents"],x] for x in cut]
    if not line:rows=[[a,b,c-x,0] for a,b,c,x in rows]
    off=d.get("discount") or 0
    if off:
        net=[r[2]-r[3] for r in rows];full=sum(net);cut=[n*off//full for n in net]
        top=max(range(len(rows)),key=lambda i:net[i]);cut[top]+=off-sum(cut)
        for r,x in zip(rows,cut):r[2]-=x
    return rows
def rch(sale,d,conf):
    """The commands, one a line: in the XML of the web service, or a frame each through USB."""
    pay=conf["payments"]
    cmds=["=K"]  # clears whatever a failed request left open; shortcut: the probe reads the status off the answer to a clear, a real status command when RCH documents one
    if sale.kind in ("sale","void","test"):cmds.append("=C1")  # the key back on REG, in case a closure stopped halfway left it on Z
    if sale.kind=="test":
        # the non-fiscal document of the forum's examples: =o opens and closes it, ="/(...) a line of text
        cmds+=["=o",'="/(PROVA DI COLLEGAMENTO)','="/(TD BOOKING SUITE)','="/(DOCUMENTO NON FISCALE)',"=o"]
    if sale.kind=="sale":
        # shortcut: codice fiscale and lotteria before the lines, as in the forum's example; move them if the register refuses
        cmds+=([f'="/?C/({d["cf"]})'] if d.get("cf") else [])+([f'="/?L/$1/({d["lottery"]})'] if d.get("lottery") else [])
        off=rch_cmd(conf,"rch_off")
        for desc,dept,c,x in units(d,bool(off)):cmds+=[f"=R{dept}/${c}/({text(desc)})"]+([off.replace("{importo}",str(x))] if x else [])
        cmds+=["=S"]+[f"=T{pay[k]}/${c}" for k,c in d["payments"]]
    elif sale.kind=="void":
        day=date.fromisoformat(d["day"])
        cmds+=[c.replace("{data}",f"{day:%d%m%y}").replace("{z}",f"{int(d['z']):04d}").replace("{numero}",f"{int(d['number']):04d}") for c in rch_cmd(conf,"rch_void").split()]
    elif sale.kind=="close":cmds+=rch_cmd(conf,"rch_close").split()
    return cmds
def epson(sale,d,conf):
    if sale.kind=="probe":body='<printerCommand><queryPrinterStatus operator="1" statusType="1" /></printerCommand>'
    elif sale.kind=="test":body='<printerNonFiscal><beginNonFiscal operator="1" /><printNormal operator="1" font="1" data="PROVA DI COLLEGAMENTO TD BOOKING SUITE" /><printNormal operator="1" font="1" data="DOCUMENTO NON FISCALE" /><endNonFiscal operator="1" /></printerNonFiscal>'
    elif sale.kind=="close":body='<printerFiscalReport><printZReport operator="1" /></printerFiscalReport>'
    elif sale.kind=="void":
        # the register finds the document by Z, number, day and its serial, then prints the annullo by itself
        o=sale.voids;day=date.fromisoformat(d["day"]) if d.get("day") else o.printed.astimezone(ZoneInfo(sale.tenant.timezone)).date()
        z,n,serial=(d.get("z") or o.z),(d.get("number") or o.number),(o.serial if o and o.serial else conf.get("serial",""))
        body=f'<printerFiscalReceipt><printRecMessage operator="1" messageType="4" message={quoteattr(f"VOID {int(z):04d} {int(n):04d} {day:%d%m%Y} {serial}")} /></printerFiscalReceipt>'
    else:
        pay=conf["payments"]
        # shortcut: adjustmentType 0 (discount on the last sale) from the fpMate examples, to be confirmed on the register
        rows="".join(f'<printRecItem operator="1" description={quoteattr(text(l["desc"],38))} quantity="{l["qty"]}" unitPrice="{money(l["cents"])}" department="{l["dept"]}" justification="1" />'
            +(f'<printRecItemAdjustment operator="1" adjustmentType="0" description="{off_label(l)}" amount="{money(l["off"])}" department="{l["dept"]}" justification="1" />' if l.get("off") else "") for l in d["lines"])
        if d.get("discount"):rows+=f'<printRecSubtotalAdjustment operator="1" adjustmentType="1" description="SCONTO" amount="{money(d["discount"])}" justification="1" />'
        if d.get("cf"):rows+=f'<printRecMessage operator="1" messageType="7" index="1" message={quoteattr(d["cf"])} />'
        if d.get("lottery"):rows+=f'<printRecLotteryID operator="1" code={quoteattr(d["lottery"])} />'
        rows+="".join(f'<printRecTotal operator="1" description={quoteattr(dict(PAYMENTS)[k].upper())} payment="{money(c)}" paymentType="{pay[k]}" index="1" justification="1" />' for k,c in d["payments"])
        body=f'<printerFiscalReceipt><beginFiscalReceipt operator="1" />{rows}<endFiscalReceipt operator="1" /></printerFiscalReceipt>'
    return '<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'+body+"</s:Body></s:Envelope>"
def off_label(l):return f"SCONTO {l['pct']}%" if l.get("pct") else "SCONTO"
def job(sale):
    """What the register gets, for deliver: a POST on the network, or the commands through the COM port."""
    conf=sale.location.till;out={"id":str(sale.pk),"attempt":attempt(sale)}
    if conf["device"]=="epson":return {**out,"url":url(conf),"body":epson(sale,sale.data,conf)}
    cmds=rch(sale,sale.data,conf)
    if conf.get("connection")=="usb":return {**out,"serial":{k:conf.get(k) for k in ("port","baud","framing")},"commands":cmds}
    return {**out,"url":url(conf),"body":'<?xml version="1.0" encoding="UTF-8"?><Service>'+"".join(f"<cmd>{escape(c)}</cmd>" for c in cmds)+"</Service>"}
def read(device,status,body):
    """What the register answered: ("done", {"number","z"}), ("busy", {}), ("failed", {"error"}), or ("unknown", ...) when
    the answer cannot be read and the document may have been printed."""
    try:root=ET.fromstring(body or "")
    except ET.ParseError:return "unknown",{"error":f"Risposta della cassa non leggibile (HTTP {status}): guarda se lo scontrino è uscito."}
    tag=lambda e:e.tag.rsplit("}",1)[-1]
    if device=="rch":
        # <Service><Request><errorCode>0</errorCode><printerError/><paperEnd/><coverOpen/><lastCmd/><busy/></Request></Service>
        r=root.find("Request")
        if r is None:return "unknown",{"error":"Risposta della cassa non leggibile: guarda se lo scontrino è uscito."}
        f=lambda k:(r.findtext(k) or "0").strip()
        if f("busy")!="0":return "busy",{}
        if f("errorCode")=="0":return "done",{}
        why=[w for k,w in (("paperEnd","carta finita"),("coverOpen","coperchio aperto"),("printerError","stampante in errore")) if f(k)!="0"]
        return "failed",{"error":f"La cassa risponde errore {f('errorCode')}"+(": "+", ".join(why) if why else "")+"."}
    r=next((e for e in root.iter() if tag(e)=="response"),None)
    if r is None:return "unknown",{"error":"Risposta della cassa non leggibile: guarda se lo scontrino è uscito."}
    info={tag(e):(e.text or "").strip() for e in r.iter()}
    # shortcut: zRepNumber taken as the Z the document belongs to, as printed on it; check it against the first annullo
    if r.get("success")=="true":return "done",{"number":info.get("fiscalReceiptNumber",""),"z":info.get("zRepNumber","")}
    return "failed",{"error":"La cassa risponde: "+(r.get("code") or "errore")+(f" (stato {r.get('status')})" if r.get("status") else "")+"."}

_ponte=None
def ponte():
    """scripts/ponte_cassa.py: the same sending code on the server and on the shop's PC."""
    global _ponte
    if _ponte is None:
        spec=importlib.util.spec_from_file_location("ponte_cassa",settings.BASE_DIR/"scripts"/"ponte_cassa.py")
        _ponte=importlib.util.module_from_spec(spec);spec.loader.exec_module(_ponte)
    return _ponte
def outcome(device,r):
    """deliver's result, from this server or from the bridge, as the state of the request."""
    error=r.get("error")
    if error=="unknown":return "unknown",{"error":"La cassa non ha risposto in tempo: guarda se lo scontrino è uscito prima di riprovare."}
    if error=="refused":return "failed",{"error":str(r.get("detail") or "Il ponte non ha inviato la richiesta.")[:200]}
    if error:return "failed",{"error":"La cassa non risponde: è accesa e collegata? Indirizzo o porta giusti?"}
    if r.get("serial")=="ok":return "done",{}
    if r.get("serial")=="nak":return "failed",{"error":f"La cassa rifiuta il comando {int(r.get('at') or 0)+1}: controlla reparti e pagamenti."}
    if r.get("serial"):return "unknown",{"error":"Inviato senza conferma della cassa: guarda se lo scontrino è uscito."}
    return read(device,r.get("status"),str(r.get("body") or ""))

# ---------- the queue
def stale(location):
    from .wpsync import touch
    late=Sale.objects.filter(location=location,status="sending",claimed__lt=timezone.now()-STALE)
    touch(location.tenant_id,"sale",late.values_list("pk",flat=True))  # 2.18.0: the site's archive gets it too
    late.update(status="unknown",error="Nessuna risposta dalla cassa: guarda se lo scontrino è uscito prima di riprovare.")
def claim(location):
    """The oldest request waiting, now sending; none while another one is with the register (one at a time)."""
    Location.objects.select_for_update().get(pk=location.pk)
    stale(location)
    if Sale.objects.filter(location=location,status="sending").exists():return None
    sale=Sale.objects.filter(location=location,status="queued").select_related("location","voids").order_by("created").first()
    if sale:sale.status="sending";sale.claimed=timezone.now();sale.save(update_fields=["status","claimed"])
    return sale
def attempt(sale):return sale.claimed.isoformat()
def finish(tenant,sale,state,info,attempt_id=None):
    """The register's answer: emitted (with its number when it gives one), not emitted, busy (back in the queue) or to
    check. A late answer of an attempt already given up is dropped. Emitted: the booking completed, the copy mailed."""
    sale=Sale.objects.select_for_update().get(pk=sale.pk)
    if sale.status not in ("sending","unknown") or attempt_id and sale.claimed and attempt_id!=attempt(sale):return sale
    if state=="busy":sale.status="queued";sale.claimed=None;sale.save(update_fields=["status","claimed"]);return sale
    sale.status=state;sale.error=info.get("error","")[:200]
    if state=="done":
        sale.number=str(info.get("number") or "")[:20];sale.z=str(info.get("z") or "")[:10];sale.serial=sale.location.till.get("serial","");sale.printed=timezone.now()
    sale.save(update_fields=["status","error","number","z","serial","printed"])
    audit(tenant,None,"sale."+state,sale.pk,kind=sale.kind,number=sale.number)
    if state=="done" and sale.kind=="void":
        from .wpsync import touch
        Sale.objects.filter(pk=sale.voids_id).update(status="void");touch(tenant.pk,"sale",[sale.voids_id])
        old=sale.voids
        if old and old.contact_id:record(tenant,old,f"Annullo dello scontrino n. {old.z+'-' if old.z else ''}{old.number}".strip(),[f"Annullato il {timezone.now().astimezone(ZoneInfo(tenant.timezone)):%d/%m/%Y %H:%M}"])
    if state=="done" and sale.kind=="sale" and sale.contact_id:
        d=sale.data;head,rows=copy_lines(tenant,sale,who=True)
        record(tenant,sale," + ".join(dict.fromkeys(l["desc"] for l in d.get("lines",[]))),rows+head)
    if state=="done" and sale.kind=="sale":
        from .booking import change_status,BookingError
        if sale.booking_id:
            b=sale.booking
            for x in ([b]+list(Booking.objects.filter(visit=b.visit).exclude(pk=b.pk)) if b.visit else [b]):
                if x.status in ("confirmed","arrived","in_progress"):
                    try:change_status(tenant,x,"completed")
                    except BookingError:pass  # not started yet: the desk completes it later
        send_copy(tenant,sale)
    return sale
def record(tenant,sale,what,rows):
    """2.20.5: an emitted receipt (or its annullo) with a client goes in the client's record: the day, what, by whom, lines and prices."""
    from .clients import save_treatment
    lines=sale.data.get("lines",[]) if sale.kind=="sale" else []
    staff=", ".join(dict.fromkeys(l["staff"] for l in lines if l.get("staff"))) or (sale.booking.resource.name if sale.booking_id and sale.booking.resource_id else "")
    day=(sale.printed or timezone.now()).astimezone(ZoneInfo(tenant.timezone)).date()
    save_treatment(tenant,sale.contact,{"date":day,"service":what[:120] or "Scontrino","staff":staff[:100],"details":"\n".join(rows)[:4000]},None,booking=sale.booking)
def send_now(tenant_id,location_id):
    """Direct: after the request is committed, the sale's own transaction each step, the register called in between."""
    tenant=Tenant.objects.get(pk=tenant_id)
    while True:
        with scope(tenant):
            location=Location.objects.get(pk=location_id);sale=claim(location)
            if not sale:return
            request,device=job(sale),location.till["device"]
        for _ in range(5):
            state,info=outcome(device,ponte().deliver(request))
            if state!="busy":break
            time.sleep(1)
        else:state,info="failed",{"error":"La cassa è occupata: riprova tra poco."}
        with scope(tenant):finish(tenant,sale,state,info,attempt(sale))
LINES={}  # one sender a location at a time: the register takes one request at a time
def kick(tenant_id,location_id):
    """2.20.5: the register is called after the page has answered, in a thread of its own: the page no longer waits
    for the register (it waited up to 20 seconds when the register kept the connection open after printing)."""
    if settings.TESTING:return send_now(tenant_id,location_id)
    def run():
        from django.db import connection
        try:
            with LINES.setdefault(location_id,threading.Lock()):send_now(tenant_id,location_id)
        finally:connection.close()
    threading.Thread(target=run,daemon=True).start()
def enqueue(tenant,location,**fields):
    sale=Sale(tenant=tenant,location=location,key=fields.pop("key",None) or secrets.token_urlsafe(24),**{k:v for k,v in fields.items() if k!="data"})
    sale.pii=encrypt(tenant,fields.get("data",{}),"sale:"+str(sale.pk));sale.save()
    if location.till.get("link")=="direct" and settings.TILL_DIRECT:
        transaction.on_commit(lambda:kick(tenant.pk,location.pk))
    return sale

# ---------- the copy for the client
def copy_lines(tenant,sale,who=False):
    """The lines of the copy for the client; who=True, for the client's record, with who did each line."""
    d=sale.data;tz=ZoneInfo(tenant.timezone)
    rows=[]
    for l in d.get("lines",[]):
        rows.append(f"{l['qty']} x {l['desc']}  {euro(Decimal(l['qty']*l['cents'])/100)}"+(f" · {l['staff']}" if who and l.get("staff") else ""))
        if l.get("off"):rows.append(f"{off_label(l).capitalize()}  -{euro(Decimal(l['off'])/100)}")
    if d.get("discount"):rows.append(f"Sconto  -{euro(Decimal(d['discount'])/100)}")
    rows.append(f"Totale  {euro(sale.total)}")
    rows+=[f"{dict(PAYMENTS).get(k,k)}  {euro(Decimal(c)/100)}" for k,c in d.get("payments",[])]
    head=[f"Documento commerciale n. {sale.z+'-' if sale.z else ''}{sale.number}" if sale.number else "Documento commerciale",
        f"del {sale.printed.astimezone(tz):%d/%m/%Y %H:%M}" if sale.printed else "",f"Registratore telematico {sale.serial}" if sale.serial else ""]
    return [x for x in head if x],rows
def send_copy(tenant,sale):
    """Only when the client asked for it at the desk (its own consent, not the marketing one), to the email of his card."""
    from django.template.loader import render_to_string
    from .booking import queue
    from .branding import contrast_color
    d=sale.data
    if not d.get("copy") or not d.get("email") or not sale.contact_id:return None
    head,rows=copy_lines(tenant,sale)
    body="\n".join(["Ecco la copia del tuo scontrino.",""]+head+[""]+rows+["","Questa è una copia di cortesia: il documento commerciale valido è quello emesso dal registratore telematico."])
    Sale.objects.filter(pk=sale.pk).update(mailed=timezone.now())
    return queue(tenant,{"to":d["email"],"subject":f"Copia del documento commerciale · {tenant.name}","body":body,
        "html":render_to_string("email.html",{"tenant":tenant,"body":body,"links":[],"color":contrast_color(tenant.brand_color)})},sale.contact)
def forget(tenant,sales):
    """Retention and erasure: the codice fiscale, the lotteria code, the email and who sent it go; lines and totals stay.
    An empty actor marks it done, so the daily retention does not open it again."""
    for s in sales:
        d={k:v for k,v in s.data.items() if k not in ("cf","lottery","email")}
        s.pii=encrypt(tenant,d,"sale:"+str(s.pk));s.contact=None;s.actor="";s.save(update_fields=["pii","contact","actor"])

# ---------- the bill
def cents(value):
    try:x=Decimal(str(value or "0").replace(",",".").strip() or "0")
    except InvalidOperation:raise ValueError("Importo non valido.")
    if not x.is_finite() or x<0 or x>Decimal("99999.99"):raise ValueError("Importo non valido.")
    return int((x*100).quantize(Decimal(1),ROUND_HALF_UP))
def row_off(value,full,desc):
    """The discount of a row: «100%» or «5,00» (euro); in cents, with the percent for the label of the receipt."""
    v=re.sub(r"\s","",str(value or ""))
    if not v:return 0,""
    if v.endswith("%"):
        try:p=Decimal(v[:-1].replace(",","."))
        except InvalidOperation:p=Decimal(-1)
        if not p.is_finite() or not 0<p<=100 or p!=p.quantize(Decimal("0.01")):raise ValueError(f"Sconto di «{desc}»: una percentuale da 0,01 a 100.")
        return int((full*p/100).quantize(Decimal(1),ROUND_HALF_UP)),f"{p.normalize():f}".replace(".",",")
    o=cents(v)
    if o>full:raise ValueError(f"Lo sconto di «{desc}» supera il prezzo della riga.")
    return o,""
def bill(post,conf):
    """The lines, discount, payments, codice fiscale and lotteria of the form, in cents; the department from the service."""
    deps=conf.get("departments",{});default=int(conf.get("default_dept") or 1);lines=[]
    offs=post.getlist("off");staff=post.getlist("staff")
    for i,(desc,qty,price,svc) in enumerate(zip(post.getlist("desc"),post.getlist("qty"),post.getlist("price"),post.getlist("svc"))):
        desc=text(desc)
        if not desc and not price.strip():continue  # a row left empty
        try:qty=int(qty)
        except ValueError:qty=0
        if not desc or not 1<=qty<=99:raise ValueError("Ogni riga vuole una descrizione e una quantità da 1 a 99.")
        c=cents(price)
        if not c:raise ValueError(f"Manca il prezzo di «{desc}».")
        line={"desc":desc,"qty":qty,"cents":c,"dept":int((deps.get(svc) or {}).get("dept") or default)}
        who=text(staff[i] if i<len(staff) else "")[:100]
        if who:line["staff"]=who  # 2.20.5: who did it, for the client's record only: never on the receipt
        o,pct=row_off(offs[i] if i<len(offs) else "",qty*c,desc)
        if o:line.update(off=o,pct=pct)
        lines.append(line)
    if not lines:raise ValueError("Aggiungi almeno una riga al conto.")
    if len(lines)>60:raise ValueError("Al massimo 60 righe per conto.")
    gross=sum(l["qty"]*l["cents"]-l.get("off",0) for l in lines);off=cents(post.get("discount"))
    if gross<=0:raise ValueError("Con gli sconti il conto è a zero: togli uno sconto o una riga.")
    if off>=gross:raise ValueError("Lo sconto non può superare il conto.")
    total=gross-off
    split=[[k,cents(post.get("pay_"+k))] for k,_ in PAYMENTS];split=[x for x in split if x[1]]
    if split and sum(c for _,c in split)!=total:raise ValueError(f"I pagamenti fanno {euro(Decimal(sum(c for _,c in split))/100)}, il conto {euro(Decimal(total)/100)}.")
    payments=split or [[post.get("pay") if post.get("pay") in dict(PAYMENTS) else "cash",total]]
    cf=re.sub(r"\s","",post.get("cf","")).upper();lottery=re.sub(r"\s","",post.get("lottery","")).upper()
    if cf and not CF.fullmatch(cf):raise ValueError("Codice fiscale non valido: 16 caratteri, o le 11 cifre della partita IVA.")
    if lottery and not LOTTERY.fullmatch(lottery):raise ValueError("Codice lotteria non valido: 8 caratteri tra lettere e numeri.")
    return {"lines":lines,"discount":off,"payments":payments,"cf":cf,"lottery":lottery},total
def prefill(booking):
    """The booking's service or variant at the price written on it, with the others of its visit."""
    rows=[booking]+(list(Booking.objects.filter(visit=booking.visit).exclude(pk=booking.pk).exclude(status="cancelled").order_by("start")) if booking.visit else [])
    return [{"desc":text(" ".join(x for x in (b.snapshot.get("service") or b.service.name,b.snapshot.get("option","")) if x)),"qty":1,
        "price":(b.snapshot.get("total") or "").replace(".",","),"svc":str(b.service_id),"staff":b.resource.name if b.resource_id else ""} for b in rows]

# ---------- the pages
def places():return [l for l in Location.objects.filter(active=True).order_by("name") if l.till.get("device")]
def till_view(request,slug):
    """Cassa: the bill of a booking (?prenotazione=) or of who buys without one, sent to the location's register; the
    documents of today with their copy and annullo."""
    require(request,"sale.write")
    t=request.tenant;where=places()
    if not where:return render(request,"cassa.html",{"none":True})
    pick=request.POST.get("sede") or request.GET.get("sede")
    loc=next((l for l in where if str(l.pk)==pick),where[0]);back=reverse("till",args=[slug])+f"?sede={loc.pk}"
    booking=None
    if request.GET.get("prenotazione") or request.POST.get("booking"):
        booking=get_object_or_404(visible_bookings(request,Booking.objects.select_related("service","contact")),pk=request.GET.get("prenotazione") or request.POST.get("booking"))
    if request.method=="POST":
        action=request.POST.get("action")
        try:
            if action=="sell":
                key=request.POST.get("key","")
                if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}",key):raise ValueError("Ricarica la pagina e riprova.")
                if Sale.objects.filter(key=key).exists():messages.info(request,"Questo conto è già stato inviato alla cassa.");return redirect(back)
                d,total=bill(request.POST,loc.till)
                contact=booking.contact if booking and booking.contact_id else None
                if request.POST.get("contact") and can(request,"contacts.read"):contact=get_object_or_404(Contact,pk=request.POST["contact"])
                if request.POST.get("copy")=="on":
                    email=contact.data.get("email","") if contact else ""
                    if not email:raise ValueError("Per la copia per email scegli un cliente con l'email nella scheda.")
                    d.update(copy=True,email=email)
                sale=enqueue(t,loc,key=key,booking=booking,contact=contact,total=Decimal(total)/100,actor=str(request.user.pk),data=d)
                if d.get("copy"):ConsentEvent.objects.create(tenant=t,contact=contact,kind="confirmed",purpose="receipt_copy",channel="desk",
                    text_hash=__import__("core.privacy",fromlist=["text_hash"]).text_hash(COPY_TEXT),ref=str(sale.pk),actor=str(request.user.pk))
                audit(t,request.user,"sale.sent",sale.pk,total=str(sale.total),booking=str(booking.pk) if booking else "")
                messages.success(request,"Conto inviato alla cassa.")
                return redirect(back)
            if action=="close":
                if loc.till["device"]=="rch" and not rch_cmd(loc.till,"rch_close"):raise ValueError("Su questa cassa la chiusura si fa dalla cassa.")
                enqueue(t,loc,kind="close",actor=str(request.user.pk));audit(t,request.user,"sale.close",loc.pk)
                messages.success(request,"Chiusura giornaliera inviata alla cassa.");return redirect(back)
            if action=="void" and not request.POST.get("sale"):
                void(request,t,loc,None);return redirect(back)
            sale=get_object_or_404(Sale,pk=request.POST.get("sale"),location=loc)
            if action=="retry" and sale.status in ("failed","unknown"):
                # unknown: the operator has looked, nothing came out of the register
                sale.status="queued";sale.error="";sale.claimed=None;sale.save(update_fields=["status","error","claimed"])
                if loc.till.get("link")=="direct" and settings.TILL_DIRECT:transaction.on_commit(lambda:kick(t.pk,loc.pk))
                audit(t,request.user,"sale.retry",sale.pk)
            elif action=="printed" and sale.status=="unknown":
                finish(t,sale,"done",{"number":re.sub(r"\D","",request.POST.get("number",""))[:20]})
                audit(t,request.user,"sale.printed_by_hand",sale.pk)
            elif action=="void" and sale.kind=="sale" and sale.status=="done":void(request,t,loc,sale)
            elif action=="mail" and sale.status in ("done","void"):
                messages.success(request,"Copia inviata per email." if send_copy(t,sale) else "Il cliente non ha chiesto la copia per email.")
            return redirect(back)
        except ValueError as ex:messages.error(request,str(ex))
    stale(loc)
    tz=ZoneInfo(t.timezone);today=datetime.combine(timezone.now().astimezone(tz).date(),datetime.min.time(),tz)
    copy=get_object_or_404(Sale,pk=request.GET["copia"],location=loc) if request.GET.get("copia") else None
    if copy:
        head,rows=copy_lines(t,copy)
        return render(request,"cassa_copy.html",{"sale":copy,"head":head,"rows":rows,"back":back})
    kinds=dict(Service._meta.get_field("kind").choices);items=[]
    for s in Service.objects.filter(location=loc,active=True).order_by("kind","name"):
        opts=[o for o in Option.objects.filter(service=s,active=True).order_by("name")]
        items+=[{"group":kinds.get(s.kind,""),"name":text(f"{s.name} {o.name}"),"price":str(o.price if o.price is not None else s.price or "").replace(".",","),"svc":str(s.pk)} for o in opts] or [{"group":kinds.get(s.kind,""),"name":text(s.name),"price":str(s.price or "").replace(".",","),"svc":str(s.pk)}]
    groups={}
    for i in items:groups.setdefault(i["group"],[]).append(i)
    rows=Sale.objects.filter(location=loc,kind__in=("sale","void","close"),created__gte=today).select_related("booking","contact","voids").order_by("-created")
    busy=any(s.status in ("queued","sending") for s in rows)
    who=booking.data if booking and can(request,"pii.read") else {}
    names=list(Resource.objects.filter(location=loc,active=True).order_by("name").values_list("name",flat=True))
    return render(request,"cassa.html",{"where":where,"loc":loc,"groups":groups,"booking":booking,"who":who,"lines":prefill(booking) if booking else [],
        "staff_names":names,"default_staff":booking.resource.name if booking and booking.resource_id else "",
        "key":secrets.token_urlsafe(24),"payments":PAYMENTS,"rows":rows,"busy":busy,"pick_client":can(request,"contacts.read") and can(request,"pii.read"),
        "contact":booking.contact if booking and booking.contact_id and can(request,"pii.read") else None,"can_void":loc.till["device"]=="epson" or bool(rch_cmd(loc.till,"rch_void")),"can_close":loc.till["device"]=="epson" or bool(rch_cmd(loc.till,"rch_close")),
        "cf":receipt_code(booking.contact.data) if booking and booking.contact_id and can(request,"pii.read") else ""})

DOC=re.compile(r"\s*(\d{1,4})\s*-\s*(\d{1,4})\s*")
def void(request,t,loc,sale):
    """The annullo of a documento commerciale: of one in the list, or of any other written by hand (day and «Documento n.»,
    closure and number as printed at its foot). On RCH with no annullo command the desk does it on the register and it is
    only marked here."""
    m=DOC.fullmatch(request.POST.get("doc","") or (f"{sale.z}-{sale.number}" if sale and sale.z and sale.number else ""))
    try:day=date.fromisoformat(request.POST.get("day","")) if request.POST.get("day") else sale.printed.astimezone(ZoneInfo(t.timezone)).date()
    except (ValueError,AttributeError):day=None
    if loc.till["device"]=="rch" and not rch_cmd(loc.till,"rch_void"):
        if not sale:raise ValueError("Su questa cassa l'annullo si fa dalla cassa.")
        sale.status="void";sale.save(update_fields=["status"]);audit(t,request.user,"sale.void",sale.pk);messages.success(request,"Segnato come annullato.");return
    if not (m and day) or day>timezone.now().astimezone(ZoneInfo(t.timezone)).date():raise ValueError("Scrivi la data e il «Documento n.» dello scontrino, come sono stampati in fondo: per esempio 2026-0012.")
    if loc.till["device"]=="epson" and not (sale and sale.serial or loc.till.get("serial")):raise ValueError("Manca la matricola della cassa: scrivila in Registratore di cassa.")
    if sale and sale.voided_by.exclude(status="failed").exists():raise ValueError("L'annullo è già stato inviato.")
    v=enqueue(t,loc,kind="void",voids=sale,total=sale.total if sale else 0,actor=str(request.user.pk),data={"z":m[1],"number":m[2],"day":day.isoformat()})
    audit(t,request.user,"sale.void",sale.pk if sale else v.pk);messages.success(request,"Annullo inviato alla cassa.")
def allowed(request):
    return request.user.is_staff or request.tenant.till_self and getattr(request.member,"role","")=="owner"
def settings_view(request,slug):
    """Registratore di cassa: the register of each location, its departments and payments, and how it is reached."""
    if not allowed(request):raise PermissionDenied
    if request.GET.get("ponte"):return FileResponse(open(settings.BASE_DIR/"scripts"/"ponte_cassa.py","rb"),as_attachment=True,filename="ponte_cassa.py")
    t=request.tenant;token="";token_for=None;found=None;found_for=None
    locations=list(Location.objects.filter(active=True).order_by("name"))
    if request.method=="POST":
        loc=get_object_or_404(Location,pk=request.POST.get("location"));action=request.POST.get("action");anchor=f"#cassa-{loc.pk}"
        try:
            if action=="save":
                device=request.POST.get("device","")
                if device not in dict(DEVICES):raise ValueError("Scegli il registratore.")
                conf={}
                if device:
                    conf={"device":device,"link":"direct" if settings.TILL_DIRECT and request.POST.get("link")=="direct" else "bridge",
                        "serial":re.sub(r"[^A-Z0-9]","",request.POST.get("serial","").upper())[:20],"departments":{},"payments":{}}
                    if device=="rch" and request.POST.get("connection")=="usb":
                        # Epson's USB goes through its own driver: network only
                        port=request.POST.get("port","").strip();port=port.upper() if port.lower().startswith("com") else port
                        if not PORT.fullmatch(port):raise ValueError("Scrivi la porta della cassa, per esempio COM3.")
                        baud=int(request.POST.get("baud") or 9600)
                        if baud not in BAUDS:raise ValueError("Velocità non valida.")
                        conf.update(connection="usb",port=port,baud=baud,framing="cr" if request.POST.get("framing")=="cr" else "stx")
                    else:conf.update(connection="net",address=lan(request.POST.get("address")))
                    def number(name,low=1):
                        v=request.POST.get(name,"").strip()
                        if not v:return None
                        if not v.isdigit() or not low<=int(v)<=99:raise ValueError("Reparti e pagamenti sono numeri da 1 a 99 (Epson: tipo di pagamento da 0).")
                        return int(v)
                    conf["default_dept"]=number("default_dept") or 1
                    if device=="rch":
                        for key,(default,need) in RCH_CMDS.items():
                            v=" ".join(request.POST.get(key,default).split())
                            if v and not (all(re.fullmatch(r"=[\x21-\x7e]{1,38}",c) and "(" not in c for c in v.split()) and need in v and len(v)<=80):
                                raise ValueError(f"Comandi RCH: ognuno comincia con =, senza parentesi, separati da uno spazio{', e ci vuole '+need if need else ''}.")
                            conf[key]=v
                    for s in Service.objects.filter(location=loc):
                        dept=number(f"dept_{s.pk}");vat=request.POST.get(f"vat_{s.pk}","").strip()[:8]
                        if dept or vat:conf["departments"][str(s.pk)]={"dept":dept or conf["default_dept"],"vat":vat}
                    for k,_ in PAYMENTS:
                        v=number("pay_"+k,0);conf["payments"][k]=DEFAULT_PAY[device][k] if v is None else v
                loc.till=conf;loc.save(update_fields=["till"])
                Tenant.objects.filter(pk=t.pk).update(till_on=Location.objects.filter(active=True).exclude(till={}).exists())
                audit(t,request.user,"till.settings",loc.pk,device=device)
                messages.success(request,"Registratore salvato." if device else "Registratore tolto dalla sede.")
            elif action=="discover":
                # 2.20.3: only where this computer is in the shop (Windows); the bridge has --cerca
                if not settings.TILL_DIRECT:raise ValueError("La ricerca si fa dal computer del negozio: lì lancia python ponte_cassa.py --cerca.")
                found,ports=ponte().discover();found_for=loc.pk
                audit(t,request.user,"till.discover",loc.pk,found=len(found))
            elif action in ("probe","test"):
                if not loc.till.get("device"):raise ValueError("Prima salva il registratore.")
                enqueue(t,loc,kind=action,actor=str(request.user.pk))
                messages.success(request,"Richiesta inviata alla cassa." if loc.till.get("link")=="direct" else "Richiesta in coda: la manda il ponte cassa appena si collega.")
            elif action=="token":
                token="tdcassa_"+secrets.token_urlsafe(32);token_for=loc.pk
                loc.till_token=token_hash(token);loc.save(update_fields=["till_token"]);audit(t,request.user,"till.token",loc.pk)
            elif action=="revoke":
                loc.till_token="";loc.save(update_fields=["till_token"]);audit(t,request.user,"till.token_revoked",loc.pk)
                messages.success(request,"Codice del ponte revocato.")
            if not token and found_for is None:return redirect(reverse("till_settings",args=[slug])+anchor)
        except ValueError as ex:messages.error(request,str(ex))
        locations=list(Location.objects.filter(active=True).order_by("name"))
    cards=[]
    for l in locations:
        conf=l.till;deps=conf.get("departments",{})
        cards.append({"loc":l,"conf":conf,"pay":[(k,label,conf.get("payments",{}).get(k,"")) for k,label in PAYMENTS],
            "services":[(s,deps.get(str(s.pk),{})) for s in Service.objects.filter(location=l,active=True).order_by("name")],
            "last":Sale.objects.filter(location=l,kind__in=("probe","test")).order_by("-created").first(),"rch":{k:rch_cmd(conf,k) for k in RCH_CMDS},"found":(found,ports) if found_for==l.pk else None,"token":token if token_for==l.pk else ""})
    ports=[]
    if settings.TILL_DIRECT:
        try:from serial.tools.list_ports import comports;ports=[p.device for p in comports()]
        except ImportError:pass
    return render(request,"cassa_settings.html",{"cards":cards,"devices":DEVICES,"direct":settings.TILL_DIRECT,"ports":ports,"bauds":BAUDS,
        "bridge_url":request.build_absolute_uri(reverse("till_bridge",args=[slug]))})

@csrf_exempt
def bridge(request,slug):
    """The bridge of a location (scripts/ponte_cassa.py): GET the next request for the register, POST its answer."""
    from .identity import throttle
    key=request.headers.get("Authorization","").removeprefix("Bearer ").strip()
    loc=Location.objects.filter(till_token=token_hash(key)).first() if key.startswith("tdcassa_") else None
    if not loc or not loc.till.get("device"):return JsonResponse({"error":"Codice del ponte non valido."},status=403)
    try:throttle(request,"till:"+str(loc.pk),120,60,identity=str(loc.pk))
    except PermissionDenied as ex:return JsonResponse({"error":str(ex)},status=429)
    t=request.tenant;Location.objects.filter(pk=loc.pk).update(till_seen=timezone.now())
    if request.method=="GET":
        sale=claim(loc)
        if not sale:return JsonResponse({"job":None})
        return JsonResponse({"job":job(sale)})
    if request.method!="POST":return JsonResponse({"error":"Metodo non consentito."},status=405)
    try:r=json.loads(request.body or b"{}");sale=Sale.objects.select_related("location").get(pk=r["id"],location=loc)
    except (ValueError,KeyError,TypeError,Sale.DoesNotExist):return JsonResponse({"error":"Risposta non valida."},status=400)
    try:state,info=outcome(loc.till["device"],r)
    except (ValueError,TypeError):return JsonResponse({"error":"Risposta non valida."},status=400)
    sale=finish(t,sale,state,info,str(r.get("attempt") or ""))
    return JsonResponse({"status":sale.status})
