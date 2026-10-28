"""2.9.0 — editions where accounts have a username and the email is optional, and editions without second factor."""
import pyotp,pytest
from django.core.exceptions import ValidationError
from django.test import Client
from core.crypto import protect
from core.models import User,Membership
from tests.test_core import sample,client_as
from tests.test_phase2 import staff_client

pytestmark=pytest.mark.django_db
PASSWORD="Pizza-forno-a-legna-2026"

def signed_in(c,user):return c.session.get("_auth_user_id")==str(user.pk)

def test_username_or_email_signs_in(settings):
    settings.LOGIN_USERNAMES=True
    u=User.objects.create_user("Mario@Example.invalid",PASSWORD,username="Mario.Rossi")
    assert (u.username,u.email,u.login_name)==("mario.rossi","mario@example.invalid","mario.rossi")
    for who in ("mario.rossi"," MARIO.ROSSI ","mario@example.invalid"):
        c=Client();c.post("/login/",{"email":who,"password":PASSWORD});assert signed_in(c,u),who
    only=User.objects.create_user("",PASSWORD,username="cassa")
    assert (only.email,only.login_name)==("","cassa")
    c=Client();c.post("/login/",{"email":"cassa","password":PASSWORD});assert signed_in(c,only)
    c=Client();c.post("/login/",{"email":"cassa","password":"sbagliata-1234567"});assert not signed_in(c,only)
    page=Client().get("/login/").content.decode()
    assert "Username o email" in page and 'type="text"' in page
    with pytest.raises(Exception):User.objects.create_user("altro@example.invalid",PASSWORD,username="cassa")  # usernames are unique
    for bad in ("a@b.it","ab","con spazio","àccento","-meno"):
        with pytest.raises(ValidationError):User.objects.create_user("",PASSWORD,username=bad)
    settings.LOGIN_USERNAMES=False
    assert 'type="email"' in Client().get("/login/").content.decode()  # the Docker edition stays as it was

def test_people_with_a_username_and_no_email(sample,settings):
    settings.LOGIN_USERNAMES=True
    t,o,s=sample;c,owner=client_as(t)
    url=f"/t/{t.slug}/members/"
    page=c.post(url,{"action":"invite","email":"nuova@example.invalid","role":"reception"}).content.decode()
    assert "scegli lo username" in page and not User.objects.filter(name_index__isnull=False).exists()
    c.post(url,{"action":"invite","username":"Cassa","email":"","role":"reception","password":PASSWORD})
    cassa=User.objects.get(name_index__isnull=False)
    assert (cassa.login_name,cassa.email)==("cassa","") and Membership.objects.filter(tenant=t,user=cassa,role="reception").exists()
    assert ">cassa<" in c.get(url).content.decode()
    other=Client();other.post("/login/",{"email":"cassa","password":PASSWORD});assert signed_in(other,cassa)
    # someone already in: the username alone adds them, the email of another account with it is refused
    page=c.post(url,{"action":"invite","username":"cassa","email":owner.email,"role":"reception"}).content.decode()
    assert "due account diversi" in page
    # the owner resets the password of a person without email: the link is on screen, nothing is mailed
    m=Membership.objects.get(tenant=t,user=cassa)
    assert "/invite/" in c.post(url,{"action":"reset","member":m.pk}).content.decode()

def test_the_agency_never_shows_a_reset_link_for_an_account_without_email(sample,settings):
    settings.LOGIN_USERNAMES=True
    t,o,s=sample
    cassa=User.objects.create_user("",PASSWORD,username="cassa")
    m=Membership.objects.create(tenant=t,user=cassa,role="reception")
    staff,_=staff_client()
    page=staff.post(f"/t/{t.slug}/members/",{"action":"reset","member":m.pk}).content.decode()
    assert "non ha un&#x27;email" in page and "/invite/" not in page
    cassa.refresh_from_db();assert cassa.check_password(PASSWORD)  # nothing was reset

def test_edition_without_second_factor(sample,settings):
    settings.MFA_ENABLED=False
    t,o,s=sample
    u=User.objects.create_user("titolare@example.invalid",PASSWORD)
    Membership.objects.create(tenant=t,user=u,role="owner",can_schedule=True)
    u.totp=protect(pyotp.random_base32(),"totp:"+str(u.pk));u.save()
    c=Client()
    assert c.post("/login/",{"email":"titolare@example.invalid","password":PASSWORD}).url=="/"  # no code asked
    assert signed_in(c,u)
    page=c.get(f"/t/{t.slug}/").content.decode()
    assert "Proteggi il tuo account" not in page
    assert "APP AUTHENTICATOR" not in c.get("/security/").content.decode()
    assert c.post("/passkey/register/begin/",{},content_type="application/json").status_code==403
    assert "passkey-login" not in Client().get("/login/").content.decode()
    assert 'name="password"' in c.get("/verify/confirm/").content.decode()  # sensitive pages ask the password again

def test_reminder_only_for_the_staff(sample,settings):
    settings.MFA_REMINDER="staff"
    t,o,s=sample
    c,u=client_as(t)
    assert "Proteggi il tuo account" not in c.get(f"/t/{t.slug}/").content.decode()
    staff,_=staff_client()
    assert "Proteggi il tuo account" in staff.get("/platform/").content.decode()
    settings.MFA_REMINDER="all"
    assert "Proteggi il tuo account" in c.get(f"/t/{t.slug}/").content.decode()
