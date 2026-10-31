"""2.14.0: the read-only API of a business (bookings, services, free times) with its own key, and the webhooks on a
booking created, moved or cancelled, signed with HMAC and sent by mail_worker with retries. The agency sets both up.
A webhook carries no client data: the receiver reads the booking from the API, with the key. 2.14.0: the agency can
let the owner set both himself (api_enabled), as for the emails' words."""
import hashlib,hmac,http.client,json,secrets,socket,time,urllib.error,urllib.request
from datetime import date,datetime,timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from django.contrib import messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render,redirect
from django.utils import timezone
from .booking import BookingError,available,queue
from .crypto import decrypt,encrypt,token_hash
from .models import Booking,Service,Tenant,Delivery,audit

EVENTS={"created":"booking.created","moved":"booking.moved","cancelled":"booking.cancelled"}
class WebhookError(Exception):
    def __init__(self,code,temporary):super().__init__(code);self.code=code;self.temporary=temporary

def emit(tenant,booking,event):
    """Queue the webhook of a booking, when the business has one; called inside the writer's transaction."""
    if not tenant.webhook:return
    tz=ZoneInfo(tenant.timezone)
    payload={"id":secrets.token_hex(8),"event":EVENTS[event],"at":timezone.now().isoformat(),"booking":{"id":str(booking.pk),"status":booking.status,
        "start":booking.start.astimezone(tz).isoformat(),"end":booking.end.astimezone(tz).isoformat(),"service":str(booking.service_id),
        "resource":str(booking.resource_id or ""),"party":booking.party,"origin":booking.origin,"source":booking.source}}
    d=queue(tenant,payload);d.channel="webhook";d.save(update_fields=["channel"])

def check_url(url):
    from .mail import public_addresses
    parts=urlsplit(url)
    if parts.scheme!="https" or not parts.hostname:raise ValueError("L'indirizzo del webhook deve iniziare con https://")
    public_addresses(parts.hostname,parts.port or 443)  # no local or reserved networks, as for SMTP

class Unfollowed(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**kw):return None  # a redirect could lead to a local address: it fails instead
class Pinned(http.client.HTTPSConnection):
    """Connects to an address checked as public in this very moment, not to a second lookup of the name that could
    answer a local one (as PinnedSMTP); TLS still checks the name."""
    def connect(self):
        from .mail import public_addresses
        family,kind,proto,addr=public_addresses(self.host,self.port)[0]
        sock=socket.socket(family,kind,proto);sock.settimeout(self.timeout)
        try:sock.connect(addr);self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
        except Exception:sock.close();raise
class PinnedHandler(urllib.request.HTTPSHandler):
    def https_open(self,req):return self.do_open(Pinned,req,context=self._context)
OPENER=urllib.request.build_opener(Unfollowed,PinnedHandler)
def send_webhook(tenant,payload):
    if not tenant.webhook:raise WebhookError("webhook_missing",False)
    conf=decrypt(tenant,tenant.webhook,"webhook")
    try:check_url(conf["url"])
    except (ValueError,OSError):raise WebhookError("webhook_address",True)
    body=json.dumps(payload,separators=(",",":")).encode();stamp=str(int(time.time()))
    sign=hmac.new(conf["secret"].encode(),stamp.encode()+b"."+body,hashlib.sha256).hexdigest()
    request=urllib.request.Request(conf["url"],data=body,method="POST",headers={"Content-Type":"application/json","User-Agent":"TD-Booking-Suite",
        "X-TDBS-Event":payload["event"],"X-TDBS-Timestamp":stamp,"X-TDBS-Signature":"sha256="+sign})
    try:
        with OPENER.open(request,timeout=10):pass
    except urllib.error.HTTPError as ex:raise WebhookError(f"webhook_{ex.code}",ex.code in (408,429) or ex.code>=500)
    except ValueError:raise WebhookError("webhook_address",True)  # now resolving to a local address
    except (urllib.error.URLError,OSError):raise WebhookError("webhook_unreachable",True)

# ---------- the API
def authorized(request,slug):
    """The key in Authorization: Bearer ...; at most 120 calls a minute per business."""
    from .identity import throttle
    tenant=request.tenant
    key=request.headers.get("Authorization","").removeprefix("Bearer ").strip()
    if not tenant.api_key or not key or not hmac.compare_digest(token_hash(key),tenant.api_key):raise PermissionDenied("Chiave API non valida.")
    throttle(request,"api:"+str(tenant.pk),120,60,identity=str(tenant.pk))
def endpoint(view):
    from functools import wraps
    @wraps(view)
    def wrapper(request,slug,*a,**kw):
        if request.method!="GET":return JsonResponse({"error":"Solo lettura."},status=405)
        try:
            authorized(request,slug)
            return JsonResponse(view(request,*a,**kw))
        except PermissionDenied as ex:return JsonResponse({"error":str(ex)},status=403 if "Troppi" not in str(ex) else 429)
        except (BookingError,ValidationError,ValueError) as ex:return JsonResponse({"error":str(ex) or "Parametri non validi."},status=400)
    return wrapper
def day_param(request,name,default):
    v=request.GET.get(name)
    return date.fromisoformat(v) if v else default

@endpoint
def bookings(request):
    tz=ZoneInfo(request.tenant.timezone);today=timezone.now().astimezone(tz).date()
    first=day_param(request,"from",today);last=day_param(request,"to",first+timedelta(days=30))
    if not 0<=(last-first).days<=366:raise ValueError("Intervallo non valido, massimo 367 giorni.")
    a=datetime.combine(first,datetime.min.time(),tz);b=datetime.combine(last+timedelta(days=1),datetime.min.time(),tz)
    out=[]
    for x in Booking.objects.filter(start__lt=b,end__gt=a).select_related("tenant").order_by("start")[:5000]:
        d=x.data
        out.append({"id":str(x.pk),"status":x.status,"start":x.start.astimezone(tz).isoformat(),"end":x.end.astimezone(tz).isoformat(),
            "service":str(x.service_id),"service_name":x.snapshot.get("service",""),"resource":str(x.resource_id or ""),"resource_name":x.snapshot.get("resource",""),
            "location":str(x.location_id),"party":x.party,"origin":x.origin,"source":x.source,"price":x.snapshot.get("total",""),"created":x.created.astimezone(tz).isoformat(),
            "client":{k:d.get(k,"") for k in ("first_name","last_name","email","phone")}})
    return {"bookings":out}
@endpoint
def services(request):
    rows=Service.objects.filter(active=True,location__active=True).select_related("location").prefetch_related("resource_links__resource").order_by("name")
    return {"services":[{"id":str(s.pk),"name":s.name,"kind":s.kind,"duration":s.duration,"price":str(s.price) if s.price is not None else "","location":str(s.location_id),
        "location_name":s.location.name,"resources":[{"id":str(l.resource_id),"name":l.resource.name} for l in s.resource_links.all() if l.resource.active]} for s in rows]}
@endpoint
def availability(request):
    day=date.fromisoformat(request.GET.get("day",""))
    return {"slots":available(request.tenant,request.GET.get("service"),day,request.GET.get("option") or None,request.GET.get("resource") or None,request.GET.get("party",1))}

# ---------- the agency's page
def settings_view(request,slug):
    from .permissions import can
    from .identity import fresh
    tenant=request.tenant;key="";error=""
    # the agency's; the owner's too when the agency allows it (Impostazioni dell'attività)
    if not (request.user.is_staff or tenant.api_enabled and can(request,"users.manage")):raise PermissionDenied
    fresh(request)
    conf=decrypt(tenant,tenant.webhook,"webhook") if tenant.webhook else {}
    if request.method=="POST":
        action=request.POST.get("action")
        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=tenant.pk)
            if action=="key":
                key="tdbs_"+secrets.token_urlsafe(32);tenant.api_key=token_hash(key);tenant.save(update_fields=["api_key"])
                audit(tenant,request.user,"api.key",tenant.pk)
            elif action=="revoke":
                tenant.api_key="";tenant.save(update_fields=["api_key"]);audit(tenant,request.user,"api.revoked",tenant.pk)
                messages.success(request,"Chiave revocata: chi la usava non entra più.");return redirect("api_settings",slug)
            elif action=="webhook":
                url=request.POST.get("url","").strip()
                try:
                    if url:
                        check_url(url)
                        conf={"url":url[:500],"secret":conf.get("secret") or secrets.token_urlsafe(32)}
                        tenant.webhook=encrypt(tenant,conf,"webhook")
                    else:conf={};tenant.webhook=""
                    tenant.save(update_fields=["webhook"]);audit(tenant,request.user,"api.webhook",tenant.pk,on=bool(url))
                    messages.success(request,"Webhook salvato." if url else "Webhook tolto.");return redirect("api_settings",slug)
                except (ValueError,OSError) as ex:error=str(ex) or "Indirizzo non raggiungibile."
    failed=Delivery.objects.filter(channel="webhook",status="failed").count()
    return render(request,"api.html",{"key":key,"has_key":bool(tenant.api_key),"conf":conf,"error":error,"failed":failed,"base":request.build_absolute_uri(f"/book/{slug}/api/v1/")})
