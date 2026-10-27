#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# installa-aggiornamento.sh — pianifica scripts/aggiorna-tutto.sh ogni notte (come il CRM)
#
#   bash scripts/installa-aggiornamento.sh            → ogni notte alle 04:00
#   bash scripts/installa-aggiornamento.sh 3          → ogni notte alle 03:00
#   bash scripts/installa-aggiornamento.sh --status   → se è attivo, quando gira e l'ultimo esito
#   bash scripts/installa-aggiornamento.sh --remove   → toglie la pianificazione
#
# Usa un TIMER SYSTEMD (c'è anche nei container LXC di Proxmox): è "Persistent", quindi se
# all'ora stabilita il server era spento l'aggiornamento parte appena si riaccende.
# Se systemd non c'è ripiega su cron. Il log finisce in ./logs/aggiorna-tutto.log
# Il backup notturno del passo 9 di INSTALLAZIONE.md parte alle 2:30: l'aggiornamento va dopo.
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
DIR="$(pwd)"
UNIT="tdbs-aggiorna-tutto"
CRON="/etc/cron.d/tdbs-aggiorna-tutto"
LOG="$DIR/logs/aggiorna-tutto.log"

has_systemd() { [[ -d /run/systemd/system ]] && command -v systemctl > /dev/null 2>&1; }

remove_all() {
  if has_systemd; then
    systemctl disable --now "$UNIT.timer" > /dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$UNIT.service" "/etc/systemd/system/$UNIT.timer"
    systemctl daemon-reload || true
  fi
  rm -f "$CRON"
}

status() {
  echo "── Aggiornamento automatico di TD Booking Suite ──"
  if has_systemd && [[ -f "/etc/systemd/system/$UNIT.timer" ]]; then
    echo "Metodo: timer systemd ($(systemctl is-enabled "$UNIT.timer" 2>/dev/null || echo '?'))"
    systemctl list-timers "$UNIT.timer" --no-pager 2>/dev/null | sed -n '1,2p'
  elif [[ -f "$CRON" ]]; then
    echo "Metodo: cron"; cat "$CRON"
  else
    echo "⚠ NON pianificato. Lancia: bash scripts/installa-aggiornamento.sh"
  fi
  local key; key=$(grep -E '^[[:space:]]*TDBS_AGG_CHIAVE=' "$DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r'); key="${key:-/root/tdbs-backup.key}"
  [[ -f "$key" ]] || echo "⚠ Manca la chiave dei backup $key: senza, l'aggiornamento non tocca niente (INSTALLAZIONE.md, passo 9)"
  [[ -d "$DIR/.git" ]] || echo "⚠ Installazione senza git: le versioni nuove di TD Booking Suite non arrivano da sole (INSTALLAZIONE.md, passo 13)"
  echo
  if [[ -s "$LOG" ]]; then
    echo "Ultime righe del log ($LOG):"
    tail -n 12 "$LOG"
  else
    echo "Log vuoto: l'aggiornamento automatico non è ancora partito."
  fi
}

case "${1:-}" in
  --remove) remove_all; echo "Pianificazione rimossa."; exit 0 ;;
  --status) status; exit 0 ;;
esac

if [[ $EUID -ne 0 ]]; then echo "Serve root."; exit 1; fi
HOUR="${1:-4}"
if ! [[ "$HOUR" =~ ^[0-9]{1,2}$ ]] || (( HOUR > 23 )); then echo "Ora non valida: $HOUR (0-23)"; exit 2; fi
HH=$(printf '%02d' "$HOUR")
mkdir -p "$DIR/logs"
chmod +x "$DIR/scripts/aggiorna-tutto.sh"

remove_all

if has_systemd; then
  cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=TD Booking Suite: aggiornamento automatico di tutto
Wants=network-online.target
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=$DIR
# docker compose vuole HOME per la sua configurazione; PATH esplicito per curl/flock/docker
Environment=HOME=/root
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/bin/bash -c 'bash "$DIR/scripts/aggiorna-tutto.sh" >> "$LOG" 2>&1'
TimeoutStartSec=3h
EOF
  cat > "/etc/systemd/system/$UNIT.timer" <<EOF
[Unit]
Description=TD Booking Suite: aggiornamento automatico ogni notte alle $HH:00

[Timer]
# ora italiana anche se il CT è in UTC (cambio ora legale/solare compreso)
OnCalendar=*-*-* $HH:00:00 Europe/Rome
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$UNIT.timer" > /dev/null
  echo "✅ Timer systemd installato: ogni notte alle $HH:00"
  systemctl list-timers "$UNIT.timer" --no-pager | sed -n '1,2p'
else
  { echo "CRON_TZ=Europe/Rome"; echo "0 $HOUR * * * root cd $DIR && /usr/bin/flock -n /tmp/tdbs-aggiorna-tutto.cron bash scripts/aggiorna-tutto.sh >> $LOG 2>&1"; } > "$CRON"
  echo "✅ Cron installato: ogni notte alle $HH:00"
  if ! pgrep -x cron > /dev/null 2>&1 && ! pgrep -x crond > /dev/null 2>&1; then
    echo "⚠ Il servizio cron NON è in esecuzione: avvialo con  systemctl enable --now cron"
  fi
fi

echo
echo "Stato:    bash scripts/installa-aggiornamento.sh --status"
echo "Log:      tail -f $LOG"
echo "Prova:    bash scripts/aggiorna-tutto.sh --dry-run"
echo "Adesso:   bash scripts/aggiorna-tutto.sh"
echo "Rimuovi:  bash scripts/installa-aggiornamento.sh --remove"
