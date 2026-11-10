"""2.22.10: Parcheggio in the Cassa: a bill parked without a receipt (as the page keeps it), seen by every PC of that
location, taken back (the bill open goes to the Parcheggio in its place) or thrown away; the Da pagare beside it; the
client's card credit for a client known by id; erasure and retention."""
import json,secrets
from datetime import timedelta
import pytest
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking
from core.privacy import erase_contact,apply_retention
from tests.test_core import client_as,payload
from tests.test_cassa import shop  # noqa: F401 (fixture)

pytestmark=pytest.mark.django_db

def draft(desc="Piega",price="18,00",staff="Cristina",**fields):
    return json.dumps({"lines":[{"desc":desc,"qty":"1","price":price,"svc":"","off":"","staff":staff,"park":""},{"desc":"Shampoo","qty":"1","price":"6","svc":"","off":"","staff":"","park":""}],
        "fields":{"till-discount":"","pay=cash":True,**fields},"booking":"","parked":[],"active":staff})

def park(c,loc,**extra):
    return c.post("/t/salone/cassa/",{"action":"standby","sede":str(loc.pk),"key":secrets.token_urlsafe(24),"desc":["Piega"],"qty":["1"],"price":["18"],"svc":[""],"pay":"cash",**extra})

def test_park_list_resume_and_drop(shop):
    t,loc,svc=shop;c,_=client_as(t)
    r=park(c,loc,draft=draft(),standby_note="signora delle 10:30")
    assert r.status_code==302 and not Sale.objects.exists()  # no receipt
    with scope(t):
        x=Standby.objects.get();assert x.total==24 and x.data["note"]=="signora delle 10:30" and x.data["draft"]["active"]=="Cristina"
        assert not Parked.objects.exists() and not Treatment.objects.exists()  # not done, not in the record
    page=c.get(f"/t/salone/cassa/?sede={loc.pk}").content.decode()
    i=page.index('id="parcheggio"');sec=page[i:page.index("till-divider",i)]
    assert "signora delle 10:30" in sec and "24,00" in sec and "Piega" in sec and "(Cristina)" in sec and "Riprendi" in sec and "Da pagare" in sec
    # without the page's help: the rows of the form, without a client «Senza nome»
    park(c,loc)
    with scope(t):assert Standby.objects.count()==2 and Standby.objects.order_by("created").last().data["draft"]["lines"][0]["desc"]=="Piega"
    assert "Senza nome" in c.get(f"/t/salone/cassa/?sede={loc.pk}").content.decode()
    # taken back: it leaves the Parcheggio and comes to the Cassa once, with all it had
    r=c.post("/t/salone/cassa/",{"action":"resume","sede":str(loc.pk),"standby_id":str(x.pk)})
    assert r.status_code==302
    page=c.get(f"/t/salone/cassa/?sede={loc.pk}").content.decode()
    data=json.loads(page[page.index('id="till-resume"'):].split(">",1)[1].split("</script>",1)[0])
    assert data["lines"][0]["desc"]=="Piega" and data["active"]=="Cristina"
    assert 'id="till-resume"' not in c.get(f"/t/salone/cassa/?sede={loc.pk}").content.decode()  # once
    with scope(t):assert not Standby.objects.filter(pk=x.pk).exists()
    # taken by another PC already: said
    r=c.post("/t/salone/cassa/",{"action":"resume","sede":str(loc.pk),"standby_id":str(x.pk)},follow=True)
    assert "già ripreso un&#x27;altra postazione" in r.content.decode()
    # a bill open here when another is taken back: it goes to the Parcheggio, nothing lost
    with scope(t):y=Standby.objects.get()
    c.post("/t/salone/cassa/",{"action":"resume","sede":str(loc.pk),"standby_id":str(y.pk),"swap":draft(desc="Colore",price="40",staff="Marco")})
    with scope(t):
        z=Standby.objects.get();assert z.data["draft"]["lines"][0]["desc"]=="Colore" and z.total==46
    # thrown away
    c.post("/t/salone/cassa/",{"action":"drop_standby","sede":str(loc.pk),"standby_id":str(z.pk)})
    with scope(t):assert not Standby.objects.exists()
    # nothing to park
    r=c.post("/t/salone/cassa/",{"action":"standby","sede":str(loc.pk),"desc":[""],"price":[""]},follow=True)
    assert "niente da parcheggiare" in r.content.decode()

def test_parcheggio_per_location_and_client(shop):
    t,loc,svc=shop;c,_=client_as(t)
    with scope(t):
        b,_=create_booking(t,payload(svc))
        other=Location.objects.create(tenant=t,name="Altra",till=loc.till)
    park(c,loc,draft=draft(contact=str(b.contact_id)),contact=str(b.contact_id))
    with scope(t):x=Standby.objects.get();assert x.contact_id==b.contact_id
    assert "Mario Rossi" in c.get(f"/t/salone/cassa/?sede={loc.pk}").content.decode()
    assert "Mario Rossi" not in c.get(f"/t/salone/cassa/?sede={other.pk}").content.decode().split('id="parcheggio"')[1].split("till-divider")[0]
    assert c.post("/t/salone/cassa/",{"action":"resume","sede":str(other.pk),"standby_id":str(x.pk)},follow=True).status_code==200
    with scope(t):assert Standby.objects.filter(pk=x.pk).exists()  # another location's bill is not taken from here
    # the card credit of a client known by id (a bill taken back with him)
    with scope(t):GiftCard.objects.create(tenant=t,code="AB12CD34",contact_id=b.contact_id,amount=50,balance=35,status="active")
    d=c.get(f"/t/salone/contacts/search/?cassa=1&id={b.contact_id}").json()
    assert d["results"][0]["gifts"]==[{"code":"AB12CD34","balance":"35.00".replace(".",",")}]
    assert c.get("/t/salone/contacts/search/?cassa=1&id=nope").json()["results"]==[]
    # erasure and retention
    with scope(t):
        contact=Contact.objects.get(pk=b.contact_id)
        Standby.objects.create(tenant=t,location=loc,pii="",total=0)
        old=Standby.objects.exclude(pk=x.pk).get();old.pii=__import__("core.crypto",fromlist=["encrypt"]).encrypt(t,{"draft":{"lines":[]}},"standby:"+str(old.pk));old.save()
        Standby.objects.filter(pk=old.pk).update(created=timezone.now()-timedelta(days=t.retention_days+2))
        assert apply_retention(t)["standby"]==1;apply_retention(t,apply=True)
        assert list(Standby.objects.values_list("pk",flat=True))==[x.pk]
        Booking.objects.filter(contact=contact).update(status="cancelled");erase_contact(t,contact,None)
        assert not Standby.objects.exists()

def test_standby_page_from_the_menu(shop):
    """Stand-by in the menu with how many wait; the page lists every register's, Riprendi opens the Cassa there, the ×
    comes back to the page."""
    t,loc,svc=shop;c,_=client_as(t)
    page=c.get("/t/salone/").content.decode()
    assert 'href="/t/salone/cassa/stand-by/"' in page and 'Stand-by <span class="nav-count">' not in page
    park(c,loc,draft=draft(),standby_note="sotto il casco");park(c,loc)
    assert 'Stand-by <span class="nav-count">2</span>' in c.get("/t/salone/").content.decode()
    page=c.get("/t/salone/cassa/stand-by/").content.decode()
    assert "sotto il casco" in page and "Senza nome" in page and page.count("Riprendi</button>")==2 and f'action="/t/salone/cassa/?sede={loc.pk}"' in page
    with scope(t):x=Standby.objects.order_by("created").first();y=Standby.objects.order_by("created").last()
    r=c.post(f"/t/salone/cassa/?sede={loc.pk}",{"action":"drop_standby","sede":str(loc.pk),"standby_id":str(y.pk),"from":"standby"})
    assert r["Location"]=="/t/salone/cassa/stand-by/"
    r=c.post(f"/t/salone/cassa/?sede={loc.pk}",{"action":"resume","sede":str(loc.pk),"standby_id":str(x.pk)})
    assert r["Location"]==f"/t/salone/cassa/?sede={loc.pk}" and 'id="till-resume"' in c.get(r["Location"]).content.decode()
    assert "Nessun conto in stand-by" in c.get("/t/salone/cassa/stand-by/").content.decode()
    # who cannot use the Cassa: no entry, no page
    op,_=client_as(t,role="operator")
    assert "cassa/stand-by" not in op.get("/t/salone/").content.decode() and op.get("/t/salone/cassa/stand-by/").status_code==403
