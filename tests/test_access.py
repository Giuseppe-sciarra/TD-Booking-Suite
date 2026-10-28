"""2.2.0 — two ways to give someone access: an invitation link, or a password typed by the administrator."""
import pytest
from core.models import *
from core.tenancy import scope
from tests.test_core import sample,client_as
from tests.test_phase2 import staff_client,member
from core.crypto import login_index

pytestmark=pytest.mark.django_db
PASSWORD="Pizza-forno-a-legna-2026"

def new_tenant(c,**extra):
    data={"name":"Pizzeria Nuova","slug":"pizzeria-nuova","timezone":"Europe/Rome","status":"active","smtp_daily_limit":200,"smtp_hourly_limit":50,
          "brand_color":"#7a55ad","retention_days":730,"privacy_text":"Informativa","consent_text":"Consenso","email":"titolare@example.invalid",**extra}
    return c.post("/platform/clienti/nuovo/",data,follow=True)

def test_new_business_with_invitation_link_shows_it_on_screen():
    c,u=staff_client()
    r=new_tenant(c);page=r.content.decode()
    t=Tenant.objects.get(slug="pizzeria-nuova");owner=Membership.objects.get(tenant=t).user
    assert "/invite/" in page and "Copia il link" in page and not owner.has_usable_password()
    with scope(t):assert Delivery.objects.filter(contact__isnull=True).count()==1
    assert Membership.objects.get(tenant=t).can_schedule  # the owner manages services and hours

def test_new_business_with_password_needs_no_email():
    c,u=staff_client()
    r=new_tenant(c,password=PASSWORD);page=r.content.decode()
    t=Tenant.objects.get(slug="pizzeria-nuova");owner=Membership.objects.get(tenant=t).user
    assert owner.check_password(PASSWORD) and "/invite/" not in page and "può già accedere" in page
    with scope(t):assert Delivery.objects.count()==0
    assert Invitation.objects.filter(user=owner).count()==0

def test_weak_password_creates_nothing():
    c,u=staff_client()
    r=new_tenant(c,password="corta");assert "troppo corta" in r.content.decode().lower() or "almeno 12" in r.content.decode()
    assert not Tenant.objects.filter(slug="pizzeria-nuova").exists()

def test_team_member_with_password(sample):
    t,o,s=sample;c,u=client_as(t)
    c.post(f"/t/{t.slug}/members/",{"action":"invite","email":"reception@example.invalid","role":"reception","password":PASSWORD})
    m=Membership.objects.get(tenant=t,user__login_index=login_index("reception@example.invalid"))
    assert m.user.check_password(PASSWORD) and m.role=="reception" and not m.can_schedule

def test_password_never_overwrites_an_existing_account(sample):
    t,o,s=sample;c,u=client_as(t)
    r=c.post(f"/t/{t.slug}/members/",{"action":"invite","email":u.email,"role":"owner","password":PASSWORD})
    assert "già un accesso" in r.content.decode()
    u.refresh_from_db();assert not u.check_password(PASSWORD) and u.check_password("A-test-password-12345")

def test_owner_always_manages_the_catalog(sample):
    t,o,s=sample;c,u=client_as(t)
    c.post(f"/t/{t.slug}/members/",{"action":"invite","email":"socio@example.invalid","role":"owner"})
    assert Membership.objects.get(tenant=t,user__login_index=login_index("socio@example.invalid")).can_schedule

# who you are, what to configure, and the guide
def test_role_is_always_visible(sample):
    t,o,s=sample;c,u=client_as(t,role="reception")
    page=c.get(f"/t/{t.slug}/").content.decode()
    assert 'class="badge">Reception<' in page and "Stai lavorando in" in page and "Area agenzia" not in page
    sc,su=staff_client();page=sc.get("/platform/").content.decode()
    assert "Area agenzia" in page and "Amministratore dell&#x27;agenzia" in page

def test_setup_lists_the_steps_in_order(sample):
    t,o,s=sample;c,u=client_as(t)
    page=c.get(f"/t/{t.slug}/setup/").content.decode()
    order=[page.index(x) for x in ("<h2>Sedi</h2>","<h2>Orari di apertura</h2>","<h2>Servizi</h2>")]
    assert order==sorted(order) and "Turni del ristorante" not in page
    # 2.6.0: places by capacity (the sample service) need no operators or rooms: the step is not there
    assert "<h2>Operatori e postazioni</h2>" not in page and "<h2>Risorse</h2>" not in page
    Tenant.objects.filter(pk=t.pk).update(business_type="parrucchiere")
    with scope(t):Service.objects.create(tenant=t,location=s.location,name="Taglio",kind="resource")
    page=c.get(f"/t/{t.slug}/setup/").content.decode()
    assert page.index("<h2>Servizi</h2>")<page.index("<h2>Operatori</h2>")
    op,_=member(t,"operator")
    assert op.get(f"/t/{t.slug}/setup/").status_code==403

def test_advanced_catalog_pages_appear_only_when_used(sample):
    t,o,s=sample;c,u=client_as(t)
    tabs=c.get(f"/t/{t.slug}/catalog/services/").content.decode()
    # places by capacity: no turns, dates, operators or variants
    assert "Turni del ristorante" not in tabs and "Eventi con data" not in tabs and "Varianti dei servizi" not in tabs and "Operatori e postazioni" not in tabs
    with scope(t):Service.objects.create(tenant=t,location=s.location,name="Cena",kind="shift")
    assert "Turni del ristorante" in c.get(f"/t/{t.slug}/catalog/services/").content.decode()
    Tenant.objects.filter(pk=t.pk).update(business_type="hotel")
    with scope(t):Service.objects.create(tenant=t,location=s.location,name="Camera",kind="day")
    tabs=c.get(f"/t/{t.slug}/catalog/services/").content.decode()
    assert ">Camere</a>" in tabs and "Varianti dei servizi" not in tabs
    page=c.get(f"/t/{t.slug}/catalog/resources/").content.decode()
    assert "<h1>Camere</h1>" in page and "Risorse" not in page

def test_guide_speaks_to_the_role(sample):
    t,o,s=sample;c,u=client_as(t)
    page=c.get(f"/t/{t.slug}/guida/").content.decode()
    assert "Sei amministratore di" in page and 'id="sedi"' in page and 'id="agenzia"' not in page
    sc,su=staff_client();page=sc.get("/guida/").content.decode()
    assert 'id="agenzia"' in page and "amministratore dell" in page


# 2.3.0 — roles named by what they do, and only what you can use is on screen
def test_role_names(sample):
    from core.permissions import ROLES
    assert [ROLES[k][0] for k in ("owner","manager","operator")]==["Amministratore","Amministratore sede","Operatore sede"]
    assert "catalog.write" in ROLES["owner"][1]

def test_location_operator_sees_only_his_location(sample):
    from core.booking import create_booking
    from tests.test_core import payload
    t,o,s=sample
    with scope(t):
        other=Location.objects.create(tenant=t,name="Mare")
        svc2=Service.objects.create(tenant=t,location=other,name="Servizio mare",kind="capacity",capacity=2,max_party=2,lead_minutes=0)
        here,_=create_booking(t,payload(s,hour=10));there,_=create_booking(t,payload(svc2,hour=11,email="anna@example.invalid"))
    c,u=client_as(t)
    c.post(f"/t/{t.slug}/members/",{"action":"invite","email":"op@example.invalid","role":"operator","locations":[str(s.location_id)]})
    m=Membership.objects.get(tenant=t,role="operator");m.can_schedule=False;m.save()
    from django.test import Client
    import time as wall
    op=Client();op.force_login(m.user,backend="core.identity.EmailBackend")
    sess=op.session;sess["mfa"]=True;sess["auth_version"]=m.user.auth_version;sess["verified_at"]=wall.time();sess["last_seen"]=wall.time();sess.save()
    page=op.get(f"/t/{t.slug}/bookings/").content.decode()
    assert str(here.pk) in page and str(there.pk) not in page
    nav=op.get(f"/t/{t.slug}/").content.decode()
    for hidden in ("Configura l&#x27;attività","Persone e permessi","Marketing","Registro attività","Accesso dell&#x27;agenzia","Clienti</a>","Email ai clienti"):
        assert hidden not in nav,hidden
    detail=op.get(f"/t/{t.slug}/bookings/{here.pk}/").content.decode()
    assert "Sposta prenotazione" in detail and ">Annulla<" not in detail

def test_single_location_is_implicit_for_location_roles(sample):
    t,o,s=sample;c,u=client_as(t)
    c.post(f"/t/{t.slug}/members/",{"action":"invite","email":"sede@example.invalid","role":"manager"})
    assert Membership.objects.get(tenant=t,role="manager").locations==[str(s.location_id)]

def test_business_users_never_see_agency_settings(sample):
    t,o,s=sample;c,u=client_as(t)
    page=c.get(f"/t/{t.slug}/setup/").content.decode()
    assert "Sito autorizzato" not in page and "Invio email" not in page and "Lo imposta" not in page
    nav=c.get(f"/t/{t.slug}/").content.decode()
    assert "Impostazioni e sito" not in nav and "Area agenzia" not in nav and "Impostazioni generali" not in nav

def test_login_keeps_the_email_and_shows_the_edition_note(settings,db):
    from django.test import Client
    settings.LOGIN_NOTE=({"title":"Amministratore del programma","text":"configura"},)
    page=Client().get("/login/?email=titolare@example.invalid").content.decode()
    assert 'value="titolare@example.invalid"' in page and "Amministratore del programma" in page
    assert 'value="&lt;b&gt;"' in Client().get("/login/?email=<b>").content.decode()  # escaped like everything else

def test_editions_rename_the_owner_and_the_agency_access(sample,settings):
    from core.permissions import role_label
    settings.ROLE_LABELS={"owner":"Titolare"}
    settings.AGENCY_NAMES={**settings.AGENCY_NAMES,"access":"Accesso dell'amministratore"}
    t,o,s=sample
    c,u=client_as(t)
    page=c.get(f"/t/{t.slug}/").content.decode()
    assert role_label("owner")=="Titolare" and role_label("reception")=="Reception"
    assert "Titolare" in page and "Accesso dell&#x27;amministratore" in page

def test_private_edition_admin_who_also_works_keeps_his_role_on_agency_pages(sample,settings):
    settings.EDITION="private"
    t=Tenant.objects.first()  # the one the private edition shows on the agency pages
    c,u=staff_client()
    page=c.get("/platform/").content.decode()
    assert "solo gestione tecnica" in page and f"/t/{t.slug}/contacts/" not in page  # technical only, as before
    Membership.objects.create(tenant=t,user=u,role="owner",can_schedule=True)
    page=c.get("/platform/").content.decode()
    assert "solo gestione tecnica" not in page and f"/t/{t.slug}/contacts/" in page and f"/t/{t.slug}/bookings/" in page

def test_the_agency_keeps_the_configuration_of_a_business_it_works_in(sample):
    from tests.test_phase2 import staff_client
    t,o,s=sample
    c,u=staff_client()
    Membership.objects.create(tenant=t,user=u,role="viewer")  # someone in the business lowered him to Consultazione
    assert c.get(f"/t/{t.slug}/setup/").status_code==200
    assert "Sei amministratore dell" in c.get(f"/t/{t.slug}/guida/").content.decode()
