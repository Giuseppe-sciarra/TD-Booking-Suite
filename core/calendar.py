"""Civil-time opening rules, UTC inventory intervals, half-open boundaries."""
from datetime import date,datetime,timedelta,time,timezone as UTCZone
from zoneinfo import ZoneInfo
from django.db.models import Q
from .models import Schedule,ExceptionDay,TimeBlock
UTC=UTCZone.utc
def local_datetime(day,clock,tz,fold=0):
    naive=datetime.combine(day,clock)
    aware=naive.replace(tzinfo=tz,fold=fold)
    if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None)!=naive:return None
    return aware
def merge(spans):
    result=[]
    for a,b in sorted(spans):
        if result and a<=result[-1][1]:result[-1]=(result[-1][0],max(b,result[-1][1]))
        elif a<b:result.append((a,b))
    return result
def intersection(left,right):
    return merge([(max(a,c),min(b,d)) for a,b in left for c,d in right if max(a,c)<min(b,d)])
def minus(spans,cuts):
    for c,d in cuts:spans=[p for a,b in spans for p in ((a,min(b,c)),(max(a,d),b)) if p[0]<p[1]]
    return spans
def covering(first,last=None):
    """2.14.0: the exceptions on a day (or on any day from first to last): a single date, or a closure from date to until."""
    return Q(date__lte=last or first)&(Q(until__gte=first)|Q(until__isnull=True,date__gte=first))
def in_turn(rule,d):
    """2.14.0: a weekly rule every N weeks (week A, week B) counts from the week of its valid_from."""
    return rule.every_weeks<2 or not rule.valid_from or ((d-rule.valid_from).days+rule.valid_from.weekday())//7%rule.every_weeks==0
def level_filters(service=None,resource=None):
    result=[{"location_id":None,"service_id":None,"resource_id":None}]
    if service:
        result+=[{"location_id":service.location_id,"service_id":None,"resource_id":None},{"location_id":None,"service_id":service.pk,"resource_id":None}]
    if resource:result+=[{"location_id":None,"service_id":None,"resource_id":resource.pk}]
    return result
def level_windows(tenant,day,purpose,selector,inherit=False):
    tz=ZoneInfo(tenant.timezone)
    rules=Schedule.objects.filter(purpose=purpose,**selector)
    exceptions=ExceptionDay.objects.filter(purpose=purpose,**selector)
    spans=[];shut=[]
    # Include yesterday for opening windows crossing midnight.
    for d in (day-timedelta(days=1),day,day+timedelta(days=1)):
        overrides=list(exceptions.filter(covering(d)))
        if any(x.closed for x in overrides):
            # A closed date also clips a window inherited from the previous night.
            shut.append((local_datetime(d,time.min,tz).astimezone(UTC),local_datetime(d+timedelta(days=1),time.min,tz).astimezone(UTC)))
        if overrides:
            windows=[] if any(x.closed for x in overrides) else [(x.start,x.end) for x in overrides]
        else:
            windows=[(x.start,x.end) for x in rules.filter(weekday=d.weekday()).filter(Q(valid_from__isnull=True)|Q(valid_from__lte=d)).filter(Q(valid_until__isnull=True)|Q(valid_until__gte=d)) if in_turn(x,d)]
            if inherit:
                a=local_datetime(d,time.min,tz)
                b=local_datetime(d+timedelta(days=1),time.min,tz)
                spans.append((a.astimezone(UTC),b.astimezone(UTC)))
        for begin,end in windows:
            if begin is None or end is None or begin==end:continue
            a=local_datetime(d,begin,tz)
            b=local_datetime(d+timedelta(days=1) if end<begin else d,end,tz,fold=1)
            if a and b:spans.append((a.astimezone(UTC),b.astimezone(UTC)))
    return minus(merge(spans),shut)
def windows(tenant,day,service=None,resource=None,purpose="service",memo=None):
    """memo (2.12.0): a dict kept for one page, so the levels shared by many operators and services (the business,
    the location) are read once a day instead of once each: the week of an agenda stays fast."""
    current=None
    for i,selector in enumerate(level_filters(service,resource)):
        key=(day,purpose,tuple(sorted(selector.items())))
        if memo is not None and key in memo:spans=memo[key]
        else:
            spans=level(tenant,day,purpose,selector,i)
            if memo is not None:memo[key]=spans
        if spans is None:continue
        current=spans if current is None else intersection(current,spans)
    return current or []
def level(tenant,day,purpose,selector,i):
    """The windows of one level, None when that level says nothing (only the first level always counts)."""
    selected=purpose
    exists=Schedule.objects.filter(purpose=selected,**selector).exists() or ExceptionDay.objects.filter(purpose=selected,**selector).exists()
    # Resolve each relevant level, never let another service change this fallback.
    if not exists and purpose in ("arrival","departure"):
        selected="service"
        exists=Schedule.objects.filter(purpose=selected,**selector).exists() or ExceptionDay.objects.filter(purpose=selected,**selector).exists()
    if not exists and i>0:return None
    weekly=Schedule.objects.filter(purpose=selected,**selector).exists()
    return level_windows(tenant,day,selected,selector,inherit=i>0 and not weekly)
def fits(tenant,start,end,service=None,resource=None,purpose="service"):
    day=start.astimezone(ZoneInfo(tenant.timezone)).date()
    return any(a<=start and end<=b for a,b in windows(tenant,day,service,resource,purpose))
def blocked_stay(tenant,start,end,service,resource=None):
    tz=ZoneInfo(tenant.timezone)
    first=start.astimezone(tz).date();last=(end-timedelta(microseconds=1)).astimezone(tz).date()
    for selector in level_filters(service,resource):
        if ExceptionDay.objects.filter(covering(first,last),purpose="service",closed=True,**selector).exists():return True
    return False
def block_spans(tenant,first,last,resource=None,service=None):
    """2.14.0: the time blocks of an operator or a service (a pause, a turn closed for the night) on the days from first
    to last, as (start, end, block) in UTC."""
    if not resource and not service:return []
    tz=ZoneInfo(tenant.timezone);out=[]
    rows=TimeBlock.objects.filter((Q(resource=resource) if resource else Q())|(Q(service=service) if service else Q()),day__lte=last)
    for b in rows.filter(Q(weekly=False,day__gte=first)|Q(weekly=True,until__isnull=True)|Q(weekly=True,until__gte=first)):
        d=first
        while d<=last:
            if b.on(d):
                a=local_datetime(d,b.start,tz);z=local_datetime(d+timedelta(days=1) if b.end<=b.start else d,b.end,tz,fold=1)
                if a and z:out.append((a.astimezone(UTC),z.astimezone(UTC),b))
            d+=timedelta(days=1)
    return out
def blocked(tenant,start,end,resource=None,service=None):
    """The first time block over start-end, or None."""
    tz=ZoneInfo(tenant.timezone)
    return next((b for a,z,b in block_spans(tenant,start.astimezone(tz).date()-timedelta(days=1),end.astimezone(tz).date(),resource,service) if a<end and start<z),None)
# 2.14.0: the Italian public holidays; 4 October is a national holiday again from 2026 (San Francesco d'Assisi)
HOLIDAYS=[(1,1,"Capodanno"),(1,6,"Epifania"),(4,25,"Festa della Liberazione"),(5,1,"Festa del Lavoro"),(6,2,"Festa della Repubblica"),(8,15,"Ferragosto"),
    (10,4,"San Francesco d'Assisi"),(11,1,"Ognissanti"),(12,8,"Immacolata"),(12,25,"Natale"),(12,26,"Santo Stefano")]
def easter(year):
    """Easter Sunday in the Gregorian calendar (the anonymous algorithm, Meeus/Jones/Butcher)."""
    a=year%19;b,c=divmod(year,100);d,e=divmod(b,4);f=(b+8)//25;g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30;i,k=divmod(c,4);l=(32+2*e+2*i-h-k)%7;m=(a+11*h+22*l)//451
    month,day=divmod(h+l-7*m+114,31)
    return date(year,month,day+1)
def holidays(year,patron_day="",patron_name=""):
    """The public holidays of a year with Easter and Easter Monday, and the patron saint of the town ("06-29")."""
    e=easter(year)
    out=[(date(year,m,d),name) for m,d,name in HOLIDAYS if (m,d)!=(10,4) or year>=2026]+[(e,"Pasqua"),(e+timedelta(days=1),"Lunedì dell'Angelo")]
    try:out.append((date(year,int(patron_day[:2]),int(patron_day[3:])),patron_name or "Santo patrono"))
    except ValueError:pass  # none, or 29 February in a year without it
    return sorted(out)
