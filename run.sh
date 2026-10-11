#!/usr/bin/env bash
# Startet die Buchhaltung. Einstellungen je Instanz optional in instance.env (nicht im git), z. B.:
#   BUCHHALTUNG_ENV=test  PORT=5001  HOST=127.0.0.1  BUCHHALTUNG_TEST_MAIL=test@example.de
# Standard: produktiv, Port 5000, nur dieser Rechner (autostart.sh setzt HOST=0.0.0.0).
set -euo pipefail
cd "$(dirname "$0")"
[ -f instance.env ] && set -a && . ./instance.env && set +a
if [ ! -x .venv/bin/python ]; then
  echo "Fehlende virtuelle Umgebung (.venv) – bitte einrichten." >&2; exit 1
fi
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5000}"
export BUCHHALTUNG_SCHEDULER=1     # Backup, Belegeingang, Erinnerungen (in der Testumgebung automatisch aus)
mkdir -p data
echo $$ > data/app.pid             # exec behält die PID → gezielter Neustart dieser Instanz
echo "$(date '+%F %T') Buchhaltung (${BUCHHALTUNG_ENV:-prod}) startet auf http://${HOST}:${PORT}"
# Proxy-Header (X-Forwarded-*) nicht von waitress löschen lassen – security.TrustLocalProxy
# übernimmt sie ausschließlich von 127.0.0.1 (Caddy, cloudflared) und ignoriert sie sonst.
exec .venv/bin/waitress-serve --host="$HOST" --port="$PORT" --no-clear-untrusted-proxy-headers app:app
