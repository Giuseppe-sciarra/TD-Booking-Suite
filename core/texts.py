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
    no to the messages about his bookings. Queued as the emails, sent by the worker."""
    from .booking import queue,normalize_phone
    from .client_mails import values,fill
    if kind not in ("reminder","reminder2"):return None
    conf=config(tenant);ch=channel(conf)
    if not ch or booking.snapshot.get("notices") is False or booking.contact_id and not booking.contact.notices:return None
    data=booking.data;phone=normalize_phone(data.get("phone",""))
    if not phone:return None
    v=values(tenant,booking,data)
    d=queue(tenant,{"to":phone,"body":fill(TEXT,v),"params":[v["nome"] or "-",v["servizio"],v["quando"],v["attivita"]]},booking.contact)
    d.channel=ch;d.save(update_fields=["channel"])
    return d

def later(tenant,now=None):
    """The moment the quiet night ends, while it lasts; otherwise None."""
    local=(now or timezone.now()).astimezone(ZoneInfo(tenant.timezone))
    return datetime.combine(local.date(),time(QUIET),local.tzinfo) if local.hour<QUIET else None

def spent(tenant):
    from .models import Delivery
    return Delivery.objects.filter(channel__in=("sms","whatsapp"),status__in=["accepted","sending","unknown"],claimed_at__gte=timezone.now()-timedelta(days=1)).count()

def post(url,body,headers,form=False):
    data=urlencode(body).encode() if form else json.dumps(body).encode()
    request=Request(url,data=data,headers={"Content-Type":"application/x-www-form-urlencoded" if form else "application/json",**headers})
    try:
        with urlopen(request,timeout=15) as r:r.read(65536)
    except HTTPError as ex:raise TextError(f"http_{ex.code}",ex.code==429 or ex.code>=500)
    except URLError:raise TextError("unreachable",True)  # not connected: nothing left yet (a read timeout stays uncertain)

def sms(conf,p):
    if conf["provider"]=="twilio":
        auth=base64.b64encode(f"{conf['user']}:{conf['secret']}".encode()).decode()
        post(f"https://api.twilio.com/2010-04-01/Accounts/{conf['user']}/Messages.json",{"To":p["to"],"From":conf["sender"],"Body":p["body"]},{"Authorization":"Basic "+auth},form=True)
    else:
        base,kind=SKEBBY[conf["provider"]]
        post(base+"/API/v1.0/REST/sms",{"message_type":kind,"message":p["body"],"recipient":[p["to"]],"sender":conf["sender"],"returnCredits":False},
            {"user_key":conf["user"],"Access_token":conf["secret"]})

def whatsapp(conf,p):
    """The approved template with four values in its text: {{1}} name, {{2}} service, {{3}} day and time, {{4}} business."""
    post(f"https://graph.facebook.com/v26.0/{conf['wa_phone']}/messages",{"messaging_product":"whatsapp","to":p["to"].lstrip("+"),"type":"template",
        "template":{"name":conf["wa_template"],"language":{"code":conf.get("wa_lang") or "it"},"components":[{"type":"body","parameters":[{"type":"text","text":x} for x in p["params"]]}]}},
        {"Authorization":"Bearer "+conf["wa_token"]})

def deliver(tenant,ch,p):
    """Sends one message; the channel it went by. WhatsApp refused (number or template) falls back to SMS when set.
    shortcut: a number without WhatsApp is only known later from Meta's webhook, not read here: SMS only on a refusal."""
    conf=config(tenant)
    if ch=="whatsapp" and conf.get("wa_token"):
        try:whatsapp(conf,p);return "whatsapp"
        except TextError as ex:
            if ex.temporary or not conf.get("provider"):raise
    if not conf.get("provider"):raise TextError("not_configured")
    sms(conf,p);return "sms"
