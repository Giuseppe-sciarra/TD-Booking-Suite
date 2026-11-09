"""2.22.9: the client's photo album: up to 4 photos a day on the visit (from the booking) or on today (from the client
card), made small and sealed; shown only to who may see that client; gone with the erasure, the retention and in the
export; moved with a merge; only where the business keeps a client record."""
import io,secrets
from datetime import timedelta
import pytest
from PIL import Image
from zoneinfo import ZoneInfo
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking
from core.crypto import decrypt_bytes
from core.privacy import erase_contact,export_contact,apply_retention
from tests.test_core import client_as,payload
from tests.test_cassa import shop  # noqa: F401 (fixture)

pytestmark=pytest.mark.django_db

def jpeg(w=3000,h=2000,rotate=False):
    """A phone-like photo; rotate: taken upright, saved sideways with the EXIF orientation 6."""
    im=Image.new("RGB",(w,h),(180,90,120));buf=io.BytesIO()
    if rotate:
        ex=Image.Exif();ex[0x0112]=6;im.save(buf,format="JPEG",quality=95,exif=ex)
    else:im.save(buf,format="JPEG",quality=95)
    return buf.getvalue()
def up(name="foto.jpg",data=None):return SimpleUploadedFile(name,data or jpeg(),content_type="image/jpeg")

@pytest.fixture
def salon(shop):
    t,loc,svc=shop
    Tenant.objects.filter(pk=t.pk).update(business_type="parrucchiere");t.refresh_from_db()
    with scope(t):b,_=create_booking(t,payload(svc))
    return t,loc,svc,b

def test_photos_on_the_visit_small_sealed_and_four_a_day(salon):
    t,loc,svc,b=salon;c,_=client_as(t,role="reception")
    page=c.get(f"/t/salone/bookings/{b.pk}/").content.decode()
    assert 'id="foto"' in page and "Aggiungi foto" in page and "al massimo 4" in page
    r=c.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[up(data=jpeg(rotate=True)),up()]},HTTP_X_REQUESTED_WITH="fetch")
    j=r.json();assert j["ok"] and len(j["photos"])==2 and j["left"]==2
    with scope(t):
        ps=list(ClientPhoto.objects.filter(contact_id=b.contact_id))
        day=b.start.astimezone(ZoneInfo(t.timezone)).date()
        assert len(ps)==2 and all(p.date==day and p.booking_id==b.pk for p in ps)
        raw=bytes(ps[0].image);assert b"\xff\xd8" not in raw[:40]  # sealed, not a plain JPEG
    # the photo comes back as a JPEG of at most 1600 px, upright, much smaller than the 3000 px original
    r=c.get(j["photos"][0]["full"]);im=Image.open(io.BytesIO(r.content))
    assert r["Content-Type"]=="image/jpeg" and "private" in r["Cache-Control"] and max(im.size)==1600
    sizes=sorted(Image.open(io.BytesIO(c.get(p["full"]).content)).size for p in j["photos"])
    assert sizes==[(1067,1600),(1600,1067)]  # the one taken upright is upright
    th=Image.open(io.BytesIO(c.get(j["photos"][0]["thumb"]).content));assert max(th.size)==360
    # two more fit, the fifth is left out and said
    r=c.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[up(),up(),up()]},HTTP_X_REQUESTED_WITH="fetch");j=r.json()
    assert len(j["photos"])==2 and j["left"]==0 and "al massimo 4" in j["error"]
    r=c.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[up()]},HTTP_X_REQUESTED_WITH="fetch")
    assert r.status_code==400 and "già 4 foto" in r.json()["error"]
    page=c.get(f"/t/salone/bookings/{b.pk}/").content.decode()
    assert page.count("?piccola=1")==4 and "Ci sono già 4 foto" in page
    # not a photo: said, nothing saved
    r=c.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[SimpleUploadedFile("x.jpg",b"not an image")]},HTTP_X_REQUESTED_WITH="fetch")
    assert r.status_code==400
    # one taken away: room again
    with scope(t):p=ClientPhoto.objects.filter(contact_id=b.contact_id).first()
    r=c.post(f"/t/salone/foto/{p.pk}/togli/",HTTP_X_REQUESTED_WITH="fetch");assert r.json()=={"ok":True,"left":1}
    with scope(t):assert ClientPhoto.objects.filter(contact_id=b.contact_id).count()==3

def test_client_card_album_by_day_and_today(salon):
    t,loc,svc,b=salon;c,_=client_as(t,role="reception")
    r=c.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"photo":[up()],"next":f"/t/salone/contacts/{b.contact_id}/#foto"})
    assert r.status_code==302 and r["Location"].endswith("#foto")
    c.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[up()]})
    page=c.get(f"/t/salone/contacts/{b.contact_id}/").content.decode()
    assert 'data-tab="foto"' in page and '<span class="tab-count">2</span>' in page and "Oggi" in page and "Taglio donna" in page
    # the plain form (without the browser's help) is sent to an outside address: it goes back to the card instead
    r=c.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"photo":[up()],"next":"https://evil.example/"})
    assert r["Location"]==f"/t/salone/contacts/{b.contact_id}/#foto"

def test_who_sees_the_photos(salon):
    t,loc,svc,b=salon;owner,_=client_as(t)
    j=owner.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[up()]},HTTP_X_REQUESTED_WITH="fetch").json()
    url=j["photos"][0]["full"]
    # an operator of another location: not his client
    with scope(t):other=Location.objects.create(tenant=t,name="Altra")
    op,u=client_as(t,role="operator")
    Membership.objects.filter(user=u).update(locations=[str(other.pk)])
    assert op.get(url).status_code==403
    assert op.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[up()]}).status_code==404
    Membership.objects.filter(user=u).update(locations=[str(loc.pk)])
    assert op.get(url).status_code==200
    # an operator does not see client cards: from the card no, from his booking yes
    assert op.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"photo":[up()]}).status_code==403
    # another business never
    t2=Tenant.objects.create(slug="altro",name="Altro",business_type="parrucchiere")
    c2,_=client_as(t2);assert c2.get(url.replace("/t/salone/","/t/altro/")).status_code==404
    # where there is no client record (a restaurant) there are no photos
    Tenant.objects.filter(pk=t.pk).update(business_type="ristorante")
    assert 'id="foto"' not in owner.get(f"/t/salone/bookings/{b.pk}/").content.decode()
    assert owner.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[up()]}).status_code==403

def test_photos_follow_privacy_export_erasure_retention_and_merge(salon):
    t,loc,svc,b=salon;c,_=client_as(t)
    c.post(f"/t/salone/contacts/{b.contact_id}/foto/",{"booking":str(b.pk),"photo":[up()]})
    with scope(t):
        contact=Contact.objects.get(pk=b.contact_id)
        out=export_contact(t,contact);assert len(out["photos"])==1 and out["photos"][0]["jpeg_base64"].startswith("/9j/")
        # merge: the other card's photos move to the one kept
        from core.clients import merge
        other,_=create_booking(t,payload(svc,hour=12,first_name="Maria",email="maria@example.invalid",phone="+393339998887"))
    c.post(f"/t/salone/contacts/{other.contact_id}/foto/",{"booking":str(other.pk),"photo":[up()]})
    with scope(t):
        merge(t,contact,Contact.objects.get(pk=other.contact_id),{},None)
        assert ClientPhoto.objects.filter(contact=contact).count()==2
        p=ClientPhoto.objects.filter(contact=contact).first()
        assert decrypt_bytes(t,p.image,"photo:"+str(p.pk))[:2]==b"\xff\xd8"  # still opens after the move
        # retention: photos as old as the period go
        ClientPhoto.objects.filter(pk=p.pk).update(date=timezone.now().date()-timedelta(days=t.retention_days+5))
        assert apply_retention(t)["photos"]==1
        apply_retention(t,apply=True);assert not ClientPhoto.objects.filter(pk=p.pk).exists()
        # erasure: all his photos go (future bookings first cancelled)
        Booking.objects.filter(contact=contact).update(status="cancelled")
        erase_contact(t,contact,None);assert not ClientPhoto.objects.filter(contact=contact).exists()
