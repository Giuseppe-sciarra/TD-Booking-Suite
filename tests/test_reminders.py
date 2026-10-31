"""2.14.0, fase 3: promemoria acceso di default con secondo invio e Confermo / Non vengo, Da chiamare, annullamento
automatico, spostamento dal link del cliente, email HTML con logo, prezzo, prima dell'arrivo, dopo la visita, avviso
all'operatore, riepilogo serale, file .ics."""
import importlib,io,re
from datetime import datetime,time,timedelta
from email import message_from_bytes,policy
from zoneinfo import ZoneInfo
import pytest
from django.apps import apps
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import decrypt,encrypt
from core.booking import create_booking,move_booking,change_status
from core.client_mails import remind,current
from core.notify import evening
from tests.test_core import sample,payload,client_as
from tests.test_phase3 import imported,stay_payload,tenant,next_weekday

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")

def mails(t,to=None):
    with scope(t):out=[decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in Delivery.objects.filter(channel="email").order_by("created")]
    return [m for m in out if to is None or m["to"]==to]
def link(mail,label):return re.search(label+r": (\S+)",mail["body"])[1].replace("http://localhost:8000","")
def get(t,pk):
    with scope(t):return Booking.objects.select_related("service").get(pk=pk)
def salon(t,cutoff=0):
    with scope(t):
        loc=Location.objects.first();loc.phone="06 1234567";loc.address="Via Roma 1, Roma";loc.save()
        svc=Service.objects.create(tenant=t,location=loc,name="Taglio",kind="resource",duration=30,interval=30,lead_minutes=60,cancel_cutoff_minutes=cutoff,price=30)
        marco=Resource.objects.create(tenant=t,location=loc,name="Marco");giulia=Resource.objects.create(tenant=t,location=loc,name="Giulia")
        for r in (marco,giulia):ServiceResource.objects.create(tenant=t,service=svc,resource=r)
    return svc,marco,giulia

def test_reminder_on_for_new_businesses_kept_for_the_others(sample):
    t,o,s=sample
    assert current(t)["reminder"]["on"] and not current(t)["reminder2"]["on"]  # a new business: on, 24 hours before
    rows={"stale":(False,{"reminder":{"on":True,"hours":48}}),"chosen":(True,{"reminder":{"on":True,"hours":48}}),"never":(True,{}),"plain":(False,{})}
    made={k:Tenant.objects.create(slug=k,name=k,client_mails_enabled=e,client_mails=m) for k,(e,m) in rows.items()}
    importlib.import_module("core.migrations.0027_reminders_and_notices").keep_reminders(apps,None)
    on={k:current(Tenant.objects.get(pk=v.pk))["reminder"]["on"] for k,v in made.items()}
    assert on=={"stale":False,"chosen":True,"never":False,"plain":False}  # what each one sent before 2.14.0
    assert Tenant.objects.get(slug="chosen").client_mails["reminder"]["hours"]==48

def test_second_reminder_once_and_both_again_when_moved(sample):
    t,o,s=sample  # the agency's switch is off: the owner still times the reminders
    t.client_mails={"reminder2":{"on":True,"hours":2}};t.save()
    with scope(t):b,_=create_booking(t,payload(s))
    assert remind(b.start-timedelta(hours=23))==1 and remind(b.start-timedelta(hours=1,minutes=50))==1
    assert remind(b.start-timedelta(hours=1))==0
    subjects=[m["subject"].split(" · ")[0] for m in mails(t,"mario@example.invalid")]
    assert subjects==["Prenotazione confermata","Promemoria","A tra poco"]
    with scope(t):
        moved=move_booking(t,b.pk,{"service":str(s.pk),"start":payload(s,hour=12)["start"],"party":1},None)
        assert not {"reminded","reminded2","asked"}&set(moved.snapshot)
    assert remind(moved.start-timedelta(hours=23))==1 and remind(moved.start-timedelta(hours=1))==1

def test_confirm_and_not_coming_from_the_reminder(sample):
    t,o,s=sample
    t.notify_email="staff@example.invalid";t.save()
    with scope(t):
        b,_=create_booking(t,payload(s))
        other,_=create_booking(t,payload(s,hour=11,email="anna@example.invalid"))
    remind(b.start-timedelta(hours=23))
    reminder=[m for m in mails(t,"mario@example.invalid") if m["subject"].startswith("Promemoria")][0]
    assert "Confermo: http" in reminder["body"] and "Non vengo: http" in reminder["body"] and ">Confermo</a>" in reminder["html"]
    c=Client();yes=link(reminder,"Confermo")
    assert "Confermo, ci sarò" in c.get(yes).content.decode()
    assert get(t,b.pk).snapshot.get("client_confirmed") is None  # opening the link (or a mail scanner) does nothing
    assert "ti aspettiamo" in c.post(yes).content.decode()
    first=get(t,b.pk).snapshot["client_confirmed"]
    assert c.post(yes).status_code==200 and get(t,b.pk).snapshot["client_confirmed"]==first  # once
    owner,u=client_as(t)
    assert "Confermata dal cliente" in owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    no=link([m for m in mails(t,"anna@example.invalid") if m["subject"].startswith("Promemoria")][0],"Non vengo")
    c.post(no)
    gone=get(t,other.pk);assert gone.status=="cancelled" and gone.snapshot["cancel_reason"]=="client"
    assert any(m["subject"].startswith("Prenotazione annullata") for m in mails(t,"staff@example.invalid"))

def test_not_coming_too_late_tells_the_business(sample):
    t,o,s=sample
    t.notify_email="staff@example.invalid";t.save()
    with scope(t):
        Location.objects.update(phone="06 7654321")
        s.cancel_cutoff_minutes=7*1440;s.save()  # inside the cutoff from the start
        b,_=create_booking(t,payload(s))
    remind(b.start-timedelta(hours=23))
    no=link([m for m in mails(t,"mario@example.invalid") if m["subject"].startswith("Promemoria")][0],"Non vengo")
    c=Client()
    assert "Da qui puoi comunque avvisare" in c.get(no).content.decode()
    page=c.post(no).content.decode()
    assert "06 7654321" in page
    b=get(t,b.pk);assert b.status=="confirmed" and b.snapshot["declined"]  # not cancelled by itself
    assert [m for m in mails(t,"staff@example.invalid") if m["subject"].startswith("Il cliente dice che non viene")]
    owner,u=client_as(t)
    assert "Il cliente ha scritto che non viene" in owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()

def test_unconfirmed_stay_unless_the_business_cancels_them(sample):
    t,o,s=sample
    with scope(t):
        b,_=create_booking(t,payload(s))
        yes,_=create_booking(t,payload(s,hour=11,email="anna@example.invalid"))
        late,_=create_booking(t,payload(s,hour=12,email="bea@example.invalid"))
    remind(b.start-timedelta(hours=23))
    with scope(t):
        Booking.objects.filter(pk=yes.pk).update(snapshot={**get(t,yes.pk).snapshot,"client_confirmed":timezone.now().isoformat()})
        # asked when the last 6 hours had begun already: he had no time to answer
        Booking.objects.filter(pk=late.pk).update(snapshot={**get(t,late.pk).snapshot,"asked":(late.start-timedelta(hours=5)).isoformat()})
    remind(b.start-timedelta(hours=5))
    assert get(t,b.pk).status=="confirmed"  # off by default, as resOS
    t.client_mails={"reminder":{"autocancel":6}};t.save()
    remind(b.start-timedelta(hours=5))
    gone=get(t,b.pk);assert gone.status=="cancelled" and gone.snapshot["cancel_reason"]=="unconfirmed"
    assert get(t,yes.pk).status=="confirmed" and get(t,late.pk).status=="confirmed"
    assert mails(t,"mario@example.invalid")[-1]["body"].startswith("Non abbiamo ricevuto la tua conferma.")
    owner,u=client_as(t)
    r=owner.post(f"/t/{t.slug}/notifications/",{"action":"client_mails","reminder_on":"on","reminder_hours":"6","reminder_autocancel":"6"},follow=True)
    assert "dopo il promemoria" in r.content.decode()

def test_the_list_of_who_to_call(sample):
    t,o,s=sample
    tomorrow=(timezone.now()+timedelta(days=1)).astimezone(ROME).date()
    with scope(t):
        b,_=create_booking(t,payload(s,first_name="Ginevra",start=datetime.combine(tomorrow,time(11),ROME).isoformat()))
        later,_=create_booking(t,payload(s,first_name="Ottavia",email="o@example.invalid"))  # in three days: not yet
        for x in (b,later):Booking.objects.filter(pk=x.pk).update(snapshot={**x.snapshot,"asked":timezone.now().isoformat()})
    owner,u=client_as(t)
    page=owner.get(f"/t/{t.slug}/").content.decode()
    calls=page[page.index('id="da-chiamare"'):page.index("</section>",page.index('id="da-chiamare"'))]
    assert "Ginevra" in calls and "https://wa.me/393331234567" in calls and 'href="tel:+393331234567"' in calls and "Ottavia" not in calls
    with scope(t):Booking.objects.filter(pk=b.pk).update(snapshot={**get(t,b.pk).snapshot,"client_confirmed":timezone.now().isoformat()})
    assert 'id="da-chiamare"' not in owner.get(f"/t/{t.slug}/").content.decode()

def test_the_client_moves_his_booking(sample):
    t,o,s=sample
    t.notify_email="staff@example.invalid";t.save()
    svc,marco,giulia=salon(t,cutoff=120)
    day=(timezone.now()+timedelta(days=3)).astimezone(ROME).date()
    with scope(t):b,_=create_booking(t,payload(svc,resource=str(marco.pk),start=datetime.combine(day,time(10),ROME).isoformat()))
    token=b.data["cancel_token"];base=f"/action/{t.slug}/{token}/"
    c=Client()
    page=c.get(base).content.decode()
    assert 'href="sposta/"' in page and "Via Roma 1" in page and "calendar.google.com" in page and 'href="calendario.ics"' in page
    assert '"locked": true' in c.get(base+"sposta/").content.decode()
    slots=c.get(base+"sposta/orari/",{"date":day.isoformat(),"service":str(s.pk)}).json()["slots"]
    assert slots and {x["resource"] for x in slots}=={str(marco.pk)}  # same service and operator, unless he asks another
    soon=(timezone.now()+timedelta(minutes=20)).astimezone(ROME).replace(second=0,microsecond=0)
    r=c.post(base+"sposta/",{"start":soon.isoformat(),"service":str(s.pk)},content_type="application/json",HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)
    assert r.status_code==400  # the public page's lead time
    new=datetime.combine(day,time(15),ROME)
    r=c.post(base+"sposta/",{"start":new.isoformat()},content_type="application/json",HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)
    assert r.json()["redirect"]==base+"?spostata=1"
    moved=get(t,b.pk);assert moved.start==new and moved.resource_id==marco.pk and moved.service_id==svc.pk
    assert "la prenotazione è spostata" in c.get(r.json()["redirect"]).content.decode()
    to_client=mails(t,"mario@example.invalid")[-1]
    assert to_client["subject"].startswith("Prenotazione aggiornata") and "BEGIN:VEVENT" in to_client["ics"]
    assert mails(t,"staff@example.invalid")[-1]["subject"].startswith("Prenotazione spostata")
    with scope(t):Booking.objects.filter(pk=b.pk).update(snapshot={**moved.snapshot,"policy":{"cancel_cutoff_minutes":5*1440}})
    page=c.get(base).content.decode()
    assert 'href="sposta/"' not in page and "06 1234567" in page
    assert c.get(base+"sposta/").status_code==400

def test_first_available_moves_to_whoever_is_free(sample):
    t,o,s=sample
    svc,marco,giulia=salon(t)
    day=(timezone.now()+timedelta(days=3)).astimezone(ROME).date()
    with scope(t):
        b,_=create_booking(t,payload(svc,resource="",start=datetime.combine(day,time(10),ROME).isoformat()))
        busy,_=create_booking(t,payload(svc,resource=str(b.resource_id),email="x@example.invalid",start=datetime.combine(day,time(16),ROME).isoformat()))
    assert b.snapshot["any_resource"]
    c=Client();base=f"/action/{t.slug}/{b.data['cancel_token']}/"
    c.get(base+"sposta/")
    r=c.post(base+"sposta/",{"start":datetime.combine(day,time(16),ROME).isoformat()},content_type="application/json",HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)
    assert r.status_code==200 and get(t,b.pk).resource_id!=busy.resource_id

def test_stays_and_events_move_by_their_rules(tenant):
    imported(tenant)  # two nights at least, 48 hours to cancel
    with scope(tenant):
        camere=Service.objects.get(name="Camere")
        arrival=next_weekday(0,8)
        b,_=create_booking(tenant,stay_payload(camere,arrival,2))
    c=Client();base=f"/action/{tenant.slug}/{b.data['cancel_token']}/"
    c.get(base+"sposta/");csrf=c.cookies["csrftoken"].value
    one=c.post(base+"sposta/",{"start":datetime.combine(arrival+timedelta(days=7),camere.checkin,ROME).isoformat(),"end_date":(arrival+timedelta(days=8)).isoformat()},content_type="application/json",HTTP_X_CSRFTOKEN=csrf)
    assert one.status_code==400 and "minima" in one.json()["error"]
    three=c.post(base+"sposta/",{"start":datetime.combine(arrival+timedelta(days=7),camere.checkin,ROME).isoformat(),"end_date":(arrival+timedelta(days=10)).isoformat()},content_type="application/json",HTTP_X_CSRFTOKEN=csrf)
    assert three.status_code==200 and (get(tenant,b.pk).end.astimezone(ROME).date()-arrival).days==10
    with scope(tenant):
        loc=Location.objects.first()
        tour=Service.objects.create(tenant=tenant,location=loc,name="Tour",kind="event",max_party=2,lead_minutes=0)
        day=next_weekday(2,2)
        first,second,full=[Occurrence.objects.create(tenant=tenant,service=tour,start=datetime.combine(day+timedelta(days=i),time(10),ROME),end=datetime.combine(day+timedelta(days=i),time(12),ROME),capacity=cap) for i,cap in ((0,5),(1,5),(2,1))]
        e,_=create_booking(tenant,payload(tour,start=first.start.isoformat(),occurrence=str(first.pk)))
        create_booking(tenant,payload(tour,start=full.start.isoformat(),occurrence=str(full.pk),email="altro@example.invalid"))
    base=f"/action/{tenant.slug}/{e.data['cancel_token']}/";c.get(base+"sposta/");csrf=c.cookies["csrftoken"].value
    move=lambda occ:c.post(base+"sposta/",{"start":occ.start.isoformat(),"occurrence":str(occ.pk)},content_type="application/json",HTTP_X_CSRFTOKEN=csrf)
    assert move(full).status_code==400 and move(second).status_code==200 and get(tenant,e.pk).occurrence_id==second.pk

def test_a_request_moved_by_the_client_stays_a_request(sample):
    t,o,s=sample
    with scope(t):
        s.request_all=True;s.save()
        b,_=create_booking(t,payload(s))
    c=Client();base=f"/action/{t.slug}/{b.data['cancel_token']}/";c.get(base+"sposta/")
    r=c.post(base+"sposta/",{"start":payload(s,hour=15)["start"]},content_type="application/json",HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)
    assert r.status_code==200 and get(t,b.pk).status=="pending"
    assert "calendar.google.com" not in c.get(base).content.decode()  # not in the calendar until the business says yes

def test_html_part_with_the_logo_inside_and_escaped(sample,monkeypatch):
    from PIL import Image
    from core.mail import send_message,worker_once
    t,o,s=sample
    png=io.BytesIO();Image.new("RGBA",(8,8),(200,0,0,255)).save(png,format="PNG")
    t.name="Bar <Sole> & Luna";t.logo=png.getvalue();t.save()
    with scope(t):b,_=create_booking(t,payload(s,first_name="<b>Mario</b>"))
    remind(b.start-timedelta(hours=23))
    confirmed,reminder=mails(t,"mario@example.invalid")
    # the text of always, as the plain part
    assert confirmed["body"].startswith("La prenotazione è confermata.\nServizio\n") and confirmed["body"].endswith(f"Gestisci o annulla: http://localhost:8000/action/{t.slug}/{b.data['cancel_token']}/")
    assert "&lt;b&gt;Mario&lt;/b&gt;" in reminder["html"] and "<b>Mario</b>" not in reminder["html"] and "Bar &lt;Sole&gt; &amp; Luna" in reminder["html"]
    assert 'src="cid:logo"' in reminder["html"] and "http" not in reminder["html"].split("cid:logo")[0]  # nothing to load from outside
    seen=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:seen.append(p))
    t.smtp=encrypt(t,{"host":"smtp.example.invalid","port":587,"mode":"starttls","username":"","password":"","sender":"info@example.invalid"},"smtp");t.save()
    worker_once()
    assert all(p["logo"]==t.logo for p in seen if p.get("html")) and any(p.get("html") for p in seen)
    sent=[];monkeypatch.setattr("core.mailapi.send",lambda conf,message:sent.append(message.as_bytes()))
    send_message({"method":"graph","sender":"info@example.invalid"},{**confirmed,"logo":t.logo},"<x@localhost>")
    message=message_from_bytes(sent[0],policy=policy.default)
    assert message.get_body(("plain",)).get_content().rstrip("\n")==confirmed["body"]
    assert "cid:logo" in message.get_body(("html",)).get_content()
    parts={p.get_content_type():p for p in message.walk()}
    assert parts["image/png"]["Content-ID"]=="<logo>" and parts["image/png"].get_content()==t.logo
    assert parts["text/calendar"].get_param("method")=="PUBLISH" and parts["text/calendar"].get_filename()=="prenotazione.ics"

def test_the_indicative_price_in_the_confirmation(sample):
    t,o,s=sample
    with scope(t):
        s.price=30;s.save()
        create_booking(t,payload(s,party=2))
    assert "Prezzo" not in mails(t)[-1]["body"]
    t.client_mails={"confirmed":{"price":True}};t.save()  # the owner's own switch, without the agency's
    with scope(t):create_booking(t,payload(s,hour=12,party=2))
    assert "Prezzo indicativo: 30,00 €, si paga in sede" in mails(t,"mario@example.invalid")[-1]["body"]  # the booking's price, not per person
    with scope(t):
        s.request_all=True;s.save()
        b,_=create_booking(t,payload(s,hour=14,email="anna@example.invalid"))
        change_status(t,b,"confirmed")
    assert "Prezzo indicativo: 30,00 €" in mails(t,"anna@example.invalid")[-1]["body"]  # a request accepted is its confirmation

def test_instructions_before_the_arrival(tenant):
    imported(tenant)
    tenant.client_mails={"arrival":{"on":True,"hours":48}};tenant.save()
    with scope(tenant):
        camere=Service.objects.get(name="Camere");camere.instructions="Chiavi in reception, citofono 3";camere.save()
        tomorrow=(timezone.now()+timedelta(days=1)).astimezone(ROME).date()
        b,_=create_booking(tenant,stay_payload(camere,tomorrow,2))  # at the last minute: the instructions go anyway
        far,_=create_booking(tenant,stay_payload(camere,tomorrow+timedelta(days=20),2))
    remind();remind()
    arrival=[m for m in mails(tenant) if m["subject"].startswith("Prima del tuo arrivo")]
    assert len(arrival)==1 and arrival[0]["to"]==b.data["email"] and "Chiavi in reception, citofono 3" in arrival[0]["body"]
    with scope(tenant):camere.instructions="";camere.save();Booking.objects.filter(pk=far.pk).update(start=b.start+timedelta(hours=1))
    remind()
    assert len([m for m in mails(tenant) if m["subject"].startswith("Prima del tuo arrivo")])==1  # no instructions, no message

def test_after_the_visit_only_with_consent_and_once_a_week(sample):
    t,o,s=sample
    t.client_mails={"review":{"on":True,"hours":3,"url":"https://g.page/r/test/review"}};t.save()
    def done(hour,email,ago=4,consent="yes"):
        with scope(t):
            b,_=create_booking(t,payload(s,hour=hour,email=email))
            end=timezone.now()-timedelta(hours=ago)
            Booking.objects.filter(pk=b.pk).update(start=end-timedelta(hours=1),end=end,status="completed")
            Contact.objects.filter(pk=b.contact_id).update(consent=consent)
        return b
    first=done(10,"mario@example.invalid");done(11,"mario@example.invalid");done(12,"anna@example.invalid",consent="none")
    done(13,"bea@example.invalid",ago=2)  # ended 2 hours ago: not yet
    remind()
    reviews=[m for m in mails(t) if m["subject"].startswith("Com'è andata")]
    assert [m["to"] for m in reviews]==["mario@example.invalid"] and "Lascia una recensione: https://g.page/r/test/review" in reviews[0]["body"]
    assert remind()==0
    with scope(t):
        for x in Booking.objects.filter(contact_id=first.contact_id):
            if x.snapshot.get("review"):Booking.objects.filter(pk=x.pk).update(snapshot={**x.snapshot,"review":(timezone.now()-timedelta(days=8)).isoformat()})
    done(14,"mario@example.invalid");remind()
    assert len([m for m in mails(t,"mario@example.invalid") if m["subject"].startswith("Com'è andata")])==2  # a week later, again
    owner,u=client_as(t)
    r=owner.post(f"/t/{t.slug}/notifications/",{"action":"client_mails","review_on":"on","review_hours":"3","review_url":"http://g.page/x"},follow=True)
    assert "deve iniziare con https://" in r.content.decode()

def operator(t,resource):
    u=User.objects.create_user("marco@example.invalid","A-test-password-12345")
    Membership.objects.create(tenant=t,user=u,role="operator",resources=[str(resource.pk)])

def test_the_operator_is_told_too(sample):
    t,o,s=sample
    svc,marco,giulia=salon(t);operator(t,marco)
    t.notify_email="staff@example.invalid";t.save()
    day=(timezone.now()+timedelta(days=3)).astimezone(ROME).date()
    with scope(t):create_booking(t,payload(svc,resource=str(marco.pk),start=datetime.combine(day,time(10),ROME).isoformat()))
    assert not mails(t,"marco@example.invalid")  # off by default
    t.notify_operators=True;t.save()
    with scope(t):
        create_booking(t,payload(svc,resource=str(marco.pk),email="a@example.invalid",start=datetime.combine(day,time(11),ROME).isoformat()))
        create_booking(t,payload(svc,resource=str(giulia.pk),email="b@example.invalid",start=datetime.combine(day,time(11),ROME).isoformat()))
    told=mails(t,"marco@example.invalid")
    assert len(told)==1 and told[0]["subject"].startswith("Nuova prenotazione") and "Marco" in told[0]["body"] and "a@example.invalid" not in told[0]["body"]
    assert len([m for m in mails(t,"staff@example.invalid") if m["subject"].startswith("Nuova prenotazione")])==3

def test_the_evening_list_once_a_day_from_its_hour(sample):
    t,o,s=sample
    svc,marco,giulia=salon(t);operator(t,marco)
    t.notify_email="staff@example.invalid";t.notify_operators=True;t.summary_hour=19
    t.telegram=encrypt(t,{"token":"1"*9+":"+"A"*35,"chat":"-100"},"telegram");t.save()
    today=timezone.now().astimezone(ROME).date();tomorrow=today+timedelta(days=1)
    with scope(t):
        create_booking(t,payload(svc,resource=str(marco.pk),start=datetime.combine(tomorrow,time(10),ROME).isoformat()))
        create_booking(t,payload(svc,resource=str(giulia.pk),first_name="Anna",email="anna@example.invalid",start=datetime.combine(tomorrow,time(11),ROME).isoformat()))
        Delivery.objects.all().delete()
    assert evening(datetime.combine(today,time(18,30),ROME))==0
    assert evening(datetime.combine(today,time(19,5),ROME))==1 and evening(datetime.combine(today,time(21),ROME))==0
    staff,=mails(t,"staff@example.invalid");own,=mails(t,"marco@example.invalid")
    assert staff["subject"]==f"Domani, {tomorrow:%d/%m} · {t.name}" and "10:00 · Taglio · Marco · Mario R." in staff["body"] and "11:00" in staff["body"]
    assert "mario@example.invalid" not in staff["body"] and "11:00" not in own["body"] and "10:00" in own["body"]
    with scope(t):assert Delivery.objects.filter(channel="telegram").count()==1
    assert evening(datetime.combine(tomorrow,time(19,1),ROME))==1  # the next evening, the next day

def test_calendar_file_by_hand(sample):
    from core.ics import ics
    t,o,s=sample
    with scope(t):
        loc=Location.objects.first();loc.address="Piazza della Libertà 10, scala B; interno 7 — accanto alla farmacia comunale, Roma";loc.save()
        s.name="Taglio, piega; colore";s.save()
        b,_=create_booking(t,payload(s))
        b=Booking.objects.select_related("location","service","tenant").get(pk=b.pk)
        text=ics(b)
    lines=text.split("\r\n")
    assert text.endswith("\r\n") and all(len(x.encode())<=75 for x in lines)
    unfolded=text.replace("\r\n ","")
    assert "METHOD:PUBLISH" in lines and f"UID:{b.pk}@localhost" in lines and f"DTSTART:{b.start.astimezone(ZoneInfo('UTC')):%Y%m%dT%H%M%SZ}" in lines
    assert "SUMMARY:Taglio\\, piega\\; colore · Salone Test" in unfolded and "Piazza della Libertà 10\\, scala B\\; interno 7" in unfolded
    assert "Mario" not in text and "mario@" not in text and "+39" not in text
    page=Client().get(f"/action/{t.slug}/{b.data['cancel_token']}/calendario.ics")
    stamp=lambda x:re.sub("DTSTAMP:\\w+","",x)
    assert page["Content-Type"].startswith("text/calendar") and stamp(page.content.decode())==stamp(text)
    with scope(t):change_status(t,Booking.objects.get(pk=b.pk),"cancelled")
    assert Client().get(f"/action/{t.slug}/{b.data['cancel_token']}/calendario.ics").status_code==404

def test_calendar_links_on_the_final_page(sample,settings):
    t,o,s=sample
    c=Client();page=c.get(f"/book/{t.slug}/")
    r=c.post(f"/book/{t.slug}/api/submit/",payload(s),content_type="application/json",HTTP_ORIGIN=settings.PUBLIC_ORIGIN,HTTP_X_TDBS_TICKET=page.context["widget"]["ticket"])
    cal=r.json()["calendar"]
    assert cal["google"].startswith("https://calendar.google.com/calendar/render?action=TEMPLATE") and cal["outlook"].startswith("https://outlook.live.com/calendar/action/compose?")
    assert cal["ics"]==r.json()["cancel"]+"calendario.ics" and 'id="calendar-links"' in page.content.decode()
