"""2.14.0: Outlook.com / Microsoft 365 calendar, both ways, for each operator, as gcal.py does with Google. The agency puts
the app of Microsoft Entra (ID and secret, encrypted); "Collega Outlook" opens Microsoft's consent (state + PKCE), the
tokens are kept encrypted. Every 5 minutes the worker reads his calendar (calendarView/delta) into "Occupato su Outlook"
blocks, without titles, and writes his bookings there (events create, patch, delete). Standard library only."""
import base64,hashlib,json,re,secrets
from datetime import datetime,timedelta,timezone as TZ
from urllib.parse import urlencode,urlsplit
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .gcal import call,GoogleError as GraphError,EVERY,AHEAD,LOOPBACK

LOGIN="https://login.microsoftonline.com/common/oauth2/v2.0/"
AUTH=LOGIN+"authorize";TOKEN=LOGIN+"token"
GRAPH="https://graph.microsoft.com/v1.0/me/"
SCOPE="offline_access User.Read Calendars.ReadWrite"

def config(tenant):
    from .crypto import decrypt
    return decrypt(tenant,tenant.microsoft,"microsoft") if tenant.microsoft else {}

def clean(post,saved):
    """The Entra app as the agency typed it; an empty secret keeps the saved one, off removes it."""
    if post.get("microsoft_off")=="on":return {}
    conf={"client_id":str(post.get("ms_client_id","")).strip(),"client_secret":str(post.get("ms_client_secret","")).strip() or saved.get("client_secret","")}
    if not conf["client_id"] and not post.get("ms_client_secret"):return {}
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",conf["client_id"]):raise ValueError("L'ID applicazione (client) di Microsoft è come 1a2b3c4d-1234-5678-9abc-1234567890ab.")
    if not re.fullmatch(r"[\w.~\-]{0,200}",conf["client_secret"]):raise ValueError("Il valore del segreto client non è valido.")
    return conf

def state(resource):
    from .crypto import decrypt
    return decrypt(resource.tenant,resource.microsoft,"microsoft:"+str(resource.pk)) if resource.microsoft else {}

def keep(resource,st,**fields):
    from .crypto import encrypt
    resource.microsoft=encrypt(resource.tenant,st,"microsoft:"+str(resource.pk)) if st else ""
    for k,v in fields.items():setattr(resource,k,v)
    resource.save(update_fields=["microsoft",*fields])

def redirect_uri(request):
    path="/oauth/outlook-calendar/"  # one address for the whole installation, to put once in the Entra app
    if urlsplit("//"+request.get_host()).hostname in LOOPBACK:return request.build_absolute_uri(path)
    return settings.PUBLIC_ORIGIN+path

def start(request,resource):
    conf=config(request.tenant);verifier=secrets.token_urlsafe(48);st=secrets.token_urlsafe(24);uri=redirect_uri(request)
    request.session["mscal_oauth"]={"state":st,"verifier":verifier,"tenant":str(request.tenant.pk),"slug":request.tenant.slug,"resource":str(resource.pk),"uri":uri}
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTH+"?"+urlencode({"client_id":conf["client_id"],"redirect_uri":uri,"response_type":"code","scope":SCOPE,"response_mode":"query",
        "prompt":"select_account","state":st,"code_challenge":challenge,"code_challenge_method":"S256"})

def finish(request):
    """Microsoft's answer: the operator connected (or the error on his card); None when it is not the answer we wait for."""
    from .models import Resource
    saved=request.session.pop("mscal_oauth",None) or {}
    if saved.get("tenant")!=str(request.tenant.pk) or not secrets.compare_digest(saved.get("state",""),request.GET.get("state","")):return None
    r=Resource.objects.filter(pk=saved["resource"]).select_related("tenant").first()
    if not r:return None
    conf=config(request.tenant);error=""
    if request.GET.get("error") or not conf:error="Microsoft non ha dato l'accesso: "+(request.GET.get("error","") or "app tolta")[:60]+"."
    else:
        body={"code":request.GET.get("code",""),"client_id":conf["client_id"],"redirect_uri":saved["uri"],"grant_type":"authorization_code","code_verifier":saved["verifier"],"scope":SCOPE}
        if conf.get("client_secret"):body["client_secret"]=conf["client_secret"]
        try:
            t=call("POST",TOKEN,form=body)
            if not t.get("refresh_token"):raise GraphError(400,"Microsoft non ha dato l'accesso permanente: riprova.")
            me=call("GET",GRAPH[:-1],token=t.get("access_token",""))
        except GraphError as ex:error=message(ex)
    if error:keep(r,state(r),microsoft_error=error[:200])
    else:keep(r,{"refresh":t["refresh_token"],"access":t.get("access_token",""),"expires":timezone.now().timestamp()+int(t.get("expires_in",3600)),"account":me.get("mail") or me.get("userPrincipalName","")},microsoft_at=None,microsoft_error="")
    return r

def message(ex):
    if ex.reason=="invalid_grant":return "Microsoft ha chiuso il collegamento (revocato o scaduto): collegalo di nuovo."
    if ex.status==0:return ex.reason
    return f"Microsoft ha risposto con un errore {ex.status}"+(": "+ex.reason[:120] if ex.reason else ".")

def access(conf,st,now):
    if st.get("access") and st.get("expires",0)>now.timestamp()+60:return st["access"]
    body={"client_id":conf["client_id"],"refresh_token":st["refresh"],"grant_type":"refresh_token","scope":SCOPE}
    if conf.get("client_secret"):body["client_secret"]=conf["client_secret"]
    t=call("POST",TOKEN,form=body)
    st.update(access=t["access_token"],expires=now.timestamp()+int(t.get("expires_in",3600)))
    if t.get("refresh_token"):st["refresh"]=t["refresh_token"]  # Microsoft may hand a new one: the old one ends
    return st["access"]

def when(x):
    """Graph's times are UTC without offset, with seven decimals."""
    return datetime.fromisoformat(x["dateTime"][:26]).replace(tzinfo=TZ.utc)

def read(tenant,resource,token,st,now):
    """His events into the blocks: calendarView/delta from yesterday for 180 days, then only the changes (deltaLink);
    once a day all again, so the far ones come closer. Our own events, the free and the cancelled are left out."""
    from .feeds import blocks
    from .models import TimeBlock
    tz=ZoneInfo(tenant.timezone);before=st.get("events",{})
    full=not st.get("delta") or st.get("full",0)<now.timestamp()-86400
    ours={v[0] for v in st.get("written",{}).values()}
    url=st["delta"] if not full else GRAPH+"calendarView/delta?"+urlencode({"startDateTime":(now-timedelta(days=1)).astimezone(TZ.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),"endDateTime":(now+AHEAD).astimezone(TZ.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    seen={} if full else dict(before)
    while url:
        r=call("GET",url,token=token)
        for e in r.get("value",[]):
            if "@removed" in e or e.get("isCancelled") or e.get("showAs") in ("free","workingElsewhere") or e.get("id") in ours:seen.pop(e.get("id"),None);continue
            try:seen[e["id"]]=[when(e["start"]).isoformat(),when(e["end"]).isoformat()]
            except (KeyError,ValueError,TypeError):pass
        url=r.get("@odata.nextLink");st["delta"]=r.get("@odata.deltaLink",st.get("delta",""))
    st["events"]={k:v for k,v in seen.items() if datetime.fromisoformat(v[1])>now and datetime.fromisoformat(v[0])<now+AHEAD}
    if full:st["full"]=now.timestamp()
    if full or st["events"]!=before:
        spans=[(datetime.fromisoformat(a),datetime.fromisoformat(b)) for a,b in st["events"].values()]
        with transaction.atomic():
            TimeBlock.objects.filter(resource=resource,reason="outlook").delete()
            TimeBlock.objects.bulk_create([TimeBlock(tenant=tenant,resource=resource,day=d,start=a,end=z,reason="outlook") for d,a,z in blocks(spans,tz)[:3000]])

def event(b,resource):
    what=b.snapshot.get("service") or b.service.name
    if resource.feed_names:d=b.data;what+=" · "+" ".join(x for x in (d.get("first_name",""),d.get("last_name","")) if x)
    fmt=lambda x:{"dateTime":x.astimezone(TZ.utc).strftime("%Y-%m-%dT%H:%M:%S"),"timeZone":"UTC"}
    return {"subject":what.strip(" ·"),"start":fmt(b.start),"end":fmt(b.end),"showAs":"busy","isReminderOn":False}

def write(tenant,resource,token,st,now):
    """His bookings from now on as events: created (transactionId: a retry does not double), patched when changed,
    deleted when cancelled or moved away; written: {booking: [event id, signature]}.
    shortcut: the first time all of the next 180 days go in one run of the worker; fine up to a few hundred."""
    from .booking import ACCEPTED
    from .models import Booking
    written=st.setdefault("written",{});want={}
    for b in Booking.objects.filter(resource=resource,status__in=ACCEPTED,end__gte=now,start__lte=now+AHEAD).select_related("service"):
        body=event(b,resource);want[str(b.pk)]=(body,hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()[:16])
    for pk,(body,sig) in want.items():
        old=written.get(pk)
        if old and old[1]==sig:continue
        if old:
            try:call("PATCH",GRAPH+"events/"+old[0],body,token);written[pk]=[old[0],sig];continue
            except GraphError as ex:
                if ex.status!=404:raise
        e=call("POST",GRAPH+"events",{**body,"transactionId":hashlib.sha256(f"{tenant.pk}:{pk}:{resource.pk}:{sig}".encode()).hexdigest()[:36]},token)
        written[pk]=[e["id"],sig]
    done={str(x) for x in Booking.objects.filter(pk__in=[p for p in written if p not in want],resource=resource,status__in=ACCEPTED,end__lt=now).values_list("pk",flat=True)}
    for pk in [p for p in written if p not in want]:
        if pk not in done:
            try:call("DELETE",GRAPH+"events/"+written[pk][0],token=token)
            except GraphError as ex:
                if ex.status not in (404,410):raise
        del written[pk]

def sync(tenant,resource,now):
    from .models import TimeBlock
    conf=config(tenant);st=state(resource);error=""
    try:
        token=access(conf,st,now)
        write(tenant,resource,token,st,now);read(tenant,resource,token,st,now)
    except (KeyError,ValueError,TypeError):error="Microsoft ha dato una risposta che non capiamo: si riprova tra 5 minuti."
    except GraphError as ex:
        error=message(ex)
        if ex.status==401:st["expires"]=0
        if ex.status==410:st.pop("delta",None)  # the deltaLink is no longer known: all again
        if ex.reason=="invalid_grant":st={};TimeBlock.objects.filter(resource=resource,reason="outlook").delete()
    keep(resource,st,microsoft_at=now,microsoft_error=error[:200])
    return not error

def disconnect(resource):
    """Scollega: the tokens forgotten (Microsoft has no revoke for them), the blocks read from it removed."""
    from .models import TimeBlock
    TimeBlock.objects.filter(resource=resource,reason="outlook").delete()
    keep(resource,{},microsoft_at=None,microsoft_error="")

def refresh(now=None):
    from .models import Tenant,Resource
    from .middleware import operational
    from .tenancy import scope
    now=now or timezone.now();done=0
    for tenant in Tenant.objects.exclude(microsoft="").iterator():
        if not operational(tenant):continue
        with scope(tenant):
            for r in Resource.objects.exclude(microsoft="").filter(Q(microsoft_at__isnull=True)|Q(microsoft_at__lte=now-EVERY)).select_related("tenant"):done+=sync(tenant,r,now)
    return done
