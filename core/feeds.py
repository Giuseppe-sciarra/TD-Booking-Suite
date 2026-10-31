"""2.14.0: the calendars of an operator. Out: a private .ics feed of his bookings, service and time, the client's name
only when the business chooses it, at a signed link that stops working when it is renewed. In: an outside calendar
(Google, Outlook, iCloud: its secret .ics address) read by the worker every 15 minutes into Occupato blocks, without
titles or people: the engine and the agenda already keep them free."""
import re
from datetime import datetime,timedelta,time,timezone as TZ
from urllib.parse import urlsplit
from urllib.request import Request,HTTPRedirectHandler,build_opener
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

SALT="td-feed"
EVERY=timedelta(minutes=15)
AHEAD=timedelta(days=180)  # the outside events read, from now on
WEEKDAYS=["MO","TU","WE","TH","FR","SA","SU"]

def link(resource,slug):
    return f"{settings.PUBLIC_ORIGIN}/action/{slug}/feed/{signing.dumps([str(resource.pk),resource.feed],salt=SALT)}/calendario.ics"

def opened(token):
    """The resource id of a valid feed link and its version, or None."""
    try:pk,version=signing.loads(token,salt=SALT);return pk,int(version)
    except (signing.BadSignature,ValueError,TypeError):return None

def feed(tenant,resource,now=None):
    """The bookings of the last 30 days and of the next year, the same UID as the client's file (ics.ics)."""
    from .ics import escape,fold,utc,place
    from .booking import ACCEPTED
    from .models import Booking
    now=now or timezone.now();host=settings.PUBLIC_ORIGIN.split("://",1)[-1].split("/")[0].split(":")[0]
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Tastiere Digitali//TD Booking Suite//IT","X-WR-CALNAME:"+escape(f"{resource.name} · {tenant.name}")]
    for b in Booking.objects.filter(resource=resource,status__in=ACCEPTED,end__gte=now-timedelta(days=30),start__lte=now+timedelta(days=365)).select_related("service","location").order_by("start"):
        what=b.snapshot.get("service") or b.service.name
        if resource.feed_names:
            d=b.data;what+=" · "+" ".join(x for x in (d.get("first_name",""),d.get("last_name","")) if x)
        lines+=["BEGIN:VEVENT",f"UID:{b.pk}@{host}","DTSTAMP:"+utc(now),"DTSTART:"+utc(b.start),"DTEND:"+utc(b.end),"SUMMARY:"+escape(what.strip(" ·")),"LOCATION:"+escape(place(b)),"END:VEVENT"]
    return "\r\n".join(fold(x) for x in lines+["END:VCALENDAR"])+"\r\n"

def moment(value,params,tz):
    """A DTSTART / DTEND: a day (all day, at midnight of the business), UTC, with its TZID, or floating."""
    if "VALUE=DATE" in params.upper() and "DATE-TIME" not in params.upper() or len(value)==8:
        return datetime.combine(datetime.strptime(value[:8],"%Y%m%d").date(),time.min,tz)
    if value.endswith("Z"):return datetime.strptime(value,"%Y%m%dT%H%M%SZ").replace(tzinfo=TZ.utc)
    zone=tz
    if m:=re.search(r'TZID="?([^;:"]+)',params):
        try:zone=ZoneInfo(m.group(1))
        except (ValueError,KeyError,OSError):pass  # Outlook's Windows names: the business's time
    return datetime.strptime(value[:15],"%Y%m%dT%H%M%S").replace(tzinfo=zone)

def duration(value):
    m=re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",value)
    if not m:raise ValueError("durata")
    w,d,h,mi,s=(int(x or 0) for x in m.groups())
    return timedelta(weeks=w,days=d,hours=h,minutes=mi,seconds=s)

def repeat(start,rule,last):
    """The starts of a repeated event until last.
    shortcut: DAILY and WEEKLY (with BYDAY) only, EXDATE ignored (a cancelled date stays busy); the others once."""
    r=dict(x.split("=",1) for x in rule.upper().split(";") if "=" in x)
    if r.get("FREQ") not in ("DAILY","WEEKLY"):return [start]
    step=max(int(r.get("INTERVAL") or 1),1);count=int(r.get("COUNT") or 0)
    if r.get("UNTIL"):last=min(last,moment(r["UNTIL"],"",start.tzinfo)+timedelta(days=1 if len(r["UNTIL"])==8 else 0))
    days=sorted({WEEKDAYS.index(x[-2:]) for x in r.get("BYDAY","").split(",") if x[-2:] in WEEKDAYS}) or [start.weekday()]
    monday=start.date()-timedelta(days=start.weekday());out=[]
    for i in range(3000):
        for d in ([start.date()+timedelta(days=i*step)] if r["FREQ"]=="DAILY" else [monday+timedelta(weeks=i*step,days=x) for x in days]):
            when=datetime.combine(d,start.time(),start.tzinfo)
            if when<start:continue
            if when>last or count and len(out)>=count:return out
            out.append(when)
    return out

def events(text,tz,now):
    """(start, end) of the busy events from now on; the free (TRANSP:TRANSPARENT) and cancelled ones are left out."""
    out=[];ev=None;inner=0;last=now+AHEAD
    for line in re.sub(r"\r?\n[ \t]","",text).splitlines():
        name,_,value=line.partition(":");key,_,params=name.partition(";");key=key.upper();value=value.strip()
        if key=="BEGIN":
            if value.upper()=="VEVENT":ev={}
            elif ev is not None:inner+=1  # VALARM inside: its DURATION is not the event's
        elif key=="END" and ev is not None:
            if inner:inner-=1;continue
            try:
                if "DTSTART" in ev and ev.get("TRANSP",("",""))[1].upper()!="TRANSPARENT" and ev.get("STATUS",("",""))[1].upper()!="CANCELLED":
                    start=moment(ev["DTSTART"][1],ev["DTSTART"][0],tz)
                    if "DTEND" in ev:length=moment(ev["DTEND"][1],ev["DTEND"][0],tz)-start
                    elif "DURATION" in ev:length=duration(ev["DURATION"][1])
                    else:length=timedelta(days=1) if len(ev["DTSTART"][1])==8 else timedelta(0)
                    for s in repeat(start,ev["RRULE"][1],last) if "RRULE" in ev else [start]:
                        if s+length>now and s<last and length>timedelta(0):out.append((s,s+length))
            except (ValueError,KeyError):pass  # one event we cannot read does not stop the others
            ev=None
        elif ev is not None and not inner:ev[key]=(params,value)
    return out

def blocks(spans,tz):
    """The busy spans as blocks of the operator's days (TimeBlock: an end not after the start goes to the next day)."""
    out=[]
    for s,e in spans:
        s,e=s.astimezone(tz),e.astimezone(tz);day=s.date()
        while datetime.combine(day,time.min,tz)<e:
            a=max(s,datetime.combine(day,time.min,tz));z=min(e,datetime.combine(day+timedelta(days=1),time.min,tz))
            if a<z:out.append((day,a.time(),z.time() if z.date()==day else time.min))
            day+=timedelta(days=1)
    return out

def check(url):
    """Only https (webcal is https), only toward public addresses, as the SMTP.
    shortcut: the name is resolved again by urlopen; a DNS that changes between the two is not caught."""
    from .mail import public_addresses
    parts=urlsplit(url)
    if parts.scheme!="https" or not parts.hostname:raise ValueError("L'indirizzo deve iniziare con https:// o webcal://")
    public_addresses(parts.hostname,parts.port or 443)
    return url

class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return super().redirect_request(req,fp,code,msg,headers,check(newurl))

def fetch(url):
    url=check(re.sub(r"^webcal://","https://",url.strip(),flags=re.I))
    with build_opener(Redirects).open(Request(url,headers={"User-Agent":"TD Booking Suite"}),timeout=20) as r:data=r.read(5_000_001)
    if len(data)>5_000_000:raise ValueError("Il calendario è troppo grande (oltre 5 MB).")
    return data.decode("utf-8","replace")

def busy_url(resource):
    from .crypto import decrypt
    return decrypt(resource.tenant,resource.busy,"busy:"+str(resource.pk)) if resource.busy else ""

def read(tenant,resource,now):
    """One outside calendar into the operator's Occupato blocks, replaced each time; an error is kept for the page."""
    from .models import TimeBlock
    tz=ZoneInfo(tenant.timezone)
    try:rows=blocks(events(fetch(busy_url(resource)),tz,now),tz)[:3000]
    except (ValueError,OSError) as ex:
        resource.busy_error=(str(getattr(ex,"reason",None) or ex) or "Calendario non raggiungibile.")[:200];resource.busy_at=now
        resource.save(update_fields=["busy_error","busy_at"]);return False
    with transaction.atomic():
        TimeBlock.objects.filter(resource=resource,reason="busy").delete()
        TimeBlock.objects.bulk_create([TimeBlock(tenant=tenant,resource=resource,day=d,start=a,end=z,reason="busy") for d,a,z in rows])
        resource.busy_error="";resource.busy_at=now;resource.save(update_fields=["busy_error","busy_at"])
    return True

def refresh(now=None):
    """Every outside calendar not read in the last 15 minutes (the worker calls it)."""
    from .models import Tenant,Resource
    from .middleware import operational
    from .tenancy import scope
    now=now or timezone.now();done=0
    for tenant in Tenant.objects.iterator():
        if not operational(tenant):continue
        with scope(tenant):
            for r in Resource.objects.exclude(busy="").filter(Q(busy_at__isnull=True)|Q(busy_at__lte=now-EVERY)).select_related("tenant"):done+=read(tenant,r,now)
    return done
