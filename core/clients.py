"""2.7.0: the client card. History of everything a client booked, notes kept by the staff (the colour
formula, an allergy), clients added by hand (also without email), quick search for manual bookings.

Contact data stay encrypted like the rest. 2.14.0: a card is found by keyed indexes of its email, phone, the starts of
the words of its name, its tags and birthday, made with the business key (stamp): none of them can be read back.
"""
import hashlib,hmac,re,unicodedata
from collections import Counter
from datetime import date,datetime,timedelta
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.db.models import Count,Max,Q
from django.utils import timezone
from .booking import BookingError,normalize_phone,ACCEPTED,MOVABLE
from .crypto import encrypt,index,tenant_key,open_seal
from .models import Contact,Booking,Treatment,audit
from .prices import spent

ERASED="erased:"

def phone_index(tenant,phone):return index("tel:"+phone,tenant_key(tenant),"email")
def email_index(tenant,email):return index(email.casefold(),tenant_key(tenant),"email")

def words(text):
    """Lower case without accents, in words: "Nicolò D'Amico" is nicolo, d, amico."""
    t=unicodedata.normalize("NFKD",str(text or "").casefold())
    return re.findall(r"[^\W_]+","".join(c for c in t if not unicodedata.combining(c)))
def search_keys(key):
    """2.14.0: the short keyed index of a word, with the business key derived once (an import stamps 20.000 cards)."""
    mac=HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=b"search").derive(key)
    return lambda v:hmac.new(mac,v.encode(),hashlib.sha256).hexdigest()[:16]
def tag_key(h,tag):return h("t:"+" ".join(words(tag)))
def stamp(c,d,key,h=None):
    """2.14.0: the indexes of a card from its data: phone (the same key a card without email has), each start of the words
    of its name, its tags, the month and day of its birthday."""
    h=h or search_keys(key)
    c.phone_index=index("tel:"+d["phone"],key,"email") if d.get("phone") else ""
    c.name_index=" ".join(sorted({h("n:"+w[:i]) for w in words(d.get("first_name","")+" "+d.get("last_name","")) for i in range(1,min(len(w),20)+1)}))
    c.tag_index=" ".join(tag_key(h,t) for t in d.get("tags",[]))
    c.birthday_index=h("b:"+d["birthday"][5:]) if d.get("birthday") else ""
INDEXES=["phone_index","name_index","tag_index","birthday_index"]
def seal_card(tenant,c,d):
    """The card's data sealed with the business key, and its indexes."""
    c.pii=encrypt(tenant,d,"contact:"+str(c.pk));stamp(c,d,tenant_key(tenant))

def clean(data,require_contact=True):
    """Name, surname, email and phone typed by the staff. Email optional, phone or email needed."""
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    out={}
    for k,limit in (("first_name",100),("last_name",100),("email",254),("phone",40)):
        v=str(data.get(k,"") or "").strip()
        if len(v)>limit or any(ord(c)<32 for c in v):raise BookingError("Dati del cliente non validi.")
        out[k]=v
    if not out["first_name"]:raise BookingError("Scrivi almeno il nome del cliente.")
    if out["email"]:
        try:validate_email(out["email"])
        except ValidationError:raise BookingError("Email non valida.")
        out["email"]=out["email"].casefold()
    if out["phone"]:
        p=normalize_phone(out["phone"])
        if not p:raise BookingError("Numero di telefono non valido, per esempio 333 123 4567.")
        out["phone"]=p
    if require_contact and not out["email"] and not out["phone"]:raise BookingError("Serve almeno il telefono o l'email del cliente.")
    return out

def key_for(tenant,d,shared=False):
    """The card's key: its email, otherwise its phone; a phone already on another person's card (2.13.0: a mother
    and her daughter) is told apart by the name."""
    if d.get("email"):return email_index(tenant,d["email"])
    if shared:return index("tel:"+d["phone"]+"|"+full_name(d),tenant_key(tenant),"email")
    return phone_index(tenant,d["phone"])

def full_name(d):return " ".join((d.get("first_name","")+" "+d.get("last_name","")).casefold().split())

def same_person(card,d):
    """2.13.0: the card found by phone is this client only if one name holds the other ("Maria" and "Maria Rossi");
    a new name on a known phone is another person (a mother and her daughter), with a card of their own."""
    short,long=sorted((full_name(card.data).split(),full_name(d).split()),key=len)
    return all(w in long for w in short)

def by_phone(tenant,phone,without_email=False):
    """The card of a phone number: keyed by it (added at the desk without email) or with it among its data
    (booked online, keyed by the email). The same phone means the same person; without_email keeps only cards
    with no email, for an online request that brings its own (two emails are two people, even on one phone).
    2.14.0: by the phone index of every card, no longer among the 5000 most recent only."""
    if not phone:return None
    key=phone_index(tenant,phone)
    c=Contact.objects.filter(email_index=key).first()
    if c:return c
    for c in Contact.objects.filter(phone_index=key).exclude(email_index__startswith=ERASED).order_by("-created"):
        if not (without_email and c.data.get("email")):return c
    return None

def find_or_create(tenant,d):
    """The contact of a manual booking or of a client added by hand: by email, then by phone."""
    c=Contact.objects.filter(email_index=email_index(tenant,d["email"])).first() if d.get("email") else None
    if c:return c,False
    known=by_phone(tenant,d.get("phone"))
    if known and same_person(known,d):return known,False
    # the same phone with another name, already with its own card: that one
    if known and not d.get("email"):
        mine=Contact.objects.filter(email_index=key_for(tenant,d,shared=True)).first()
        if mine:return mine,False
    c=Contact(tenant=tenant,email_index=key_for(tenant,d,shared=bool(known)))
    seal_card(tenant,c,{k:d.get(k,"") for k in ("first_name","last_name","email","phone")});c.save()
    return c,True

def update(tenant,contact,fields=None,notes=None):
    """Edit the card. A new email or phone moves the contact's key, unless another client already has it."""
    data=dict(contact.data)
    if fields is not None:
        d=clean(fields)
        new_key=key_for(tenant,d)
        taken=lambda key:Contact.objects.filter(email_index=key).exclude(pk=contact.pk).exists()
        # a phone shared with another person keeps both cards, told apart by the name
        if not d.get("email") and new_key!=contact.email_index and taken(new_key):new_key=key_for(tenant,d,shared=True)
        if new_key!=contact.email_index and taken(new_key):
            raise BookingError("Un altro cliente ha già questa email o questo telefono.")
        data.update(d);contact.email_index=new_key
    if notes is not None:data["staff_notes"]=str(notes)[:4000]
    seal_card(tenant,contact,data);contact.save(update_fields=["pii","email_index",*INDEXES])
    return data

MERGE_FIELDS=[("first_name","Nome"),("last_name","Cognome"),("email","Email"),("phone","Telefono"),("birthday","Data di nascita")]
def merge(tenant,main,other,keep,actor):
    """2.14.0: two cards of the same person made one, for good. main keeps its place (address, history); each field
    takes the value chosen (keep: field -> "main" or "other"), notes and tags of both; VIP and "not bookable online" if
    either has them, the emails of the bookings only if both want them. Bookings, record, waiting list, emails and
    links move to main. The marketing consent follows the email kept: the double opt-in proof belongs to that mailbox,
    so it never passes to another one (an email dropped takes its consent with it). The other card stays, without
    data and merged into main, for its consent history, which cannot be changed."""
    import secrets
    from django.db import transaction
    from .crypto import decrypt
    from .models import Tenant,WaitEntry,ActionToken,Delivery
    from .privacy import record
    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        main=Contact.objects.select_for_update().get(pk=main.pk);other=Contact.objects.select_for_update().get(pk=other.pk)
        if main.pk==other.pk or other.email_index.startswith(ERASED):raise BookingError("Scegli due schede diverse.")
        a,b=main.data,other.data;d=dict(a)
        for f,_ in MERGE_FIELDS:
            v=(b if keep.get(f)=="other" else a).get(f,"")
            if v:d[f]=v
            else:d.pop(f,None)
        d["first_name"]=d.get("first_name") or b.get("first_name","")
        if not d.get("email") and not d.get("phone"):raise BookingError("Tieni almeno il telefono o l'email.")
        notes="\n".join(x for x in (a.get("staff_notes",""),b.get("staff_notes","")) if x)[:4000]
        if notes:d["staff_notes"]=notes
        tags=list(dict.fromkeys(a.get("tags",[])+b.get("tags",[])))[:20]
        if tags:d["tags"]=tags
        if not d.get("import_code") and b.get("import_code"):d["import_code"]=b["import_code"]
        moved=d.get("email","")!=a.get("email","")
        if moved:
            mine=bool(d.get("email"))  # the kept email is the other card's: its consent comes with it, otherwise none
            main.consent,main.consent_at,main.suppressed=(other.consent,other.consent_at,other.suppressed) if mine else ("none",None,False)
            main.consent_proof=encrypt(tenant,decrypt(tenant,other.consent_proof,"consent:"+str(other.pk)),"consent:"+str(main.pk)) if mine and other.consent_proof else ""
        if not main.discount_kind and other.discount_kind:main.discount_kind,main.discount=other.discount_kind,other.discount
        main.vip|=other.vip;main.online_blocked|=other.online_blocked;main.notices=main.notices and other.notices
        other.email_index=ERASED+"merged-"+secrets.token_hex(20)
        other.pii=encrypt(tenant,{"first_name":"Scheda unita","last_name":"","email":"","phone":""},"contact:"+str(other.pk))
        other.consent_proof="";other.suppressed=True;other.merged_into=main;other.vip=other.online_blocked=False
        other.phone_index=other.name_index=other.tag_index=other.birthday_index=""
        other.save()
        new=key_for(tenant,d);taken=lambda k:Contact.objects.filter(email_index=k).exclude(pk=main.pk).exists()
        if not d.get("email") and taken(new):new=key_for(tenant,d,shared=True)
        if taken(new):raise BookingError("Un altro cliente ha già questa email o questo telefono.")
        main.email_index=new;seal_card(tenant,main,d);main.save()
        for model in (Booking,Treatment,WaitEntry):model.objects.filter(contact=other).update(contact=main)
        # a confirmation asked to a mailbox the card no longer has must not confirm anything
        ActionToken.objects.filter(contact=main if moved else other,kind="consent").delete()
        ActionToken.objects.filter(contact=other).update(contact=main)
        taken_campaigns=Delivery.objects.filter(contact=main,campaign__isnull=False).values_list("campaign_id",flat=True)
        Delivery.objects.filter(contact=other).exclude(campaign_id__in=list(taken_campaigns)).update(contact=main)
        for x in Delivery.objects.filter(contact=other).exclude(payload=""):  # the same campaign to both: the copy is history
            x.payload=encrypt(tenant,{"redacted":True},"delivery:"+str(x.pk));x.save(update_fields=["payload"])
        record(tenant,main,"merged","operator",actor,ref=other.pk);record(tenant,other,"merged","operator",actor,ref=main.pk)
        audit(tenant,actor,"contact.merged",main.pk,other=str(other.pk),kept={f:keep.get(f) or "main" for f,_ in MERGE_FIELDS},consent="other" if moved and d.get("email") else "none" if moved else "main")
        return main

def clean_profile(post,today):
    """2.14.0: tags (free words, comma separated, at most 20 of 40 characters), the birthday (optional, not in the
    future), VIP and "not bookable online", as the staff typed them."""
    tags={}
    for t in str(post.get("tags","")).split(","):
        t=" ".join(t.split())[:40]
        if t:tags.setdefault(" ".join(words(t)),t)  # "Sposa" and "sposa" are one tag
    tags=list(tags.values())
    if len(tags)>20 or any(any(ord(c)<32 for c in t) for t in tags):raise BookingError("Etichette: al massimo 20, separate da virgole.")
    birthday=""
    if post.get("birthday"):
        try:b=date.fromisoformat(str(post["birthday"]))
        except ValueError:raise BookingError("Data di nascita non valida.")
        if not date(1900,1,1)<=b<=today:raise BookingError("Data di nascita non valida.")
        birthday=b.isoformat()
    return {"tags":tags,"birthday":birthday,"vip":post.get("vip")=="on","online_blocked":post.get("online_blocked")=="on"}

def save_profile(tenant,contact,p):
    data=dict(contact.data)
    for k in ("tags","birthday"):
        if p[k]:data[k]=p[k]
        else:data.pop(k,None)
    contact.vip,contact.online_blocked=p["vip"],p["online_blocked"]
    seal_card(tenant,contact,data);contact.save(update_fields=["pii","vip","online_blocked",*INDEXES])
    return data

def opener(tenant):
    """The cards opened with the business key unwrapped once: a list of many cards stays fast."""
    key=tenant_key(tenant)
    return lambda c:open_seal(c.pii,key,f"{tenant.pk}:contact:{c.pk}")

def known_tags(tenant):
    """Every tag in use, for the filter of the client list and the suggestions.
    shortcut: opens only the tagged cards; fine while tags are put by hand, a list kept on the business past that."""
    read=opener(tenant)
    return sorted({t for c in Contact.objects.exclude(tag_index="").exclude(email_index__startswith=ERASED).only("id","pii") for t in read(c).get("tags",[])},key=str.casefold)

def with_tag(tenant,rows,tag):return rows.filter(tag_index__contains=tag_key(search_keys(tenant_key(tenant)),tag))

def birthdays(tenant,first,days=7):
    """2.14.0: the clients whose birthday falls in the days from first, with the day it falls on this time."""
    h=search_keys(tenant_key(tenant));when={}
    for i in range(days):
        d=first+timedelta(days=i);when[h("b:"+d.strftime("%m-%d"))]=d
        if d.month==2 and d.day==28 and (d+timedelta(days=1)).month==3:when[h("b:02-29")]=d  # no 29 February this year
    read=opener(tenant);out=[]
    for c in Contact.objects.filter(birthday_index__in=list(when)).exclude(email_index__startswith=ERASED):
        d=read(c);day=when[c.birthday_index];out.append({"contact":c,"data":d,"day":day,"age":day.year-int(d["birthday"][:4])})
    return sorted(out,key=lambda x:x["day"])

LATE_HOURS=24
def reliability(contact_ids):
    """2.14.0: for each card how many times the client did not come, and how many times he cancelled himself (or did
    not confirm) in the last 24 hours before the start."""
    out={}
    for cid,status,start,snap in Booking.objects.filter(contact_id__in=contact_ids,status__in=("no_show","cancelled")).values_list("contact_id","status","start","snapshot"):
        n=out.setdefault(cid,[0,0])
        if status=="no_show":n[0]+=1
        elif late(status,start,snap):n[1]+=1
    return out
def late(status,start,snap):
    """Cancelled by the client (or not confirmed) in the last LATE_HOURS before the start; also used by the reports."""
    return status=="cancelled" and snap.get("cancel_reason") in ("client","unconfirmed") and bool(snap.get("undo")) and datetime.fromisoformat(snap["undo"]["at"])>start-timedelta(hours=LATE_HOURS)

def mark(request,rows):
    """2.14.0: VIP, tags, absences and late cancellations of the client, as row.marks on each booking, only for who may see
    the client cards (the agenda, the lists, the booking)."""
    from .permissions import can
    rows=list(rows)
    ids={r.contact_id for r in rows if r.contact_id}
    if not ids or not can(request,"contacts.read"):return rows
    counts=reliability(ids);read=opener(request.tenant)
    cards={c.pk:c for c in Contact.objects.filter(pk__in=ids).only("id","pii","vip","online_blocked","tag_index")}
    for r in rows:
        c=cards.get(r.contact_id)
        if c:
            n=counts.get(c.pk,(0,0))
            r.marks={"vip":c.vip,"blocked":c.online_blocked,"tags":read(c).get("tags",[]) if c.tag_index else [],"no_show":n[0],"late":n[1]}
    return rows

def set_discount(contact,kind,value):
    """2.11.0: the client's own discount, a percentage (up to 100) or euro off; nothing to remove it. His confirmed
    future bookings take it at once; the past ones stay as they were paid. Returns how many bookings changed."""
    from decimal import Decimal,InvalidOperation
    from .prices import stamp
    if kind not in ("","percent","amount"):raise BookingError("Tipo di sconto non valido.")
    try:amount=Decimal(str(value or "0").strip().replace("€","").replace(" ","").replace(",",".")) if kind else Decimal(0)
    except InvalidOperation:raise BookingError("Scrivi lo sconto con un numero, per esempio 10 o 5,50.")
    if kind and not (Decimal("0.01")<=amount<=(Decimal(100) if kind=="percent" else Decimal(100000))):
        raise BookingError("Lo sconto in percentuale va da 0,01 a 100." if kind=="percent" else "Lo sconto in euro va da 0,01 a 100.000.")
    contact.discount_kind=kind;contact.discount=amount.quantize(Decimal("0.01"))
    contact.save(update_fields=["discount_kind","discount"])
    changed=0
    for b in Booking.objects.filter(contact=contact,status__in=MOVABLE,start__gt=timezone.now()).select_related("service","option","resource"):
        before=dict(b.snapshot);b.contact=contact;stamp(b)
        if b.snapshot!=before:b.save(update_fields=["snapshot"]);changed+=1
    return changed

def search(tenant,q,limit=10,rows=None):
    """2.14.0: every card, not only the 5000 most recent: a whole email or phone by their index, a name by the starts of
    its words ("mar ros" finds Mario Rossi), most recent first. Part of an email or of a phone ("4567") is looked for in
    every card, opened in batches. rows: the cards to look among (the filters of the list)."""
    q=(q or "").strip()
    if len(q)<2:return []
    rows=(Contact.objects.all() if rows is None else rows).exclude(email_index__startswith=ERASED)
    key=tenant_key(tenant);read=opener(tenant);digits=re.sub(r"\D","",q);out=[]
    phone=normalize_phone(q) if len(digits)>=6 and re.fullmatch(r"[\d\s+()./-]+",q) else ""
    if "@" in q:found=rows.filter(email_index=index(q.casefold(),key,"email"))
    elif phone:found=rows.filter(Q(phone_index=index("tel:"+phone,key,"email"))|Q(email_index=index("tel:"+phone,key,"email")))
    elif not re.search(r"[^\W\d_]",q):found=rows.none()  # no letters: part of a phone, below
    else:
        h=search_keys(key);found=rows
        for w in words(q):found=found.filter(name_index__contains=h("n:"+w[:20]))
    for c in found.order_by("-created")[:limit]:c.found=read(c);out.append(c)
    if out or not ("@" in q or len(digits)>=3):return out
    # part of an email or a phone: the cards in batches, the business key unwrapped once (about 0.6 s for 20.000)
    q=q.casefold()
    for c in rows.order_by("-created").only("id","pii","created").iterator(chunk_size=2000):
        d=read(c)
        if (q in d.get("email","").casefold() if "@" in q else digits in d.get("phone","").replace("+","")):
            c.found=d;out.append(c)
            if len(out)>=limit:break
    return out

def history(contact,visible):
    """All the bookings of a client the user may see, newest first, and the numbers of the card."""
    rows=list(visible.filter(contact=contact).select_related("service","resource","option","location","tenant").order_by("-start"))
    now=timezone.now()
    done=[b for b in rows if b.start<=now and b.status in ACCEPTED]
    upcoming=sorted([b for b in rows if b.start>now and b.status in MOVABLE],key=lambda b:b.start)
    services=Counter(b.snapshot.get("service") or b.service.name for b in done)
    staff=Counter(b.snapshot.get("resource") for b in done if b.snapshot.get("resource"))
    stats={"visits":len(done),"last":done[0].start if done else None,"next":upcoming[0] if upcoming else None,
        "cancelled":sum(1 for b in rows if b.status=="cancelled"),"no_show":sum(1 for b in rows if b.status=="no_show"),
        "service":services.most_common(1)[0] if services else None,"staff":staff.most_common(1)[0] if staff else None,"total":len(rows),
        "spent":spent(done)}
    return rows,stats

def clean_treatment(data,today):
    """2.8.0: one entry of the client record typed by the staff: a past or today's date, what was done (required),
    who did it and the details, line breaks included."""
    from datetime import date
    try:day=date.fromisoformat(str(data.get("date","")))
    except ValueError:raise BookingError("Data non valida.")
    if day>today:raise BookingError("La data non può essere nel futuro: qui si scrive quello che è stato fatto.")
    if day.year<1900:raise BookingError("Data non valida.")
    out={"date":day}
    for k,limit in (("service",120),("staff",100)):
        v=str(data.get(k,"") or "").strip()
        if len(v)>limit or any(ord(c)<32 for c in v):raise BookingError("Dati non validi o troppo lunghi.")
        out[k]=v
    if not out["service"]:raise BookingError("Scrivi cosa è stato fatto.")
    details=str(data.get("details","") or "").replace("\r\n","\n").strip()
    if len(details)>4000 or any(ord(c)<32 and c not in "\n\t" for c in details):raise BookingError("Dettagli non validi o troppo lunghi.")
    out["details"]=details
    return out

def save_treatment(tenant,contact,d,actor,treatment=None,booking=None):
    t=treatment or Treatment(tenant=tenant,contact=contact,booking=booking,author=str(getattr(actor,"pk","") or ""))
    t.date=d["date"];t.pii=encrypt(tenant,{k:d[k] for k in ("service","staff","details")},"treatment:"+str(t.pk))
    t.save();return t

def treatments(contact):
    """The record of a client, newest first, each entry with its decrypted info."""
    rows=list(Treatment.objects.filter(contact=contact).select_related("booking").order_by("-date","-created"))
    for r in rows:r.info=r.data
    return rows

def from_booking(booking,tz):
    """What a booking already says about the treatment: day, service with its variant, who."""
    s=booking.snapshot
    return {"date":booking.start.astimezone(tz).date(),"service":(s.get("service") or booking.service.name)+(" · "+s["option"] if s.get("option") else ""),
        "staff":s.get("resource") or "","details":"","booking":str(booking.pk)}

def visit_counts(contacts):
    """Visits and last visit for a page of the client list."""
    now=timezone.now()
    rows=Booking.objects.filter(contact__in=contacts).values("contact_id").annotate(
        visits=Count("id",filter=Q(start__lte=now,status__in=ACCEPTED)),last=Max("start",filter=Q(start__lte=now,status__in=ACCEPTED)),
        next=Count("id",filter=Q(start__gt=now,status__in=MOVABLE)))
    return {r["contact_id"]:r for r in rows}
