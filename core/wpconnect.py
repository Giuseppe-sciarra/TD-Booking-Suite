"""2.6.0: the WordPress plugin connects itself to the portal.

The portal gives a one-time code (valid one day). In WordPress the administrator enters the
portal address and the code, the plugin calls connect() from its server: the portal authorizes
the site for the widget and returns the configuration (business, locations, services), so the
plugin needs nothing else. config() returns the same public data later, to refresh it.
"""
import hashlib,json,secrets
from datetime import timedelta
from urllib.parse import urlparse
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST,require_GET
from .models import Tenant,Location,Service,audit
from .presets import PRESETS
from .tenancy import scope

ALPHABET="ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I

def normalize(code):return "".join(c for c in str(code).upper() if c.isalnum())
def digest(code):return hashlib.sha256(("tdbs-connect:"+normalize(code)).encode()).hexdigest()

def new_code(tenant,actor=None):
    raw="".join(secrets.choice(ALPHABET) for _ in range(16))
    code="-".join(raw[i:i+4] for i in range(0,16,4))
    tenant.connect_hash=digest(code);tenant.connect_until=timezone.now()+timedelta(days=1)
    tenant.save(update_fields=["connect_hash","connect_until"])
    with scope(tenant):audit(tenant,actor,"wordpress.code",tenant.pk)
    return code

def valid_origin(value):
    """https://host[:port] with nothing else; plain http only on a development portal."""
    p=urlparse(value or "")
    schemes=("https","http") if settings.DEBUG else ("https",)
    if p.scheme not in schemes or not p.hostname or p.path not in ("","/") or p.query or p.fragment or p.username or p.password or "*" in value:return ""
    return f"{p.scheme}://{p.netloc}".lower()

def config(tenant):
    with scope(tenant):
        locations=list(Location.objects.filter(active=True).order_by("name"))
        services=list(Service.objects.filter(active=True,location__active=True).select_related("location").order_by("location__name","name"))
    p=PRESETS.get(tenant.business_type)
    return {"tenant":tenant.slug,"name":tenant.name,"portal":settings.PUBLIC_ORIGIN.rstrip("/"),
        "business_type":tenant.business_type,"business_label":p["label"] if p else "",
        "brand_color":tenant.brand_color,"booking_url":settings.PUBLIC_ORIGIN.rstrip("/")+"/book/"+tenant.slug+"/",
        "locations":[{"id":str(l.pk),"name":l.name,"address":l.address} for l in locations],
        "services":[{"id":str(s.pk),"name":s.name,"location":str(s.location_id),"location_name":s.location.name} for s in services]}

def fail(message,status=400):return JsonResponse({"ok":False,"error":message},status=status)

@csrf_exempt
@require_POST
def connect_view(request):
    from django.core.exceptions import PermissionDenied
    from .identity import throttle
    try:throttle(request,"wp-connect",20,3600)
    except PermissionDenied:return fail("Troppi tentativi: riprova tra un'ora.",429)
    try:data=json.loads(request.body or b"{}")
    except ValueError:return fail("Richiesta non valida.")
    if not isinstance(data,dict):return fail("Richiesta non valida.")
    code=normalize(data.get("code",""))
    if len(code)!=16:return fail("Il codice di collegamento non è valido.")
    site=valid_origin(str(data.get("site","")))
    if not site:return fail("L'indirizzo del sito deve essere https://dominio, senza percorsi.")
    tenant=Tenant.objects.filter(connect_hash=digest(code),connect_until__gt=timezone.now()).first()
    if not tenant:return fail("Codice non valido o scaduto: generane uno nuovo dal portale.",403)
    origins=list(tenant.embed_origins or [])
    if site not in origins:
        if len(origins)>=10:return fail("L'attività ha già 10 siti collegati: togline uno dalle impostazioni.",409)
        origins.append(site)
    # one use only
    tenant.embed_origins=origins;tenant.connect_hash="";tenant.connect_until=None
    tenant.save(update_fields=["embed_origins","connect_hash","connect_until"])
    with scope(tenant):audit(tenant,None,"wordpress.connected",tenant.pk,site=site)
    return JsonResponse({"ok":True,**config(tenant)})

@require_GET
def config_view(request,slug):
    """Public data the booking page shows anyway: the plugin refreshes its list of shortcodes."""
    return JsonResponse({"ok":True,**config(request.tenant)})
