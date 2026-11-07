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

def tables(request,a,b,first,last,occupancy=True):
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
    for r in (resources if occupancy else []):
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

def euro(c):return (Decimal(c)/100).quantize(Decimal("0.01"))
def record_tables(request,first,last):
    """2.20.5: from the clients' records (receipts and the history of the old program): visits and money by operator,
    month, year, service and client, the figures on top and the bars of the dashboard. Only for who may export
    (owner and location administrator); the clients with their names only with pii.read."""
    if not can(request,"export.operational"):return {},{}
    from .models import Treatment
    by_staff=defaultdict(lambda:[0,0]);by_month=defaultdict(lambda:[0,0]);by_client=defaultdict(lambda:[0,0,None]);who_is={}
    by_year=defaultdict(lambda:[0,0,Counter()]);by_service=Counter();listed=[]
    names_ok=can(request,"pii.read")
    from .crypto import open_seal
    key=tenant_key(request.tenant);tid=str(request.tenant.pk)  # one key for all: t.data would read the business and unwrap its key every row
    # shortcut: a client record has no location; a location administrator sees the business's records, as on the client card
    for t in Treatment.objects.select_related("contact").order_by("date"):
        d=open_seal(t.pii,key,f"{tid}:treatment:{t.pk}");cost=d.get("amount") or 0;split=dict(d.get("by") or {})
        names=[x.strip() for x in (d.get("staff") or "").split(",") if x.strip()]
        n=0 if d.get("kind")=="payment" else 1  # 2.20.9: a «Da pagare» paid later is money in on its day, not one more visit
        if not split and names:split={n:cost//len(names)+((cost%len(names)) if i==0 else 0) for i,n in enumerate(names)}
        if not split:split={"":cost}
        elif cost and sum(split.values())!=cost and d.get("by"):split[""]=split.get("",0)+cost-sum(split.values())  # products and lines without an operator
        y=by_year[t.date.year];y[0]+=n;y[1]+=cost
        for who,c in split.items():y[2][who or "Senza operatore"]+=c
        if not first<=t.date<=last:continue
        for who,c in split.items():
            v=by_staff[who or "Senza operatore"];v[0]+=n;v[1]+=c
        v=by_month[t.date.strftime("%Y-%m")];v[0]+=n;v[1]+=cost
        v=by_client[t.contact_id];v[0]+=n;v[1]+=cost;who_is[t.contact_id]=t.contact
        if n:
            v[2]=max(v[2] or t.date,t.date)
            for one in (d.get("service") or "").split(" + "):
                if one.strip():by_service[one.strip()]+=1
        listed.append((t,d,cost))
    out={}
    total=[sum(v[0] for v in by_month.values()),sum(v[1] for v in by_month.values())]
    out["lavori_operatore"]=(["Operatore","Lavori","Incassato (€)"],[[k,v[0],euro(v[1])] for k,v in sorted(by_staff.items(),key=lambda i:-i[1][1])])
    out["lavori_mese"]=(["Mese","Visite","Incassato (€)"],[[k[5:]+"/"+k[:4],v[0],euro(v[1])] for k,v in sorted(by_month.items())]+([["Totale",total[0],euro(total[1])]] if by_month else []))
    team=[k for k,_ in sum((y[2] for y in by_year.values()),Counter()).most_common()]
    out["lavori_anno"]=(["Anno","Visite","Incassato (€)",*team],[[k,v[0],euro(v[1]),*[euro(v[2][n]) for n in team]] for k,v in sorted(by_year.items())])
    out["lavori_servizio"]=(["Servizio","Volte"],[[k,n] for k,n in by_service.most_common()])
    people={}
    if names_ok:
        rows=[]
        for cid,v in by_client.items():
            c=open_seal(who_is[cid].pii,key,f"{tid}:contact:{cid}");people[cid]=" ".join(x for x in (c.get("first_name",""),c.get("last_name","")) if x) or "Cliente"
            rows.append([people[cid],v[0],euro(v[1]),v[2].strftime("%d/%m/%Y") if v[2] else ""])
        out["lavori_cliente"]=(["Cliente","Visite","Speso (€)","Ultima visita"],sorted(rows,key=lambda r:-r[2]))
        out["elenco"]=(["Data","Cliente","Trattamento","Operatore","Importo (€)"],[[t.date.strftime("%d/%m/%Y"),people.get(t.contact_id,""),d.get("service",""),d.get("staff",""),euro(c)] for t,d,c in reversed(listed)])
    # 2.20.9: what the clients still owe, today, whatever the period: the «Da pagare» of the Cassa not paid yet
    from .cassa import owed
    open_=list(owed().select_related("contact").order_by("created"));today=timezone.now().astimezone(ZoneInfo(request.tenant.timezone)).date()
    for s in open_:
        if s.contact_id and s.contact_id not in people and names_ok:
            c=open_seal(s.contact.pii,key,f"{tid}:contact:{s.contact_id}");people[s.contact_id]=" ".join(x for x in (c.get("first_name",""),c.get("last_name","")) if x) or "Cliente"
    out["da_incassare"]=(["Cliente","Scontrino del","Numero","Da pagare (€)","Da giorni"],[[people.get(s.contact_id,"Cliente"),s.printed.astimezone(ZoneInfo(request.tenant.timezone)).strftime("%d/%m/%Y"),
        s.doc,s.due,(today-s.printed.astimezone(ZoneInfo(request.tenant.timezone)).date()).days] for s in open_])
    # 2.22.0: Preventivo pagamenti cassa, to this day: done and not paid, with no receipt yet
    from .models import Parked,GiftCard
    name=lambda c:(people.setdefault(c.pk," ".join(x for x in (open_seal(c.pii,key,f"{tid}:contact:{c.pk}").get(k,"") for k in ("first_name","last_name")) if x) or "Cliente") if names_ok else "Cliente") if c else ""
    parked=list(Parked.objects.select_related("contact").order_by("created"));tz=ZoneInfo(request.tenant.timezone)
    rows=[]
    for x in parked:
        for l in x.data.get("lines",[]):rows.append([name(x.contact),x.created.astimezone(tz).strftime("%d/%m/%Y"),(f"{l['qty']} x " if l["qty"]>1 else "")+l["desc"],l.get("staff",""),euro(l["qty"]*l["cents"]-l.get("off",0))])
    out["sospesi"]=(["Cliente","Del","Cosa","Operatore","Importo (€)"],rows)
    # gift cards sold in the period, and the credit left on each
    cards=list(GiftCard.objects.filter(created__date__gte=first,created__date__lte=last).exclude(status="pending").select_related("contact").order_by("created"))
    out["gift_card"]=(["Codice","Intestata a","Venduta il","Valore (€)","Credito (€)","Scade","Stato"],[[g.code,name(g.contact),g.created.astimezone(tz).strftime("%d/%m/%Y"),g.amount,g.balance,g.expires.strftime("%d/%m/%Y") if g.expires else "",g.get_status_display()] for g in cards])
    # the dashboard: the figures of the period and its bars, as widths of 0..100
    top=max([v[1] for v in by_month.values()] or [0]) or 1;best=max([v[1] for v in by_staff.values()] or [0]) or 1
    dash={"money":euro(total[1]),"visits":total[0],"average":euro(total[1]//total[0]) if total[0] else euro(0),"clients":len(by_client),"width":10*min(len(by_month),24),
        "months":[{"label":k[5:]+"/"+k[2:4],"money":euro(v[1]),"x":10*i+2,"y":102-round(100*v[1]/top),"h":max(1,round(100*v[1]/top))} for i,(k,v) in enumerate(sorted(by_month.items())[-24:])],
        "staff":[{"label":k,"money":euro(v[1]),"n":v[0],"w":round(100*v[1]/best)} for k,v in sorted(by_staff.items(),key=lambda i:-i[1][1])],
        "owed":sum(s.due for s in open_),"owed_n":len(open_),"parked":sum(x.total for x in parked),"parked_n":len(parked)} if total[0] or open_ or parked else {}
    return out,dash

def xlsx(head,rows,title):
    """A one-sheet Excel file without libraries: inline strings, numbers as numbers."""
    import zipfile
    from xml.sax.saxutils import escape
    def cell(v):
        if isinstance(v,bool) or v is None:v="" if v is None else str(v)
        if isinstance(v,(int,float,Decimal)):return f'<c t="n"><v>{v}</v></c>'
        return f'<c t="inlineStr"><is><t xml:space="preserve">{escape(csv_cell(str(v),"sheet"))}</t></is></c>'
    data="".join("<row>"+"".join(cell(v) for v in r)+"</row>" for r in [head,*rows])
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr("_rels/.rels",'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/_rels/workbook.xml.rels",'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{escape(title[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/worksheets/sheet1.xml",f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>{data}</sheetData></worksheet>')
    return buf.getvalue()

TITLES={"lavori_operatore":"Lavori e incassi per operatore","lavori_mese":"Visite e incassi per mese","lavori_anno":"Anno per anno, con gli operatori",
    "lavori_servizio":"I servizi più fatti","da_incassare":"Da incassare","sospesi":"Trattamenti non nello scontrino","gift_card":"Gift card vendute","lavori_cliente":"Clienti: visite e speso","elenco":"Elenco dei trattamenti",
    "occupazione":"Occupazione per operatore e risorsa","assenze":"Assenze e disdette all'ultimo momento","clienti":"Clienti nuovi e di ritorno",
    "origine":"Da dove arrivano le prenotazioni","valore":"Valore stimato"}
def view(request,slug):
    require(request,"booking.read")
    tz=ZoneInfo(request.tenant.timezone);today=timezone.now().astimezone(tz).date()
    try:
        first=date.fromisoformat(request.GET.get("from") or (today-timedelta(days=29)).isoformat());last=date.fromisoformat(request.GET.get("to") or today.isoformat())
        if not 0<=(last-first).days<=366:raise ValueError
    except ValueError:return render(request,"message.html",{"title":"Periodo non valido","text":"Scegli un periodo di al massimo un anno."},status=400)
    a=datetime.combine(first,time.min,tz);b=datetime.combine(last+timedelta(days=1),time.min,tz)
    mine,dash=record_tables(request,first,last)
    # 2.20.5: the occupancy reads the hours of every day of every operator: on a whole year it took seconds, so beyond
    # three months it is worked out on request (or for its Excel)
    heavy=(last-first).days>92 and not request.GET.get("occupazione") and request.GET.get("xlsx")!="occupazione" and request.GET.get("csv")!="occupazione"
    data={**mine,**tables(request,a,b,first,last,occupancy=not heavy)}
    name=request.GET.get("xlsx")
    if name in data:
        head,rows=data[name];audit(request.tenant,request.user,"export.report",name)
        response=HttpResponse(xlsx(head,rows,TITLES[name]),content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"]=f'attachment; filename="report-{name}-{first}-{last}.xlsx"'
        return response
    name=request.GET.get("csv")
    if name in data:
        head,rows=data[name];buf=io.StringIO(newline="");w=csv.writer(buf);w.writerow(head)
        for r in rows:w.writerow([csv_cell(v,"sheet") for v in r])
        audit(request.tenant,request.user,"export.report",name)
        response=HttpResponse("﻿"+buf.getvalue(),content_type="text/csv; charset=utf-8")
        response["Content-Disposition"]=f'attachment; filename="report-{name}-{first}-{last}.csv"'
        return response
    from .models import Treatment
    years=[d.year for d in Treatment.objects.dates("date","year")]
    years=sorted(set(years)|{today.year})
    return render(request,"reports.html",{"first":first,"last":last,"years":years,"dash":dash,"heavy":heavy,"sections":[{"key":k,"title":TITLES[k],"head":h,"rows":r[:200] if k in ("lavori_cliente","elenco") else r,"more":len(r)-200 if k in ("lavori_cliente","elenco") and len(r)>200 else 0} for k,(h,r) in data.items()]})

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
