"""2.17.2: the colour of Logo e aspetto changes the business's own pages too, and a new colour is a new address."""
from tests.test_core import sample,client_as,pytestmark  # noqa
def test_business_colour_inside_the_app(sample):
    t,o,s=sample
    c,u=client_as(t)
    assert "/app.css?c=" not in c.get(f"/t/{t.slug}/").content.decode()  # same colour as the portal: nothing extra
    t.brand_color="#e11d48";t.save()
    page=c.get(f"/t/{t.slug}/").content.decode()
    assert f"/brand/{t.slug}/app.css?c=e11d48" in page
    css=c.get(f"/brand/{t.slug}/app.css").content.decode()
    assert "--violet:#e11d48" in css and "--brand:" in css
