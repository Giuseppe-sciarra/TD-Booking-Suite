"""2.5.0 — public showcase (Prezzi e versioni, Perché sceglierci), privacy policy, logos for light and dark backgrounds."""
import io
import pytest
from django.test import Client
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from core.models import SiteSettings,PricingPlan
from tests.test_phase2 import staff_client,member
from tests.test_core import sample
from tests.test_site import form_data

pytestmark=pytest.mark.django_db

def turn(**kw):SiteSettings.objects.update_or_create(pk=1,defaults=kw)

def test_showcase_pages_are_off_until_switched_on():
    c=Client()
    assert c.get("/prezzi/").status_code==404 and c.get("/perche-sceglierci/").status_code==404
    login=c.get("/login/").content.decode()
    assert "/prezzi/" not in login and "/perche-sceglierci/" not in login and 'href="/privacy/"' in login

def test_pricing_page_shows_active_plans_by_edition():
    turn(pricing_enabled=True)
    PricingPlan.objects.create(name="Nascosto",price="1 €",active=False)
    page=Client().get("/prezzi/").content.decode()
    assert "Cloud, in abbonamento" in page and "Private, sul server del cliente" in page
    assert "Base" in page and "49 €" in page and "Il più scelto" in page and "Nascosto" not in page
    assert 'href="/prova/"' in page and "Prezzi IVA esclusa" in page and "Nessuna commissione" in page
    assert "/prezzi/" in Client().get("/login/").content.decode()
    assert "/perche-sceglierci/" not in page  # the comparison stays off on its own

def test_why_page_is_separate_and_linked_from_pricing_and_home():
    turn(pricing_enabled=True,why_enabled=True,why_columns="Noi | Loro",why_rows="Costo | Fisso | A commissione\nSolo voce | Sì")
    page=Client().get("/perche-sceglierci/").content.decode()
    assert "Perché sceglierci" in page and "<th class=\"ours\">Noi</th>" in page and ">A commissione</td>" in page and "Più prenotazioni, stessi costi" in page
    assert "/perche-sceglierci/" in Client().get("/prezzi/").content.decode()
    assert "/perche-sceglierci/" in Client().get("/login/").content.decode()  # 2.6.0: also from the home
    assert Client().get("/perche-sceglierlo/")["Location"]=="/perche-sceglierci/"  # the 2.5.0 address still works

def test_showcase_does_not_exist_in_private(settings):
    turn(pricing_enabled=True,why_enabled=True);settings.EDITION="private"
    assert Client().get("/prezzi/").status_code==404 and Client().get("/perche-sceglierci/").status_code==404
    assert Client().get("/privacy/").status_code==200

def test_privacy_standard_text_with_company_data():
    turn(legal_name="Tastiere Digitali Srls",vat_number="IT00000000000",legal_address="Via di prova 1, Roma",privacy_email="privacy@agenzia.invalid",data_location="Roma, Italia")
    page=Client().get("/privacy/").content.decode()
    assert "Tastiere Digitali Srls, Via di prova 1, Roma, partita IVA IT00000000000" in page and "privacy@agenzia.invalid" in page
    assert "<h2>Cookie</h2>" in page and "non serve un banner" in page and "a Roma, Italia" in page

def test_privacy_custom_text_is_escaped():
    turn(privacy_policy="## Titolo mio\nRiga <script>alert(1)</script>\n- voce uno\n- voce due")
    page=Client().get("/privacy/").content.decode()
    assert "<h2>Titolo mio</h2>" in page and "<li>voce uno</li><li>voce due</li>" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page

def test_privacy_date_moves_only_with_privacy_data():
    c,u=staff_client()
    c.post("/platform/settings/",form_data(name="Portale"));assert SiteSettings.current().privacy_updated is None
    c.post("/platform/settings/",form_data(legal_name="Nuova Srl"));assert SiteSettings.current().privacy_updated

def test_agency_edits_pages_and_plans(sample):
    t,o,s=sample
    owner,_=member(t,"owner")
    assert owner.get("/platform/prezzi/").status_code==403
    c,u=staff_client()
    r=c.post("/platform/prezzi/",{"page":"why","why-why_enabled":"on","why-why_title":"Perché noi","why-why_intro":"Intro","why-why_points":"A | b","why-why_columns":"Noi | Loro","why-why_rows":"X | 1 | 2","why-why_note":""},follow=True)
    assert "Pagina Perché sceglierci salvata" in r.content.decode() and SiteSettings.current().why_enabled and not SiteSettings.current().pricing_enabled
    bad=c.post("/platform/prezzi/nuovo/",{"edition":"cloud","name":"Trucco","price":"1 €","cta_url":"javascript:alert(1)","order":1,"active":"on"}).content.decode()
    assert "Usa un indirizzo che inizia con https://" in bad and not PricingPlan.objects.filter(name="Trucco").exists()
    c.post("/platform/prezzi/nuovo/",{"edition":"private","name":"Studio","price":"990 €","period":"una tantum","cta_url":"mailto:vendite@agenzia.invalid","order":1,"active":"on"})
    p=PricingPlan.objects.get(name="Studio");assert p.cta_url=="mailto:vendite@agenzia.invalid"
    c.post(f"/platform/prezzi/{p.pk}/",{"action":"delete"});assert not PricingPlan.objects.filter(name="Studio").exists()

def png(size,color=(30,30,30)):
    out=io.BytesIO();Image.new("RGBA",size,color+(255,)).save(out,format="PNG");return SimpleUploadedFile("logo.png",out.getvalue(),content_type="image/png")

def test_two_logos_and_wide_wordmarks():
    c,u=staff_client()
    c.post("/platform/settings/",form_data(logo_file=png((900,200)),logo_dark_file=png((900,200),(240,240,240))))
    site=SiteSettings.current()
    assert site.logo and site.logo_dark and site.logo_ratio>4 and site.light_wide and site.dark_wide
    assert Image.open(io.BytesIO(bytes(site.logo))).size==(800,178)
    assert Client().get("/site/logo-dark.png")["Content-Type"]=="image/png"
    page=c.get("/platform/").content.decode()
    assert 'class="logo logo-wide"' in page and 'src="/site/logo-dark.png' in page
    login=Client().get("/login/").content.decode()
    assert 'class="brand-img for-light wide" src="/site/logo.png' in login and 'class="brand-img for-dark wide" src="/site/logo-dark.png' in login
    c.post("/platform/settings/",form_data(remove_logo_dark="on"))
    assert not SiteSettings.current().logo_dark
    assert 'class="brand-img tile wide" src="/site/logo.png' in c.get("/platform/").content.decode()  # light logo on a white tile in the dark menu

def test_transparent_margins_are_trimmed():
    from core.site import clean_brand
    im=Image.new("RGBA",(1000,400),(0,0,0,0));im.paste((200,40,120,255),(100,150,700,250))
    out=io.BytesIO();im.save(out,format="PNG")
    data,ratio=clean_brand(SimpleUploadedFile("l.png",out.getvalue(),content_type="image/png"))
    assert Image.open(io.BytesIO(data)).size==(600,100) and ratio==6.0

def test_showcase_markup_is_clean():
    from tests.test_pages import check_markup
    turn(pricing_enabled=True,why_enabled=True)
    for path in ("/prezzi/","/perche-sceglierci/","/privacy/","/login/","/prova/"):
        r=Client().get(path);assert r.status_code==200,path;check_markup(r.content.decode(),path)
        assert "style=" not in r.content.decode(),path
