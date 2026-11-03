"""2.17.0: the WordPress site of a PC that is often off and not reachable from the Internet (Windows, Un PC solo).

Only the PC calls, every 2 minutes while it is on (the worker): it sends the site what the booking form needs (services,
variants, operators, free times of the next days, the texts of the page) and the site answers with the bookings taken
meanwhile. They become bookings here like the online ones (origin "wordpress"); refused now (the time was taken at the
desk) they become requests at that time. The next call acks them and the site deletes them. Both ways are signed
HMAC-SHA256 with the shared secret over "time.body", within 5 minutes; the site also refuses a time already seen.
"""
import base64,hashlib,hmac,json,re,secrets,time,urllib.error,urllib.request
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from .crypto import encrypt,decrypt
from .models import Tenant,Service,Option,Location,Resource,Booking,Audit,audit
from .tenancy import scope

EVERY=120
WINDOW=300
MAX_DAYS=90
FULL=timedelta(minutes=15)  # the free times are slow to count: again only after a change in the register, or after this
RUNNING=timedelta(minutes=10)  # a sync that never ended (the PC switched off meanwhile) is no longer "in corso"

def sign(secret,ts,body):return hmac.new(secret.encode(),str(ts).encode()+b"."+body,hashlib.sha256).hexdigest()
def verify(secret,ts,body,sig,now=None):
    try:age=abs((now or time.time())-int(ts))
    except (TypeError,ValueError):return False
    return age<=WINDOW and hmac.compare_digest(sign(secret,ts,body),str(sig or ""))

def conf(tenant):return decrypt(tenant,tenant.wp_sync,"wp-sync") if tenant.wp_sync else {}
def store(tenant,c):
    tenant.wp_sync=encrypt(tenant,c,"wp-sync");tenant.save(update_fields=["wp_sync"])
def state(tenant,**kw):
    tenant.wp_state={**(tenant.wp_state or {}),**kw};Tenant.objects.filter(pk=tenant.pk).update(wp_state=tenant.wp_state)

def new_secret(tenant,actor):
    c=conf(tenant);c["secret"]=secrets.token_urlsafe(32);store(tenant,c)
    with scope(tenant):audit(tenant,actor,"wpsync.secret",tenant.pk)
    return c["secret"]
def save(tenant,post,actor):
    """The card in Configura l'attività: the site's address, how many days ahead, on or off."""
    from .wpconnect import valid_origin
    url=valid_origin(str(post.get("url","")).strip().rstrip("/"))
    if not url:raise ValueError("Indirizzo del sito non valido: scrivilo come https://www.sito.it, senza percorsi.")
    try:days=int(post.get("days") or 30)
    except ValueError:days=0
    if not 1<=days<=MAX_DAYS:raise ValueError(f"I giorni vanno da 1 a {MAX_DAYS}.")
    c=conf(tenant);on=bool(post.get("on"))
    if on and not c.get("secret"):raise ValueError("Prima genera la chiave e incollala nel plugin.")
    c.update(url=url,days=days,on=on);store(tenant,c);state(tenant,sent="")  # the next call sends the free times again
    with scope(tenant):audit(tenant,actor,"wpsync.saved",tenant.pk,site=url,on=on)

def snapshot(tenant,days):
    """What the site shows with the PC off: the appointment services of the public page, without private links or
    questions the site does not ask, each operator's free times per variant as "HH:MM-HH:MM" of each day."""
    from .booking import available,linked
    from .client_mails import current
    tz=ZoneInfo(tenant.timezone);today=timezone.now().astimezone(tz).date();out=[]
    for s in Service.objects.filter(active=True,location__active=True,secret="",kind="resource").select_related("location").order_by("name"):
        if any(f.get("required") for f in s.fields):continue  # shortcut: the service's own questions are not on the site, a service that needs one stays off it
        options=list(Option.objects.filter(service=s,active=True));people=list(linked(s))
        slots={}
        for o in options or [None]:
            for r in people:
                for n in range(days):
                    d=today+timedelta(days=n)
                    if t:=[f"{datetime.fromisoformat(x['start']).strftime('%H:%M')}-{datetime.fromisoformat(x['end']).strftime('%H:%M')}" for x in available(tenant,s.pk,d,o and o.pk,r.pk)]:
                        slots.setdefault(str(o.pk) if o else "",{}).setdefault(str(r.pk),{})[d.isoformat()]=t
        out.append({"id":str(s.pk),"name":s.name,"description":s.description,"location":s.location.name,"picks":s.client_picks,
            "options":[{"id":str(o.pk),"name":o.name,"duration":o.duration} for o in options],"resources":[{"id":str(r.pk),"name":r.name} for r in people],"slots":slots})
    phone=next((l.phone for l in Location.objects.filter(active=True).exclude(phone="").order_by("name")),"")
    reminder=current(tenant)["reminder"]["on"]
    return {"v":1,"name":tenant.name,"timezone":tenant.timezone,"generated":timezone.now().isoformat(),"days":days,"phone":phone,"services":out,
        "texts":{"privacy":tenant.privacy_text,"consent":tenant.consent_text,
            "notices":"Mandami per email la conferma, il promemoria e gli avvisi su questa prenotazione" if reminder else "Mandami per email la conferma e gli avvisi su questa prenotazione"}}

def seal(secret,data):
    """2.17.1: as tdbs_pc_seal in the plugin: AES-256-GCM, key HKDF-SHA256 of the shared secret, base64(iv.tag.ciphertext)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv=secrets.token_bytes(12);ct=AESGCM(HKDF(hashes.SHA256(),32,None,b"tdbs-wp-data").derive(secret.encode())).encrypt(iv,json.dumps(data,ensure_ascii=False,separators=(",",":")).encode(),None)
    return base64.b64encode(iv+ct[-16:]+ct[:-16]).decode()

def agenda(tenant,c):
    """2.17.1: the private agenda of the site, read-only with the PC off: the bookings of the next days from today (the
    cancelled ones out), with client, phone and notes, sealed with the shared secret; the site shows it after its password."""
    tz=ZoneInfo(tenant.timezone);start=datetime.combine(timezone.now().astimezone(tz).date(),datetime.min.time(),tz)
    status=dict(Booking._meta.get_field("status").choices);rows=[]
    for b in Booking.objects.filter(start__gte=start,start__lt=start+timedelta(days=int(c.get("days",30)))).exclude(status="cancelled").select_related("service","option","resource").order_by("start"):
        d=b.data;rows.append({"start":b.start.astimezone(tz).strftime("%Y-%m-%dT%H:%M"),"end":b.end.astimezone(tz).strftime("%Y-%m-%dT%H:%M"),
            "service":b.service.name+(" · "+b.option.name if b.option else ""),"resource":str(b.resource_id or ""),"operator":b.resource.name if b.resource else "",
            "status":status[b.status],"pending":b.status=="pending","client":f"{d.get('first_name','')} {d.get('last_name','')}".strip(),"phone":d.get("phone",""),"notes":d.get("notes","")})
    return seal(c["secret"],{"generated":timezone.now().isoformat(),"timezone":tenant.timezone,
        "operators":[{"id":str(r.pk),"name":r.name} for r in Resource.objects.filter(active=True).order_by("name")],"bookings":rows})

def take(tenant,rows):
    """The site's bookings: each one once (request_key wp-<id>), refused now a request at the same time; one that cannot
    be kept at all is acked too, and its client told by email why. Returns the new bookings' ids, the ids to ack, errors."""
    from .booking import create_booking,queue,BookingError
    from .calendar import local_datetime
    tz=ZoneInfo(tenant.timezone);new=[];ack=[];errors=[]
    for r in rows[:200] if isinstance(rows,list) else []:
        sid=str(r.get("id","")) if isinstance(r,dict) else ""
        if not re.fullmatch(r"[a-f0-9]{32}",sid):continue
        key="wp-"+sid;ack.append(sid)
        with scope(tenant):
            if Booking.objects.filter(request_key=key).exists():continue
            try:start=local_datetime(date.fromisoformat(str(r.get("date"))),datetime.strptime(str(r.get("time")),"%H:%M").time(),tz).isoformat()
            except (ValueError,AttributeError):errors.append("una prenotazione del sito con data non valida");continue
            data={"service":str(r.get("service","")),"option":str(r.get("option") or ""),"resource":"" if r.get("any") else str(r.get("resource","")),"start":start,"party":1,
                **{k:str(r.get(k) or "") for k in ("first_name","last_name","email","phone","notes")},"marketing":r.get("marketing") is True,"notices":r.get("notices") is not False,"request_key":key}
            try:b,_=create_booking(tenant,data,origin="wordpress")
            except BookingError:
                try:b,_=create_booking(tenant,{**data,"resource":str(r.get("resource",""))},origin="wordpress",late=True)
                except BookingError as ex:
                    when=f"{r.get('date')} {r.get('time')}";errors.append(f"{when}: {ex}")
                    if "@" in data["email"]:queue(tenant,{"to":data["email"],"subject":"Richiesta non registrata · "+tenant.name,
                        "body":f"La tua richiesta di prenotazione per il {date.fromisoformat(str(r['date'])).strftime('%d/%m/%Y')} alle {r.get('time')} non è stata registrata: {ex}\nContatta {tenant.name} per prenotare."})
                    continue
            new.append(str(b.pk))
    return new,ack,errors

def sync(tenant,full=False):
    """One call to the site; how it went stays on the tenant for the card and the banner. Returns the new bookings.
    The free times go again when something was written in the register (bookings, changes), every FULL, or full."""
    c=conf(tenant)
    if not (c.get("on") and c.get("url") and c.get("secret")):return 0
    old=tenant.wp_state or {}
    state(tenant,running=timezone.now().isoformat())
    try:
        with scope(tenant):
            mark=str(Audit.objects.exclude(action__startswith="wpsync.").order_by("-created").values_list("created","pk").first() or "")
            due=full or mark!=old.get("mark") or not old.get("sent") or timezone.now()-datetime.fromisoformat(old["sent"])>FULL
            body=json.dumps({"ack":old.get("ack",[]),**({"snapshot":snapshot(tenant,int(c.get("days",30))),"agenda":agenda(tenant,c)} if due else {})},separators=(",",":")).encode()
        ts=str(int(time.time()))
        req=urllib.request.Request(c["url"]+"/wp-json/tdbs/v1/sync",data=body,method="POST",headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":"TD-Booking-Suite","X-TDBS-Time":ts,"X-TDBS-Signature":sign(c["secret"],ts,body)})
        try:
            with urllib.request.urlopen(req,timeout=30) as r:raw=r.read(5_000_000)
        except urllib.error.HTTPError as ex:raise ValueError({401:"il sito non riconosce la chiave: incollala di nuovo nel plugin",403:"il sito non riconosce la chiave: incollala di nuovo nel plugin",404:"nel sito manca il plugin TD Booking Suite 2.17 in modalità PC"}.get(ex.code,f"il sito ha risposto con l'errore {ex.code}"))
        except (urllib.error.URLError,OSError) as ex:raise ValueError(f"sito non raggiungibile ({getattr(ex,'reason',ex)})")
        try:env=json.loads(raw);inner=str(env["body"]).encode()
        except (ValueError,KeyError,TypeError):raise ValueError("il sito ha risposto in modo non valido")
        if not verify(c["secret"],env.get("time"),inner,env.get("sig")):raise ValueError("risposta del sito senza la firma giusta: controlla la chiave nel plugin")
        data=json.loads(inner)
        new,ack,errors=take(tenant,data.get("bookings",[]) if isinstance(data,dict) else [])
        state(tenant,running="",at=timezone.now().isoformat(),new=len(new),ids=new[:20],ack=ack,error="; ".join(errors)[:500],**({"sent":timezone.now().isoformat(),"mark":mark} if due else {}))
        if new:
            with scope(tenant):audit(tenant,None,"wpsync.bookings",tenant.pk,n=len(new))
        return len(new)
    except ValueError as ex:
        state(tenant,running="",at=timezone.now().isoformat(),new=0,ids=[],error=str(ex)[:500])
        return 0

def run():
    """The worker, every EVERY seconds: each business with the site linked this way."""
    from .middleware import operational
    done=0
    for t in Tenant.objects.exclude(wp_sync="").iterator():
        if operational(t):done+=sync(t)
    return done

def banner(tenant):
    """2.17.0: above the agenda and the Dashboard: in progress, done with how many new bookings, or the error."""
    if not tenant or not tenant.wp_sync:return None
    s=tenant.wp_state or {};when=lambda k:datetime.fromisoformat(s[k]) if s.get(k) else None
    running=when("running")
    if running and timezone.now()-running<RUNNING:return {"running":True}
    if not s.get("at"):return None
    ids=s.get("ids") or []
    return {"at":when("at"),"new":s.get("new",0),"error":s.get("error",""),"one":ids[0] if len(ids)==1 else ""}

def panel(tenant):
    """What the card shows: never the secret, only whether there is one."""
    c=conf(tenant);s=tenant.wp_state or {}
    return {"url":c.get("url",""),"days":c.get("days",30),"on":c.get("on",False),"secret":bool(c.get("secret")),
        "at":datetime.fromisoformat(s["at"]) if s.get("at") else None,"new":s.get("new",0),"error":s.get("error","")}
