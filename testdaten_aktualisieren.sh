#!/usr/bin/env bash
# Kopiert die produktiven Daten in die Testumgebung und entschärft sie (nur in der Testumgebung ausführbar).
set -euo pipefail
cd "$(dirname "$0")"
[ -f instance.env ] && set -a && . ./instance.env && set +a
[ "${BUCHHALTUNG_ENV:-prod}" = "test" ] || { echo "Nur in der Testumgebung erlaubt (BUCHHALTUNG_ENV=test in instance.env)." >&2; exit 1; }
# Quelle: Argument, PROD_DATA aus instance.env oder der Nachbarordner ohne „-test“
PROD="${1:-${PROD_DATA:-../$(basename "$PWD" -test)/data}}"
export TEST_APP_URL="${TEST_APP_URL:-}"
mkdir -p data
.venv/bin/python - "$PROD" <<'PY'
import os, shutil, sqlite3, sys
prod = sys.argv[1]
src = sqlite3.connect(f"file:{prod}/buchhaltung.db?mode=ro", uri=True)
tmp = "data/buchhaltung.db.neu"
dst = sqlite3.connect(tmp)
with dst:
    src.backup(dst)
src.close()
logo = ""
for name in os.listdir(prod):
    if name.startswith("logo."):
        shutil.copy2(os.path.join(prod, name), os.path.join("data", name))
        logo = os.path.abspath(os.path.join("data", name))
# Entschärfen: nichts darf in der Testumgebung nach außen wirken
dst.execute("""UPDATE settings SET app_url=?, reminder_auto=0, paperless_archive_invoices=0,
               paperless_inbox_enabled=0, backup_dir='', last_backup_at=NULL, last_backup_file='', last_backup_error='',
               logo_path=?""", (os.environ.get("TEST_APP_URL", ""), logo))
dst.commit(); dst.close()
os.replace(tmp, "data/buchhaltung.db")
print("Testdaten aus", prod, "übernommen und entschärft.")
PY
./restart.sh
