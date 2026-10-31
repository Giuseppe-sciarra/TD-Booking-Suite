"""2.10.0: the day of a salon, a studio, a court: one column for each operator, the appointments where they fall.
2.11.0: also the week and the month, for every business.

A free quarter of hour inside someone's working time is a link to a new booking with that operator, day and time
already chosen; the time outside it is greyed. Each operator keeps one colour in every view. The week of a business
without appointments (a restaurant, a hotel) lists each day's bookings instead of a grid of hours: many tables at
the same time would not fit side by side. The list of bookings stays one click away (?vista=elenco)."""
from datetime import date,datetime,time,timedelta
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from .calendar import windows,merge,minus,block_spans,blocked,local_datetime
from .models import Booking,Location,Resource,Service,ServiceResource,Occurrence,TimeBlock,Tenant,audit
from .permissions import can,require,shown,visible_bookings,check_new_booking
from .booking import OPEN,MOVABLE,BookingError,ForceWarning,move_booking
from .presets import units
from .public import json_denied,get_visible,check_force,warned
from .identity import json_body

STEP=15  # minutes in a row of the grid
SHOWN=("pending","confirmed","arrived","in_progress","completed","no_show")  # 2.14.0: a request holds its place, shown as such
COLORS=8  # operator colours in the stylesheet: op-1 ... op-8
MONTHS=["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"]

def fits(request):
    """The day view is for businesses that book people or rooms one at a time (appointments)."""
    return Service.objects.filter(active=True,kind="resource",location__active=True).exists()

def operators(request,location):
    """Who works in this location for appointments, as the staff member may see them."""
    linked=ServiceResource.objects.filter(service__active=True,service__kind="resource",service__location=location).values("resource_id")
    rows=Resource.objects.filter(active=True,secondary=False,location=location,id__in=linked).order_by("name")
    m=getattr(request,"member",None)
    if m and m.resources:rows=rows.filter(pk__in=m.resources)
    return list(rows)

def open_spans(tenant,day,op,memo=None,services=None):
    """When an operator can be booked that day: the union of what each of his services allows. memo and services
    are kept by the page across days and operators (2.12.0): the same levels and services are read once."""
    if services is None:services=list(Service.objects.filter(active=True,kind="resource",id__in=ServiceResource.objects.filter(resource=op).values("service_id")))
    spans=[]
    for s in services:spans+=windows(tenant,day,s,op,memo=memo)
    # 2.14.0: his pauses, training and meetings are not bookable, greyed as the time he does not work
    return minus(merge(spans),[(a,b) for a,b,_ in block_spans(tenant,day-timedelta(days=1),day,resource=op)])

def services_of(ops):
    """The appointment services of each operator, in one query."""
    out={op.pk:[] for op in ops}
    for link in ServiceResource.objects.filter(resource__in=ops,service__active=True,service__kind="resource").select_related("service"):
        out[link.resource_id].append(link.service)
    return out

def statuses(request):
    """2.14.0: the cancelled ones stay hidden, shown on request (Mostra annullate) to see what freed up."""
    return SHOWN+("cancelled",) if request.GET.get("annullate") else SHOWN

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
    return {"booking":r,"data":data,"name":f"{data.get('first_name','')} {data.get('last_name','')}".strip() or "Cliente",
        "service":r.snapshot.get("service") or r.service.name,"from":ra.strftime("%H:%M"),"to":rb.strftime("%H:%M"),"start":ra,"end":rb,
        "status":r.status,"color":color.get(r.resource_id,0),"who":r.snapshot.get("resource") or "","party":r.party,
        # 2.11.0: allergies or intolerances the client ticked show on the appointment itself, as the salon programs do
        "alert":list(data.get("allergies") or []),
        # 2.14.0: dragged and stretched in the agenda, an appointment of one day still to come; moves snap to its interval
        "movable":r.status in MOVABLE and r.service.kind=="resource" and ra.date()==rb.date() and r.end>timezone.now(),
        "minutes":int((rb-ra).total_seconds()//60),"snap":r.service.interval if 5<=r.service.interval<=60 else STEP}

PHASE_ORDER={"partenza":0,"in casa":1,"arrivo":2,"":3}
def days_of(request,r,tz,color,first,last):
    """The days a booking shows on, between first and last: its own day, and for a stay (a room, a flat, a
    rented bike: 2.12.0) every day from arrival to departure, each saying which of the three it is."""
    x=item(request,r,tz,color)
    a,b=x["start"].date(),x["end"].date()
    if r.service.kind!="day" or a==b:
        return [(a,x)] if first<=a<=last else []
    out=[]
    d=max(a,first)
    while d<=min(b,last):
        phase="arrivo" if d==a else "partenza" if d==b else "in casa"
        y=dict(x,phase=phase,phase_label={"arrivo":"Arrivo "+x["from"],"partenza":"Partenza "+x["to"],"in casa":"In casa"}[phase])
        out.append((d,y));d+=timedelta(days=1)
    return out

def common(request,view,day,today,places,location,ops,chosen=None):
    """What the bar above every view needs: the views, the day, the location and the operator chosen."""
    one,many,_=units(request.tenant.business_type)
    keep=[("sede",str(location.pk))] if location and len(places)>1 else []
    if chosen:keep.append(("operatore",str(chosen.pk)))
    plain="".join(f"&amp;{k}={v}" for k,v in keep)
    return {"view":view,"day":day,"today":today,"places":places,"location":location,"ops":ops,"chosen":chosen,"one":one,"many":many,
        "keep":plain+("&amp;annullate=1" if request.GET.get("annullate") else ""),"keep_plain":plain,"sede":str(location.pk) if location and len(places)>1 else "",
        "appointments":fits(request),"cancelled_on":bool(request.GET.get("annullate")),"legend":[{"name":o.name,"color":c} for o,c in zip(ops,colors(ops).values())] if len(ops)>1 else []}

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
    rows=visible_bookings(request,Booking.objects.filter(location=location,start__lt=end,end__gt=start,status__in=statuses(request))).select_related("service","resource","tenant").order_by("start")
    if chosen:rows=rows.filter(resource=chosen)
    from .clients import mark
    rows=mark(request,rows) if location else []
    memo={};mine=services_of(ops)
    spans={op.pk:open_spans(t,day,op,memo,mine[op.pk]) for op in ops}
    # 2.11.0: like the salon programs, the day shows who works: an operator off that day, with nothing booked,
    # leaves his column and is named above the agenda. On a day nobody works every column stays, greyed
    works=lambda op:any(b>start and a<end for a,b in spans[op.pk])  # windows() also looks at the nights around
    blocks={op.pk:block_spans(t,day,day,resource=op) for op in ops}  # 2.14.0: Blocca orario
    off=[op for op in ops if not works(op) and not any(r.resource_id==op.pk for r in rows) and not blocks[op.pk]]
    if len(off)<len(ops):ops=[op for op in ops if op not in off]
    else:off=[]
    # the frame of the day: the working time of anyone, and any booking outside it, in whole hours
    edges=[(a.astimezone(tz),b.astimezone(tz)) for s in spans.values() for a,b in s]+[(r.start.astimezone(tz),r.end.astimezone(tz)) for r in rows]+[(a.astimezone(tz),b.astimezone(tz)) for op in ops for a,b,_ in blocks[op.pk]]
    edges=[(max(a,start),min(b,end)) for a,b in edges if b>start and a<end]
    first=min((a for a,_ in edges),default=datetime.combine(day,time(9),tz))
    last=max((b for _,b in edges),default=datetime.combine(day,time(19),tz))
    frame_a=first.replace(minute=0,second=0,microsecond=0)
    frame_b=last if last.minute==0 and last.second==0 else last.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
    frame_b=min(frame_b,end);count=max(1,int((frame_b-frame_a).total_seconds()//60//STEP))
    row_of=lambda moment:max(1,min(count+1,int((moment.astimezone(tz)-frame_a).total_seconds()//60//STEP)+1))
    # one column per operator; bookings without one (made before operators existed) get their own
    columns=[{"op":op,"name":op.name,"color":color[op.pk],"closed":[],"cells":[],"items":[],"blocks":[]} for op in ops]
    by_op={c["op"].pk:c for c in columns}
    loose=[r for r in rows if r.resource_id not in by_op]
    if loose:columns.append({"op":None,"name":"Senza "+units(t.business_type)[0].lower(),"color":0,"closed":[],"cells":[],"items":[],"blocks":[]})
    writer=can(request,"booking.write") and day>=today
    now=timezone.now()
    for i,c in enumerate(columns,start=2):
        c["col"]=i
        mine=[r for r in rows if (r.resource_id==c["op"].pk if c["op"] else r.resource_id not in by_op)]
        busy=[(r.start,r.end) for r in mine if r.status!="cancelled"]
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
        c["lanes"]=lanes(c["items"])  # 2.14.0: forced or cancelled over another appointment, side by side as in the week
        for a,b,blk in (blocks.get(c["op"].pk,[]) if c["op"] else []):
            row=row_of(max(a,frame_a));c["blocks"].append({"block":blk,"row":row,"row_end":max(row+1,row_of(min(b,frame_b))),"label":blk.get_reason_display(),
                "from":a.astimezone(tz).strftime("%H:%M"),"to":b.astimezone(tz).strftime("%H:%M")})
    hours=[{"row":n+1,"end":min(n+1+60//STEP,count+1),"label":(frame_a+timedelta(minutes=n*STEP)).strftime("%H:%M")} for n in range(count) if (n*STEP)%60==0]
    now_row=None
    if day==today and frame_a<=now.astimezone(tz)<frame_b:
        now_row=(now.astimezone(tz)-frame_a).total_seconds()/60/STEP
    ctx=common(request,"giorno",day,today,places,location,everyone,chosen)
    ctx.update({"prev":day-timedelta(days=1),"next":day+timedelta(days=1),"title":day,
        "columns":columns,"hours":hours,"count":count,"now_row":now_row,"legend":[],"off":[o.name for o in off],
        "closed_day":bool(ops) and not any(works(o) for o in ops),"total":sum(r.status!="cancelled" for r in rows),"writer":writer,"end_row":count+1,
        "first":frame_a.hour*60+frame_a.minute})
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
    rows=visible_bookings(request,Booking.objects.filter(start__lt=end,end__gt=start,status__in=statuses(request))).select_related("service","resource","tenant").order_by("start")
    if location:rows=rows.filter(location=location)
    if chosen:rows=rows.filter(resource=chosen)
    from .clients import mark
    rows=mark(request,rows) if location else []
    writer=can(request,"booking.write")
    now=timezone.now()
    columns=[{"day":d,"col":i,"today":d==today,"items":[],"closed":[],"cells":[]} for i,d in enumerate(days,start=2)]
    for r in rows:
        for d,x in days_of(request,r,tz,color,monday,monday+timedelta(days=6)):columns[(d-monday).days]["items"].append(x)
    ctx=common(request,"settimana",day,today,places,location,ops,chosen)
    sunday=monday+timedelta(days=6)
    title=f"{monday.day}–{sunday.day} {MONTHS[sunday.month-1]} {sunday.year}" if monday.month==sunday.month else f"{monday.day} {MONTHS[monday.month-1]} – {sunday.day} {MONTHS[sunday.month-1]} {sunday.year}"
    ctx.update({"prev":monday-timedelta(days=7),"next":monday+timedelta(days=7),"title_text":title,"columns":columns,"total":sum(r.status!="cancelled" for r in rows),"grid":appointments and bool(ops),"writer":writer})
    if not ctx["grid"]:
        for c in columns:c["items"].sort(key=lambda x:(PHASE_ORDER[x.get("phase","")],x["start"]))
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
        busy={op.pk:[(x["start"],x["end"]) for x in c["items"] if x["booking"].resource_id==op.pk and x["status"]!="cancelled"] for op in shown_ops}
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
    ctx.update({"hours":hours,"count":count,"end_row":count+1,"first":first})
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
    for r in (rows if location else []):
        for d,x in days_of(request,r,tz,color,grid_a,grid_b):by_day.setdefault(d,[]).append(x)
    for items in by_day.values():items.sort(key=lambda x:(PHASE_ORDER[x.get("phase","")],x["start"]))
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
        # a stay is on several days, counted once
        "weeks":weeks,"total":len({x["booking"].pk for w in weeks for c in w if c["inside"] for x in by_day.get(c["day"],[])}),"names":["Lun","Mar","Mer","Gio","Ven","Sab","Dom"]})
    return render(request,"agenda_month.html",ctx)

@require_POST
@json_denied
def drop(request,slug,booking_id):
    """2.14.0: an appointment dragged in the agenda to another time, day or operator, or stretched by its lower edge:
    move_booking with the desk's rules. What it goes beyond comes back as the refusal or, with Forza, as the warning
    to confirm (409), as in the booking form. The keyboard way stays the Sposta page."""
    require(request,"booking.write")
    booking=get_visible(request,booking_id)
    try:
        data=json_body(request);check_force(request,data)
        try:start=local_datetime(date.fromisoformat(str(data.get("date"))),time.fromisoformat(str(data.get("time"))),ZoneInfo(request.tenant.timezone))
        except ValueError:start=None
        if not start:raise BookingError("Giorno o ora non validi.")
        resource=str(data.get("resource") or booking.resource_id or "")
        check_new_booking(request,booking.service,resource)
        move_booking(request.tenant,booking.pk,{"service":str(booking.service_id),"option":str(booking.option_id or ""),"resource":resource,"party":booking.party,
            "start":start.isoformat(),"minutes":data.get("minutes"),"force":data.get("force") is True,"force_ok":data.get("force_ok") is True},request.user)
        return JsonResponse({"ok":True})
    except ForceWarning as ex:return warned(ex)
    except (ValueError,ValidationError,TypeError) as ex:return JsonResponse({"error":getattr(ex,"message",None) or str(ex)},status=400)

def sheet(request,slug):
    """2.14.0: the day on paper, for everyone or one operator, room or court: time, client and service as the agenda
    shows them to this role, people, and the phone and the notes only to who may read them."""
    require(request,"booking.read")
    t=request.tenant;tz=ZoneInfo(t.timezone);now=timezone.now()
    day=chosen_day(request,now.astimezone(tz).date())
    start=datetime.combine(day,time.min,tz)
    rows=visible_bookings(request,Booking.objects.filter(start__lt=start+timedelta(days=1),end__gt=start,status__in=SHOWN)).select_related("service","resource","tenant").order_by("start")
    places,location=place(request,False)
    location=location if request.GET.get("sede") else None
    if location:rows=rows.filter(location=location)
    ops=list(blockable(request))
    chosen=next((o for o in ops if str(o.pk)==request.GET.get("operatore")),None)
    if chosen:rows=rows.filter(resource=chosen)
    groups={}
    for r in rows:
        for _,x in days_of(request,r,tz,{},day,day):groups.setdefault(r.snapshot.get("resource") or x["service"],[]).append(x)
    back=reverse("bookings",args=[slug])+f"?data={day.isoformat()}"+(f"&sede={location.pk}" if location else "")+(f"&operatore={chosen.pk}" if chosen else "")
    return render(request,"agenda_print.html",{"day":day,"now":now,"location":location,"places":places,"ops":ops,"chosen":chosen,"back":back,
        "groups":[{"name":k,"rows":v} for k,v in sorted(groups.items())],"count":rows.count(),"one":units(t.business_type)[0],
        "people":any(x["party"]>1 for v in groups.values() for x in v)})

def blockable(request):
    """2.14.0: the operators, rooms and courts whose time this staff member may block: the ones he books for."""
    rows=Resource.objects.filter(active=True,location__active=True).order_by("name")
    m=getattr(request,"member",None)
    if m and m.resources:return rows.filter(pk__in=m.resources)
    if m and (m.locations or m.role=="operator"):return rows.filter(location_id__in=m.locations or [])
    return rows

def block_view(request,slug,block_id=None):
    """2.14.0: Blocca orario from the agenda: a pause, training or meeting of an operator, once or every week. It takes
    the time as a booking does, but it is not one: it counts in no number. The bookings already there are listed first."""
    from .forms import TimeBlockForm
    from .portal import listed
    require(request,"booking.write")
    allowed=blockable(request)
    block=get_object_or_404(TimeBlock.objects.filter(resource__in=allowed),pk=block_id) if block_id else None
    t=request.tenant;tz=ZoneInfo(t.timezone)
    day=block.day if block else chosen_day(request,timezone.now().astimezone(tz).date())
    back=reverse("bookings",args=[slug])+f"?vista=giorno&data={day.isoformat()}"
    if block and request.method=="POST" and request.POST.get("remove"):
        with transaction.atomic():
            block.delete();audit(t,request.user,"agenda.unblock",block_id)
            from .waitlist import freed
            freed(t)  # 2.14.0: the time is free again: the waiting list looks at it
        messages.success(request,"Blocco tolto: quell'orario si prenota di nuovo.");return redirect(back)
    initial={"day":day}
    if not block:
        who=next((r for r in allowed if str(r.pk)==request.GET.get("operatore")),None)
        if who:initial["resource"]=who.pk
        try:a=time.fromisoformat(request.GET.get("ora",""));initial.update(start=a,end=(datetime.combine(day,a)+timedelta(hours=1)).time())
        except ValueError:pass
    form=TimeBlockForm(request.POST or None,instance=block,tenant=t,resources=allowed,initial=initial)
    need_confirm=False
    if request.method=="POST" and form.is_valid():
        try:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=t.pk)
                obj=form.save()
                # the bookings of that operator under any of his blocks, from that day on
                hit=[str(b.pk)[:8] for b in Booking.objects.filter(resource=obj.resource,status__in=OPEN,end__gt=timezone.now(),start__gte=datetime.combine(obj.day,time.min,tz))
                    if blocked(t,b.busy_start,b.busy_end,obj.resource)]
                if hit and not request.POST.get("confirm_conflicts"):
                    raise ValidationError(f"In quell'orario ci sono già {len(hit)} prenotazioni: "+listed(hit)+". Restano valide: chiamale per spostarle. Conferma esplicitamente per bloccare lo stesso.")
                audit(t,request.user,"agenda.block",obj.pk,kind=obj.reason,weekly=obj.weekly,conflicts=len(hit))
                if block:
                    from .waitlist import freed
                    freed(t)  # a block made shorter frees time
            messages.success(request,"Orario bloccato: online e al banco non si prenota."+(f" {len(hit)} prenotazioni da ricontattare." if hit else ""))
            return redirect(reverse("bookings",args=[slug])+f"?vista=giorno&data={obj.day.isoformat()}")
        except ValidationError as ex:
            form.add_error(None,ex.message);need_confirm="Conferma esplicitamente" in ex.message
    one=units(t.business_type)[0].lower()
    return render(request,"form.html",{"form":form,"title":"Blocca orario" if not block else "Orario bloccato","back":back,"back_label":"Torna all'agenda",
        "description":f"Un periodo in cui l'{one} non si prenota: pausa, formazione, riunione. Toglie il posto come una prenotazione, ma non conta nei numeri. Al banco si passa sopra solo con Forza",
        "need_confirm":need_confirm,"remove_label":"Togli il blocco" if block else ""})

def turns(tenant,day):
    """2.14.0: the turns of a restaurant on a day, open or closed for that day, with the people booked: the turns of the
    services a turni and the lunch and the dinner of the places without time slots."""
    tz=ZoneInfo(tenant.timezone);out=[]
    services=list(Service.objects.filter(Q(kind="shift")|Q(kind="capacity",turnover=False),active=True,location__active=True).select_related("location").order_by("location__name","name"))
    if not services:return None  # not a restaurant: no card
    for s in services:
        if s.kind=="shift":
            first=datetime.combine(day,time.min,tz)
            spans=[(o.start,o.end,o.rule.name if o.rule else "") for o in Occurrence.objects.filter(service=s,active=True,start__gte=first,start__lt=first+timedelta(days=1)).select_related("rule").order_by("start")]
        else:spans=[(a,b,"") for a,b in windows(tenant,day,s) if a.astimezone(tz).date()==day]
        for a,b,name in spans:
            out.append({"service":s,"start":a,"end":b,"name":name or ("Pranzo" if a.astimezone(tz).hour<16 else "Cena"),
                "closed":blocked(tenant,a,b,service=s) is not None,
                "people":sum(Booking.objects.filter(service=s,status__in=OPEN,busy_start__lt=b,busy_end__gt=a).values_list("party",flat=True))})
    return out

def turn_switch(request,slug):
    """2.14.0: Chiudi / Riapri a turn of the restaurant for one day, from the Dashboard. Closing it blocks the online page
    and the desk (Forza aside); the bookings already there are listed first, to call back, and stay valid."""
    require(request,"catalog.write")
    if request.method!="POST":raise PermissionDenied
    t=request.tenant;tz=ZoneInfo(t.timezone)
    try:day=date.fromisoformat(request.POST.get("day",""));start=datetime.fromisoformat(request.POST.get("start",""))
    except ValueError:raise PermissionDenied
    turn=next((x for x in turns(t,day) or [] if str(x["service"].pk)==request.POST.get("service") and x["start"]==start),None)
    if not turn:
        messages.error(request,"Turno non trovato: ricarica la pagina.");return redirect("dashboard",slug)
    s,a,b=turn["service"],turn["start"],turn["end"]
    back=reverse("dashboard",args=[slug])+(f"?turni={day.isoformat()}" if day!=timezone.now().astimezone(tz).date() else "")+"#turni"
    label=f"{turn['name']} delle {a.astimezone(tz).strftime('%H:%M')} di {day.strftime('%d/%m')}"
    if request.POST.get("action")=="open":
        with transaction.atomic():
            gone=[x.pk for _,_,x in block_spans(t,day,day,service=s) if x.reason=="closed" and not x.weekly and x.day==day]
            TimeBlock.objects.filter(pk__in=gone).delete();audit(t,request.user,"agenda.turn_open",s.pk,day=day.isoformat(),start=a.isoformat())
            from .waitlist import freed
            freed(t,a,b)
        messages.success(request,f"{label}: di nuovo aperto, si prenota.");return redirect(back)
    if turn["closed"]:return redirect(back)  # closed meanwhile, from another screen
    held=Booking.objects.filter(service=s,status__in=OPEN,busy_start__lt=b,busy_end__gt=a).select_related("service","tenant").order_by("start")
    if held.exists() and not request.POST.get("confirm"):
        from .portal import to_call
        return render(request,"remove_check.html",{"title":"Prima di chiudere: "+label,"rows":to_call(request,held),
            "text":"Queste prenotazioni sono in questo turno. Chiudendolo restano valide: chiama i clienti per spostarle o annullarle. Online e al banco il turno non si prenota più.",
            "action":reverse("turn_switch",args=[slug]),"fields":[("service",s.pk),("day",day.isoformat()),("start",a.isoformat()),("action","close")],"button":"Chiudi il turno","back":back})
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=t.pk)
        # shortcut: the hours of the turn are blocked for the service, so a turn overlapping them closes too; fine for
        # back-to-back turns (19-21, 21-23), an occurrence flag would be needed for overlapping ones
        TimeBlock.objects.create(tenant=t,service=s,day=day,start=a.astimezone(tz).time(),end=b.astimezone(tz).time(),reason="closed")
        audit(t,request.user,"agenda.turn_closed",s.pk,day=day.isoformat(),start=a.isoformat(),bookings=held.count())
    messages.success(request,f"{label}: chiuso, online e al banco non si prenota."+(f" {held.count()} prenotazioni restano, da ricontattare." if held.exists() else ""))
    return redirect(back)
