"""Business notifications (email, Telegram) and activity presets, one per kind of business."""
import json,secrets,urllib.error
from datetime import datetime,time as clock,timedelta
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import encrypt,decrypt
from core.booking import create_booking,change_status,move_booking
from core.mail import worker_once
from tests.test_core import sample,payload,client_as

pytestmark=pytest.mark.django_db
SMTP={"host":"smtp.example.invalid","port":587,"mode":"starttls","username":"","password":"","sender":"test@example.invalid","reply_to":""}
TOKEN="123456789:"+"A"*35

def configure(t,email="staff@example.invalid",telegram=True):
    t.smtp=encrypt(t,SMTP,"smtp");t.notify_email=email
    t.telegram=encrypt(t,{"token":TOKEN,"chat":"-1001234567890"},"telegram") if telegram else ""
    t.save()

class FakeTelegram:
    def __init__(self,fail=None):self.calls=[];self.fail=fail
    def __call__(self,request,timeout=10):
        self.calls.append((request.full_url,request.data.decode()))
        if self.fail:raise self.fail
        class R:
            def read(self_):return b'{"ok":true}'
            def __enter__(self_):return self_
            def __exit__(self_,*a):return False
        return R()

def test_staff_notifications_are_minimal(sample,monkeypatch):
    t,o,s=sample;configure(t)
    with scope(t):b,_=create_booking(t,payload(s,notes="Allergia nichel"))
    mails=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:mails.append(p))
    tg=FakeTelegram();monkeypatch.setattr("urllib.request.urlopen",tg)
    worker_once()
    staff=[m for m in mails if m["to"]=="staff@example.invalid"]
    assert len(staff)==1 and len(tg.calls)==1
    for text in (staff[0]["body"],tg.calls[0][1]):
        from urllib.parse import unquote_plus
        text=unquote_plus(text)
        assert "Nuova prenotazione" in text and "Mario R." in text and "Servizio" in text and f"/bookings/{b.pk}/" in text
        assert "Rossi" not in text and "mario@example.invalid" not in text and "3331234567" not in text and "nichel" not in text
    assert tg.calls[0][0]==f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    with scope(t):
        d=Delivery.objects.get(channel="telegram")
        assert d.status=="accepted" and TOKEN not in json.dumps(decrypt(t,d.payload,"delivery:"+str(d.pk)))

def test_moved_and_cancelled_are_notified(sample,monkeypatch):
    t,o,s=sample;configure(t,telegram=False)
    with scope(t):
        b,_=create_booking(t,payload(s))
        move_booking(t,b.pk,{"service":str(s.pk),"start":payload(s,hour=12)["start"],"party":1},None)
        change_status(t,Booking.objects.get(pk=b.pk),"cancelled")
    mails=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:mails.append(p))
    worker_once()
    subjects=[m["subject"] for m in mails if m["to"]=="staff@example.invalid"]
    assert sorted(x.split(" · ")[0] for x in subjects)==["Nuova prenotazione","Prenotazione annullata","Prenotazione spostata"]

def test_no_configuration_no_staff_messages(sample,monkeypatch):
    t,o,s=sample;t.smtp=encrypt(t,SMTP,"smtp");t.save()
    with scope(t):create_booking(t,payload(s))
    mails=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:mails.append(p))
    worker_once()
    assert [m["to"] for m in mails]==["mario@example.invalid"]

@pytest.mark.parametrize("error,status,retry",[
    (urllib.error.HTTPError("u",429,"Too Many",{},None),"pending",True),
    (urllib.error.HTTPError("u",403,"Forbidden",{},None),"failed",False),
    (TimeoutError(),"unknown",False)])
def test_telegram_errors(sample,monkeypatch,error,status,retry):
    t,o,s=sample;configure(t,email="")
    with scope(t):create_booking(t,payload(s))
    monkeypatch.setattr("core.mail.send_message",lambda *a:None)
    tg=FakeTelegram(fail=error);monkeypatch.setattr("urllib.request.urlopen",tg)
    worker_once();worker_once()
    with scope(t):
        d=Delivery.objects.get(channel="telegram")
        assert d.status==status and (d.next_attempt>timezone.now())==retry
    assert len(tg.calls)==1  # never resent automatically when the outcome is uncertain or final

def test_telegram_does_not_consume_email_quota(sample,monkeypatch):
    t,o,s=sample;configure(t,email="");t.smtp_daily_limit=1;t.smtp_hourly_limit=1;t.save()
    with scope(t):create_booking(t,payload(s));create_booking(t,payload(s,hour=12,email="lucia@example.invalid"))
    mails=[];monkeypatch.setattr("core.mail.send_message",lambda conf,p,mid:mails.append(p))
    tg=FakeTelegram();monkeypatch.setattr("urllib.request.urlopen",tg)
    worker_once()
    assert len(mails)==1 and len(tg.calls)==2

def test_notifications_page(sample):
    t,o,s=sample
    c,owner=client_as(t,role="owner")
    assert c.get(f"/t/{t.slug}/notifications/").status_code==200
    r=c.post(f"/t/{t.slug}/notifications/",{"notify_email":"staff@example.invalid","telegram_token":"non-un-token","telegram_chat":"-100"})
    assert "non validi" in r.content.decode()
    r=c.post(f"/t/{t.slug}/notifications/",{"notify_email":"staff@example.invalid","telegram_token":TOKEN,"telegram_chat":"-1001234567890"})
    assert r.status_code==302
    t.refresh_from_db()
    assert t.notify_email=="staff@example.invalid" and TOKEN not in t.telegram and decrypt(t,t.telegram,"telegram")["token"]==TOKEN
    page=c.get(f"/t/{t.slug}/notifications/").content.decode()
    assert TOKEN not in page and "salvato" in page
    c.post(f"/t/{t.slug}/notifications/",{"notify_email":"staff@example.invalid","telegram_chat":"-1001234567890"})  # empty token keeps it
    t.refresh_from_db();assert decrypt(t,t.telegram,"telegram")["token"]==TOKEN
    c.post(f"/t/{t.slug}/notifications/",{"notify_email":"","telegram_off":"on"})
    t.refresh_from_db();assert t.telegram=="" and t.notify_email==""
    op,_=client_as(t,role="reception")
    assert op.get(f"/t/{t.slug}/notifications/").status_code==403

# ---------- presets
def test_presets_cover_all_categories():
    from core.presets import PRESETS
    expected={"ristorante","parrucchiere","medico","consulenza","centro_estetico","spa","tattoo","autoscuola","campo","corso","officina","hotel","casa_vacanze","noleggio","evento","tour"}
    assert set(PRESETS)==expected

def test_new_business_from_preset():
    from core.platform import seed_tenant
    t=Tenant.objects.create(slug="hotel-test",name="Hotel Test")
    seed_tenant(t,"hotel")
    with scope(t):
        svc=Service.objects.get()
        assert (svc.kind,svc.resource_label,svc.checkin,svc.max_party,svc.preset)==("day","Camera",clock(15),6,"hotel")
        # the hotel type's own catalog, as Configura l'attività makes it: one Camera service, three rooms
        assert svc.name=="Camera" and list(Resource.objects.order_by("name").values_list("name",flat=True))==["Camera 1","Camera 2","Camera 3"]
        arrival=(timezone.now()+timedelta(days=10)).astimezone(ZoneInfo(t.timezone)).date()
        data={"service":str(svc.pk),"start":datetime.combine(arrival,svc.checkin,ZoneInfo(t.timezone)).isoformat(),"end_date":(arrival+timedelta(days=2)).isoformat(),
            "party":2,"first_name":"Ospite","last_name":"Test","email":"o@example.invalid","phone":"+393331234567","request_key":secrets.token_urlsafe(24)}
        b,_=create_booking(t,data)
        assert b.resource.name=="Camera 1"

def test_catalog_preset_prefills_form(sample):
    # 2.6.0: templates and the kind of booking are the agency's choice; the business never sees them
    from tests.test_phase2 import staff_client
    t,o,s=sample
    c,_=client_as(t,role="owner")
    page=c.get(f"/t/{t.slug}/catalog/services/").content.decode()
    assert "da un modello" not in page and "Tipo di attività e sedi" not in page
    form=c.get(f"/t/{t.slug}/catalog/services/new/?preset=parrucchiere").content.decode()
    assert 'name="kind"' not in form and 'value="Operatore"' not in form
    sc,_=staff_client()
    page=sc.get(f"/t/{t.slug}/catalog/services/").content.decode()
    assert "Aggiungi un solo servizio da un modello" in page and "?preset=parrucchiere" in page and "#tipo" in page
    form=sc.get(f"/t/{t.slug}/catalog/services/new/?preset=parrucchiere").content.decode()
    assert 'value="Operatore"' in form and 'value="resource" selected' in form

def test_widget_uses_resource_label(sample):
    t,o,s=sample
    with scope(t):s.resource_label="Campo";s.save()
    page=Client().get(f"/book/{t.slug}/").content.decode()
    assert '"resource_label": "Campo"' in page
