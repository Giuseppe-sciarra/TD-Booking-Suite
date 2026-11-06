"""Notifications for the business (email and Telegram), minimised:
service, time, place, unit and an abbreviated name. Never email, phone, notes or answers
(finding S14): the full booking stays one click away in the portal."""
import json,re,urllib.error,urllib.parse,urllib.request
from zoneinfo import ZoneInfo
from django.conf import settings
from .crypto import decrypt
from .booking import queue

TOKEN=re.compile(r"\d{5,12}:[A-Za-z0-9_-]{30,60}")
CHAT=re.compile(r"-?\d{1,20}|@[A-Za-z0-9_]{5,32}")
TITLES={"created":"Nuova prenotazione","requested":"Richiesta da confermare","moved":"Prenotazione spostata","cancelled":"Prenotazione annullata",
    "declined":"Il cliente dice che non viene"}

def short(d):
    """Mario R.: the name the notices show."""
    return (d.get("first_name") or "").strip()+(" "+d["last_name"].strip()[:1].upper()+"." if d.get("last_name") else "")

def summary(tenant,booking,event):
    tz=ZoneInfo(tenant.timezone);d=booking.data;s=booking.snapshot
    name=short(d)
    from .booking import visit_rows
    rows=visit_rows(booking)  # 2.14.0: a visit of several services in one notice, each with its time and operator
    lines=[TITLES[event]+" · "+tenant.name,"Servizio: "+(s.get("service") or "") if len(rows)<2 else "Servizi: "+" + ".join(f"{r.snapshot.get('service','')} {r.start.astimezone(tz):%H:%M} ({r.snapshot.get('resource','')})" for r in rows)]
    if s.get("option"):lines.append("Prestazione: "+s["option"])
    if s.get("location"):lines.append("Sede: "+s["location"])
    if s.get("resource") and len(rows)<2:lines.append((booking.service.resource_label or "Risorsa")+": "+s["resource"])
    if booking.service.kind=="day":
        nights=(booking.end.astimezone(tz).date()-booking.start.astimezone(tz).date()).days
        lines+=[f"Arrivo: {booking.start.astimezone(tz):%d/%m/%Y %H:%M}",f"Partenza: {booking.end.astimezone(tz):%d/%m/%Y %H:%M}",f"Notti: {nights}"]
    else:lines.append(f"Quando: {booking.start.astimezone(tz):%d/%m/%Y %H:%M}")
    if booking.party>1 or booking.service.kind!="resource":lines.append(f"Persone / quantità: {booking.party}")
    lines.append("Cliente: "+(name or "—"))
    lines.append(f"Dettagli: {settings.PUBLIC_ORIGIN}/t/{tenant.slug}/bookings/{booking.pk}/")
    return "\n".join(lines)

def notify_staff(tenant,booking,event):
    if not (tenant.notify_email or tenant.telegram or tenant.notify_operators):return
    text=summary(tenant,booking,event);subject=TITLES[event]+" · "+(booking.snapshot.get("service") or "")
    to=[tenant.notify_email] if tenant.notify_email else []
    from .booking import visit_rows
    for rid in dict.fromkeys(b.resource_id for b in visit_rows(booking) if b.resource_id):to+=[e for e,_ in operators(tenant,rid) if e not in to]  # 2.14.0: also the operator of this booking (of each service of a visit)
    from .wpsync import by_site
    if by_site(tenant):to=[]  # 2.21.0: the site in charge tells the business by email; Telegram stays from here
    for address in to:queue(tenant,{"to":address,"subject":subject,"body":text})
    if tenant.telegram:
        d=queue(tenant,{"text":text})
        d.channel="telegram";d.save(update_fields=["channel"])

def operators(tenant,resource_id=None):
    """2.14.0: with "Avvisa anche l'operatore", the people limited to their own operators (Persone e permessi) who
    have an email other than the business's: (email, their operators), only those of resource_id when given."""
    from .models import Membership
    if not tenant.notify_operators:return []
    out=[]
    for m in Membership.objects.filter(tenant=tenant,user__is_active=True).select_related("user"):
        email=m.user.email if m.resources else ""
        if email and email!=tenant.notify_email and (resource_id is None or str(resource_id) in m.resources):out.append((email,m.resources))
    return out

def line(tenant,booking):
    """One booking of the evening list: time, service, with whom, how many and the abbreviated name, as the notices."""
    s=booking.snapshot;tz=ZoneInfo(tenant.timezone)
    parts=[f"{booking.start.astimezone(tz):%H:%M}",s.get("service") or "",s.get("resource") or "",f"{booking.party} pers." if booking.party>1 else "",short(booking.data) or "—"]
    return " · ".join(x for x in parts if x)+(" (da confermare)" if booking.status=="pending" else "")

def evening(now=None):
    """2.14.0: tomorrow's bookings, once a day from the hour the business chose (its own time): to its address and
    Telegram, and to each operator his own, when the business wants it and he has some."""
    from datetime import datetime,timedelta,time
    from django.db import transaction
    from django.utils import timezone
    from .models import Tenant,Booking
    from .middleware import operational
    from .tenancy import scope
    now=now or timezone.now();sent=0
    for tenant in Tenant.objects.exclude(summary_hour=None).iterator():
        today=now.astimezone(ZoneInfo(tenant.timezone))
        if today.hour<tenant.summary_hour or tenant.summary_sent==today.date() or not operational(tenant):continue
        day=today.date()+timedelta(days=1);start=datetime.combine(day,time.min,ZoneInfo(tenant.timezone))
        with scope(tenant),transaction.atomic():
            t=Tenant.objects.select_for_update().get(pk=tenant.pk)
            if t.summary_sent==today.date():continue
            rows=list(Booking.objects.filter(start__gte=start,start__lt=start+timedelta(days=1),status__in=("pending","confirmed")).select_related("tenant").order_by("start"))
            title=f"Domani, {day:%d/%m} · {t.name}"
            text=lambda rs:title+"\n"+("\n".join(line(t,b) for b in rs) if rs else "Nessuna prenotazione.")
            if t.notify_email:queue(t,{"to":t.notify_email,"subject":title,"body":text(rows)})
            if t.telegram:
                d=queue(t,{"text":text(rows)});d.channel="telegram";d.save(update_fields=["channel"])
            for email,mine in operators(t):
                if own:=[b for b in rows if str(b.resource_id) in mine]:queue(t,{"to":email,"subject":title,"body":text(own)})
            t.summary_sent=today.date();t.save(update_fields=["summary_sent"]);sent+=1
    return sent

class TelegramError(Exception):
    def __init__(self,code,temporary):super().__init__(code);self.code=code;self.temporary=temporary

def send_telegram(tenant,payload):
    """POST to the fixed Telegram API host; the bot token never leaves the encrypted tenant settings."""
    if not tenant.telegram:raise TelegramError("telegram_missing",False)
    conf=decrypt(tenant,tenant.telegram,"telegram")
    body=urllib.parse.urlencode({"chat_id":conf["chat"],"text":payload["text"][:4000],"disable_web_page_preview":"true"}).encode()
    request=urllib.request.Request(f"https://api.telegram.org/bot{conf['token']}/sendMessage",data=body,method="POST")
    try:
        with urllib.request.urlopen(request,timeout=10) as response:
            if not json.loads(response.read() or b"{}").get("ok"):raise TelegramError("telegram_rejected",False)
    except urllib.error.HTTPError as ex:
        raise TelegramError(f"telegram_{ex.code}",ex.code==429 or ex.code>=500)
    except urllib.error.URLError:raise TelegramError("telegram_unreachable",True)  # network down: retry instead of losing it
