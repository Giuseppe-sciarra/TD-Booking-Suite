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
from django.db import models
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import FileResponse,JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from .clients import receipt_code
from . import gift
from .crypto import encrypt,token_hash
from .models import Booking,ConsentEvent,Contact,GiftCard,Location,Option,Parked,Product,Quote,Resource,Sale,Service,Tenant,audit
from .permissions import can,require,visible_bookings
from .prices import euro
from .tenancy import scope

DEVICES=[("","Nessuno"),("rch","RCH Print!F"),("epson","Epson FP-81II / FP-90III RT")]
PAYMENTS=[("cash","Contanti"),("card","Carta o bancomat"),("other","Altro"),("due","Da pagare")]
SETTLE=PAYMENTS[:3]  # 2.20.9: how a «Da pagare» is paid later
# shortcut: RCH 1 contanti, 4 carta, 2 non riscosso as Spiagge.it's setup asks; Epson paymentType 0 contanti, 2 carta,
# 5 non riscosso, always index 1: change them in Registratore di cassa to what the register has
DEFAULT_PAY={"rch":{"cash":1,"card":4,"other":2,"due":2},"epson":{"cash":0,"card":2,"other":5,"due":5}}  # 2.20.9: Da pagare is non riscosso
def pay_code(conf,k):return conf.get("payments",{}).get(k,DEFAULT_PAY[conf["device"]][k])  # a register saved before 2.20.9 has no «due»
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
    for kind,off in (("discount",d.get("discount") or 0),("gift",gift_cents(d))):
        if not off:continue
        net=[r[2]-r[3] for r in rows];full=sum(net);cut=[n*off//full for n in net]
        top=max(range(len(rows)),key=lambda i:net[i]);cut[top]+=off-sum(cut)
        for r,x in zip(rows,cut):
            if kind=="gift" and line:r[3]+=x  # 2.22.0: the gift card as the row's discount, so the price printed is the real one
            else:r[2]-=x
    return rows
def gift_cents(d):return (d.get("gift") or {}).get("cents",0)
def rch(sale,d,conf):
    """The commands, one a line: in the XML of the web service, or a frame each through USB."""
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
        # 2.22.0: a bill all paid with a gift card closes at 0: =T with no amount is the exact total
        # shortcut: a receipt at 0 on RCH is to be tried on the register, as the row discount
        cmds+=["=S"]+[f"=T{pay_code(conf,k)}/${c}" if c else f"=T{pay_code(conf,k)}" for k,c in d["payments"]]
    elif sale.kind=="void":
        day=date.fromisoformat(d["day"])
        # 2.22.0: without the closure number (RCH never tells it) the [z/] part goes
        cmds+=[(c if d.get("z") else re.sub(r"\[[^\]]*\{z\}[^\]]*\]|\{z\}/?","",c)).replace("{data}",f"{day:%d%m%y}").replace("{z}",f"{int(d['z']):04d}" if d.get("z") else "").replace("{numero}",f"{int(d['number']):04d}") for c in rch_cmd(conf,"rch_void").split()]
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
        # shortcut: adjustmentType 0 (discount on the last sale) from the fpMate examples, to be confirmed on the register
        rows="".join(f'<printRecItem operator="1" description={quoteattr(text(l["desc"],38))} quantity="{l["qty"]}" unitPrice="{money(l["cents"])}" department="{l["dept"]}" justification="1" />'
            +(f'<printRecItemAdjustment operator="1" adjustmentType="0" description="{off_label(l)}" amount="{money(l["off"])}" department="{l["dept"]}" justification="1" />' if l.get("off") else "") for l in d["lines"])
        if d.get("discount"):rows+=f'<printRecSubtotalAdjustment operator="1" adjustmentType="1" description="SCONTO" amount="{money(d["discount"])}" justification="1" />'
        if gift_cents(d):rows+=f'<printRecSubtotalAdjustment operator="1" adjustmentType="1" description={quoteattr(text("GIFT CARD "+d["gift"].get("code",""),38))} amount="{money(gift_cents(d))}" justification="1" />'
        if d.get("cf"):rows+=f'<printRecMessage operator="1" messageType="7" index="1" message={quoteattr(d["cf"])} />'
        if d.get("lottery"):rows+=f'<printRecLotteryID operator="1" code={quoteattr(d["lottery"])} />'
        rows+="".join(f'<printRecTotal operator="1" description={quoteattr(dict(PAYMENTS)[k].upper())} payment="{money(c)}" paymentType="{pay_code(conf,k)}" index="1" justification="1" />' for k,c in d["payments"])
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
    was=sale.status;sale.status=state;sale.error=info.get("error","")[:200]
    if state=="done":
        sale.number=str(info.get("number") or "")[:20];sale.z=str(info.get("z") or "")[:10];sale.serial=sale.location.till.get("serial","");sale.printed=timezone.now()
        if sale.kind=="sale" and not sale.number and sale.location.till.get("device")=="rch":
            # 2.22.0: RCH never says the number: the program counts. A «Da verificare» found printed keeps the place
            # it had when it was sent, so the ones printed after it are not shifted
            if was=="unknown" and sale.claimed:sale.printed=sale.claimed
            sale.z,sale.number=counted(sale)
        if sale.kind=="close":closed(sale.location)
    sale.save(update_fields=["status","error","number","z","serial","printed"])
    audit(tenant,None,"sale."+state,sale.pk,kind=sale.kind,number=sale.number)
    if sale.kind=="sale":
        if state=="failed":gift.give_back(tenant,sale)  # 2.22.0: not emitted: the credit back on the card, taken again by Riprova
        if state=="done":gift.sold(tenant,sale,"done")
    if state=="done" and sale.kind=="void":
        from .wpsync import touch
        Sale.objects.filter(pk=sale.voids_id).update(status="void");touch(tenant.pk,"sale",[sale.voids_id])
        old=sale.voids
        if old:
            gift.give_back(tenant,old);gift.sold(tenant,old,"void")
        if old and old.contact_id:record(tenant,old,f"Annullo dello scontrino n. {old.z+'-' if old.z else ''}{old.number}".strip(),[f"Annullato il {timezone.now().astimezone(ZoneInfo(tenant.timezone)):%d/%m/%Y %H:%M}"],sign=-1)
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
def counted(sale):
    """2.22.0: the «chiusura-documento» pair of a document emitted on a register that does not say it (RCH), as printed
    at its foot: the closure it will belong to (the last closure's number, written once in Registratore di cassa and
    raised at each closure, plus one) and its number, 1 for the first after the closure, then 2, 3… counting the
    receipts and annulli this program emitted here. Receipts made on the register's own keys are not seen: the desk
    corrects the number then. Without the closure number the pair is ("", n)."""
    location=Location.objects.get(pk=sale.location_id);conf=location.till  # fresh: the one on the sale was read before the register was called
    last=Sale.objects.filter(location=location,kind="close",status="done",printed__isnull=False,printed__lt=sale.printed).order_by("-printed").values_list("printed",flat=True).first()
    # the ones printed before it, and the ones sent before it still «Da verificare» (printed, as far as anyone knows)
    q=Sale.objects.filter(location=location,kind__in=("sale","void")).exclude(pk=sale.pk).filter(models.Q(status__in=("done","void"),printed__lt=sale.printed)|models.Q(status="unknown",claimed__lt=sale.printed))
    if last:q=q.filter(models.Q(printed__gt=last)|models.Q(status="unknown",claimed__gt=last))
    return (str(int(conf["last_z"])+1) if conf.get("last_z") is not None else ""),str(q.count()+1)
def closed(location):
    """A daily closure done on an RCH (emitted here or on its keys): the next documents belong to the closure after."""
    location=Location.objects.get(pk=location.pk);conf=location.till  # fresh, never the copy read before the register was called
    if conf.get("device")=="rch" and conf.get("last_z") is not None:
        conf["last_z"]=int(conf["last_z"])+1;location.till=conf;location.save(update_fields=["till"])
def shares(d,total):
    """{operator: cents} of a bill: each line net of its discount, the discount on the total spread over the lines."""
    nets=[(l.get("staff",""),l["qty"]*l["cents"]-l.get("off",0)) for l in d.get("lines",[])];gross=sum(c for _,c in nets) or 1
    out={}
    for who,c in nets:out[who]=out.get(who,0)+c*total//gross
    if out:out[max(out,key=out.get)]+=total-sum(out.values())  # the cents of the rounding
    return out
def record(tenant,sale,what,rows,sign=1):
    """2.20.5: an emitted receipt (or its annullo) with a client goes in the client's record: the day, what, by whom,
    lines and prices, and what it cost, in all and for each operator, for the reports (an annullo takes it back)."""
    from .clients import save_treatment
    lines=sale.data.get("lines",[]) if sale.kind=="sale" else []
    staff=", ".join(dict.fromkeys(l["staff"] for l in lines if l.get("staff"))) or (sale.booking.resource.name if sale.booking_id and sale.booking.resource_id else "")
    day=(sale.printed or timezone.now()).astimezone(ZoneInfo(tenant.timezone)).date()
    total=paid(sale);by={k:sign*v for k,v in shares(sale.data,total).items()}
    save_treatment(tenant,sale.contact,{"date":day,"service":what[:120] or "Scontrino","staff":staff[:100],"details":"\n".join(rows)[:4000],"amount":sign*total,"by":by},None,booking=sale.booking)
def paid(sale):
    """2.20.9: the cents the client has paid: a «Da pagare» still open is not money in (it goes in on the day he pays)."""
    return int(sale.total*100)-(0 if sale.settled else int(sale.due*100))
def owed(**where):
    """The receipts with a «Da pagare» still open: emitted, not voided, not paid yet."""
    return Sale.objects.filter(kind="sale",status="done",due__gt=0,settled__isnull=True,**where)
def settle(tenant,user,sale,how):
    """2.20.9: the client pays what he owed: the day and how, and in his record the payment, with each operator's part,
    for the reports of that day. Nothing goes to the register: what to print, if anything, the shop's accountant says."""
    if how not in dict(SETTLE):raise ValueError("Scegli come ha pagato.")
    sale=Sale.objects.select_for_update().get(pk=sale.pk)
    if not (sale.kind=="sale" and sale.status=="done" and sale.due>0 and not sale.settled):raise ValueError("Questo scontrino non ha niente da pagare.")
    sale.settled=timezone.now();sale.settled_with=how;sale.save(update_fields=["settled","settled_with"])
    audit(tenant,user,"sale.settled",sale.pk,due=str(sale.due),how=how)
    if sale.contact_id:
        from .clients import save_treatment
        tz=ZoneInfo(tenant.timezone);c=int(sale.due*100);lines=sale.data.get("lines",[])
        staff=", ".join(dict.fromkeys(l["staff"] for l in lines if l.get("staff")))
        doc=f"n. {sale.z+'-' if sale.z else ''}{sale.number} " if sale.number else ""
        save_treatment(tenant,sale.contact,{"date":sale.settled.astimezone(tz).date(),"service":f"Pagamento dello scontrino {doc}del {sale.printed.astimezone(tz):%d/%m/%Y}"[:120],
            "staff":staff[:100],"details":f"Pagato {euro(sale.due)} con {dict(SETTLE)[how].lower()}","amount":c,"by":shares(sale.data,c),"kind":"payment"},user,booking=sale.booking)
    return sale
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
    if gift_cents(d):rows.append(f"Gift card {d['gift'].get('code','')}  -{euro(Decimal(gift_cents(d))/100)}")
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
def bill(post,conf,credit=0):
    """The lines, discount, payments, codice fiscale and lotteria of the form, in cents; the department from the service.
    2.22.0: credit, the cents of a gift card that may pay it: what it pays is taken off the total, the rest is paid as usual."""
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
        # 2.22.0: a product (p:id) or a gift card sold (gift:expiry) on their own department
        dept=conf.get("product_dept") if svc.startswith("p:") else conf.get("gift_dept") if svc.startswith("gift:") else (deps.get(svc) or {}).get("dept")
        line={"desc":desc,"qty":qty,"cents":c,"dept":int(dept or default)}
        if svc:line["svc"]=svc[:44]  # 2.20.8: which service, for the ones most used on top of the Cassa
        who=text(staff[i] if i<len(staff) else "")[:100]
        if who:line["staff"]=who  # 2.20.5: who did it, for the client's record only: never on the receipt
        o,pct=row_off(offs[i] if i<len(offs) else "",qty*c,desc)
        if o:line.update(off=o,pct=pct)
        if svc.startswith("gift:"):
            if qty!=1 or o:raise ValueError("Una gift card per riga, senza sconto di riga.")
            if c<100:raise ValueError("Una gift card vale almeno 1,00 €.")
            try:svc[5:] and date.fromisoformat(svc[5:])
            except ValueError:raise ValueError("Scadenza della gift card non valida.")
        lines.append(line)
    if not lines:raise ValueError("Aggiungi almeno una riga al conto.")
    if len(lines)>60:raise ValueError("Al massimo 60 righe per conto.")
    gross=sum(l["qty"]*l["cents"]-l.get("off",0) for l in lines)
    if gross<=0:raise ValueError("Con gli sconti il conto è a zero: togli uno sconto o una riga.")
    off,_=row_off(post.get("discount"),gross,"Sconto sul totale") if str(post.get("discount") or "").strip().endswith("%") else (cents(post.get("discount")),"")  # 2.20.5: in euro or with the %
    if off>=gross:raise ValueError("Lo sconto non può superare il conto.")
    total=gross-off
    use=min(credit,total);total-=use
    if use and any(l.get("svc","").startswith("gift:") for l in lines):raise ValueError("Una gift card non si paga con un'altra gift card.")
    split=[[k,cents(post.get("pay_"+k))] for k,_ in PAYMENTS];split=[x for x in split if x[1]]
    if split and sum(c for _,c in split)!=total:raise ValueError(f"I pagamenti fanno {euro(Decimal(sum(c for _,c in split))/100)}, il conto {euro(Decimal(total)/100)}.")
    payments=split or [[post.get("pay") if post.get("pay") in dict(PAYMENTS) and total else "cash",total]]
    cf=re.sub(r"\s","",post.get("cf","")).upper();lottery=re.sub(r"\s","",post.get("lottery","")).upper()
    if cf and not CF.fullmatch(cf):raise ValueError("Codice fiscale non valido: 16 caratteri, o le 11 cifre della partita IVA.")
    if lottery and not LOTTERY.fullmatch(lottery):raise ValueError("Codice lotteria non valido: 8 caratteri tra lettere e numeri.")
    return {"lines":lines,"discount":off,"payments":payments,"cf":cf,"lottery":lottery,**({"gift":{"cents":use}} if use else {})},total
def prefill(booking):
    """The booking's service or variant at the price written on it, with the others of its visit."""
    rows=[booking]+(list(Booking.objects.filter(visit=booking.visit).exclude(pk=booking.pk).exclude(status="cancelled").order_by("start")) if booking.visit else [])
    return [{"desc":text(" ".join(x for x in (b.snapshot.get("service") or b.service.name,b.snapshot.get("option","")) if x)),"qty":1,
        "price":(b.snapshot.get("total") or "").replace(".",","),"svc":str(b.service_id),"staff":b.resource.name if b.resource_id else ""} for b in rows]

# ---------- the bill sent, or left in Preventivo pagamenti cassa
ROW=("desc","qty","price","svc","off","staff")
def split(post):
    """2.22.0: the rows marked «In sospeso» (park=1) apart from the others, each as a form of its own for bill()."""
    keep,park=post.copy(),post.copy()
    flags=post.getlist("park");n=len(post.getlist("desc"))
    for k in ROW:
        v=post.getlist(k);v+=[""]*(n-len(v))
        keep.setlist(k,[x for i,x in enumerate(v) if not (i<len(flags) and flags[i]=="1")])
        park.setlist(k,[x for i,x in enumerate(v) if i<len(flags) and flags[i]=="1"])
    for k,_ in PAYMENTS:park.pop("pay_"+k,None)
    park.pop("discount",None);park.pop("pay",None)
    return keep,park
def has_rows(post):return any(d.strip() or p.strip() for d,p in zip(post.getlist("desc"),post.getlist("price")))
def settled(t,x):
    """2.22.0: a bill «Non nello scontrino» taken into a receipt: its treatment in the client's record loses the mark."""
    from .models import Treatment
    pk=x.data.get("treatment")
    if not pk:return
    tr=Treatment.objects.filter(pk=pk).first()
    if tr:tr.pii=encrypt(t,{**tr.data,"kind":"parked_paid"},"treatment:"+str(tr.pk));tr.save(update_fields=["pii"])
def park(t,loc,contact,booking,post,key,user,note=""):
    """Lines out of the receipt, on the client's card: Preventivo pagamenti cassa. The booking's work is done: completed."""
    d,total=bill(post,loc.till)
    if any(l.get("svc","").startswith("gift:") for l in d["lines"]):raise ValueError("La gift card si vende con lo scontrino: non può restare fuori.")
    # 2.22.1: without a client and without a booking nothing is kept: the business writes it down itself (the owner's choice)
    if not contact and not booking:
        audit(t,user,"sale.parked",None,total=str(Decimal(total)/100),unrecorded=True);return None
    x=Parked(tenant=t,location=loc,contact=contact,booking=booking,total=Decimal(total)/100,key=key,actor=str(user.pk))
    x.pii=encrypt(t,{"lines":d["lines"],"discount":d["discount"],"note":" ".join(str(note or "").split())[:120]},"parked:"+str(x.pk));x.save()
    audit(t,user,"sale.parked",x.pk,total=str(x.total))
    if contact:
        # 2.22.0: the treatment is done: in the record and the reports today, with a mark until it is in a receipt
        from .clients import save_treatment
        rows=[(f"{l['qty']} x " if l["qty"]>1 else "")+l["desc"]+(f" ({l['staff']})" if l.get("staff") else "")+f": {euro(Decimal(l['qty']*l['cents']-l.get('off',0))/100)}" for l in d["lines"]]
        if note:rows.append(" ".join(str(note).split())[:120])
        staff=", ".join(dict.fromkeys(l["staff"] for l in d["lines"] if l.get("staff")))
        tr=save_treatment(t,contact,{"date":timezone.now().astimezone(ZoneInfo(t.timezone)).date(),"service":" + ".join(dict.fromkeys(l["desc"] for l in d["lines"]))[:120],"staff":staff[:100],
            "details":"\n".join(rows)[:4000],"amount":total,"by":shares(d,total),"kind":"parked"},user,booking=booking)
        x.pii=encrypt(t,{**x.data,"treatment":str(tr.pk)},"parked:"+str(x.pk));x.save(update_fields=["pii"])
    if booking:
        from .booking import change_status,BookingError
        for b in ([booking]+list(Booking.objects.filter(visit=booking.visit).exclude(pk=booking.pk)) if booking.visit else [booking]):
            if b.status in ("confirmed","arrived","in_progress"):
                try:change_status(t,b,"completed")
                except BookingError:pass
    return x
def sell(request,t,loc,booking,action):
    """Invia alla cassa: the receipt of the rows not «In sospeso», the others in Preventivo pagamenti cassa;
    Metti in sospeso (park): the whole bill there. The suspended ones taken back into this bill are gone from there."""
    key=request.POST.get("key","")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}",key):raise ValueError("Ricarica la pagina e riprova.")
    if Sale.objects.filter(key=key).exists() or Parked.objects.filter(key=key).exists():
        messages.info(request,"Questo conto è già stato inviato.");return ""
    contact=booking.contact if booking and booking.contact_id else None
    if request.POST.get("contact") and can(request,"contacts.read"):contact=get_object_or_404(Contact,pk=request.POST["contact"])
    taken=list(Parked.objects.select_for_update().filter(pk__in=[x for x in request.POST.getlist("parked") if re.fullmatch(r"[0-9a-f-]{36}",x)],location=loc))
    if not contact:contact=next((x.contact for x in taken if x.contact_id),None)
    keep,later=split(request.POST)
    if action=="park":
        keep,later=None,request.POST.copy();later.pop("pay",None)
    note=request.POST.get("park_note","")
    if keep is None or not has_rows(keep):
        if not has_rows(later):raise ValueError("Aggiungi almeno una riga al conto.")
        if gift.clean(request.POST.get("gift_code")):raise ValueError("La gift card si usa sullo scontrino: togli il codice per lasciare tutto il conto fuori dallo scontrino.")
        for k,_ in PAYMENTS:later.pop("pay_"+k,None)
        x=park(t,loc,contact,booking,later,key,request.user,note)
        Parked.objects.filter(pk__in=[p.pk for p in taken]).delete()
        if not x:return f"Non nello scontrino: {euro(Decimal(bill(later,loc.till)[1])/100)}. Senza cliente non resta segnato da nessuna parte."
        return f"Non nello scontrino: {euro(x.total)}, segnato tra i trattamenti del cliente."+("" if contact else " Resta sulla prenotazione.")
    card=None;credit=0
    code=gift.clean(request.POST.get("gift_code"))
    if code:
        card=gift.usable(code,timezone.now().astimezone(ZoneInfo(t.timezone)).date());credit=gift.cents(card)
        want=request.POST.get("gift_use","").strip()
        if want:credit=min(credit,cents(want))
    d,total=bill(keep,loc.till,credit)
    if card and "gift" in d:d["gift"].update(id=str(card.pk),code=card.code)
    sold=[l for l in d["lines"] if l.get("svc","").startswith("gift:")]
    codes=[gift.new_code() for _ in sold]
    for l,c in zip(sold,codes):l["desc"]=f"GIFT CARD {c}"
    due=sum(c for k,c in d["payments"] if k=="due")
    if due and not contact:raise ValueError("Con «Da pagare» scegli il cliente: così resta segnato chi deve pagare.")
    if request.POST.get("copy")=="on":
        email=contact.data.get("email","") if contact else ""
        if not email:raise ValueError("Per la copia per email scegli un cliente con l'email nella scheda.")
        d.update(copy=True,email=email)
    parked_x=park(t,loc,contact,booking,later,key+"p",request.user,note) if has_rows(later) else None
    for x in taken:settled(t,x)
    sale=enqueue(t,loc,key=key,booking=booking,contact=contact,total=Decimal(total)/100,due=Decimal(due)/100,actor=str(request.user.pk),data=d)
    gift.reserve(t,sale)
    for l,c in zip(sold,codes):
        x=l["svc"][5:]
        GiftCard.objects.create(tenant=t,code=c,contact=contact,sale=sale,amount=Decimal(l["cents"])/100,balance=Decimal(l["cents"])/100,expires=date.fromisoformat(x) if x else None)
    Parked.objects.filter(pk__in=[p.pk for p in taken]).delete()
    if d.get("copy"):ConsentEvent.objects.create(tenant=t,contact=contact,kind="confirmed",purpose="receipt_copy",channel="desk",
        text_hash=__import__("core.privacy",fromlist=["text_hash"]).text_hash(COPY_TEXT),ref=str(sale.pk),actor=str(request.user.pk))
    audit(t,request.user,"sale.sent",sale.pk,total=str(sale.total),booking=str(booking.pk) if booking else "")
    return "Conto inviato alla cassa."+(f" Non nello scontrino: {euro(parked_x.total)}, tra i trattamenti del cliente." if parked_x else "")
def resumed(request,loc):
    """The suspended bills taken back into this one (?sospeso=, more than one), with the rows of a quote (?preventivo=)."""
    rows=list(Parked.objects.filter(pk__in=[x for x in request.GET.getlist("sospeso") if re.fullmatch(r"[0-9a-f-]{36}",x)],location=loc).select_related("contact").order_by("created"))
    lines=[];off=0
    for x in rows:
        d=x.data;off+=d.get("discount") or 0  # the discount on the whole bill comes back with it
        for l in d.get("lines",[]):
            lines.append({"desc":l["desc"],"qty":l["qty"],"price":money(l["cents"]),"svc":l.get("svc",""),"staff":l.get("staff",""),"off":(f"{l['pct']}%" if l.get("pct") else money(l["off"])) if l.get("off") else ""})
    return rows,lines,money(off) if off else ""

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
            if action in ("sell","park"):
                with transaction.atomic():
                    done=sell(request,t,loc,booking,action)
                if done:messages.success(request,done)
                return redirect(back)
            if action=="close":
                if loc.till["device"]=="rch" and not rch_cmd(loc.till,"rch_close"):raise ValueError("Su questa cassa la chiusura si fa dalla cassa.")
                enqueue(t,loc,kind="close",actor=str(request.user.pk));audit(t,request.user,"sale.close",loc.pk)
                messages.success(request,"Chiusura giornaliera inviata alla cassa.");return redirect(back)
            if action=="drop_parked":
                x=get_object_or_404(Parked,pk=request.POST.get("parked_id"),location=loc)
                audit(t,request.user,"sale.parked_dropped",x.pk,total=str(x.total));settled(t,x);x.delete()
                messages.success(request,f"Tolto: {euro(x.total)}. Il trattamento resta nella scheda del cliente.");return redirect(back)
            if action=="closed_by_hand":
                # 2.22.0: the closure done on the register: only noted, so the reminder goes away
                sale=Sale(tenant=t,location=loc,kind="close",status="done",printed=timezone.now(),error="Fatta dalla cassa",key=secrets.token_urlsafe(24),actor=str(request.user.pk))
                sale.pii=encrypt(t,{},"sale:"+str(sale.pk));sale.save();closed(loc);audit(t,request.user,"sale.close_by_hand",loc.pk)
                messages.success(request,"Segnata la chiusura fatta dalla cassa.");return redirect(back)
            if action=="void" and not request.POST.get("sale"):
                void(request,t,loc,None);return redirect(back)
            sale=get_object_or_404(Sale,pk=request.POST.get("sale"),location=loc)
            if action=="retry" and sale.status in ("failed","unknown"):
                # unknown: the operator has looked, nothing came out of the register
                if sale.kind=="sale":
                    with transaction.atomic():gift.reserve(t,sale)
                sale.status="queued";sale.error="";sale.claimed=None;sale.save(update_fields=["status","error","claimed"])
                if loc.till.get("link")=="direct" and settings.TILL_DIRECT:transaction.on_commit(lambda:kick(t.pk,loc.pk))
                audit(t,request.user,"sale.retry",sale.pk)
            elif action=="printed" and sale.status=="unknown":
                finish(t,sale,"done",{"number":re.sub(r"\D","",request.POST.get("number",""))[:20]})
                audit(t,request.user,"sale.printed_by_hand",sale.pk)
            elif action=="void" and sale.kind=="sale" and sale.status=="done":void(request,t,loc,sale)
            elif action=="settle":
                settle(t,request.user,sale,request.POST.get("how",""));messages.success(request,f"Segnato pagato: {euro(sale.due)}.")
            elif action=="mail" and sale.status in ("done","void"):
                messages.success(request,"Copia inviata per email." if send_copy(t,sale) else "Il cliente non ha chiesto la copia per email.")
            return redirect(back)
        except ValueError as ex:messages.error(request,str(ex));again=request.POST
    else:again=None
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
    items+=[{"group":"Prodotti","name":text(p.name),"price":str(p.price or "").replace(".",","),"svc":"p:"+str(p.pk)} for p in Product.objects.filter(active=True).order_by("name")]  # 2.22.0
    groups={}
    for i in items:groups.setdefault(i["group"],[]).append(i)
    top=favourites(t,loc,items)
    rows=list(Sale.objects.filter(location=loc,kind__in=("sale","void","close"),created__gte=today).select_related("booking","contact","voids").order_by("-created"))
    busy=any(s.status in ("queued","sending") for s in rows)
    due=list(owed(location=loc).select_related("contact").order_by("created"))  # 2.20.9: Da incassare, of every day
    names_ok=can(request,"pii.read")
    for s in due:s.who=" ".join(x for x in (s.contact.data.get("first_name",""),s.contact.data.get("last_name","")) if x) if names_ok and s.contact_id else "Cliente"
    for s in rows:s.who=" ".join(x for x in (s.contact.data.get("first_name",""),s.contact.data.get("last_name","")) if x) if names_ok and s.contact_id else ""  # 2.22.1: who paid, in Scontrini di oggi
    who=booking.data if booking and names_ok else {}
    lines=prefill(booking) if booking else []
    # 2.22.0: suspended bills and a quote taken into this bill; their client when the booking has none
    taken,extra,discount=resumed(request,loc);lines+=extra
    contact=booking.contact if booking and booking.contact_id else next((x.contact for x in taken if x.contact_id),None)
    quote=Quote.objects.filter(pk=request.GET["preventivo"]).select_related("contact").first() if re.fullmatch(r"[0-9a-f-]{36}",request.GET.get("preventivo","")) else None
    if quote:
        lines+=[{"desc":text(l["desc"]),"qty":l["qty"],"price":money(l["cents"]),"svc":l["ref"][2:] if l.get("ref","").startswith("s:") else l.get("ref","") if l.get("ref","").startswith("p:") else "","staff":""} for l in quote.data.get("lines",[]) if l.get("cents")]
        contact=contact or quote.contact
    # 2.22.0: from the Gift card page: the card as a row of the bill, its client, the receipt as always
    if request.GET.get("gift"):
        try:v=Decimal(request.GET["gift"].replace(",","."))
        except (InvalidOperation,ValueError):v=None
        if v is not None and 1<=v<=9999:
            exp=request.GET.get("scade","")
            try:exp=date.fromisoformat(exp).isoformat() if exp else ""
            except ValueError:exp=""
            lines.append({"desc":"GIFT CARD","qty":1,"price":money(int(v*100)),"svc":"gift:"+exp,"staff":""})
        if not contact and re.fullmatch(r"[0-9a-f-]{36}",request.GET.get("cliente","")):contact=Contact.objects.filter(pk=request.GET["cliente"]).first()
    if contact and not who and names_ok:who=contact.data
    ops=list(Resource.objects.filter(location=loc,active=True).order_by("name"));names=[o.name for o in ops]
    # 2.22.1: the operators as a bar beside the services, Exagon style: tap the service, tap who did it; the agenda's colours and faces
    from .agenda import operators,colors
    tone=colors(operators(request,loc));staff=[{"name":o.name,"color":tone.get(o.pk,0),"photo":o.photo_link(t.slug)} for o in ops]
    parked=list(Parked.objects.filter(location=loc).select_related("contact","booking").order_by("-created"))
    for x in parked:
        d=x.data;x.lines=d.get("lines",[]);x.note=d.get("note","");x.who=" ".join(v for v in (x.contact.data.get("first_name",""),x.contact.data.get("last_name","")) if v) if x.contact_id and names_ok else ("Cliente" if x.contact_id else "")
    ids={str(x.pk) for x in taken}
    waiting=[x for x in parked if contact and x.contact_id==contact.pk and str(x.pk) not in ids]  # the client of this bill has more to pay
    if again is not None:
        # 2.22.0: the bill as it was typed, the refused one: rows, discount, client, so the person fixes the one thing and sends again
        n=len(again.getlist("desc"));col=lambda k:(again.getlist(k)+[""]*n)[:n]
        lines=[{"desc":d,"qty":q or 1,"price":p,"off":o,"staff":st,"svc":sv,"park":pk=="1"} for d,q,p,o,st,sv,pk in zip(col("desc"),col("qty"),col("price"),col("off"),col("staff"),col("svc"),col("park")) if d.strip() or p.strip()]
        discount=again.get("discount","");taken=[]
        if again.get("contact") and can(request,"contacts.read"):contact=Contact.objects.filter(pk=again["contact"]).first() or contact
    today_d=timezone.now().astimezone(tz).date()
    return render(request,"cassa.html",{"where":where,"loc":loc,"groups":groups,"top":top,"count":len(items),"booking":booking,"who":who,"lines":lines,
        "taken":taken,"parked":parked,"waiting":waiting,"quote":quote,"discount":discount,"gift_presets":gift.PRESETS,"gift_expires":gift.proposed(today_d),
        "gifts":[c for c in gift.of(contact) if c.status=="active" and c.balance>0 and not (c.expires and c.expires<today_d)] if contact else [],
        "missing_close":missing_close(t,loc),"today":today_d,"month_start":today_d.replace(day=1),
        "staff_names":names,"staff":staff,"default_staff":booking.resource.name if booking and booking.resource_id else "",
        "key":secrets.token_urlsafe(24),"payments":PAYMENTS,"settle":SETTLE,"due":due,"rows":rows,"busy":busy,"pick_client":can(request,"contacts.read") and can(request,"pii.read"),
        "contact":contact if contact and names_ok else None,"can_void":loc.till["device"]=="epson" or bool(rch_cmd(loc.till,"rch_void")),"counted":loc.till["device"]=="rch","can_close":loc.till["device"]=="epson" or bool(rch_cmd(loc.till,"rch_close")),
        "cf":receipt_code(contact.data) if contact and names_ok else ""})
def missing_close(t,loc):
    """2.22.0: the last day with receipts emitted here and no daily closure after them, before today: the register
    wants the closure of that day before it emits again. The closure counts when it is emitted, or marked done by hand."""
    tz=ZoneInfo(t.timezone);today=datetime.combine(timezone.now().astimezone(tz).date(),datetime.min.time(),tz)
    last=Sale.objects.filter(location=loc,kind="close",status="done",printed__isnull=False).order_by("-printed").values_list("printed",flat=True).first()
    q=Sale.objects.filter(location=loc,kind="sale",status__in=("done","void"),printed__lt=today)
    if last:q=q.filter(printed__gt=last)
    s=q.order_by("-printed").values_list("printed",flat=True).first()
    return s.astimezone(tz).date() if s else None

def summary(request,slug):
    """2.22.1: Riepilogo di cassa in PDF, beside the closure: one day, or a period (?dal=&al=), everything the register
    did day by day: the receipts with lines, who paid and how, the annulli, the closures, then the day's totals by
    payment and by operator, the «Da pagare» collected and what was done «non nello scontrino». A download, nothing
    the window waits on."""
    require(request,"sale.write")
    t=request.tenant;tz=ZoneInfo(t.timezone);today=timezone.now().astimezone(tz).date()
    loc=next((l for l in places() if str(l.pk)==request.GET.get("sede","")),None) or places()[0]
    def day(k,default):
        try:return date.fromisoformat(request.GET.get(k,""))
        except ValueError:return default
    a=day("dal",today);b=day("al",a)
    if b<a:a,b=b,a
    b=min(b,a+timedelta(days=366))
    start=datetime.combine(a,datetime.min.time(),tz);end=datetime.combine(b+timedelta(days=1),datetime.min.time(),tz)
    names_ok=can(request,"pii.read")
    who=lambda s:(" ".join(x for x in (s.contact.data.get("first_name",""),s.contact.data.get("last_name","")) if x) if names_ok else "Cliente") if s.contact_id else ""
    sales=list(Sale.objects.filter(location=loc,kind__in=("sale","void","close"),status__in=("done","void","unknown"),created__gte=start,created__lt=end).select_related("contact","voids").order_by("created"))
    paid=list(Sale.objects.filter(location=loc,kind="sale",settled__gte=start,settled__lt=end).select_related("contact").order_by("settled"))
    parked=list(Parked.objects.filter(location=loc,created__gte=start,created__lt=end).select_related("contact").order_by("created"))
    from .pdf import Doc,A4,MARGIN
    from django.utils.formats import date_format
    L,R=MARGIN,A4[0]-MARGIN;period=f"{a:%d/%m/%Y}" if a==b else f"dal {a:%d/%m/%Y} al {b:%d/%m/%Y}"
    doc=Doc(f"Riepilogo di cassa {period}");doc.page()
    doc.line([(L,f"Riepilogo di cassa · {period}")],16,True,gap=18);doc.line([(L,f"{t.name} · {loc.name} · stampato il {timezone.now().astimezone(tz):%d/%m/%Y %H:%M}")],9);doc.space()
    e=lambda c:euro(Decimal(c)/100);total_all={"n":0,"cents":0,"pay":{},"staff":{}}
    days=sorted({x.created.astimezone(tz).date() for x in sales}|{x.settled.astimezone(tz).date() for x in paid}|{x.created.astimezone(tz).date() for x in parked})
    for d in days:
        doc.rule();doc.line([(L,date_format(d,"l j F Y").capitalize())],12,True,gap=16)
        tot={"n":0,"cents":0,"pay":{},"staff":{}}
        for s in [x for x in sales if x.created.astimezone(tz).date()==d]:
            when=(s.printed or s.created).astimezone(tz).strftime("%H:%M");state={"void":"annullato","unknown":"da verificare"}.get(s.status,"")
            if s.kind=="close":doc.line([(L,when),(L+40,"Chiusura giornaliera"+(" fatta dalla cassa" if s.error=="Fatta dalla cassa" else ""))],10,True);continue
            if s.kind=="void":
                v=s.voids;doc.line([(L,when),(L+40,f"Annullo del n. {(v.z+'-' if v.z else '')+v.number if v and v.number else s.data.get('number','')}"),(R,"-"+euro(s.total),True)],10,True);continue
            doc.line([(L,when),(L+40,f"Scontrino n. {(s.z+'-' if s.z else '')+s.number}" if s.number else "Scontrino"),(L+170,who(s)),(R,euro(s.total)+(f" ({state})" if state else ""),True)],10,True)
            dd=s.data
            for l in dd.get("lines",[]):doc.line([(L+40,f"{l['qty']} x {l['desc']}"+(f" · {l['staff']}" if l.get("staff") else "")),(R,e(l["qty"]*l["cents"]-l.get("off",0)),True)],9,gap=12)
            if dd.get("discount"):doc.line([(L+40,"Sconto sul totale"),(R,"-"+e(dd["discount"]),True)],9,gap=12)
            if gift_cents(dd):doc.line([(L+40,"Pagato con gift card"),(R,"-"+e(gift_cents(dd)),True)],9,gap=12)
            doc.line([(L+40,"Pagamento: "+", ".join(f"{dict(PAYMENTS).get(k,k)} {e(c)}" for k,c in dd.get("payments",[])))],9,gap=12)
            if s.status=="void":continue
            tot["n"]+=1;cents=int(s.total*100);tot["cents"]+=cents
            for k,c in dd.get("payments",[]):tot["pay"][k]=tot["pay"].get(k,0)+c
            if gift_cents(dd):tot["pay"]["gift"]=tot["pay"].get("gift",0)+gift_cents(dd)
            for k,c in shares(dd,cents+gift_cents(dd)).items():tot["staff"][k or "Senza operatore"]=tot["staff"].get(k or "Senza operatore",0)+c
        for s in [x for x in paid if x.settled.astimezone(tz).date()==d]:
            doc.line([(L,s.settled.astimezone(tz).strftime("%H:%M")),(L+40,f"Incassato il Da pagare dello scontrino n. {(s.z+'-' if s.z else '')+s.number} del {s.printed.astimezone(tz):%d/%m}" if s.number else "Incassato un Da pagare"),(L+300,who(s)),(R,euro(s.due)+" "+dict(SETTLE).get(s.settled_with,"").lower(),True)],9)
            tot["pay"]["settled"]=tot["pay"].get("settled",0)+int(s.due*100)
        for x in [x for x in parked if x.created.astimezone(tz).date()==d]:
            doc.line([(L,x.created.astimezone(tz).strftime("%H:%M")),(L+40,"Non nello scontrino"),(L+170,who(x)),(R,euro(x.total),True)],9)
            for l in x.data.get("lines",[]):doc.line([(L+40,f"{l['qty']} x {l['desc']}"+(f" · {l['staff']}" if l.get("staff") else "")),(R,e(l["qty"]*l["cents"]-l.get("off",0)),True)],9,gap=12)
        doc.space(0.5);doc.line([(L+40,f"Scontrini: {tot['n']}"),(R,"Totale "+e(tot["cents"]),True)],10,True)
        labels={**dict(PAYMENTS),"gift":"Gift card","settled":"Da pagare incassati"}
        for k,c in tot["pay"].items():doc.line([(L+40,labels.get(k,k)),(R,e(c),True)],9,gap=12)
        for k,c in sorted(tot["staff"].items(),key=lambda i:-i[1]):doc.line([(L+40,"Operatore "+k),(R,e(c),True)],9,gap=12)
        total_all["n"]+=tot["n"];total_all["cents"]+=tot["cents"]
        for k in ("pay","staff"):
            for kk,c in tot[k].items():total_all[k][kk]=total_all[k].get(kk,0)+c
    if not days:doc.line([(L,"Nessuno scontrino in questi giorni.")],10)
    if len(days)>1:
        doc.space();doc.rule();doc.line([(L,f"Totale del periodo · scontrini: {total_all['n']}"),(R,e(total_all["cents"]),True)],12,True,gap=16)
        for k,c in total_all["pay"].items():doc.line([(L+40,labels.get(k,k)),(R,e(c),True)],9,gap=12)
        for k,c in sorted(total_all["staff"].items(),key=lambda i:-i[1]):doc.line([(L+40,"Operatore "+k),(R,e(c),True)],9,gap=12)
    audit(t,request.user,"sale.summary",None,day=str(a),until=str(b))
    from django.http import HttpResponse
    r=HttpResponse(doc.render(),content_type="application/pdf");r["Content-Disposition"]=f'attachment; filename="riepilogo-cassa-{a:%Y-%m-%d}{"" if a==b else f"-{b:%Y-%m-%d}"}.pdf"';return r
def favourites(t,loc,items,n=12):
    """2.20.8: the services this location uses most, from the last receipts and the bookings of the last months,
    so the bill is there without scrolling; with no history yet, the first ones of the list."""
    from collections import Counter
    from .crypto import open_seal,tenant_key
    used=Counter();key=tenant_key(t)
    for sale in Sale.objects.filter(location=loc,kind="sale").order_by("-created")[:300]:
        try:lines=open_seal(sale.pii,key,f"{t.pk}:sale:{sale.pk}").get("lines",[])
        except Exception:continue
        for l in lines:used[(l.get("svc") or "",l.get("desc",""))]+=l.get("qty",1)
    since=timezone.now()-timedelta(days=120)
    for svc,k in Booking.objects.filter(location=loc,start__gte=since).exclude(status="cancelled").values_list("service_id").annotate(k=models.Count("pk")):used[(str(svc),"")]+=k
    score=lambda i:used.get((i["svc"],i["name"]),0)+used.get((i["svc"],""),0)
    ranked=sorted((i for i in items if score(i)),key=lambda i:-score(i))
    return (ranked+[i for i in items if not score(i)])[:n]  # the used ones first, then the list fills the rest
DOC=re.compile(r"\s*(?:(\d{1,4})\s*-)?\s*(\d{1,4})\s*")  # 2.22.0: «2026-0012» or just «12» (RCH, no closure number)
def void(request,t,loc,sale):
    """The annullo of a documento commerciale: of one in the list, or of any other written by hand (day and «Documento n.»,
    closure and number as printed at its foot). On RCH with no annullo command the desk does it on the register and it is
    only marked here."""
    m=DOC.fullmatch(request.POST.get("doc","") or (f"{sale.z}-{sale.number}" if sale and sale.z and sale.number else sale.number if sale and sale.number else ""))
    try:day=date.fromisoformat(request.POST.get("day","")) if request.POST.get("day") else sale.printed.astimezone(ZoneInfo(t.timezone)).date()
    except (ValueError,AttributeError):day=None
    if loc.till["device"]=="rch" and not rch_cmd(loc.till,"rch_void"):
        if not sale:raise ValueError("Su questa cassa l'annullo si fa dalla cassa.")
        sale.status="void";sale.save(update_fields=["status"]);audit(t,request.user,"sale.void",sale.pk)
        gift.give_back(t,sale);gift.sold(t,sale,"void")
        messages.success(request,"Segnato come annullato.");return
    if not (m and day) or day>timezone.now().astimezone(ZoneInfo(t.timezone)).date():raise ValueError("Scrivi la data e il «Documento n.» dello scontrino, come sono stampati in fondo: per esempio 2026-0012.")
    if loc.till["device"]=="epson" and not (sale and sale.serial or loc.till.get("serial")):raise ValueError("Manca la matricola della cassa: scrivila in Registratore di cassa.")
    if loc.till["device"]=="epson" and not (m[1] or sale and sale.z):raise ValueError("Su Epson serve anche il numero della chiusura, come stampato in fondo: per esempio 2026-0012.")
    if sale and sale.voided_by.exclude(status="failed").exists():raise ValueError("L'annullo è già stato inviato.")
    v=enqueue(t,loc,kind="void",voids=sale,total=sale.total if sale else 0,actor=str(request.user.pk),data={"z":m[1] or "","number":m[2],"day":day.isoformat()})
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
                    for k in ("product_dept","gift_dept"):  # 2.22.0: empty, the one of the free lines
                        if number(k):conf[k]=number(k)
                    if device=="rch":
                        v=request.POST.get("last_z","").strip()  # 2.22.0: the closure number the documents are counted from
                        if v and not (v.isdigit() and int(v)<=9999):raise ValueError("Numero dell'ultima chiusura: da 0 a 9999, come stampato sulla chiusura.")
                        if v:conf["last_z"]=int(v)
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
            "depts_set":sum(1 for k,d in deps.items() if d.get("dept") or d.get("vat")),  # 2.22.0: the table stays closed unless something is set
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
