"""2.17.0: the WordPress site of a PC that is often off and not reachable from the Internet (Windows, Un PC solo).

Only the PC calls, every 2 minutes while it is on (the worker): it sends the site what the booking form needs (services,
variants, operators, free times of the next days, the texts of the page) and the site answers with the bookings taken
meanwhile. They become bookings here like the online ones (origin "wordpress"); refused now (the time was taken at the
desk) they become requests at that time. The next call acks them and the site deletes them. Both ways are signed
HMAC-SHA256 with the shared secret over "time.body", within 5 minutes; the site also refuses a time already seen.

2.18.0, the site as the central archive (set by the agency, and on in the plugin too): every change of a booking, a card
(with its fiscal data), its record, its consent history and the receipts' summary goes in an outbox (touch) and is pushed
in order to /archive/push, sealed, until the site acks it; the site keeps the last state of each record, an empty one for
what is gone. A new PC gets it all back from /archive/pull (restore). The PC only ever POSTs JSON to /wp-json/tdbs/v1/sync
and /wp-json/tdbs/v1/archive/..., with X-TDBS-Time and X-TDBS-Signature, each body under 512 KB but the free times of /sync.
"""
import base64,hashlib,hmac,json,re,secrets,time,urllib.error,urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from django.db.models import F,Q
from django.db.models.functions import Greatest
from django.utils import timezone
from .clients import ERASED,key_for,same_person,seal_card
from .crypto import encrypt,decrypt
from .models import Tenant,Service,Option,Location,Resource,Booking,Contact,Treatment,ConsentEvent,Sale,SiteOutbox,Audit,audit
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
    c=conf(tenant);on=bool(post.get("on"));archive=bool(post.get("archive"))
    if on and not c.get("secret"):raise ValueError("Prima genera la chiave e incollala nel plugin.")
    if archive and not on:raise ValueError("Il sito fa da archivio solo con la sincronizzazione attiva.")
    c.update(url=url,days=days,on=on);store(tenant,c);state(tenant,sent="")  # the next call sends the free times again
    was=tenant.wp_archive;Tenant.objects.filter(pk=tenant.pk).update(wp_archive=archive);tenant.wp_archive=archive
    if archive and not was:upload_all(tenant)  # the first time everything goes up
    with scope(tenant):
        if was and not archive:SiteOutbox.objects.all().delete()
        audit(tenant,actor,"wpsync.saved",tenant.pk,site=url,on=on,archive=archive)

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

def aes(secret):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(HKDF(hashes.SHA256(),32,None,b"tdbs-wp-data").derive(secret.encode()))
def seal(secret,data):
    """2.17.1: as tdbs_pc_seal in the plugin: AES-256-GCM, key HKDF-SHA256 of the shared secret, base64(iv.tag.ciphertext)."""
    iv=secrets.token_bytes(12);ct=aes(secret).encrypt(iv,json.dumps(data,ensure_ascii=False,separators=(",",":")).encode(),None)
    return base64.b64encode(iv+ct[-16:]+ct[:-16]).decode()
def unseal(secret,value):
    """2.18.0: as tdbs_pc_open; None for what does not open with this secret."""
    from cryptography.exceptions import InvalidTag
    try:raw=base64.b64decode(str(value),validate=True);return json.loads(aes(secret).decrypt(raw[:12],raw[28:]+raw[12:28],None))
    except (ValueError,InvalidTag):return None

STATUS=dict(Booking._meta.get_field("status").choices)
def row(b,tz):
    """A booking as the private agenda of the site shows it."""
    d=b.data
    return {"start":b.start.astimezone(tz).strftime("%Y-%m-%dT%H:%M"),"end":b.end.astimezone(tz).strftime("%Y-%m-%dT%H:%M"),
        "service":b.service.name+(" · "+b.option.name if b.option else ""),"resource":str(b.resource_id or ""),"operator":b.resource.name if b.resource else "",
        "status":STATUS[b.status],"pending":b.status=="pending","client":f"{d.get('first_name','')} {d.get('last_name','')}".strip(),"phone":d.get("phone",""),"notes":d.get("notes","")}

def agenda(tenant,c):
    """2.17.1: the private agenda of the site, read-only with the PC off: the bookings of the next days from today (the
    cancelled ones out), with client, phone and notes, sealed with the shared secret; the site shows it after its password."""
    tz=ZoneInfo(tenant.timezone);start=datetime.combine(timezone.now().astimezone(tz).date(),datetime.min.time(),tz)
    rows=[row(b,tz) for b in Booking.objects.filter(start__gte=start,start__lt=start+timedelta(days=int(c.get("days",30)))).exclude(status="cancelled").select_related("service","option","resource").order_by("start")]
    return seal(c["secret"],{"generated":timezone.now().isoformat(),"timezone":tenant.timezone,
        "operators":[{"id":str(r.pk),"name":r.name} for r in Resource.objects.filter(active=True).order_by("name")],"bookings":rows})

STAFF=type("SiteAgenda",(),{"pk":""})()  # 2.19.0: who writes in the site's agenda: the desk's rules, no user of this program
def take(tenant,rows):
    """The site's bookings: each one once (request_key wp-<id>), refused now a request at the same time; one that cannot
    be kept at all is acked too, and its client told by email why. Returns the new bookings' ids, the ids to ack, errors.
    2.19.0: source "agenda", written by the staff in the site's agenda: a booking of the desk, origin "Agenda del sito",
    confirmed when the time and the operator are still free, otherwise a request, without emails to the client until then."""
    from .booking import create_booking,queue,BookingError
    from .calendar import local_datetime
    tz=ZoneInfo(tenant.timezone);new=[];ack=[];errors=[]
    for r in rows[:200] if isinstance(rows,list) else []:
        sid=str(r.get("id","")) if isinstance(r,dict) else ""
        if not re.fullmatch(r"[a-f0-9]{32}",sid):continue
        key="wp-"+sid;ack.append(sid);staff=r.get("source")=="agenda"
        how={"actor":STAFF,"origin":"agenda"} if staff else {"origin":"wordpress"}
        with scope(tenant):
            if Booking.objects.filter(request_key=key).exists():continue
            try:start=local_datetime(date.fromisoformat(str(r.get("date"))),datetime.strptime(str(r.get("time")),"%H:%M").time(),tz).isoformat()
            except (ValueError,AttributeError):errors.append("una prenotazione del sito con data non valida");continue
            data={"service":str(r.get("service","")),"option":str(r.get("option") or ""),"resource":"" if r.get("any") else str(r.get("resource","")),"start":start,"party":1,
                **{k:str(r.get(k) or "") for k in ("first_name","last_name","email","phone","notes")},"marketing":r.get("marketing") is True and not staff,"notices":r.get("notices") is not False,"request_key":key,
                **({"force":True} if staff and r.get("free") else {})}  # Altro orario: any time, kept if nothing is in the way
            try:b,_=create_booking(tenant,data,**how)
            except BookingError:
                try:b,_=create_booking(tenant,{**data,"resource":str(r.get("resource",""))},late=True,**how)
                except BookingError as ex:
                    when=f"{r.get('date')} {r.get('time')}";errors.append(f"{when}: {ex}")
                    if "@" in data["email"] and not staff:queue(tenant,{"to":data["email"],"subject":"Richiesta non registrata · "+tenant.name,
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
            body={"ack":old.get("ack",[]),**({"snapshot":snapshot(tenant,int(c.get("days",30))),"agenda":agenda(tenant,c)} if due else {})}
        data=call(c,"/sync",body)
        new,ack,errors=take(tenant,data.get("bookings",[]) if isinstance(data,dict) else [])
        state(tenant,running="",at=timezone.now().isoformat(),new=len(new),ids=new[:20],ack=ack,error="; ".join(errors)[:500],**({"sent":timezone.now().isoformat(),"mark":mark} if due else {}))
        if new:
            with scope(tenant):audit(tenant,None,"wpsync.bookings",tenant.pk,n=len(new))
        if tenant.wp_archive:
            try:push(tenant,c)
            except ValueError as ex:state(tenant,archive={**(tenant.wp_state or {}).get("archive",{}),"error":str(ex)[:500]})
        return len(new)
    except ValueError as ex:
        state(tenant,running="",at=timezone.now().isoformat(),new=0,ids=[],error=str(ex)[:500])
        return 0

def call(c,route,payload):
    """One signed POST JSON to /wp-json/tdbs/v1<route> (the path, not ?rest_route=, so a firewall can allow it by prefix);
    the answer only with the site's signature."""
    body=json.dumps(payload,separators=(",",":")).encode();ts=str(int(time.time()))
    req=urllib.request.Request(c["url"]+"/wp-json/tdbs/v1"+route,data=body,method="POST",headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":"TD-Booking-Suite","X-TDBS-Time":ts,"X-TDBS-Signature":sign(c["secret"],ts,body)})
    try:
        with urllib.request.urlopen(req,timeout=30) as r:raw=r.read(10_000_000)
    except urllib.error.HTTPError as ex:
        try:said=str(json.loads(ex.read(4000)).get("error") or "")
        except (ValueError,AttributeError,OSError):said=""
        raise ValueError({401:"il sito non riconosce la chiave: incollala di nuovo nel plugin",403:"il sito non riconosce la chiave: incollala di nuovo nel plugin"}.get(ex.code) or said.rstrip(".").lower() or
            {404:"nel sito manca il plugin TD Booking Suite in modalità PC, o la chiave non è ancora nel plugin"}.get(ex.code,f"il sito ha risposto con l'errore {ex.code}"))
    except (urllib.error.URLError,OSError) as ex:raise ValueError(f"sito non raggiungibile ({getattr(ex,'reason',ex)})")
    try:env=json.loads(raw);inner=str(env["body"]).encode()
    except (ValueError,KeyError,TypeError):raise ValueError("il sito ha risposto in modo non valido")
    if not verify(c["secret"],env.get("time"),inner,env.get("sig")):raise ValueError("risposta del sito senza la firma giusta: controlla la chiave nel plugin")
    data=json.loads(inner)
    return data if isinstance(data,dict) else {}

# ---------- 2.18.0 the site as the central archive
KINDS={"contact":Contact,"booking":Booking,"treatment":Treatment,"consent":ConsentEvent,"sale":Sale}
RELATED={"booking":("service","location","resource","option","secondary"),"consent":("contact",),"sale":("location",)}
CHUNK=400_000  # each body under 512 KB: under the 1 MB nginx allows by default, no proxy setting to change
QUIET=ContextVar("wps_quiet",default=False)

def touch(tenant_id,kind,refs):
    """Records of a kind changed or gone: in the outbox with numbers after every one given before, only with the archive on.
    The numbers start from the clock, so a PC installed again goes on after the old one's."""
    refs=[str(r) for r in refs if r]
    if not refs or QUIET.get():return
    n=len(refs);now=time.time_ns()//1000
    if not Tenant.objects.filter(pk=tenant_id,wp_archive=True).update(wp_seq=Greatest(F("wp_seq")+n,now+n)):return
    top=Tenant.objects.filter(pk=tenant_id).values_list("wp_seq",flat=True)[0]
    for i in range(0,n,500):
        part=refs[i:i+500]
        SiteOutbox.objects.filter(kind=kind,ref__in=part).delete()
        SiteOutbox.objects.bulk_create([SiteOutbox(tenant_id=tenant_id,kind=kind,ref=r,seq=top-n+1+i+j) for j,r in enumerate(part)])
def changed(sender,instance):
    """The models' save and delete (models._archived). An erased or merged card takes its consent history along."""
    kind=next(k for k,m in KINDS.items() if m is sender)
    touch(instance.tenant_id,kind,[instance.pk])
    if kind=="contact" and instance.email_index.startswith(ERASED) and not QUIET.get():
        touch(instance.tenant_id,"consent",ConsentEvent.objects.filter(contact_id=instance.pk).values_list("pk",flat=True))

def plain(v,tz):return v.astimezone(tz).isoformat() if isinstance(v,datetime) else v if v is None or isinstance(v,(bool,int,float,str,list,dict)) else str(v)
SKIP={"tenant","pii","consent_proof","request_fingerprint","email_index","phone_index","name_index","tag_index","birthday_index"}
def pack(kind,obj,tz):
    """What the site keeps of a record, its fields as they are; None for what it must no longer have: a record gone, an
    erased or merged card, the consent history of an erased card, a test of the register."""
    if obj is None:return None
    out={f.attname:plain(getattr(obj,f.attname),tz) for f in obj._meta.concrete_fields if f.name not in SKIP}
    if kind=="contact":
        if obj.email_index.startswith(ERASED):return None
        out["data"]=obj.data
        if obj.consent_proof:out["proof"]=decrypt(obj.tenant,obj.consent_proof,"consent:"+str(obj.pk))
    elif kind=="consent":
        card=obj.contact
        if card.email_index.startswith(ERASED) and not card.merged_into_id:return None
        out["contact_id"]=str(card.merged_into_id or card.pk)  # a merged card's history goes with the card it went into
    elif kind=="sale":
        if obj.kind not in ("sale","void"):return None
        out["location"]=obj.location.name  # the summary: lines, payments and codice fiscale stay on the PC
    elif kind=="treatment":out["data"]=obj.data
    else:
        out["data"]=obj.data;out["names"]={k:getattr(obj,k).name if getattr(obj,k+"_id") else "" for k in RELATED["booking"]}
        if obj.status!="cancelled":out["view"]=row(obj,tz)  # what [tdbs_agenda] shows; never the client record
    return out

def push(tenant,c,calls=20):
    """The outbox to /archive/push in order of number, a chunk per call; what the site acks leaves the outbox (a record
    changed meanwhile has a new number and goes again). Returns how many acked."""
    tz=ZoneInfo(tenant.timezone);sent=0
    for _ in range(calls):
        with scope(tenant):
            rows=list(SiteOutbox.objects.order_by("seq")[:500])
            if not rows:break
            objs={k:{str(pk):o for pk,o in KINDS[k].objects.select_related("tenant",*RELATED.get(k,())).in_bulk([r.ref for r in rows if r.kind==k]).items()} for k in {r.kind for r in rows}}
            ops=[];size=0
            for r in rows:
                rec=pack(r.kind,objs[r.kind].get(r.ref),tz)
                op={"kind":r.kind,"ref":r.ref,"seq":r.seq,"day":rec["start"][:10] if rec and r.kind=="booking" else "","data":seal(c["secret"],rec) if rec else ""}
                size+=len(op["data"])+200
                if ops and size>CHUNK:break
                ops.append(op)
        data=call(c,"/archive/push",{"ops":ops})
        acked=[x for x in data.get("acked",[]) if isinstance(x,int)]
        with scope(tenant):SiteOutbox.objects.filter(seq__in=acked).delete()
        sent+=len(acked)
        state(tenant,archive={"at":timezone.now().isoformat(),"records":int(data.get("records") or 0),"size":int(data.get("size") or 0),"error":""})
        if len(acked)<len(ops):break  # the site took only part: the rest at the next sync
    return sent

def upload_all(tenant):
    """Carica tutto sul sito: every record in the outbox again; the next syncs send them. Returns how many."""
    with scope(tenant):
        SiteOutbox.objects.all().delete()
        for kind,rows in (("contact",Contact.objects.exclude(email_index__startswith=ERASED)),("booking",Booking.objects.all()),("treatment",Treatment.objects.all()),
                ("consent",ConsentEvent.objects.exclude(contact__email_index__startswith=ERASED,contact__merged_into__isnull=True)),("sale",Sale.objects.filter(kind__in=("sale","void")))):
            touch(tenant.pk,kind,list(rows.values_list("pk",flat=True)))
        return SiteOutbox.objects.count()

def pull(c,kind):
    """Every record of a kind in the site's archive, page after page."""
    after="";out=[]
    while True:
        data=call(c,"/archive/pull",{"kind":kind,"after":after})
        out+=[x for x in (unseal(c["secret"],r.get("data","")) for r in data.get("records",[]) if isinstance(r,dict)) if isinstance(x,dict)]
        nxt=str(data.get("next") or "")
        if not nxt or nxt==after:return out
        after=nxt

def fill(obj,rec,skip=()):
    for f in obj._meta.concrete_fields:
        if f.attname in rec and not f.is_relation and f.name not in skip:setattr(obj,f.attname,f.to_python(rec[f.attname]))
    return obj
@contextmanager
def kept_times():
    """The dates of the archive, not today's, on the records made again (consent history cannot be changed after)."""
    fields=[m._meta.get_field("at" if m is ConsentEvent else "created") for m in KINDS.values()]
    for f in fields:f.auto_now_add=False
    try:yield
    finally:
        for f in fields:f.auto_now_add=True  # shortcut: process-wide while it runs, fine on the PC; a queue job if it ever runs on a busy server

def restore(tenant,actor,confirm=False):
    """Ripristina dal sito: the cards, bookings, client records and consent history of the archive made again here with this
    PC's business key, the catalogue of this PC matched by name (sede, servizio, variante, operatore). What is already
    here is skipped, so a second time adds nothing; into a business with clients or bookings only with confirm.
    Returns the counts and the names missing from the catalogue."""
    c=conf(tenant)
    if not (c.get("url") and c.get("secret")):raise ValueError("Prima scrivi l'indirizzo del sito e incolla la chiave nel plugin.")
    with scope(tenant):
        if not confirm and (Contact.objects.exists() or Booking.objects.exists()):
            raise ValueError("Ci sono già clienti o prenotazioni: spunta la conferma per aggiungere quelli del sito.")
    got={k:pull(c,k) for k in ("contact","booking","treatment","consent")}
    out={"contacts":0,"bookings":0,"treatments":0,"consents":0,"skipped":0,"lost":0,"missing":set()}
    from django.db import IntegrityError
    token=QUIET.set(True)  # already on the site: not sent back
    try:
        with scope(tenant),kept_times():
            services={}
            for s in Service.objects.select_related("location"):services[(s.location.name,s.name)]=s;services.setdefault(s.name,s)
            people={r.name:r for r in Resource.objects.all()};options={(o.service_id,o.name):o for o in Option.objects.all()}
            cards={}
            for rec in got["contact"]:
                pk=str(rec.get("id"));d=rec.get("data") or {}
                if Contact.objects.filter(pk=pk).exists():cards[pk]=pk;out["skipped"]+=1;continue
                key=key_for(tenant,d) if d.get("email") or d.get("phone") else "restored:"+pk
                hit=Contact.objects.filter(email_index=key).first()
                if hit and not d.get("email") and not same_person(hit,d):key=key_for(tenant,d,shared=True);hit=Contact.objects.filter(email_index=key).first()
                if hit:cards[pk]=str(hit.pk);out["skipped"]+=1;continue  # the same person already here: the bookings go on that card
                x=fill(Contact(tenant=tenant),rec);x.email_index=key
                x.consent_proof=encrypt(tenant,rec["proof"],"consent:"+pk) if rec.get("proof") else ""
                seal_card(tenant,x,d);x.save(force_insert=True);cards[pk]=pk;out["contacts"]+=1
            for rec in got["booking"]:
                pk=str(rec.get("id"));n=rec.get("names") or {}
                if Booking.objects.filter(Q(pk=pk)|Q(request_key=rec.get("request_key",""))).exists():out["skipped"]+=1;continue
                s=services.get((n.get("location"),n.get("service"))) or services.get(n.get("service"))
                if not s:out["missing"].add("servizio "+str(n.get("service")));out["lost"]+=1;continue
                # shortcut: event dates, tables and other cabins are not matched, the booking comes back without them
                b=fill(Booking(tenant=tenant),rec,("more","tables"));b.service=s;b.location=s.location;b.option=options.get((s.pk,n.get("option")))
                b.resource=people.get(n.get("resource"));b.secondary=people.get(n.get("secondary"));b.contact_id=cards.get(str(rec.get("contact_id")))
                for k,label in (("resource","operatore"),("option","variante"),("secondary","risorsa")):
                    if n.get(k) and not getattr(b,k):out["missing"].add(f"{label} {n[k]}")
                b.pii=encrypt(tenant,rec.get("data") or {},"booking:"+pk);b.request_fingerprint="";b.save(force_insert=True);out["bookings"]+=1
            for kind,model in (("treatment",Treatment),("consent",ConsentEvent)):
                for rec in got[kind]:
                    pk=str(rec.get("id"));card=cards.get(str(rec.get("contact_id")))
                    if not card or model.objects.filter(pk=pk).exists():out["skipped"]+=1;continue
                    x=fill(model(tenant=tenant),rec);x.contact_id=card
                    if kind=="treatment":
                        x.booking_id=rec.get("booking_id") if rec.get("booking_id") and Booking.objects.filter(pk=rec["booking_id"]).exists() else None
                        x.pii=encrypt(tenant,rec.get("data") or {},"treatment:"+pk)
                    x.save(force_insert=True);out[kind+"s"]+=1
            audit(tenant,actor,"archive.restored",tenant.pk,**{k:v for k,v in out.items() if k!="missing"})
    except IntegrityError:raise ValueError("questi dati sono già in un'altra attività di questo programma: il ripristino è per un PC nuovo")  # the ids are kept
    finally:QUIET.reset(token)
    out["missing"]=sorted(out["missing"])
    return out

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
    a=s.get("archive") or {}
    with scope(tenant):queue=SiteOutbox.objects.count()
    return {"url":c.get("url",""),"days":c.get("days",30),"on":c.get("on",False),"secret":bool(c.get("secret")),
        "at":datetime.fromisoformat(s["at"]) if s.get("at") else None,"new":s.get("new",0),"error":s.get("error",""),
        "archive":tenant.wp_archive,"queue":queue,"archive_at":datetime.fromisoformat(a["at"]) if a.get("at") else None,
        "records":a.get("records",0),"size":round(a.get("size",0)/1048576,1),"archive_error":a.get("error","")}
