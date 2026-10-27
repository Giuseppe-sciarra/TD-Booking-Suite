"""2.8.0: what the nightly update (scripts/aggiorna-tutto.sh) checks inside the new image before keeping it.
Every check uses a library the way the portal does; one line per check, then a JSON line for the script."""
import io,json,platform
from importlib import metadata
from urllib.parse import urlparse
from django.conf import settings
from django.core.management import BaseCommand,call_command
LIBRARIES=("Django","psycopg","cryptography","pillow","webauthn","PyOTP","qrcode","argon2-cffi","gunicorn","whitenoise","dj-database-url")

def versions():
    from django.db import connection
    found={"TD Booking Suite":settings.VERSION,"Python":platform.python_version()}
    try:found["Debian"]=open("/etc/debian_version").read().strip()
    except OSError:pass
    for name in LIBRARIES:
        try:found[name]=metadata.version(name)
        except metadata.PackageNotFoundError:found[name]=""
    if connection.vendor=="postgresql":
        with connection.cursor() as c:c.execute("SHOW server_version");found["PostgreSQL"]=c.fetchone()[0].split()[0]
    return found

def check_database():
    call_command("check_runtime",stdout=io.StringIO())
    from core.models import SiteSettings
    SiteSettings.current()
def check_crypto():
    from core.crypto import protect,reveal
    assert reveal(protect({"n":1},"selftest"),"selftest")=={"n":1}
def check_passwords():
    from django.contrib.auth.hashers import make_password,check_password
    hashed=make_password("selftest-password")
    assert hashed.startswith("argon2") and check_password("selftest-password",hashed)
def check_logos():
    from PIL import Image
    from core.site import clean_brand
    buf=io.BytesIO();Image.new("RGBA",(400,100),(120,80,160,255)).save(buf,format="PNG");buf.seek(0)
    data,ratio=clean_brand(buf)
    assert data.startswith(b"\x89PNG") and ratio==4
def check_authenticator():
    import pyotp,qrcode
    totp=pyotp.TOTP(pyotp.random_base32())
    assert totp.verify(totp.now())
    buf=io.BytesIO();qrcode.make(totp.provisioning_uri(name="selftest@example.com",issuer_name="TD Booking Suite")).save(buf,format="PNG")
    assert buf.getvalue().startswith(b"\x89PNG")
def check_passkeys():
    from webauthn import generate_registration_options,options_to_json
    options=generate_registration_options(rp_id=urlparse(settings.PUBLIC_ORIGIN).hostname,rp_name="TD Booking Suite",user_id=b"selftest",user_name="selftest")
    assert "challenge" in json.loads(options_to_json(options))
def check_pages():
    from django.test import Client
    from core.models import Tenant
    client=Client(HTTP_HOST=urlparse(settings.PUBLIC_ORIGIN).netloc)
    page=client.get("/login/",secure=True)
    assert page.status_code==200 and b"login-submit" in page.content,f"accesso: {page.status_code}"
    tenant=Tenant.objects.filter(status="active").order_by("created").first()
    if tenant:
        page=client.get(f"/book/{tenant.slug}/",secure=True)
        assert page.status_code<500,f"pagina prenotazioni: {page.status_code}"
CHECKS=[("database e isolamento delle attività",check_database),("cifratura",check_crypto),("password",check_passwords),
        ("loghi",check_logos),("app Authenticator e codice QR",check_authenticator),("passkey",check_passkeys),
        ("pagine e file statici",check_pages)]

class Command(BaseCommand):
    help="Prova le parti del portale che dipendono da librerie e immagine: la usa l'aggiornamento notturno."
    def add_arguments(self,p):p.add_argument("--versioni",action="store_true",help="solo le versioni installate, in JSON")
    def handle(self,*a,**kw):
        if kw["versioni"]:
            self.stdout.write(json.dumps(versions()));return
        failed=0
        for name,check in CHECKS:
            try:check();self.stdout.write("OK      "+name)
            except Exception as ex:  # any failure of the new image, whatever its kind, means: keep the old one
                failed+=1;self.stdout.write(f"ERRORE  {name}: {type(ex).__name__} {ex}"[:400])
        try:found=versions()
        except Exception:found={}
        self.stdout.write(json.dumps({"ok":not failed,"versioni":found}))
        if failed:raise SystemExit(1)
