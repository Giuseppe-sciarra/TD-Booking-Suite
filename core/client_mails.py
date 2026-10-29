"""2.13.0: the emails to the client about his booking: confirmation, change, cancellation and the reminder before it.
The standard texts go to everyone, without reminder; the agency can let the business choose which ones go, when the
reminder leaves and the words (Notifiche). The client who leaves an email chooses whether he wants them: his card
(Contact.notices) and the box of the booking form, kept on the booking, since an online request never rewrites a card."""
import re
from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db import transaction
from django.utils import timezone

KINDS=[("confirmed","Conferma","Appena la prenotazione è fatta, online o da voi"),
       ("moved","Modifica","Quando spostate giorno, orario o servizio"),
       ("cancelled","Annullamento","Quando la prenotazione viene annullata, da voi o dal cliente"),
       ("reminder","Promemoria","Prima dell'appuntamento, perché non se ne dimentichi")]
DEFAULTS={
 "confirmed":{"on":True,"subject":"Prenotazione confermata · {attivita}","body":"La prenotazione è confermata.\n{servizio}\n{quando}\nGestisci o annulla: {link}"},
 "moved":{"on":True,"subject":"Prenotazione aggiornata · {attivita}","body":"Nuovo orario: {quando}.\nContatta l'attività per informazioni."},
 "cancelled":{"on":True,"subject":"Prenotazione annullata · {attivita}","body":"La prenotazione è stata annullata. Contatta l'attività per prenotare nuovamente."},
 "reminder":{"on":False,"hours":24,"subject":"Promemoria · {attivita}","body":"Ciao {nome},\nti ricordiamo la prenotazione: {servizio}, {quando}.\nSe non puoi venire, gestisci o annulla: {link}"},
}
HOURS=[2,3,6,12,24,48,72]
FIELDS=[("nome","nome del cliente"),("servizio","servizio"),("quando","giorno e ora"),("data","data"),("ora","ora"),("sede","sede"),
        ("operatore","operatore o camera"),("attivita","nome dell'attività"),("link","link per gestire o annullare")]
WEEKDAYS=["lunedì","martedì","mercoledì","giovedì","venerdì","sabato","domenica"]

def current(tenant):
    """What goes and with which words: the business's own only while the agency lets it choose."""
    own=tenant.client_mails if tenant.client_mails_enabled else {}
    return {k:{**v,**(own.get(k) or {})} for k,v in DEFAULTS.items()}

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
        "sede":s.get("location",""),"operatore":s.get("resource",""),"attivita":tenant.name,
        "link":f"{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/" if token else ""}

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
        "attivita":tenant.name,"link":f"{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/…/"}

def send(tenant,booking,kind):
    """The email of this moment, if it goes and the client wants it: an email to write to and no "no" from him,
    on his card or on this booking."""
    from .booking import queue
    conf=current(tenant)[kind];data=booking.data
    if not conf["on"] or not data.get("email") or booking.snapshot.get("notices") is False:return None
    if booking.contact_id and not booking.contact.notices:return None
    v=values(tenant,booking,data)
    return queue(tenant,{"to":data["email"],"subject":" ".join(fill(conf["subject"],v).split()),"body":fill(conf["body"],v)},booking.contact)

def clean(post):
    """The four emails as the business wrote them; an empty subject or text goes back to the standard one."""
    from .booking import BookingError
    out={}
    for key,label,_ in KINDS:
        d=DEFAULTS[key]
        subject=" ".join(str(post.get(key+"_subject","")).split()) or d["subject"]
        body=str(post.get(key+"_body","")).replace("\r\n","\n").strip() or d["body"]
        if len(subject)>150:raise BookingError(f"{label}: l'oggetto è troppo lungo, al massimo 150 caratteri.")
        if len(body)>4000:raise BookingError(f"{label}: il testo è troppo lungo, al massimo 4000 caratteri.")
        if any(ord(c)<32 and c!="\n" for c in subject+body):raise BookingError(f"{label}: il testo contiene caratteri non validi.")
        out[key]={"on":post.get(key+"_on")=="on","subject":subject,"body":body}
    try:hours=int(post.get("reminder_hours",""))
    except ValueError:hours=0
    if hours not in HOURS:raise BookingError("Scegli quando parte il promemoria.")
    out["reminder"]["hours"]=hours
    return out

def remind(now=None):
    """The reminders due: confirmed bookings starting within the hours chosen and booked before that window opened
    (who books for this afternoon needs no reminder), each once; a moved booking is reminded again (booking.py).
    shortcut: the bookings are read one business at a time; select_for_update(skip_locked) keeps two workers from
    sending twice on Postgres."""
    from .models import Tenant,Booking
    from .middleware import operational
    from .tenancy import scope
    now=now or timezone.now();sent=0
    for tenant in Tenant.objects.filter(client_mails_enabled=True).iterator():
        conf=current(tenant)["reminder"]
        if not conf["on"] or not operational(tenant):continue
        window=timedelta(hours=conf["hours"])
        with scope(tenant),transaction.atomic():
            rows=(Booking.objects.select_for_update(skip_locked=True,of=("self",)).filter(status="confirmed",start__gt=now,start__lte=now+window)
                .exclude(snapshot__has_key="reminded").select_related("tenant","service","contact"))
            for b in rows:
                if b.created<=b.start-window and send(tenant,b,"reminder"):sent+=1
                b.snapshot["reminded"]=now.isoformat();b.save(update_fields=["snapshot"])
    return sent
