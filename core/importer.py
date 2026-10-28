"""Import of the client list and of the treatment history from the old program, as CSV or Excel (.xlsx).

The file is read once and sealed with the business key in a temporary file; the staff say what each column
holds, the preview runs the same code as the import without writing. An existing card is never overwritten:
it only gets its empty fields, new notes and new treatments. Marketing consents in the file are recorded as
"Importato" and stay to be confirmed: only the confirmation from the mailbox makes them valid.
"""
import csv,io,os,re,secrets,tempfile,time,unicodedata,zipfile
from collections import Counter
from datetime import date,timedelta
from xml.etree import ElementTree
from zoneinfo import ZoneInfo
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from .booking import BookingError,normalize_phone,action_token,queue
from .clients import ERASED,email_index,phone_index
from .crypto import encrypt,decrypt
from .models import Contact,Treatment,ConsentEvent,ActionToken

MAX_BYTES=20*1024*1024
MAX_ROWS=20000      # shortcut: one request does the whole import, about 20 s at this size; past it, a background job
MAX_COLUMNS=60      # Django accepts 100 fields per form: one select per column

FIELDS=[("","Non importare"),("first_name","Nome"),("last_name","Cognome"),("full_name","Nome e cognome insieme"),
    ("phone","Telefono"),("email","Email"),("code","Codice cliente del vecchio programma"),("notes","Note"),
    ("extra","Nelle note, con il titolo della colonna"),("date","Data del trattamento"),("service","Trattamento fatto"),
    ("staff","Operatore"),("details","Dettagli del trattamento"),("consent","Consenso marketing")]
RECORD={"date","service","staff","details"}
SINGLE={"first_name","last_name","full_name","code","date","service","staff","consent"}
# column titles of the common programs and of hand-made sheets, without spaces and accents
SKIP=("codicefiscale","cf","sesso","genere","partitaiva","piva")
EXTRA=("datanascita","datadinascita","nascita","compleanno","ultimavisita","dataultimavisita","primavisita","dataiscrizione","datainserimento","datacreazione","indirizzo","via","citta","cap","provincia","comune","professione")
GUESS=(("full_name",("nomecognome","cognomenome","nomeecognome","cognomeenome","nominativo","cliente","nomecliente","fullname","name")),
    ("first_name",("nome","firstname","givenname")),("last_name",("cognome","lastname","surname","familyname")),
    ("email",("email","mail","eposta","posta","emailaddress","indirizzoemail")),
    ("phone",("telefono","cellulare","cell","tel","mobile","phone","whatsapp","numero","recapito")),
    ("code",("codice","codicecliente","codcliente","cod","idcliente","id","clientid","customerid","numerocliente")),
    ("notes",("note","notes","annotazioni","osservazioni","nota")),
    ("date",("data","datatrattamento","datavisita","dataappuntamento","dataservizio","giorno","date","quando")),
    ("service",("trattamento","servizio","servizi","prestazione","service","treatment","lavoro","seduta")),
    ("staff",("operatore","operatrice","collaboratore","collaboratrice","staff","dipendente","eseguitoda","artista")),
    ("details",("dettagli","notetecniche","notetrattamento","notecolore","notelavoro","formula","colore","descrizione","prodotti","details","ricetta")),
    ("consent",("consenso","consensomarketing","marketing","newsletter","privacymarketing","promozioni","consensonewsletter")))
YES={"si","sì","s","yes","y","x","1","true","vero","ok","consenso","acconsento","✓","✔"}

def fields(record,consent):
    return [f for f in FIELDS if (record or f[0] not in RECORD) and (consent or f[0]!="consent")]

def plain(title):
    t=unicodedata.normalize("NFKD",title).encode("ascii","ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]","",t)

def guess(head,record,consent):
    """What each column probably holds, from its title. Single-value fields go to the first column only."""
    out=[]
    for title in head:
        t=plain(title);field=""
        if t in SKIP or t.startswith("codicefiscale"):field=""
        elif t in EXTRA or any(t.startswith(x) for x in EXTRA):field="extra"
        else:
            field=next((f for f,names in GUESS if t in names),None)
            if field is None:field=next((f for f,names in GUESS if any(len(n)>=4 and t.startswith(n) for n in names)),"extra" if t else "")
        if field in RECORD and not record:field="extra"
        if field=="consent" and not consent:field=""
        if field in SINGLE and field in out:field="extra"
        out.append(field)
    if "first_name" in out or "last_name" in out:out=[("extra" if f=="full_name" else f) for f in out]
    return out

# ---------- reading the file
def read(raw):
    """Title row and data rows of a CSV or Excel file, every cell a string."""
    if raw[:2]==b"PK":rows=xlsx_rows(raw)
    elif raw[:8]==b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":raise BookingError("È un file Excel del vecchio formato (.xls): aprilo con Excel e salvalo come .xlsx o .csv.")
    else:rows=csv_rows(raw)
    rows=[[str(c).strip() for c in r[:MAX_COLUMNS]] for r in rows]
    rows=[r for r in rows if any(r)]
    # some programs put a title line ("Elenco clienti al ...") above the column titles
    rows=rows[next((i for i,r in enumerate(rows[:10]) if sum(1 for c in r if c)>=2),0):]
    if len(rows)<2:raise BookingError("Nel file non ci sono righe da importare: servono la riga dei titoli e almeno un cliente.")
    if len(rows)-1>MAX_ROWS:raise BookingError(f"Il file ha {len(rows)-1} righe: se ne importano al massimo {MAX_ROWS} alla volta, dividilo in più file.")
    width=max(len(r) for r in rows)
    head=[(rows[0][i] if i<len(rows[0]) else "") or f"Colonna {i+1}" for i in range(width)]
    return head,[r+[""]*(width-len(r)) for r in rows[1:]]

def csv_rows(raw):
    if raw[:2] in (b"\xff\xfe",b"\xfe\xff"):text=raw.decode("utf-16")
    else:
        for encoding in ("utf-8-sig","cp1252","latin-1"):
            try:text=raw.decode(encoding);break
            except UnicodeDecodeError:continue
    text=text.replace("\x00","")
    top="\n".join(text.splitlines()[:20])
    delimiter=max(";,\t|",key=top.count) if any(d in top for d in ";,\t|") else ","
    try:return list(csv.reader(io.StringIO(text),delimiter=delimiter))
    except csv.Error:raise BookingError("File CSV non leggibile: salvalo di nuovo da Excel come CSV.")

M="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R="{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
def xlsx_rows(raw):
    """The first sheet of an .xlsx, without libraries: shared strings, inline strings, numbers as written."""
    try:
        z=zipfile.ZipFile(io.BytesIO(raw))
        if sum(i.file_size for i in z.infolist())>200*1024*1024:raise BookingError("Il file Excel è troppo grande una volta aperto.")
        xml=lambda name:ElementTree.fromstring(z.read(name))
        sheet=xml("xl/workbook.xml").find(f"{M}sheets/{M}sheet")
        rels={r.get("Id"):r.get("Target") for r in xml("xl/_rels/workbook.xml.rels")}
        target=rels[sheet.get(R)].lstrip("/")
        target=target if target.startswith("xl/") else "xl/"+target
        shared=[]
        if "xl/sharedStrings.xml" in z.namelist():
            shared=["".join(t.text or "" for t in si.findall(f"{M}t")+si.findall(f"{M}r/{M}t")) for si in xml("xl/sharedStrings.xml").findall(f"{M}si")]
        rows=[]
        for row in xml(target).iter(f"{M}row"):
            cells={};col=-1
            for c in row.findall(f"{M}c"):
                ref=re.match(r"[A-Z]+",c.get("r") or "")
                col=sum((ord(ch)-64)*26**i for i,ch in enumerate(reversed(ref[0])))-1 if ref else col+1
                if col>=MAX_COLUMNS:continue
                kind=c.get("t");v=c.find(f"{M}v");value=v.text if v is not None and v.text else ""
                if kind=="s":value=shared[int(value)] if value else ""
                elif kind=="inlineStr":value="".join(t.text or "" for t in c.iter(f"{M}t"))
                elif kind=="e":value=""
                elif kind in (None,"n") and value:value=number(value)
                cells[col]=value
            rows.append([cells.get(i,"") for i in range(max(cells)+1)] if cells else [])
        return rows
    except (zipfile.BadZipFile,KeyError,IndexError,ValueError,AttributeError,ElementTree.ParseError):
        raise BookingError("File Excel non leggibile: aprilo e salvalo di nuovo come .xlsx o come .csv.")

def number(value):
    """3331234567 and not 3.331234567E9, 45000 and not 45000.0: Excel keeps numbers as floats."""
    f=float(value)
    return str(int(f)) if f.is_integer() and abs(f)<1e15 else value

# ---------- the sealed copy between the steps
def folder():
    path=getattr(settings,"IMPORT_DIR","") or os.path.join(tempfile.gettempdir(),"tdbs-import")
    os.makedirs(path,mode=0o700,exist_ok=True)
    return path
def stash(tenant,name,head,rows):
    """shortcut: a temporary file on this server, deleted after a day; several web servers would need shared storage."""
    for f in os.scandir(folder()):
        try:
            if f.stat().st_mtime<time.time()-86400:os.remove(f.path)
        except OSError:pass
    token=secrets.token_urlsafe(18)
    with open(os.path.join(folder(),token),"w",encoding="ascii") as fh:
        fh.write(encrypt(tenant,{"name":str(name)[:120],"head":head,"rows":rows},"import:"+token))
    return token
def unstash(tenant,token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{24}",token or ""):return None
    try:
        with open(os.path.join(folder(),token),encoding="ascii") as fh:return decrypt(tenant,fh.read(),"import:"+token)
    except Exception:return None  # gone, expired or sealed for another business: the page asks for the file again
def drop(token):
    if re.fullmatch(r"[A-Za-z0-9_-]{24}",token or ""):
        try:os.remove(os.path.join(folder(),token))
        except OSError:pass

def samples(rows,i,n=3):
    out=[]
    for r in rows:
        if r[i] and r[i] not in out:out.append(r[i][:40])
        if len(out)>=n:break
    return out

# ---------- what the import does
def line(v,limit):return re.sub(r"[\x00-\x1f\x7f]"," ",v).strip()[:limit]
def lines(v):return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]","",v.replace("\r\n","\n").replace("\r","\n")).strip()
def name(v):
    v=line(v,100)
    return v.title() if v.isupper() else v  # ROSSI MARIA from the old programs becomes Rossi Maria

def day(value,today):
    """31/12/2024, 31-12-24, 2024-12-31, 2024/12/31 or an Excel date number. Day before month, as in Italy."""
    v=value.strip()
    if re.fullmatch(r"\d{5}(\.\d+)?",v):
        n=float(v)
        return date(1899,12,30)+timedelta(days=int(n)) if 20000<n<80000 else None
    m=re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})",v)
    if m:y,mo,d=map(int,m.groups())
    else:
        m=re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})\b",v)
        if not m:return None
        d,mo,y=map(int,m.groups())
        if y<100:y+=2000 if y<=today.year%100 else 1900
    try:return date(y,mo,d)
    except ValueError:return None

def plan(tenant,head,rows,mapping,record=True,consent=False):
    """Everything the import would do, computed in memory: the preview shows it, apply() writes it."""
    if not {"first_name","last_name","full_name","phone","email","code"}&set(mapping):
        raise BookingError("Indica almeno la colonna del nome, del telefono, dell'email o del codice cliente.")
    tz=ZoneInfo(tenant.timezone);today=timezone.now().astimezone(tz).date()
    since=(timezone.now()-timedelta(days=tenant.retention_days)).astimezone(tz).date()
    emails,phones,codes,names={},{},{},{}
    def learn(c):
        d=c.d
        if d.get("email"):emails.setdefault(d["email"],c)
        if d.get("phone"):phones.setdefault(d["phone"],c)
        if d.get("import_code"):codes.setdefault(d["import_code"],c)
        first,last=d.get("first_name",""),d.get("last_name","")
        for full in {f"{first} {last}".strip().casefold(),f"{last} {first}".strip().casefold()}:
            if full and c not in names.setdefault(full,[]):names[full].append(c)
    cards=[]
    for c in Contact.objects.exclude(email_index__startswith=ERASED):
        c.d=c.data;c.new=False;c.wanted=False;learn(c);cards.append(c)
    taken={c.email_index for c in cards}
    record=record and bool(RECORD&set(mapping))
    known=set()
    if record:
        for t in Treatment.objects.all():
            d=t.data;known.add((t.contact_id,t.date,d.get("service","").casefold(),d.get("staff","").casefold(),d.get("details","")))
    report=Counter();problems={};sample=[];created=[];changed={};treatments=[]
    def problem(n,reason):problems.setdefault(reason,[]).append(n)
    for n,row in enumerate(rows,start=2):
        got={}
        for i,f in enumerate(mapping):
            if f and i<len(row) and row[i].strip():got.setdefault(f,[]).append((head[i],row[i]))
        one=lambda f:got[f][0][1] if f in got else ""
        first,last=name(one("first_name")),name(one("last_name"))
        if not first and not last:first=name(one("full_name"))
        if not first and last:first,last=last,""
        found_emails,found_phones=[],[]
        for _,v in got.get("email",[]):
            for part in re.split(r"[\s,;/]+",v):
                part=part.strip().casefold()
                if not part:continue
                try:
                    if len(part)>254:raise ValidationError("")
                    validate_email(part)
                    if part not in found_emails:found_emails.append(part)
                except ValidationError:report["bad_email"]+=1
        for _,v in got.get("phone",[]):
            for part in re.split(r"\s*[,;/|]\s*|\s{2,}",v):
                p=normalize_phone(part)
                if p:
                    if p not in found_phones:found_phones.append(p)
                elif sum(ch.isdigit() for ch in part)>=6:report["bad_phone"]+=1
        email=found_emails[0] if found_emails else "";phone=found_phones[0] if found_phones else ""
        code=line(one("code"),60)
        note=[(h+": " if mapping.count("notes")>1 else "")+lines(v) for h,v in got.get("notes",[])]+[h+": "+lines(v) for h,v in got.get("extra",[])]
        # find the card: email, then phone (two different emails are two people), the old code, the name alone
        card=emails.get(email) if email else None
        if not card:
            for p in found_phones:
                c=phones.get(p)
                if c and not (email and c.d.get("email") and c.d["email"]!=email):card=c;break
        if not card and code:card=codes.get(code)
        if not card and not email and not phone and first:
            same=names.get(f"{first} {last}".strip().casefold(),[])
            if len(same)>1:problem(n,"più clienti con questo nome e nessun telefono, email o codice per distinguerli");continue
            card=same[0] if same else None
        if not card:
            if not email and not phone:
                problem(n,"cliente da creare senza telefono né email" if first else "riga senza nome, telefono, email o codice");continue
            if not first:problem(n,"cliente da creare senza nome");continue
            key=email_index(tenant,email) if email else phone_index(tenant,phone)
            if key in taken:problem(n,"telefono o email già usati da una scheda cancellata o in conflitto");continue
            card=Contact(tenant=tenant,email_index=key);taken.add(key)
            card.d={"first_name":first,"last_name":last,"email":email,"phone":phone};card.new=True;card.wanted=False
            if code:card.d["import_code"]=code
            created.append(card);cards.append(card);learn(card);status="nuovo"
        else:
            d=card.d;before=dict(d)
            if last and not d.get("last_name") and last.casefold() not in d.get("first_name","").casefold():d["last_name"]=last
            if email and not d.get("email"):
                # a card without email is keyed by its phone: with the email it moves to the email key, as update() does
                key=email_index(tenant,email)
                if key not in taken:d["email"]=email;taken.discard(card.email_index);card.email_index=key;taken.add(key)
            if phone and not d.get("phone"):d["phone"]=phone
            if code and not d.get("import_code"):d["import_code"]=code
            learn(card)
            if d!=before and not card.new:changed[card.pk]=card
            status="nuovo" if card.new else "già presente"
        note+=["Altra email: "+e for e in found_emails if e!=card.d.get("email")]+["Altro telefono: "+p for p in found_phones if p!=card.d.get("phone")]
        if note:
            old=card.d.get("staff_notes","")
            add=[x for x in dict.fromkeys(note) if x and x not in old]
            if add:
                card.d["staff_notes"]=((old+"\n") if old else "")+"\n".join(add)
                card.d["staff_notes"]=card.d["staff_notes"][:4000]
                if not card.new:changed[card.pk]=card
        if consent and one("consent").strip().casefold().rstrip("'.") in YES:card.wanted=True
        entry=""
        if record and RECORD&set(got):
            when=day(one("date"),today) if one("date") else None
            service=line(one("service"),120);staff=line(one("staff"),100)
            details="\n".join((h+": " if mapping.count("details")>1 else "")+lines(v) for h,v in got.get("details",[]))[:4000]
            if not when:problem(n,"trattamento senza una data valida, per esempio 31/12/2024")
            elif when>today:problem(n,"trattamento con la data nel futuro")
            elif not service:problem(n,"trattamento senza scritto cosa è stato fatto")
            elif when<since:report["too_old"]+=1
            else:
                k=(card.pk,when,service.casefold(),staff.casefold(),details)
                if k in known:report["same_treatment"]+=1
                else:known.add(k);treatments.append((card,{"date":when,"service":service,"staff":staff,"details":details}));entry=f"{when:%d/%m/%Y} · {service}"
        if len(sample)<8:sample.append({"line":n,"name":f"{card.d.get('first_name','')} {card.d.get('last_name','')}".strip(),"phone":card.d.get("phone",""),"email":card.d.get("email",""),"status":status,"entry":entry})
    imported=set(ConsentEvent.objects.filter(kind="imported").values_list("contact_id",flat=True))
    consents=[]
    for c in cards:
        if not c.wanted:continue
        if not c.d.get("email"):report["consent_no_email"]+=1
        elif c.consent=="revoked" or c.suppressed:report["consent_revoked"]+=1
        elif c.consent=="yes" or c.pk in imported:report["consent_known"]+=1
        else:consents.append(c)
    report=dict(report,created=len(created),updated=len(changed),treatments=len(treatments),consents=len(consents),
        problems=sum(len(v) for v in problems.values()),rows=len(rows))
    return {"created":created,"changed":list(changed.values()),"treatments":treatments,"consents":consents,
        "report":report,"problems":[(reason,numbers) for reason,numbers in problems.items()],"sample":sample,"since":since}

def apply(tenant,p,actor,ask=False,batch=""):
    """Write a plan. Call it inside a transaction holding the business lock, as every writer of contacts."""
    from .privacy import record as consent_event
    who=str(getattr(actor,"pk","") or "")
    for c in p["created"]+p["changed"]:c.pii=encrypt(tenant,c.d,"contact:"+str(c.pk))
    Contact.objects.bulk_create(p["created"],batch_size=500)
    Contact.objects.bulk_update(p["changed"],["pii","email_index"],batch_size=500)
    rows=[]
    for c,d in p["treatments"]:
        t=Treatment(tenant=tenant,contact=c,date=d["date"],author=who)
        t.pii=encrypt(tenant,{k:d[k] for k in ("service","staff","details")},"treatment:"+str(t.pk));rows.append(t)
    Treatment.objects.bulk_create(rows,batch_size=500)
    ConsentEvent.objects.bulk_create([ConsentEvent(tenant=tenant,contact=c,kind="imported",channel="import",actor=who,ref=batch) for c in p["consents"]],batch_size=500)
    Contact.objects.filter(pk__in=[c.pk for c in p["consents"]],consent="none").update(consent="pending")
    sent=0
    if ask:
        busy=set(ActionToken.objects.filter(kind="consent",used=False,expires__gt=timezone.now()).values_list("contact_id",flat=True))
        for c in p["consents"]:
            if c.pk in busy:continue
            token=action_token(tenant,"consent",contact=c,days=30)
            consent_event(tenant,c,"requested","import",actor,tenant.consent_text,batch)
            queue(tenant,{"to":c.d["email"],"subject":"Conferma iscrizione · "+tenant.name,"body":
                f"Nel nostro archivio risulta che hai scelto di ricevere novità e offerte di {tenant.name}.\n"
                f"Per continuare a riceverle conferma qui:\n{settings.PUBLIC_ORIGIN}/action/{tenant.slug}/{token}/\n"
                "Se non confermi non ti scriveremo più per questo. Se non ti interessa, ignora il messaggio."},c)
            sent+=1
    return {**p["report"],"requested":sent}

def summary(r):
    parts=[f"{r['created']} clienti nuovi",f"{r['updated']} schede completate"]
    if r.get("treatments"):parts.append(f"{r['treatments']} trattamenti")
    if r.get("consents"):parts.append(f"{r['consents']} consensi da confermare"+(f", {r['requested']} email di conferma in coda" if r.get("requested") else ""))
    return "Importazione fatta: "+", ".join(parts)+"."
