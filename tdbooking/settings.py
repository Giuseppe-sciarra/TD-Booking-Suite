import os,json,base64
from pathlib import Path
from urllib.parse import urlparse
import dj_database_url
BASE_DIR=Path(__file__).resolve().parent.parent
def env(name,default=None):
    """TDBS_* variables; PRENODO_* still accepted for .env files created by the prototype."""
    return os.environ.get("TDBS_"+name,os.environ.get("PRENODO_"+name,default))
DEV=env("DEV")=="1"
TESTING=env("TESTING")=="1"
DEBUG=DEV
SECRET_KEY=os.environ.get("DJANGO_SECRET_KEY","")
KEYS=json.loads(env("KEYS","{}"))
ACTIVE_KEY=env("ACTIVE_KEY","v1")
EDITION=env("EDITION","cloud")
VERSION=(BASE_DIR/"VERSION").read_text().strip()
# AGPL-3.0: the footer links here so every user can reach the source; empty hides the link
SOURCE_URL=env("SOURCE_URL","https://github.com/Giuseppe-sciarra/TD-Booking-Suite").strip()
# hooks for an edition built on top of this one (the Windows edition sets them)
EXTRA_NAV=[]  # extra links in the agency menu: [{"label":..., "url":...}]
EXTRA_CSS=[]  # stylesheets an edition loads after app.css on its own pages (static paths), e.g. a denser desktop look
TENANT_LIMIT=None  # most businesses the agency may create, None = no limit; Private is always 1
MORE_TENANTS_HINT="Per più attività serve l'edizione Cloud"
EDITION_LABEL=""  # name in the sidebar, empty = Private or Cloud
AGENCY_NAMES={"area":"Area agenzia","admin":"Amministratore dell'agenzia","who":"agenzia","access":"Accesso dell'agenzia"}  # how the staff pages and role are called
ROLE_LABELS={}  # an edition can rename roles: {"owner":"Titolare"}
LOGIN_NOTE=()  # an edition can explain its accounts under the sign-in form: ({"title","text"},...)
LOGIN_USERNAMES=False  # an edition can let accounts have a username, with the email optional
MFA_ENABLED=True  # Authenticator and passkeys; an edition on a PC that only it reaches can turn them off
MFA_REMINDER="all"  # who sees "Proteggi il tuo account" until a second factor is set: "all", "staff" or ""
PUBLIC_ORIGIN=os.environ.get("PUBLIC_ORIGIN","http://localhost:8000" if DEV else "").rstrip("/")
if "index" not in KEYS or len(SECRET_KEY)<50 or ACTIVE_KEY not in KEYS or len(base64.urlsafe_b64decode(KEYS[ACTIVE_KEY]))!=32:
    raise RuntimeError("Configurare DJANGO_SECRET_KEY e TDBS_KEYS con chiavi casuali distinte; usare scripts/configure.py.")
if EDITION not in ("private","cloud"): raise RuntimeError("Edizione non valida")
origin=urlparse(PUBLIC_ORIGIN)
if not origin.hostname or origin.path or origin.query or origin.username or (not DEV and origin.scheme!="https"):
    raise RuntimeError("PUBLIC_ORIGIN deve essere una origine HTTPS, senza percorso.")
ALLOWED_HOSTS=[origin.hostname]+(["localhost","127.0.0.1","testserver"] if DEV or TESTING else [])
CSRF_TRUSTED_ORIGINS=[PUBLIC_ORIGIN]
ROOT_URLCONF="tdbooking.urls"
WSGI_APPLICATION="tdbooking.wsgi.application"
INSTALLED_APPS=["django.contrib.auth","django.contrib.contenttypes","django.contrib.sessions","django.contrib.messages","django.contrib.staticfiles","core"]
MIDDLEWARE=["django.middleware.security.SecurityMiddleware","whitenoise.middleware.WhiteNoiseMiddleware","django.contrib.sessions.middleware.SessionMiddleware","django.middleware.common.CommonMiddleware","django.middleware.csrf.CsrfViewMiddleware","django.contrib.auth.middleware.AuthenticationMiddleware","django.contrib.messages.middleware.MessageMiddleware","core.middleware.GuardMiddleware","django.middleware.clickjacking.XFrameOptionsMiddleware"]
TEMPLATES=[{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[BASE_DIR/"templates"],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages","core.context.presentation"]}}]
DATABASES={"default":dj_database_url.parse(os.environ.get("DATABASE_URL",f"sqlite:///{BASE_DIR / 'dev.sqlite3'}"),conn_max_age=60)}
if DATABASES["default"]["ENGINE"]=="django.db.backends.sqlite3":
    DATABASES["default"]["OPTIONS"]={"timeout":20,"transaction_mode":"IMMEDIATE"}
if not (DEV or TESTING) and DATABASES["default"]["ENGINE"]!="django.db.backends.postgresql":
    raise RuntimeError("PostgreSQL obbligatorio in produzione.")
AUTH_USER_MODEL="core.User"
AUTHENTICATION_BACKENDS=["core.identity.EmailBackend"]
PASSWORD_HASHERS=["django.contrib.auth.hashers.Argon2PasswordHasher","django.contrib.auth.hashers.PBKDF2PasswordHasher"]
AUTH_PASSWORD_VALIDATORS=[{"NAME":"django.contrib.auth.password_validation.MinimumLengthValidator","OPTIONS":{"min_length":12}},{"NAME":"django.contrib.auth.password_validation.CommonPasswordValidator"},{"NAME":"django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE="it"
TIME_ZONE="Europe/Rome"
USE_I18N=True
USE_TZ=True
DEFAULT_AUTO_FIELD="django.db.models.BigAutoField"
STATIC_URL="/static/"
STATIC_ROOT=BASE_DIR/"static-collected"
STATICFILES_DIRS=[BASE_DIR/"static"]
STORAGES={"default":{"BACKEND":"django.core.files.storage.FileSystemStorage"},"staticfiles":{"BACKEND":"whitenoise.storage.CompressedManifestStaticFilesStorage"}}
SESSION_COOKIE_NAME="tdbs_session" if DEV else "__Host-tdbs_session"
SESSION_COOKIE_SECURE=not DEV
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE="Lax"
SESSION_COOKIE_AGE=3600*8
SESSION_EXPIRE_AT_BROWSER_CLOSE=True
# 2.6.0: "Resta collegato" on the login page keeps a device signed in this many days since the last visit
SESSION_REMEMBER_DAYS=max(1,min(365,int(env("SESSION_REMEMBER_DAYS") or 30)))
CSRF_COOKIE_SECURE=not DEV
CSRF_COOKIE_HTTPONLY=True
CSRF_COOKIE_SAMESITE="Lax"
SECURE_SSL_REDIRECT=not DEV
SECURE_HSTS_SECONDS=31536000 if not DEV else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
SECURE_HSTS_PRELOAD=True
SECURE_CONTENT_TYPE_NOSNIFF=True
SECURE_REFERRER_POLICY="same-origin"
X_FRAME_OPTIONS="DENY"
# Only the bundled proxy may reach the app in production.
TRUST_PROXY=os.environ.get("TRUST_PROXY")=="1"
FORMS_URLFIELD_ASSUME_HTTPS=True
SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO","https") if os.environ.get("TRUST_PROXY")=="1" else None
DATA_UPLOAD_MAX_MEMORY_SIZE=131072
DATA_UPLOAD_MAX_NUMBER_FIELDS=100
FILE_UPLOAD_MAX_MEMORY_SIZE=131072
LOGIN_URL="/login/"
EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST=os.environ.get("TRANSACTIONAL_SMTP_HOST","")
EMAIL_PORT=int(os.environ.get("TRANSACTIONAL_SMTP_PORT","587"))
EMAIL_HOST_USER=os.environ.get("TRANSACTIONAL_SMTP_USER","")
EMAIL_HOST_PASSWORD=os.environ.get("TRANSACTIONAL_SMTP_PASSWORD","")
EMAIL_USE_TLS=True
EMAIL_TIMEOUT=15
DEFAULT_FROM_EMAIL=os.environ.get("TRANSACTIONAL_FROM","")
LOGGING={"version":1,"disable_existing_loggers":False,"handlers":{"console":{"class":"logging.StreamHandler"}},"root":{"handlers":["console"],"level":"WARNING"}}
