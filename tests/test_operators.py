"""2.12.0: operators with their photo, and what each one does with its own duration and price."""
import io,json
from datetime import datetime,time,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from core.booking import create_booking
from core.models import *
from core.profile import apply_profile
from core.tenancy import scope
from tests.test_core import sample,client_as,payload
from tests.test_agenda import next_open_day

pytestmark=pytest.mark.django_db

def picture(fmt="PNG",size=(1200,900)):
    out=io.BytesIO();Image.new("RGBA" if fmt=="PNG" else "RGB",size,(200,80,140,255) if fmt=="PNG" else (200,80,140)).save(out,format=fmt)
    return SimpleUploadedFile("anna."+fmt.lower(),out.getvalue(),content_type="image/"+fmt.lower())

def form_data(op,services,**extra):
    data={"name":op.name,"location":str(op.location_id),"description":"","photo_url":"","active":"on","services":[str(s.pk) for s in services]}
    data.update(extra);return data

def salon(t):
    apply_profile(t,"parrucchiere",["Centro"])
    with scope(t):
        anna,bruno=Resource.objects.order_by("name")
        Resource.objects.filter(pk=anna.pk).update(name="Anna");Resource.objects.filter(pk=bruno.pk).update(name="Bruno")
        anna.refresh_from_db();bruno.refresh_from_db()
        taglio=Service.objects.get(name="Taglio");piega=Service.objects.get(name="Piega");colore=Service.objects.get(name="Colore")
        Service.objects.filter(pk=taglio.pk).update(price=Decimal("28"));taglio.refresh_from_db()
    return anna,bruno,taglio,piega,colore

def test_operator_photo_is_uploaded_shown_and_removed(sample):
    t,o,s=sample
    anna,bruno,taglio,piega,colore=salon(t)
    owner,_=client_as(t,role="owner")
    url=f"/t/{t.slug}/catalog/resources/{anna.pk}/"
    r=owner.post(url,{**form_data(anna,[taglio,piega,colore]),"photo_file":picture()},follow=True)
    assert "Salvato." in r.content.decode()
    with scope(t):
        anna.refresh_from_db();photo=ResourcePhoto.objects.get(resource=anna)
        im=Image.open(io.BytesIO(bytes(photo.data)))
        assert anna.photo_at and im.format=="JPEG" and max(im.size)==800  # kept small, whatever was uploaded
    link=anna.photo_link(t.slug)
    served=owner.get(link)
    assert served.status_code==200 and served["Content-Type"]=="image/jpeg"
    # the photo goes in the list, at the head of the agenda and in the booking form, also the public one
    assert f'src="{link}"' in owner.get(f"/t/{t.slug}/catalog/resources/").content.decode().replace("&amp;","&")
    day=next_open_day()
    assert 'class="op-photo"' in owner.get(f"/t/{t.slug}/bookings/?data={day.isoformat()}").content.decode()
    assert json.dumps(link) in owner.get(f"/book/{t.slug}/").content.decode()
    # a file that is not a photo is refused; the photo can be taken away
    bad=SimpleUploadedFile("cv.png",b"not a picture",content_type="image/png")
    assert "Usa una foto PNG, JPEG o WebP valida" in owner.post(url,{**form_data(anna,[taglio]),"photo_file":bad}).content.decode()
    owner.post(url,{**form_data(anna,[taglio,piega,colore]),"photo_remove":"on"})
    with scope(t):
        anna.refresh_from_db();assert not anna.photo_at and not ResourcePhoto.objects.filter(resource=anna).exists()
    assert owner.get(link).status_code==404

def test_each_operator_has_own_duration_and_price(sample):
    t,o,s=sample
    anna,bruno,taglio,piega,colore=salon(t)
    owner,_=client_as(t,role="owner")
    url=f"/t/{t.slug}/catalog/resources/{anna.pk}/"
    form=owner.get(url).content.decode()
    assert 'class="services-grid"' in form and f'name="dur_{taglio.pk}"' in form and f'placeholder="{taglio.duration}"' in form
    # Anna: cut in 45 minutes at 35 euro, no colour; the duration is checked
    assert "maggiore o uguale a 5" in owner.post(url,form_data(anna,[taglio],**{f"dur_{taglio.pk}":"2"})).content.decode()
    r=owner.post(url,form_data(anna,[taglio,piega],**{f"dur_{taglio.pk}":"45",f"price_{taglio.pk}":"35"}),follow=True)
    assert "Salvato." in r.content.decode()
    with scope(t):
        link=ServiceResource.objects.get(service=taglio,resource=anna)
        assert link.duration==45 and link.price==Decimal("35")
        assert not ServiceResource.objects.filter(service=colore,resource=anna).exists()
    day=next_open_day();tz=ZoneInfo(t.timezone)
    slots=lambda op:json.loads(owner.get(f"/t/{t.slug}/booking-api/slots/",{"service":str(taglio.pk),"date":day.isoformat(),"resource":str(op.pk)}).content)["slots"]
    a=slots(anna)[0];b=slots(bruno)[0]
    span=lambda x:(datetime.fromisoformat(x["end"])-datetime.fromisoformat(x["start"])).seconds//60
    assert span(a)==45 and span(b)==taglio.duration
    with scope(t):
        mine,_=create_booking(t,payload(taglio,resource=str(anna.pk),start=a["start"]))
        his,_=create_booking(t,payload(taglio,resource=str(bruno.pk),start=b["start"],email="b@example.invalid",phone="+393330001111"))
        assert (mine.end-mine.start).seconds//60==45 and mine.snapshot["price"]=="35.00"
        assert his.snapshot["price"]=="28.00"
    # the service saved again keeps Anna's own duration and price
    page=owner.get(f"/t/{t.slug}/catalog/services/{taglio.pk}/").content.decode()
    assert 'name="resources"' in page
    with scope(t):
        from core.forms import ServiceForm
        form=ServiceForm({"name":"Taglio","location":str(taglio.location_id),"description":"","price":"28","duration":str(taglio.duration),"interval":"15",
            "buffer_before":"0","buffer_after":"0","lead_minutes":"60","horizon_days":"180","cancel_cutoff_minutes":"0","resource_label":"Operatore","fields":"[]","active":"on",
            "resources":[str(anna.pk),str(bruno.pk)]},instance=taglio,tenant=t)
        assert form.is_valid(),form.errors
        form.save()
        assert ServiceResource.objects.get(service=taglio,resource=anna).duration==45

def test_the_day_of_one_operator(sample):
    t,o,s=sample
    anna,bruno,taglio,piega,colore=salon(t)
    owner,_=client_as(t,role="owner")
    day=next_open_day();tz=ZoneInfo(t.timezone)
    with scope(t):
        create_booking(t,payload(taglio,resource=str(anna.pk),start=datetime.combine(day,time(10),tz).isoformat(),first_name="Giulia"))
        create_booking(t,payload(taglio,resource=str(bruno.pk),start=datetime.combine(day,time(11),tz).isoformat(),first_name="Marta",email="m@example.invalid",phone="+393330002222"))
    page=owner.get(f"/t/{t.slug}/bookings/?vista=giorno&data={day.isoformat()}&operatore={bruno.pk}").content.decode()
    assert ">Bruno</strong>" in page and ">Anna</strong>" not in page and "Marta" in page and "Giulia" not in page
    assert 'name="operatore"' in page and f'value="{bruno.pk}" selected' in page and f"&amp;operatore={bruno.pk}" in page
