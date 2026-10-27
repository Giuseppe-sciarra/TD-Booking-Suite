"""Civil-time opening rules, UTC inventory intervals, half-open boundaries."""
from datetime import datetime,timedelta,time,timezone as UTCZone
from zoneinfo import ZoneInfo
from django.db.models import Q
from .models import Schedule,ExceptionDay
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
    spans=[]
    # Include yesterday for opening windows crossing midnight.
    for d in (day-timedelta(days=1),day,day+timedelta(days=1)):
        overrides=list(exceptions.filter(date=d))
        if overrides:
            windows=[] if any(x.closed for x in overrides) else [(x.start,x.end) for x in overrides]
        else:
            windows=[(x.start,x.end) for x in rules.filter(weekday=d.weekday()).filter(Q(valid_from__isnull=True)|Q(valid_from__lte=d)).filter(Q(valid_until__isnull=True)|Q(valid_until__gte=d))]
            if inherit:
                a=local_datetime(d,time.min,tz)
                b=local_datetime(d+timedelta(days=1),time.min,tz)
                spans.append((a.astimezone(UTC),b.astimezone(UTC)))
        for begin,end in windows:
            if begin is None or end is None or begin==end:continue
            a=local_datetime(d,begin,tz)
            b=local_datetime(d+timedelta(days=1) if end<begin else d,end,tz,fold=1)
            if a and b:spans.append((a.astimezone(UTC),b.astimezone(UTC)))
    spans=merge(spans)
    # A closed date also clips a window inherited from the previous night.
    for x in exceptions.filter(date__range=(day-timedelta(days=1),day+timedelta(days=1)),closed=True):
        a=local_datetime(x.date,time.min,tz).astimezone(UTC)
        b=local_datetime(x.date+timedelta(days=1),time.min,tz).astimezone(UTC)
        clipped=[]
        for start,end in spans:
            if end<=a or start>=b:clipped.append((start,end))
            else:
                if start<a:clipped.append((start,a))
                if end>b:clipped.append((b,end))
        spans=clipped
    return spans
def windows(tenant,day,service=None,resource=None,purpose="service"):
    current=None
    for i,selector in enumerate(level_filters(service,resource)):
        selected=purpose
        exists=Schedule.objects.filter(purpose=selected,**selector).exists() or ExceptionDay.objects.filter(purpose=selected,**selector).exists()
        # Resolve each relevant level, never let another service change this fallback.
        if not exists and purpose in ("arrival","departure"):
            selected="service"
            exists=Schedule.objects.filter(purpose=selected,**selector).exists() or ExceptionDay.objects.filter(purpose=selected,**selector).exists()
        if not exists and i>0:continue
        weekly=Schedule.objects.filter(purpose=selected,**selector).exists()
        spans=level_windows(tenant,day,selected,selector,inherit=i>0 and not weekly)
        current=spans if current is None else intersection(current,spans)
    return current or []
def fits(tenant,start,end,service=None,resource=None,purpose="service"):
    day=start.astimezone(ZoneInfo(tenant.timezone)).date()
    return any(a<=start and end<=b for a,b in windows(tenant,day,service,resource,purpose))
def blocked_stay(tenant,start,end,service,resource=None):
    tz=ZoneInfo(tenant.timezone)
    first=start.astimezone(tz).date();last=(end-timedelta(microseconds=1)).astimezone(tz).date()
    for selector in level_filters(service,resource):
        if ExceptionDay.objects.filter(purpose="service",closed=True,date__range=(first,last),**selector).exists():return True
    return False
