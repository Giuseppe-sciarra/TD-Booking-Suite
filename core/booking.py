import json,re,secrets
from collections import Counter
from datetime import datetime,date,timedelta,time,timezone as TZ
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext,get_language
from .models import Tenant,Service,Resource,ServiceResource,Option,Occurrence,Booking,Contact,ActionToken,Delivery,audit
from .crypto import encrypt,index,tenant_key,token_hash
from .calendar import windows,fits,local_datetime,blocked_stay,blocked,block_spans
from .middleware import operational
UTC=TZ.utc
class BookingError(ValueError):pass
class ForceWarning(BookingError):
    """2.14.0: Forza at the desk: what the booking goes beyond, said before it is saved (force_ok confirms it)."""
# 2.14.0: the statuses. A request waiting for the business's yes holds its place as a confirmed booking does
HOLDING=("pending","confirmed","arrived","in_progress","completed","no_show")
OPEN=("pending","confirmed","arrived","in_progress")  # not closed yet
ACCEPTED=("confirmed","arrived","in_progress","completed")  # the business said yes and the client did not give up
MOVABLE=("pending","confirmed")
# where each status may go; a closed one goes back to confirmed (a click on the wrong row, a client who comes after all)
MOVES={"pending":{"confirmed","cancelled"},"confirmed":{"arrived","in_progress","completed","no_show","cancelled"},
    "arrived":{"in_progress","completed","cancelled"},"in_progress":{"completed","cancelled"},
    "completed":{"confirmed"},"no_show":{"confirmed"},"cancelled":{"confirmed"}}
UNDO_MINUTES=5
REASONS=[("client","Disdetta del cliente"),("business","Imprevisto dell'attività"),("weather","Maltempo"),("duplicate","Prenotazione doppia"),("other","Altro")]
REASON_LABELS={**dict(REASONS),"rejected":"Richiesta rifiutata","unconfirmed":"Non confermata dal cliente"}
# 2.14.0: what a booking that changes day or time gets again: reminders, message before the arrival, the client's answer
REARM=("reminded","reminded2","arrival","asked","client_confirmed","declined")
def integer(value,label,minimum=1,maximum=10000):
    if isinstance(value,bool) or not re.fullmatch(r"\d+",str(value)):raise BookingError(label+" non valido.")
    val=int(value)
    if not minimum<=val<=maximum:raise BookingError(label+" fuori intervallo.")
    return val
def choice(model,value,**filters):
    source=model.objects if hasattr(model,"objects") else model  # model or pre-scoped queryset
    try:obj=source.filter(pk=value,**filters).first()
    except (ValidationError,ValueError):obj=None
    if not obj:raise BookingError(gettext("Selezione non valida o non disponibile."))
    return obj
def details(service,option_id=None,resource_id=None):
    option=choice(Option,option_id,service=service,active=True) if option_id else None
    # 2.14.0: a cabin or machine is never the one booked, only taken with him (support)
    resource=choice(Resource,resource_id,active=True,secondary=False,location=service.location) if resource_id else None
    if resource and not ServiceResource.objects.filter(service=service,resource=resource).exists():raise BookingError("La risorsa non appartiene al servizio.")
    if service.kind not in ("resource","day") and resource:raise BookingError("Risorsa non prevista.")
    return option,resource,length(service,option,resource)
def length(service,option=None,resource=None,party=1):
    """Minutes of an appointment: the variant's; otherwise the operator's own for this service (2.11.0, Giulia does
    the colour in 75 minutes); otherwise the service's. 2.14.0: a table for 2 frees sooner than one for 6 (party_durations)."""
    if option:return option.duration
    if resource is not None and service.kind=="resource":
        own=ServiceResource.objects.filter(service=service,resource=resource).values_list("duration",flat=True).first()
        if own:return own
    if service.kind=="capacity" and service.turnover:
        return next((m for n,m in sorted(service.party_durations or []) if party<=n),service.duration)
    return service.duration
def over(events,party,cap):
    """The sweep of the places: (moment, +people at the start, -people at the end); True when party does not fit."""
    occupied=0
    for _,delta in sorted(events,key=lambda x:(x[0],x[1])):
        occupied+=delta
        if occupied+party>cap:return True
    return party>cap
def spans(rows,start,end,each=None):
    return [e for r in rows for n in (each or r.party,) for e in ((max(r.busy_start,start),n),(min(r.busy_end,end),-n))]
def shared(service,start,end,party,exclude=None):
    """2.14.0: the covers of the whole place, taken together by every restaurant service there (lunch and the group
    menu): the tightest limit wins. The words of the limit passed, empty when it fits."""
    place=service.location
    if service.kind not in ("capacity","shift") or not place.capacity:return ""
    rows=Booking.objects.filter(status__in=HOLDING,busy_start__lt=end,busy_end__gt=start,location=place,service__kind__in=("capacity","shift")).exclude(pk=exclude)
    return f"oltre i coperti del locale ({place.capacity})" if over(spans(rows,start,end),party,place.capacity) else ""
def paced(service,start,party,exclude=None):
    """2.14.0: the rhythm of the arrivals, for the kitchen: at each time of the page (every `interval` minutes from
    midnight) at most pace_covers people and pace_bookings tables. The words of the limit passed, empty when it fits."""
    if service.kind!="capacity" or not (service.pace_covers or service.pace_bookings):return ""
    local=start.astimezone(ZoneInfo(service.tenant.timezone))
    a=start-timedelta(minutes=(local.hour*60+local.minute)%service.interval,seconds=local.second,microseconds=local.microsecond)
    rows=list(Booking.objects.filter(service=service,status__in=HOLDING,start__gte=a,start__lt=a+timedelta(minutes=service.interval)).exclude(pk=exclude).values_list("party",flat=True))
    if service.pace_covers and sum(rows)+party>service.pace_covers or service.pace_bookings and len(rows)+1>service.pace_bookings:
        return f"oltre il ritmo degli arrivi delle {a.astimezone(local.tzinfo):%H:%M}"
    return ""
def day_cache(tenant,day,resource,service,memo=None):
    """2.22.3: what the slots of one day with one operator need, read once instead of twice per slot (the free times
    of a month for the site went from minutes to seconds): his time blocks and the bookings holding him that day;
    with a memo, the bookings once for all the services of the day."""
    tz=ZoneInfo(tenant.timezone);a=local_datetime(day-timedelta(days=1),time(0),tz);z=local_datetime(day+timedelta(days=2),time(0),tz)
    key=("rows",resource.pk,day)
    if memo is None or key not in memo:
        rows=list(Booking.objects.filter(status__in=HOLDING,busy_start__lt=z,busy_end__gt=a).filter(Q(resource=resource)|Q(secondary=resource)|Q(more__icontains=str(resource.pk))))
        if memo is not None:memo[key]=rows
    else:rows=memo[key]
    return {"spans":block_spans(tenant,day-timedelta(days=1),day+timedelta(days=1),resource,service),"rows":rows}
def capacity_available(service,start,end,party,resource=None,occurrence=None,exclude=None,blocks=True,online=False,rooms=True,gap=None,cache=None):
    """online (2.14.0): the places sold online (online_capacity), the rest kept for the desk; rooms: the covers of the
    whole place (shared), left out where the caller says them apart (Forza). gap: the posa of the booking being
    checked: the operator only needs to be free before and after it. cache (2.22.3): day_cache of this operator."""
    if gap and resource:return all(capacity_available(service,x,y,party,resource,occurrence,exclude,blocks,online,rooms,cache=cache) for x,y in ((start,gap[0]),(gap[1],end)))
    # 2.14.0: an operator's pause, a turn closed for the night: the time is taken as by a booking (Forza says it apart)
    if blocks and (any(a<end and start<z for a,z,b in cache["spans"]) if cache else blocked(service.tenant,start,end,resource,service)):return False
    excl=[] if not exclude else exclude if isinstance(exclude,list) else [exclude]  # a list: the services of a visit being moved
    if cache and resource:rows=[r for r in cache["rows"] if r.busy_start<end and r.busy_end>start and r.pk not in excl];cap=1;party=1
    else:
     rows=Booking.objects.filter(status__in=HOLDING,busy_start__lt=end,busy_end__gt=start)
     if excl:rows=rows.exclude(pk__in=excl)
     # 2.14.0: a cabin or a machine is also taken by the bookings that need it next to their operator
     if resource:rows=rows.filter(Q(resource=resource)|Q(secondary=resource)|Q(more__icontains=str(resource.pk)));cap=1;party=1
     elif occurrence:rows=rows.filter(occurrence=occurrence);cap=occurrence.capacity
     else:
        rows=rows.filter(service=service);cap=service.capacity
        if online and service.online_capacity:cap=min(cap,service.online_capacity)
    events=[]
    for row in rows:
        n=1 if resource else row.party
        lo,hi=max(row.busy_start,start),min(row.busy_end,end)
        # 2.14.0: inside another client's posa the operator is free (the cabin is not: the client is still there)
        if resource and row.gap_start and row.resource_id==resource.pk and row.gap_start<=lo and hi<=row.gap_end:continue
        events.extend([(lo,n),(hi,-n)])
    if service.tenant.wait_mode=="queue":
        from .waitlist import holds
        events+=holds(service,start,end,resource,occurrence)  # 2.14.0: places kept for the first in the waiting list
    if over(events,party,cap):return False
    if occurrence and service.kind=="shift":return capacity_available(service,start,end,party,exclude=exclude,blocks=False,online=online,rooms=rooms)
    return bool(resource) or not rooms or not shared(service,start,end,party,exclude)
def linked(service,secondary=False):
    """The active operators, rooms or units of a service, in name order; secondary (2.14.0): the cabins, machines,
    cars it also needs, any one of them."""
    return Resource.objects.filter(active=True,secondary=secondary,location=service.location,id__in=ServiceResource.objects.filter(service=service).values("resource_id")).order_by("name")
def posa(service,start,end,resource):
    """2.14.0: the posa of an appointment (work, posa, finish): the client stays, the operator is free for another.
    The operator's own posa for this service when he has one (as his duration), otherwise the service's; asked once
    per service object, as the slots of a day call this for every time."""
    if not (resource and service.kind=="resource"):return None
    memo=service.__dict__.setdefault("_posa",{})
    if resource.pk not in memo:
        own=ServiceResource.objects.filter(service=service,resource=resource).values_list("pause_start","pause_minutes").first()
        memo[resource.pk]=(own[0] or 0,own[1]) if own and own[1] is not None else (service.pause_start,service.pause_minutes)
    at,minutes=memo[resource.pk]
    if not minutes:return None
    a=start+timedelta(minutes=at);b=a+timedelta(minutes=minutes)
    return (a,b) if start<a and b<end else None
def groups(pool):
    """2.14.0: the cabins and machines of a service by group (cabin AND machine), in name order."""
    out={}
    for r in pool:out.setdefault(r.group.strip().casefold(),[]).append(r)
    return list(out.values())
def support(service,bs,be,exclude=None,pool=None):
    """2.14.0: the first free cabin or machine of each group the service needs, a list; None when it needs none,
    False when a group has none free."""
    pool=list(linked(service,True)) if pool is None else pool
    if not pool:return None
    got=[next((r for r in g if capacity_available(service,bs,be,1,r,exclude=exclude)),None) for g in groups(pool)]
    return False if None in got else got
def equip(booking,second):
    """The cabins and machines taken with the operator, one per group: the first in secondary, the others in more."""
    booking.secondary=second[0] if second else None;booking.more=[str(r.pk) for r in second[1:]] if second else []
    booking.snapshot["secondary"]=", ".join(r.name for r in second) if second else ""
def ranked(service,resources,day,prefer=None):
    """2.14.0: who gets "Il primo disponibile", as its location says; those excluded from it stay out.
    prefer: the operator of the client's last visit (last_operator), first when the location assigns so."""
    way=service.location.assign;resources=[r for r in resources if not r.auto_excluded]
    if way=="even":
        a=local_datetime(day,time(0),ZoneInfo(service.tenant.timezone))
        load=Counter(Booking.objects.filter(resource__in=resources,status__in=HOLDING,start__gte=a,start__lt=a+timedelta(days=1)).values_list("resource_id",flat=True))
        return sorted(resources,key=lambda r:(load[r.pk],r.name))
    if way=="priority":return sorted(resources,key=lambda r:(r.priority,r.name))
    if way=="last" and prefer:return sorted(resources,key=lambda r:(r.pk!=prefer,r.name))
    return resources
def last_operator(tenant,service,pii,data,staff):
    """2.14.0: the operator of the client's last visit, for the locations that give it back to him."""
    if service.location.assign!="last" or data.get("resource"):return None
    rows=Booking.objects.filter(resource__isnull=False,status__in=ACCEPTED)
    try:
        if staff and data.get("contact"):return rows.filter(contact_id=data["contact"]).order_by("-start").values_list("resource_id",flat=True).first()
        if pii.get("email"):return rows.filter(contact__email_index=index(pii["email"],tenant_key(tenant),"email")).order_by("-start").values_list("resource_id",flat=True).first()
    except (ValidationError,ValueError):pass
    return None
def kit(service,start,end,bs,be,resource,issues,exclude=None):
    """2.14.0: the posa and the cabin or machine of an appointment; none of them free is said as Forza says the rest."""
    pool=list(linked(service,True)) if service.kind=="resource" else []
    second=support(service,bs,be,exclude,pool)
    if second is False:
        # Forza: the groups with one free keep it, the others are said
        second=[]
        for g in groups(pool):
            r=next((r for r in g if capacity_available(service,bs,be,1,r,exclude=exclude)),None)
            if r:second.append(r)
            else:issues.append("nessuna risorsa libera tra "+", ".join(x.name for x in g))
    return posa(service,start,end,resource) or (None,None),second
def holes(rows,bs,be,minutes):
    """2.14.0: a time that would leave less than `minutes` free next to another appointment of the operator, a time
    block or the opening and closing of his hours (rows: (start, end), the edges as (moment, moment))."""
    n=timedelta(minutes=minutes)
    return any(timedelta(0)<bs-e<n or timedelta(0)<s-be<n for s,e in rows)
def has_units(service):
    return service.kind=="day" and ServiceResource.objects.filter(service=service).exists()
def free_units(tenant,service,bs,be,party,resource=None,exclude=None):
    """Named units of a day service free for the whole interval, in name order."""
    units=linked(service)
    if resource:units=units.filter(pk=resource.pk)
    return [u for u in units if u.housekeeping!="out" and (u.capacity==0 or u.capacity>=party)
        and capacity_available(service,bs,be,1,resource=u,exclude=exclude)
        and not blocked_stay(tenant,bs,be,service,u)]
PERIODS=(("day","al giorno"),("week","a settimana"),("month","al mese"))
def limited(tenant,owner,day,minutes,exclude=None,cache=None):
    """2.14.0: the limits of a service or an operator (owner.limits): what one more booking of `minutes` that day goes
    beyond, in words; "" within. Counted on the bookings holding a place, in the business's day, ISO week and month."""
    lim=owner.limits or {}
    if not any(lim.values()):return ""
    key=(owner.pk,day);tz=ZoneInfo(tenant.timezone)
    if cache is None or key not in cache:
        week=day-timedelta(days=day.weekday());month=day.replace(day=1);nxt=(month+timedelta(days=32)).replace(day=1)
        rows=Booking.objects.filter(**{"resource" if isinstance(owner,Resource) else "service":owner},status__in=HOLDING,
            start__gte=local_datetime(min(week,month),time(0),tz),start__lt=local_datetime(max(week+timedelta(days=7),nxt),time(0),tz)).exclude(pk__in=exclude if isinstance(exclude,list) else [exclude]).values_list("start","end")
        used={p:[0,0] for p,_ in PERIODS}
        for a,b in rows:
            d=a.astimezone(tz).date()
            for p,inside in (("day",d==day),("week",week<=d<week+timedelta(days=7)),("month",month<=d<nxt)):
                if inside:used[p][0]+=1;used[p][1]+=int((b-a).total_seconds()//60)
        if cache is None:return over_limit(lim,used,minutes,owner)
        cache[key]=used
    return over_limit(lim,cache[key],minutes,owner)
def over_limit(lim,used,minutes,owner):
    for p,words in PERIODS:
        n,m=lim.get(p+"_n") or 0,lim.get(p+"_m") or 0
        if n and used[p][0]+1>n:return f"oltre le {n} prenotazioni {words} di {owner.name}"
        if m and used[p][1]+minutes>m:return f"oltre i {m} minuti prenotati {words} di {owner.name}"
    return ""
def away(r,day):
    """2.14.0: an operator fuori ufficio that day."""
    return bool(r and r.away_from and r.away_until and r.away_from<=day<=r.away_until)
def candidates(tenant,service,day,option=None,resource=None,desk=False,force=False,party=1,memo=None):
    """desk (2.14.0): lead time, horizon and last arrival are the public page's, the desk books from now on;
    force: Forza lists every date of an event of that day, also started, full or outside the hours."""
    now=timezone.now()
    duration=timedelta(minutes=length(service,option,resource,party))
    before=timedelta(minutes=service.buffer_before);after=timedelta(minutes=service.buffer_after)
    lead=now if desk else now+timedelta(minutes=service.lead_minutes)
    horizon=now+timedelta(days=730 if desk else service.horizon_days)
    tz=ZoneInfo(tenant.timezone)
    if service.kind in ("event","shift"):
        rows=Occurrence.objects.filter(service=service,active=True,start__lte=horizon)
        for event in (rows if force else rows.filter(start__gte=lead)).order_by("start"):
            if event.start.astimezone(tz).date()==day and (force or fits(tenant,event.start-before,event.end+after,service)):
                yield event.start,event.end,event
        return
    if service.kind=="day":return
    whole=service.kind=="capacity" and not service.turnover
    last=timedelta(minutes=service.last_arrival if service.kind=="capacity" and not desk else 0)  # 2.14.0: no table offered at closing time
    for a,b in windows(tenant,day,service,resource,memo=memo):
        tick=a
        while tick<b:
            # without time slots a booking holds its places until the window closes: lunch and dinner each have their seats
            end=b-after if whole else tick+duration
            if tick.astimezone(tz).date()==day and lead<=tick<=horizon and tick-before>=a and end+after<=b and tick<=b-last:
                yield tick,end,None
            tick+=timedelta(minutes=min(service.interval,15) if desk and service.kind=="resource" else service.interval)  # 2.22.2: the desk books on the agenda's quarters
def available(tenant,service_id,day,option_id=None,resource_id=None,party=1,exclude=None,allowed=None,desk=False,force=False,prefer=None,area=None,memo=None):
    """Free slots of a day. allowed: resource ids a scoped staff member may book ("Il primo disponibile" stays among them).
    desk and force: see candidates; the desk also books groups bigger than the online limit (max_party), within the places.
    2.14.0, restaurants: the covers of the whole place; online the places sold online and the rhythm of the arrivals
    (at the desk too with pace_desk). prefer: see ranked. area: the room the client asked for, with its own covers;
    online only the times with a table free, where the location says so (tables.py). memo (2.22.3): a dict kept by a
    caller asking many days and operators (the site's snapshot): the opening hours shared between them are read once."""
    from .tables import pick_area,area_over,needs_table,fit
    service=choice(Service,service_id,active=True,location__active=True)
    option,resource,_=details(service,option_id,resource_id)
    area=pick_area(service,area);table=needs_table(service,desk)
    party=integer(party,"Quantità",1,1000 if desk else service.max_party)
    pace=not desk or service.pace_desk
    tz=ZoneInfo(tenant.timezone)
    today=timezone.now().astimezone(tz).date()
    if (day<today and not force) or day>today+timedelta(days=730 if desk else min(service.horizon_days,730)):return []
    resources=[resource]
    if service.kind=="resource" and not resource:
        resources=list(linked(service))
        if allowed is not None:resources=[r for r in resources if str(r.pk) in allowed]
        resources=ranked(service,resources,day,prefer)
        # 2.14.0: the stand-in of who is fuori ufficio takes "Il primo disponibile" first
        subs={r.away_by_id for r in resources if away(r,day)}
        resources=sorted(resources,key=lambda r:r.pk not in subs)
    if not force:resources=[r for r in resources if not away(r,day)]
    limits={}
    rank={str(r.pk) if r else "":i for i,r in enumerate(resources)}
    pool=list(linked(service,True)) if service.kind=="resource" else []
    gap=service.location.min_gap if service.kind=="resource" and not desk else 0
    if gap:a=local_datetime(day,time(0),tz);a,b=a-timedelta(days=1),a+timedelta(days=2)
    result=[]
    for res in resources:
        near=list(Booking.objects.filter(resource=res,status__in=HOLDING,busy_start__lt=b,busy_end__gt=a).exclude(pk__in=exclude if isinstance(exclude,list) else [exclude]).values_list("busy_start","busy_end")) if gap else []
        if gap:near+=[(x,x) for w in windows(tenant,day,service,res,memo=memo) for x in w]+[(x,y) for x,y,_ in block_spans(tenant,day,day,res)]
        cache=day_cache(tenant,day,res,service,memo) if res and service.kind=="resource" else None
        for start,end,event in candidates(tenant,service,day,option,res,desk,force,party,memo):
            bs=start-timedelta(minutes=service.buffer_before);be=end+timedelta(minutes=service.buffer_after)
            if force or (capacity_available(service,bs,be,party,res,event,exclude,online=not desk,gap=posa(service,start,end,res),cache=cache) and not (pace and paced(service,start,party,exclude)) and support(service,bs,be,exclude,pool) is not False and not (gap and holes(near,bs,be,gap)) and not area_over(area,bs,be,party,exclude) and not (table and not fit(service.location,party,bs,be,area,exclude))):
                if not desk and not force and any(limited(tenant,x,day,int((end-start).total_seconds()//60),exclude,limits) for x in (service,res) if x):continue
                result.append({"start":start.astimezone(tz).isoformat(),"end":end.astimezone(tz).isoformat(),"resource":str(res.pk) if res else "","resource_name":res.name if res else "","occurrence":str(event.pk) if event else ""})
    result=sorted(result,key=lambda x:(x["start"],rank[x["resource"]]))
    if service.kind=="resource" and not resource:
        # "Il primo disponibile": one slot per time, the first free resource; cut after this, or many operators hide the evening
        seen=set();result=[x for x in result if not (x["start"] in seen or seen.add(x["start"]))]
    return result[:500]
VISIT_MAX=5
def visit_services(first,extra):
    """2.14.0: the services added after the first to a visit (ids, a list or "a,b"): appointments of the same location."""
    ids=[str(x) for x in (extra.split(",") if isinstance(extra,str) else extra if isinstance(extra,list) else []) if x]
    if len(ids)>VISIT_MAX-1:raise BookingError(f"Al massimo {VISIT_MAX} servizi in una visita.")
    rest=[choice(Service,x,active=True,location=first.location) for x in ids]
    if any(x.kind!="resource" for x in [first,*rest]):raise BookingError("Più servizi nella stessa visita solo per gli appuntamenti con un operatore.")
    return [first,*rest]
def piece(tenant,service,option,r,start,taken,exclude,desk,day,cache):
    """2.14.0: one service of a visit at an exact time with r: (service,option,r,start,end,bs,be,gap,second), or None.
    taken: the services of the visit already placed, not saved yet: their operator and cabin are busy for them.
    cache: what the database said for this day, asked once (opening windows, cabins, operator free at a time)."""
    end=start+timedelta(minutes=length(service,option,r))
    bs=start-timedelta(minutes=service.buffer_before);be=end+timedelta(minutes=service.buffer_after)
    clash=lambda x:any(x.pk in (p[2].pk,*(y.pk for y in p[8] or ())) and bs<p[6] and p[5]<be for p in taken)
    if clash(r):return None
    key=(service.pk,r.pk,start)
    if key not in cache:
        if ("w",service.pk,r.pk) not in cache:cache[("w",service.pk,r.pk)]=windows(tenant,day,service,r,memo=cache)
        cache[key]=any(a<=bs and be<=b for a,b in cache[("w",service.pk,r.pk)]) and capacity_available(service,bs,be,1,r,exclude=exclude,online=not desk,gap=posa(service,start,end,r))
    if not cache[key]:return None
    if ("need",service.pk) not in cache:cache[("need",service.pk)]=list(linked(service,True))
    need=cache[("need",service.pk)]
    second=[next((x for x in g if not clash(x) and capacity_available(service,bs,be,1,x,exclude=exclude)),None) for g in groups(need)] if need else None
    return None if second and None in second else (service,option,r,start,end,bs,be,posa(service,start,end,r) or (None,None),second)
def chain(tenant,items,first,start,split,day,wait,exclude,desk,allowed,cache):
    """2.14.0: a visit from start, its first service with first; each next one as soon as the previous ends, waiting at
    most wait; split: also with other operators (first stays the first choice). The services placed, or None."""
    plan=[piece(tenant,*items[0],first,start,[],exclude,desk,day,cache)]
    if not plan[0]:return None
    for service,option in items[1:]:
        if ("ops",service.pk) not in cache:cache[("ops",service.pk)]=ranked(service,[r for r in linked(service) if allowed is None or str(r.pk) in allowed],day)
        mine=cache[("ops",service.pk)]
        ops=([first] if first in mine else [])+([r for r in mine if r!=first] if split else [])
        t=plan[-1][4];found=None
        while t<=plan[-1][4]+wait and not found:
            found=next((x for r in ops if (x:=piece(tenant,service,option,r,t,plan,exclude,desk,day,cache))),None);t+=timedelta(minutes=5)
        if not found:return None
        plan.append(found)
    return plan
def visit_slots(tenant,items,day,resource=None,exclude=None,desk=False,allowed=None):
    """2.14.0: the times a visit of several services (items: [(service, option)]) can start: the first service's free
    times as available finds them, then the others one after the other (chain); one per time, as "Il primo disponibile".
    The location says if the services may go to different operators (visit_split) and the longest wait between two."""
    service,option=items[0];place=service.location
    ops=[resource] if resource else ranked(service,[r for r in linked(service) if allowed is None or str(r.pk) in allowed],day)
    firsts={r:available(tenant,service.pk,day,option and option.pk,r.pk,1,exclude,desk=desk) for r in ops}
    wait=timedelta(minutes=place.visit_wait);out={}
    # the first service's times are free already (available)
    cache={(service.pk,r.pk,datetime.fromisoformat(x["start"]).astimezone(UTC)):True for r in ops for x in firsts[r]}
    for split in {"same":(False,),"split":(True,),"need":(False,True)}[place.visit_split]:
        for r in ops:
            for slot in firsts[r]:
                if slot["start"] not in out and (plan:=chain(tenant,items,r,datetime.fromisoformat(slot["start"]).astimezone(UTC),split,day,wait,exclude,desk,allowed,cache)):out[slot["start"]]=plan
    tz=ZoneInfo(tenant.timezone)
    return [{"start":k,"end":plan[-1][4].astimezone(tz).isoformat(),"resource":str(plan[0][2].pk),"resource_name":" + ".join(dict.fromkeys(p[2].name for p in plan)),"occurrence":"","plan":plan} for k,plan in sorted(out.items())]
def visit_rows(booking):
    """2.14.0: the services of a booking's visit in order (the cancelled ones only when it is cancelled too); [booking] alone."""
    if not booking.visit:return [booking]
    rows=Booking.objects.filter(visit=booking.visit).select_related("service").order_by("start")
    return list(rows if booking.status=="cancelled" else rows.exclude(status="cancelled")) or [booking]
def visit_plan(tenant,first,option,extra,data,exclude=None,desk=False,allowed=None):
    """The services placed for the time asked (data["start"], data["resource"] for the first), or why not."""
    items=[(x,None) for x in visit_services(first,extra)];items[0]=(first,option)
    try:start=datetime.fromisoformat(str(data["start"]))
    except (KeyError,ValueError):raise BookingError(gettext("Data o ora non valida."))
    if start.tzinfo is None:raise BookingError("Fuso orario mancante.")
    resource=details(first,None,data.get("resource") or None)[1]
    slots=visit_slots(tenant,items,start.astimezone(ZoneInfo(tenant.timezone)).date(),resource,exclude,desk,allowed)
    plan=next((x["plan"] for x in slots if datetime.fromisoformat(x["start"])==start),None)
    if not plan:raise BookingError(gettext("Orario non disponibile. Aggiorna le disponibilità."))
    return plan
def create_visit(tenant,data,actor,origin,source,wait,allowed):
    """2.14.0: several services in one visit: the time checked for the whole visit, then one booking per service tied by
    visit; a request when one of them asks for it; one email to the client and one notice to the staff, for the visit."""
    import uuid
    if data.get("walkin") is True or data.get("force") is True:raise BookingError("Più servizi insieme si prenotano su un orario libero, senza forzare.")
    first=choice(Service,data.get("service"),active=True,location__active=True)
    plan=visit_plan(tenant,first,details(first,data.get("option"))[0],data.get("extra"),data,desk=actor is not None,allowed=allowed)
    vid=uuid.uuid4();key=str(data["request_key"]);made=[]
    for i,p in enumerate(plan):
        b,_=create_booking(tenant,data if i==0 else {**data,"request_key":f"{key[:58]}-v{i}"},actor,origin,source,wait if i==0 else "",visit=(vid,p,first))
        made.append(b)
    if any(b.status=="pending" for b in made):Booking.objects.filter(visit=vid,status="confirmed").update(status="pending");made[0].status="pending"
    from .client_mails import send
    from .notify import notify_staff
    send(tenant,made[0],"requested" if made[0].status=="pending" else "confirmed")
    notify_staff(tenant,made[0],"requested" if made[0].status=="pending" else "created")
    return made[0],True
def normalize_phone(value):
    """2.6.1: Italian numbers as people write them. 333 123 4567, 06 1234567, 0039..., +39... all become +39...;
    a foreign number keeps its own prefix. Empty when it cannot be a phone number."""
    raw=re.sub(r"[\s\-./()]","",value or "")
    if raw.startswith("00"):raw="+"+raw[2:]
    if not raw.startswith("+"):raw="+39"+raw
    return raw if re.fullmatch(r"\+[1-9]\d{6,14}",raw) else ""
def personal(data,service,staff=False,walkin=False):
    if staff:
        # 2.7.0: a booking taken at the desk or on the phone: surname and email are optional, phone or email is enough
        # 2.14.0: a client without appointment may leave nothing at all
        from .clients import clean
        result=clean({**data,"first_name":data.get("first_name") or "Cliente di passaggio"} if walkin else data,require_contact=not walkin)
        return {**result,**extras(data,service)}
    result={}
    for key in ("first_name","last_name","email","phone"):
        value=data.get(key,"")
        if not isinstance(value,str):raise BookingError("Recapito non valido: "+key)
        val=value.strip()
        if not val or len(val)>(254 if key=="email" else 100) or any(ord(c)<32 for c in val):raise BookingError(gettext("Compila correttamente nome, cognome, email e telefono."))
        result[key]=val
    try:validate_email(result["email"])
    except ValidationError:raise BookingError(gettext("Email non valida."))
    result["email"]=result["email"].casefold()
    result["phone"]=normalize_phone(result["phone"])
    if not result["phone"]:raise BookingError(gettext("Numero di telefono non valido, per esempio 333 123 4567."))
    return {**result,**extras(data,service)}
def shown_value(answer):return ("sì" if answer else "no") if type(answer) is bool else str(answer if answer is not None else "")
def extras(data,service):
    """Notes and the service's own questions, the same for online and staff bookings."""
    result={}
    if not isinstance(data.get("notes",""),str):raise BookingError("Note non valide.")
    result["notes"]=data.get("notes","").strip()
    if len(result["notes"])>4000:raise BookingError("Note troppo lunghe.")
    # 2.8.0: allergies and intolerances, only from the list the business asks about
    asked=service.tenant.asked_allergens
    picked=(data.get("allergies") or []) if asked else []
    if not isinstance(picked,list) or any(not isinstance(a,str) or a not in asked for a in picked):raise BookingError("Intolleranze non valide: ricarica la pagina e riprova.")
    if picked:result["allergies"]=[a for a in asked if a in picked]
    answers=data.get("answers",{})
    if not isinstance(answers,dict):raise BookingError("Campi aggiuntivi non validi.")
    result["answers"]={}
    for field in service.fields:
        # 2.14.0: a question shown only for an answer to an earlier one: otherwise not asked, not kept
        if (cond:=field.get("show_if")) and shown_value(result["answers"].get(cond["key"]))!=cond["value"]:continue
        val=answers.get(field["key"],"")
        if field["type"]=="checkbox":
            if val=="" or val is None:val=False
            if type(val) is not bool:raise BookingError("Valore sì/no non valido: "+field["label"])
            if field.get("required") and not val:raise BookingError("Compila: "+field["label"])
        elif field.get("required") and (val is None or val==""):
            raise BookingError("Compila: "+field["label"])
        if field["type"]=="checkbox":pass
        elif field["type"]=="select":
            if val and val not in field["choices"]:raise BookingError("Scelta non valida: "+field["label"])
        elif field["type"]=="number":
            if val not in ("",None):val=integer(val,field["label"],0,100000)
        else:
            if not isinstance(val,str):raise BookingError("Testo non valido: "+field["label"])
            if len(val)>500:raise BookingError("Campo troppo lungo.")
        result["answers"][field["key"]]=val
    return result
def bounds(tenant,service,data,exclude=None,desk=False,force=False,prefer=None):
    """Where and when a booking goes, checked. desk (2.14.0): the rules of the public page (lead time, horizon, last
    arrival, people per booking) do not bind the staff. force: Forza at the desk, any time typed in (in the business's
    time); what it goes beyond comes back in the last value, in words, instead of a refusal (stays excluded)."""
    option,resource,duration=details(service,data.get("option"),data.get("resource"))
    party=integer(data.get("party",1),"Quantità",1,1000 if desk else service.max_party)
    tz=ZoneInfo(tenant.timezone);now=timezone.now()
    try:start=datetime.fromisoformat(str(data["start"]))
    except (KeyError,ValueError):raise BookingError(gettext("Data o ora non valida."))
    if force and start.tzinfo is None:start=local_datetime(start.date(),start.time(),tz)
    if start is None:raise BookingError(gettext("Data o ora non valida."))
    if start.tzinfo is None:raise BookingError("Fuso orario mancante.")
    start=start.astimezone(UTC)
    day=start.astimezone(tz).date();occurrence=None;issues=[]
    before=timedelta(minutes=service.buffer_before);after=timedelta(minutes=service.buffer_after)
    # 2.14.0: an appointment dragged or stretched in the agenda keeps its own length: every rule checked one by one,
    # as with Forza, so a refusal says why (slots of the standard length would not match)
    minutes=integer(data["minutes"],"Durata",5,1440) if desk and service.kind=="resource" and data.get("minutes") not in (None,"") else None
    if service.kind=="day":
        try:end_day=date.fromisoformat(str(data["end_date"]))
        except (ValueError,KeyError):raise BookingError(gettext("Seleziona la data di partenza."))
        nights=(end_day-day).days
        # 2.14.0: 0 nights minimum is the rental of a day, picked up and returned the same day (end>start below)
        if 0<=nights<service.min_days:raise BookingError(f"Durata minima: {service.min_days} giorni.")
        if nights>service.max_days:raise BookingError(f"Durata massima: {service.max_days} giorni.")
        from .stays import restricted
        if why:=restricted(service,day,end_day):raise BookingError(why)
        clock_in=service.checkin if isinstance(service.checkin,time) else time.fromisoformat(service.checkin)
        clock_out=service.checkout if isinstance(service.checkout,time) else time.fromisoformat(service.checkout)
        expected=local_datetime(day,clock_in,tz);end=local_datetime(end_day,clock_out,tz)
        if not expected or not end or start!=expected.astimezone(UTC):raise BookingError("Ora di arrivo non valida.")
        end=end.astimezone(UTC)
        if end<=start:raise BookingError(gettext("La partenza viene prima dell'arrivo: scegli un'altra data di partenza."))
        if (day<now.astimezone(tz).date()) if desk else not now+timedelta(minutes=service.lead_minutes)<=start<=now+timedelta(days=service.horizon_days):raise BookingError(gettext("Data fuori dall'intervallo prenotabile."))
        if not fits(tenant,start,start+timedelta(microseconds=1),service,purpose="arrival") or not fits(tenant,end,end+timedelta(microseconds=1),service,purpose="departure") or blocked_stay(tenant,start,end,service):
            raise BookingError(gettext("Arrivo, partenza o soggiorno non consentiti dal calendario."))
        bs=start-before;be=end+after
        if has_units(service):
            # The same named unit for every night of the stay.
            free=free_units(tenant,service,bs,be,party,resource,exclude)
            if not free:raise BookingError("Nessuna unità disponibile per le date scelte." if not resource else "L'unità scelta non è disponibile per queste date.")
            resource=free[0]
        elif not capacity_available(service,bs,be,party,exclude=exclude):raise BookingError(gettext("Disponibilità esaurita."))
    elif force or minutes:
        if service.kind in ("event","shift"):
            occurrence=choice(Occurrence,data.get("occurrence"),service=service,active=True);start=occurrence.start
        elif service.kind=="resource" and not resource:
            # "Il primo disponibile" at the time typed in: the first one free then, otherwise the desk chooses with whom
            for r in ranked(service,list(linked(service)),day,prefer):
                e=start+timedelta(minutes=length(service,option,r))
                if fits(tenant,start-before,e+after,service,r) and capacity_available(service,start-before,e+after,party,r,exclude=exclude,gap=posa(service,start,e,r)):resource=r;break
            else:raise BookingError(gettext("Nessuno è libero a quell'ora: scegli con chi."))
        if occurrence:end=occurrence.end
        elif service.kind=="capacity" and not service.turnover:
            # a table holds its places until that opening window closes, as on the page
            end=next((b-after for a,b in windows(tenant,day,service) if a<=start<b),start+timedelta(minutes=service.duration))
        else:end=start+timedelta(minutes=minutes or length(service,option,resource,party))
        bs=start-before;be=end+after
        if start<now-timedelta(minutes=1):issues.append("nel passato")
        if not fits(tenant,bs,be,service,resource):issues.append("fuori dall'orario di apertura")
        block=blocked(tenant,bs,be,resource,service)
        if block:issues.append(f"nell'orario bloccato di {resource.name} ({block.get_reason_display().lower()})" if block.resource_id else "in un turno chiuso")
        if not capacity_available(service,bs,be,party,resource,occurrence,exclude,blocks=False,rooms=False,gap=posa(service,start,end,resource)):
            issues.append(f"sovrapposta a un altro appuntamento di {resource.name}" if resource else f"oltre i posti disponibili ({occurrence.capacity if occurrence else service.capacity})")
        from .tables import pick_area,area_over
        if away(resource,day):issues.append(f"{resource.name} è fuori ufficio")
        issues+=[x for x in (limited(tenant,o,day,int((end-start).total_seconds()//60),exclude) for o in (service,resource) if o) if x]
        if not resource:issues+=[x for x in (shared(service,bs,be,party,exclude),service.pace_desk and paced(service,start,party,exclude),area_over(pick_area(service,data.get("area")),bs,be,party,exclude)) if x]
    else:
        slots=available(tenant,service.pk,day,data.get("option"),data.get("resource"),party,exclude,desk=desk,prefer=prefer,area=data.get("area"))
        match=next((s for s in slots if datetime.fromisoformat(s["start"]).astimezone(UTC)==start and (not data.get("occurrence") or s["occurrence"]==str(data["occurrence"]))),None)
        if not match:raise BookingError(gettext("Orario non disponibile. Aggiorna le disponibilità."))
        resource=choice(Resource,match["resource"]) if match["resource"] else None
        occurrence=choice(Occurrence,match["occurrence"]) if match["occurrence"] else None
        end=datetime.fromisoformat(match["end"]).astimezone(UTC)
        bs=start-before;be=end+after
    return start,end,bs,be,party,option,resource,occurrence,issues
def overrun(issues,data):
    """2.14.0: what a forced booking or a client without appointment goes beyond: without Forza a refusal, with Forza
    a warning until the desk confirms it."""
    if not issues:return
    if data.get("force") is not True:raise BookingError("Adesso non si può: "+", ".join(issues)+". Scegli con chi o chiedi a chi può forzare.")
    if data.get("force_ok") is not True:raise ForceWarning("Stai forzando: "+", ".join(issues)+". Se va bene, conferma.")
def needs_request(service,party,pii,contact):
    """2.14.0: an online booking waits for the business's yes when its service says so (immediate confirmation stays
    the default), or when its client did not come as many times as the business says (Al banco)."""
    n=service.tenant.absences_request
    return (service.request_all or 0<service.request_above<party or (service.request_allergies and bool(pii.get("allergies")))
        or bool(n and contact and contact.pk and Booking.objects.filter(contact=contact,status="no_show").count()>=n))
def refused(tenant,service):
    """2.14.0: the words for a client the business made not bookable online: neutral, with the phone to call."""
    phone=service.location.phone
    return BookingError("Per prenotare contatta l'attività"+(f": {phone}." if phone else "."))
def action_token(tenant,kind,contact=None,booking=None,days=30):
    token=secrets.token_urlsafe(32)
    ActionToken.objects.create(tenant=tenant,digest=token_hash(token),kind=kind,contact=contact,booking=booking,expires=timezone.now()+timedelta(days=days))
    return token
def queue(tenant,payload,contact=None,campaign=None):
    delivery=Delivery(tenant=tenant,contact=contact,campaign=campaign,next_attempt=timezone.now())
    delivery.payload=encrypt(tenant,payload,"delivery:"+str(delivery.pk));delivery.save()
    return delivery
def create_booking(tenant,data,actor=None,origin="public",source="",wait="",series=None,visit=None,allowed=None,late=False):
    """origin and source (2.14.0): the channel of an online booking, from the signed ticket of its page (public.py);
    the staff's bookings are the desk's, or of a client without appointment (walkin: now, already in progress).
    wait: the waiting-list entry whose link opened the page: the place kept for it is its own (waitlist.claim).
    series (2.14.0): a date repeated from the desk: tied to the others, without its own email and notice.
    data["extra"] (2.14.0): more services in the same visit (create_visit); visit: (id, the service placed, the first
    service, whose questions the client answered), the email and the notice are the visit's. allowed: staff_resources.
    late (2.17.0): a booking taken on the WordPress site while the PC was off and refused now: kept as a request at the
    time the client chose, for the business to accept, move or refuse (wpsync.py)."""
    # All inventory writers take the same stable tenant row lock.
    with transaction.atomic():
        tenant=Tenant.objects.select_for_update().get(pk=tenant.pk)
        if not operational(tenant):raise BookingError(gettext("Prenotazioni non disponibili."))
        if actor is None and not late and tenant.receive_schedule and not fits(tenant,timezone.now(),timezone.now()+timedelta(seconds=1),purpose="receive"):
            raise BookingError(gettext("Le richieste online sono accettate soltanto negli orari indicati dall'attività."))
        key=str(data.get("request_key",""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}",key):raise BookingError("Identificativo richiesta mancante.")
        fingerprint=index(json.dumps(data,sort_keys=True,separators=(",",":")),tenant_key(tenant),"booking-request")
        existing=Booking.objects.filter(request_key=key).first()
        if existing:
            if existing.request_fingerprint!=fingerprint:raise BookingError("Richiesta già usata con dati differenti.")
            return existing,False
        if data.get("extra") and not visit:return create_visit(tenant,data,actor,origin,source,wait,allowed)
        service=visit[1][0] if visit else choice(Service,data.get("service"),active=True,location__active=True)
        staff=actor is not None
        walkin=staff and data.get("walkin") is True
        pii=personal(data,visit[2] if visit else service,staff,walkin)
        from .waitlist import claim,booked
        if wait:claim(tenant,wait)
        when={**data,"start":timezone.now().replace(second=0,microsecond=0).isoformat()} if walkin else data
        if visit:(_,option,resource,start,end,bs,be,gap,second),party,event,issues=visit[1],1,None,[]
        else:
            start,end,bs,be,party,option,resource,event,issues=bounds(tenant,service,when,desk=staff,force=late or staff and (walkin or data.get("force") is True),prefer=last_operator(tenant,service,pii,data,staff))
            gap,second=kit(service,start,end,bs,be,resource,issues)
            if not late:overrun(issues,data)
        from .tables import seat,pick_area
        area,tables=seat(service,party,bs,be,pick_area(service,data.get("area")),data.get("tables") if staff else None,online=not staff)
        known_by_phone=None
        if staff:
            # 2.7.0: the client picked from the card, or found by email or phone, or a new card
            from .clients import find_or_create
            if data.get("contact"):
                contact=choice(Contact.objects.exclude(email_index__startswith="erased:"),data.get("contact"));new_contact=False
            elif walkin and not (pii["email"] or pii["phone"]):contact,new_contact=None,False  # passing by: no card
            else:contact,new_contact=find_or_create(tenant,pii,named=origin=="agenda")  # 2.21.0: written in the site's agenda, also by name
            if not pii.get("email"):data={**data,"marketing":False}
        else:
            idx=index(pii["email"],tenant_key(tenant),"email")
            contact=Contact.objects.filter(email_index=idx).first()
            # 2.8.0: not found by email, the same phone is the client added at the desk without email: one card,
            # one history. A card with another email stays another person. The card is never rewritten here
            from .clients import by_phone
            if contact is None:known_by_phone=by_phone(tenant,pii["phone"],without_email=True)
            # 2.21.0: from the WordPress site, also the card with the same first and last name, however written
            if contact is None and not known_by_phone and origin=="wordpress":
                from .clients import by_name
                known_by_phone=by_name(tenant,pii)
            contact=contact or known_by_phone
            # 2.14.0: Non prenotabile online, also with another email on the phone of a card the business marked
            from .clients import phone_index
            if (contact and contact.online_blocked) or Contact.objects.filter(phone_index=phone_index(tenant,pii["phone"]),online_blocked=True).exists():raise refused(tenant,service)
            from .clients import category_refusal
            why=category_refusal(tenant,contact,start,timezone.now())  # 2.14.0: Socio, Ospite: days ahead, bookings at once
            if why:raise BookingError(why)
            new_contact=contact is None
            if new_contact:
                from .clients import seal_card
                contact=Contact(tenant=tenant,email_index=idx)
                seal_card(tenant,contact,{k:pii[k] for k in ("first_name","last_name","email","phone")})
        # An unverified public request never rewrites an existing contact card,
        # its consent state or its proof: the declared data stay in the booking.
        # a card found by phone has no email: this request cannot confirm marketing for it
        consent_requested=(data.get("marketing") is True and contact.consent!="yes" and not contact.suppressed and not known_by_phone
            and not ActionToken.objects.filter(contact_id=contact.pk,kind="consent",used=False,expires__gt=timezone.now()).exists())
        if consent_requested and new_contact:contact.consent="pending"
        # 2.13.0: the box "send me the emails about this booking"; missing (older forms, the plugin) means yes
        notices=data.get("notices") is not False
        if new_contact:contact.notices=notices
        # 2.14.0: the channel chosen on the form for the reminders, with a phone; on a new card, and on this booking
        ch=data.get("channel") if data.get("channel") in ("email","sms","whatsapp") and data.get("phone") else ""
        if new_contact and ch:contact.channel=ch
        if contact:contact.save()
        status="in_progress" if walkin else "pending" if late or not staff and needs_request(service,party,pii,contact) else "confirmed"
        booking=Booking(tenant=tenant,service=service,location=service.location,resource=resource,option=option,occurrence=event,contact=contact,start=start,end=end,busy_start=bs,busy_end=be,party=party,request_key=key,request_fingerprint=fingerprint,
            status=status,origin=("walkin" if walkin else origin if origin in ("import","agenda") else "desk") if staff else origin,source="" if staff else source,series=series,visit=visit and visit[0],snapshot={"service":service.name,"location":service.location.name,"resource":resource.name if resource else "","option":option.name if option else "","field_labels":{x["key"]:x["label"] for x in (visit[2] if visit else service).fields},
            "policy":{"cancel_cutoff_minutes":service.cancel_cutoff_minutes,"min_days":service.min_days}})
        if not notices:booking.snapshot["notices"]=False
        if ch:booking.snapshot["channel"]=ch
        # 2.14.0: the language of the client's page, for the emails that follow (client_mails.send)
        if not staff and get_language()!="it":booking.snapshot["lang"]=get_language()
        if issues and not late:booking.snapshot["forced"]=issues
        (booking.gap_start,booking.gap_end)=gap;equip(booking,second)
        booking.area,booking.tables=area,tables
        # 2.14.0: "Il primo disponibile": the client may move it to whoever is free then (public.py)
        if resource and not data.get("resource"):booking.snapshot["any_resource"]=True
        from .prices import stamp
        stamp(booking)  # 2.11.0: price, client discount and what he pays, as at this moment
        booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk));booking.save()
        token=action_token(tenant,"cancel",contact,booking,days=max(30,service.horizon_days+service.max_days+30))
        pii["cancel_token"]=token;booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk));booking.save(update_fields=["pii"])
        from django.conf import settings
        from .client_mails import send
        # 2.14.0: the appointments imported from the old program were already agreed: no email, no notice to the staff
        # 2.19.0: one written in the site's agenda by the staff and not free now: no "richiesta ricevuta", the client gets the yes when the desk accepts it
        if not walkin and not series and not visit and booking.origin!="import" and not (booking.origin=="agenda" and status=="pending"):send(tenant,booking,"requested" if status=="pending" else "confirmed")
        if consent_requested:
            token=action_token(tenant,"consent",contact=contact,booking=booking,days=7)
            # 2.14.0: the words the client read, in the language of his page
            shown=tenant.consent_text_en if get_language()=="en" and tenant.consent_text_en else tenant.consent_text
            booking.snapshot["consent_request"]={"text":shown,"requested":timezone.now().isoformat(),"source":"booking"}
            booking.save(update_fields=["snapshot"])
            from .privacy import record
            record(tenant,contact,"requested","booking",actor,shown,booking.pk)
            queue(tenant,{"to":pii["email"],"subject":gettext("Conferma iscrizione")+" · "+tenant.name,"body":gettext("Conferma la richiesta di iscrizione alle comunicazioni di {attivita}:").format(attivita=tenant.name)+f"\n{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/\n"+gettext("Se non hai richiesto l'iscrizione, ignora il messaggio.")},contact)
        from .notify import notify_staff
        if not series and not visit and booking.origin!="import":notify_staff(tenant,booking,"requested" if status=="pending" else "created")
        booked(tenant,booking,wait)
        from .api import emit
        emit(tenant,booking,"created")
        audit(tenant,actor,"booking.created",booking.pk,origin=booking.origin,**({"forced":issues} if issues else {}))
        return booking,True
def move_booking(tenant,booking_id,data,actor,desk=True,quiet=False):
    """desk=False (2.14.0): the client from his link, with the rules of the public page and no Forza, and the limits of
    his category (this booking does not count twice). quiet: the other dates of a series moved together, without an
    email and a notice each."""
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        booking=choice(Booking,booking_id,status__in=MOVABLE)
        if booking.visit and data.get("visit") is True:return move_visit(tenant,booking,data,actor,desk)
        service=choice(Service,data.get("service",booking.service_id),active=True,location__active=True)
        # 2.14.0: moving is the desk's, with the desk's rules and Forza
        *values,issues=bounds(tenant,service,data,exclude=booking.pk,desk=desk,force=desk and data.get("force") is True)
        gap,second=kit(service,*values[:4],values[6],issues,booking.pk)
        overrun(issues,data)
        if not desk:
            from .clients import category_refusal
            if why:=category_refusal(tenant,booking.contact,values[0],timezone.now(),booking.pk):raise BookingError(why)
        key=lambda b:(b.start,b.party,b.option_id,b.resource_id,b.occurrence_id,b.service_id)
        before=key(booking);left=(booking.start,booking.end)
        booking.start,booking.end,booking.busy_start,booking.busy_end,booking.party,booking.option,booking.resource,booking.occurrence=values
        booking.service=service
        (booking.gap_start,booking.gap_end)=gap;equip(booking,second)
        # 2.14.0: only the end changed (stretched in the agenda): for the client nothing moved, no email, the reminders
        # and his Confermo stay
        moved=key(booking)!=before
        booking.snapshot.pop("forced",None)
        if issues:booking.snapshot["forced"]=issues
        booking.location=service.location
        booking.snapshot.update(service=service.name,location=service.location.name,resource=booking.resource.name if booking.resource else "",option=booking.option.name if booking.option else "")
        from .prices import stamp
        stamp(booking)
        if moved:
            for name in REARM:booking.snapshot.pop(name,None)  # 2.13.0: the new day gets its own reminder
            # 2.14.0: its tables stay if still free at the new time, otherwise the best fit or none (tables.py)
            from .tables import taken,seat
            if booking.service.kind not in ("capacity","shift"):booking.tables=[]
            elif set(booking.tables)&taken(booking.location,booking.busy_start,booking.busy_end,booking.pk) or not booking.tables:booking.area,booking.tables=seat(service,booking.party,booking.busy_start,booking.busy_end,booking.area,exclude=booking.pk)
        booking.save()
        ActionToken.objects.filter(booking=booking,kind="cancel",used=False).update(expires=booking.end+timedelta(days=30))
        if not moved:
            audit(tenant,actor,"booking.resized",booking.pk,minutes=int((booking.end-booking.start).total_seconds()//60),**({"forced":issues} if issues else {}))
            return booking
        # Confermo / Non vengo of the old day must not answer for the new one
        ActionToken.objects.filter(booking=booking,kind__in=("confirm","decline"),used=False).update(expires=timezone.now())
        from .waitlist import freed
        freed(tenant,*left)  # 2.14.0: the place it left, to the waiting list
        from .client_mails import send
        from .notify import notify_staff
        if not quiet:send(tenant,booking,"moved");notify_staff(tenant,booking,"moved")
        from .api import emit
        emit(tenant,booking,"moved")
        audit(tenant,actor,"booking.moved",booking.pk,**({"forced":issues} if issues else {}))
        return booking
def move_visit(tenant,booking,data,actor,desk):
    """2.14.0: a visit moves whole (data["visit"]): its services in the same order from the new time, as visit_slots
    places them; dragged in the agenda one service moves alone. One email and one notice, for the visit."""
    rows=[b for b in visit_rows(booking) if b.status in MOVABLE]
    plan=visit_plan(tenant,rows[0].service,rows[0].option,[str(b.service_id) for b in rows[1:]],data,[b.pk for b in rows],desk)
    if not desk:
        from .clients import category_refusal
        if why:=category_refusal(tenant,booking.contact,plan[0][3],timezone.now(),[b.pk for b in rows]):raise BookingError(why)
    from .prices import stamp
    from .waitlist import freed
    from .api import emit
    for b,(_,_,r,start,end,bs,be,gap,second) in zip(rows,plan):
        left=(b.start,b.end)
        b.start,b.end,b.busy_start,b.busy_end,b.resource,(b.gap_start,b.gap_end)=start,end,bs,be,r,gap;equip(b,second)
        b.snapshot.update(resource=r.name);b.snapshot.pop("forced",None)
        for name in REARM:b.snapshot.pop(name,None)
        stamp(b);b.save()
        ActionToken.objects.filter(booking=b,kind="cancel",used=False).update(expires=b.end+timedelta(days=30))
        ActionToken.objects.filter(booking=b,kind__in=("confirm","decline"),used=False).update(expires=timezone.now())
        freed(tenant,*left);emit(tenant,b,"moved");audit(tenant,actor,"booking.moved",b.pk,visit=str(b.visit))
    from .client_mails import send
    from .notify import notify_staff
    send(tenant,rows[0],"moved");notify_staff(tenant,rows[0],"moved")
    return Booking.objects.get(pk=booking.pk)
def can_undo(booking,now=None):
    """2.14.0: the last change of status can be taken back for a few minutes, where it was made."""
    last=booking.snapshot.get("undo")
    return bool(last) and last["to"]==booking.status and (now or timezone.now())<=datetime.fromisoformat(last["at"])+timedelta(minutes=UNDO_MINUTES)
def change_status(tenant,booking,status,actor=None,reason="",note="",undo=False,quiet=False):
    """2.14.0: a request accepted or refused, arrived, in progress, done, absent, cancelled with its reason (the free
    note sealed with the booking's data), back to confirmed; undo takes back the last change. quiet: the other dates of
    a series cancelled together, without an email and a notice each."""
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        booking=Booking.objects.select_for_update().get(pk=booking.pk)
        old=booking.status;now=timezone.now();tz=ZoneInfo(tenant.timezone)
        if undo:
            if not can_undo(booking,now):raise BookingError(f"Si torna indietro solo entro {UNDO_MINUTES} minuti: cambia lo stato da qui sotto.")
            status=booking.snapshot["undo"]["from"]
        elif booking.status==status:return booking
        elif status not in MOVES.get(old,()):raise BookingError("Cambio di stato non consentito.")
        elif status in ("completed","no_show") and booking.start>now and old not in ("arrived","in_progress"):raise BookingError("L'appuntamento non è ancora iniziato.")
        elif status in ("arrived","in_progress") and booking.start.astimezone(tz).date()>now.astimezone(tz).date():raise BookingError("La prenotazione non è di oggi.")
        # a place given up may have been taken meanwhile
        # an event's substitute instructor sits on its bookings for the agenda: the places are the date's
        if old=="cancelled" and (not capacity_available(booking.service,booking.busy_start,booking.busy_end,booking.party,booking.resource if booking.service.kind=="resource" else None,booking.occurrence,exclude=booking.pk,gap=booking.gap_start and (booking.gap_start,booking.gap_end))
            or any(not capacity_available(booking.service,booking.busy_start,booking.busy_end,1,x,exclude=booking.pk) for x in Resource.objects.filter(Q(pk=booking.secondary_id)|Q(pk__in=booking.more)))):
            raise BookingError("Il posto nel frattempo è stato preso: sposta la prenotazione su un altro orario.")
        booking.status=status
        if undo:booking.snapshot.pop("undo")
        else:booking.snapshot["undo"]={"from":old,"to":status,"at":now.isoformat()}
        if status=="cancelled" and not undo:
            booking.snapshot["cancel_reason"]=reason if reason in REASON_LABELS else "other"
            note=" ".join(str(note or "").split())[:300]
            pii=booking.data;pii.pop("cancel_note",None)
            if note:pii["cancel_note"]=note
            booking.pii=encrypt(tenant,pii,"booking:"+str(booking.pk))
        # back from cancelled: the client's link to cancel works again
        if old=="cancelled":ActionToken.objects.filter(booking=booking,kind="cancel").update(used=False)
        booking.save(update_fields=["status","snapshot","pii"])
        if booking.visit and not quiet and (status in ("cancelled","confirmed") or undo):
            # 2.14.0: a visit is accepted, cancelled, put back or undone whole; the email and the notice below are the visit's
            for other in Booking.objects.filter(visit=booking.visit,status=old).exclude(pk=booking.pk):
                try:change_status(tenant,other,status,actor,reason,note,undo,quiet=True)
                except BookingError:pass
        if old in HOLDING and status not in HOLDING:
            from .waitlist import freed
            freed(tenant,booking.start,booking.end)  # 2.14.0: the place given up, to the waiting list
        if not undo and not quiet:
            from .client_mails import send
            mail={"cancelled":"rejected" if reason=="rejected" else "cancelled","confirmed":"accepted" if old=="pending" else ""}.get(status)
            if mail:send(tenant,booking,mail)
            if status=="cancelled":
                from .notify import notify_staff
                notify_staff(tenant,booking,"cancelled")
        if status=="cancelled":
            from .api import emit
            emit(tenant,booking,"cancelled")  # also the dates of a series cancelled together, and an undo: each one was "created"
        audit(tenant,actor,"booking.undo" if undo else "booking."+status,booking.pk,**{"from":old},**({"reason":booking.snapshot["cancel_reason"]} if status=="cancelled" else {}))
        return booking
def series_base(booking):
    """What the other dates of a series copy from the booking: the service, with whom, the client and his answers."""
    d=booking.data
    return {"service":str(booking.service_id),"resource":str(booking.resource_id or ""),"option":str(booking.option_id or ""),"party":booking.party,
        "contact":str(booking.contact_id),**{k:d.get(k,"") for k in ("first_name","last_name","email","phone","notes")},"answers":d.get("answers",{}),"allergies":d.get("allergies",[])}
def series_plan(tenant,booking,every,until):
    """2.14.0: Ripeti from the desk: the same time every N weeks until a day (a year at most), each date checked as the
    desk would book it: [(start, None or why not)]. Nothing is saved."""
    if booking.service.kind not in ("resource","capacity") or not booking.contact_id:raise BookingError("Si ripetono gli appuntamenti e le fasce orarie di un cliente con la scheda.")
    tz=ZoneInfo(tenant.timezone);first=booking.start.astimezone(tz)
    if every not in (1,2,3,4) or not until or not first.date()<until<=first.date()+timedelta(days=366):raise BookingError("Scegli ogni quante settimane e fino a quando, al massimo un anno.")
    out=[];d=first.date()+timedelta(weeks=every);base=series_base(booking)
    while d<=until:
        start=local_datetime(d,first.time(),tz);why=None
        if start is None:why="ora che non esiste per il cambio dell'ora"
        else:
            try:bounds(tenant,booking.service,{**base,"start":start.isoformat()},desk=True)
            except BookingError as ex:why=str(ex).split(".")[0].lower()
        out.append((start or datetime.combine(d,first.time(),tz),why));d+=timedelta(weeks=every)
    return out
def series_create(tenant,booking,every,until,actor):
    """The free dates of the plan, tied to the booking (series); the ones taken meanwhile are skipped. How many made."""
    import uuid
    sid=booking.series or uuid.uuid4();made=0
    if not booking.series:booking.series=sid;booking.save(update_fields=["series"])  # 2.18.0: saved, not updated: the site's archive gets it
    for start,why in series_plan(tenant,booking,every,until):
        if why:continue
        try:create_booking(tenant,{**series_base(booking),"start":start.isoformat(),"request_key":secrets.token_urlsafe(24)},actor,series=sid);made+=1
        except BookingError:pass
    audit(tenant,actor,"booking.series",booking.pk,made=made,every=every)
    return made
def series_rows(booking,following):
    """2.14.0: this date alone, or this and the next ones of its series still to come, in order."""
    if not following or not booking.series:return [booking]
    return list(Booking.objects.filter(series=booking.series,status__in=MOVABLE,start__gte=booking.start).select_related("service").order_by("start"))
def series_move_plan(tenant,booking,start,resource,following):
    """2.14.0: Sposta from the desk for this date or this and the next ones: each date moved by the same days to the new
    time of day (the same clock time across the change of hour) and operator, checked as the desk books it:
    [(booking, new start, None or why not)]. Nothing is saved."""
    tz=ZoneInfo(tenant.timezone);rows=series_rows(booking,following);new=start.astimezone(tz);shift=new.date()-booking.start.astimezone(tz).date()
    out=[]
    for b in rows:
        at=local_datetime(b.start.astimezone(tz).date()+shift,new.time(),tz);why=None
        if at is None:why="ora che non esiste per il cambio dell'ora"
        else:
            try:bounds(tenant,b.service,{"option":str(b.option_id or ""),"resource":str(resource.pk if resource else b.resource_id or ""),"party":b.party,"start":at.isoformat()},exclude=b.pk,desk=True)
            except BookingError as ex:why=str(ex).split(".")[0].lower()
        out.append((b,at or datetime.combine(b.start.astimezone(tz).date()+shift,new.time(),tz),why))
    return out
def series_move(tenant,booking,start,resource,following,actor):
    """The free dates of the plan moved, one email and one notice for the first (as cancelling a series); the ones
    taken meanwhile are skipped. How many moved."""
    made=0
    for i,(b,at,why) in enumerate(series_move_plan(tenant,booking,start,resource,following)):
        if why:continue
        try:move_booking(tenant,b.pk,{"service":str(b.service_id),"option":str(b.option_id or ""),"resource":str(resource.pk if resource else b.resource_id or ""),"party":b.party,"start":at.isoformat()},actor,quiet=made>0);made+=1
        except BookingError:pass
    audit(tenant,actor,"booking.series_moved",booking.pk,moved=made)
    return made
