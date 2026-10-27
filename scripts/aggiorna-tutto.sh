#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# aggiorna-tutto.sh — aggiornamento AUTOMATICO di TD Booking Suite, ogni notte (come il CRM)
# ─────────────────────────────────────────────────────────────────────────────
# Cosa aggiorna:
#   • Python 3.12 all'ultima immagine e i pacchetti Debian;
#   • TUTTE le librerie Python all'ultima versione, major comprese, tranne i limiti scritti in
#     requirements.in con il motivo (per esempio Django resta sulla 5.2 LTS);
#   • PostgreSQL 17 all'ultima versione 17.x (mai il passaggio a una versione maggiore).
# Il codice di TD Booking Suite non lo tocca e GitHub non lo guarda: la versione nuova si installa
# a mano (INSTALLAZIONE.md, passo 11).
#
# Come, nell'ordine:
#   1. fotografa le versioni installate;
#   2. guarda se ci sono immagine Python nuova, librerie nuove, pacchetti Debian, PostgreSQL nuovo;
#   3. se serve ricostruisce l'immagine mentre il portale gira ancora con quella di prima;
#   4. ferma portale e invio email, fa il backup cifrato del database (serve la chiave del passo 9
#      di INSTALLAZIONE.md), aggiorna PostgreSQL, applica le migrazioni e riavvia
#      (il portale resta giù da qualche secondo a un minuto);
#   5. prova la versione nuova: pagina di accesso dall'host e `manage.py selftest` DENTRO il container
#      (database, cifratura, password, loghi, Authenticator, passkey, pagine): se qualcosa non va
#      → ROLLBACK a immagine, librerie e, se le migrazioni l'hanno cambiato, database di prima;
#   6. email di riepilogo all'agenzia, con l'invio email del portale (Impostazioni generali);
#   7. pulizia di Docker che non tocca le immagini di rollback.
#
#   bash scripts/aggiorna-tutto.sh              → aggiornamento vero
#   bash scripts/aggiorna-tutto.sh --dry-run    → dice cosa farebbe, non tocca niente
#   bash scripts/aggiorna-tutto.sh --solo-app   → niente PostgreSQL
#
# Variabili (nel .env oppure davanti al comando):
#   TDBS_AGG_EMAIL=           a chi mandare il riepilogo (vuoto = email delle richieste di prova o di assistenza)
#   TDBS_AGG_MAIL_SEMPRE=0    1 = email anche quando era già tutto aggiornato
#   TDBS_AGG_TIENI_ROLLBACK=3 quante immagini di rollback tenere
#   TDBS_AGG_CHIAVE=/root/tdbs-backup.key   la chiave dei backup
#   TDBS_AGG_BACKUP=/var/backups/tdbs       dove vanno i backup fatti prima di aggiornare
#
# Le immagini e le librerie aggiornate finiscono in compose.override.yaml e requirements.auto.lock
# (generati, fuori da git): docker compose li legge da solo. Per tornare a quelli del rilascio:
#   rm compose.override.yaml requirements.auto.lock && docker compose build && docker compose up -d
# ═════════════════════════════════════════════════════════════════════════════
set -uo pipefail
cd "${TDBS_AGG_DIR:-$(dirname "$0")/..}" || exit 1
DIR="$(pwd)"
export TZ=Europe/Rome   # orari del log e delle immagini di rollback in ora italiana (il CT è in UTC)

DRY_RUN=0
SOLO_APP=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY_RUN=1 ;;
    --solo-app) SOLO_APP=1 ;;
    *) echo "Argomento sconosciuto: $arg"; exit 2 ;;
  esac
done

# ── Un solo aggiornamento alla volta ────────────────────────────────────────
exec 9> "${TDBS_AGG_LOCK:-/tmp/tdbs-aggiorna-tutto.lock}"
if ! flock -n 9; then
  echo "$(date '+%F %T') · un altro aggiornamento è già in corso, esco."
  exit 0
fi

log() { echo "$(date '+%F %T') · $*"; }
envval() { grep -E "^[[:space:]]*$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'; }
conf() { local v="${!1:-$(envval "$1")}"; printf '%s' "${v:-$2}"; }   # $1 variabile, $2 predefinito

EMAIL="$(conf TDBS_AGG_EMAIL '')"
MAIL_SEMPRE="$(conf TDBS_AGG_MAIL_SEMPRE 0)"
TIENI="$(conf TDBS_AGG_TIENI_ROLLBACK 3)"
CHIAVE="$(conf TDBS_AGG_CHIAVE /root/tdbs-backup.key)"
BACKUP_DIR="$(conf TDBS_AGG_BACKUP /var/backups/tdbs)"
PROVE="${TDBS_AGG_PROVE:-36}"       # tentativi per vedere se risponde…
PAUSA="${TDBS_AGG_PAUSA:-5}"        # …ogni 5 secondi: 3 minuti

ORIGIN="$(envval PUBLIC_ORIGIN)"; HOST="${ORIGIN#*://}"; HOST="${HOST%%/*}"
PORTA="$(envval TDBS_PORT)"; PORTA="${PORTA:-8085}"
BIND="$(envval TDBS_BIND)"; [[ -z "$BIND" || "$BIND" == "0.0.0.0" ]] && BIND="127.0.0.1"
URL="http://$BIND:$PORTA"
APP_IMG="tdbooking-suite:local"

START_TS=$(date +%s)
STAMP="$(date '+%Y%m%d-%H%M')"
mkdir -p logs
# Tetto al log: il timer/cron lo allunga a ogni giro (>>). Oltre 1 MB tiene l'ultimo mezzo MB.
# cat > riscrive lo stesso file, così chi ci sta già scrivendo in coda continua a scriverci.
if [[ -f logs/aggiorna-tutto.log ]] && (( $(stat -c %s logs/aggiorna-tutto.log) > 1048576 )); then
  tail -c 524288 logs/aggiorna-tutto.log | tail -n +2 > logs/.aggiorna-tutto.taglio &&
    cat logs/.aggiorna-tutto.taglio > logs/aggiorna-tutto.log
  rm -f logs/.aggiorna-tutto.taglio
fi
RIEPILOGO=""        # righe dell'email
ESITO="nessuno"     # nessuno | ok | rollback | errore
aggiungi() { RIEPILOGO+="$*"$'\n'; }
peggiora() {        # l'esito finale è il peggiore incontrato
  case "$1" in
    errore)   ESITO="errore" ;;
    rollback) [[ "$ESITO" != "errore" ]] && ESITO="rollback" ;;
    ok)       [[ "$ESITO" == "nessuno" ]] && ESITO="ok" ;;
  esac
}
DUMP=""
# il backup in chiaro serve solo durante l'aggiornamento; la copia dello script si toglie alla fine
trap '[[ -n "$DUMP" ]] && rm -f "$DUMP"' EXIT

log "── Aggiornamento automatico di TD Booking Suite $( [[ $DRY_RUN == 1 ]] && echo '(dry-run) ' )──"
for c in docker curl flock python3; do
  command -v "$c" > /dev/null || { log "❌ manca il comando $c"; exit 1; }
done
[[ -f .env ]] || { log "❌ manca .env: questo script va lanciato dalla cartella dell'installazione"; exit 1; }

# ═════════════════════════════════════════════════════════════════════════════
# Cosa c'è adesso
# ═════════════════════════════════════════════════════════════════════════════
versioni_app() {   # JSON su una riga, dal container che gira adesso
  local out
  out=$(docker compose exec -T web python manage.py selftest --versioni 2>/dev/null | tail -1)
  [[ "$out" == \{* ]] && printf '%s' "$out" || printf '{}'
}
digest_di() {      # $1 immagine con tag → il suo digest dopo il pull
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" 2>/dev/null | grep -m1 "^${1%:*}@" | cut -d@ -f2
}
override_val() { sed -n "s/.*$1: *\"\([^\"]*\)\".*/\1/p" compose.override.yaml 2>/dev/null | head -1; }   # i valori li scrive sempre tra virgolette

PRIMA=$(versioni_app)
log "versioni attuali: $PRIMA"

BASE_ATT="$(override_val PYTHON_IMAGE)"; BASE_ATT="${BASE_ATT:-$(sed -n 's/^ARG PYTHON_IMAGE=//p' Dockerfile)}"
LOCK_ATT="requirements.lock"; [[ -s requirements.auto.lock ]] && LOCK_ATT="requirements.auto.lock"
DB_CID=$(docker compose ps -q db 2>/dev/null | head -1)
DB_ATT=""; [[ -n "$DB_CID" ]] && DB_ATT=$(docker inspect -f '{{.Config.Image}}' "$DB_CID" 2>/dev/null)
db_immagine() { sed -n 's/^ *image: *\([^ ]*postgres:[^ ]*\).*/\1/p' | head -1; }   # l'immagine del servizio db in un compose.yaml
DB_ATT="${DB_ATT:-$(override_val image)}"; DB_ATT="${DB_ATT:-$(db_immagine < compose.yaml)}"

# ── 2. Immagine Python (3.12 + Debian): c'è una versione nuova? ────────────
BASE_RIL="$(sed -n 's/^ARG PYTHON_IMAGE=//p' Dockerfile)"
BASE_TAG="${BASE_RIL%@*}"
BASE_NUOVA="$BASE_ATT"
if docker pull -q "$BASE_TAG" > /dev/null 2>&1 && D=$(digest_di "$BASE_TAG") && [[ -n "$D" ]]; then
  BASE_NUOVA="$BASE_TAG@$D"
else
  log "⚠ pull di $BASE_TAG non riuscito: resta l'immagine Python di adesso"
fi
BASE_CAMBIATA=0
[[ "$BASE_NUOVA" != "$BASE_ATT" ]] && BASE_CAMBIATA=1
log "immagine Python $BASE_TAG: $( [[ $BASE_CAMBIATA == 1 ]] && echo 'NUOVA' || echo 'invariata')"

# ── 3. Ultima versione di tutte le librerie (rispettando requirements.in) ──
#    Nel container usa e getta entrano SOLO requirements.in e la lista di adesso: niente .env, niente dati.
rm -rf .agg-tmp .agg-lock-nuovo .agg-modifiche.txt
mkdir -p .agg-tmp
cp requirements.in .agg-tmp/requirements.in 2>/dev/null
cp "$LOCK_ATT" .agg-tmp/attuale.lock
docker run --rm -v "$DIR/.agg-tmp:/w" -w /w "$BASE_NUOVA" sh -c '
  pip install -q --root-user-action=ignore --disable-pip-version-check --upgrade pip > /dev/null 2>&1
  pip install -q --root-user-action=ignore --disable-pip-version-check --dry-run --ignore-installed \
      --report /w/report.json -r requirements.in' > logs/pip-risoluzione.log 2>&1
if [[ -s .agg-tmp/report.json ]]; then
  python3 - "$DIR/.agg-tmp" >> logs/pip-risoluzione.log 2>&1 <<'PY'
import datetime, json, re, sys
w = sys.argv[1]
rep = json.load(open(f"{w}/report.json"))
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
nuove = {norm(i["metadata"]["name"]): (i["metadata"]["name"], i["metadata"]["version"]) for i in rep["install"]}
vecchie = {}
for riga in open(f"{w}/attuale.lock"):
    m = re.match(r"^\s*([A-Za-z0-9_.\-]+)==([^\s#;]+)", riga)
    if m:
        vecchie[norm(m.group(1))] = m.group(2)
with open(f"{w}/lock.nuovo", "w") as f:
    f.write("# GENERATO da scripts/aggiorna-tutto.sh, non modificarlo a mano: le librerie si scelgono in requirements.in\n")
    f.write(f"# Versioni esatte di tutto (dipendenze comprese), risolte il {datetime.date.today():%d/%m/%Y}.\n")
    for n in sorted(nuove):
        f.write(f"{nuove[n][0]}=={nuove[n][1]}\n")
def tipo(a, b):
    pa, pb = (re.split(r"[.\-+]", a) + ["0", "0"])[:3], (re.split(r"[.\-+]", b) + ["0", "0"])[:3]
    return "MAJOR" if pa[0] != pb[0] else "minor" if pa[1] != pb[1] else "patch"
righe = []
for n in sorted(set(nuove) | set(vecchie)):
    a, b = vecchie.get(n), nuove.get(n, (None, None))[1]
    if a == b:
        continue
    if a is None:
        righe.append(f"+ {nuove[n][0]} {b} (nuova dipendenza)")
    elif b is None:
        righe.append(f"- {n} {a} (non serve più)")
    else:
        righe.append(f"  {nuove[n][0]} {a} → {b} ({tipo(a, b)})")
open(f"{w}/modifiche.txt", "w").write("\n".join(righe) + ("\n" if righe else ""))
PY
  [[ -s .agg-tmp/lock.nuovo ]] && mv .agg-tmp/lock.nuovo .agg-lock-nuovo
  [[ -f .agg-tmp/modifiche.txt ]] && mv .agg-tmp/modifiche.txt .agg-modifiche.txt
fi
rm -rf .agg-tmp

LIB_CAMBIATE=0
if [[ ! -s .agg-lock-nuovo ]]; then
  log "⚠ non sono riuscito a calcolare le versioni nuove delle librerie (logs/pip-risoluzione.log): tengo quelle di adesso"
  peggiora errore
  aggiungi "❌ Librerie Python: controllo delle versioni nuove non riuscito, restano quelle di adesso."
  aggiungi "   Ultime righe di logs/pip-risoluzione.log:"
  aggiungi "$(tail -n 8 logs/pip-risoluzione.log 2>/dev/null | sed 's/^/   /')"
elif [[ -s .agg-modifiche.txt ]]; then
  LIB_CAMBIATE=1
  log "librerie Python da aggiornare:"; sed 's/^/      /' .agg-modifiche.txt
else
  log "librerie Python: già tutte all'ultima versione"
fi

# ── 4. Pacchetti Debian nell'immagine di adesso ────────────────────────────
DEB_N=$(docker run --rm --user 0 --entrypoint sh "$APP_IMG" -c 'apt-get update -qq > /dev/null 2>&1; apt-get -s -qq upgrade 2>/dev/null | grep -c "^Inst"' 2>/dev/null | tr -dc '0-9')
DEB_N="${DEB_N:-0}"
log "pacchetti Debian da aggiornare: $DEB_N"

# ── 5. PostgreSQL 17: c'è una 17.x nuova? ──────────────────────────────────
DB_RIL="$(db_immagine < compose.yaml)"
DB_TAG="${DB_RIL%@*}"
DB_NUOVA="$DB_ATT"
DB_CAMBIATA=0
if [[ $SOLO_APP == 1 ]]; then
  log "PostgreSQL: non controllato (--solo-app)"
elif [[ -z "$DB_TAG" || "${DB_TAG%%.*}" != "$(printf '%s' "${DB_ATT%@*}" | cut -d. -f1)" ]]; then
  log "⚠ il rilascio passa a $DB_TAG ma il database è ${DB_ATT%@*}: una versione maggiore di PostgreSQL si cambia a mano"
  peggiora errore
  aggiungi "❌ PostgreSQL: il rilascio chiede $DB_TAG, il database è ${DB_ATT%@*}. Va fatto a mano, con backup e ripristino."
elif docker pull -q "$DB_TAG" > /dev/null 2>&1 && D=$(digest_di "$DB_TAG") && [[ -n "$D" ]]; then
  DB_NUOVA="$DB_TAG@$D"
  [[ "$DB_NUOVA" != "$DB_ATT" ]] && DB_CAMBIATA=1
  log "PostgreSQL $DB_TAG: $( [[ $DB_CAMBIATA == 1 ]] && echo 'NUOVO' || echo 'invariato')"
else
  log "⚠ pull di $DB_TAG non riuscito: resta il PostgreSQL di adesso"
  peggiora errore
  aggiungi "❌ PostgreSQL: non sono riuscito a scaricare l'immagine nuova (resta quella di prima)."
fi

# ── 6. Serve ricostruire? ───────────────────────────────────────────────────
#    Anche se l'ultima build riuscita aveva codice, Dockerfile, librerie o immagine diversi (per esempio
#    dopo un aggiornamento fatto a mano). Al primo giro non c'è la firma: si ricostruisce.
LOCK_DOPO="$LOCK_ATT"; [[ -s .agg-lock-nuovo ]] && LOCK_DOPO=".agg-lock-nuovo"
firma() { { [[ -d .git ]] && git rev-parse HEAD; cat Dockerfile; grep -v '^#' "$LOCK_DOPO"; echo "$BASE_NUOVA"; } 2>/dev/null | sha256sum | cut -c1-16; }   # senza i commenti: la data di risoluzione cambia ogni notte
FIRMA_ORA=$(firma)
FIRMA_BUILD=$(cat .agg-ultima-build 2>/dev/null || true)
DA_RICOSTRUIRE=0
MOTIVI=()
[[ $BASE_CAMBIATA == 1 ]] && { DA_RICOSTRUIRE=1; MOTIVI+=("Python/Debian di base nuovi"); }
[[ $LIB_CAMBIATE == 1 ]]  && { DA_RICOSTRUIRE=1; MOTIVI+=("librerie Python"); }
[[ "$DEB_N" -gt 0 ]]      && { DA_RICOSTRUIRE=1; MOTIVI+=("$DEB_N pacchetti Debian"); }
[[ $DA_RICOSTRUIRE == 0 && "$FIRMA_BUILD" != "$FIRMA_ORA" ]] && { DA_RICOSTRUIRE=1; MOTIVI+=("$( [[ -z "$FIRMA_BUILD" ]] && echo 'primo giro' || echo 'diversa dall'"'"'ultima build')"); }

# ═════════════════════════════════════════════════════════════════════════════
# Aggiornamento
# ═════════════════════════════════════════════════════════════════════════════
scrivi_override() {   # $1 immagine Python, $2 immagine PostgreSQL, $3 file delle librerie
  cat > compose.override.yaml.nuovo <<EOF
# GENERATO da scripts/aggiorna-tutto.sh il $(date '+%d/%m/%Y %H:%M'): non modificarlo a mano.
# Immagini e librerie dell'ultimo aggiornamento riuscito, al posto di quelle fissate nel rilascio.
# Per tornare a quelle del rilascio:
#   rm compose.override.yaml requirements.auto.lock && docker compose build && docker compose up -d
x-tdbs-build: &tdbs-build
  context: .
  args:
    PYTHON_IMAGE: "$1"
    REQUIREMENTS: "$3"
services:
  migrate: {build: *tdbs-build}
  web: {build: *tdbs-build}
  worker: {build: *tdbs-build}
  db: {image: "$2"}
EOF
  mv compose.override.yaml.nuovo compose.override.yaml
}
app_risponde() {   # la pagina di accesso, dall'host, come la chiede NPMplus
  local code
  for _ in $(seq 1 "$PROVE"); do
    sleep "$PAUSA"
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Host: $HOST" -H "X-Forwarded-Proto: https" \
           -H "X-Forwarded-For: 127.0.0.1" "$URL/login/" 2>/dev/null)
    [[ "$code" == "200" ]] && return 0
  done
  return 1
}
db_risponde() {
  for _ in $(seq 1 "$PROVE"); do
    sleep "$PAUSA"
    docker compose exec -T db pg_isready -q -U postgres -d tdbooking > /dev/null 2>&1 && return 0
  done
  return 1
}
backup_db() {      # con portale ed email fermi: è esattamente il database che si rimette in caso di rollback
  mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR" || return 1
  # quelli degli aggiornamenti di prima restano 30 giorni, come i backup di ogni notte (passo 9)
  find "$BACKUP_DIR" -maxdepth 1 -name 'pre-aggiornamento-*.dump.enc' -mtime +30 -delete 2>/dev/null || true
  DUMP="$BACKUP_DIR/pre-aggiornamento-$STAMP.dump"
  docker compose exec -T db pg_dump -U postgres -Fc tdbooking > "$DUMP" 2> logs/backup.log && [[ -s "$DUMP" ]] || return 1
  chmod 600 "$DUMP"
  python3 scripts/backup_crypto.py encrypt "$DUMP" "$DUMP.enc" --key-file "$CHIAVE" >> logs/backup.log 2>&1
}
ripristina_db() {  # il database com'era: anche le tabelle create dalle migrazioni nuove spariscono.
                   # Si ripristina in un database a parte e si scambia solo se il ripristino è riuscito.
  docker compose exec -T db psql -q -v ON_ERROR_STOP=1 -U postgres -d postgres \
    -c "DROP DATABASE IF EXISTS tdbooking_ripristino" -c "CREATE DATABASE tdbooking_ripristino" &&
  docker compose exec -T db pg_restore -U postgres -d tdbooking_ripristino < "$DUMP" &&
  docker compose exec -T db psql -q -v ON_ERROR_STOP=1 -U postgres -d postgres \
    -c "DROP DATABASE tdbooking WITH (FORCE)" -c "ALTER DATABASE tdbooking_ripristino RENAME TO tdbooking"
}
prova_tutto() {    # pagina di accesso + selftest dentro il container nuovo
  PROVA=""
  app_risponde || { PROVA="ERRORE  la pagina di accesso non risponde su $URL/login/"; return 1; }
  PROVA=$(docker compose exec -T web python manage.py selftest 2>&1)
}

if [[ $DA_RICOSTRUIRE == 0 && $DB_CAMBIATA == 0 ]]; then
  log "niente da aggiornare"
  rm -f .agg-lock-nuovo .agg-modifiche.txt
elif [[ $DRY_RUN == 1 ]]; then
  [[ $DA_RICOSTRUIRE == 1 ]] && log "dry-run: ricostruirei l'immagine per: $(IFS=', '; echo "${MOTIVI[*]}")"
  [[ $DB_CAMBIATA == 1 ]] && log "dry-run: aggiornerei PostgreSQL a $DB_NUOVA"
  log "dry-run: prima farei il backup cifrato in $BACKUP_DIR con la chiave $CHIAVE$( [[ -f "$CHIAVE" ]] || echo ' (CHE NON C'"'"'È)')"
  rm -f .agg-lock-nuovo .agg-modifiche.txt
elif [[ ! -f "$CHIAVE" ]]; then
  log "❌ manca la chiave dei backup $CHIAVE: senza backup non aggiorno"
  peggiora errore
  aggiungi "❌ Niente aggiornato: manca la chiave dei backup $CHIAVE (INSTALLAZIONE.md, passo 9:"
  aggiungi "   python3 $DIR/scripts/backup_crypto.py keygen $CHIAVE). Senza backup il database non si tocca."
  rm -f .agg-lock-nuovo .agg-modifiche.txt
else
  MODIFICHE="$(cat .agg-modifiche.txt 2>/dev/null)"
  rm -f .agg-modifiche.txt .agg-lock-prima
  [[ -f requirements.auto.lock ]] && cp requirements.auto.lock .agg-lock-prima
  DB_EFF="$DB_ATT"           # il PostgreSQL che gira, man mano
  ROLLBACK_TAG="tdbooking-suite:rollback-$STAMP"
  CUR_ID=$(docker image inspect -f '{{.Id}}' "$APP_IMG" 2>/dev/null || true)
  APP_OK=0
  MIGRA=0

  annulla_file() {   # codice, librerie e override come prima dell'aggiornamento
    if [[ -f .agg-lock-prima ]]; then mv .agg-lock-prima requirements.auto.lock; else rm -f requirements.auto.lock; fi
    scrivi_override "$BASE_ATT" "$DB_EFF" "$LOCK_ATT"
    [[ -n "$CUR_ID" ]] && docker tag "$CUR_ID" "$APP_IMG"
  }

  # ── build, mentre il portale gira con l'immagine di prima ──
  if [[ $DA_RICOSTRUIRE == 1 ]]; then
    log "ricostruisco l'immagine per: $(IFS=', '; echo "${MOTIVI[*]}")"
    [[ -n "$CUR_ID" ]] && docker tag "$CUR_ID" "$ROLLBACK_TAG" && log "immagine attuale salvata come $ROLLBACK_TAG"
    [[ -s .agg-lock-nuovo ]] && mv .agg-lock-nuovo requirements.auto.lock
    LOCK_USO="requirements.lock"; [[ -s requirements.auto.lock ]] && LOCK_USO="requirements.auto.lock"
    scrivi_override "$BASE_NUOVA" "$DB_ATT" "$LOCK_USO"
    if docker compose build --pull --no-cache web > logs/build.log 2>&1; then
      APP_OK=1
      docker compose --profile tools run --rm migrate python manage.py migrate --plan > logs/migrate-piano.log 2>&1
      grep -q "No planned migration operations" logs/migrate-piano.log || MIGRA=1
      log "build ok$( [[ $MIGRA == 1 ]] && echo ', con migrazioni del database')"
    else
      log "❌ build non riuscita (logs/build.log): il portale che gira non è stato toccato"
      annulla_file
      peggiora errore
      aggiungi "❌ TD Booking Suite: la build della versione nuova non è riuscita. Il portale che gira non è stato toccato."
      aggiungi "   Ultime righe di logs/build.log:"
      aggiungi "$(grep -v '^[[:space:]]*$' logs/build.log | tail -n 12 | sed 's/^/   /')"
    fi
  fi
  rm -f .agg-lock-nuovo

  if [[ $APP_OK == 1 || $DB_CAMBIATA == 1 ]]; then
    # ── fermo, backup ──
    docker compose stop web worker > /dev/null 2>&1
    if ! backup_db; then
      log "❌ backup non riuscito (logs/backup.log): non aggiorno niente, riparto come prima"
      [[ $APP_OK == 1 ]] && annulla_file
      docker compose start web worker > /dev/null 2>&1   # gli stessi container di prima
      peggiora errore
      aggiungi "❌ Niente aggiornato: il backup del database prima dell'aggiornamento non è riuscito."
      aggiungi "$(tail -n 6 logs/backup.log 2>/dev/null | sed 's/^/   /')"
      APP_OK=0; DB_CAMBIATA=0
    else
      log "backup cifrato: $DUMP.enc"
    fi
  fi

  # ── PostgreSQL ──
  if [[ $DB_CAMBIATA == 1 ]]; then
    # l'immagine di prima resta con un nome: la pulizia di Docker non la toglie
    docker tag "$DB_ATT" "tdbooking-rollback/postgres:$STAMP" > /dev/null 2>&1 || true
    scrivi_override "$( [[ $APP_OK == 1 ]] && echo "$BASE_NUOVA" || echo "$BASE_ATT")" "$DB_NUOVA" "$( [[ $APP_OK == 1 ]] && echo "$LOCK_USO" || echo "$LOCK_ATT")"
    log "PostgreSQL: riavvio con ${DB_NUOVA%@*} nuovo…"
    docker compose up -d db > logs/db.log 2>&1
    if db_risponde; then
      DB_EFF="$DB_NUOVA"
    else
      log "❌ PostgreSQL nuovo non risponde: torno a quello di prima"
      scrivi_override "$( [[ $APP_OK == 1 ]] && echo "$BASE_NUOVA" || echo "$BASE_ATT")" "$DB_ATT" "$( [[ $APP_OK == 1 ]] && echo "$LOCK_USO" || echo "$LOCK_ATT")"
      docker compose up -d db >> logs/db.log 2>&1
      db_risponde || log "⚠⚠ PostgreSQL non risponde nemmeno con l'immagine di prima: controllare a mano (docker compose logs db)"
      peggiora rollback
      aggiungi "⚠ PostgreSQL: l'immagine nuova non partiva. È tornato a quella di prima."
      DB_CAMBIATA=0
      if [[ $APP_OK == 0 ]]; then   # non c'è altro da aggiornare: riparte il portale di prima
        docker compose start web worker > /dev/null 2>&1
        app_risponde || log "⚠⚠ il portale non risponde: controllare a mano (docker compose logs web)"
      fi
    fi
  fi

  # ── migrazioni e riavvio ──
  if [[ $APP_OK == 1 || $DB_CAMBIATA == 1 ]]; then
    MIGRATE_OK=1
    if [[ $APP_OK == 1 ]] && ! docker compose --profile tools run --rm migrate > logs/migrate.log 2>&1; then
      MIGRATE_OK=0
    fi
    [[ $MIGRATE_OK == 1 ]] && docker compose up -d --remove-orphans web worker > logs/avvio.log 2>&1
    if [[ $MIGRATE_OK == 1 ]] && prova_tutto; then
      log "✅ aggiornato: la versione nuova risponde e la prova automatica è passata"
      printf '%s\n' "$PROVA" > "logs/selftest-$STAMP.log"
      [[ $APP_OK == 1 ]] && echo "$FIRMA_ORA" > .agg-ultima-build
      rm -f .agg-lock-prima
      peggiora ok
      DOPO=$(printf '%s\n' "$PROVA" | tail -1 | sed -n 's/.*"versioni": \({.*}\)}$/\1/p')
      [[ -z "$DOPO" ]] && DOPO=$(versioni_app)
      COSA=()
      [[ $APP_OK == 1 ]] && COSA+=("${MOTIVI[@]}")
      [[ $DB_CAMBIATA == 1 ]] && COSA+=("PostgreSQL")
      aggiungi "✅ Aggiornato ($(IFS=', '; echo "${COSA[*]}")), prova automatica passata:"
      CAMBI=$(python3 -c '
import json, sys
a, b = json.loads(sys.argv[1] or "{}"), json.loads(sys.argv[2] or "{}")
for k in b:
    if a.get(k) != b.get(k):
        print(f"   {k}: {a.get(k) or chr(8212)} → {b.get(k)}")
' "$PRIMA" "$DOPO" 2>/dev/null)
      [[ -n "$CAMBI" ]] && aggiungi "$CAMBI"
      [[ -n "$MODIFICHE" && $APP_OK == 1 ]] && { aggiungi "   Tutte le librerie cambiate:"; aggiungi "$(printf '%s\n' "$MODIFICHE" | sed 's/^/   /')"; }
      [[ "$DEB_N" -gt 0 && $APP_OK == 1 ]] && aggiungi "   + $DEB_N pacchetti Debian aggiornati"
      aggiungi "   Backup del database fatto prima: $DUMP.enc"
    else
      # ── ROLLBACK ──
      MOTIVO="$( [[ $MIGRATE_OK == 0 ]] && echo 'le migrazioni del database non sono riuscite (logs/migrate.log)' || echo 'la versione nuova non passa la prova')"
      log "❌ $MOTIVO: ROLLBACK"
      printf '%s\n' "$PROVA" > "logs/selftest-$STAMP.log"
      ERR=$(docker compose logs --tail 20 web 2>&1 | tail -20)
      docker compose stop web worker > /dev/null 2>&1
      if [[ $APP_OK == 1 ]]; then annulla_file; fi
      if [[ $DB_CAMBIATA == 1 ]]; then
        DB_EFF="$DB_ATT"
        scrivi_override "$BASE_ATT" "$DB_ATT" "$LOCK_ATT"
        docker compose up -d db >> logs/db.log 2>&1
        db_risponde
      fi
      if [[ $MIGRA == 1 || $MIGRATE_OK == 0 ]]; then
        log "rimetto il database com'era prima dell'aggiornamento"
        if ! ripristina_db > logs/ripristino.log 2>&1; then
          log "⚠⚠ ripristino del database non riuscito (logs/ripristino.log): resta quello aggiornato, controllare a mano"
          peggiora errore
          aggiungi "❌ Il database non è stato rimesso com'era: controllare a mano. Backup: $DUMP.enc"
        fi
      fi
      docker compose --profile tools run --rm migrate >> logs/ripristino.log 2>&1
      docker compose up -d --remove-orphans web worker >> logs/ripristino.log 2>&1
      if app_risponde; then log "tornato alla versione di prima e risponde"
      else log "⚠⚠ il portale non risponde nemmeno con la versione di prima: controllare a mano (docker compose logs web)"; fi
      peggiora rollback
      aggiungi "⚠ La versione nuova non funzionava: $MOTIVO. È tornato tutto com'era, funziona come ieri."
      [[ -n "$PROVA" ]] && { aggiungi "   Prove non passate:"; aggiungi "$(printf '%s\n' "$PROVA" | grep '^ERRORE' | sed 's/^/   /')"; }
      [[ -n "$ERR" ]] && { aggiungi "   Ultime righe del portale:"; aggiungi "$(printf '%s\n' "$ERR" | sed 's/^/   /')"; }
      [[ -n "$MODIFICHE" ]] && { aggiungi "   Librerie che erano state provate:"; aggiungi "$(printf '%s\n' "$MODIFICHE" | sed 's/^/   /')"; }
      aggiungi "   Domani riprova da solo; se il problema resta, una libreria va bloccata in requirements.in."
      aggiungi "   Backup del database fatto prima: $DUMP.enc"
    fi
  fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# Pulizia (senza `prune -a`: le immagini di rollback restano)
# ═════════════════════════════════════════════════════════════════════════════
if [[ $DRY_RUN == 0 ]]; then
  for repo in tdbooking-suite:rollback- tdbooking-rollback/postgres:; do
    vecchie=$(docker image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep "^$repo" | sort -r | tail -n +$((TIENI + 1)))
    [[ -n "$vecchie" ]] && echo "$vecchie" | xargs -r docker rmi > /dev/null 2>&1
  done
  docker image prune -f > /dev/null 2>&1 || true
  docker builder prune -f --filter until=168h > /dev/null 2>&1 || true
  find "$BACKUP_DIR" -maxdepth 1 -name 'pre-aggiornamento-*.enc' -mtime +30 -delete 2>/dev/null || true
fi

# ═════════════════════════════════════════════════════════════════════════════
# Email di riepilogo (la manda il portale)
# ═════════════════════════════════════════════════════════════════════════════
DURATA=$(( $(date +%s) - START_TS ))
log "esito: $ESITO (${DURATA}s)"
if [[ $DRY_RUN == 0 && ( "$ESITO" != "nessuno" || "$MAIL_SEMPRE" == "1" ) ]]; then
  [[ "$ESITO" == "nessuno" ]] && aggiungi "Era già tutto all'ultima versione."
  aggiungi ""
  aggiungi "Durata: $((DURATA / 60)) min $((DURATA % 60)) s · log sull'host: $DIR/logs/aggiorna-tutto.log"
  ARGS=(python manage.py update_report --esito "$ESITO"); [[ -n "$EMAIL" ]] && ARGS+=(--a "$EMAIL")
  RISP=$(printf '%s' "$RIEPILOGO" | docker compose exec -T web "${ARGS[@]}" 2>/dev/null | tail -1)
  [[ "$RISP" == \{* ]] || RISP=$(printf '%s' "$RIEPILOGO" | docker compose run --rm -T web "${ARGS[@]}" 2>/dev/null | tail -1)
  A=$(printf '%s' "$RISP" | sed -n 's/.*"a": *"\([^"]*\)".*/\1/p')
  if [[ "$RISP" =~ \"ok\":\ *true ]]; then
    log "email di riepilogo inviata a ${A:-?}"
  else
    if [[ "$RISP" == \{* ]]; then
      log "⚠ email di riepilogo non inviata${A:+ a $A}: $(printf '%s' "$RISP" | sed -n 's/.*"msg": *"\([^"]*\)".*/\1/p')"
    else
      log "⚠ email di riepilogo non inviata (il portale non ha risposto)"
    fi
    log "il riepilogo che avrebbe mandato:"; printf '%s' "$RIEPILOGO" | sed 's/^/      /'
  fi
fi
log "── fine ──"
[[ "$ESITO" == "errore" || "$ESITO" == "rollback" ]] && exit 1
exit 0
