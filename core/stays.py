"""2.14.0: hotel, B&B and holiday homes. The calendar of each unit for the portals (.ics) and theirs read back as
closures; rules for a period; the day at the desk with cleaning and breakfasts; the guests, sealed in the booking, typed
at the desk or by the client from his link; the Alloggiati Web file or its web service with the receipts, the ISTAT movements for Ross1000, the tourist tax by the
town's rules. The Ross1000 file is downloaded and loaded by the business."""
import base64,csv,ipaddress,re,socket,unicodedata,urllib.error,urllib.parse,urllib.request
import xml.etree.ElementTree as ET
from datetime import date,datetime,timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path
from xml.sax.saxutils import escape as xml
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse,Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from .models import Tenant,Location,Resource,Service,ServiceResource,ExceptionDay,Booking,ActionToken,Receipt,audit
from .crypto import encrypt,decrypt,index,tenant_key,token_hash
from .booking import BookingError,ACCEPTED
from .calendar import covering
from .tenancy import scope
from .ics import fold,utc
from .identity import throttle
from .permissions import require,can,shown,visible_bookings

WEEKDAYS=["lunedì","martedì","mercoledì","giovedì","venerdì","sabato","domenica"]
STAYING=("pending",)+ACCEPTED  # what holds a unit for the portals; the files and the tax count the accepted ones only
KINDS=[("16","Ospite singolo"),("17","Capofamiglia"),("18","Capogruppo"),("19","Familiare"),("20","Membro del gruppo")]
HEADS=("16","17","18")  # who shows a document; family and group members travel on their head's
# Ross1000, asked once a stay at check-in: the values of its tracciato (XML 2.4)
TOURISM=["Culturale","Balneare","Congressuale/Affari","Fieristico","Sportivo/Fitness","Scolastico","Religioso","Sociale","Parchi Tematici",
    "Termale/Trattamenti salute","Enogastronomico","Cicloturismo","Escursionistico/Naturalistico","Altro motivo","Non specificato"]
TRANSPORT=["Auto","Aereo","Aereo+Pullman","Aereo+Navetta/Taxi/Auto","Aereo+Treno","Treno","Pullman","Caravan/Autocaravan","Barca/Nave/Traghetto",
    "Moto","Bicicletta","A piedi","Altro mezzo","Non specificato"]
# the tourist tax: why a guest does not pay (or pays less), chosen for each guest; workers and students pay up to the
# town's nights a year, escorts one every tax_escort guests (Roma: DAC 34/2024 artt. 5-6)
EXEMPTIONS=[("","Paga"),("resident","Residente nel comune"),("worker","Lavoro (notti massime all'anno)"),("student","Studio (notti massime all'anno)"),
    ("escort","Accompagnatore turistico del gruppo"),("driver","Autista del pullman"),("care","Cure, assistenza a un malato"),("disability","Disabilità grave o suo accompagnatore"),
    ("staff","Personale della struttura"),("duty","Forze dell'ordine o soccorso in servizio"),("emergency","Emergenza, ospite delle autorità"),
    ("reduced","Riduzione (gruppi scolastici o sportivi)"),("other","Altro motivo di esenzione")]
TAX_CATEGORIES=[("albergo1","Albergo 1 stella"),("albergo2","Albergo 2 stelle"),("albergo3","Albergo 3 stelle"),("albergo4","Albergo 4 stelle"),("albergo5","Albergo 5 stelle e lusso"),
    ("rta","Residenza turistico-alberghiera"),("agriturismo","Agriturismo"),("affittacamere1","Affittacamere o guest house cat. 1"),("affittacamere2","Affittacamere o guest house cat. 2"),
    ("affittacamere3","Affittacamere o guest house cat. 3"),("ostello","Ostello"),("cav1","Casa vacanze cat. 1"),("cav2","Casa vacanze cat. 2"),("bb","Bed and breakfast"),
    ("casaferie","Casa per ferie"),("country","Country house"),("rifugio","Rifugio"),("uso_turistico","Alloggio per uso turistico"),("locazione","Locazione breve"),("campeggio","Campeggio o villaggio")]

def restricted(service,first,last):
    """The rules of the periods holding the arrival and the departure: minimum nights, no arrivals or departures on
    some weekdays. Empty when the stay is allowed."""
    for r in service.stay_rules:
        if r["from"]<=first.isoformat()<=r["until"]:
            if first.weekday() in r["no_arrival"]:return f"In questo periodo non si arriva di {WEEKDAYS[first.weekday()]}."
            if (last-first).days<r["min"]:return f"In questo periodo il soggiorno minimo è di {r['min']} notti."
        if r["from"]<=last.isoformat()<=r["until"] and last.weekday() in r["no_departure"]:return f"In questo periodo non si parte di {WEEKDAYS[last.weekday()]}."
    return ""

def local(moment,tz):return moment.astimezone(tz).date()

# ---- calendars of the portals -------------------------------------------------------------------------------------
def feed_token(tenant,unit):return index("ical:"+str(unit.pk),tenant_key(tenant),"ical-feed")[:32]
def feed_link(tenant,unit):return f"{settings.PUBLIC_ORIGIN}/book/{tenant.slug}/calendario/{unit.pk}/{feed_token(tenant,unit)}.ics"

def feed(tenant,unit,now=None):
    """The busy nights of a unit, all day and without names: its stays and the closures over it. The nights read from
    a portal stay out, or two portals would hand each other's back."""
    now=now or timezone.now();tz=ZoneInfo(tenant.timezone);today=local(now,tz);events=[]
    for b in Booking.objects.filter(resource=unit,status__in=STAYING,end__gte=now):
        a=local(b.start,tz);events.append((b.pk,a,max(local(b.end,tz),a+timedelta(days=1))))
    services=ServiceResource.objects.filter(resource=unit).values("service_id")
    rows=ExceptionDay.objects.filter(Q(resource=unit)|Q(service_id__in=services,resource=None)|Q(location=unit.location,service=None,resource=None)
        |Q(location=None,service=None,resource=None),covering(today,today+timedelta(days=730)),purpose="service",closed=True,feed="")
    events+=[(c.pk,c.date,(c.until or c.date)+timedelta(days=1)) for c in rows]
    host=settings.PUBLIC_ORIGIN.split("://",1)[-1].split("/")[0].split(":")[0]
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Tastiere Digitali//TD Booking Suite//IT","METHOD:PUBLISH"]
    for uid,a,z in sorted(events,key=lambda e:e[1]):
        lines+=["BEGIN:VEVENT",f"UID:{uid}@{host}","DTSTAMP:"+utc(now),f"DTSTART;VALUE=DATE:{a:%Y%m%d}",f"DTEND;VALUE=DATE:{z:%Y%m%d}","SUMMARY:Non disponibile","END:VEVENT"]
    return "\r\n".join(fold(x) for x in lines+["END:VCALENDAR"])+"\r\n"

def parse(text):
    """The nights of each event of a calendar file, as (first night, departure day)."""
    out=[];a=z=None
    for line in re.sub(r"\r?\n[ \t]","",text).splitlines():
        name,_,value=line.partition(":");name=name.split(";")[0].strip().upper();value=value.strip()
        try:
            if name=="BEGIN" and value=="VEVENT":a=z=None
            elif name=="DTSTART":a=datetime.strptime(value[:8],"%Y%m%d").date()
            elif name=="DTEND":z=datetime.strptime(value[:8],"%Y%m%d").date()
            elif name=="END" and value=="VEVENT" and a:out.append((a,z if z and z>a else a+timedelta(days=1)))
        except ValueError:a=None  # a broken date: that event is skipped
    return out

def fetch(url):
    """A portal's calendar: https, never an address of the server's own network."""
    parts=urllib.parse.urlsplit(url)
    if parts.scheme!="https" or not parts.hostname:raise ValueError("indirizzo non valido")
    for *_,addr in socket.getaddrinfo(parts.hostname,parts.port or 443):
        if not ipaddress.ip_address(addr[0]).is_global:raise ValueError("indirizzo non raggiungibile")
    # shortcut: a redirect is followed without the check above; portals answer directly, recheck if one ever redirects
    with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"TD Booking Suite"}),timeout=20) as r:
        return r.read(3_000_000).decode("utf-8","replace")

def host(url):return urllib.parse.urlsplit(url).hostname or "calendario"
def feeds(tenant,unit):return decrypt(tenant,unit.ical_in,"ical:"+str(unit.pk)) if unit.ical_in else []
def feed_key(tenant,url):return index(url,tenant_key(tenant),"ical-in")[:16]

def set_feeds(tenant,unit,text):
    """The addresses of the portals' calendars of a unit, sealed (they hold the portal's secret)."""
    urls=list(dict.fromkeys(text.split()))
    if bad:=[u for u in urls if not re.fullmatch(r"https://\S{4,600}",u)]:raise BookingError(f"Indirizzo non valido: {bad[0][:60]}. Copialo intero dal portale, inizia con https://")
    unit.ical_in=encrypt(tenant,urls,"ical:"+str(unit.pk)) if urls else "";unit.save(update_fields=["ical_in"])
    ExceptionDay.objects.filter(resource=unit).exclude(feed="").exclude(feed__in=[feed_key(tenant,u) for u in urls]).delete()

def refresh(tenant,unit,now=None):
    """Reads the unit's portal calendars again: their nights become closures of the unit, replaced at every reading.
    A calendar that does not answer keeps its last nights: better a night closed than a double booking."""
    now=now or timezone.now();today=local(now,ZoneInfo(tenant.timezone));read={};errors=[]
    for url in feeds(tenant,unit):
        try:read[feed_key(tenant,url)]=(host(url),parse(fetch(url)))
        except (OSError,ValueError):errors.append(host(url))
    with scope(tenant):
        for key,(name,nights) in read.items():
            ExceptionDay.objects.filter(resource=unit,feed=key).delete()
            ExceptionDay.objects.bulk_create([ExceptionDay(tenant=tenant,resource=unit,date=a,until=z-timedelta(days=1) if (z-a).days>1 else None,
                label=("Occupato su "+name)[:100],feed=key) for a,z in nights if z>today])
        unit.ical_at=now;unit.ical_error=("Non risponde: "+", ".join(errors))[:200] if errors else ""
        unit.save(update_fields=["ical_at","ical_error"])
    return not errors

def run(now=None):
    """The worker, every hour: the units with portal calendars, read again. Returns how many answered."""
    from .middleware import operational
    done=0
    for tenant in Tenant.objects.all():
        if not operational(tenant):continue
        with scope(tenant):units=list(Resource.objects.exclude(ical_in=""))
        for unit in units:done+=refresh(tenant,unit,now)
        if conf:=ws_conf(tenant):
            # 2.14.0: Alloggiati Web, the arrivals of yesterday and today and the receipts; what goes wrong is shown in Alloggi
            with scope(tenant):
                try:
                    today=local(now or timezone.now(),ZoneInfo(tenant.timezone))
                    if conf.get("auto"):
                        for day in (today-timedelta(days=1),today):ws_send(tenant,day,auto=True,now=now)
                    receipts(tenant,conf,now);error=""
                except WSError as ex:error=str(ex)
                if error!=conf.get("error",""):ws_save(tenant,{**conf,"error":error})
    return done

# ---- guests and the Alloggiati tables ------------------------------------------------------------------------------
DATA=Path(__file__).parent/"data"/"alloggiati"  # the tables of the Polizia di Stato, 31/10/2026: towns change every January
@cache
def table(name):
    with open(DATA/f"{name}.csv",encoding="ascii",newline="") as f:return list(csv.DictReader(f))
def plain(text):
    """ASCII in capitals, accents dropped, as the Alloggiati file and its tables are written."""
    return unicodedata.normalize("NFKD",str(text)).encode("ascii","ignore").decode().upper()
def key(text):return re.sub(r"[^A-Z]","",plain(text))
@cache
def places():
    """Name or code -> (code, province; ES for a state). States first, then the towns in force, then the ceased ones
    (still good as a birthplace)."""
    out={}
    for name in ("stati","comuni"):
        for r in sorted(table(name),key=lambda r:bool(r["DataFineVal"])):
            out.setdefault(key(r["Descrizione"]),(r["Codice"],r["Provincia"]));out.setdefault(r["Codice"],(r["Codice"],r["Provincia"]))
    out["ITALIANA"]=out["ITALIANO"]=out["ITALIA"]
    return out
def place(text):
    t=str(text).strip()
    return places().get(t if t.isdigit() else key(t))
@cache
def documents():return [(r["Codice"],r["Descrizione"].capitalize()) for r in table("documenti")]

GUEST=("kind","last_name","first_name","sex","birth_date","birth_place","citizenship","residence","doc_type","doc_number","doc_place")
def read_guests(post,party):
    """The guests typed in, checked: towns and states must be in the tables, the document is asked of the heads."""
    out=[]
    for i in range(party):
        g={k:str(post.get(f"g{i}-{k}","")).strip()[:60] for k in GUEST}
        if not (g["last_name"] or g["first_name"]):continue
        who=f"Ospite {i+1}: "
        if g["kind"] not in dict(KINDS):g["kind"]="16"
        if not (g["last_name"] and g["first_name"]):raise BookingError(who+"scrivi nome e cognome.")
        if g["sex"] not in ("M","F"):raise BookingError(who+"indica il sesso.")
        try:born=date.fromisoformat(g["birth_date"])
        except ValueError:raise BookingError(who+"data di nascita non valida.")
        if not date(1900,1,1)<=born<=timezone.now().date():raise BookingError(who+"data di nascita non valida.")
        for f,label in (("birth_place","luogo di nascita"),("citizenship","cittadinanza"),("residence","residenza")):
            if not place(g[f]):raise BookingError(who+f"{label} non trovato. Scrivi il comune italiano o lo Stato estero per intero, per esempio Roma o Germania.")
        if g["kind"] in HEADS:
            if g["doc_type"] not in dict(documents()) or not re.fullmatch(r"[A-Za-z0-9./-]{3,20}",g["doc_number"]):raise BookingError(who+"scegli il documento e scrivi il numero.")
            if not place(g["doc_place"]):raise BookingError(who+"luogo di rilascio del documento non trovato (comune o Stato).")
        else:g["doc_type"]=g["doc_number"]=g["doc_place"]=""
        g["exemption"]=post.get(f"g{i}-exemption","") if post.get(f"g{i}-exemption","") in dict(EXEMPTIONS) else ""
        out.append(g)
    if out and out[0]["kind"] in ("19","20"):raise BookingError("Il primo ospite è il capofamiglia, il capogruppo o un ospite singolo.")
    return out

def read_trip(post):
    """Why and how the guests came (Ross1000), once for the stay."""
    pick=lambda v,values:v if v in [x.upper() for x in values] else "NON SPECIFICATO"
    return {"tourism":pick(post.get("tourism",""),TOURISM),"transport":pick(post.get("transport",""),TRANSPORT)}

def save_guests(tenant,booking,guests,actor=None,trip=None):
    data=booking.data;data["guests"]=guests;data.update(trip or {})
    booking.pii=encrypt(tenant,data,"booking:"+str(booking.pk));booking.snapshot.pop("alloggiati_error",None)  # changed: the worker may try again
    booking.save(update_fields=["pii","snapshot"])
    audit(tenant,actor,"booking.guests",booking.pk,guests=len(guests))

def guest_rows(booking,post=None):
    """The forms of a booking's guests: as typed, or as saved, one for each person."""
    saved=booking.data.get("guests") or []
    rows=[]
    for i in range(booking.party):
        g={k:post.get(f"g{i}-{k}","") for k in GUEST+("exemption",)} if post else (saved[i] if i<len(saved) else {"kind":"16" if booking.party==1 else "17" if i==0 else "19"})
        rows.append({"i":i,"g":g})
    return rows

# ---- the files ---------------------------------------------------------------------------------------------------
def pad(text,n):return plain(text)[:n].ljust(n)
def alloggiati_line(g,arrival,nights):
    """One guest, 168 characters (Alloggiati Web, Tabella 1)."""
    born=place(g["birth_place"]);italy=born[1]!="ES";head=g["kind"] in HEADS
    line=(g["kind"]+f"{arrival:%d/%m/%Y}{min(30,max(1,nights)):02d}"+pad(g["last_name"],50)+pad(g["first_name"],30)+("1" if g["sex"]=="M" else "2")
        +f"{date.fromisoformat(g['birth_date']):%d/%m/%Y}"+(born[0] if italy else "").ljust(9)+(born[1] if italy else "").ljust(2)
        +("100000100" if italy else born[0])+place(g["citizenship"])[0]
        +(pad(g["doc_type"],5)+pad(g["doc_number"],20)+place(g["doc_place"])[0] if head else " "*34))
    assert len(line)==168,len(line)
    return line

def stays(tenant,first,last,location=None):
    """The accepted stays that touch the days from first to last."""
    tz=ZoneInfo(tenant.timezone)
    rows=Booking.objects.filter(service__kind="day",status__in=ACCEPTED,start__lt=datetime.combine(last+timedelta(days=2),datetime.min.time(),tz),
        end__gt=datetime.combine(first-timedelta(days=1),datetime.min.time(),tz)).select_related("service","location","resource").order_by("start")
    return rows.filter(location=location) if location else rows

def alloggiati(tenant,day):
    """The Alloggiati Web file of the arrivals of a day, and the stays left out because their guests are missing."""
    tz=ZoneInfo(tenant.timezone);lines=[];missing=[]
    for b in stays(tenant,day,day):
        if local(b.start,tz)!=day:continue
        guests=b.data.get("guests") or []
        if not guests:missing.append(b);continue
        lines+=[alloggiati_line(g,day,(local(b.end,tz)-day).days) for g in guests]
    return "\r\n".join(lines[:1000]),missing  # the portal takes 1000 rows a file: no small structure gets there in a day

def forget(tenant,b):
    data=b.data;data["guests"]=[{**g,"doc_type":"","doc_number":"","doc_place":""} for g in data["guests"]]
    b.pii=encrypt(tenant,data,"booking:"+str(b.pk));b.snapshot["alloggiati"]=timezone.now().isoformat();b.snapshot.pop("alloggiati_error",None)
    b.save(update_fields=["pii","snapshot"])

def sent(tenant,day,actor=None):
    """The Alloggiati file of a day is sent: the documents of its guests are deleted (DM 7/1/2013 art. 4-bis);
    what ISTAT and the tax need stays."""
    tz=ZoneInfo(tenant.timezone);n=0
    for b in stays(tenant,day,day):
        if local(b.start,tz)!=day or not b.data.get("guests"):continue
        forget(tenant,b);n+=1
    audit(tenant,actor,"stays.alloggiati_sent",day.isoformat(),bookings=n)
    return n

# ---- Alloggiati Web, its web service (MANUALEWS rev. 01, 24/01/2022) ----------------------------------------------
WS="https://alloggiatiweb.poliziadistato.it/service/service.asmx"
class WSError(Exception):pass
def ws_conf(tenant):return decrypt(tenant,tenant.alloggiati,"alloggiati") if tenant.alloggiati else {}

def ws_post(method,body):
    """One SOAP 1.1 call, going out only: it works the same from the Windows edition."""
    envelope=(f'<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        f'<{method} xmlns="AlloggiatiService">{body}</{method}></soap:Body></soap:Envelope>')
    request=urllib.request.Request(WS,data=envelope.encode(),headers={"Content-Type":"text/xml; charset=utf-8","SOAPAction":f'"AlloggiatiService/{method}"'})
    try:
        with urllib.request.urlopen(request,timeout=60) as r:return r.read(20_000_000).decode("utf-8","replace")
    except urllib.error.HTTPError as ex:return ex.read(200_000).decode("utf-8","replace")  # a SOAP fault comes with a 500
    except OSError:raise WSError("Alloggiati Web non risponde: riprova più tardi o scarica il file e caricalo sul portale.")

def ws(method,**args):
    """The answer of a call: its elements by name, without namespaces."""
    body="".join(f"<{k}>"+("".join(f"<string>{xml(x)}</string>" for x in v) if isinstance(v,list) else xml(str(v)))+f"</{k}>" for k,v in args.items())
    try:root=ET.fromstring(ws_post(method,body))
    except ET.ParseError:raise WSError("Alloggiati Web ha risposto in modo non valido: riprova più tardi.")
    find=lambda name:[e for e in root.iter() if e.tag.rsplit("}",1)[-1]==name]
    if fault:=find("faultstring"):raise WSError("Alloggiati Web: "+(fault[0].text or "errore")[:200])
    return find

def outcome(e):
    """An EsitoOperazioneServizio: done, and why not."""
    get=lambda n:next((c.text or "" for c in e if c.tag.rsplit("}",1)[-1]==n),"")
    return get("esito")=="true",(get("ErroreDes")+" "+get("ErroreDettaglio")).strip()[:200]

def ws_token(conf):
    find=ws("GenerateToken",Utente=conf["user"],Password=conf["password"],WsKey=conf["wskey"])
    ok,why=outcome(find("result")[0]) if find("result") else (False,"")
    token=next((e.text for e in find("token") if e.text),"")
    if not ok or not token:raise WSError(f"Alloggiati Web non accetta l'accesso ({why or 'nessun motivo'}): controlla utente, password e chiave web service. Dopo ogni cambio di password serve una chiave nuova.")
    return token

def ws_rows(conf,token,method,rows):
    """Test or Send: the outcome of each row, in order."""
    find=ws(method,Utente=conf["user"],token=token,ElencoSchedine=rows)
    out=[outcome(e) for e in find("EsitoOperazioneServizio")]
    if len(out)!=len(rows):
        why=outcome(find(method+"Result")[0])[1] if find(method+"Result") else ""
        raise WSError("Alloggiati Web ha rifiutato l'invio"+(f": {why}" if why else "")+".")
    return out

def ws_send(tenant,day,actor=None,auto=False,now=None):
    """The arrivals of a day not sent yet, to Alloggiati Web: first Test, then Send of the stays whose rows are all good,
    so a family never goes without its head (Send takes the good rows only). Those taken lose their documents at once
    (DM 7/1/2013 art. 4-bis) and count in the receipt of today; the others keep the reason, shown at the desk. The
    worker (auto) sends a stay once its arrival time has come and leaves the refused ones until they are corrected.
    Returns (stays sent, reasons of the refused)."""
    conf=ws_conf(tenant);tz=ZoneInfo(tenant.timezone);now=now or timezone.now();today=local(now,tz)
    if not conf:raise WSError("Alloggiati Web non è configurato.")
    if day not in (today,today-timedelta(days=1)):raise WSError("Alloggiati Web accetta solo gli arrivi di oggi o di ieri.")
    todo=[b for b in stays(tenant,day,day) if local(b.start,tz)==day and b.data.get("guests") and not b.snapshot.get("alloggiati")
        and not (auto and (b.snapshot.get("alloggiati_error") or b.start>now))]
    if not todo:return 0,[]
    lines=[[alloggiati_line(g,day,(local(b.end,tz)-day).days) for g in b.data["guests"]] for b in todo]
    token=ws_token(conf);errors=[]
    def split(results,group):
        i=0;good=[]
        for b,rows in group:
            res=results[i:i+len(rows)];i+=len(rows)
            if all(ok for ok,_ in res):good.append((b,rows))
            else:
                why="; ".join(f"ospite {k+1}: {w or 'dato non valido'}" for k,(ok,w) in enumerate(res) if not ok)[:300]
                b.snapshot["alloggiati_error"]=why;b.save(update_fields=["snapshot"]);errors.append(f"{b.snapshot.get('resource') or b.service.name}: {why}")
        return good
    good=split(ws_rows(conf,token,"Test",[x for ls in lines for x in ls]),list(zip(todo,lines)))
    done=split(ws_rows(conf,token,"Send",[x for _,ls in good for x in ls]),good) if good else []
    for b,_ in done:forget(tenant,b)
    if done:
        receipt,_=Receipt.objects.get_or_create(tenant=tenant,day=today)
        receipt.rows+=sum(len(ls) for _,ls in done);receipt.save(update_fields=["rows"])
    audit(tenant,actor,"stays.alloggiati_ws",day.isoformat(),sent=len(done),refused=len(errors),auto=auto)
    return len(done),errors

def receipts(tenant,conf,now=None):
    """The receipts of the past days of sending not downloaded yet: the Polizia gives them for 30 days, never for
    today. Those older than five years go."""
    today=local(now or timezone.now(),ZoneInfo(tenant.timezone));n=0
    Receipt.objects.filter(day__lt=today-timedelta(days=5*366)).delete()
    rows=list(Receipt.objects.filter(pdf__isnull=True,day__lt=today,day__gte=today-timedelta(days=29)))
    if not rows:return 0
    token=ws_token(conf)
    for r in rows:
        if pdf:=next((e.text for e in ws("Ricevuta",Utente=conf["user"],token=token,Data=f"{r.day:%Y-%m-%d}T00:00:00")("PDF") if e.text),""):
            r.pdf=base64.b64decode(pdf);r.save(update_fields=["pdf"]);n+=1
    return n

def ws_clean(post,saved):
    """The credentials as typed; an empty password or key keeps the one saved."""
    if post.get("ws_off")=="on":return {}
    conf={"user":str(post.get("ws_user","")).strip(),"auto":post.get("ws_auto")=="on"}
    for k in ("password","wskey"):conf[k]=str(post.get("ws_"+k,"")).strip() or saved.get(k,"")
    if not (re.fullmatch(r"[\w.@-]{3,60}",conf["user"]) and 0<len(conf["password"])<=100 and re.fullmatch(r"\S{4,200}",conf["wskey"] or "")):
        raise BookingError("Per Alloggiati Web servono utente, password e chiave web service (la trovi nel portale, menu utente, Chiave Web Service).")
    return conf

def ws_save(tenant,conf,actor=None):
    tenant.alloggiati=encrypt(tenant,conf,"alloggiati") if conf else "";tenant.save(update_fields=["alloggiati"])
    if actor:audit(tenant,actor,"stays.alloggiati_conf",on=bool(conf),auto=bool(conf.get("auto")))

CHANNELS={"public":"DIRETTA WEB","wordpress":"DIRETTA WEB","desk":"DIRETTA TRADIZIONALE","walkin":"DIRETTA TRADIZIONALE"}
def ross1000(tenant,location,year,month):
    """The ISTAT movements of a month for Ross1000 (XML 2.4): one day after the other, also the days without guests
    (movimento zero) and the days the location is closed (apertura NO). Names stay out: they are optional there."""
    tz=ZoneInfo(tenant.timezone);first=date(year,month,1);last=(first+timedelta(days=32)).replace(day=1)-timedelta(days=1)
    units=list(Resource.objects.filter(active=True,location=location,id__in=ServiceResource.objects.filter(service__kind="day").values("resource_id")))
    rows=list(stays(tenant,first,last,location));missing=[b for b in rows if first<=local(b.start,tz)<=last and not b.data.get("guests")]
    closed=set()
    for c in ExceptionDay.objects.filter(Q(location=location)|Q(location=None),covering(first,last),service=None,resource=None,purpose="service",closed=True):
        closed|={c.date+timedelta(days=i) for i in range(((c.until or c.date)-c.date).days+1)}
    out=['<?xml version="1.0" encoding="UTF-8"?>',"<movimenti>",f"<codice>{xml(location.ross_code)}</codice><prodotto>TD Booking Suite</prodotto>"]
    d=first
    while d<=last:
        night=[b for b in rows if local(b.start,tz)<=d<local(b.end,tz)]
        rooms=len({b.resource_id or b.pk for b in night})
        if d in closed:out.append(f"<movimento><data>{d:%Y%m%d}</data><struttura><apertura>NO</apertura><camereoccupate>0</camereoccupate><cameredisponibili>0</cameredisponibili><lettidisponibili>0</lettidisponibili></struttura>")
        else:out.append(f"<movimento><data>{d:%Y%m%d}</data><struttura><apertura>SI</apertura><camereoccupate>{min(rooms,len(units)) if units else rooms}</camereoccupate>"
            f"<cameredisponibili>{len(units)}</cameredisponibili><lettidisponibili>{sum(u.capacity for u in units)}</lettidisponibili></struttura>")
        arrivals=[];departures=[]
        for b in rows:
            guests=b.data.get("guests") or []
            ids=[f"{b.pk.hex[:12]}-{i+1}" for i in range(len(guests))]
            if local(b.start,tz)==d:
                for i,g in enumerate(guests):
                    born=place(g["birth_place"]);home=place(g["residence"])
                    arrivals.append(f"<arrivo><idswh>{ids[i]}</idswh><tipoalloggiato>{g['kind']}</tipoalloggiato><idcapo>{ids[0] if g['kind'] in ('19','20') else ''}</idcapo>"
                        f"<sesso>{g['sex']}</sesso><cittadinanza>{place(g['citizenship'])[0]}</cittadinanza><statoresidenza>{'100000100' if home[1]!='ES' else home[0]}</statoresidenza>"
                        f"<luogoresidenza>{home[0] if home[1]!='ES' else ''}</luogoresidenza><datanascita>{date.fromisoformat(g['birth_date']):%Y%m%d}</datanascita>"
                        f"<statonascita>{'100000100' if born[1]!='ES' else born[0]}</statonascita><comunenascita>{born[0] if born[1]!='ES' else ''}</comunenascita>"
                        f"<tipoturismo>{xml(b.data.get('tourism') or 'NON SPECIFICATO')}</tipoturismo><mezzotrasporto>{xml(b.data.get('transport') or 'NON SPECIFICATO')}</mezzotrasporto><canaleprenotazione>{CHANNELS.get(b.origin,'NON SPECIFICATO')}</canaleprenotazione></arrivo>")
            if local(b.end,tz)==d:
                departures+=[f"<partenza><idswh>{ids[i]}</idswh><tipoalloggiato>{g['kind']}</tipoalloggiato><arrivo>{local(b.start,tz):%Y%m%d}</arrivo></partenza>" for i,g in enumerate(guests)]
        out.append(("<arrivi>"+"".join(arrivals)+"</arrivi>" if arrivals else "")+("<partenze>"+"".join(departures)+"</partenze>" if departures else "")+"</movimento>")
        d+=timedelta(days=1)
    return "\n".join(out+["</movimenti>"])+"\n",missing

def age(born,on):return on.year-born.year-((on.month,on.day)<(born.month,born.day))
def tax_rule(loc,night):
    """The rate and the most nights taxed in a stay, for a night: the first rule of the structure's category (or of
    every category) whose period holds it; a season (yearly) comes back every year, also across the new year."""
    for r in loc.tax_rules:
        if r["category"] and r["category"]!=loc.tax_category:continue
        a,z,d=(r["from"][5:],r["until"][5:],night.isoformat()[5:]) if r["yearly"] else (r["from"],r["until"],night.isoformat())
        if a<=d<=z if a<=z else (d>=a or d<=z):return Decimal(r["rate"]),r["nights"]
    return loc.tax_rate,loc.tax_nights
def money(rates):return "; ".join(f"{r:.2f}".replace(".",",") for r in rates)
def tax(tenant,year):
    """The tourist tax of a year by location and quarter, laid out as the annual declaration (DM 29/4/2022): nights at
    each ordinary rate, at each reduced rate, exempt. Each guest each night: exempt by age that night or by the reason
    chosen for him, reduced, or at the rate of that night; the nights of a stay after the cap are not taxed, those of
    workers and students also after their nights of the year. A stay without guests' data counts everyone at the
    ordinary rate. It is a sum, not a payment."""
    tz=ZoneInfo(tenant.timezone);out={};yearly={}
    locs={l.pk:l for l in Location.objects.all() if l.tax_rate is not None or l.tax_rules}
    for b in stays(tenant,date(year,1,1),date(year,12,31)).filter(location__in=list(locs)).order_by("start"):
        loc=locs[b.location_id];a=local(b.start,tz);n=(local(b.end,tz)-a).days;guests=b.data.get("guests") or []
        row=out.setdefault(loc.pk,{"location":loc,"unknown":0,"codes":{},"quarters":[{"q":q+1,"rates":{},"reduced":{},"exempt":0,"amount":Decimal(0)} for q in range(4)]})
        if not guests:row["unknown"]+=1
        escorts=sum(1 for g in guests if g.get("exemption")!="escort")//loc.tax_escort if loc.tax_escort else len(guests)
        for g in guests or [{}]*b.party:
            code=g.get("exemption","");born=date.fromisoformat(g["birth_date"]) if g.get("birth_date") else None
            if code=="escort":
                if escorts:escorts-=1
                else:code=""  # more escorts than the town exempts: they pay
            who=(loc.pk,plain(g.get("last_name","")),plain(g.get("first_name","")),g.get("birth_date"))
            for i in range(n):
                night=a+timedelta(days=i);rate,cap=tax_rule(loc,night)
                if rate is None or cap and i>=cap:continue
                if night.year!=year:continue
                q=row["quarters"][(night.month-1)//3]
                why="Età" if born and age(born,night)<loc.tax_age else dict(EXEMPTIONS)[code] if code not in ("","reduced","worker","student") else ""
                if code in ("worker","student") and loc.tax_year_nights:
                    if yearly.get(who,0)>=loc.tax_year_nights:why=dict(EXEMPTIONS)[code]
                    elif not why:yearly[who]=yearly.get(who,0)+1
                if why:q["exempt"]+=1;row["codes"][why]=row["codes"].get(why,0)+1
                elif code=="reduced" and loc.tax_reduction:
                    r=(rate*(100-loc.tax_reduction)/100).quantize(Decimal("0.01"));q["reduced"][r]=q["reduced"].get(r,0)+1;q["amount"]+=r
                else:q["rates"][rate]=q["rates"].get(rate,0)+1;q["amount"]+=rate
    for row in out.values():
        for q in row["quarters"]:
            q["rates_text"]=money(q["rates"]);q["nights_text"]="; ".join(str(x) for x in q["rates"].values())
            q["reduced_text"]=money(q["reduced"]);q["reduced_nights"]=sum(q["reduced"].values());q["ordinary"]=sum(q["rates"].values())
        row["total"]={k:sum(q[k] for q in row["quarters"]) for k in ("ordinary","reduced_nights","exempt","amount")}
    return list(out.values())

# ---- pages -------------------------------------------------------------------------------------------------------
def units_of(request):
    rows=Resource.objects.filter(active=True,id__in=ServiceResource.objects.filter(service__kind="day").values("resource_id")).select_related("location").order_by("location__name","name")
    m=request.member
    if m and m.locations:rows=rows.filter(location_id__in=m.locations)
    return list(rows)

def pick(rows,pk):
    found=next((x for x in rows if str(x.pk)==str(pk)),None)
    if not found:raise Http404()
    return found

def stays_view(request,slug):
    """Alloggi: the day (arrivals, departures, in house, breakfasts), the rooms' cleaning, the portals' calendars, the
    rules for a period and the files for the authorities."""
    require(request,"booking.read")
    t=request.tenant;tz=ZoneInfo(t.timezone);today=local(timezone.now(),tz)
    try:day=date.fromisoformat(request.GET.get("giorno",""))
    except ValueError:day=today
    units=units_of(request);error=""
    if request.method=="POST":
        action=request.POST.get("action","")
        try:
            if action=="room":
                require(request,"booking.write")
                u=pick(units,request.POST.get("unit"))
                if request.POST.get("status") in dict(Resource._meta.get_field("housekeeping").choices):u.housekeeping=request.POST["status"];u.save(update_fields=["housekeeping"])
                if u.housekeeping=="out" and (n:=conflicts(u).count()):messages.warning(request,f"{u.name} è fuori servizio: non si prenota più. Ha {n} prenotazioni da spostare, le trovi sotto Camere e pulizie.")
            elif action in ("feeds","refresh"):
                require(request,"catalog.write")
                u=pick(units,request.POST.get("unit"))
                if action=="feeds":set_feeds(t,u,request.POST.get("urls",""));audit(t,request.user,"stays.feeds",u.pk)
                ok=refresh(t,u)
                messages.success(request,f"Calendari di {u.name} letti adesso." if ok else f"{u.name}: un calendario non risponde, restano le notti lette l'ultima volta.")
            elif action in ("rule_add","rule_del"):
                require(request,"catalog.write")
                s=pick(Service.objects.filter(kind="day"),request.POST.get("service"))
                if action=="rule_del":s.stay_rules=[r for i,r in enumerate(s.stay_rules) if str(i)!=request.POST.get("rule")]
                else:
                    try:a=date.fromisoformat(request.POST.get("from",""));z=date.fromisoformat(request.POST.get("until",""))
                    except ValueError:raise BookingError("Scegli le date del periodo.")
                    if z<a:raise BookingError("Il periodo finisce prima di iniziare.")
                    n=request.POST.get("min","0") or "0"
                    if not n.isdigit() or int(n)>60:raise BookingError("Soggiorno minimo non valido.")
                    days=lambda f:sorted({int(x) for x in request.POST.getlist(f) if x in "0123456"})
                    s.stay_rules=s.stay_rules+[{"from":a.isoformat(),"until":z.isoformat(),"min":int(n),"no_arrival":days("no_arrival"),"no_departure":days("no_departure")}]
                s.save(update_fields=["stay_rules"]);audit(t,request.user,"stays.rules",s.pk)
            elif action=="tax_location":
                require(request,"catalog.write")
                loc=pick(Location.objects.all(),request.POST.get("location"))
                if request.POST.get("tax_category","") not in dict(TAX_CATEGORIES)|{"":""}:raise BookingError("Categoria non valida.")
                try:n,e,r=(int(request.POST.get(k) or 0) for k in ("tax_year_nights","tax_escort","tax_reduction"))
                except ValueError:raise BookingError("Scrivi numeri interi.")
                if not (0<=n<=366 and 0<=e<=100 and 0<=r<=100):raise BookingError("Valori fuori misura.")
                loc.tax_category=request.POST.get("tax_category","");loc.tax_year_nights=n;loc.tax_escort=e;loc.tax_reduction=r
                loc.save(update_fields=["tax_category","tax_year_nights","tax_escort","tax_reduction"]);audit(t,request.user,"stays.tax",loc.pk)
            elif action in ("tax_add","tax_del"):
                require(request,"catalog.write")
                loc=pick(Location.objects.all(),request.POST.get("location"))
                if action=="tax_del":loc.tax_rules=[r for i,r in enumerate(loc.tax_rules) if str(i)!=request.POST.get("rule")]
                else:
                    try:a=date.fromisoformat(request.POST.get("from",""));z=date.fromisoformat(request.POST.get("until",""));rate=Decimal(request.POST.get("rate","").replace(",","."))
                    except (ValueError,ArithmeticError):raise BookingError("Scegli le date e scrivi la tariffa, per esempio 6,00.")
                    yearly=request.POST.get("yearly")=="on"
                    if z<a and not yearly:raise BookingError("Il periodo finisce prima di iniziare.")
                    if not Decimal(0)<=rate<=Decimal(100):raise BookingError("Tariffa non valida.")
                    n=request.POST.get("nights","0") or "0"
                    if not n.isdigit() or int(n)>366:raise BookingError("Notti massime non valide.")
                    if request.POST.get("category","") not in dict(TAX_CATEGORIES)|{"":""}:raise BookingError("Categoria non valida.")
                    loc.tax_rules=loc.tax_rules+[{"category":request.POST.get("category",""),"from":a.isoformat(),"until":z.isoformat(),"yearly":yearly,"rate":f"{rate:.2f}","nights":int(n)}]
                loc.save(update_fields=["tax_rules"]);audit(t,request.user,"stays.tax",loc.pk)
            elif action=="ws_conf":
                if not ws_editor(request):raise PermissionDenied
                from .identity import fresh
                fresh(request);ws_save(t,ws_clean(request.POST,ws_conf(t)),request.user);messages.success(request,"Alloggiati Web salvato.")
            elif action=="ws_send":
                require(request,"pii.read","export.operational")
                n,errors=ws_send(t,date.fromisoformat(request.POST.get("day","")),request.user)
                if n:messages.success(request,f"Inviati ad Alloggiati Web: {n} soggiorni. I dati dei documenti sono stati cancellati, la ricevuta arriva da domani.")
                elif not errors:messages.info(request,"Niente da inviare: gli arrivi del giorno sono già inviati o senza i dati degli ospiti.")
                for e in errors:messages.error(request,"Rifiutato da Alloggiati Web, "+e)
            elif action=="sent":
                require(request,"pii.read","export.operational")
                messages.success(request,f"Fatto: documenti cancellati per {sent(t,date.fromisoformat(request.POST.get('day','')),request.user)} prenotazioni.")
            return redirect(f"{request.path}?giorno={day.isoformat()}")
        except (BookingError,ValueError,WSError) as ex:error=str(ex)
    rows=list(visible_bookings(request,stays(t,day-timedelta(days=1),day)))
    for b in rows:
        b.first=local(b.start,tz);b.last=local(b.end,tz);b.who=shown(request,b.data);b.guests=len(b.who.pop("guests",None) or [])
    year=int(request.GET["anno"]) if request.GET.get("anno","").isdigit() and 2000<=int(request.GET["anno"])<=2100 else today.year
    for u in units:
        u.feed_link=feed_link(t,u);u.urls="\n".join(feeds(t,u)) if can(request,"catalog.write") else ""
        u.conflicts=list(visible_bookings(request,conflicts(u))[:20]) if u.housekeeping=="out" else []
    locations=list(Location.objects.filter(active=True).order_by("name"))
    for loc in locations:loc.rules_shown=[{**r,"category_name":dict(TAX_CATEGORIES).get(r["category"],"tutte le categorie")} for r in loc.tax_rules]
    files=can(request,"pii.read") and can(request,"export.operational");conf=ws_conf(t)
    breakfast=[b for b in rows if b.first<day<=b.last]  # who slept here the night before
    return render(request,"stays.html",{"day":day,"today":today,"yesterday":today-timedelta(days=1),"prev":day-timedelta(days=1),"next":day+timedelta(days=1),"error":error,
        "lists":[("Arrivi",[b for b in rows if b.first==day],"Nessun arrivo."),("Partenze",[b for b in rows if b.last==day],"Nessuna partenza."),("In casa",[b for b in rows if b.first<day<b.last],"Nessuno in casa.")],
        "breakfast":breakfast,"breakfast_people":sum(b.party for b in breakfast),"units":units,"services":Service.objects.filter(kind="day",active=True).order_by("name"),
        "weekdays":list(enumerate(WEEKDAYS)),"locations":locations,"month":f"{today:%Y-%m}",
        "year":year,"taxes":tax(t,year) if any(l.tax_rate is not None or l.tax_rules for l in locations) else None,"categories":TAX_CATEGORIES,
        "files":files,"ws":conf,"ws_editor":ws_editor(request),"receipts":Receipt.objects.order_by("-day")[:60] if files and conf else []})

def conflicts(unit):
    """The stays still to come on a unit out of service: they are to be moved."""
    return Booking.objects.filter(resource=unit,status__in=STAYING,end__gt=timezone.now()).order_by("start")

def ws_editor(request):
    """The agency sets the Alloggiati Web credentials, the owner too when the agency allows it."""
    return request.user.is_staff or request.tenant.alloggiati_self and getattr(request.member,"role","")=="owner"

def stay_files(request,slug,what):
    """The files for the authorities, downloaded: Alloggiati Web (arrivals of today or yesterday), Ross1000 (a month)."""
    require(request,"pii.read","export.operational")
    t=request.tenant;tz=ZoneInfo(t.timezone)
    try:
        if what=="alloggiati":
            day=date.fromisoformat(request.GET.get("giorno",""));today=local(timezone.now(),tz)
            if day not in (today,today-timedelta(days=1)):raise ValueError
            body,missing=alloggiati(t,day);name=f"alloggiati-{day:%Y%m%d}.txt";kind="text/plain; charset=us-ascii"
        elif what=="ricevuta":
            r=get_object_or_404(Receipt,day=date.fromisoformat(request.GET.get("giorno","")),pdf__isnull=False)
            response=HttpResponse(bytes(r.pdf),content_type="application/pdf");response["Content-Disposition"]=f'attachment; filename="ricevuta-alloggiati-{r.day:%Y%m%d}.pdf"'
            return response
        elif what=="ross1000":
            y,m=(int(x) for x in request.GET.get("mese","").split("-"))
            body,missing=ross1000(t,get_object_or_404(Location,pk=request.GET.get("sede")),y,m);name=f"ross1000-{y}{m:02d}.xml";kind="application/xml; charset=utf-8"
        else:raise Http404()
    except (ValueError,TypeError):
        messages.error(request,"Scegli un giorno (oggi o ieri) o un mese valido.");return redirect("stays",slug)
    if missing:
        messages.warning(request,f"Nel file mancano {len(missing)} prenotazioni senza i dati degli ospiti: completale e scaricalo di nuovo.")
    audit(t,request.user,"stays.file",what,missing=len(missing))
    response=HttpResponse(body.encode("ascii" if what=="alloggiati" else "utf-8"),content_type=kind)
    response["Content-Disposition"]=f'attachment; filename="{name}"'
    return response

def guests_page(request,booking,post_to,public=False,actor=None):
    t=request.tenant;error=""
    if request.method=="POST":
        try:
            guests=read_guests(request.POST,booking.party)
            if not guests:raise BookingError("Scrivi almeno un ospite.")
            save_guests(t,booking,guests,actor,read_trip(request.POST))
            if public:return render(request,"message.html",{"title":"Check-in online fatto","text":"Grazie: i dati degli ospiti sono arrivati. All'arrivo mostrate i documenti, come chiede la legge.","public":True})
            messages.success(request,"Dati degli ospiti salvati.");return redirect("booking_detail",t.slug,booking.pk)
        except BookingError as ex:error=str(ex)
    trip=read_trip(request.POST) if error else booking.data
    return render(request,"guests.html",{"booking":booking,"rows":guest_rows(booking,request.POST if error else None),"kinds":KINDS,"documents":documents(),
        "error":error,"public":public,"post_to":post_to,"heads":HEADS,"exemptions":EXEMPTIONS,"trip":trip,
        "tourism":[(x.upper(),x) for x in TOURISM],"transport":[(x.upper(),x) for x in TRANSPORT]})

def guests_view(request,slug,booking_id):
    """The guests of a stay at the desk."""
    require(request,"booking.write","pii.read")
    b=get_object_or_404(visible_bookings(request,Booking.objects.filter(service__kind="day")),pk=booking_id)
    return guests_page(request,b,request.path,actor=request.user)

def checkin_view(request,slug,token):
    """Check-in online from the client's "Gestisci" link, until the departure: the same form as the desk's."""
    throttle(request,"action",60,60)
    obj=ActionToken.objects.filter(digest=token_hash(token),kind="cancel",expires__gt=timezone.now(),booking__service__kind="day",
        booking__status__in=STAYING,booking__end__gt=timezone.now()).select_related("booking__service").first()
    if not obj:return render(request,"message.html",{"title":"Check-in non disponibile","text":"Il collegamento è scaduto o il soggiorno è concluso. Contatta la struttura.","public":True},status=400)
    if request.method=="POST":throttle(request,"booking:"+str(request.tenant.pk),20,3600)
    return guests_page(request,obj.booking,request.path,public=True)

def feed_view(request,slug,unit_id,token):
    """The calendar of a unit for Airbnb, Booking.com and the others: busy nights only."""
    throttle(request,"availability:"+str(request.tenant.pk),120,60)
    unit=Resource.objects.filter(pk=unit_id,active=True).first()
    import hmac
    if not unit or not hmac.compare_digest(token,feed_token(request.tenant,unit)):raise Http404("Calendario non disponibile.")
    response=HttpResponse(feed(request.tenant,unit),content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"]=f'inline; filename="{plain(unit.name).lower().replace(" ","-") or "alloggio"}.ics"'
    return response
