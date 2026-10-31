"""2.14.0: hotel, B&B and holiday homes. The calendar of each unit for the portals (.ics) and theirs read back as
closures; rules for a period; the day at the desk with cleaning and breakfasts; the guests, sealed in the booking, typed
at the desk or by the client from his link; the Alloggiati Web file, the ISTAT movements for Ross1000, the tourist tax.
The files are downloaded and sent by the business: no web service."""
import csv,ipaddress,re,socket,unicodedata,urllib.parse,urllib.request
from datetime import date,datetime,timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path
from xml.sax.saxutils import escape as xml
from zoneinfo import ZoneInfo
from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse,Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from .models import Tenant,Location,Resource,Service,ServiceResource,ExceptionDay,Booking,ActionToken,audit
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
        g["exempt"]=post.get(f"g{i}-exempt")=="1"
        out.append(g)
    if out and out[0]["kind"] in ("19","20"):raise BookingError("Il primo ospite è il capofamiglia, il capogruppo o un ospite singolo.")
    return out

def save_guests(tenant,booking,guests,actor=None):
    data=booking.data;data["guests"]=guests
    booking.pii=encrypt(tenant,data,"booking:"+str(booking.pk));booking.save(update_fields=["pii"])
    audit(tenant,actor,"booking.guests",booking.pk,guests=len(guests))

def guest_rows(booking,post=None):
    """The forms of a booking's guests: as typed, or as saved, one for each person."""
    saved=booking.data.get("guests") or []
    rows=[]
    for i in range(booking.party):
        g={k:post.get(f"g{i}-{k}","") for k in GUEST}|{"exempt":post.get(f"g{i}-exempt")=="1"} if post else (saved[i] if i<len(saved) else {"kind":"16" if booking.party==1 else "17" if i==0 else "19"})
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

def sent(tenant,day,actor=None):
    """The Alloggiati file of a day is sent: the documents of its guests are deleted (DM 7/1/2013 art. 4-bis);
    what ISTAT and the tax need stays."""
    tz=ZoneInfo(tenant.timezone);n=0
    for b in stays(tenant,day,day):
        if local(b.start,tz)!=day or not b.data.get("guests"):continue
        data=b.data;data["guests"]=[{**g,"doc_type":"","doc_number":"","doc_place":""} for g in data["guests"]]
        b.pii=encrypt(tenant,data,"booking:"+str(b.pk));b.snapshot["alloggiati"]=timezone.now().isoformat()
        b.save(update_fields=["pii","snapshot"]);n+=1
    audit(tenant,actor,"stays.alloggiati_sent",day.isoformat(),bookings=n)
    return n

CHANNELS={"public":"DIRETTA WEB","wordpress":"DIRETTA WEB","desk":"DIRETTA TRADIZIONALE","walkin":"DIRETTA TRADIZIONALE"}
def ross1000(tenant,location,year,month):
    """The ISTAT movements of a month for Ross1000 (XML 2.4): one day after the other, also the days without guests
    (movimento zero). Names stay out: they are optional there."""
    tz=ZoneInfo(tenant.timezone);first=date(year,month,1);last=(first+timedelta(days=32)).replace(day=1)-timedelta(days=1)
    units=list(Resource.objects.filter(active=True,location=location,id__in=ServiceResource.objects.filter(service__kind="day").values("resource_id")))
    rows=list(stays(tenant,first,last,location));missing=[b for b in rows if first<=local(b.start,tz)<=last and not b.data.get("guests")]
    out=['<?xml version="1.0" encoding="UTF-8"?>',"<movimenti>",f"<codice>{xml(location.ross_code)}</codice><prodotto>TD Booking Suite</prodotto>"]
    d=first
    while d<=last:
        night=[b for b in rows if local(b.start,tz)<=d<local(b.end,tz)]
        rooms=len({b.resource_id or b.pk for b in night})
        out.append(f"<movimento><data>{d:%Y%m%d}</data><struttura><apertura>SI</apertura><camereoccupate>{min(rooms,len(units)) if units else rooms}</camereoccupate>"
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
                        f"<tipoturismo>NON SPECIFICATO</tipoturismo><mezzotrasporto>NON SPECIFICATO</mezzotrasporto><canaleprenotazione>{CHANNELS.get(b.origin,'NON SPECIFICATO')}</canaleprenotazione></arrivo>")
            if local(b.end,tz)==d:
                departures+=[f"<partenza><idswh>{ids[i]}</idswh><tipoalloggiato>{g['kind']}</tipoalloggiato><arrivo>{local(b.start,tz):%Y%m%d}</arrivo></partenza>" for i,g in enumerate(guests)]
        out.append(("<arrivi>"+"".join(arrivals)+"</arrivi>" if arrivals else "")+("<partenze>"+"".join(departures)+"</partenze>" if departures else "")+"</movimento>")
        d+=timedelta(days=1)
    return "\n".join(out+["</movimenti>"])+"\n",missing

def age(born,on):return on.year-born.year-((on.month,on.day)<(born.month,born.day))
def tax(tenant,year):
    """The tourist tax of the nights of a year, by quarter: each guest, each night up to the town's cap, exempt under
    its age that night or when declared so. A stay without guests' data counts everyone. It is a sum, not a payment."""
    tz=ZoneInfo(tenant.timezone);quarters=[{"q":q+1,"taxed":0,"exempt":0,"amount":Decimal(0)} for q in range(4)];unknown=0
    for b in stays(tenant,date(year,1,1),date(year,12,31)).filter(location__tax_rate__isnull=False):
        a=local(b.start,tz);loc=b.location;n=(local(b.end,tz)-a).days;guests=b.data.get("guests") or []
        if not guests:unknown+=1
        for g in guests or [{}]*b.party:
            born=date.fromisoformat(g["birth_date"]) if g.get("birth_date") else None
            for i in range(min(n,loc.tax_nights or n)):
                night=a+timedelta(days=i)
                if night.year!=year:continue
                row=quarters[(night.month-1)//3]
                if g.get("exempt") or born and age(born,night)<loc.tax_age:row["exempt"]+=1
                else:row["taxed"]+=1;row["amount"]+=loc.tax_rate
    total={k:sum(r[k] for r in quarters) for k in ("taxed","exempt","amount")}
    return quarters,total,unknown

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
            elif action=="sent":
                require(request,"pii.read","export.operational")
                messages.success(request,f"Fatto: documenti cancellati per {sent(t,date.fromisoformat(request.POST.get('day','')),request.user)} prenotazioni.")
            return redirect(f"{request.path}?giorno={day.isoformat()}")
        except (BookingError,ValueError) as ex:error=str(ex)
    rows=list(visible_bookings(request,stays(t,day-timedelta(days=1),day)))
    for b in rows:
        b.first=local(b.start,tz);b.last=local(b.end,tz);b.who=shown(request,b.data);b.guests=len(b.who.pop("guests",None) or [])
    year=int(request.GET["anno"]) if request.GET.get("anno","").isdigit() and 2000<=int(request.GET["anno"])<=2100 else today.year
    quarters,total,unknown=tax(t,year) if Location.objects.filter(tax_rate__isnull=False).exists() else (None,None,0)
    for u in units:u.feed_link=feed_link(t,u);u.urls="\n".join(feeds(t,u)) if can(request,"catalog.write") else ""
    breakfast=[b for b in rows if b.first<day<=b.last]  # who slept here the night before
    return render(request,"stays.html",{"day":day,"today":today,"yesterday":today-timedelta(days=1),"prev":day-timedelta(days=1),"next":day+timedelta(days=1),"error":error,
        "lists":[("Arrivi",[b for b in rows if b.first==day],"Nessun arrivo."),("Partenze",[b for b in rows if b.last==day],"Nessuna partenza."),("In casa",[b for b in rows if b.first<day<b.last],"Nessuno in casa.")],
        "breakfast":breakfast,"breakfast_people":sum(b.party for b in breakfast),"units":units,"services":Service.objects.filter(kind="day",active=True).order_by("name"),
        "weekdays":list(enumerate(WEEKDAYS)),"locations":Location.objects.filter(active=True).order_by("name"),"month":f"{today:%Y-%m}",
        "year":year,"quarters":quarters,"total":total,"unknown":unknown,
        "files":can(request,"pii.read") and can(request,"export.operational")})

def stay_files(request,slug,what):
    """The files for the authorities, downloaded: Alloggiati Web (arrivals of today or yesterday), Ross1000 (a month)."""
    require(request,"pii.read","export.operational")
    t=request.tenant;tz=ZoneInfo(t.timezone)
    try:
        if what=="alloggiati":
            day=date.fromisoformat(request.GET.get("giorno",""));today=local(timezone.now(),tz)
            if day not in (today,today-timedelta(days=1)):raise ValueError
            body,missing=alloggiati(t,day);name=f"alloggiati-{day:%Y%m%d}.txt";kind="text/plain; charset=us-ascii"
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
            save_guests(t,booking,guests,actor)
            if public:return render(request,"message.html",{"title":"Check-in online fatto","text":"Grazie: i dati degli ospiti sono arrivati. All'arrivo mostrate i documenti, come chiede la legge.","public":True})
            messages.success(request,"Dati degli ospiti salvati.");return redirect("booking_detail",t.slug,booking.pk)
        except BookingError as ex:error=str(ex)
    return render(request,"guests.html",{"booking":booking,"rows":guest_rows(booking,request.POST if error else None),"kinds":KINDS,"documents":documents(),
        "error":error,"public":public,"post_to":post_to,"heads":HEADS})

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
