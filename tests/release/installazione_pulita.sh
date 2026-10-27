#!/bin/bash
# Installazione pulita in modalità produzione, con gli stessi passi di Dockerfile e compose.yaml,
# senza immagini Docker. Da lanciare nella cartella del progetto, con:
#   PY       interprete Python 3.12 con requirements.lock installato (default: python3)
#   PGHOST/PGPORT/PGUSER  un PostgreSQL di prova dove PGUSER può creare database e ruoli
# Crea e cancella il database "tdbooking" e il ruolo "tdbooking_app": NON usarlo su un server vero.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PY:-python3}
S=$(mktemp -d)
export PGHOST=${PGHOST:-localhost} PGPORT=${PGPORT:-5432} PGUSER=${PGUSER:-postgres}
step(){ echo "ok  $1"; }
fail(){ echo "FALLITO: $1"; exit 1; }
[ -e .env ] && { echo "Esiste già un .env: sposta la configurazione reale prima del collaudo";exit 1; }
$PY scripts/configure.py --edition cloud --origin https://booking.tdbs-collaudo.it >/dev/null
[ "$(stat -c %a .env)" = "600" ] || fail "configure.py: .env creato con permessi 600"
step "configure.py: .env creato con permessi 600"
$PY scripts/configure.py --edition cloud --origin https://booking.tdbs-collaudo.it >/dev/null 2>&1 && { echo "FALLITO: .env sovrascritto"; exit 1; } || step "configure.py: rifiuta di sovrascrivere .env"
if command -v docker >/dev/null;then
  docker compose --env-file .env -f compose.yaml config --quiet || fail "compose.yaml valido con il .env generato"
  step "compose.yaml valido con il .env generato"
fi
eval "$(python3 -c 'import shlex;[print("export "+k+"="+shlex.quote(v)) for k,v in (l.rstrip("\n").split("=",1) for l in open(".env") if "=" in l)]')"
psql -q -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS tdbooking" -c "DROP ROLE IF EXISTS tdbooking_app" -c "CREATE DATABASE tdbooking" >/dev/null 2>&1 \
  || { echo "FALLITO: il ruolo tdbooking_app esiste ancora in un altro database di prova: eliminalo prima del collaudo"; exit 1; }
POSTGRES_USER=postgres POSTGRES_DB=tdbooking sh deploy/init-db.sh >/dev/null || { echo "FALLITO: init-db.sh"; exit 1; }
step "init-db.sh: ruolo tdbooking_app creato senza privilegi"
ADMIN_URL=postgresql://$PGUSER@$PGHOST:$PGPORT/tdbooking
APP_URL=postgresql://tdbooking_app:${APP_DB_PASSWORD}@$PGHOST:$PGPORT/tdbooking
DATABASE_URL=$ADMIN_URL $PY manage.py migrate --noinput -v0 || fail "migrazioni 0001-0009 applicate su database vuoto"
step "migrazioni 0001-0009 applicate su database vuoto"
DATABASE_URL=$ADMIN_URL $PY manage.py grant_runtime >/dev/null || fail "grant_runtime: permessi al ruolo applicativo"
step "grant_runtime: permessi al ruolo applicativo"
PYTHONPATH=. $PY scripts/build_static.py >/dev/null && ls static-collected | grep "app\.[0-9a-f]\{12\}\.css" >/dev/null || fail "build_static.py: file statici con nome hash"
step "build_static.py: file statici con nome hash"
DATABASE_URL=$APP_URL $PY manage.py check --deploy --fail-level WARNING >/dev/null 2>&1 && step "manage.py check --deploy senza avvisi" || { DATABASE_URL=$APP_URL $PY manage.py check --deploy 2>&1 | tail -5; exit 1; }
DATABASE_URL=$APP_URL $PY manage.py check_runtime | grep "superati" >/dev/null || fail "check_runtime come ruolo applicativo"
step "check_runtime come ruolo applicativo"
DATABASE_URL=$ADMIN_URL $PY manage.py check_runtime >/dev/null 2>&1 && { echo "FALLITO: avvio come superuser accettato"; exit 1; } || step "check_runtime rifiuta l'avvio come superuser"
DATABASE_URL=$APP_URL $PY - <<'PYEOF'
import getpass,os,django
os.environ.setdefault("DJANGO_SETTINGS_MODULE","tdbooking.settings");django.setup()
getpass.getpass=lambda prompt="":"Admin-collaudo-password-2026"
from django.core.management import call_command
call_command("bootstrap","--email","admin@tdbs-collaudo.it")
from core.models import Tenant,Location,Service,Schedule
from core.platform import seed_tenant
from django.db import connection
with connection.cursor() as c:c.execute("SELECT current_user");assert c.fetchone()[0]=="tdbooking_app"
t=Tenant.objects.create(slug="pizzeria-collaudo",name="Pizzeria Collaudo")
seed_tenant(t,"ristorante")
PYEOF
step "bootstrap amministratore e attività da modello 'ristorante' come ruolo applicativo"
DATABASE_URL=$APP_URL $PY -m gunicorn tdbooking.wsgi:application --no-control-socket --bind 127.0.0.1:8001 --workers 2 --threads 2 --log-level warning --daemon --pid $S/gunicorn.pid
sleep 3
DATABASE_URL=$APP_URL $PY - <<'PYEOF'
import re,json,requests,secrets
from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
B="http://127.0.0.1:8001";H={"Host":"booking.tdbs-collaudo.it","X-Forwarded-Proto":"https","X-Forwarded-For":"203.0.113.10"}  # as set by NPMplus
def ok(c,m):
    if not c:raise SystemExit("FALLITO: "+m)
    print("ok ",m)
r=requests.get(B+"/login/",headers={"Host":"booking.tdbs-collaudo.it"},allow_redirects=False)
ok(r.status_code==301 and r.headers["Location"].startswith("https://"),"HTTP reindirizzato a HTTPS")
r=requests.get(B+"/login/",headers=H)
ok(r.status_code==200,"pagina di accesso")
ok("max-age=31536000" in r.headers.get("Strict-Transport-Security","") and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"],"HSTS e CSP attivi")
ok(requests.get(B+"/login/",headers={**H,"Host":"evil.example"}).status_code==400,"host non autorizzato rifiutato")
css=re.search(r'href="(/static/app\.[0-9a-f]{12}\.css)"',r.text)
ok(css and requests.get(B+css.group(1),headers=H).status_code==200,"file statici serviti con nome hash")
ok(requests.get(B+"/book/pizzeria-collaudo/api/slots/",params={"service":"x","date":"2030-01-01"},headers={k:v for k,v in H.items() if k!="X-Forwarded-For"}).status_code==403,"senza l'IP inoltrato dal proxy le API si bloccano")
ok(requests.get(B+"/t/pizzeria-collaudo/",headers=H,allow_redirects=False).status_code==302,"portale protetto da login")
s=requests.Session()
page=s.get(B+"/book/pizzeria-collaudo/",headers=H)
ok(page.status_code==200,"pagina prenotazioni pubblica")
cfg=json.loads(page.text.split('id="widget-config" type="application/json">')[1].split("</script>")[0])
svc=cfg["services"][0];ok(svc["name"]=="Ristorante","servizio creato dal modello")
day=(datetime.now(ZoneInfo("Europe/Rome"))+timedelta(days=3)).date().isoformat()
slots=s.get(B+cfg["slots"],params={"service":svc["id"],"date":day,"party":2},headers=H).json()["slots"]
ok(len(slots)>0,f"{len(slots)} orari disponibili")
chain=s.get(B+cfg["slots"],params={"service":svc["id"],"date":day,"party":2},headers={**H,"X-Forwarded-For":"198.51.100.7, 203.0.113.10"})
ok(chain.status_code==200,"indirizzo aggiunto in coda da nginx/NPMplus accettato")
csrf=s.cookies.get("csrftoken")
body={"service":svc["id"],"start":slots[0]["start"],"party":2,"first_name":"Collaudo","last_name":"Finale","email":"collaudo@tdbs-collaudo.it","phone":"+393331234567","request_key":secrets.token_urlsafe(24)}
hdr={**H,"Origin":"https://booking.tdbs-collaudo.it","Referer":"https://booking.tdbs-collaudo.it/book/pizzeria-collaudo/","X-CSRFToken":csrf or "","X-TDBS-Ticket":cfg["ticket"],"Content-Type":"application/json"}
r=s.post(B+cfg["submit"],data=json.dumps(body),headers=hdr)
ok(r.status_code==201 and r.json()["ok"],"prenotazione pubblica confermata")
again=s.post(B+cfg["submit"],data=json.dumps(body),headers=hdr)
ok(again.status_code==200 and again.json()["reference"]==r.json()["reference"],"stessa richiesta ripetuta: nessun doppione")
bad=s.post(B+cfg["submit"],data=json.dumps(body),headers={**hdr,"Origin":"https://evil.example"})
ok(bad.status_code==403,"origine estranea rifiutata")
cancel=s.get(B+r.json()["cancel"],headers=H)
ok(cancel.status_code==200 and "Annulla prenotazione" in cancel.text,"link di annullamento con conferma")
PYEOF
DATABASE_URL=$APP_URL $PY manage.py shell -c "
from core.mail import worker_once
from core.models import Tenant,Delivery
from core.tenancy import scope
worker_once()
t=Tenant.objects.get(slug='pizzeria-collaudo')
with scope(t):print('ok  email di conferma in coda:',Delivery.objects.filter(status='pending',last_code='smtp_missing').count(),'(nessun SMTP configurato, resta in attesa)')"
kill $(cat $S/gunicorn.pid)
# init-db.sh did not run (2.0.2: unreadable in the container): the migration step must create the role itself
psql -q -v ON_ERROR_STOP=1 -d tdbooking -c "DROP OWNED BY tdbooking_app" >/dev/null && psql -q -v ON_ERROR_STOP=1 -c "DROP ROLE tdbooking_app" >/dev/null
DATABASE_URL=$ADMIN_URL $PY manage.py grant_runtime | grep "creato senza privilegi" >/dev/null || fail "grant_runtime: ricrea il ruolo applicativo se init-db.sh non ha girato"
step "grant_runtime: ricrea il ruolo applicativo se init-db.sh non ha girato"
DATABASE_URL=$APP_URL $PY manage.py check_runtime | grep "superati" >/dev/null || fail "check_runtime con il ruolo ricreato e la password di .env"
step "check_runtime con il ruolo ricreato e la password di .env"
rm -f .env
echo "INSTALLAZIONE PULITA SUPERATA"
