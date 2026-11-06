"""2.21.0, end to end with a real WordPress and the plugin (TDBS_WP_E2E=http://127.0.0.1:8899, its folder in TDBS_WP_DIR,
its emails captured in TDBS_WP_MAILS): the site in charge confirms and mails, the program gets bookings and changes only."""
import json,os,re,subprocess,time,urllib.parse,urllib.request
from datetime import datetime,timedelta,time as clock
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from django.utils import timezone
from core import wpsync
from core.booking import create_booking,move_booking
from core.clients import seal_card,key_for
from core.models import *
from core.tenancy import scope
from tests.test_core import sample,payload
from tests.test_salon import shop,day_of

URL=os.environ.get("TDBS_WP_E2E","");WP=os.environ.get("TDBS_WP_DIR","");MAILS=Path(os.environ.get("TDBS_WP_MAILS","/tmp/wplog/mails.jsonl"))
pytestmark=[pytest.mark.django_db,pytest.mark.skipif(not URL,reason="WordPress di prova non configurato")]
SECRET="k"*43

def mails():return [json.loads(l) for l in MAILS.read_text().splitlines()] if MAILS.exists() else []
def ctl(*args):return subprocess.run(["php","/home/claude/wptools/ctl.php",WP,*args],capture_output=True,text=True,timeout=60).stdout
def get(url,data=None):
    req=urllib.request.Request(url,data=urllib.parse.urlencode(data).encode() if data else None)
    with urllib.request.urlopen(req,timeout=30) as r:return r.read().decode()
def book(**kw):
    page=get(URL+"/prenota/");conf=json.loads(re.search(r'data-config="([^"]+)"',page).group(1).replace("&quot;",'"'))
    body=json.dumps({"nonce":conf["nonce"],"notices":True,**kw}).encode()
    req=urllib.request.Request(URL+"/wp-json/tdbs/v1/book",data=body,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=30) as r:return json.loads(r.read())
def link_of(mail,label=None):
    urls=re.findall(r'href="([^"]*\?tdbs=[^"]+)"',mail["message"])
    return urls[0].replace("&amp;","&") if urls else None

def test_the_site_in_charge_end_to_end(sample):
    MAILS.write_text("")
    t,o,_=sample
    t.notify_email="titolare-pc@example.invalid";t.save()
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        lucia=Contact(tenant=t,email_index=key_for(t,{"phone":"+393339990000","email":""}));seal_card(t,lucia,{"first_name":"Lucia","last_name":"Bianchi","email":"","phone":"+393339990000"});lucia.save()
    wpsync.store(t,{"url":URL,"secret":SECRET,"days":5,"on":True,"mails":True,"mails_since":time.time_ns()//1000})
    Tenant.objects.filter(pk=t.pk).update(wp_archive=True);t.refresh_from_db()
    assert wpsync.by_site(t)
    # 1. the PC sends services, free times and "the site is in charge"
    wpsync.sync(t,full=True);t.refresh_from_db()
    assert not t.wp_state.get("error"),t.wp_state
    # 2. a client books on the site with the PC off: confirmed at once, the email from the site, the owner told
    day=day_of(taglio).isoformat()
    r=book(service=str(taglio.pk),option="",resource=str(anna.pk),any=False,date=day,time="10:00",first_name="lucia",last_name="BIANCHI",email="lucia.b@example.invalid",phone="347 111 2222")
    assert r["ok"] and "confermata" in r["message"],r
    m=mails();assert [x["to"] for x in m]==["lucia.b@example.invalid","titolare@example.invalid"],m
    assert m[0]["subject"].startswith("Prenotazione confermata") and "Taglio" in m[0]["message"] and "Ciao Lucia," in m[0]["message"] and "Anna" not in m[0]["subject"]
    manage=link_of(m[0]);assert manage and manage.startswith(URL)
    # 3. the PC on again: it gets the booking, on Lucia's card (by name, however written), and sends no email
    wpsync.sync(t);t.refresh_from_db()
    with scope(t):
        b=Booking.objects.get(origin="wordpress")
        assert b.status=="confirmed" and b.contact_id==lucia.pk and b.resource_id==anna.pk
        assert not Delivery.objects.exists()
    assert len(mails())==2  # the site does not send it again when the PC's copy arrives
    # 4. a booking at the desk, with the client's email: the site confirms it to him; moved: the change
    with scope(t):
        start=datetime.combine(day_of(taglio),clock(15),ZoneInfo(t.timezone))
        who=User.objects.create_user("banco@example.invalid","A-test-password-12345");Membership.objects.create(tenant=t,user=who,role="owner",can_schedule=True)
        desk,_=create_booking(t,{**payload(taglio,email="marco@example.invalid",phone="+393330001111",first_name="Marco",last_name="Verdi"),"start":start.isoformat(),"resource":str(bruno.pk)},actor=who)
        assert not Delivery.objects.exists()
    wpsync.sync(t);t.refresh_from_db()
    m=mails();assert m[-1]["to"]=="marco@example.invalid" and m[-1]["subject"].startswith("Prenotazione confermata"),m[-1]
    with scope(t):move_booking(t,desk.pk,{"service":str(taglio.pk),"start":(start+timedelta(hours=1)).isoformat(),"resource":str(bruno.pk),"party":1},None)
    wpsync.sync(t);t.refresh_from_db()
    m=mails();assert m[-1]["to"]=="marco@example.invalid" and m[-1]["subject"].startswith("Prenotazione aggiornata") and "16:00" in m[-1]["message"],m[-1]
    # 5. the client cancels from the site's link: the email and the owner's notice from the site, the PC cancels it at its sync
    link=link_of([x for x in m if x["to"]=="marco@example.invalid"][-1])
    assert "Annulla la prenotazione" in get(link)
    assert "Prenotazione annullata" in get(link,{"do":"cancel"})
    m=mails();assert [x["to"] for x in m[-2:]]==["marco@example.invalid","titolare@example.invalid"] and m[-2]["subject"].startswith("Prenotazione annullata")
    wpsync.sync(t);t.refresh_from_db()
    with scope(t):
        d=Booking.objects.get(pk=desk.pk);assert d.status=="cancelled" and d.snapshot["cancel_reason"]=="client"
        assert not Delivery.objects.exists()
    assert "già annullata" in get(link) or "è annullata" in get(link)
    n=len(mails());wpsync.sync(t);assert len(mails())==n  # the PC's copy cancelled: no second email
    # 6. the reminder, from the site's cron, with Confermo: the PC gets the client's yes
    assert ctl("cron",str(int(b.start.timestamp())-23*3600)).strip()=="reminders sent: 1"
    rem=mails()[-1];assert rem["to"]=="lucia.b@example.invalid" and rem["subject"].startswith("Promemoria") and "Confermo" in rem["message"]
    yes=[u for u in re.findall(r'href="([^"]*\?tdbs=[^"]+)"',rem["message"])][0].replace("&amp;","&")
    assert "Grazie, ti aspettiamo" in get(yes,{"do":"confirm"})
    assert ctl("cron",str(int(b.start.timestamp())-23*3600)).strip()=="reminders sent: 0"  # once
    wpsync.sync(t);t.refresh_from_db()
    with scope(t):assert "client_confirmed" in Booking.objects.get(pk=b.pk).snapshot
    wpsync.sync(t)  # the ack goes with the next call
    assert json.loads(ctl("changes"))==[]  # acked, gone from the site

def test_with_the_pc_off_for_a_day(sample):
    MAILS.write_text("")
    t,o,_=sample
    with scope(t):
        loc,anna,bruno,taglio,colore=shop(t)
        Service.objects.filter(pk=colore.pk).update(request_all=True)  # Colore: the business confirms it
    wpsync.store(t,{"url":URL,"secret":SECRET,"days":5,"on":True,"mails":True,"mails_since":time.time_ns()//1000})
    Tenant.objects.filter(pk=t.pk).update(wp_archive=True);t.refresh_from_db()
    wpsync.sync(t,full=True);t.refresh_from_db();assert not t.wp_state.get("error"),t.wp_state
    day=day_of(taglio).isoformat()
    # the PC off: three bookings on the site, one cancelled by its client before the PC is back
    one=book(service=str(taglio.pk),option="",resource=str(anna.pk),any=False,date=day,time="09:00",first_name="Ada",last_name="Neri",email="ada@example.invalid",phone="347 100 0001")
    two=book(service=str(taglio.pk),option="",resource=str(bruno.pk),any=False,date=day,time="11:00",first_name="Bea",last_name="Gialli",email="bea@example.invalid",phone="347 100 0002")
    req=book(service=str(colore.pk),option="",resource=str(anna.pk),any=False,date=day,time="14:00",first_name="Cora",last_name="Blu",email="cora@example.invalid",phone="347 100 0003")
    assert one["ok"] and two["ok"] and "Richiesta ricevuta" in req["message"]
    m=mails();assert [x["subject"].split(" · ")[0] for x in m if x["to"]!="titolare@example.invalid"]==["Prenotazione confermata","Prenotazione confermata","Richiesta ricevuta"]
    ada=link_of([x for x in m if x["to"]=="ada@example.invalid"][0])
    assert "Prenotazione annullata" in get(ada,{"do":"cancel"})
    with scope(t):  # meanwhile, at the desk (the PC on but not online), Bea's time is taken
        who=User.objects.create_user("banco@example.invalid","A-test-password-12345");Membership.objects.create(tenant=t,user=who,role="owner",can_schedule=True)
        start=datetime.combine(day_of(taglio),clock(11),ZoneInfo(t.timezone))
        create_booking(t,{**payload(taglio,email="",phone="+393330002222",first_name="Dino",last_name="Rosa"),"start":start.isoformat(),"resource":str(bruno.pk)},actor=who)
    # the PC back a day later: two bookings, not Ada's; Bea's a request now, and the site tells her
    wpsync.sync(t);t.refresh_from_db();assert not t.wp_state.get("error"),t.wp_state
    with scope(t):
        got={b.data["first_name"]:b.status for b in Booking.objects.filter(origin="wordpress")}
        assert got=={"Bea":"pending","Cora":"pending"},got
        assert not Delivery.objects.exists()
    wpsync.sync(t)
    m=mails();bea=[x for x in m if x["to"]=="bea@example.invalid"]
    assert bea[-1]["subject"].startswith("La tua prenotazione va ricontrollata"),bea
    # the business accepts Cora's colour at the desk: the site confirms it to her
    with scope(t):
        from core.booking import change_status
        cora=Booking.objects.get(origin="wordpress",start__hour__gte=0,status="pending",service=colore)
        change_status(t,cora,"confirmed",who)
    wpsync.sync(t)
    assert [x for x in mails() if x["to"]=="cora@example.invalid"][-1]["subject"].startswith("Prenotazione confermata")
    assert json.loads(ctl("changes"))==[]
