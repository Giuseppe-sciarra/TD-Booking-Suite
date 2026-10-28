"""Import of the client list and of the treatment history from the old program, as CSV or Excel (.xlsx).

The file is read once and sealed with the business key in a temporary file; the staff say what each column
holds, the preview runs the same code as the import without writing. An existing card is never overwritten:
it only gets its empty fields, new notes and new treatments. Marketing consents in the file are recorded as
"Importato" and stay to be confirmed: only the confirmation from the mailbox makes them valid. Whoever asked
for their data to be erased is not brought back.
"""
import csv,hashlib,hmac,io,os,re,secrets,tempfile,time,unicodedata,zipfile
from collections import Counter
from datetime import date,timedelta
from xml.etree import ElementTree
from zoneinfo import ZoneInfo
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone
from .booking import BookingError,normalize_phone,action_token,queue
from .clients import ERASED
from .crypto import seal,open_seal,tenant_key
from .models import Contact,Treatment,ConsentEvent,ActionToken

MAX_BYTES=20*1024*1024
MAX_ROWS=20000        # shortcut: one request does the whole import, about 20 s at this size; past it, a background job
MAX_COLUMNS=60        # Django accepts 100 fields per form: one select per column
MAX_CELL=4000
MAX_LINES=200000      # rows of the file, empty ones included: a crafted file stops here, not in the server's memory
MAX_TEXT=25_000_000   # characters kept from the whole file
KEEP_SECONDS=7200     # the sealed copy between the steps

FIELDS=[("","Non importare"),("first_name","Nome"),("last_name","Cognome"),("full_name","Nome e cognome insieme"),
    ("phone","Telefono"),("email","Email"),("code","Codice cliente del vecchio programma"),("notes","Note"),
    ("extra","Nelle note, con il titolo della colonna"),("date","Data del trattamento"),("service","Trattamento fatto"),
    ("staff","Operatore"),("details","Dettagli del trattamento"),("consent","Consenso marketing")]
RECORD={"date","service","staff","details"}
SINGLE={"first_name","last_name","full_name","code","date","service","staff","consent"}
# column titles of the common programs and of hand-made sheets, without spaces and accents
SKIP=("cf","sesso","genere","partitaiva","piva")
SKIP_START=("codicefiscale","codfisc")
EXTRA=("datanascita","datadinascita","nascita","compleanno","ultimavisita","dataultimavisita","primavisita","dataiscrizione","datainserimento","datacreazione","indirizzo","via","citta","cap","provincia","comune","professione","tessera")
GUESS=(("full_name",("nomecognome","cognomenome","nomeecognome","cognomeenome","nominativo","cliente","nomecliente","fullname","name")),
    ("first_name",("nome","firstname","givenname")),("last_name",("cognome","lastname","surname","familyname")),
    ("email",("email","mail","eposta","posta","emailaddress","indirizzoemail")),
    ("phone",("telefono","cellulare","cell","tel","mobile","phone","whatsapp","recapito")),
    ("code",("codice","codicecliente","codcliente","cod","idcliente","id","clientid","customerid","numerocliente")),
    ("notes",("note","notes","annotazioni","osservazioni","nota")),
    ("date",("data","datatrattamento","datavisita","dataappuntamento","dataservizio","giorno","date","quando")),
    ("service",("trattamento","servizio","servizi","prestazione","service","treatment","lavoro","seduta")),
    ("staff",("operatore","operatrice","collaboratore","collaboratrice","staff","dipendente","eseguitoda","artista")),
    ("details",("dettagli","notetecniche","notetrattamento","notecolore","notelavoro","formula","colore","descrizione","prodotti","details","ricetta")),
    # only these exact titles: "Consenso privacy" is the notice the client signed, not a yes to marketing
    ("consent",("consensomarketing","marketing","newsletter","privacymarketing","promozioni","consensonewsletter","consensopromozioni","consensocommerciale")))
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
        if t in SKIP or t.startswith(SKIP_START):field=""
        elif t in EXTRA or any(t.startswith(x) for x in EXTRA):field="extra"
        else:
            field=next((f for f,names in GUESS if t in names),None)
            if field is None:field=next((f for f,names in GUESS if f!="consent" and any(len(n)>=4 and t.startswith(n) for n in names)),"extra" if t else "")
        if field in RECORD and not record:field="extra"
        if field=="consent" and not consent:field=""
        if field in SINGLE and field in out:field="extra"
        out.append(field)
    if "first_name" in out or "last_name" in out:out=[("extra" if f=="full_name" else f) for f in out]
    return out

# ---------- reading the file
class Rows:
    """The rows of the file, with the limits that keep a crafted file from filling the server's memory."""
    def __init__(self):self.rows=[];self.lines=0;self.size=0
    def add(self,cells):
        self.lines+=1
        if self.lines>MAX_LINES:raise BookingError("Il file ha troppe righe, anche vuote: dividilo in più file.")
        cells=[str(c).strip()[:MAX_CELL] for c in cells[:MAX_COLUMNS]]
        if not any(cells):return
        if len(self.rows)>MAX_ROWS+10:raise BookingError(f"Il file ha più di {MAX_ROWS} righe: se ne importano al massimo {MAX_ROWS} alla volta, dividilo in più file.")
        self.size+=sum(map(len,cells))
        if self.size>MAX_TEXT:raise BookingError("Il file è troppo grande una volta letto: dividilo in più file.")
        self.rows.append(cells)

def read(raw):
    """Title row and data rows of a CSV or Excel file, every cell a string."""
    if raw[:2]==b"PK":rows=xlsx_rows(raw)
    elif raw[:8]==b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":raise BookingError("È un file Excel del vecchio formato (.xls): aprilo con Excel e salvalo come .xlsx o .csv.")
    else:rows=csv_rows(raw)
    # some programs put a title line ("Elenco clienti al ...") above the column titles
    rows=rows[next((i for i,r in enumerate(rows[:10]) if sum(1 for c in r if c)>=2),0):]
    if len(rows)<2:raise BookingError("Nel file non ci sono righe da importare: servono la riga dei titoli e almeno un cliente.")
    if len(rows)-1>MAX_ROWS:raise BookingError(f"Il file ha {len(rows)-1} righe: se ne importano al massimo {MAX_ROWS} alla volta, dividilo in più file.")
    width=max(len(r) for r in rows)
    head=[(rows[0][i] if i<len(rows[0]) else "") or f"Colonna {i+1}" for i in range(width)]
    return head,[r+[""]*(width-len(r)) for r in rows[1:]]

def delimiter(text):
    """The separator that splits the first lines into the same number of columns most often: a semicolon file
    with commas in its notes stays a semicolon file."""
    top=text[:65536].splitlines()[:30]
    best=(0,0,",")
    for d in ";,\t|":
        try:counts=Counter(len(r) for r in csv.reader(top,delimiter=d) if len(r)>1)
        except csv.Error:continue
        if counts:
            width,times=counts.most_common(1)[0]
            best=max(best,(times,width,d))
    return best[2]

def csv_rows(raw):
    if raw[:2] in (b"\xff\xfe",b"\xfe\xff"):text=raw.decode("utf-16",errors="replace")
    else:
        for encoding in ("utf-8-sig","cp1252","latin-1"):
            try:text=raw.decode(encoding);break
            except UnicodeDecodeError:continue
    text=text.replace("\x00","")
    out=Rows()
    try:
        for row in csv.reader(io.StringIO(text),delimiter=delimiter(text)):out.add(row)
    except csv.Error:raise BookingError("File CSV non leggibile: salvalo di nuovo da Excel come CSV.")
    return out.rows

M="{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R="{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
DATE_FORMATS={14,15,16,17,22}  # Excel's own date formats; 18-21 and 45-47 are times
def xlsx_rows(raw):
    """The first sheet of an .xlsx, without libraries and row by row: shared strings, inline strings, numbers
    as written, dates as 31/12/2024."""
    try:
        z=zipfile.ZipFile(io.BytesIO(raw))
        size={i.filename:i.file_size for i in z.infolist()}
        def part(name,limit):
            if size[name]>limit:raise BookingError("Il file Excel è troppo grande una volta aperto: dividilo in più file.")
            return z.open(name)
        xml=lambda name:ElementTree.parse(part(name,5*1024*1024)).getroot()
        sheet=xml("xl/workbook.xml").find(f"{M}sheets/{M}sheet")
        rels={r.get("Id"):r.get("Target") for r in xml("xl/_rels/workbook.xml.rels")}
        target=rels[sheet.get(R)].lstrip("/")
        target=target if target.startswith("xl/") else "xl/"+target
        shared=[];total=0
        if "xl/sharedStrings.xml" in size:
            for _,el in ElementTree.iterparse(part("xl/sharedStrings.xml",80*1024*1024)):
                if el.tag==f"{M}si":
                    text="".join(t.text or "" for t in el.findall(f"{M}t")+el.findall(f"{M}r/{M}t"))[:MAX_CELL]
                    total+=len(text);shared.append(text);el.clear()
                    if total>MAX_TEXT or len(shared)>2*MAX_LINES:raise BookingError("Il file è troppo grande una volta letto: dividilo in più file.")
        dates=date_styles(xml("xl/styles.xml")) if "xl/styles.xml" in size else set()
        out=Rows();parent=None
        for event,el in ElementTree.iterparse(part(target,200*1024*1024),events=("start","end")):
            if event=="start":
                if el.tag==f"{M}sheetData":parent=el
                continue
            if el.tag!=f"{M}row":continue
            cells={};col=-1
            for c in el.findall(f"{M}c"):
                ref=re.match(r"[A-Z]{1,3}",c.get("r") or "")
                col=sum((ord(ch)-64)*26**i for i,ch in enumerate(reversed(ref[0])))-1 if ref else col+1
                if col>=MAX_COLUMNS:continue
                kind=c.get("t");v=c.find(f"{M}v");value=v.text if v is not None and v.text else ""
                if kind=="s":value=shared[int(value)] if value else ""
                elif kind=="inlineStr":value="".join(t.text or "" for t in c.iter(f"{M}t"))
                elif kind=="e":value=""
                elif kind in (None,"n") and value:value=excel_date(value) if int(c.get("s") or 0) in dates else number(value)
                cells[col]=value
            out.add([cells.get(i,"") for i in range(max(cells)+1)] if cells else [])
            if parent is not None:parent.clear()  # the rows already read leave memory
        return out.rows
    except BookingError:raise
    except Exception:  # zip, deflate, XML or structure: whatever it is, the file cannot be read
        raise BookingError("File Excel non leggibile: aprilo e salvalo di nuovo come .xlsx o come .csv.")

def date_styles(styles):
    """The cell styles that show a date: Excel's own date formats and the custom ones with day or year."""
    custom={int(n.get("numFmtId")):n.get("formatCode","") for n in styles.iter(f"{M}numFmt")}
    def is_date(fid):
        if fid in custom:return bool(re.search(r"[dy]",re.sub(r'"[^"]*"|\[[^\]]*\]|\\.',"",custom[fid]).lower()))
        return fid in DATE_FORMATS
    xfs=styles.find(f"{M}cellXfs")
    return {i for i,xf in enumerate(xfs.findall(f"{M}xf")) if is_date(int(xf.get("numFmtId") or 0))} if xfs is not None else set()

def excel_date(value):
    n=float(value)
    return (date(1899,12,30)+timedelta(days=int(n))).strftime("%d/%m/%Y") if 0<n<2958466 else number(value)

def number(value):
    """3331234567 and not 3.331234567E9, 45000 and not 45000.0: Excel keeps numbers as floats."""
    f=float(value)
    return str(int(f)) if f.is_integer() and abs(f)<1e15 else value

# ---------- the sealed copy between the steps
def folder():
    path=getattr(settings,"IMPORT_DIR","") or os.path.join(tempfile.gettempdir(),"tdbs-import")
    os.makedirs(path,mode=0o700,exist_ok=True)
    return path
def purge():
    """Copies left by an import never finished go after two hours; also called by the nightly retention."""
    for f in os.scandir(folder()):
        try:
            if f.stat().st_mtime<time.time()-KEEP_SECONDS:os.remove(f.path)
        except OSError:pass
def stash(tenant,name,head,rows):
    """shortcut: a temporary file on this server; several web servers would need shared storage."""
    purge()
    token=secrets.token_urlsafe(18)
    with open(os.path.join(folder(),token),"w",encoding="ascii") as fh:
        fh.write(seal({"name":str(name)[:120],"head":head,"rows":rows},tenant_key(tenant),f"{tenant.pk}:import:{token}"))
    return token
def unstash(tenant,token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{24}",token or ""):return None
    purge()
    try:
        with open(os.path.join(folder(),token),encoding="ascii") as fh:return open_seal(fh.read(),tenant_key(tenant),f"{tenant.pk}:import:{token}")
    except Exception:return None  # gone, expired or sealed for another business: the page asks for the file again
def drop(token):
    if re.fullmatch(r"[A-Za-z0-9_-]{24}",token or ""):
        try:os.remove(os.path.join(folder(),token))
        except OSError:pass

def samples(rows,i,n=3):
    out=[]
    for r in rows:
        if r[i] and r[i][:40] not in out:out.append(r[i][:40])
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

class Keys:
    """The business key unwrapped once for the whole file: the same seals and indexes as core.crypto and
    core.clients, without unwrapping it again for every card."""
    def __init__(self,tenant):
        self.tenant=tenant;self.key=tenant_key(tenant)
        self.mac=HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=b"email").derive(self.key)
    def open(self,obj,label):return open_seal(obj.pii,self.key,f"{self.tenant.pk}:{label}:{obj.pk}")
    def seal(self,data,label,pk):return seal(data,self.key,f"{self.tenant.pk}:{label}:{pk}")
    def email(self,e):return hmac.new(self.mac,e.casefold().encode(),hashlib.sha256).hexdigest()
    def phone(self,p):return hmac.new(self.mac,("tel:"+p).encode(),hashlib.sha256).hexdigest()

def plan(tenant,head,rows,mapping,record=True,consent=False,lock=False):
    """Everything the import would do, computed in memory: the preview shows it, apply() writes it.
    lock=True for the import itself: the cards are read FOR UPDATE, so nobody edits them in between."""
    if not {"first_name","last_name","full_name","phone","email","code"}&set(mapping):
        raise BookingError("Indica almeno la colonna del nome, del telefono, dell'email o del codice cliente.")
    tz=ZoneInfo(tenant.timezone);today=timezone.now().astimezone(tz).date()
    since=(timezone.now()-timedelta(days=tenant.retention_days)).astimezone(tz).date()
    keys=Keys(tenant)
    emails,phones,codes,names={},{},{},{}
    def learn(c):
        d=c.d
        if d.get("email"):emails.setdefault(d["email"],c)
        if d.get("phone") and c not in phones.setdefault(d["phone"],[]):phones[d["phone"]].append(c)
        if d.get("import_code"):codes.setdefault(d["import_code"],c)
        for full in fulls(d):
            if c not in names.setdefault(full,[]):names[full].append(c)
    cards=[];forgotten=set()
    existing=Contact.objects.order_by("created","pk")
    for c in (existing.select_for_update() if lock else existing):
        c.d=keys.open(c,"contact")
        if c.email_index.startswith(ERASED):forgotten.update(c.d.get("erased_keys",[]));continue
        c.new=False;c.wanted=False;learn(c);cards.append(c)
    taken={c.email_index for c in cards}
    record=record and bool(RECORD&set(mapping))
    known=set()
    if record:
        for t in Treatment.objects.all():
            d=keys.open(t,"treatment");known.add((t.contact_id,t.date,d.get("service","").casefold(),d.get("staff","").casefold(),d.get("details","")))
    report=Counter();problems={};issues={};sample=[];created=[];changed={};treatments=[]
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
        code=line(one("code"),60)
        note=[(h+": " if mapping.count("notes")>1 else "")+lines(v) for h,v in got.get("notes",[])]+[h+": "+lines(v) for h,v in got.get("extra",[])]
        row_names={f"{first} {last}".strip().casefold(),f"{last} {first}".strip().casefold()}-{""}
        # find the card: any of its emails, then phone (two different emails are two people; a phone shared in
        # the family needs the name to tell who), the old code, the name alone when the row has nothing else
        card=next((emails[e] for e in found_emails if e in emails),None)
        email=card.d["email"] if card else (found_emails[0] if found_emails else "")
        phone=found_phones[0] if found_phones else ""
        differs=lambda c:bool(email and c.d.get("email") and c.d["email"]!=email)
        if not card:
            ambiguous=False
            for p in found_phones:
                same=[c for c in phones.get(p,[]) if not differs(c)]
                if len(same)>1:
                    named=[c for c in same if row_names&set(fulls(c.d))]
                    if len(named)!=1:ambiguous=True;continue
                    same=named
                if same:card=same[0];break
            if not card and ambiguous:problem(problems,n,"telefono di più clienti e nome che non basta a dire di chi è");continue
        if not card and code:
            c=codes.get(code)
            card=c if c and not differs(c) else None
        if not card and not email and not phone:
            if code:problem(problems,n,"codice cliente che non c'è e nessun telefono o email: importa prima l'anagrafica");continue
            same=[c for name_ in row_names for c in names.get(name_,[])]
            same=list(dict.fromkeys(same))
            if len(same)>1:problem(problems,n,"più clienti con questo nome e nessun telefono, email o codice per distinguerli");continue
            card=same[0] if same else None
        if not card:
            if not email and not phone:
                problem(problems,n,"cliente da creare senza telefono né email" if first else "riga senza nome, telefono, email o codice");continue
            if not first:problem(problems,n,"cliente da creare senza nome");continue
            if forgotten&({keys.email(e) for e in found_emails}|{keys.phone(p) for p in found_phones}):
                problem(problems,n,"cliente che ha chiesto di cancellare i suoi dati: non si reimporta");continue
            key=keys.email(email) if email else keys.phone(phone)
            if key in taken:problem(problems,n,"telefono o email già usati da un altro cliente");continue
            card=Contact(tenant=tenant,email_index=key);taken.add(key)
            card.d={"first_name":first,"last_name":last,"email":email,"phone":phone};card.new=True;card.wanted=False
            if code:card.d["import_code"]=code
            created.append(card);cards.append(card);learn(card);status="nuovo"
        else:
            d=card.d;before=dict(d)
            if last and not d.get("last_name") and last.casefold() not in d.get("first_name","").casefold():d["last_name"]=last
            if email and not d.get("email"):
                # a card without email is keyed by its phone: with the email it moves to the email key, as update() does
                key=keys.email(email)
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
                card.d["staff_notes"]=(((old+"\n") if old else "")+"\n".join(add))[:4000]
                if not card.new:changed[card.pk]=card
        if consent and one("consent").strip().casefold().rstrip("'.") in YES:card.wanted=True
        entry=""
        if record and RECORD&set(got):
            when=day(one("date"),today) if one("date") else None
            service=line(one("service"),120);staff=line(one("staff"),100)
            details="\n".join((h+": " if mapping.count("details")>1 else "")+lines(v) for h,v in got.get("details",[]))[:4000]
            if not when:problem(issues,n,"data non valida, per esempio 31/12/2024")
            elif when>today:problem(issues,n,"data nel futuro")
            elif not service:problem(issues,n,"non c'è scritto cosa è stato fatto")
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
        problems=sum(len(v) for v in problems.values()),issues=sum(len(v) for v in issues.values()),rows=len(rows))
    return {"created":created,"changed":list(changed.values()),"treatments":treatments,"consents":consents,"keys":keys,
        "report":report,"problems":list(problems.items()),"issues":list(issues.items()),"sample":sample,"since":since}

def fulls(d):
    first,last=d.get("first_name",""),d.get("last_name","")
    return {f"{first} {last}".strip().casefold(),f"{last} {first}".strip().casefold()}-{""}

def problem(where,n,reason):where.setdefault(reason,[]).append(n)

def apply(tenant,p,actor,ask=False,batch=""):
    """Write a plan made with lock=True, inside the transaction that holds the business lock."""
    from .privacy import record as consent_event
    keys=p["keys"];who=str(getattr(actor,"pk","") or "")
    for c in p["created"]+p["changed"]:c.pii=keys.seal(c.d,"contact",c.pk)
    Contact.objects.bulk_create(p["created"],batch_size=500)
    Contact.objects.bulk_update(p["changed"],["pii","email_index"],batch_size=500)
    rows=[]
    for c,d in p["treatments"]:
        t=Treatment(tenant=tenant,contact=c,date=d["date"],author=who)
        t.pii=keys.seal({k:d[k] for k in ("service","staff","details")},"treatment",t.pk);rows.append(t)
    Treatment.objects.bulk_create(rows,batch_size=500)
    ConsentEvent.objects.bulk_create([ConsentEvent(tenant=tenant,contact=c,kind="imported",channel="import",actor=who,ref=batch) for c in p["consents"]],batch_size=500)
    Contact.objects.filter(pk__in=[c.pk for c in p["consents"]],consent="none",suppressed=False).update(consent="pending")
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
