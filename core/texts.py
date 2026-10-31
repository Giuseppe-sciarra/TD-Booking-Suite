"""2.14.0: the reminders also as a text message: an SMS through Twilio or Skebby, or a WhatsApp Cloud API "utility"
template with the SMS as fallback when WhatsApp refuses it. The agency sets the channel (SMS e WhatsApp page), the keys
sealed with the business's key; never between midnight and 7 of the business, within a daily budget as the emails.
Only the reminders go this way, nothing promotional: the campaigns stay emails tied to the marketing consent."""
import base64,json,re
from datetime import datetime,timedelta,time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from zoneinfo import ZoneInfo
from django.utils import timezone

PROVIDERS=[("","Nessuno"),("twilio","Twilio"),("skebby","Skebby"),("aruba","Aruba SMS")]
# Skebby and Aruba share one API: the address and the message type change (GP = Classic+, N = high quality, both with receipt)
SKEBBY={"skebby":("https://api.skebby.it","GP"),"aruba":("https://smspanel.aruba.it","N")}
TEXT="{attivita}: ti ricordiamo {servizio}, {quando}. Per gestire o annullare: {link}"
QUIET=7  # nothing before 7 in the morning, as Plateform does

class TextError(Exception):
    def __init__(self,code,temporary=False):super().__init__(code);self.code=code;self.temporary=temporary

def config(tenant):
    from .crypto import decrypt
    return decrypt(tenant,tenant.texts,"texts") if tenant.texts else {}

def channel(conf):return "whatsapp" if conf.get("wa_token") else "sms" if conf.get("provider") else ""

def clean(post,saved):
    """The settings as the agency typed them; an empty key keeps the one saved."""
    conf={k:str(post.get(k,"")).strip() for k in ("provider","user","sender","wa_phone","wa_template","wa_lang")}
    for k in ("secret","wa_token"):conf[k]=str(post.get(k,"")).strip() or saved.get(k,"")
    if post.get("sms_off")=="on":conf.update(provider="",user="",secret="",sender="")
    if post.get("wa_off")=="on":conf.update(wa_phone="",wa_token="",wa_template="")
    if conf["provider"] not in dict(PROVIDERS):raise ValueError("Scegli il servizio SMS.")
    if conf["provider"]:
        if not (conf["user"] and conf["secret"] and conf["sender"]):raise ValueError("Per gli SMS servono utente, chiave e mittente.")
        # they go into an address or a header: only the characters the providers use
        if conf["provider"]=="twilio" and not re.fullmatch(r"AC[0-9a-f]{32}",conf["user"]):raise ValueError("Twilio: l'Account SID inizia con AC e ha 34 caratteri.")
        if not re.fullmatch(r"[\w.\-]{1,80}",conf["user"]) or not re.fullmatch(r"[\w.\-]{1,200}",conf["secret"]):raise ValueError("Utente o chiave SMS non validi.")
        if not re.fullmatch(r"\+?[0-9]{6,15}|[A-Za-z0-9 ]{1,11}",conf["sender"]):raise ValueError("Mittente: un numero o un nome di al massimo 11 lettere e cifre.")
    if conf["wa_token"] or conf["wa_phone"]:
        if not (conf["wa_token"] and conf["wa_phone"] and conf["wa_template"]):raise ValueError("Per WhatsApp servono ID del numero, token e nome del modello.")
        if not re.fullmatch(r"\d{5,20}",conf["wa_phone"]) or not re.fullmatch(r"[a-z0-9_]{1,512}",conf["wa_template"]) or not re.fullmatch(r"[A-Za-z0-9_\-.]{20,1000}",conf["wa_token"]):
            raise ValueError("WhatsApp: ID del numero, token o nome del modello non validi.")
        conf["wa_lang"]=conf["wa_lang"] if re.fullmatch(r"[a-z]{2}(_[A-Z]{2})?",conf["wa_lang"]) else "it"
    return conf if channel(conf) else {}

def remind(tenant,booking,kind):
    """The reminder as a text, to the phone of the booking, when the agency set a channel and the client did not say
    no to the messages about his bookings. Queued as the emails, sent by the worker. 2.14.0: the channel the client
    chose (on the booking form, or on his card) when the business has it; "email" = no text. The words are the
    business's (Notifiche); for WhatsApp the values go in the order of the {fields} of the template text."""
    from .booking import queue,normalize_phone
    from .client_mails import values,fill
    if kind not in ("reminder","reminder2"):return None
    conf=config(tenant);ch=channel(conf)
    if not ch or booking.snapshot.get("notices") is False or booking.contact_id and not booking.contact.notices:return None
    pref=booking.snapshot.get("channel") or (booking.contact.channel if booking.contact_id else "")
    if pref=="email":return None
    if pref=="sms" and conf.get("provider") or pref=="whatsapp" and conf.get("wa_token"):ch=pref
    data=booking.data;phone=normalize_phone(data.get("phone",""))
    if not phone:return None
    v=values(tenant,booking,data);own=(tenant.client_mails or {}).get("texts") or {}
    params=[v.get(k) or "-" for k in re.findall(r"\{(\w+)\}",own["wa"])] if own.get("wa") else [v["nome"] or "-",v["servizio"],v["quando"],v["attivita"]]
    d=queue(tenant,{"to":phone,"body":fill(own.get("sms") or TEXT,v),"params":params},booking.contact)
    d.channel=ch;d.booking=booking;d.save(update_fields=["channel","booking"])
    return d

def clean_texts(post):
    """2.14.0: the SMS and the WhatsApp template text as the business wrote them (Notifiche); empty = standard."""
    from .client_mails import FIELDS
    out={}
    for k,most in (("sms",640),("wa",1024)):
        text=str(post.get("text_"+k,"")).replace("\r\n","\n").strip()
        if len(text)>most or any(ord(c)<32 and c!="\n" for c in text):raise ValueError(f"{'SMS' if k=='sms' else 'WhatsApp'}: al massimo {most} caratteri, senza caratteri speciali.")
        if bad:=set(re.findall(r"\{(\w+)\}",text))-{x for x,_ in FIELDS}:raise ValueError("Campo sconosciuto nel testo: {"+sorted(bad)[0]+"}.")
        if text and text!=TEXT:out[k]=text
    return out

def later(tenant,now=None):
    """The moment the quiet night ends, while it lasts; otherwise None."""
    local=(now or timezone.now()).astimezone(ZoneInfo(tenant.timezone))
    return datetime.combine(local.date(),time(QUIET),local.tzinfo) if local.hour<QUIET else None

def spent(tenant):
    from .models import Delivery
    return Delivery.objects.filter(channel__in=("sms","whatsapp"),status__in=["accepted","sending","unknown"],claimed_at__gte=timezone.now()-timedelta(days=1)).count()

def post(url,body,headers,form=False):
    """One call to the provider (a GET without body); its JSON answer, {} when it is not JSON."""
    data=None if body is None else urlencode(body).encode() if form else json.dumps(body).encode()
    request=Request(url,data=data,headers={**({"Content-Type":"application/x-www-form-urlencoded" if form else "application/json"} if data else {}),**headers})
    try:
        with urlopen(request,timeout=15) as r:raw=r.read(65536)
    except HTTPError as ex:raise TextError(f"http_{ex.code}",ex.code==429 or ex.code>=500)
    except URLError:raise TextError("unreachable",True)  # not connected: nothing left yet (a read timeout stays uncertain)
    try:return json.loads(raw) if raw else {}
    except ValueError:return {}

def sms(conf,p):
    if conf["provider"]=="twilio":
        auth=base64.b64encode(f"{conf['user']}:{conf['secret']}".encode()).decode()
        return post(f"https://api.twilio.com/2010-04-01/Accounts/{conf['user']}/Messages.json",{"To":p["to"],"From":conf["sender"],"Body":p["body"]},{"Authorization":"Basic "+auth},form=True).get("sid","")
    base,kind=SKEBBY[conf["provider"]]
    return post(base+"/API/v1.0/REST/sms",{"message_type":kind,"message":p["body"],"recipient":[p["to"]],"sender":conf["sender"],"returnCredits":False},
            {"user_key":conf["user"],"Access_token":conf["secret"]}).get("order_id","")

def whatsapp(conf,p):
    """The approved template with four values in its text: {{1}} name, {{2}} service, {{3}} day and time, {{4}} business."""
    return ((post(f"https://graph.facebook.com/v26.0/{conf['wa_phone']}/messages",{"messaging_product":"whatsapp","to":p["to"].lstrip("+"),"type":"template",
        "template":{"name":conf["wa_template"],"language":{"code":conf.get("wa_lang") or "it"},"components":[{"type":"body","parameters":[{"type":"text","text":x} for x in p["params"]]}]}},
        {"Authorization":"Bearer "+conf["wa_token"]}).get("messages") or [{}])[0]).get("id","")

def deliver(tenant,ch,p):
    """Sends one message; the channel it went by and the provider's id of it. WhatsApp refused by the API (number or
    template) falls back to SMS when set; a number without WhatsApp that Meta accepts and then does not deliver is not
    sent again by SMS, as the client chose WhatsApp."""
    conf=config(tenant)
    if ch=="whatsapp" and conf.get("wa_token"):
        try:return "whatsapp",whatsapp(conf,p)[:80]
        except TextError as ex:
            if ex.temporary or not conf.get("provider"):raise
    if not conf.get("provider"):raise TextError("not_configured")
    return "sms",str(sms(conf,p))[:80]

# the provider's words for a text that will not arrive (Twilio, then Skebby and Aruba)
FAILED={"undelivered","failed","ERROR","TIMEOUT","TOOM4USER","TOOM4NUM","UNKNRCPT","UNKNPFX","INVALIDDST","BLACKLISTED","KO","INVALIDCONTENTS"}
def receipt(conf,ref):
    """delivered, failed or "" (not yet) for one SMS, asked to its provider (no public address needed)."""
    if not re.fullmatch(r"[\w.\-]{1,80}",ref):return "failed"
    if conf["provider"]=="twilio":
        auth=base64.b64encode(f"{conf['user']}:{conf['secret']}".encode()).decode()
        state=post(f"https://api.twilio.com/2010-04-01/Accounts/{conf['user']}/Messages/{ref}.json",None,{"Authorization":"Basic "+auth}).get("status","")
    else:
        state=((post(SKEBBY[conf["provider"]][0]+f"/API/v1.0/REST/sms/{ref}",None,{"user_key":conf["user"],"Access_token":conf["secret"]}).get("recipients") or [{}])[0]).get("status","")
    return "delivered" if state in ("delivered","DLVRD") else "failed" if state in FAILED else ""

def receipts(now=None):
    """2.14.0: what became of the SMS of the last two days (the worker asks every 5 minutes): on the booking."""
    from .models import Tenant,Delivery
    from .middleware import operational
    from .tenancy import scope
    now=now or timezone.now();done=0
    for tenant in Tenant.objects.exclude(texts="").iterator():
        if not operational(tenant):continue
        with scope(tenant):
            conf=config(tenant)
            if not conf.get("provider"):continue
            for d in Delivery.objects.filter(channel="sms",status="accepted",receipt="",sent_at__gte=now-timedelta(days=2)).exclude(ref="")[:200]:
                try:state=receipt(conf,d.ref)
                except TextError:break  # the provider is down: next round
                if state:d.receipt=state;d.save(update_fields=["receipt"]);done+=1
    return done
