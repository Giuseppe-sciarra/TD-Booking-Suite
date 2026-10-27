#!/bin/bash
# Aggiornamento dal prototipo Prenodo 2.0.0-rc1 a questa versione, con il vecchio .env (PRENODO_*).
# Da lanciare nella cartella del progetto, con:
#   PROTO    cartella del prototipo originale (quella con manage.py e il pacchetto prenodo)
#   PY       interprete Python 3.12 con requirements.lock installato (default: python3)
#   PGHOST/PGPORT/PGUSER  un PostgreSQL di prova dove PGUSER può creare database e ruoli
# Crea e cancella il database "upgrade_test" e il ruolo "tdbooking_app": NON usarlo su un server vero.
set -euo pipefail
cd "$(dirname "$0")/../.."
NEW=$(pwd);PY=${PY:-python3};PROTO=${PROTO:?indica in PROTO la cartella del prototipo}
export PGHOST=${PGHOST:-localhost} PGPORT=${PGPORT:-5432} PGUSER=${PGUSER:-postgres}
W=$(mktemp -d)
step(){ echo "ok  $1"; }
psql -q -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS upgrade_test" -c "DROP ROLE IF EXISTS tdbooking_app" -c "CREATE DATABASE upgrade_test" >/dev/null 2>&1 \
  || { echo "FALLITO: il ruolo tdbooking_app esiste ancora in un altro database di prova: eliminalo prima del collaudo"; exit 1; }
# the prototype's own .env: PRENODO_* variables, development mode, superuser connection as it shipped
export PRENODO_DEV=1 PRENODO_KEYS=$($PY -c 'import base64,json,secrets;k=lambda:base64.urlsafe_b64encode(secrets.token_bytes(32)).decode();print(json.dumps({"v1":k(),"index":k()}))')
export DJANGO_SECRET_KEY=$($PY -c 'import secrets;print(secrets.token_urlsafe(64))') PUBLIC_ORIGIN=http://localhost:8000
export DATABASE_URL=postgresql://$PGUSER@$PGHOST:$PGPORT/upgrade_test
APP_DB_PASSWORD=$($PY -c 'import secrets;print(secrets.token_hex(24))')

echo "1. Database creato e popolato con il codice originale del prototipo"
(cd "$PROTO" && $PY manage.py migrate --noinput -v0 && $PY manage.py showmigrations core | grep "\[X\]")
(cd "$PROTO" && STATE=$W/state.json $PY manage.py shell -c "
import json,os,re,secrets
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
from django.utils import timezone
from core.models import *
from core.tenancy import scope
from core.booking import create_booking
from core.crypto import decrypt
t=Tenant.objects.create(slug='pizzeria-storica',name='Pizzeria Storica')
with scope(t):
    loc=Location.objects.create(tenant=t,name='Centro')
    for d in range(7):Schedule.objects.create(tenant=t,weekday=d,start=time(9),end=time(18))
    svc=Service.objects.create(tenant=t,location=loc,name='Taglio',kind='resource',duration=60,interval=30,lead_minutes=0)
    res=Resource.objects.create(tenant=t,location=loc,name='Sala 1')
    ServiceResource.objects.create(tenant=t,service=svc,resource=res)
    day=(timezone.now()+timedelta(days=3)).astimezone(ZoneInfo(t.timezone)).date()
    start=datetime.combine(day,time(10),ZoneInfo(t.timezone)).isoformat()
    b,_=create_booking(t,{'service':str(svc.pk),'resource':str(res.pk),'start':start,'party':1,'first_name':'Mario','last_name':'Rossi','email':'mario@example.invalid','phone':'+393331234567','notes':'Cliente storico','marketing':True,'request_key':secrets.token_urlsafe(24)})
    assert b.contact.consent=='pending' and Delivery.objects.count()==2
    consent=[re.search(r'/action/[^/]+/([^/]+)/',decrypt(t,d.payload,'delivery:'+str(d.pk))['body']).group(1) for d in Delivery.objects.all() if 'iscrizione' in decrypt(t,d.payload,'delivery:'+str(d.pk))['subject']][0]
    svc.refresh_from_db();service={f.name:str(getattr(svc,f.attname)) for f in svc._meta.concrete_fields}
    json.dump({'booking':str(b.pk),'start':b.start.isoformat(),'status':b.status,'resource':str(res.pk),'service':service,'consent':consent,'day':day.isoformat()},open(os.environ['STATE'],'w'))
print('\n   prenotazione, contatto con consenso in sospeso e 2 email in coda creati')
")

echo;echo "2. Aggiornamento con TD Booking Suite $(cat VERSION)"
$PY manage.py migrate --noinput | grep "Applying"
STATE=$W/state.json $PY manage.py shell -c "
import json,os,re,secrets
from datetime import datetime,time
from zoneinfo import ZoneInfo
from django.conf import settings
from django.test import Client
from core.models import *
from core.tenancy import scope
from core.booking import create_booking,BookingError
def ok(c,m):
    if not c:raise SystemExit('FALLITO: '+m)
    print('ok ',m)
s=json.load(open(os.environ['STATE']))
t=Tenant.objects.get(slug='pizzeria-storica')
with scope(t):
    b=Booking.objects.get(pk=s['booking'])
    ok(b.data['phone']=='+393331234567' and b.data['first_name']=='Mario','prenotazione del prototipo leggibile con le chiavi originali')
    svc=b.service;same=all(str(getattr(svc,f.attname))==s['service'][f.name] for f in svc._meta.concrete_fields if f.name in s['service'])
    new=[f for f in svc._meta.concrete_fields if f.name not in s['service']]
    ok(same and b.start.isoformat()==s['start'] and b.status==s['status'] and str(b.resource_id)==s['resource'] and all(getattr(svc,f.attname)==f.get_default() for f in new),'dati storici intatti, nessuna regola inventata')
    ok(Delivery.objects.count()==2 and set(Delivery.objects.values_list('channel',flat=True))=={'email'},'email già in coda migrate come canale email')
c=Client();path=f'/action/{t.slug}/{s[\"consent\"]}/';host=settings.ALLOWED_HOSTS[0]
page=c.get(path,HTTP_HOST=host);done=c.post(path,HTTP_HOST=host)
with scope(t):ok(page.status_code==200 and done.status_code==200 and Contact.objects.get(pk=b.contact_id).consent=='yes','consenso chiesto col prototipo confermabile dopo l aggiornamento')
with scope(t):
    base={'service':str(svc.pk),'resource':s['resource'],'party':1,'first_name':'Anna','last_name':'Bianchi','email':'anna@example.invalid','phone':'+393339876543'}
    tz=ZoneInfo(t.timezone);day=datetime.fromisoformat(s['day']).date()
    try:create_booking(t,{**base,'start':datetime.combine(day,time(10),tz).isoformat(),'request_key':secrets.token_urlsafe(24)});taken=False
    except BookingError:taken=True
    ok(taken,'stessa risorsa e orario rifiutati: le prenotazioni vecchie occupano ancora il posto')
    n,created=create_booking(t,{**base,'start':datetime.combine(day,time(12),tz).isoformat(),'request_key':secrets.token_urlsafe(24)})
    ok(created and n.status=='confirmed','nuova prenotazione con le regole della nuova versione')
"
POSTGRES_USER=$PGUSER POSTGRES_DB=upgrade_test APP_DB_PASSWORD=$APP_DB_PASSWORD sh -c 'sed "s/DATABASE tdbooking/DATABASE upgrade_test/" deploy/init-db.sh | sh' >/dev/null || { echo "FALLITO: init-db.sh"; exit 1; }
$PY manage.py grant_runtime
# check_runtime does nothing in development mode: run it as production does, with the application role
PRENODO_DEV=0 PUBLIC_ORIGIN=https://booking.tdbs-collaudo.it DATABASE_URL=postgresql://tdbooking_app:$APP_DB_PASSWORD@$PGHOST:$PGPORT/upgrade_test $PY manage.py check_runtime | tee $W/runtime.txt
grep -q "superati" $W/runtime.txt || { echo "FALLITO: check_runtime"; exit 1; }
rm -rf "${W:?}"
echo "AGGIORNAMENTO SUPERATO"
