"""2.13.0: Entra senza password, for owners the agency allows, only from a device the edition trusts."""
import pytest
from django.test import Client
from core.models import *
from tests.test_core import sample,client_as
from tests.test_phase2 import staff_client,member

pytestmark=pytest.mark.django_db

def device(request):return request.META.get("HTTP_X_TEST_TRUSTED")=="1"

def test_owner_enters_without_password_only_from_a_trusted_device(sample,settings):
    t,o,s=sample
    owner,u=client_as(t)
    staff,_=staff_client()
    m=Membership.objects.get(tenant=t,user=u)
    url=f"/t/{t.slug}/members/"
    # the Docker edition trusts no device: no switch, no button
    assert "Entra senza password" not in staff.get(url).content.decode()
    assert "Questa edizione" in staff.post(url,{"action":"quick_on","member":m.pk}).content.decode()
    settings.TRUSTED_DEVICE="tests.test_quick.device"
    staff.post(url,{"action":"quick_on","member":m.pk})
    u.refresh_from_db();assert u.quick_login
    rec,ru=member(t,"reception")
    assert "solo un titolare" in staff.post(url,{"action":"quick_on","member":Membership.objects.get(user=ru).pk}).content.decode()
    trusted=Client(HTTP_X_TEST_TRUSTED="1")
    page=trusted.get("/login/").content.decode()
    assert f'value="{u.pk}"' in page and "Entra come" in page
    assert "Entra come" not in Client().get("/login/").content.decode()  # elsewhere only the password
    assert Client().post("/login/senza-password/",{"user":str(u.pk)}).status_code==403
    r=trusted.post("/login/senza-password/",{"user":str(u.pk)})
    assert r.status_code==302 and trusted.session["_auth_user_id"]==str(u.pk) and trusted.session["remember"]
    assert trusted.get(f"/t/{t.slug}/").status_code==200
    # a sensitive page: one click on the same device
    trusted.session.save();s2=trusted.session;s2["verified_at"]=0;s2.save()
    page=trusted.get("/verify/confirm/?next=/").content.decode()
    assert "Sono io, continua" in page
    assert trusted.post("/verify/confirm/",{"next":"/","quick":"1"}).status_code==302 and trusted.session["verified_at"]>0
    # an agency account never, a reception never, an owner switched back never
    other=Client(HTTP_X_TEST_TRUSTED="1")
    other.post("/login/senza-password/",{"user":str(ru.pk)});assert "_auth_user_id" not in other.session
    staff.post(url,{"action":"quick_off","member":m.pk})
    other.post("/login/senza-password/",{"user":str(u.pk)});assert "_auth_user_id" not in other.session
    with __import__("core.tenancy",fromlist=["scope"]).scope(t):
        assert Audit.objects.filter(action="login.quick").count()==1
