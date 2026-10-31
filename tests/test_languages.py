"""2.14.0: the client's pages and emails in English (?lang=, the browser's language, kept on the booking); the staff
pages in Italian; the business's own texts as written."""
import json
from pathlib import Path
import pytest
from django.conf import settings
from django.test import Client
from django.utils import translation
from core.models import *
from core.tenancy import scope
from core.booking import change_status
from tests.test_core import sample,payload,client_as
from tests.test_client_mails import to_clients
from tests.test_waitlist import salon,book,waiting,cancel_desk

pytestmark=pytest.mark.django_db

def test_the_catalog_is_compiled():
    # a .po changed without scripts/compile_messages.py leaves the page half in Italian
    po=Path(settings.BASE_DIR)/"locale/en/LC_MESSAGES/django.po";key=None;n=0
    with translation.override("en"):
        for line in po.read_text(encoding="utf-8").splitlines():
            if line.startswith("msgid "):key=json.loads(line[6:])
            elif line.startswith("msgstr ") and key:assert translation.gettext(key)==json.loads(line[7:]),key;n+=1
    assert n>100

def test_english_page_booking_and_emails(sample):
    t,o,s=sample
    c=Client();page=c.get(f"/book/{t.slug}/?lang=en&service={s.pk}").content.decode()
    assert '<html lang="en"' in page and "Book with Salone Test" in page and "Confirm booking" in page and 'hreflang="it"' in page
    assert f"service={s.pk}" in page  # the switch keeps the page's own address
    assert '<html lang="en"' in Client(HTTP_ACCEPT_LANGUAGE="en-GB,en;q=0.9").get(f"/book/{t.slug}/").content.decode()
    assert '<html lang="it"' in Client(HTTP_ACCEPT_LANGUAGE="de-DE").get(f"/book/{t.slug}/").content.decode()
    widget=c.get(f"/book/{t.slug}/?lang=en").context["widget"];ticket=widget["ticket"]
    assert widget["submit"].endswith("?lang=en")
    r=c.post(widget["submit"],payload(s),content_type="application/json",HTTP_ORIGIN=settings.PUBLIC_ORIGIN,HTTP_X_TDBS_TICKET=ticket)
    with scope(t):b=Booking.objects.get(pk__startswith=r.json()["reference"])
    assert b.snapshot["lang"]=="en"
    # the later emails and the client's link in his language, without ?lang=
    with scope(t):change_status(t,Booking.objects.get(pk=b.pk),"cancelled")
    sent=to_clients(t)
    assert sent[0]["subject"]=="Booking confirmed · Salone Test" and "Manage or cancel: " in sent[0]["body"] and " at " in sent[0]["body"]
    assert sent[1]["body"].startswith("The booking has been cancelled") and '<html lang="en">' in sent[1]["html"]
    action=Client().get(r.json()["cancel"]).content.decode()
    assert '<html lang="en"' in action and "The booking is cancelled" in action
    # the staff stay in Italian, whatever the browser says
    staff,_=client_as(t);staff.defaults["HTTP_ACCEPT_LANGUAGE"]="en"
    assert '<html lang="it"' in staff.get(f"/t/{t.slug}/bookings/new/").content.decode()

def test_the_business_text_stays_as_written(sample):
    t,o,s=sample
    t.client_mails_enabled=True;t.client_mails={"confirmed":{"subject":"Ci vediamo · {attivita}","body":"Testo nostro {quando}"}};t.save()
    c=Client();ticket=c.get(f"/book/{t.slug}/?lang=en").context["widget"]["ticket"]
    c.post(f"/book/{t.slug}/api/submit/?lang=en",payload(s),content_type="application/json",HTTP_ORIGIN=settings.PUBLIC_ORIGIN,HTTP_X_TDBS_TICKET=ticket)
    m=to_clients(t)[0]
    assert m["subject"]=="Ci vediamo · Salone Test" and m["body"].startswith("Testo nostro ") and " at " in m["body"]

def test_the_waiting_list_writes_in_the_clients_language(salon):
    from core.waitlist import run
    from core.crypto import decrypt
    t,svc,_=salon
    b=book(t,svc)
    with translation.override("en"):e=waiting(t,svc)
    assert e.snapshot["lang"]=="en"
    cancel_desk(t,b);run()
    with scope(t):m=[decrypt(t,d.payload,"delivery:"+str(d.pk)) for d in Delivery.objects.filter(channel="email")]
    offer=[x for x in m if x["to"]=="giulia@example.invalid"][0]
    assert offer["subject"]=="A place is free · Salone Attesa" and "&lang=en" in offer["body"] and " at " in offer["body"]
