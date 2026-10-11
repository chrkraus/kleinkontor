#!/usr/bin/env bash
# Spielt den getesteten Stand aus der Testumgebung in die Produktion ein (nur im Produktiv-Ordner ausführen).
# Ablauf: Prüfungen → Datensicherung → git fast-forward → Abhängigkeiten → Neustart → bei Fehler automatisch zurück.
set -euo pipefail
cd "$(dirname "$0")"
[ -f instance.env ] && set -a && . ./instance.env && set +a
[ "${BUCHHALTUNG_ENV:-prod}" = "prod" ] || { echo "deploy.sh nur in der Produktivumgebung ausführen." >&2; exit 1; }
# Quelle: Argument, DEPLOY_SOURCE aus instance.env oder der Nachbarordner <dieser Ordner>-test
SRC="${1:-${DEPLOY_SOURCE:-../$(basename "$PWD")-test}}"
if ! git -C "$SRC" diff --quiet || ! git -C "$SRC" diff --cached --quiet; then
  echo "Die Testumgebung hat nicht gespeicherte Änderungen – zuerst dort committen." >&2; exit 1; fi
if [ -n "$(git status --porcelain)" ]; then
  echo "Im Produktiv-Ordner gibt es lokale Änderungen – bitte prüfen:" >&2; git status --short >&2; exit 1; fi
OLD=$(git rev-parse HEAD)
git fetch -q "$SRC" main
NEW=$(git rev-parse FETCH_HEAD)
if [ "$OLD" = "$NEW" ]; then echo "Produktion ist bereits aktuell ($(git log --oneline -1))."; exit 0; fi
echo "Neue Änderungen:"; git log --oneline "$OLD..$NEW"
echo "Datensicherung …"
BUCHHALTUNG_SCHEDULER=0 .venv/bin/python -c "
from app import app, run_backup_job
with app.app_context(): print('  ', run_backup_job()[0])" 2>/dev/null
git merge --ff-only -q FETCH_HEAD
.venv/bin/pip install -q -r requirements.txt
if ./restart.sh; then
  echo "$OLD" > data/vorherige_version
  echo "Fertig: $(git log --oneline -1)  (zurück mit ./rollback.sh)"
else
  echo "Neustart fehlgeschlagen – setze auf $OLD zurück." >&2
  git reset -q --hard "$OLD"; ./restart.sh; exit 1
fi
