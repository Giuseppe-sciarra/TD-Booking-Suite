"""2.14.0: the reports of a period (occupancy, absences and late cancellations, new and returning clients, who leaves
with the next appointment, origin, estimated value), each with its CSV; and the import of the future appointments
from the old program, with the column choice and the preview of the conflicts of contacts_import."""
import csv,io,json,re
from collections import Counter,defaultdict
from datetime import date,datetime,time,timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Min
from django.http import HttpResponse
from django.shortcuts import render,redirect
from django.utils import timezone
from .booking import BookingError,ForceWarning,create_booking
from .calendar import windows,block_spans,minus,local_datetime,UTC
from .clients import late
from .crypto import index,tenant_key
from .models import Booking,Resource,Service,Occurrence,audit
from .permissions import can,require,visible_bookings
from .portal import csv_cell

def minutes(spans):return int(sum((b-a).total_seconds() for a,b in spans)//60)
def pct(n,d):return round(100*n/d) if d else 0

def tables(request,a,b,first,last):
    t=request.tenant;tz=ZoneInfo(t.timezone);out={}
    rows=list(visible_bookings(request,Booking.objects.filter(start__gte=a,start__lt=b)).select_related("service","resource"))
    held=[x for x in rows if x.status!="cancelled"]
    # occupancy: booked minutes over the open minutes of each operator or room, time blocks excluded
    resources=Resource.objects.filter(active=True).select_related("location").order_by("location__name","name")
    m=getattr(request,"member",None)  # the same reach as visible_bookings
    if m and m.resources:resources=resources.filter(pk__in=m.resources)
    elif m and (m.locations or m.role=="operator"):resources=resources.filter(location_id__in=m.locations or [])
    memo={};booked=Counter()
    for x in held:
        if x.resource_id:booked[x.resource_id]+=minutes([(x.start,min(x.end,b))])
    occ=[]
    for r in resources:
        place=SimpleNamespace(location_id=r.location_id,pk=None)  # the location's hours as well, not only the business's
        spans=[];d=first
        while d<=last:
            day=(local_datetime(d,time.min,tz).astimezone(UTC),local_datetime(d+timedelta(days=1),time.min,tz).astimezone(UTC))
            spans+=[(max(p,day[0]),min(q,day[1])) for p,q in windows(t,d,place,r,memo=memo) if p<day[1] and q>day[0]]
            d+=timedelta(days=1)
        free=minutes(minus(spans,[(p,q) for p,q,_ in block_spans(t,first,last,resource=r)]))
        occ.append([r.name,r.location.name,free,booked[r.pk],pct(booked[r.pk],free)])
    out["occupazione"]=(["Risorsa","Sede","Minuti disponibili","Minuti prenotati","Occupazione %"],occ)
    # new client: no booking kept before this one
    ids={x.contact_id for x in rows if x.contact_id}
    since=dict(Booking.objects.filter(contact_id__in=ids).exclude(status="cancelled").values("contact_id").annotate(f=Min("start")).values_list("contact_id","f"))
    is_new=lambda x:since.get(x.contact_id) is None or x.start<=since[x.contact_id]
    groups=defaultdict(Counter)
    for x in rows:
        keys=[x.snapshot.get("service") or x.service.name]+(["Clienti nuovi" if is_new(x) else "Clienti abituali"] if x.contact_id else [])+["Totale"]
        for k in keys:
            g=groups[k];g["n"]+=1;g["no_show"]+=x.status=="no_show";g["late"]+=late(x.status,x.start,x.snapshot)
    order=sorted(k for k in groups if k not in ("Clienti nuovi","Clienti abituali","Totale"))+[k for k in ("Clienti nuovi","Clienti abituali","Totale") if k in groups]
    out["assenze"]=(["Servizio o clienti","Prenotazioni","Assenze","Assenze %","Disdette all'ultimo momento","Disdette all'ultimo momento %"],
        [[k,groups[k]["n"],groups[k]["no_show"],pct(groups[k]["no_show"],groups[k]["n"]),groups[k]["late"],pct(groups[k]["late"],groups[k]["n"])] for k in order])
    # clients of the period, new or returning, and the visits done that left with the next appointment already booked
    clients={x.contact_id:is_new(x) for x in sorted(held,key=lambda x:x.start,reverse=True) if x.contact_id}  # the first of the period decides
    visits=[x for x in held if x.status=="completed" and x.contact_id]
    after=defaultdict(list)
    for cid,s,c in Booking.objects.filter(contact_id__in={x.contact_id for x in visits},start__gte=a).exclude(status="cancelled").values_list("contact_id","start","created"):after[cid].append((s,c))
    def rebooked(v):
        close=datetime.combine(v.start.astimezone(tz).date()+timedelta(days=1),time.min,tz)
        return any(s>=v.end and c<close for s,c in after[v.contact_id])
    back=sum(map(rebooked,visits));new=sum(clients.values())
    out["clienti"]=(["Voce","Valore"],[["Clienti nuovi",new],["Clienti di ritorno",len(clients)-new],["Visite fatte",len(visits)],
        ["Uscite con il prossimo appuntamento già preso",back],["Uscite con il prossimo appuntamento %",pct(back,len(visits))]])
    # where they came from, and how many arrived while the business was closed
    origin=defaultdict(Counter)
    for x in rows:
        k=(x.get_origin_display() or "Non registrata",x.source);origin[k]["n"]+=1
        c=x.created;d=c.astimezone(tz).date()
        origin[k]["out"]+=not any(p<=c<q for p,q in windows(t,d,x.service,memo=memo))
    out["origine"]=(["Origine","Fonte","Prenotazioni","Arrivate a locale chiuso"],[[k[0],k[1],v["n"],v["out"]] for k,v in sorted(origin.items(),key=lambda i:-i[1]["n"])])
    # the value at the list price of the booking, not money taken
    value=defaultdict(lambda:[0,Decimal(0)])
    for x in held:
        if x.status=="no_show":continue
        v=value[x.snapshot.get("service") or x.service.name];v[0]+=1
        try:v[1]+=Decimal(x.snapshot.get("total") or x.snapshot.get("price") or 0)
        except ArithmeticError:pass
    total=[sum(v[0] for v in value.values()),sum((v[1] for v in value.values()),Decimal(0))]
    out["valore"]=(["Servizio","Prenotazioni","Valore stimato da listino (€)"],[[k,v[0],v[1]] for k,v in sorted(value.items())]+([["Totale",*total]] if value else []))
    return out

TITLES={"occupazione":"Occupazione per operatore e risorsa","assenze":"Assenze e disdette all'ultimo momento","clienti":"Clienti nuovi e di ritorno",
    "origine":"Da dove arrivano le prenotazioni","valore":"Valore stimato"}
def view(request,slug):
    require(request,"booking.read")
    tz=ZoneInfo(request.tenant.timezone);today=timezone.now().astimezone(tz).date()
    try:
        first=date.fromisoformat(request.GET.get("from") or (today-timedelta(days=29)).isoformat());last=date.fromisoformat(request.GET.get("to") or today.isoformat())
        if not 0<=(last-first).days<=366:raise ValueError
    except ValueError:return render(request,"message.html",{"title":"Periodo non valido","text":"Scegli un periodo di al massimo un anno."},status=400)
    a=datetime.combine(first,time.min,tz);b=datetime.combine(last+timedelta(days=1),time.min,tz)
    data=tables(request,a,b,first,last)
    name=request.GET.get("csv")
    if name in data:
        head,rows=data[name];buf=io.StringIO(newline="");w=csv.writer(buf);w.writerow(head)
        for r in rows:w.writerow([csv_cell(v,"sheet") for v in r])
        audit(request.tenant,request.user,"export.report",name)
        response=HttpResponse("﻿"+buf.getvalue(),content_type="text/csv; charset=utf-8")
        response["Content-Disposition"]=f'attachment; filename="report-{name}-{first}-{last}.csv"'
        return response
    return render(request,"reports.html",{"first":first,"last":last,"sections":[{"key":k,"title":TITLES[k],"head":h,"rows":r} for k,(h,r) in data.items()]})

# ---------- import of the future appointments
FIELDS=[("","Non importare"),("date","Data"),("time","Ora di inizio"),("end","Ora di fine"),("minutes","Durata in minuti"),("end_date","Data di partenza (soggiorni)"),
    ("service","Servizio"),("staff","Operatore o risorsa"),("full_name","Nome e cognome insieme"),("first_name","Nome"),("last_name","Cognome"),
    ("phone","Telefono"),("email","Email"),("notes","Note"),("party","Persone")]
GUESS={"data":"date","giorno":"date","dataappuntamento":"date","date":"date","ora":"time","orario":"time","orainizio":"time","inizio":"time","dalle":"time","time":"time",
    "orafine":"end","fine":"end","alle":"end","durata":"minutes","minuti":"minutes","partenza":"end_date","datapartenza":"end_date","checkout":"end_date",
    "servizio":"service","trattamento":"service","prestazione":"service","service":"service","operatore":"staff","operatrice":"staff","risorsa":"staff","staff":"staff",
    "cliente":"full_name","nominativo":"full_name","nomecognome":"full_name","cognomenome":"full_name","nome":"first_name","cognome":"last_name",
    "telefono":"phone","cellulare":"phone","cell":"phone","tel":"phone","email":"email","mail":"email","note":"notes","persone":"party","coperti":"party","ospiti":"party"}
MAX_ROWS=3000  # shortcut: one request books the whole file, every row with the checks of the desk; past it, a background job
def clock(value):
    """10:30, 10.30, 1030 or an Excel time (a fraction of the day, also after a date number)."""
    v=value.strip()
    m=re.search(r"\b(\d{1,2})[:.](\d{2})",v) or re.fullmatch(r"(\d{1,2})(\d{2})",v)
    if m and int(m[1])<24 and int(m[2])<60:return time(int(m[1]),int(m[2]))
    m=re.fullmatch(r"\d*\.(\d+)",v)
    if m:
        n=round(float("0."+m[1])*1440)%1440
        return time(n//60,n%60)
    return None
def whole(v):return str(v).strip().split(".")[0].split(",")[0]  # 30, 30.0 of Excel, 30,0
def booking_data(t,row,mapping,services,resources,today):
    from .importer import day
    get=lambda f:next((row[i] for i,m in enumerate(mapping) if m==f and row[i].strip()),"")
    d=day(get("date"),today)
    if not d:raise BookingError("data mancante o non valida")
    s=services.get(get("service").strip().casefold())
    if not s:raise BookingError(f"servizio «{get('service')[:40]}» non trovato" if get("service") else "servizio mancante")
    r=None
    if get("staff"):
        r=resources.get(get("staff").strip().casefold())
        if not r:raise BookingError(f"operatore o risorsa «{get('staff')[:40]}» non trovato")
    tz=ZoneInfo(t.timezone)
    at=s.checkin if s.kind=="day" else clock(get("time")) or clock(get("date"))
    if at is None:raise BookingError("ora mancante o non valida")
    start=local_datetime(d,at,tz)
    if start is None:raise BookingError("ora che non esiste per il cambio dell'ora legale")
    first,last=(get("full_name").split(None,1)+[""])[:2] if get("full_name") and not get("first_name") else (get("first_name"),get("last_name"))
    data={"service":str(s.pk),"resource":str(r.pk) if r else "","start":start.isoformat(),"party":whole(get("party")) or 1,"first_name":first,"last_name":last,
        "phone":get("phone"),"email":get("email"),"notes":get("notes")[:2000],"marketing":False,"force":True}
    if s.kind=="day":
        end=day(get("end_date"),today)
        if not end:raise BookingError("data di partenza mancante")
        data["end_date"]=end.isoformat()
    elif s.kind in ("event","shift"):
        o=Occurrence.objects.filter(service=s,start=start,active=True).first()
        if not o:raise BookingError("nessuna data dell'evento o turno a quell'ora")
        data["occurrence"]=str(o.pk)
    else:
        end=clock(get("end"));n=get("minutes")
        if end:n=(datetime.combine(d,end)-datetime.combine(d,at)).seconds//60
        if n:data["minutes"]=whole(n)
    return start,data
class Preview(Exception):pass
def run(t,rows,mapping,token,actor,write=False,force=False):
    """Every row booked as at the desk with Forza: what it goes beyond is a conflict. The preview runs the same and
    takes it all back. No email to the clients (create_booking, origin import)."""
    tz=ZoneInfo(t.timezone);now=timezone.now();today=now.astimezone(tz).date()
    services={s.name.strip().casefold():s for s in Service.objects.filter(active=True,location__active=True)}
    resources={r.name.strip().casefold():r for r in Resource.objects.filter(active=True)}
    report=Counter();problems=[];key=tenant_key(t)
    try:
        with transaction.atomic():
            for n,row in enumerate(rows,start=2):
                try:
                    start,data=booking_data(t,row,mapping,services,resources,today)
                    if start<now:report["past"]+=1;continue
                    # the same row of the same file is the same booking, also when the file is loaded again
                    data["request_key"]="import-"+index(json.dumps([row,mapping]),key,"import")[:56]
                    if Booking.objects.filter(request_key=data["request_key"]).exists():report["same"]+=1;continue
                    try:
                        with transaction.atomic():_,created=create_booking(t,data,actor,origin="import")
                        report["ok" if created else "same"]+=1
                    except ForceWarning as ex:
                        if not force:report["conflicts"]+=1;problems.append({"n":n,"conflict":True,"text":str(ex).removeprefix("Stai forzando: ").removesuffix(" Se va bene, conferma.")});continue
                        with transaction.atomic():create_booking(t,{**data,"force_ok":True},actor,origin="import")
                        report["forced"]+=1
                except (BookingError,ValidationError,ValueError,TypeError) as ex:report["errors"]+=1;problems.append({"n":n,"conflict":False,"text":str(ex)})
            if not write:raise Preview
    except Preview:pass
    return report,problems
def import_view(request,slug):
    from . import importer
    require(request,"booking.write","contacts.read","pii.read")
    t=request.tenant;key="bimport:"+slug;token=request.session.get(key)
    stash=importer.unstash(t,token) if token else None
    action=request.POST.get("action") if request.method=="POST" else None
    force_ok=can(request,"booking.force");ctx={"error":"","force_ok":force_ok}
    if action=="upload":
        f=request.FILES.get("file")
        try:
            if not f:raise BookingError("Scegli il file da importare.")
            if f.size>importer.MAX_BYTES:raise BookingError("File troppo grande: al massimo 20 MB.")
            head,rows=importer.read(f.read())
            if len(rows)>MAX_ROWS:raise BookingError(f"Il file ha {len(rows)} righe: se ne importano al massimo {MAX_ROWS} alla volta, dividilo in più file.")
            importer.drop(token);request.session[key]=importer.stash(t,f.name,head,rows)
            return redirect("bookings_import",slug)
        except BookingError as ex:ctx["error"]=str(ex)
    elif action=="reset" or (token and not stash):
        importer.drop(token);request.session.pop(key,None)
        if action=="reset":return redirect("bookings_import",slug)
    elif stash:
        head,rows=stash["head"],stash["rows"];allowed=dict(FIELDS)
        if action in ("preview","import"):mapping=[m if m in allowed else "" for m in (request.POST.get(f"col{i}","") for i in range(len(head)))]
        else:
            mapping=[]
            for h in head:
                f=GUESS.get(importer.plain(h),"");mapping.append("" if f in mapping else f)
        force=force_ok and request.POST.get("force")=="1"
        checked=token+"|"+",".join(mapping)
        if action=="import" and request.POST.get("checked")!=checked:action="preview";ctx["error"]="Hai cambiato qualche colonna: controlla di nuovo l'anteprima."
        try:
            if action in ("preview","import") and not {"date","service"}<=set(mapping):raise BookingError("Indica almeno la colonna della data e quella del servizio.")
            if action=="import":
                report,problems=run(t,rows,mapping,token,request.user,write=True,force=force)
                audit(t,request.user,"bookings.imported",token,**report)
                importer.drop(token);request.session.pop(key,None)
                messages.success(request,f"Importate {report['ok']+report['forced']} prenotazioni"+(f", {report['forced']} forzate" if report["forced"] else "")
                    +(f"; saltate {report['conflicts']+report['errors']} righe" if report["conflicts"]+report["errors"] else "")+(f" e {report['past']} già passate" if report["past"] else "")+".")
                return redirect("bookings",slug)
            if action=="preview":ctx["report"],ctx["problems"]=run(t,rows,mapping,token,request.user)
        except BookingError as ex:ctx["error"]=str(ex)
        ctx.update(name=stash["name"],count=len(rows),fields=FIELDS,checked=checked,force=force,
            columns=[{"i":i,"head":h,"samples":importer.samples(rows,i),"field":mapping[i]} for i,h in enumerate(head)])
    return render(request,"bookings_import.html",ctx)
