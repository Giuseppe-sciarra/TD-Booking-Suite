"""2.10.0: the day of a salon, a studio, a court: one column for each operator, the appointments where they fall.

A free quarter of hour inside someone's working time is a link to a new booking with that operator, day and time
already chosen; the time outside it is greyed. The list of bookings stays one click away (?vista=elenco)."""
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

def open_spans(tenant,day,op):
    """When an operator can be booked that day: the union of what each of his services allows."""
    spans=[]
    for s in Service.objects.filter(active=True,kind="resource",id__in=ServiceResource.objects.filter(resource=op).values("service_id")):
        spans+=windows(tenant,day,s,op)
    return merge(spans)

def day_view(request):
    t=request.tenant;tz=ZoneInfo(t.timezone)
    today=timezone.now().astimezone(tz).date()
    try:day=date.fromisoformat(request.GET.get("data",""))
    except ValueError:day=today
    m=getattr(request,"member",None)
    places=Location.objects.filter(active=True,id__in=Service.objects.filter(active=True,kind="resource").values("location_id")).order_by("name")
    if m and m.locations:places=places.filter(pk__in=m.locations)
    places=list(places)
    location=next((p for p in places if str(p.pk)==request.GET.get("sede")),places[0] if places else None)
    ops=operators(request,location) if location else []
    start=datetime.combine(day,time.min,tz);end=start+timedelta(days=1)
    rows=list(visible_bookings(request,Booking.objects.filter(location=location,start__lt=end,end__gt=start,status__in=SHOWN)).select_related("service","resource").order_by("start")) if location else []
    spans={op.pk:open_spans(t,day,op) for op in ops}
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
    columns=[{"op":op,"name":op.name,"closed":[],"cells":[],"items":[]} for op in ops]
    by_op={c["op"].pk:c for c in columns}
    loose=[r for r in rows if r.resource_id not in by_op]
    if loose:columns.append({"op":None,"name":"Senza "+units(t.business_type)[0].lower(),"closed":[],"cells":[],"items":[]})
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
            data=shown(request,r.data)
            ra,rb=r.start.astimezone(tz),r.end.astimezone(tz)
            c["items"].append({"booking":r,"name":f"{data.get('first_name','')} {data.get('last_name','')}".strip() or "Cliente",
                "service":r.snapshot.get("service") or r.service.name,"from":ra.strftime("%H:%M"),"to":rb.strftime("%H:%M"),
                "row":row_of(max(ra,frame_a)),"end":max(row_of(max(ra,frame_a))+1,row_of(min(rb,frame_b))),"status":r.status})
    hours=[{"row":n+1,"end":min(n+1+60//STEP,count+1),"label":(frame_a+timedelta(minutes=n*STEP)).strftime("%H:%M")} for n in range(count) if (n*STEP)%60==0]
    now_row=None
    if day==today and frame_a<=now.astimezone(tz)<frame_b:
        now_row=(now.astimezone(tz)-frame_a).total_seconds()/60/STEP
    one,many,_=units(t.business_type)
    return render(request,"agenda.html",{"day":day,"today":today,"prev":day-timedelta(days=1),"next":day+timedelta(days=1),
        "columns":columns,"hours":hours,"count":count,"location":location,"places":places,"now_row":now_row,
        "closed_day":bool(ops) and not any(spans.values()),"total":len(rows),"one":one,"many":many,"writer":writer,
        "sede":str(location.pk) if location and len(places)>1 else "","end_row":count+1})
