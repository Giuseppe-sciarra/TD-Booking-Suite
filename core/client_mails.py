"""2.13.0: the emails to the client about his booking: confirmation, change, cancellation and the reminder before it.
The standard texts go to everyone; the agency can let the business choose which ones go and write the words (Notifiche).
2.14.0: the reminders (on by default for the new businesses, a second one, Confermo / Non vengo), the message before the
arrival and the one after the visit are switched on and timed by the business itself; every email has an HTML part with
logo and colour, the confirmation and the change the calendar file. The client who leaves an email chooses whether he
wants them: his card (Contact.notices) and the box of the booking form, kept on the booking, since an online request
never rewrites a card."""
import re
from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db import transaction
from django.utils import timezone

KINDS=[("confirmed","Conferma","Appena la prenotazione è fatta, online o da voi"),
       ("reminder","Promemoria","Prima dell'appuntamento, con i tasti Confermo e Non vengo"),
       ("reminder2","Secondo promemoria","Poche ore prima, con gli stessi tasti: facoltativo"),
       # 2.14.0: the services you confirm by hand (Servizi, Su richiesta): the request, then your yes or no
       ("requested","Richiesta ricevuta","Quando una prenotazione online aspetta la vostra conferma"),
       ("accepted","Richiesta accettata","Quando confermate una richiesta"),
       ("rejected","Richiesta rifiutata","Quando rifiutate una richiesta"),
       ("moved","Modifica","Quando la prenotazione cambia giorno, orario o servizio, da voi o dal cliente"),
       ("cancelled","Annullamento","Quando la prenotazione viene annullata, da voi o dal cliente"),
       ("arrival","Prima dell'arrivo","Soggiorni ed eventi: le istruzioni scritte nel servizio, per esempio check-in o punto d'incontro"),
       ("review","Dopo la visita","Il grazie con il link per la recensione, solo a chi è iscritto alle comunicazioni, al massimo una volta a settimana"),
       ("waitlist","Si è liberato un posto","A chi è in lista d'attesa, quando si libera un posto che aspetta, con il link per prenotarlo"),
       ("recall","Richiamo","Quando è ora di tornare per un servizio con il richiamo, solo a chi è iscritto alle comunicazioni e non ha già un appuntamento")]
GROUPS=[("Promemoria e conferme",("confirmed","reminder","reminder2","requested","accepted","rejected","moved","cancelled")),("Prima dell'arrivo",("arrival",)),("Dopo la visita",("review","recall")),("Lista d'attesa",("waitlist",))]
# the standard emails go and stop with the agency's switch, as in 2.13.0; the others are switched by the business
STANDARD=("confirmed","requested","accepted","rejected","moved","cancelled")
DEFAULTS={
 "confirmed":{"on":True,"price":False,"subject":"Prenotazione confermata · {attivita}","body":"La prenotazione è confermata.\n{servizio}\n{quando}\nGestisci o annulla: {link}"},
 "moved":{"on":True,"subject":"Prenotazione aggiornata · {attivita}","body":"Nuovo orario: {quando}.\nContatta l'attività per informazioni."},
 "cancelled":{"on":True,"subject":"Prenotazione annullata · {attivita}","body":"La prenotazione è stata annullata. Contatta l'attività per prenotare nuovamente."},
 "requested":{"on":True,"subject":"Richiesta ricevuta · {attivita}","body":"Abbiamo ricevuto la tua richiesta: {servizio}, {quando}.\nTi scriviamo appena la confermiamo. Fino ad allora il posto resta tenuto per te.\nPer annullarla: {link}"},
 "accepted":{"on":True,"subject":"Prenotazione confermata · {attivita}","body":"La tua richiesta è confermata.\n{servizio}\n{quando}\nGestisci o annulla: {link}"},
 "rejected":{"on":True,"subject":"Richiesta non confermata · {attivita}","body":"Ci dispiace, non possiamo confermare la tua richiesta: {servizio}, {quando}.\nContatta l'attività per trovare un'altra soluzione."},
 # 2.14.0: on by default (migration 0027 keeps the businesses that exist as they were); autocancel: hours, 0 = never
 "reminder":{"on":True,"hours":24,"autocancel":0,"subject":"Promemoria · {attivita}","body":"Ciao {nome},\nti ricordiamo la prenotazione: {servizio}, {quando}.\nSe non puoi venire, gestisci o annulla: {link}"},
 "reminder2":{"on":False,"hours":2,"subject":"A tra poco · {attivita}","body":"Ciao {nome},\nti aspettiamo tra poco: {servizio}, {quando}.\nSe non puoi venire, gestisci o annulla: {link}"},
 "arrival":{"on":False,"hours":48,"subject":"Prima del tuo arrivo · {attivita}","body":"Ciao {nome},\nti aspettiamo: {servizio}, {quando}.\n{istruzioni}\nPer gestire la prenotazione: {link}"},
 "review":{"on":False,"hours":3,"url":"","subject":"Com'è andata? · {attivita}","body":"Ciao {nome},\ngrazie di aver scelto {attivita}. Se ti va, lasciaci una recensione: per noi conta molto."},
 # 2.14.0: the line on who takes the place (everyone told, or kept for him until...) is added after the text (offer)
 # 2.14.0: Richiami: {link} is the booking page, not the old booking
 "recall":{"on":False,"subject":"È ora di tornare · {attivita}","body":"Ciao {nome},\nè il momento giusto per {servizio}. Prenota quando vuoi: {link}"},
 "waitlist":{"on":True,"subject":"Si è liberato un posto · {attivita}","body":"Ciao {nome},\nsi è liberato un posto che aspettavi: {servizio}, {quando}.\nPrenota da qui: {link}"},
}
HOURS=[2,3,6,12,24,48,72]
# when each timed message goes: hours before the start, hours after the end for the review
TIMING={"reminder":HOURS,"reminder2":[1,2,3,4,5,6],"arrival":HOURS+[168],"review":[1,2,3,6,12,24,48]}
AUTOCANCEL=[0,2,3,6,12]
FIELDS=[("nome","nome del cliente"),("servizio","servizio"),("quando","giorno e ora"),("data","data"),("ora","ora"),("sede","sede"),
        ("operatore","operatore o camera"),("attivita","nome dell'attività"),("link","link per gestire o annullare"),("istruzioni","istruzioni del servizio")]
WEEKDAYS=["lunedì","martedì","mercoledì","giovedì","venerdì","sabato","domenica"]

def current(tenant):
    """What goes and with which words. The words, and whether the standard emails go, are the business's own only while
    the agency lets it choose; the reminders, the other messages and their timing always (2.14.0)."""
    own=tenant.client_mails or {}
    mine=lambda k,x:tenant.client_mails_enabled or x not in ("subject","body") and not (x=="on" and k in STANDARD)
    return {k:{**v,**{x:y for x,y in (own.get(k) or {}).items() if mine(k,x)}} for k,v in DEFAULTS.items()}

def fill(text,values):
    """{nome}, {quando}...: a name not in the list stays as it was written."""
    return re.sub(r"\{(\w+)\}",lambda m:values.get(m.group(1),m.group(0)),text)

def when(start,end,stay):
    if stay:return f"dal {start:%d/%m/%Y}, arrivo {start:%H:%M}, al {end:%d/%m/%Y}, partenza {end:%H:%M}"
    return f"{WEEKDAYS[start.weekday()]} {start:%d/%m/%Y} alle {start:%H:%M}"

def values(tenant,booking,data):
    tz=ZoneInfo(tenant.timezone);s=booking.snapshot
    start=booking.start.astimezone(tz);end=booking.end.astimezone(tz)
    token=data.get("cancel_token")
    return {"nome":data.get("first_name",""),"servizio":" · ".join(x for x in (s.get("service") or booking.service.name,s.get("option")) if x),
        "quando":when(start,end,booking.service.kind=="day"),"data":f"{start:%d/%m/%Y}","ora":f"{start:%H:%M}",
        "sede":s.get("location",""),"operatore":s.get("resource",""),"attivita":tenant.name,"istruzioni":booking.service.instructions,
        "link":f"{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/" if token else ""}

# 2.14.0: the WhatsApp button of a booking and of the client card opens the chat with this text, with the fields of the
# emails; the person at the desk sends it from WhatsApp, the program sends nothing
WHATSAPP="Ciao {nome}, ti scriviamo da {attivita} per la prenotazione: {servizio}, {quando}."
def whatsapp(tenant,phone,booking=None):
    """The wa.me link to this phone (an Italian number without prefix is +39), empty without a usable number.
    With a booking, the business's text filled with it; without, an empty chat."""
    from urllib.parse import quote
    from .booking import normalize_phone
    number=normalize_phone(phone)[1:]
    if not number:return ""
    text=fill(tenant.whatsapp_text or WHATSAPP,values(tenant,booking,booking.data)) if booking else ""
    return f"https://wa.me/{number}"+(f"?text={quote(text)}" if text else "")

def sample(tenant):
    """The values of the preview: a booking tomorrow at 10:30 with the first service, operator and location."""
    from .models import Service,Resource,Location
    tz=ZoneInfo(tenant.timezone);day=timezone.now().astimezone(tz).date()+timedelta(days=1)
    start=datetime.combine(day,time(10,30),tz)
    service=Service.objects.filter(active=True).order_by("name").first()
    stay=bool(service) and service.kind=="day"
    resource=Resource.objects.filter(active=True).order_by("name").first()
    location=Location.objects.filter(active=True).order_by("name").first()
    return {"nome":"Giulia","servizio":service.name if service else "Taglio","quando":when(start,start+timedelta(days=2),stay),
        "data":f"{start:%d/%m/%Y}","ora":f"{start:%H:%M}","sede":location.name if location else "Sede","operatore":resource.name if resource else "",
        "attivita":tenant.name,"link":f"{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/…/",
        "istruzioni":(service.instructions if service else "") or "Le chiavi sono nella cassetta accanto al portone, codice 1234."}

def send(tenant,booking,kind):
    """The email of this moment, if it goes and the client wants it: an email to write to and no "no" from him,
    on his card or on this booking. 2.14.0: the same text with an HTML part (logo and colour of the business); the
    reminders with Confermo and Non vengo, the review with its link, as buttons and as lines at the end of the text;
    the price in the confirmation when the business wants it; the calendar file with the confirmation and the change."""
    from django.template.loader import render_to_string
    from .booking import queue,action_token
    from .branding import contrast_color
    mails=current(tenant);conf=mails[kind];data=booking.data
    if not conf["on"] or not data.get("email") or booking.snapshot.get("notices") is False:return None
    if booking.contact_id and not booking.contact.notices:return None
    v=values(tenant,booking,data);body=fill(conf["body"],v);links=[]
    if kind in ("confirmed","accepted") and mails["confirmed"]["price"] and booking.snapshot.get("total"):
        from .prices import euro
        body+=f"\nPrezzo indicativo: {euro(booking.snapshot['total'])}, si paga in sede"
    if kind=="cancelled" and booking.snapshot.get("cancel_reason")=="unconfirmed":body="Non abbiamo ricevuto la tua conferma.\n"+body
    if kind in ("reminder","reminder2"):
        # a reminder leaves at most 3 days before: its buttons last until the day after
        links=[(label,f"{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{action_token(tenant,what,booking.contact,booking,days=4)}/") for label,what in (("Confermo","confirm"),("Non vengo","decline"))]
    if kind=="review":links=[("Lascia una recensione",conf["url"])]
    if kind=="recall":
        v["link"]=f"{settings.PUBLIC_ORIGIN}/book/{tenant.slug}/";body=fill(conf["body"],v);links=[("Prenota",v["link"])]
    # 2.14.0: a distanza: the video-call link with the confirmation, the change and the reminders
    if booking.service.remote_url and kind in ("confirmed","accepted","moved","reminder","reminder2"):links.insert(0,("Entra nella videochiamata",booking.service.remote_url))
    payload={"to":data["email"],"subject":" ".join(fill(conf["subject"],v).split()),"body":body+"".join(f"\n{label}: {url}" for label,url in links),
        "html":render_to_string("email.html",{"tenant":tenant,"body":body,"links":links,"color":contrast_color(tenant.brand_color)})}
    if kind in ("confirmed","accepted","moved"):
        from .ics import ics
        payload["ics"]=ics(booking)
    return queue(tenant,payload,booking.contact)

def offer(tenant,entry,slots,link,until=None):
    """2.14.0: Si è liberato un posto, to a client of the waiting list who left an email and did not say no to the
    emails about his bookings: the free times of the day, the link of the booking page on it and who takes the place."""
    from django.template.loader import render_to_string
    from .booking import queue
    from .branding import contrast_color
    conf=current(tenant)["waitlist"];d=entry.data
    if not conf["on"] or not d.get("email") or not entry.contact.notices:return None
    tz=ZoneInfo(tenant.timezone);first=datetime.fromisoformat(slots[0]["start"]).astimezone(tz)
    if entry.service.kind=="day":quando=when(first,datetime.fromisoformat(slots[0]["end"]).astimezone(tz),True)
    else:quando=f"{WEEKDAYS[first.weekday()]} {first:%d/%m/%Y} alle "+", ".join(datetime.fromisoformat(x["start"]).astimezone(tz).strftime("%H:%M") for x in slots[:6])+(" e altri orari" if len(slots)>6 else "")
    v={"nome":d.get("first_name",""),"servizio":entry.service.name,"quando":quando,"data":f"{first:%d/%m/%Y}","ora":f"{first:%H:%M}","sede":entry.service.location.name,
        "operatore":entry.resource.name if entry.resource else "","attivita":tenant.name,"istruzioni":"","link":link}
    body=fill(conf["body"],v)+"\n"+(f"Il posto è tenuto per te fino alle {until.astimezone(tz):%H:%M} di {WEEKDAYS[until.astimezone(tz).weekday()]}, poi passa a chi viene dopo in lista." if until
        else "Lo abbiamo scritto a tutti quelli in lista: lo prende chi prenota per primo. Se non c'è più, sulla pagina vedi cosa resta libero.")
    return queue(tenant,{"to":d["email"],"subject":" ".join(fill(conf["subject"],v).split()),"body":body,
        "html":render_to_string("email.html",{"tenant":tenant,"body":body,"links":[("Prenota",link)],"color":contrast_color(tenant.brand_color)})},entry.contact)

def clean(post,enabled=True,saved=None):
    """The emails as the business set them. Words, and whether the standard emails go, only while the agency lets it
    write them (otherwise those saved stay as they were); an empty subject or text goes back to the standard one."""
    from .booking import BookingError
    out={}
    for key,label,_ in KINDS:
        d=DEFAULTS[key];mine=dict((saved or {}).get(key) or {})
        if enabled:
            subject=" ".join(str(post.get(key+"_subject","")).split()) or d["subject"]
            body=str(post.get(key+"_body","")).replace("\r\n","\n").strip() or d["body"]
            if len(subject)>150:raise BookingError(f"{label}: l'oggetto è troppo lungo, al massimo 150 caratteri.")
            if len(body)>4000:raise BookingError(f"{label}: il testo è troppo lungo, al massimo 4000 caratteri.")
            if any(ord(c)<32 and c!="\n" for c in subject+body):raise BookingError(f"{label}: il testo contiene caratteri non validi.")
            mine.update(subject=subject,body=body)
        if enabled or key not in STANDARD:mine["on"]=post.get(key+"_on")=="on"
        if key in TIMING:
            try:mine["hours"]=int(post.get(key+"_hours",mine.get("hours",d["hours"])))
            except ValueError:mine["hours"]=0
            if mine["hours"] not in TIMING[key]:raise BookingError(f"{label}: scegli quando parte.")
        out[key]=mine
    out["confirmed"]["price"]=post.get("confirmed_price")=="on"
    try:cancel=int(post.get("reminder_autocancel",0))
    except ValueError:cancel=-1
    if cancel not in AUTOCANCEL:raise BookingError("Scegli quando annullare le prenotazioni non confermate.")
    if cancel and cancel>=out["reminder"]["hours"]:raise BookingError("L'annullamento automatico deve venire dopo il promemoria: scegli meno ore.")
    out["reminder"]["autocancel"]=cancel
    url=str(post.get("review_url","")).strip()
    if url:
        from django.core.exceptions import ValidationError
        from django.core.validators import URLValidator
        try:URLValidator(schemes=["https"])(url)
        except ValidationError:raise BookingError("Dopo la visita: il link della recensione deve iniziare con https://")
    elif out["review"]["on"]:raise BookingError("Dopo la visita: scrivi il link per la recensione, per esempio quello di Google.")
    out["review"]["url"]=url
    return out

BEFORE={"reminder":"reminded","reminder2":"reminded2","arrival":"arrival"}  # the messages before the start, the mark of each
def remind(now=None):
    """The messages due, each once (a moved booking gets them again, booking.py): the reminders to the confirmed
    bookings starting within the hours chosen and booked before that window opened (who books for this afternoon needs
    no reminder), the instructions before the arrival to the stays and events whose service has them; then the
    bookings left unconfirmed, where the business wants it, and the email after the visit.
    shortcut: the bookings are read one business at a time; select_for_update(skip_locked) keeps two workers from
    sending twice on Postgres."""
    from .models import Tenant,Booking
    from .middleware import operational
    from .tenancy import scope
    from . import texts
    now=now or timezone.now();sent=0
    for tenant in Tenant.objects.iterator():
        if not operational(tenant):continue
        conf=current(tenant)
        with scope(tenant),transaction.atomic():
            for kind,mark in BEFORE.items():
                if not conf[kind]["on"]:continue
                window=timedelta(hours=conf[kind]["hours"])
                rows=(Booking.objects.select_for_update(skip_locked=True,of=("self",)).filter(status="confirmed",start__gt=now,start__lte=now+window)
                    .exclude(snapshot__has_key=mark).select_related("tenant","service","contact","location"))
                # the instructions are needed also by who books at the last minute
                if kind=="arrival":rows=rows.filter(service__kind__in=("day","event")).exclude(service__instructions="")
                for b in rows:
                    # 2.14.0: the reminders also by SMS or WhatsApp, when the agency set one (texts.py)
                    if (kind=="arrival" or b.created<=b.start-window) and (bool(send(tenant,b,kind))|bool(texts.remind(tenant,b,kind))):
                        sent+=1
                        if kind!="arrival":b.snapshot["asked"]=now.isoformat()  # Confermo / Non vengo went out: Da chiamare
                    b.snapshot[mark]=now.isoformat();b.save(update_fields=["snapshot"])
            sent+=unconfirmed(tenant,conf["reminder"]["autocancel"],now)+reviews(tenant,conf["review"],now)+recall(tenant,conf["recall"],now)
    return sent

def unconfirmed(tenant,hours,now):
    """2.14.0: off unless the business wants it, as resOS does: the bookings whose client was asked to confirm before
    the last hours began and did not, cancelled with their reason and the email that says it."""
    if not hours:return 0
    from .models import Booking
    from .booking import change_status
    window=timedelta(hours=hours);done=0
    for b in Booking.objects.filter(status="confirmed",start__gt=now,start__lte=now+window,snapshot__has_key="asked").exclude(snapshot__has_key="client_confirmed"):
        if datetime.fromisoformat(b.snapshot["asked"])<=b.start-window:change_status(tenant,b,"cancelled",reason="unconfirmed");done+=1
    return done

def reviews(tenant,conf,now):
    """2.14.0: the email after the visit, to the bookings closed as done that ended these hours ago (not to the older
    ones when it is switched on), only to the clients with the marketing consent and never twice in 7 days to the same."""
    if not conf["on"] or not conf.get("url"):return 0
    from .models import Booking
    after=now-timedelta(hours=conf["hours"]);sent=0
    rows=(Booking.objects.select_for_update(skip_locked=True,of=("self",)).filter(status="completed",end__lte=after,end__gt=after-timedelta(days=2),contact__consent="yes",contact__suppressed=False)
        .exclude(snapshot__has_key="review").select_related("tenant","service","contact","location"))
    for b in rows:
        recent=Booking.objects.filter(contact_id=b.contact_id,end__gt=now-timedelta(days=14),snapshot__has_key="review").values_list("snapshot",flat=True)
        go=not any(s["review"] and datetime.fromisoformat(s["review"])>now-timedelta(days=7) for s in recent)
        b.snapshot["review"]=now.isoformat() if go and send(tenant,b,"review") else ""  # "": looked at, not sent
        sent+=bool(b.snapshot["review"]);b.save(update_fields=["snapshot"])
    return sent

def recall(tenant,conf,now):
    """2.14.0: the recall email, once per visit, on its day, only with the marketing consent (art. 130: it invites to book)
    and never to who already has something booked (clients.recalls)."""
    if not conf["on"]:return 0
    from .clients import recalls
    sent=0
    for r in recalls(now,ahead=0):
        b,c=r["booking"],r["contact"]
        if "recall_sent" in b.snapshot or c.consent!="yes" or c.suppressed:continue
        b.snapshot["recall_sent"]=now.isoformat() if send(tenant,b,"recall") else ""  # "": looked at, not sent
        sent+=bool(b.snapshot["recall_sent"]);b.save(update_fields=["snapshot"])
    return sent
