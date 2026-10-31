"""2.14.0: Google Calendar, both ways, for each operator. The agency puts the OAuth client of a Google Cloud project
(ID and secret, encrypted); from the operator's card "Collega Google Calendar" opens Google's consent (state + PKCE) and
the tokens are kept encrypted. Every 5 minutes the worker reads the events of his main calendar (events.list with
singleEvents and syncToken) into "Occupato su Google" blocks, without titles, and writes his bookings there: service
and time, the client's name only when the business chose it for his feed. Only outgoing HTTPS, standard library."""
import base64,hashlib,json,re,secrets
from datetime import date,datetime,timedelta,time,timezone as TZ
from urllib.parse import quote,urlencode,urlsplit
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

AUTH="https://accounts.google.com/o/oauth2/v2/auth"
TOKEN="https://oauth2.googleapis.com/token"
REVOKE="https://oauth2.googleapis.com/revoke"
CALENDARS="https://www.googleapis.com/calendar/v3/users/me/calendarList"
def events_url(cal):return "https://www.googleapis.com/calendar/v3/calendars/"+quote(cal or "primary",safe="")+"/events"
SCOPE="https://www.googleapis.com/auth/calendar.events"
EVERY=timedelta(minutes=4)  # the worker looks every 5 minutes
AHEAD=timedelta(days=180)
LOOPBACK=("127.0.0.1","localhost","::1")

class GoogleError(Exception):
    def __init__(self,status,reason=""):super().__init__(reason or f"http_{status}");self.status=status;self.reason=reason

def call(method,url,body=None,token="",form=None):
    """One call to Google; its JSON answer. An error carries Google's reason (invalid_grant as it is, for the tokens)."""
    from .mailapi import http,detail,ApiError
    headers={"Authorization":"Bearer "+token} if token else {}
    if body is not None:headers["Content-Type"]="application/json"
    try:status,data=http(url,form=form,body=json.dumps(body) if body is not None else None,headers=headers,timeout=20,method=method)
    except ApiError as ex:raise GoogleError(0,str(ex))
    if status>=300:raise GoogleError(status,"invalid_grant" if data.get("error")=="invalid_grant" else detail(data))
    return data

def config(tenant):
    from .crypto import decrypt
    return decrypt(tenant,tenant.google,"google") if tenant.google else {}

def clean(post,saved):
    """The OAuth client as the agency typed it; an empty secret keeps the saved one, off removes it."""
    if post.get("google_off")=="on":return {}
    conf={"client_id":str(post.get("client_id","")).strip(),"client_secret":str(post.get("client_secret","")).strip() or saved.get("client_secret","")}
    if not conf["client_id"] and not post.get("client_secret"):return {}
    # they go into Google's addresses and forms: only the characters Google uses
    if not re.fullmatch(r"[\w.\-]{5,200}\.apps\.googleusercontent\.com",conf["client_id"]):raise ValueError("L'ID client finisce con .apps.googleusercontent.com.")
    if not re.fullmatch(r"[\w\-]{0,200}",conf["client_secret"]):raise ValueError("Il client secret non è valido.")
    return conf

def state(resource):
    from .crypto import decrypt
    return decrypt(resource.tenant,resource.google,"google:"+str(resource.pk)) if resource.google else {}

def keep(resource,st,**fields):
    from .crypto import encrypt
    resource.google=encrypt(resource.tenant,st,"google:"+str(resource.pk)) if st else ""
    for k,v in fields.items():setattr(resource,k,v)
    resource.save(update_fields=["google",*fields])

def redirect_uri(request):
    """Back to this program: at its public address (a "Web application" client), or, opened on this very PC, at the
    loopback address a "Desktop app" client accepts (Windows without a public address)."""
    path="/oauth/google-calendar/"  # one address for the whole installation, to put once in Google Cloud
    if urlsplit("//"+request.get_host()).hostname in LOOPBACK:return request.build_absolute_uri(path)
    return settings.PUBLIC_ORIGIN+path

def start(request,resource):
    """Google's consent page, with a state and a PKCE verifier kept in the session."""
    conf=config(request.tenant);verifier=secrets.token_urlsafe(48);st=secrets.token_urlsafe(24);uri=redirect_uri(request)
    request.session["gcal_oauth"]={"state":st,"verifier":verifier,"tenant":str(request.tenant.pk),"slug":request.tenant.slug,"resource":str(resource.pk),"uri":uri}
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return AUTH+"?"+urlencode({"client_id":conf["client_id"],"redirect_uri":uri,"response_type":"code","scope":SCOPE,"access_type":"offline",
        "prompt":"consent","state":st,"code_challenge":challenge,"code_challenge_method":"S256"})

def finish(request):
    """Google's answer: the operator connected (or the error on his card); None when it is not the answer we wait for."""
    from .models import Resource
    saved=request.session.pop("gcal_oauth",None) or {}
    if saved.get("tenant")!=str(request.tenant.pk) or not secrets.compare_digest(saved.get("state",""),request.GET.get("state","")):return None
    r=Resource.objects.filter(pk=saved["resource"]).select_related("tenant").first()
    if not r:return None
    conf=config(request.tenant);error=""
    if request.GET.get("error") or not conf:error="Google non ha dato l'accesso: "+(request.GET.get("error","") or "client OAuth tolto")[:60]+"."
    else:
        body={"code":request.GET.get("code",""),"client_id":conf["client_id"],"redirect_uri":saved["uri"],"grant_type":"authorization_code","code_verifier":saved["verifier"]}
        if conf.get("client_secret"):body["client_secret"]=conf["client_secret"]
        try:
            t=call("POST",TOKEN,form=body)
            if not t.get("refresh_token"):raise GoogleError(400,"Google non ha dato l'accesso permanente: riprova.")
        except GoogleError as ex:error=message(ex)
    if error:keep(r,state(r),google_error=error[:200])
    else:keep(r,{"refresh":t["refresh_token"],"access":t.get("access_token",""),"expires":timezone.now().timestamp()+int(t.get("expires_in",3600))},google_at=None,google_error="")
    return r

def message(ex):
    if ex.reason=="invalid_grant":return "Google ha chiuso il collegamento (revocato o scaduto): collegalo di nuovo."
    if ex.status==0:return ex.reason
    if ex.status==403:return "Google rifiuta: controlla che la Google Calendar API sia attiva nel progetto ("+ex.reason[:80]+")."
    return f"Google ha risposto con un errore {ex.status}"+(": "+ex.reason[:120] if ex.reason else ".")

def access(conf,st,now):
    """A valid access token, refreshed when it is about to end."""
    if st.get("access") and st.get("expires",0)>now.timestamp()+60:return st["access"]
    body={"client_id":conf["client_id"],"refresh_token":st["refresh"],"grant_type":"refresh_token"}
    if conf.get("client_secret"):body["client_secret"]=conf["client_secret"]
    t=call("POST",TOKEN,form=body)
    st.update(access=t["access_token"],expires=now.timestamp()+int(t.get("expires_in",3600)))
    return st["access"]

def busy(e,tz):
    """[start, end] of an event that keeps the operator busy; None for the free, the cancelled and the bookings we wrote."""
    if e.get("status")=="cancelled" or e.get("transparency")=="transparent" or "tdb_booking_id" in (e.get("extendedProperties") or {}).get("private",{}):return None
    s,z=e.get("start") or {},e.get("end") or {}
    try:
        if "dateTime" in s:a,b=datetime.fromisoformat(s["dateTime"]),datetime.fromisoformat(z["dateTime"])
        else:a,b=(datetime.combine(date.fromisoformat(x["date"]),time.min,tz) for x in (s,z))  # all day, in the business's days
    except (KeyError,ValueError,TypeError):return None
    return [a.isoformat(),b.isoformat()] if b>a else None

def listing(token,cal,sync,since):
    """The events of a calendar changed since its syncToken, or all from since without one: (events, next token,
    calendar name); None when Google no longer knows the token (410)."""
    params={"singleEvents":"true","maxResults":"2500",**({"syncToken":sync} if sync else {"timeMin":since})}
    items=[];page=""
    while True:
        try:r=call("GET",events_url(cal)+"?"+urlencode({**params,**({"pageToken":page} if page else {})}),token=token)
        except GoogleError as ex:
            if ex.status==410 and sync:return None
            raise
        items+=r.get("items",[])
        if not (page:=r.get("nextPageToken")):return items,r.get("nextSyncToken",""),r.get("summary","")

def read(tenant,resource,token,st,now):
    """His events into the blocks: the first time (and once a day, for the far ones coming closer) all from yesterday on,
    then only the changes since the last syncToken; a token Google no longer knows (410) means all again. 2.14.0: from
    the calendar chosen on his card, and our events changed in Google go to the desk as a warning (changed)."""
    from .feeds import blocks
    from .models import TimeBlock
    tz=ZoneInfo(tenant.timezone);before=st.get("events",{});cal=st.get("read","primary");wcal=st.get("write","primary")
    full=not st.get("sync") or st.get("full",0)<now.timestamp()-86400
    since=(now-timedelta(days=1)).astimezone(TZ.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    got=listing(token,cal,"" if full else st["sync"],since)
    if got is None:st.pop("sync",None);return read(tenant,resource,token,st,now)
    items,st["sync"],account=got;seen={} if full else dict(before)
    for e in items:
        if span:=busy(e,tz):seen[e["id"]]=span
        else:seen.pop(e.get("id"),None)
    # the past and the too far are forgotten: the far ones come back with the daily full read
    st["events"]={k:v for k,v in seen.items() if datetime.fromisoformat(v[1])>now and datetime.fromisoformat(v[0])<now+AHEAD}
    st["account"]=account or st.get("account","")
    if full:st["full"]=now.timestamp()
    if full or st["events"]!=before:
        spans=[(datetime.fromisoformat(a),datetime.fromisoformat(b)) for a,b in st["events"].values()]
        with transaction.atomic():
            TimeBlock.objects.filter(resource=resource,reason="google").delete()
            TimeBlock.objects.bulk_create([TimeBlock(tenant=tenant,resource=resource,day=d,start=a,end=z,reason="google") for d,a,z in blocks(spans,tz)[:3000]])
    if wcal!=cal:  # the bookings go to another calendar: its changes read on their own
        got=listing(token,wcal,st.get("wsync",""),since) or listing(token,wcal,"",since)
        items,st["wsync"],_=got
    changed(tenant,resource,items,st)

def changed(tenant,resource,items,st):
    """2.14.0: our events moved or deleted in Google: a warning on the booking ("google" in its snapshot), never applied;
    it goes away when the event is as the booking again (put back in Google, or the booking moved and written)."""
    from .booking import ACCEPTED
    from .models import Booking
    ours={event_id(tenant,pk,resource):pk for pk in st.get("written",{})}
    found={ours[e["id"]]:e for e in items if e.get("id") in ours}
    for b in Booking.objects.filter(pk__in=list(found),status__in=ACCEPTED):
        e=found[str(b.pk)];note="deleted"
        if e.get("status")!="cancelled":
            try:a,z=datetime.fromisoformat(e["start"]["dateTime"]),datetime.fromisoformat(e["end"]["dateTime"])
            except (KeyError,ValueError,TypeError):a=z=None  # turned into an all-day event
            note="" if (a,z)==(b.start,b.end) else "moved:"+(a.isoformat() if a else "")
        if b.snapshot.get("google","")!=note:
            if note:b.snapshot["google"]=note
            else:b.snapshot.pop("google",None)
            b.save(update_fields=["snapshot"])

def calendars(token,st):
    """2.14.0: his calendars, to choose on his card which one is read and which one gets the bookings."""
    r=call("GET",CALENDARS+"?"+urlencode({"minAccessRole":"reader","maxResults":"250"}),token=token)
    st["calendars"]=[[c["id"],c.get("summaryOverride") or c.get("summary") or c["id"],c.get("accessRole") in ("owner","writer")] for c in r.get("items",[]) if c.get("id")][:250]

def choose(resource,read_cal,write_cal):
    """The calendars chosen on his card, among his; a new read calendar is read whole, a new write calendar gets the
    bookings at the next run (and they leave the old one)."""
    st=state(resource);known={c[0]:c[2] for c in st.get("calendars",[])}
    if read_cal not in known and read_cal!="primary" or write_cal!="primary" and not known.get(write_cal):raise ValueError("Scegli uno dei calendari dell'elenco.")
    if read_cal!=st.get("read","primary"):st.pop("sync",None)
    if write_cal!=st.get("write","primary"):st.pop("wsync",None)
    st.update(read=read_cal,write=write_cal);keep(resource,st)

def event_id(tenant,booking_pk,resource):
    # base32hex as Google wants, the same every time: an insert retried after a timeout answers 409 instead of doubling
    return "tdb"+hashlib.sha256(f"{tenant.pk}:{booking_pk}:{resource.pk}".encode()).hexdigest()[:40]

def event(tenant,b,resource):
    what=b.snapshot.get("service") or b.service.name
    if resource.feed_names:d=b.data;what+=" · "+" ".join(x for x in (d.get("first_name",""),d.get("last_name","")) if x)
    return {"summary":what.strip(" ·"),"start":{"dateTime":b.start.isoformat(),"timeZone":tenant.timezone},"end":{"dateTime":b.end.isoformat(),"timeZone":tenant.timezone},
        "transparency":"opaque","status":"confirmed","extendedProperties":{"private":{"tdb_booking_id":str(b.pk)}}}

def put(token,eid,body,known,cal="primary"):
    """Patch what we wrote, insert the rest; 409 on insert means it is there already (a retry, or deleted in Google:
    the patch with status confirmed brings it back), 404 on patch that it is gone: inserted again."""
    base=events_url(cal);url=f"{base}/{eid}?sendUpdates=none"
    if known:
        try:return call("PATCH",url,body,token)
        except GoogleError as ex:
            if ex.status not in (404,410):raise
    try:call("POST",base+"?sendUpdates=none",{"id":eid,**body},token)
    except GoogleError as ex:
        if ex.status!=409:raise
        call("PATCH",url,body,token)

def write(tenant,resource,token,st,now):
    """His bookings from now on as events: new ones inserted, changed ones patched, cancelled or moved away deleted.
    shortcut: the first time all of the next 180 days go in one run of the worker; fine up to a few hundred."""
    from .booking import ACCEPTED
    from .models import Booking
    written=st.setdefault("written",{});want={};cal=st.get("write","primary")
    if st.get("wrote","primary")!=cal:  # 2.14.0: another calendar chosen: out of the old one, all into the new one
        for pk in list(written):
            try:call("DELETE",f"{events_url(st.get('wrote'))}/{event_id(tenant,pk,resource)}?sendUpdates=none",token=token)
            except GoogleError as ex:
                if ex.status not in (404,410):raise
            del written[pk]
        st["wrote"]=cal
    for b in Booking.objects.filter(resource=resource,status__in=ACCEPTED,end__gte=now,start__lte=now+AHEAD).select_related("service"):
        body=event(tenant,b,resource);want[str(b.pk)]=(body,hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()[:16])
    for pk,(body,sig) in want.items():
        if written.get(pk)!=sig:put(token,event_id(tenant,pk,resource),body,pk in written,cal);written[pk]=sig
    gone=[pk for pk in written if pk not in want]
    # ended as planned: they stay in his calendar, as history
    done={str(x) for x in Booking.objects.filter(pk__in=gone,resource=resource,status__in=ACCEPTED,end__lt=now).values_list("pk",flat=True)}
    for pk in gone:
        if pk not in done:
            try:call("DELETE",f"{events_url(cal)}/{event_id(tenant,pk,resource)}?sendUpdates=none",token=token)
            except GoogleError as ex:
                if ex.status not in (404,410):raise  # already deleted in Google
        del written[pk]

def sync(tenant,resource,now):
    """One operator, both ways; the state is kept also when Google stops half way, and the error goes on his card."""
    from .models import TimeBlock
    conf=config(tenant);st=state(resource);error=""
    try:
        token=access(conf,st,now)
        if "calendars" not in st or not st.get("sync"):calendars(token,st)  # at the first run and with each full read
        # written first: what Google gives back then is already as the bookings, so only Google's own changes warn
        write(tenant,resource,token,st,now);read(tenant,resource,token,st,now)
    except (KeyError,ValueError,TypeError):error="Google ha dato una risposta che non capiamo: si riprova tra 5 minuti."  # never stops the worker
    except GoogleError as ex:
        error=message(ex)
        if ex.status==401:st["expires"]=0  # the next run asks for a new access token
        if ex.reason=="invalid_grant":st={};TimeBlock.objects.filter(resource=resource,reason="google").delete()
    keep(resource,st,google_at=now,google_error=error[:200])
    return not error

def disconnect(resource):
    """Scollega: the access given back to Google (when it answers), the blocks read from it removed."""
    from .models import TimeBlock
    if token:=state(resource).get("refresh"):
        try:call("POST",REVOKE,form={"token":token})
        except GoogleError:pass  # revoked already, or Google down: the token is forgotten here anyway
    TimeBlock.objects.filter(resource=resource,reason="google").delete()
    keep(resource,{},google_at=None,google_error="")

def refresh(now=None):
    """Every operator connected and not synced in the last minutes (the worker calls it)."""
    from .models import Tenant,Resource
    from .middleware import operational
    from .tenancy import scope
    now=now or timezone.now();done=0
    for tenant in Tenant.objects.exclude(google="").iterator():
        if not operational(tenant):continue
        with scope(tenant):
            for r in Resource.objects.exclude(google="").filter(Q(google_at__isnull=True)|Q(google_at__lte=now-EVERY)).select_related("tenant"):done+=sync(tenant,r,now)
    return done
