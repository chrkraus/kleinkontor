#!/usr/bin/env bash
# Setzt die Produktion auf die Version vor dem letzten deploy.sh zurück.
# Die Datenbank bleibt unverändert (neue Spalten stören ältere Versionen nicht). Bei Bedarf eine
# Sicherung aus data/backups zurückspielen (siehe README).
set -euo pipefail
cd "$(dirname "$0")"
[ -s data/vorherige_version ] || { echo "Keine vorherige Version bekannt." >&2; exit 1; }
PREV=$(cat data/vorherige_version)
echo "Zurück zu: $(git log --oneline -1 "$PREV")"
git reset -q --hard "$PREV"
.venv/bin/pip install -q -r requirements.txt
./restart.sh && rm -f data/vorherige_version
