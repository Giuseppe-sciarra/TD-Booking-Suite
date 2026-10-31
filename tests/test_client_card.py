"""2.14.0, fase 6: scheda cliente. Assenze e disdette all'ultimo momento sulle prenotazioni per chi vede i clienti,
Non prenotabile online e la regola delle assenze, etichette e VIP, compleanno cifrato, unione di due schede con i
consensi, campagne per gruppi di clienti, ricerca su tutte le schede."""
import json,secrets,time as wallclock
from datetime import datetime,date,time,timedelta
from zoneinfo import ZoneInfo
import pytest
from django.test import Client
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.crypto import decrypt,encrypt,tenant_key,seal
from core.booking import create_booking,change_status,BookingError
from core.clients import search,save_profile,clean_profile,merge,birthdays,by_phone,stamp,search_keys,find_or_create
from core.mail import campaign_contacts
from core.privacy import history,record
from tests.test_core import sample,payload,client_as
from tests.test_clients import staff_payload

pytestmark=pytest.mark.django_db
ROME=ZoneInfo("Europe/Rome")

def absent(t,s,n,email="mario@example.invalid"):
    """n past bookings of the client closed as absent."""
    with scope(t):
        for i in range(n):
            b,_=create_booking(t,payload(s,hour=10+i,email=email))
            Booking.objects.filter(pk=b.pk).update(start=b.start-timedelta(days=10),end=b.end-timedelta(days=10),busy_start=b.busy_start-timedelta(days=10),busy_end=b.busy_end-timedelta(days=10))
            change_status(t,Booking.objects.get(pk=b.pk),"no_show")
        return Contact.objects.get()

def test_absences_shown_only_to_who_sees_the_client_cards(sample):
    t,o,s=sample;contact=absent(t,s,2)
    with scope(t):
        b,_=create_booking(t,payload(s,hour=15))
        late,_=create_booking(t,payload(s,hour=16))
        Booking.objects.filter(pk=late.pk).update(start=timezone.now()+timedelta(hours=5),end=timezone.now()+timedelta(hours=6))
        change_status(t,Booking.objects.get(pk=late.pk),"cancelled",reason="client")  # five hours before: at the last moment
    owner,_=client_as(t)
    page=owner.get(f"/t/{t.slug}/bookings/{b.pk}/").content.decode()
    assert "2 assenze" in page and "1 disdetta all&#x27;ultimo momento" in page
    assert 'class="cmark absent"' in owner.get(f"/t/{t.slug}/bookings/?vista=elenco").content.decode()
    viewer,_=client_as(t,role="viewer")  # sees the agenda, not the client cards: no marks
    seen=viewer.get(f"/t/{t.slug}/bookings/{b.pk}/");assert seen.status_code==200 and "assenz" not in seen.content.decode()
    assert "cmark" not in viewer.get(f"/t/{t.slug}/bookings/?vista=elenco").content.decode()
    assert "1 all'ultimo momento" in owner.get(f"/t/{t.slug}/contacts/{contact.pk}/").content.decode()

def test_absent_marks_in_the_agenda(sample):
    t,o,s=sample;absent(t,s,1)
    with scope(t):
        loc=Location.objects.first()
        svc=Service.objects.create(tenant=t,location=loc,name="Taglio",kind="resource",duration=30,interval=30,lead_minutes=0)
        r=Resource.objects.create(tenant=t,location=loc,name="Marco");ServiceResource.objects.create(tenant=t,service=svc,resource=r)
        b,_=create_booking(t,payload(svc,hour=11))
    owner,_=client_as(t)
    day=b.start.astimezone(ROME).date().isoformat()
    assert 'class="cmark absent"' in owner.get(f"/t/{t.slug}/bookings/?data={day}").content.decode()
    assert 'class="cmark absent"' in owner.get(f"/t/{t.slug}/bookings/?vista=settimana&data={day}").content.decode()

def test_not_bookable_online_and_absences_rule(sample):
    t,o,s=sample;contact=absent(t,s,2)
    with scope(t):Location.objects.update(phone="06 1234567")
    t.absences_request=3;t.save()
    with scope(t):assert create_booking(t,payload(s,hour=9))[0].status=="confirmed"  # 2 absences, rule at 3
    t.absences_request=2;t.save()
    with scope(t):
        assert create_booking(t,payload(s,hour=12))[0].status=="pending"  # needs_request: waits for the business's yes
        assert create_booking(t,payload(s,hour=13,email="new@example.invalid",phone="+393339990000"))[0].status=="confirmed"  # another client
        save_profile(t,contact,clean_profile({"online_blocked":"on"},date.today()))
        with pytest.raises(BookingError,match=r"^Per prenotare contatta l'attività: 06 1234567\.$"):create_booking(t,payload(s,hour=14))
        # another email on his phone: the same person
        with pytest.raises(BookingError,match="contatta l'attività"):create_booking(t,payload(s,hour=14,email="other@example.invalid"))
    owner,u=client_as(t)
    with scope(t):assert create_booking(t,staff_payload(s,hour=15,contact=str(contact.pk)),actor=u)[0].status=="confirmed"  # the desk books him

def test_tags_vip_birthday_sealed_and_filters(sample):
    t,o,s=sample
    owner,_=client_as(t)
    with scope(t):
        a,_=find_or_create(t,{"first_name":"Anna","last_name":"Neri","email":"anna@example.invalid","phone":""})
        b,_=find_or_create(t,{"first_name":"Bruno","last_name":"Gialli","email":"bruno@example.invalid","phone":""})
    today=timezone.now().astimezone(ROME).date();d=today+timedelta(days=2);born=date(1992 if (d.month,d.day)==(2,29) else 1990,d.month,d.day)
    r=owner.post(f"/t/{t.slug}/contacts/{a.pk}/",{"action":"profile","tags":"Sposa 2027, capelli ricci,sposa 2027","birthday":born.isoformat(),"vip":"on"})
    assert r.status_code==302
    with scope(t):
        a=Contact.objects.get(pk=a.pk)
        assert a.vip and a.data["tags"]==["Sposa 2027","capelli ricci"] and a.data["birthday"]==born.isoformat()
        assert "Sposa" not in a.pii and born.isoformat() not in a.pii and born.isoformat() not in a.birthday_index  # sealed and keyed
        assert [x["contact"].pk for x in birthdays(t,today)]==[a.pk]
    with pytest.raises(BookingError):clean_profile({"birthday":"2999-01-01"},today)
    page=owner.get(f"/t/{t.slug}/contacts/",{"tag":"sposa 2027"}).content.decode()  # accents and case do not matter
    assert "Anna" in page and "Bruno" not in page
    assert "Bruno" not in owner.get(f"/t/{t.slug}/contacts/?vip=1").content.decode()
    assert "Compleanni della settimana" in owner.get(f"/t/{t.slug}/").content.decode()
    with scope(t):b2,_=create_booking(t,payload(s,email="anna@example.invalid"))
    assert 'class="cmark vip"' in owner.get(f"/t/{t.slug}/bookings/?vista=elenco").content.decode()  # the small mark on her bookings

def test_merge_moves_everything_and_keeps_the_consent_proof(sample):
    t,o,s=sample
    owner,u=client_as(t)
    with scope(t):
        b1,_=create_booking(t,payload(s,email="mario@example.invalid",hour=10))
        b2,_=create_booking(t,payload(s,email="mario.rossi@example.invalid",hour=11))
        main,other=b1.contact,b2.contact
        Treatment.objects.create(tenant=t,contact=other,date=date.today(),pii=encrypt(t,{"service":"Colore","staff":"","details":""},"treatment:x"))
        from core.waitlist import add
        add(t,{"service":str(s.pk),"first":(date.today()+timedelta(days=5)).isoformat()},u,other)
        # the other mailbox confirmed the marketing consent
        other.consent="yes";other.consent_at=timezone.now();other.consent_proof=encrypt(t,{"text":"Ok","confirmed":"2026-10-01"},"consent:"+str(other.pk));other.save()
        record(t,other,"confirmed","email_link")
    r=owner.get(f"/t/{t.slug}/contacts/{main.pk}/unisci/?q=mario.rossi@example.invalid")
    assert str(other.pk) in r.content.decode()
    form={"con":str(other.pk),"main":"this","keep_first_name":"this","keep_last_name":"this","keep_email":"other","keep_phone":"this","keep_birthday":"this"}
    assert owner.post(f"/t/{t.slug}/contacts/{main.pk}/unisci/",form).status_code==200  # not without the word
    with scope(t):assert not Contact.objects.get(pk=other.pk).merged_into_id
    assert owner.post(f"/t/{t.slug}/contacts/{main.pk}/unisci/",{**form,"confirm":"UNISCI"}).status_code==302
    with scope(t):
        main=Contact.objects.get(pk=main.pk);other=Contact.objects.get(pk=other.pk)
        assert main.data["email"]=="mario.rossi@example.invalid" and main.consent=="yes"  # the consent of the mailbox kept
        assert decrypt(t,main.consent_proof,"consent:"+str(main.pk))["confirmed"]=="2026-10-01"
        assert set(Booking.objects.filter(contact=main).values_list("pk",flat=True))=={b1.pk,b2.pk}
        assert Treatment.objects.filter(contact=main).count()==1 and WaitEntry.objects.get().contact_id==main.pk
        assert other.merged_into_id==main.pk and other.email_index.startswith("erased:") and other.data["email"]=="" and other.suppressed
        kinds=[e.kind for e in history(main)];assert "confirmed" in kinds and kinds.count("merged")==2  # both histories, kept
        assert Audit.objects.filter(action="contact.merged",target=str(main.pk)).exists()
        assert [c.pk for c in search(t,"mario.rossi@example.invalid")]==[main.pk]
    assert owner.get(f"/t/{t.slug}/contacts/{other.pk}/").status_code==404

def test_merge_never_invents_consent(sample):
    t,o,s=sample
    with scope(t):
        a,_=find_or_create(t,{"first_name":"Lia","last_name":"","email":"lia@example.invalid","phone":""})
        b,_=find_or_create(t,{"first_name":"Lia","last_name":"Blu","email":"lia.blu@example.invalid","phone":"+393331112222"})
        b.consent="yes";b.consent_proof=encrypt(t,{"text":"Ok"},"consent:"+str(b.pk));b.save()
        merged=merge(t,a,b,{"last_name":"other","phone":"other"},None)  # her own email kept: the other mailbox's yes stays there
        assert merged.consent=="none" and not merged.consent_proof and merged.data["last_name"]=="Blu" and merged.data["phone"]=="+393331112222"
        assert by_phone(t,"+393331112222").pk==merged.pk

def test_campaigns_by_tag_vip_last_visit_and_visits(sample):
    t,o,s=sample
    with scope(t):
        people={}
        for name in ("tag","vip","old","often","plain"):
            c,_=find_or_create(t,{"first_name":name,"last_name":"","email":name+"@example.invalid","phone":""})
            c.consent="yes";c.save();people[name]=c
        save_profile(t,people["tag"],clean_profile({"tags":"Sposa"},date.today()))
        save_profile(t,people["vip"],clean_profile({"vip":"on"},date.today()))
        def visit(c,days_ago):
            b,_=create_booking(t,payload(s,email=c.data["email"],hour=9+len(Booking.objects.all())%8))
            Booking.objects.filter(pk=b.pk).update(start=b.start-timedelta(days=days_ago+3),end=b.end-timedelta(days=days_ago+3),status="completed",contact=c)
        visit(people["old"],300)
        for d in (5,10,20):visit(people["often"],d)
        names=lambda f:sorted(c.data["first_name"] for c in campaign_contacts(t,f))
        assert names({})==["often","old","plain","tag","vip"]
        assert names({"tag":"sposa"})==["tag"] and names({"vip":True})==["vip"]
        cut=(timezone.now()-timedelta(days=180)).date().isoformat()
        assert names({"before":cut})==["old","plain","tag","vip"] and names({"after":cut})==["often"] and names({"visits":2})==["often"]
        Contact.objects.filter(pk=people["often"].pk).update(consent="revoked")
        assert names({"visits":2})==[]  # only among who said yes, as always
    owner,_=client_as(t);t.marketing_enabled=True;t.save()
    r=owner.post(f"/t/{t.slug}/marketing/new/",{"name":"Spose","subject":"Novità","body":"Ciao","tag":"Sposa"})
    assert r.status_code==302
    with scope(t):camp=Campaign.objects.get();assert camp.filters=={"tag":"Sposa"}
    assert "1 destinatari" in owner.get(f"/t/{t.slug}/marketing/{camp.pk}/").content.decode()

def test_search_reaches_every_card_beyond_5000(sample):
    """6000 cards: the oldest is found by name, by phone, by email and by part of its phone; an online booking with its
    phone joins its card (by_phone used to read only the 5000 most recent)."""
    t,o,s=sample
    with scope(t):
        key=tenant_key(t);h=search_keys(key);rows=[]
        first,_=find_or_create(t,{"first_name":"Zebedeo","last_name":"D'Àlvaro","email":"zeb@example.invalid","phone":"+393330000001"})
        Contact.objects.filter(pk=first.pk).update(created=timezone.now()-timedelta(days=400))
        for i in range(6000):
            c=Contact(tenant=t,email_index=secrets.token_hex(32));d={"first_name":f"Nome{i}","last_name":"Cognome","email":f"x{i}@example.invalid","phone":f"+3934{i:08d}"}
            c.pii=seal(d,key,f"{t.pk}:contact:{c.pk}");stamp(c,d,key,h);rows.append(c)
        Contact.objects.bulk_create(rows,batch_size=2000)
        started=wallclock.time()
        assert [c.pk for c in search(t,"zeb alv")]==[first.pk] and [c.pk for c in search(t,"dalvaro")]==[] and [c.pk for c in search(t,"d alvaro")]==[first.pk]
        assert [c.pk for c in search(t,"333 000 0001")]==[first.pk] and [c.pk for c in search(t,"3330000")]==[first.pk]
        assert len(search(t,"nome59",limit=200))==111 and len(search(t,"x5999@example.invalid"))==1
        assert wallclock.time()-started<10
    owner,u=client_as(t)
    with scope(t):  # at the desk with the phone only: his card, though 6000 are more recent
        b,_=create_booking(t,staff_payload(s,first_name="Zebedeo",phone="333 000 0001"),actor=u)
        assert b.contact_id==first.pk
