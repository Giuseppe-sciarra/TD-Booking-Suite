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
TITLES={"created":"Nuova prenotazione","moved":"Prenotazione spostata","cancelled":"Prenotazione annullata"}

def summary(tenant,booking,event):
    tz=ZoneInfo(tenant.timezone);d=booking.data;s=booking.snapshot
    name=(d.get("first_name") or "").strip()
    if d.get("last_name"):name+=" "+d["last_name"].strip()[:1].upper()+"."
    lines=[TITLES[event]+" · "+tenant.name,"Servizio: "+(s.get("service") or "")]
    if s.get("option"):lines.append("Prestazione: "+s["option"])
    if s.get("location"):lines.append("Sede: "+s["location"])
    if s.get("resource"):lines.append((booking.service.resource_label or "Risorsa")+": "+s["resource"])
    if booking.service.kind=="day":
        nights=(booking.end.astimezone(tz).date()-booking.start.astimezone(tz).date()).days
        lines+=[f"Arrivo: {booking.start.astimezone(tz):%d/%m/%Y %H:%M}",f"Partenza: {booking.end.astimezone(tz):%d/%m/%Y %H:%M}",f"Notti: {nights}"]
    else:lines.append(f"Quando: {booking.start.astimezone(tz):%d/%m/%Y %H:%M}")
    if booking.party>1 or booking.service.kind!="resource":lines.append(f"Persone / quantità: {booking.party}")
    lines.append("Cliente: "+(name or "—"))
    lines.append(f"Dettagli: {settings.PUBLIC_ORIGIN}/t/{tenant.slug}/bookings/{booking.pk}/")
    return "\n".join(lines)

def notify_staff(tenant,booking,event):
    if not (tenant.notify_email or tenant.telegram):return
    text=summary(tenant,booking,event)
    if tenant.notify_email:
        queue(tenant,{"to":tenant.notify_email,"subject":TITLES[event]+" · "+(booking.snapshot.get("service") or ""),"body":text})
    if tenant.telegram:
        d=queue(tenant,{"text":text})
        d.channel="telegram";d.save(update_fields=["channel"])

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
