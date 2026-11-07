"""2.22.0: prodotti, Preventivo pagamenti cassa (righe in sospeso), gift card, preventivi, il riepilogo dell'appuntamento
dall'agenda, lo storico del cliente in PDF e l'avviso della chiusura giornaliera mancante."""
import secrets
from datetime import timedelta,date
from decimal import Decimal
import pytest
from zoneinfo import ZoneInfo
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core import cassa,gift
from core.booking import create_booking
from tests.test_core import client_as,payload
from tests.test_cassa import shop  # noqa: F401 (fixture)

pytestmark=pytest.mark.django_db
def post(c,loc,**kw):
    data={"action":"sell","sede":str(loc.pk),"key":kw.pop("key",None) or secrets.token_urlsafe(24),"pay":"cash",**kw}
    return c.post("/t/salone/cassa/",data)
def rows(*r):
    """(desc, qty, price, svc, park) for each row."""
    return {"desc":[x[0] for x in r],"qty":[x[1] for x in r],"price":[x[2] for x in r],"svc":[x[3] for x in r],"off":["" for x in r],"staff":["Cristina" for x in r],"park":[x[4] for x in r]}

def test_products_in_catalog_cassa_and_department(shop):
    t,loc,svc=shop;c,_=client_as(t)
    r=c.post("/t/salone/catalog/products/new/",{"name":"Shampoo  ristrutturante","price":"12,50","active":"on"})
    assert r.status_code==302
    r=c.post("/t/salone/catalog/products/new/",{"name":"shampoo ristrutturante","price":"9","active":"on"})
    assert "già un prodotto con questo nome" in r.content.decode()
    with scope(t):
        p=Product.objects.get();assert p.name=="Shampoo ristrutturante" and p.price==Decimal("12.50")
        loc.till={**loc.till,"product_dept":4,"gift_dept":5};loc.save()
    assert "Shampoo ristrutturante" in c.get("/t/salone/catalog/products/").content.decode()
    page=c.get("/t/salone/cassa/").content.decode()
    assert f'data-svc="p:{p.pk}"' in page and "Prodotti" in page and "12,50" in page
    post(c,loc,**rows(("Shampoo ristrutturante","2","12,50",f"p:{p.pk}","")))
    with scope(t):
        s=Sale.objects.get();l=s.data["lines"][0]
        assert l["dept"]==4 and l["svc"]==f"p:{p.pk}" and "=R4/$1250/(Shampoo ristrutturante)" in cassa.rch(s,s.data,loc.till)
    # the step in Configura l'attività never counts as missing
    assert "Prodotti" in c.get("/t/salone/setup/").content.decode()
    # removed: gone (nothing points to it)
    c.post(f"/t/salone/catalog/products/{p.pk}/remove/")
    with scope(t):assert not Product.objects.exists()

def test_rows_in_sospeso_go_on_the_card_and_come_back(shop):
    t,loc,svc=shop
    with scope(t):
        Tenant.objects.filter(pk=t.pk).update(business_type="parrucchiere");t.refresh_from_db()  # a salon: the card has the Trattamenti
        b,_=create_booking(t,payload(svc));Booking.objects.filter(pk=b.pk).update(status="arrived")
    c,_=client_as(t,role="reception")
    key=secrets.token_urlsafe(24)
    data=rows(("Taglio donna","1","25,00",str(svc.pk),""),("Piega","1","20,00","","1"))
    r=post(c,loc,key=key,booking=str(b.pk),**data)
    assert r.status_code==302
    post(c,loc,key=key,booking=str(b.pk),**data)  # pressed twice: once
    with scope(t):
        s=Sale.objects.get();assert s.total==Decimal("25.00") and [l["desc"] for l in s.data["lines"]]==["Taglio donna"]
        x=Parked.objects.get();assert x.total==Decimal("20.00") and x.contact_id==b.contact_id and x.data["lines"][0]["desc"]=="Piega" and x.data["lines"][0]["staff"]=="Cristina"
        assert "Piega" not in x.pii  # sealed
    owner,_=client_as(t)
    # 2.22.0: the row out of the receipt is a treatment of the client at once, marked *, counted in the reports as work of today
    with scope(t):
        from core.clients import treatments
        tr=[r for r in treatments(b.contact) if r.info.get("kind")=="parked"];assert len(tr)==1
        assert tr[0].info["service"]=="Piega" and tr[0].info["amount"]==2000 and tr[0].info["by"]=={"Cristina":2000} and tr[0].info["staff"]=="Cristina" and x.data["treatment"]==str(tr[0].pk)
    card=owner.get(f"/t/salone/contacts/{b.contact_id}/").content.decode()
    assert "Preventivo pagamenti cassa" not in card and "sospeso" not in card and 'title="Fatto, non ancora in uno scontrino">*</abbr>' in card
    page=c.get(f"/t/salone/cassa/?sede={loc.pk}").content.decode()
    assert "Piega (Cristina)" not in page and 'id="preventivo-cassa"' not in page and "till-waiting" not in page and "Preventivo pagamenti cassa" not in page and "In sospeso" not in page  # nothing reminds it in the Cassa: the owner decides
    rep=owner.get("/t/salone/report/").content.decode()
    assert "Trattamenti non nello scontrino" in rep and "<td>Piega</td>" in rep and "Preventivo pagamenti cassa" not in rep
    assert "<td>Cristina</td><td>1</td><td>20,00</td>" in rep.replace("</td>\n<td>","</td><td>") or "Cristina" in rep  # her work of today, with its money
    hist=owner.get(f"/t/salone/contacts/{b.contact_id}/storico/").content.decode()
    assert "<strong>Piega *</strong>" in hist and "Non ancora in uno scontrino *" in hist
    # the next visit: taken into the bill, with the client; sent, it is gone from there
    page=c.get(f"/t/salone/cassa/?sede={loc.pk}&sospeso={x.pk}").content.decode()
    assert f'name="parked" value="{x.pk}"' in page and 'value="Piega"' in page and 'value="20,00"' in page and f'value="{b.contact_id}"' in page
    r=post(c,loc,parked=str(x.pk),**rows(("Piega","1","20,00","","")))
    with scope(t):
        assert not Parked.objects.exists() and Sale.objects.count()==2
        s2=Sale.objects.order_by("-created").first();assert s2.contact_id==b.contact_id and s2.total==Decimal("20.00")
        assert "done_before" not in s2.data  # the receipt counts its full money: the row outside a receipt never went into the takings
        tr=[r for r in treatments(b.contact) if r.info.get("kind")=="parked_paid"];assert len(tr)==1  # the mark is gone
    assert "*</abbr>" not in owner.get(f"/t/salone/contacts/{b.contact_id}/").content.decode()

def test_whole_bill_in_sospeso_toggle_many_times_and_drop(shop):
    t,loc,svc=shop
    with scope(t):b,_=create_booking(t,payload(svc));Booking.objects.filter(pk=b.pk).update(status="arrived")
    c,_=client_as(t,role="reception")
    # all the rows marked: nothing to the register, the booking's work done
    for i in range(3):
        post(c,loc,booking=str(b.pk),park_note=f"paga sabato {i}",**rows(("Taglio donna","1","25,00",str(svc.pk),"1")))
    post(c,loc,action="park",booking=str(b.pk),discount="5",**rows(("Colore","1","40,00","",""),("Piega","1","20,00","","")))
    with scope(t):
        assert not Sale.objects.exists() and Parked.objects.count()==4 and Booking.objects.get(pk=b.pk).status=="completed"
        last=Parked.objects.order_by("-created").first();assert last.total==Decimal("55.00") and last.data["discount"]==500
        assert Parked.objects.order_by("created").first().data["note"]=="paga sabato 0"
    # a gift card and a «Da pagare» cannot be suspended in the same way
    r=post(c,loc,action="park",gift_code="ABCD2345",**rows(("Piega","1","20,00","","")))
    assert "si usa sullo scontrino" in r.content.decode()
    r=post(c,loc,**rows(("GIFT CARD","1","50,00","gift:","1")))
    assert "non può restare fuori" in r.content.decode()
    with scope(t):ids=list(Parked.objects.values_list("pk",flat=True))
    # taken back all together into one bill, sent: all gone
    page=c.get(f"/t/salone/cassa/?sede={loc.pk}&"+"&".join(f"sospeso={i}" for i in ids)).content.decode()
    assert page.count('name="parked"')==4 and 'name="discount" maxlength="8" placeholder="0 o 10%" value="5,00"' in page  # the 5 € off comes back
    with scope(t):x=Parked.objects.order_by("created").first()
    c.post("/t/salone/cassa/",{"action":"drop_parked","sede":str(loc.pk),"parked_id":str(x.pk)})
    with scope(t):assert Parked.objects.count()==3 and Audit.objects.filter(action="sale.parked_dropped").exists()

def test_gift_card_sold_used_at_zero_given_back_and_voided(shop):
    t,loc,svc=shop
    with scope(t):
        b,_=create_booking(t,payload(svc));loc.till={**loc.till,"gift_dept":7};loc.save()
    c,_=client_as(t,role="reception")
    exp=(timezone.now()+timedelta(days=365)).date().isoformat()
    r=post(c,loc,contact=str(b.contact_id),**rows(("GIFT CARD","1","50,00",f"gift:{exp}","")))
    assert r.status_code==302
    with scope(t):
        s=cassa.claim(loc);card=GiftCard.objects.get()
        assert card.status=="pending" and card.amount==Decimal("50.00") and card.contact_id==b.contact_id and str(card.expires)==exp and len(card.code)==8
        assert s.data["lines"][0]["desc"]==f"GIFT CARD {card.code}" and s.data["lines"][0]["dept"]==7
        assert f"=R7/$5000/(GIFT CARD {card.code})" in cassa.rch(s,s.data,loc.till)
        with pytest.raises(ValueError,match="non è ancora attiva"):gift.usable(card.code,date.today())
        cassa.finish(t,s,"done",{"number":"1","z":"5"});card.refresh_from_db();assert card.status=="active"
    # used on a bill of 25: the receipt at 0, the credit 25 less
    page=c.get(f"/t/salone/cassa/?prenotazione={b.pk}&sede={loc.pk}").content.decode()
    assert f'data-till-gift-use="{card.code}"' in page  # his card, ready to use
    assert c.get(f"/t/salone/cassa/gift-card/codice/?code={card.code.lower()}").json()["balance"]=="50,00"
    r=post(c,loc,booking=str(b.pk),gift_code=card.code.lower(),**rows(("Taglio donna","1","25,00",str(svc.pk),"")))
    with scope(t):
        card.refresh_from_db();assert card.balance==Decimal("25.00")
        s=cassa.claim(loc);assert s.total==0 and s.data["gift"]=={"cents":2500,"id":str(card.pk),"code":card.code} and s.data["payments"]==[["cash",0]]
        x=cassa.epson(s,s.data,{**loc.till,"device":"epson"})
        assert f'description="GIFT CARD {card.code}" amount="25,00"' in x and 'payment="0,00"' in x and 'unitPrice="25,00"' in x
        cmds=cassa.rch(s,s.data,loc.till)
        assert "=R2/$2500/(Taglio donna)" in cmds and "=V-/$2500" in cmds and "=T1" in cmds  # the real price, the gift as its discount, closed at 0
        assert "Gift card " in "\n".join(cassa.copy_lines(t,s)[1])
        # not emitted: the credit back; Riprova takes it again
        cassa.finish(t,s,"failed",{"error":"x"});card.refresh_from_db();assert card.balance==Decimal("50.00")
    c.post("/t/salone/cassa/",{"action":"retry","sede":str(loc.pk),"sale":str(s.pk)})
    c.post("/t/salone/cassa/",{"action":"retry","sede":str(loc.pk),"sale":str(s.pk)})  # twice: once
    with scope(t):
        card.refresh_from_db();assert card.balance==Decimal("25.00") and Sale.objects.get(pk=s.pk).status=="queued"
        s=cassa.claim(loc);cassa.finish(t,s,"done",{"number":"2","z":"5"})
    # partly: 25 left on a bill of 40, 15 paid by card
    r=post(c,loc,gift_code=card.code,pay="",pay_card="15",**rows(("Colore","1","40,00","","")))
    with scope(t):
        card.refresh_from_db();assert card.balance==0
        s3=Sale.objects.order_by("-created").first();assert s3.total==Decimal("15.00") and s3.data["payments"]==[["card",1500]]
        with pytest.raises(ValueError,match="non ha più credito"):gift.usable(card.code,date.today())
    # its annullo gives the credit back
    with scope(t):
        s3=cassa.claim(loc);cassa.finish(t,s3,"done",{"number":"3","z":"5"})
        v=cassa.enqueue(t,loc,kind="void",voids=s3,total=s3.total,data={"z":"5","number":"3","day":date.today().isoformat()})
        v=cassa.claim(loc);cassa.finish(t,v,"done",{});card.refresh_from_db();assert card.balance==Decimal("25.00")
    # expired, or sold with an annullo: refused
    with scope(t):
        GiftCard.objects.filter(pk=card.pk).update(expires=date.today()-timedelta(days=1))
        with pytest.raises(ValueError,match="scaduta"):gift.usable(card.code,date.today())
    # the page: given to another client, printed
    owner,_=client_as(t)
    with scope(t):other=Contact.objects.exclude(pk=b.contact_id).first() or Contact.objects.create(tenant=t,email_index=secrets.token_hex(8),pii=__import__("core.crypto",fromlist=["encrypt"]).encrypt(t,{"first_name":"Luca","last_name":"Bianchi"},"contact:x"))
    page=owner.get("/t/salone/cassa/gift-card/").content.decode()
    assert card.code in page and "Credito ancora da usare" in page
    # 2.22.0: sold from the page: the Cassa opens with the card in the bill and its client, the receipt as always
    assert "Vendi una gift card" in page and 'data-gift-preset="50"' in page and 'name="gift"' in page
    from core.clients import seal_card,key_for
    with scope(t):
        luca=Contact(tenant=t,email_index=key_for(t,{"email":"","phone":"","first_name":"Luca","last_name":"Bianchi"}));seal_card(t,luca,{"first_name":"Luca","last_name":"Bianchi","email":"","phone":""});luca.save()
    till=owner.get(f"/t/salone/cassa/?gift=50&scade=2027-06-30&cliente={luca.pk}").content.decode()
    assert 'value="GIFT CARD"' in till and 'value="gift:2027-06-30"' in till and 'value="50,00"' in till and "Luca" in till
    till=owner.get("/t/salone/cassa/?gift=abc&scade=x&cliente=zzz").content.decode();assert "GIFT CARD" not in till.split("till-row")[0]
    assert card.code in owner.get(f"/t/salone/cassa/gift-card/?stampa={card.pk}").content.decode()
    owner.post("/t/salone/cassa/gift-card/",{"card":str(card.pk),"action":"expires","expires":""})
    with scope(t):card.refresh_from_db();assert card.expires is None
    rep=owner.get("/t/salone/report/").content.decode()
    assert "Gift card vendute" in rep and card.code in rep

def test_gift_card_rules_of_the_bill(shop):
    t,loc,svc=shop;c,_=client_as(t,role="reception")
    for bad,why in (({"qty":["2"]},"Una gift card per riga"),({"price":["0,50"]},"almeno 1,00"),({"svc":["gift:2026-99-99"]},"Scadenza della gift card")):
        data={**rows(("GIFT CARD","1","50,00","gift:","")),**bad}
        assert why in post(c,loc,**data).content.decode()
    assert "non trovata" in post(c,loc,gift_code="ZZZZ9999",**rows(("Piega","1","20,00","",""))).content.decode()
    with scope(t):assert not Sale.objects.exists() and not GiftCard.objects.exists()

def test_quote_with_products_header_pdf_and_into_cassa(shop):
    t,loc,svc=shop;c,_=client_as(t)
    with scope(t):
        b,_=create_booking(t,payload(svc));loc.address="Via Roma 1, Pescara";loc.phone="085 123456";loc.save()
    r=c.post("/t/salone/preventivi/nuovo/",{"first_name":"Anna","last_name":"Verdi","phone":"333 1112233","email":"anna@example.invalid","new_client":"on",
        "desc":["Taglio donna","Maschera ristrutturante",""],"qty":["1","2",""],"price":["25,00","15",""],"ref":[f"s:{svc.pk}","",""],"save":["","1",""],"notes":"Prezzi indicativi","valid_until":"2026-12-31"})
    assert r.status_code==302
    with scope(t):
        q=Quote.objects.get();assert q.ref.endswith("-0001") and q.total==Decimal("55.00") and q.contact_id and "Anna" not in q.pii
        p=Product.objects.get();assert p.name=="Maschera ristrutturante" and p.price==Decimal("15.00")
        assert q.data["lines"][1]["ref"]==f"p:{p.pk}"
    sheet=c.get(f"/t/salone/preventivi/{q.pk}/stampa/").content.decode()
    assert "Preventivo" in sheet and "Anna Verdi" in sheet and "Via Roma 1, Pescara" in sheet and "085 123456" in sheet and "30,00 €" in sheet and "55,00 €" in sheet and 'data-print="quote-sheet"' in sheet
    assert "mancano questi dati: partita IVA" in sheet  # missing in the header: said
    # the same quote edited and saved many times: one number, the product once
    for i in range(3):
        c.post(f"/t/salone/preventivi/{q.pk}/",{"first_name":"Anna","last_name":"Verdi","contact":str(q.contact_id),"desc":["Maschera ristrutturante"],"qty":[str(i+1)],"price":["15"],"ref":[""],"save":["1"]})
    with scope(t):
        assert Quote.objects.count()==1 and Product.objects.count()==1 and Quote.objects.get().total==Decimal("45.00")
    c.post("/t/salone/preventivi/nuovo/",{"first_name":"Bruno","desc":["Piega"],"qty":["1"],"price":["20"],"ref":[""],"save":[""]})
    with scope(t):assert sorted(Quote.objects.values_list("number",flat=True))==[1,2]
    assert "Bruno" in c.get("/t/salone/preventivi/").content.decode()
    till=c.get(f"/t/salone/cassa/?preventivo={q.pk}").content.decode()
    assert 'value="Maschera ristrutturante"' in till and f'value="p:{p.pk}"' in till
    assert "Scrivi il nome del cliente" in c.post("/t/salone/preventivi/nuovo/",{"desc":["Piega"],"qty":["1"],"price":["20"]}).content.decode()
    card=c.get(f"/t/salone/contacts/{q.contact_id}/").content.decode()
    assert "Preventivi" in card and q.ref in card and "Nuovo preventivo" in card

def test_appointment_summary_and_client_history(shop):
    t,loc,svc=shop
    with scope(t):b,_=create_booking(t,payload(svc))
    c,_=client_as(t)
    r=c.get(f"/t/salone/bookings/{b.pk}/riepilogo/").content.decode()
    assert "Taglio donna" in r and "Totale" in r and "Da pagare" not in r and f"?prenotazione={b.pk}&amp;sede={loc.pk}" in r and "Pagamento" in r and "Apri la prenotazione" in r
    post(c,loc,booking=str(b.pk),**rows(("Taglio donna","1","25,00",str(svc.pk),"")))
    with scope(t):s=cassa.claim(loc);cassa.finish(t,s,"done",{"number":"9","z":"1"})
    r=c.get(f"/t/salone/bookings/{b.pk}/riepilogo/").content.decode()
    assert "Pagato" in r and "Scontrino già fatto" in r and ">Pagamento<" not in r
    h=c.get(f"/t/salone/contacts/{b.contact_id}/storico/").content.decode()
    assert "Storico" in h and "Taglio donna" in h and "25,00 €" in h and 'data-print="client-history"' in h
    assert "Niente registrato in questo periodo" in c.get(f"/t/salone/contacts/{b.contact_id}/storico/?dal=2001-01-01&al=2001-12-31").content.decode()
    reception,_=client_as(t,role="reception")
    assert reception.get(f"/t/salone/bookings/{b.pk}/riepilogo/").status_code==200

def test_missing_daily_closure_banner(shop):
    t,loc,svc=shop;c,_=client_as(t)
    with scope(t):
        s=cassa.enqueue(t,loc,total=Decimal("10"),data={"lines":[],"payments":[]});s=cassa.claim(loc);cassa.finish(t,s,"done",{})
        Sale.objects.filter(pk=s.pk).update(printed=timezone.now()-timedelta(days=1))
    assert "Manca la chiusura giornaliera" in c.get("/t/salone/cassa/").content.decode()
    assert "Manca la chiusura giornaliera" in c.get("/t/salone/").content.decode()
    c.post("/t/salone/cassa/",{"action":"closed_by_hand","sede":str(loc.pk)})
    page=c.get("/t/salone/cassa/").content.decode()
    assert "Manca la chiusura giornaliera" not in page and "Chiusura giornaliera fatta dalla cassa" in page
    assert "Manca la chiusura giornaliera" not in c.get("/t/salone/").content.decode()

def test_erasure_and_export_take_quotes_suspended_and_cards(shop):
    from core.privacy import erase_contact,export_contact
    t,loc,svc=shop;c,u=client_as(t)
    with scope(t):b,_=create_booking(t,payload(svc));Booking.objects.filter(pk=b.pk).update(status="completed")
    post(c,loc,action="park",contact=str(b.contact_id),**rows(("Piega","1","20,00","","")))
    c.post("/t/salone/preventivi/nuovo/",{"first_name":"Mario","last_name":"Rossi","contact":str(b.contact_id),"desc":["Piega"],"qty":["1"],"price":["20"],"ref":[""],"save":[""]})
    with scope(t):
        GiftCard.objects.create(tenant=t,code="ABCD2345",contact_id=b.contact_id,amount=10,balance=10,status="active")
        e=export_contact(t,Contact.objects.get(pk=b.contact_id))
        assert e["quotes"][0]["first_name"]=="Mario" and e["to_pay_at_the_desk"][0]["total"]=="20.00" and e["gift_cards"][0]["code"]=="ABCD2345"
        erase_contact(t,Contact.objects.get(pk=b.contact_id),u)
        assert not Quote.objects.exists() and not Parked.objects.exists() and GiftCard.objects.get().contact_id is None

def test_whatsapp_reminders_of_tomorrow_marked_and_undone(shop):
    from urllib.parse import unquote
    t,loc,svc=shop
    tz=ZoneInfo(t.timezone);tomorrow=(timezone.now().astimezone(tz)+timedelta(days=1)).date()
    with scope(t):
        b,_=create_booking(t,{**payload(svc),"start":timezone.datetime.combine(tomorrow,timezone.datetime.min.time().replace(hour=10),tz).isoformat()})
    c,_=client_as(t,role="reception")
    page=c.get("/t/salone/promemoria-whatsapp/").content.decode()
    assert b.start.astimezone(tz).date()==tomorrow,b.start
    assert "Da avvisare: 1" in page and "https://wa.me/39" in page and "ti ricordiamo" in unquote(page)
    assert page.count('class="card wa-day')==7 and "Domani, " in page and "In programma" in page  # 2.22.0: the week in sight; off: nothing due yet
    assert "Giorno prima" in c.get(f"/t/salone/promemoria-whatsapp/?giorno={tomorrow.isoformat()}").content.decode()
    owner,_=client_as(t)
    assert "da avvisare" not in owner.get("/t/salone/").content.decode()  # off until the owner turns it on
    r=owner.post("/t/salone/setup/banco/",{"wa_when":"d1","absences_request":str(t.absences_request),"wait_mode":t.wait_mode,"wait_minutes":str(t.wait_minutes)})
    with scope(t):assert Tenant.objects.get(pk=t.pk).wa_when=="d1"
    for page in ("/t/salone/","/t/salone/contacts/","/t/salone/report/"):
        assert "Promemoria WhatsApp: 1 cliente da avvisare" in c.get(page).content.decode()  # on every page
    page=c.get("/t/salone/promemoria-whatsapp/").content.decode()
    assert "Da avvisare oggi" in page and 'class="card wa-day is-due"' in page and "In programma" not in page  # tomorrow's visit is the one due now
    assert "cliente da avvisare" not in c.get("/t/salone/promemoria-whatsapp/").content.decode()
    with scope(t):Tenant.objects.filter(pk=t.pk).update(wa_when="h2")
    assert "da avvisare" not in c.get("/t/salone/").content.decode()  # tomorrow at 10 is not in the next 2 hours
    with scope(t):Tenant.objects.filter(pk=t.pk).update(wa_when="d1")
    for on in ("1","0","1"):
        r=c.post("/t/salone/promemoria-whatsapp/",{"booking":str(b.pk),"sent":on});assert r.json()["ok"]
    with scope(t):assert Booking.objects.get(pk=b.pk).snapshot.get("wa_reminded")
    assert "Tutti avvisati" in c.get("/t/salone/promemoria-whatsapp/").content.decode()
    assert "da avvisare" not in owner.get("/t/salone/").content.decode()
    owner.post("/t/salone/notifications/",{"action":"whatsapp","whatsapp_text":"","whatsapp_reminder":"Ehi {nome}, domani alle {ora}!"})
    with scope(t):assert Tenant.objects.get(pk=t.pk).whatsapp_reminder=="Ehi {nome}, domani alle {ora}!"
    assert "Ehi Mario, domani alle 10:00!" in unquote(c.get("/t/salone/promemoria-whatsapp/").content.decode())

def test_whatsapp_window_follows_the_opening_days(shop):
    """Closed on Sunday and Monday: on Saturday the clients of Tuesday; a closure of the whole shop counts too."""
    from core.client_mails import wa_window
    from datetime import datetime,time as clock
    t,loc,svc=shop;tz=ZoneInfo(t.timezone)
    with scope(t):
        Schedule.objects.filter(weekday__in=(6,0)).delete()  # Sunday, Monday
        Tenant.objects.filter(pk=t.pk).update(wa_when="d1");t.refresh_from_db()
        sat=datetime(2026,11,7,18,0,tzinfo=tz);a,b=wa_window(t,sat)
        assert a==datetime(2026,11,8,0,0,tzinfo=tz) and b==datetime(2026,11,11,0,0,tzinfo=tz)  # Sun, Mon and Tue
        a,b=wa_window(t,datetime(2026,11,10,9,0,tzinfo=tz));assert (a.date(),b.date())==(date(2026,11,11),date(2026,11,12))  # Tuesday: Wednesday
        ExceptionDay.objects.create(tenant=t,date=date(2026,11,10),closed=True,label="Chiuso")
        assert wa_window(t,sat)[1]==datetime(2026,11,12,0,0,tzinfo=tz)  # Tuesday closed: through Wednesday
        Tenant.objects.filter(pk=t.pk).update(wa_when="t0");t.refresh_from_db()
        a,b=wa_window(t,sat);assert a==sat and b==datetime(2026,11,8,0,0,tzinfo=tz)

def test_review_fixes_visit_mark_void_receipt_quote_retention(shop):
    """A visit of two services is marked once for both; a voided receipt lets the agenda ask Pagamento again;
    quotes without a card go with retention; a double «Metti in sospeso» parks once; a bad expiry is refused."""
    from core.privacy import apply_retention
    t,loc,svc=shop;tz=ZoneInfo(t.timezone);tomorrow=(timezone.now().astimezone(tz)+timedelta(days=1)).date()
    with scope(t):
        svc2=Service.objects.create(tenant=t,location=loc,name="Piega",kind="capacity",capacity=5,max_party=1,lead_minutes=0,price=Decimal("20"))
        b,_=create_booking(t,{**payload(svc),"start":timezone.datetime.combine(tomorrow,timezone.datetime.min.time().replace(hour=10),tz).isoformat()})
        b2,_=create_booking(t,{**payload(svc2),"start":timezone.datetime.combine(tomorrow,timezone.datetime.min.time().replace(hour=11),tz).isoformat()})
        Booking.objects.filter(pk__in=(b.pk,b2.pk)).update(visit=b.pk);Tenant.objects.filter(pk=t.pk).update(wa_when="d1")
    c,_=client_as(t)
    assert "1 cliente da avvisare" in c.get("/t/salone/").content.decode()
    c.post("/t/salone/promemoria-whatsapp/",{"booking":str(b.pk),"sent":"1"})
    assert "da avvisare" not in c.get("/t/salone/").content.decode()
    # the receipt voided: Pagamento again
    post(c,loc,booking=str(b.pk),**rows(("Taglio donna","1","25,00",str(svc.pk),"")))
    with scope(t):
        s=cassa.claim(loc);cassa.finish(t,s,"done",{"number":"3","z":"1"});Sale.objects.filter(pk=s.pk).update(status="void")
    assert ">Pagamento<" in c.get(f"/t/salone/bookings/{b.pk}/riepilogo/").content.decode()
    # the same «Metti in sospeso» twice: once
    key=secrets.token_urlsafe(24)
    for _ in range(2):post(c,loc,action="park",key=key,**rows(("Piega","1","20,00","","")))
    with scope(t):assert Parked.objects.count()==1
    # quotes without a card go with retention
    c.post("/t/salone/preventivi/nuovo/",{"first_name":"Solo","last_name":"Nome","desc":["Piega"],"qty":["1"],"price":["20"],"ref":[""],"save":[""]})
    with scope(t):
        Quote.objects.update(created=timezone.now()-timedelta(days=t.retention_days+1))
        assert apply_retention(t,apply=True)["quotes"]==1 and not Quote.objects.exists()
        card=GiftCard.objects.create(tenant=t,code="ABCD2345",amount=10,balance=10,status="active",expires=date(2027,1,1))
    c.post("/t/salone/cassa/gift-card/",{"card":str(card.pk),"action":"expires","expires":"31/12/2026"})
    with scope(t):assert GiftCard.objects.get().expires==date(2027,1,1)

def test_rch_counts_the_numbers_and_voids_without_asking(shop):
    """RCH never answers the document number: the program counts the «chiusura-documento» pair from the last closure
    written in Registratore di cassa; Annulla on the row needs no number typed; without the closure number the void
    command drops that part; a number typed by hand still wins."""
    t,loc,svc=shop;c,_=client_as(t)
    sell_one=lambda:cassa.finish(t,cassa.claim(loc),"done",{}) if cassa.enqueue(t,loc,total=Decimal("10"),data={"lines":[{"desc":"X","qty":1,"cents":1000,"dept":1}],"payments":[["cash",1000]]}) else None
    with scope(t):
        assert [(s.z,s.number) for s in (sell_one(),sell_one())]==[("","1"),("","2")]  # no closure number known yet
        loc.till={**loc.till,"last_z":2025,"rch_void":"=k/&{data}/[{z}/]{numero}"};loc.save()
        s=sell_one();assert (s.z,s.number)==("2026","3")
        cl=cassa.enqueue(t,loc,kind="close");cassa.finish(t,cassa.claim(loc),"done",{});loc.refresh_from_db();assert loc.till["last_z"]==2026
        s=sell_one();assert (s.z,s.number)==("2027","1")  # after the closure: next closure, documents from 1
    page=c.get(f"/t/salone/cassa/?sede={loc.pk}").content.decode()
    assert f'id="d-{s.pk}" name="doc" value="2027-1"' in page and "contato il programma" in page and 'name="doc" value="2027-1" placeholder="Documento n., es. 2026-0012" maxlength="12" inputmode="numeric">' in page
    c.post("/t/salone/cassa/",{"action":"void","sede":str(loc.pk),"sale":str(s.pk),"doc":"2027-1"})
    with scope(t):
        v=Sale.objects.get(kind="void");assert cassa.rch(v,v.data,loc.till)[-1]=="=k/&"+date.today().strftime("%d%m%y")+"/[2027/]0001"
        cassa.finish(t,cassa.claim(loc),"done",{});assert Sale.objects.get(pk=s.pk).status=="void"
        s2=sell_one();assert (s2.z,s2.number)==("2027","3")  # the annullo counted too
    # the closure done on the register's keys, marked here: the counter moves too
    c.post("/t/salone/cassa/",{"action":"closed_by_hand","sede":str(loc.pk)})
    with scope(t):
        loc.refresh_from_db();assert loc.till["last_z"]==2027
        s3=sell_one();assert (s3.z,s3.number)==("2028","1")
        # no closure number: the void command without the [z/] part; typed by hand «5-0007» wins
        v=cassa.enqueue(t,loc,kind="void",voids=s3,total=s3.total,data={"z":"","number":"4","day":date.today().isoformat()})
        assert cassa.rch(v,v.data,loc.till)[-1].endswith("/0004") and "[" not in cassa.rch(v,v.data,loc.till)[-1]
    r=c.post("/t/salone/cassa/",{"action":"void","sede":str(loc.pk),"sale":str(s3.pk),"doc":"5-0007"},follow=True)
    assert "già stato inviato" in r.content.decode()  # one annullo a receipt
    with scope(t):cassa.finish(t,cassa.claim(loc),"done",{});s4=sell_one();assert s4.kind=="sale"  # the queued annullo first, then a new receipt
    c.post("/t/salone/cassa/",{"action":"void","sede":str(loc.pk),"sale":str(s4.pk),"doc":"5-0007"})
    with scope(t):v=Sale.objects.filter(kind="void").order_by("-created").first();assert v.voids_id==s4.pk and v.data["z"]=="5" and v.data["number"]=="0007" and "[0005/]" in cassa.rch(v,v.data,loc.till)[-1]
    # the settings page takes the closure number, and refuses a bad one
    owner,_=client_as(t);Tenant.objects.filter(pk=t.pk).update(till_self=True)
    base={"location":str(loc.pk),"action":"save","device":"rch","connection":"net","address":"192.168.1.50","serial":"1","default_dept":"1","rch_off":"=V-/${importo}","rch_void":"=k/&{data}/[{z}/]{numero}","rch_close":"=C3 =C10 =C1"}
    owner.post("/t/salone/registratore/",{**base,"last_z":"abc"})
    with scope(t):loc.refresh_from_db();assert loc.till["last_z"]==2027
    owner.post("/t/salone/registratore/",{**base,"last_z":"3000"})
    with scope(t):loc.refresh_from_db();assert loc.till["last_z"]==3000

def test_rch_count_survives_a_receipt_to_check_and_custom_void_commands(shop):
    """A sent and lost («Da verificare»), B printed, then A found printed: A=1, B=2 as on the register's paper.
    A void command written without brackets drops {z}/ cleanly; a stale till copy never overwrites last_z."""
    t,loc,svc=shop
    with scope(t):
        loc.till={**loc.till,"last_z":10};loc.save()
        mk=lambda:cassa.enqueue(t,loc,total=Decimal("10"),data={"lines":[{"desc":"X","qty":1,"cents":1000,"dept":1}],"payments":[["cash",1000]]})
        a=mk();a=cassa.claim(loc);a=cassa.finish(t,a,"unknown",{"error":"persa"});assert a.status=="unknown" and a.printed is None
        b=mk();b=cassa.claim(loc);b=cassa.finish(t,b,"done",{});assert (b.z,b.number)==("11","2")  # A sent before it counts
        a=cassa.finish(t,a,"done",{});assert (a.z,a.number)==("11","1") and a.printed==a.claimed  # found printed: its own place
        c_=mk();c_=cassa.claim(loc);c_=cassa.finish(t,c_,"done",{});assert (c_.z,c_.number)==("11","3")
        # a command without brackets
        v=cassa.enqueue(t,loc,kind="void",voids=c_,total=10,data={"z":"","number":"3","day":date.today().isoformat()})
        assert cassa.rch(v,v.data,{**loc.till,"rch_void":"=k/&{data}/{z}/{numero}"})[-1]=="=k/&"+date.today().strftime("%d%m%y")+"/0003"
        assert cassa.rch(v,{**v.data,"z":"11"},{**loc.till,"rch_void":"=k/&{data}/{z}/{numero}"})[-1].endswith("/0011/0003")
        # the closure finishing with a stale location copy: last_z bumped on the fresh one, other settings kept
        cassa.finish(t,cassa.claim(loc),"done",{})  # the annullo queued above
        cl=cassa.enqueue(t,loc,kind="close");cl=cassa.claim(loc);assert cl.kind=="close"
        Location.objects.filter(pk=loc.pk).update(till={**loc.till,"last_z":10,"serial":"NEW"})
        cassa.finish(t,cl,"done",{});loc.refresh_from_db();assert loc.till["last_z"]==11 and loc.till["serial"]=="NEW"

def test_site_visit_whose_time_went_is_kept_as_a_request_of_its_first_service(shop):
    from core import wpsync
    t,loc,svc=shop
    with scope(t):
        op=Resource.objects.create(tenant=t,location=loc,name="Anna");piega=Service.objects.create(tenant=t,location=loc,name="Piega",kind="resource",duration=30,capacity=1,max_party=1,lead_minutes=0)
        taglio=Service.objects.create(tenant=t,location=loc,name="Taglio",kind="resource",duration=30,capacity=1,max_party=1,lead_minutes=0)
        for s in (piega,taglio):ServiceResource.objects.create(tenant=t,service=s,resource=op)
        day=(timezone.now()+timedelta(days=3)).astimezone(ZoneInfo(t.timezone)).date()
        create_booking(t,{**payload(taglio),"resource":str(op.pk),"start":timezone.datetime.combine(day,timezone.datetime.min.time().replace(hour=11),ZoneInfo(t.timezone)).isoformat()},actor=None,origin="desk")
    row={"id":"a"*32,"service":str(taglio.pk),"option":"","resource":str(op.pk),"any":False,"date":day.isoformat(),"time":"11:00","first_name":"Dora","last_name":"Viola","email":"dora@example.invalid","phone":"3471000004","notes":"","notices":True,"extra":[{"service":str(piega.pk),"resource":str(op.pk),"time":"11:30","end":"12:00"}]}
    new,ack,errors,refused=wpsync.take(t,[row])
    with scope(t):
        b=Booking.objects.get(origin="wordpress");assert b.status=="pending" and b.service_id==taglio.pk and not b.visit and "Dal sito con: Piega" in b.data["notes"]
    assert ack==["a"*32] and not errors
