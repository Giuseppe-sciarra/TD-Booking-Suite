"""2.14.0, fase 5: l'agenda da banco. Trascinare e allungare un appuntamento con le regole del banco e Forza, la durata
che resta sua senza disturbare il cliente, l'aggiornamento che non tiene aperta la sessione, il foglio del giorno con
quello che il ruolo può leggere, le annullate a richiesta."""
import json,time as wallclock
from datetime import datetime,timedelta
import pytest
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,change_status,BookingError
from tests.test_core import sample,payload,client_as
from tests.test_desk import clock,at,salon,sent

pytestmark=pytest.mark.django_db

def drop(c,t,b,**data):
    return c.post(f"/t/{t.slug}/agenda/sposta/{b.pk}/",json.dumps(data),content_type="application/json")

def second(t,svc,name="Luca"):
    with scope(t):
        r=Resource.objects.create(tenant=t,location=svc.location,name=name);ServiceResource.objects.create(tenant=t,service=svc,resource=r)
    return r

def test_dragged_with_the_desk_rules_to_another_operator(sample,clock):
    t,o,s=sample
    svc,marco=salon(t);luca=second(t,svc)
    owner,_=client_as(t)
    today=clock[0].date()
    with scope(t):
        b,_=create_booking(t,payload(svc,start=at(today+timedelta(days=1),11),resource=str(marco.pk)),None)
        Booking.objects.filter(pk=b.pk).update(snapshot={**b.snapshot,"client_confirmed":"x","reminded":"x"})
    page=owner.get(f"/t/{t.slug}/bookings/?vista=giorno&data={(today+timedelta(days=1)).isoformat()}").content.decode()
    assert f'data-move="/t/{t.slug}/agenda/sposta/{b.pk}/" data-start="11:00" data-minutes="30" data-snap="30"' in page and f'data-drop="{luca.pk}"' in page
    before=len(sent(t))
    # today at 10:30: inside the online page's hour of notice, the desk books it; with Luca, for 45 minutes
    r=drop(owner,t,b,date=today.isoformat(),time="10:30",resource=str(luca.pk),minutes=45)
    assert r.status_code==200 and r.json()=={"ok":True}
    with scope(t):
        b.refresh_from_db()
        assert b.resource==luca and b.start==datetime.fromisoformat(at(today,10,30)) and b.end-b.start==timedelta(minutes=45) and b.snapshot["resource"]=="Luca"
        # a new time: the reminders and the client's Confermo start again
        assert "client_confirmed" not in b.snapshot and "reminded" not in b.snapshot
        assert Audit.objects.filter(action="booking.moved",target=str(b.pk)).exists()
    assert sent(t)[before]["subject"].startswith("Prenotazione aggiornata")

def test_refused_with_the_reason_forced_with_the_permission(sample,clock):
    t,o,s=sample
    svc,marco=salon(t)
    owner,_=client_as(t);desk,_=client_as(t,role="reception")
    day=clock[0].date()+timedelta(days=1)
    with scope(t):
        a,_=create_booking(t,payload(svc,start=at(day,11),resource=str(marco.pk)),None)
        b,_=create_booking(t,payload(svc,start=at(day,15),resource=str(marco.pk),email="b@example.invalid"),None)
    r=drop(desk,t,a,date=day.isoformat(),time="15:00",minutes=30)
    assert r.status_code==400 and "sovrapposta a un altro appuntamento di Marco" in r.json()["error"]
    r=drop(desk,t,a,date=day.isoformat(),time="17:45",minutes=30)
    assert r.status_code==400 and "fuori dall'orario di apertura" in r.json()["error"]
    assert drop(desk,t,a,date=day.isoformat(),time="15:00",minutes=30,force=True).status_code==403  # reception does not force
    r=drop(owner,t,a,date=day.isoformat(),time="15:00",minutes=30,force=True)
    assert r.status_code==409 and r.json()["force"] and r.json()["error"].startswith("Stai forzando: sovrapposta")
    with scope(t):assert Booking.objects.get(pk=a.pk).start==datetime.fromisoformat(at(day,11))  # nothing saved before the second yes
    assert drop(owner,t,a,date=day.isoformat(),time="15:00",minutes=30,force=True,force_ok=True).status_code==200
    with scope(t):
        a.refresh_from_db()
        assert a.start==datetime.fromisoformat(at(day,15)) and a.snapshot["forced"]==["sovrapposta a un altro appuntamento di Marco"]
        assert Audit.objects.get(action="booking.moved",target=str(a.pk)).detail["forced"]
    assert drop(owner,t,b,date="ieri",time="15:00").json()["error"]=="Giorno o ora non validi."
    assert drop(owner,t,b,date=day.isoformat(),time="16:00",minutes=2).json()["error"]=="Durata fuori intervallo."
    with scope(t):change_status(t,b,"cancelled")
    assert drop(owner,t,b,date=day.isoformat(),time="16:00",minutes=30).status_code==404  # closed: it does not move any more

def test_stretched_it_keeps_its_length_and_the_client_hears_nothing(sample,clock):
    t,o,s=sample
    svc,marco=salon(t)
    with scope(t):Service.objects.filter(pk=svc.pk).update(buffer_after=10)
    owner,_=client_as(t)
    day=clock[0].date()+timedelta(days=1)
    with scope(t):
        b,_=create_booking(t,payload(svc,start=at(day,11),resource=str(marco.pk)),None)
        Booking.objects.filter(pk=b.pk).update(snapshot={**b.snapshot,"client_confirmed":"x","reminded":"x"})
    before=len(sent(t))
    assert drop(owner,t,b,date=day.isoformat(),time="11:00",minutes=75).status_code==200
    with scope(t):
        b.refresh_from_db()
        assert (b.start,b.end,b.busy_end)==(datetime.fromisoformat(at(day,11)),datetime.fromisoformat(at(day,12,15)),datetime.fromisoformat(at(day,12,25)))
        assert b.snapshot["client_confirmed"]=="x" and b.snapshot["reminded"]=="x"
        assert Audit.objects.get(action="booking.resized",target=str(b.pk)).detail["minutes"]==75
        # the new length holds the time: 12:00 is taken now
        with pytest.raises(BookingError):create_booking(t,payload(svc,start=at(day,12),resource=str(marco.pk),email="x@example.invalid"),None)
    assert len(sent(t))==before  # for the client nothing moved: no email
    page=owner.get(f"/t/{t.slug}/bookings/?vista=giorno&data={day.isoformat()}").content.decode()
    assert 'data-start="11:00" data-minutes="75"' in page and "11:00–12:15" in page
    # dragged later it keeps its 75 minutes (the agenda sends them), and that is a move for the client
    assert drop(owner,t,b,date=day.isoformat(),time="14:00",minutes=75).status_code==200
    with scope(t):b.refresh_from_db();assert b.end==datetime.fromisoformat(at(day,15,15)) and "client_confirmed" not in b.snapshot
    assert len(sent(t))==before+1

def test_only_who_books_and_inside_his_scope(sample,clock):
    t,o,s=sample
    svc,marco=salon(t);luca=second(t,svc)
    day=clock[0].date()+timedelta(days=1)
    with scope(t):b,_=create_booking(t,payload(svc,start=at(day,11),resource=str(marco.pk)),None)
    op,u=client_as(t,role="operator")
    with scope(t):Membership.objects.filter(user=u).update(resources=[str(marco.pk)])
    assert drop(op,t,b,date=day.isoformat(),time="14:00",resource=str(luca.pk),minutes=30).status_code==403
    assert drop(op,t,b,date=day.isoformat(),time="14:00",minutes=30).status_code==200  # in his own column
    viewer,_=client_as(t,role="viewer")
    assert drop(viewer,t,b,date=day.isoformat(),time="15:00",minutes=30).status_code==403
    assert "data-move=" not in viewer.get(f"/t/{t.slug}/bookings/?vista=giorno&data={day.isoformat()}").content.decode()
    stranger,_=client_as(o)
    assert drop(stranger,t,b,date=day.isoformat(),time="15:00",minutes=30).status_code==403
    assert drop(stranger,o,b,date=day.isoformat(),time="15:00",minutes=30).status_code==404  # not in his business
    with scope(t):assert Booking.objects.get(pk=b.pk).start==datetime.fromisoformat(at(day,14))

def test_the_refresh_keeps_the_role_and_does_not_keep_the_session_open(sample,clock):
    t,o,s=sample
    svc,marco=salon(t)
    day=clock[0].date()+timedelta(days=1)
    with scope(t):create_booking(t,payload(svc,start=at(day,11),resource=str(marco.pk),first_name="Giulia",last_name="Rossi"),None)
    viewer,_=client_as(t,role="viewer")
    url=f"/t/{t.slug}/bookings/?vista=giorno&data={day.isoformat()}"
    session=viewer.session;session["last_seen"]=wallclock.time()-1700;session.save()
    page=viewer.get(url,HTTP_X_TDBS_REFRESH="1").content.decode()
    assert 'data-refresh="agenda"' in page and "G. R." in page and "Giulia" not in page
    assert viewer.session["last_seen"]<wallclock.time()-1600  # the agenda refreshing itself is nobody at the screen
    viewer.get(url);assert viewer.session["last_seen"]>wallclock.time()-60
    session=viewer.session;session["last_seen"]=wallclock.time()-1900;session.save()
    assert viewer.get(url,HTTP_X_TDBS_REFRESH="1")["Location"].startswith("/login/")  # after the half hour it is closed
    other,_=client_as(o)
    assert other.get(url,HTTP_X_TDBS_REFRESH="1").status_code==403

def test_the_day_on_paper_shows_what_the_role_may_read(sample,clock):
    t,o,s=sample
    svc,marco=salon(t);luca=second(t,svc)
    day=clock[0].date()+timedelta(days=1)
    with scope(t):
        Tenant.objects.filter(pk=t.pk).update(allergens=["Nichel"],allergens_on=True,business_type="hotel")  # 2.22.0: allergens only where food is served
        create_booking(t,payload(svc,start=at(day,11),resource=str(marco.pk),first_name="Giulia",last_name="Rossi",allergies=["Nichel"],notes="Cute sensibile"),None)
        create_booking(t,payload(svc,start=at(day,12),resource=str(luca.pk),first_name="Anna",last_name="Verdi",email="anna@example.invalid",phone="+393339990000"),None)
    owner,_=client_as(t)
    url=f"/t/{t.slug}/agenda/stampa/?data={day.isoformat()}"
    assert url in owner.get(f"/t/{t.slug}/bookings/?vista=giorno&data={day.isoformat()}").content.decode().replace("&amp;","&")
    page=owner.get(url).content.decode()
    assert "<h2>Marco <small>1</small></h2>" in page and "<h2>Luca <small>1</small></h2>" in page
    assert "Giulia Rossi" in page and "+393331234567" in page and "Cute sensibile" in page and "Nichel" in page and "11:00–11:30" in page
    one=owner.get(url+f"&operatore={luca.pk}").content.decode()
    assert "Anna Verdi" in one and "Giulia" not in one
    viewer,_=client_as(t,role="viewer")
    page=viewer.get(url).content.decode()
    assert "G. R." in page and "Giulia" not in page and "+39333" not in page and "Telefono" not in page and "Cute sensibile" not in page and "Nichel" not in page
    op,u=client_as(t,role="operator")
    with scope(t):Membership.objects.filter(user=u).update(resources=[str(luca.pk)])
    page=op.get(url).content.decode()
    assert "Anna Verdi" in page and "Giulia" not in page and "+393339990000" in page
    assert client_as(o)[0].get(url).status_code==403

def test_statuses_signs_and_the_cancelled_on_request(sample,clock):
    t,o,s=sample
    svc,marco=salon(t)
    owner,_=client_as(t)
    day=clock[0].date()+timedelta(days=1)
    with scope(t):
        a,_=create_booking(t,payload(svc,start=at(day,11),resource=str(marco.pk),first_name="Elena"),None);change_status(t,a,"cancelled")
        b,_=create_booking(t,payload(svc,start=at(day,14),resource=str(marco.pk),email="b@example.invalid"),None)
        Booking.objects.filter(pk=b.pk).update(status="pending",snapshot={**b.snapshot,"forced":["fuori dall'orario di apertura"]})
    url=f"/t/{t.slug}/bookings/?vista=giorno&data={day.isoformat()}"
    page=owner.get(url).content.decode()
    assert "Elena" not in page and "1 appuntamento." in page and "Mostra le annullate" in page
    assert '<em class="agenda-state">Da confermare</em>' in page and 'class="agenda-forced" title="Forzata al banco: fuori dall&#x27;orario di apertura"' in page
    page=owner.get(url+"&annullate=1").content.decode()
    assert 'class="agenda-item op-1 cancelled"' in page and "Elena" in page and "Nascondi le annullate" in page and "&amp;annullate=1" in page
    # the place given up is free: the space books, the cancelled one does not move, it is not counted
    assert f"?operatore={marco.pk}&amp;data={day.isoformat()}&amp;ora=11:00" in page and page.count("data-move=")==1 and "1 appuntamento." in page
    week=owner.get(f"/t/{t.slug}/bookings/?vista=settimana&data={day.isoformat()}&annullate=1").content.decode()
    assert "Elena" in week and f'data-day="{day.isoformat()}"' in week
