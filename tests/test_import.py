"""Import of clients and treatment history from the old program (CSV and Excel)."""
import io,re,time as wallclock,zipfile
from datetime import timedelta
from zoneinfo import ZoneInfo
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,BookingError
from core.crypto import decrypt
from core import importer
from tests.test_core import sample,payload,client_as
from tests.test_phase2 import member

pytestmark=pytest.mark.django_db

HEAD=["Codice","Nome","Cognome","Cellulare","Email","Data","Servizio","Operatore","Formula","Consenso marketing","Compleanno"]
def rows(today):
    recent=(today-timedelta(days=40)).strftime("%d/%m/%Y")
    return [
        ["1","MARIO","ROSSI","333 123 4567","Mario@Example.invalid",recent,"Colore","Anna","6.3","si","12/05/1980"],  # the client already there
        ["2","Giulia","Bianchi","338 765 4321","giulia@example.invalid",(today-timedelta(days=30)).isoformat(),"Taglio","","","no",""],
        ["2","","","","",(today-timedelta(days=30)).strftime("%d/%m/%Y"),"Taglio","","","",""],  # same treatment, by code
        ["3","Luca","Verdi","","","01/01/2026","Piega","","","",""],
        ["4","Sara","Neri","339 1111111 / 06 1234567","non-una-email",recent,"Colore","","","x",""],
        ["","Giulia","Bianchi","","","01/01/2015","Colore","","","",""],  # by name, older than the retention
        ["5","Anna","Futura","3401234567","",(today+timedelta(days=3)).strftime("%d/%m/%Y"),"Colore","","","",""],
    ]

def csv_bytes(head,body,encoding="cp1252"):
    return ("\r\n".join(";".join(r) for r in [head]+body)+"\r\n").encode(encoding)

def xlsx_bytes(table,styles=None,sheet_xml=None):
    """A minimal .xlsx as Excel writes it: shared strings for text, numbers as numbers, ("date",n) a date cell."""
    strings=[];out=io.BytesIO()
    def cell(ref,v):
        if isinstance(v,tuple):return f'<c r="{ref}" s="1"><v>{v[1]}</v></c>'
        if isinstance(v,(int,float)):return f'<c r="{ref}"><v>{v}</v></c>'
        if v not in strings:strings.append(v)
        return f'<c r="{ref}" t="s"><v>{strings.index(v)}</v></c>'
    sheet="".join(f'<row r="{i+1}">'+"".join(cell(chr(65+j)+str(i+1),v) for j,v in enumerate(r) if v!="")+"</row>" for i,r in enumerate(table))
    ns='xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(out,"w") as z:
        z.writestr("xl/workbook.xml",f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Clienti" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml",f'<sst {ns}>'+"".join(f"<si><t>{s}</t></si>" for s in strings)+"</sst>")
        z.writestr("xl/worksheets/sheet1.xml",sheet_xml or f'<worksheet {ns}><sheetData>{sheet}</sheetData></worksheet>')
        z.writestr("xl/styles.xml",styles or f'<styleSheet {ns}><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    return out.getvalue()

def test_read_csv_and_xlsx():
    head,body=importer.read(csv_bytes(["Nome","Cognome","Cellulare","E-mail"],[["María","ROSSI","333 123 4567","M@X.it"],["","","",""]]))
    assert head==["Nome","Cognome","Cellulare","E-mail"] and body==[["María","ROSSI","333 123 4567","M@X.it"]]
    head,body=importer.read("Nome,Telefono\nAnna,3331234567\n".encode("utf-8-sig"))
    assert body==[["Anna","3331234567"]]
    head,body=importer.read("Elenco clienti al 27/10/2026\n\nNome;Telefono;Note\nAnna;333 1234567;viene il sabato, di mattina\n".encode())
    assert head==["Nome","Telefono","Note"] and body==[["Anna","333 1234567","viene il sabato, di mattina"]]
    head,body=importer.read(xlsx_bytes([["Nome","Cellulare","Data"],["Anna",3331234567,45658],["Bruno","","31/12/2024"]]))
    assert head==["Nome","Cellulare","Data"] and body==[["Anna","3331234567","45658"],["Bruno","","31/12/2024"]]
    today=timezone.now().date()
    assert importer.day("45658",today).isoformat()=="2025-01-01" and importer.day("31/12/24",today).isoformat()=="2024-12-31"
    assert importer.day("2024-12-31 10:30",today).isoformat()=="2024-12-31" and importer.day("31/02/2024",today) is None and importer.day("2024/12/31",today).isoformat()=="2024-12-31"
    for bad in (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1old",b"PKnot a zip",b"Nome\n"):
        with pytest.raises(BookingError):importer.read(bad)

def test_crafted_and_odd_files_end_in_a_message():
    import time as clock
    started=clock.time()
    with pytest.raises(BookingError,match="troppe righe"):importer.read(b"Nome;Telefono\n"+b"\n"*(importer.MAX_LINES+5))
    ns='xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    empty=f'<worksheet {ns}><sheetData>'+"<row/>"*(importer.MAX_LINES+5)+'</sheetData></worksheet>'
    with pytest.raises(BookingError,match="troppe righe"):importer.read(xlsx_bytes([["Nome"]],sheet_xml=empty))
    assert clock.time()-started<20
    assert importer.read(b"\xff\xfeN\x00o\x00m\x00e\x00,\x00T\x00e\x00l\x00\n\x00A\x00,\x001\x00\n")[0][0]=="Nome"  # odd byte at the end
    broken=bytearray(xlsx_bytes([["Nome","Telefono"],["Anna","333"]]))
    at=broken.find(b"sheetData");broken[at-40:at+40]=b"\x00"*80
    with pytest.raises(BookingError,match="non leggibile"):importer.read(bytes(broken))
    # a semicolon file with commas in its notes stays a semicolon file
    head,body=importer.read("Nome;Telefono;Note\nAnna;333 1234567;viene il sabato, di mattina, con la figlia, a volte\n".encode())
    assert head==["Nome","Telefono","Note"] and body[0][2].startswith("viene il sabato")
    # an Excel date cell, outside the treatment date too, reads as a date and not as its serial number
    head,body=importer.read(xlsx_bytes([["Nome","Data di nascita"],["Anna",("date",29354)]]))
    assert body==[["Anna","13/05/1980"]]

def test_guess_from_titles():
    head=["Codice","Nominativo","Cellulare","Telefono casa","E-mail","Data di nascita","Data","Servizio","Operatrice","Formula colore","Consenso newsletter","Codice fiscale","Città"]
    assert importer.guess(head,True,True)==["code","full_name","phone","phone","email","extra","date","service","staff","details","consent","","extra"]
    # no client record (restaurants, hotels): the treatment columns go to the notes; no consent without the permission
    assert importer.guess(["Nome","Cognome","Data","Consenso marketing"],False,False)==["first_name","last_name","extra",""]
    # the privacy notice is not marketing consent; the tax code is not kept; a card number is not a phone
    assert importer.guess(["Nome","Email","Consenso privacy","Consenso marketing","Cod. Fiscale","Numero tessera"],True,True)==["first_name","email","extra","consent","","extra"]

def test_plan_matches_merges_and_never_overwrites(sample):
    t,o,s=sample
    t.business_type="parrucchiere";t.save()
    today=timezone.now().astimezone(ZoneInfo(t.timezone)).date()
    with scope(t):
        b,_=create_booking(t,payload(s))  # Mario Rossi, mario@example.invalid, +393331234567
        mario=Contact.objects.get(pk=b.contact_id)
        mapping=importer.guess(HEAD,True,True)
        assert mapping==["code","first_name","last_name","phone","email","date","service","staff","details","consent","extra"]
        p=importer.plan(t,HEAD,rows(today),mapping,True,True)
        r=p["report"]
        assert (r["created"],r["updated"],r["treatments"],r["same_treatment"],r["too_old"],r["consents"])==(3,1,3,1,1,1)
        assert r["bad_email"]==1 and r["consent_no_email"]==1
        assert dict(p["problems"])=={"codice cliente che non c'è e nessun telefono o email: importa prima l'anagrafica":[5]}
        assert dict(p["issues"])=={"data nel futuro":[8]}  # Anna Futura comes in, her treatment does not
        assert Contact.objects.count()==1 and not Treatment.objects.exists()  # the preview writes nothing
        done=importer.apply(t,p,None,batch="prova")
        assert done["created"]==3
        mario.refresh_from_db();d=mario.data
        # the card already there keeps its data and gets what it lacked
        assert d["first_name"]=="Mario" and d["email"]=="mario@example.invalid" and d["import_code"]=="1" and "Compleanno: 12/05/1980" in d["staff_notes"]
        assert mario.consent=="pending" and ConsentEvent.objects.filter(contact=mario,kind="imported",channel="import",ref="prova").exists()
        sara=next(c for c in Contact.objects.all() if c.data["first_name"]=="Sara")
        assert sara.data["phone"]=="+393391111111" and "Altro telefono: +39061234567" in sara.data["staff_notes"] and sara.consent=="none"
        entries=[(x.contact_id,x.data["service"],x.data["details"]) for x in Treatment.objects.all()]
        assert (mario.pk,"Colore","6.3") in entries and len(entries)==3 and "6.3" not in Treatment.objects.get(contact=mario).pii
        # the same file again: nothing new, nothing doubled
        again=importer.plan(t,HEAD,rows(today),mapping,True,True)["report"]
        assert (again["created"],again["updated"],again["treatments"],again["consents"],again["consent_known"])==(0,0,0,0,1)
        assert again["same_treatment"]==4

def test_import_page_flow_permissions_and_consent_email(sample):
    t,o,s=sample
    t.business_type="parrucchiere";t.marketing_enabled=True;t.save()
    today=timezone.now().astimezone(ZoneInfo(t.timezone)).date()
    owner,u=client_as(t)
    url=f"/t/{t.slug}/contacts/import/"
    assert "Importa" in owner.get(f"/t/{t.slug}/contacts/").content.decode()
    r=owner.post(url,{"action":"upload","file":SimpleUploadedFile("clienti.xlsx",xlsx_bytes([HEAD]+rows(today)))})
    assert r.status_code==302
    page=owner.get(url).content.decode()
    assert "clienti.xlsx" in page and "Consenso marketing" in page and '<option value="phone" selected>' in page
    form={f"col{i}":f for i,f in enumerate(importer.guess(HEAD,True,True))}
    page=owner.post(url,{**form,"action":"preview","ask":"1"}).content.decode()
    assert "Anteprima" in page and "Rossi" in page and "Data nel futuro" in page and "non vengono importati" in page
    checked=re.search(r'name="checked" value="([^"]*)"',page)[1]
    # columns changed after the preview: back to the preview, nothing written
    page=owner.post(url,{**form,"col10":"","action":"import","ask":"1","checked":checked}).content.decode()
    assert "controlla di nuovo" in page
    with scope(t):assert not Contact.objects.exists()
    r=owner.post(url,{**form,"action":"import","ask":"1","checked":checked},follow=True).content.decode()
    assert "Importazione fatta: 4 clienti nuovi" in r and "1 email di conferma in coda" in r
    with scope(t):
        assert Contact.objects.count()==4 and Treatment.objects.count()==3
        assert Audit.objects.get(action="contacts.imported").detail["created"]==4
        mario=next(c for c in Contact.objects.all() if c.data["first_name"]=="Mario")
        delivery=Delivery.objects.get(contact=mario)
        body=decrypt(t,delivery.payload,"delivery:"+str(delivery.pk))["body"]
    token=re.search(r"/action/[^/]+/([^/]+)/",body)[1]
    Client().post(f"/action/{t.slug}/{token}/")
    with scope(t):
        mario.refresh_from_db()
        assert mario.consent=="yes" and decrypt(t,mario.consent_proof,"consent:"+str(mario.pk))["source"]=="import"
    assert owner.get(url).content.decode().count("Scegli il file")==1  # the sealed copy is gone
    # the columns previewed for one file never import another one, uploaded meanwhile in a second tab
    owner.post(url,{"action":"upload","file":SimpleUploadedFile("a.csv",csv_bytes(["Nome","Email","Consenso marketing"],[["Bea","bea@example.invalid","no"]]))})
    page=owner.post(url,{"col0":"first_name","col1":"email","col2":"consent","action":"preview"}).content.decode()
    checked=re.search(r'name="checked" value="([^"]*)"',page)[1]
    owner.post(url,{"action":"upload","file":SimpleUploadedFile("b.csv",csv_bytes(["Nome","Email","Visite"],[["Ugo","ugo@example.invalid","1"]]))})
    assert "controlla di nuovo" in owner.post(url,{"col0":"first_name","col1":"email","col2":"consent","action":"import","checked":checked}).content.decode()
    with scope(t):assert not ConsentEvent.objects.filter(kind="imported",contact__in=[c for c in Contact.objects.all() if c.data["first_name"]=="Ugo"]).exists() and Contact.objects.count()==4
    # reception imports clients but not consents; marketing does not import
    desk,_=member(t,"reception")
    desk.post(url,{"action":"upload","file":SimpleUploadedFile("c.csv",csv_bytes(HEAD,rows(today)))})
    assert '<option value="consent"' not in desk.get(url).content.decode()
    shop,_=member(t,"marketing")
    assert shop.get(url).status_code==403 and "Importa</a>" not in shop.get(f"/t/{t.slug}/contacts/").content.decode()

def test_sealed_copy_belongs_to_its_business(sample):
    t,o,s=sample
    with scope(t):token=importer.stash(t,"a.csv",["Nome"],[["Anna"]])
    with scope(t):assert importer.unstash(t,token)["rows"]==[["Anna"]]
    assert importer.unstash(o,token) is None and importer.unstash(t,"../../etc/passwd") is None
    importer.drop(token)
    assert importer.unstash(t,token) is None

def test_who_is_who_erasure_codes_family_phones_and_second_emails(sample):
    from core.clients import email_index,phone_index
    from core.privacy import erase_contact
    t,o,s=sample
    t.business_type="parrucchiere";t.save()
    today=timezone.now().astimezone(ZoneInfo(t.timezone)).date();recent=(today-timedelta(days=10)).strftime("%d/%m/%Y")
    head=["Codice","Nome","Cognome","Cellulare","Email","Data","Servizio","Consenso marketing"]
    mapping=importer.guess(head,True,True)
    with scope(t):
        keys=importer.Keys(t)
        assert keys.email("A@B.it")==email_index(t,"a@b.it") and keys.phone("+393331234567")==phone_index(t,"+393331234567")
        # who asked to be erased does not come back with the old program's file
        importer.apply(t,importer.plan(t,head,[["","Anna","Neri","3330000001","anna@example.invalid","","","si"]],mapping,True,True,lock=True),None)
        anna=Contact.objects.get();erase_contact(t,anna,None)
        p=importer.plan(t,head,[["","Anna","Neri","333 0000001","","","","si"]],mapping,True,True)
        assert p["report"]["created"]==0 and "cancellare i suoi dati" in p["problems"][0][0]
        # a family phone: the name says whose treatment it is; without a matching name the row waits
        mother,_=create_booking(t,payload(s,first_name="Maria",last_name="Verdi",email="maria@example.invalid",phone="+393330000002"))
        daughter,_=create_booking(t,payload(s,hour=11,first_name="Sofia",last_name="Verdi",email="sofia@example.invalid",phone="+393330000002"))
        rows=[["","Sofia","Verdi","333 0000002","",recent,"Taglio",""],["","Luca","Verdi","333 0000002","",recent,"Barba",""]]
        p=importer.plan(t,head,rows,mapping,True,True)
        assert [c.pk for c,_ in p["treatments"]]==[daughter.contact_id] and dict(p["problems"])=={"telefono di più clienti e nome che non basta a dire di chi è":[3]}
        # a second email in the cell finds the card it belongs to
        p=importer.plan(t,head,[["","Maria","Verdi","","lavoro@example.invalid; maria@example.invalid","","",""]],mapping,True,True)
        assert p["report"]["created"]==0 and p["changed"][0].pk==mother.contact_id and "Altra email: lavoro@example.invalid" in p["changed"][0].d["staff_notes"]
        # a code that does not match is not rescued by the name: two people with one name stay two
        importer.apply(t,importer.plan(t,head,[["57","Mario","Rossi","3330000003","","","",""]],mapping,True,True,lock=True),None)
        p=importer.plan(t,head,[["102","Mario","Rossi","","",recent,"Colore",""]],mapping,True,True)
        assert not p["treatments"] and "codice cliente che non c'è" in p["problems"][0][0]
        p=importer.plan(t,head,[["57","","","","",recent,"Colore",""]],mapping,True,True)
        assert p["treatments"][0][0].d["first_name"]=="Mario"

@pytest.mark.django_db(transaction=True)
def test_a_card_saved_during_the_import_neither_deadlocks_nor_loses_its_notes():
    from threading import Event
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connection,connections,transaction
    from core.clients import update
    from core.models import audit
    if connection.vendor!="postgresql":pytest.skip("Richiede PostgreSQL reale")
    t=Tenant.objects.create(slug="salone-lock",name="Salone",business_type="parrucchiere")
    with scope(t):
        c=Contact(tenant=t,email_index=importer.Keys(t).email("anna@example.invalid"))
        c.pii=importer.Keys(t).seal({"first_name":"Anna","last_name":"","email":"anna@example.invalid","phone":""},"contact",c.pk);c.save()
    head=["Email","Note"];saved=Event()
    def staff():
        connections.close_all()
        with scope(t),transaction.atomic():
            card=Contact.objects.select_for_update().get(pk=c.pk)
            update(t,card,notes="allergica al nichel")
            saved.set();wallclock.sleep(1.5)  # the import starts meanwhile and must wait, not crash
            audit(t,None,"contact.notes",card.pk)
        connections.close_all()
    def importing():
        connections.close_all();saved.wait(5)
        with scope(t),transaction.atomic():
            Tenant.objects.select_for_update(no_key=True).get(pk=t.pk)
            done=importer.apply(t,importer.plan(t,head,[["anna@example.invalid","preferisce il sabato"]],["email","notes"],True,False,lock=True),None)
        connections.close_all()
        return done
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(staff);second=pool.submit(importing)
        first.result(timeout=30);done=second.result(timeout=30)
    with scope(t):notes=Contact.objects.get(pk=c.pk).data["staff_notes"]
    assert done["updated"]==1 and notes=="allergica al nichel\npreferisce il sabato"
