"""2.10.0: the day of a salon, a studio, a court: one column for each operator, the appointments where they fall.
2.11.0: also the week and the month, for every business.

A free quarter of hour inside someone's working time is a link to a new booking with that operator, day and time
already chosen; the time outside it is greyed. Each operator keeps one colour in every view. The week of a business
without appointments (a restaurant, a hotel) lists each day's bookings instead of a grid of hours: many tables at
the same time would not fit side by side. The list of bookings stays one click away (?vista=elenco)."""
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
from django.shortcuts import render
from django.utils import timezone
from .calendar import windows,merge
from .models import Booking,Location,Resource,Service,ServiceResource
from .permissions import can,shown,visible_bookings
from .presets import units

STEP=15  # minutes in a row of the grid
SHOWN=("confirmed","completed","no_show")
COLORS=8  # operator colours in the stylesheet: op-1 ... op-8
MONTHS=["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"]

def fits(request):
    """The day view is for businesses that book people or rooms one at a time (appointments)."""
    return Service.objects.filter(active=True,kind="resource",location__active=True).exists()

def operators(request,location):
    """Who works in this location for appointments, as the staff member may see them."""
    linked=ServiceResource.objects.filter(service__active=True,service__kind="resource",service__location=location).values("resource_id")
    rows=Resource.objects.filter(active=True,location=location,id__in=linked).order_by("name")
    m=getattr(request,"member",None)
    if m and m.resources:rows=rows.filter(pk__in=m.resources)
    return list(rows)

def open_spans(tenant,day,op,memo=None,services=None):
    """When an operator can be booked that day: the union of what each of his services allows. memo and services
    are kept by the page across days and operators (2.12.0): the same levels and services are read once."""
    if services is None:services=list(Service.objects.filter(active=True,kind="resource",id__in=ServiceResource.objects.filter(resource=op).values("service_id")))
    spans=[]
    for s in services:spans+=windows(tenant,day,s,op,memo=memo)
    return merge(spans)

def services_of(ops):
    """The appointment services of each operator, in one query."""
    out={op.pk:[] for op in ops}
    for link in ServiceResource.objects.filter(resource__in=ops,service__active=True,service__kind="resource").select_related("service"):
        out[link.resource_id].append(link.service)
    return out

def chosen_day(request,today):
    try:return date.fromisoformat(request.GET.get("data",""))
    except ValueError:return today

def place(request,appointments):
    """The locations the user may see (with appointments, those that have them) and the one shown."""
    m=getattr(request,"member",None)
    kinds={"kind":"resource"} if appointments else {}
    places=Location.objects.filter(active=True,id__in=Service.objects.filter(active=True,**kinds).values("location_id")).order_by("name")
    if m and m.locations:places=places.filter(pk__in=m.locations)
    places=list(places)
    return places,next((p for p in places if str(p.pk)==request.GET.get("sede")),places[0] if places else None)

def colors(ops):
    """One colour for each operator, the same in day, week and month."""
    return {op.pk:i%COLORS+1 for i,op in enumerate(ops)}

def item(request,r,tz,color):
    data=shown(request,r.data)
    ra,rb=r.start.astimezone(tz),r.end.astimezone(tz)
    return {"booking":r,"name":f"{data.get('first_name','')} {data.get('last_name','')}".strip() or "Cliente",
        "service":r.snapshot.get("service") or r.service.name,"from":ra.strftime("%H:%M"),"to":rb.strftime("%H:%M"),"start":ra,"end":rb,
        "status":r.status,"color":color.get(r.resource_id,0),"who":r.snapshot.get("resource") or "","party":r.party,
        # 2.11.0: allergies or intolerances the client ticked show on the appointment itself, as the salon programs do
        "alert":list(data.get("allergies") or [])}

def common(request,view,day,today,places,location,ops,chosen=None):
    """What the bar above every view needs: the views, the day, the location and the operator chosen."""
    one,many,_=units(request.tenant.business_type)
    keep=[("sede",str(location.pk))] if location and len(places)>1 else []
    if chosen:keep.append(("operatore",str(chosen.pk)))
    return {"view":view,"day":day,"today":today,"places":places,"location":location,"ops":ops,"chosen":chosen,"one":one,"many":many,
        "keep":"".join(f"&amp;{k}={v}" for k,v in keep),"sede":str(location.pk) if location and len(places)>1 else "",
        "appointments":fits(request),"legend":[{"name":o.name,"color":c} for o,c in zip(ops,colors(ops).values())] if len(ops)>1 else []}

def day_view(request):
    t=request.tenant;tz=ZoneInfo(t.timezone)
    today=timezone.now().astimezone(tz).date()
    day=chosen_day(request,today)
    places,location=place(request,True)
    everyone=operators(request,location) if location else []
    color=colors(everyone)
    # 2.12.0: the day of one operator only, chosen above the agenda
    chosen=next((o for o in everyone if str(o.pk)==request.GET.get("operatore")),None)
    ops=[chosen] if chosen else everyone
    start=datetime.combine(day,time.min,tz);end=start+timedelta(days=1)
    rows=visible_bookings(request,Booking.objects.filter(location=location,start__lt=end,end__gt=start,status__in=SHOWN)).select_related("service","resource","tenant").order_by("start")
    if chosen:rows=rows.filter(resource=chosen)
    rows=list(rows) if location else []
    memo={};mine=services_of(ops)
    spans={op.pk:open_spans(t,day,op,memo,mine[op.pk]) for op in ops}
    # 2.11.0: like the salon programs, the day shows who works: an operator off that day, with nothing booked,
    # leaves his column and is named above the agenda. On a day nobody works every column stays, greyed
    works=lambda op:any(b>start and a<end for a,b in spans[op.pk])  # windows() also looks at the nights around
    off=[op for op in ops if not works(op) and not any(r.resource_id==op.pk for r in rows)]
    if len(off)<len(ops):ops=[op for op in ops if op not in off]
    else:off=[]
    # the frame of the day: the working time of anyone, and any booking outside it, in whole hours
    edges=[(a.astimezone(tz),b.astimezone(tz)) for s in spans.values() for a,b in s]+[(r.start.astimezone(tz),r.end.astimezone(tz)) for r in rows]
    edges=[(max(a,start),min(b,end)) for a,b in edges if b>start and a<end]
    first=min((a for a,_ in edges),default=datetime.combine(day,time(9),tz))
    last=max((b for _,b in edges),default=datetime.combine(day,time(19),tz))
    frame_a=first.replace(minute=0,second=0,microsecond=0)
    frame_b=last if last.minute==0 and last.second==0 else last.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
    frame_b=min(frame_b,end);count=max(1,int((frame_b-frame_a).total_seconds()//60//STEP))
    row_of=lambda moment:max(1,min(count+1,int((moment.astimezone(tz)-frame_a).total_seconds()//60//STEP)+1))
    # one column per operator; bookings without one (made before operators existed) get their own
    columns=[{"op":op,"name":op.name,"color":color[op.pk],"closed":[],"cells":[],"items":[]} for op in ops]
    by_op={c["op"].pk:c for c in columns}
    loose=[r for r in rows if r.resource_id not in by_op]
    if loose:columns.append({"op":None,"name":"Senza "+units(t.business_type)[0].lower(),"color":0,"closed":[],"cells":[],"items":[]})
    writer=can(request,"booking.write") and day>=today
    now=timezone.now()
    for i,c in enumerate(columns,start=2):
        c["col"]=i
        mine=[r for r in rows if (r.resource_id==c["op"].pk if c["op"] else r.resource_id not in by_op)]
        busy=[(r.start,r.end) for r in mine]
        opened=spans.get(c["op"].pk,[]) if c["op"] else []
        for n in range(count):
            a=frame_a+timedelta(minutes=n*STEP);b=a+timedelta(minutes=STEP)
            is_open=any(x<=a and b<=y for x,y in opened)
            if not is_open:
                # closed quarters merge into one grey band
                if c["closed"] and c["closed"][-1]["end"]==n+1:c["closed"][-1]["end"]=n+2
                else:c["closed"].append({"start":n+1,"end":n+2})
            elif writer and a>=now and not any(x<b and a<y for x,y in busy):
                c["cells"].append({"row":n+1,"time":a.strftime("%H:%M")})
        for r in mine:
            x=item(request,r,tz,color)
            x["row"]=row_of(max(x["start"],frame_a));x["row_end"]=max(x["row"]+1,row_of(min(x["end"],frame_b)))
            c["items"].append(x)
    hours=[{"row":n+1,"end":min(n+1+60//STEP,count+1),"label":(frame_a+timedelta(minutes=n*STEP)).strftime("%H:%M")} for n in range(count) if (n*STEP)%60==0]
    now_row=None
    if day==today and frame_a<=now.astimezone(tz)<frame_b:
        now_row=(now.astimezone(tz)-frame_a).total_seconds()/60/STEP
    ctx=common(request,"giorno",day,today,places,location,everyone,chosen)
    ctx.update({"prev":day-timedelta(days=1),"next":day+timedelta(days=1),"title":day,
        "columns":columns,"hours":hours,"count":count,"now_row":now_row,"legend":[],"off":[o.name for o in off],
        "closed_day":bool(ops) and not any(works(o) for o in ops),"total":len(rows),"writer":writer,"end_row":count+1})
    return render(request,"agenda.html",ctx)

def lanes(items):
    """Appointments of the same day side by side when they overlap: each gets a lane, one alone takes the width."""
    ends=[]
    for x in sorted(items,key=lambda x:(x["start"],x["end"])):
        free=next((i for i,e in enumerate(ends) if e<=x["start"]),None)
        if free is None:ends.append(x["end"]);free=len(ends)-1
        else:ends[free]=x["end"]
        x["lane"]=free+1
    for x in items:
        alone=not any(y is not x and y["start"]<x["end"] and x["start"]<y["end"] for y in items)
        x["lane_col"]="1 / -1" if alone else str(x["lane"])
    return max(1,len(ends))

def week_view(request):
    t=request.tenant;tz=ZoneInfo(t.timezone)
    today=timezone.now().astimezone(tz).date()
    day=chosen_day(request,today)
    monday=day-timedelta(days=day.weekday());days=[monday+timedelta(days=n) for n in range(7)]
    appointments=fits(request)
    places,location=place(request,appointments)
    ops=operators(request,location) if location and appointments else []
    color=colors(ops)
    chosen=next((o for o in ops if str(o.pk)==request.GET.get("operatore")),None)
    start=datetime.combine(monday,time.min,tz);end=datetime.combine(monday+timedelta(days=7),time.min,tz)
    rows=visible_bookings(request,Booking.objects.filter(start__lt=end,end__gt=start,status__in=SHOWN)).select_related("service","resource","tenant").order_by("start")
    if location:rows=rows.filter(location=location)
    if chosen:rows=rows.filter(resource=chosen)
    rows=list(rows) if location else []
    writer=can(request,"booking.write")
    now=timezone.now()
    columns=[{"day":d,"col":i,"today":d==today,"items":[],"closed":[],"cells":[]} for i,d in enumerate(days,start=2)]
    for r in rows:
        d=r.start.astimezone(tz).date()
        if monday<=d<monday+timedelta(days=7):columns[(d-monday).days]["items"].append(item(request,r,tz,color))
    ctx=common(request,"settimana",day,today,places,location,ops,chosen)
    sunday=monday+timedelta(days=6)
    title=f"{monday.day}–{sunday.day} {MONTHS[sunday.month-1]} {sunday.year}" if monday.month==sunday.month else f"{monday.day} {MONTHS[monday.month-1]} – {sunday.day} {MONTHS[sunday.month-1]} {sunday.year}"
    ctx.update({"prev":monday-timedelta(days=7),"next":monday+timedelta(days=7),"title_text":title,"columns":columns,"total":len(rows),"grid":appointments and bool(ops)})
    if not ctx["grid"]:
        for c in columns:c["items"].sort(key=lambda x:x["start"])
        return render(request,"agenda_week.html",ctx)
    # the hours of the week: from the first opening or booking of any day to the last, in whole hours
    shown_ops=[chosen] if chosen else ops
    memo={};mine=services_of(shown_ops)
    spans={(d,op.pk):open_spans(t,d,op,memo,mine[op.pk]) for d in days for op in shown_ops}
    minutes=lambda moment,d:(moment.astimezone(tz)-datetime.combine(d,time.min,tz)).total_seconds()/60
    edges=[(minutes(a,d),minutes(b,d)) for (d,_),s in spans.items() for a,b in s]+[(minutes(x["start"],c["day"]),minutes(x["end"],c["day"])) for c in columns for x in c["items"]]
    edges=[(max(0,a),min(1440,b)) for a,b in edges if b>0 and a<1440]
    first=int(min((a for a,_ in edges),default=540))//60*60
    last=-(-int(max((b for _,b in edges),default=1140))//60)*60
    count=max(1,(last-first)//STEP)
    row_of=lambda m:max(1,min(count+1,int((m-first)//STEP)+1))
    for c in columns:
        d=c["day"];midnight=datetime.combine(d,time.min,tz)
        busy={op.pk:[(x["start"],x["end"]) for x in c["items"] if x["booking"].resource_id==op.pk] for op in shown_ops}
        for n in range(count):
            a=midnight+timedelta(minutes=first+n*STEP);b=a+timedelta(minutes=STEP)
            free=[op for op in shown_ops if any(x<=a and b<=y for x,y in spans[(d,op.pk)])]
            if not free:
                if c["closed"] and c["closed"][-1]["end"]==n+1:c["closed"][-1]["end"]=n+2
                else:c["closed"].append({"start":n+1,"end":n+2})
            elif writer and a>=now and any(not any(x<b and a<y for x,y in busy[op.pk]) for op in free):
                c["cells"].append({"row":n+1,"time":a.strftime("%H:%M")})
        for x in c["items"]:
            x["row"]=row_of(max(minutes(x["start"],d),first));x["row_end"]=max(x["row"]+1,row_of(min(minutes(x["end"],d),last)))
        c["lanes"]=lanes(c["items"])
        if c["today"] and first<=minutes(now,d)<last:c["now_row"]=(minutes(now,d)-first)/STEP
    hours=[{"row":n+1,"end":min(n+1+60//STEP,count+1),"label":f"{(first+n*STEP)//60:02d}:00"} for n in range(count) if (n*STEP)%60==0]
    ctx.update({"hours":hours,"count":count,"end_row":count+1})
    return render(request,"agenda_week.html",ctx)

def month_view(request):
    t=request.tenant;tz=ZoneInfo(t.timezone)
    today=timezone.now().astimezone(tz).date()
    day=chosen_day(request,today)
    first=day.replace(day=1);last=(first+timedelta(days=32)).replace(day=1)-timedelta(days=1)
    grid_a=first-timedelta(days=first.weekday());grid_b=last+timedelta(days=6-last.weekday())
    appointments=fits(request)
    places,location=place(request,appointments)
    ops=operators(request,location) if location and appointments else []
    color=colors(ops)
    chosen=next((o for o in ops if str(o.pk)==request.GET.get("operatore")),None)
    start=datetime.combine(grid_a,time.min,tz);end=datetime.combine(grid_b+timedelta(days=1),time.min,tz)
    rows=visible_bookings(request,Booking.objects.filter(start__lt=end,end__gt=start,status__in=SHOWN)).select_related("service","resource","tenant").order_by("start")
    if location:rows=rows.filter(location=location)
    if chosen:rows=rows.filter(resource=chosen)
    by_day={}
    for r in (rows if location else []):by_day.setdefault(r.start.astimezone(tz).date(),[]).append(item(request,r,tz,color))
    weeks=[];d=grid_a
    while d<=grid_b:
        week=[]
        for _ in range(7):
            items=by_day.get(d,[])
            week.append({"day":d,"inside":d.month==first.month,"today":d==today,"past":d<today,"items":items[:3],"more":max(0,len(items)-3),"count":len(items),
                "people":sum(x["party"] for x in items)})
            d+=timedelta(days=1)
        weeks.append(week)
    ctx=common(request,"mese",day,today,places,location,ops,chosen)
    ctx.update({"prev":(first-timedelta(days=1)).replace(day=1),"next":last+timedelta(days=1),"title_text":f"{MONTHS[first.month-1].capitalize()} {first.year}",
        "weeks":weeks,"total":sum(c["count"] for w in weeks for c in w if c["inside"]),"names":["Lun","Mar","Mer","Gio","Ven","Sab","Dom"]})
    return render(request,"agenda_month.html",ctx)
