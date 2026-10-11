#!/usr/bin/env bash
# Verschlüsselt die lokalen Sicherungen (AES-256, Kennwort in data/backup-verschluesselung.key)
# und spiegelt sie per rsync auf ein NAS – nur in den Unterordner NAS_TARGET (aus instance.env).
# Für cron (täglich 04:40); läuft nur in der Produktivumgebung und nur, wenn beide Kennwort-Dateien existieren.
set -uo pipefail
cd "$(dirname "$0")"
[ -f instance.env ] && set -a && . ./instance.env && set +a
[ "${BUCHHALTUNG_ENV:-prod}" = "prod" ] || exit 0
[ -s data/nas-rsync.pass ] && [ -s data/backup-verschluesselung.key ] || exit 0
# Ziel aus instance.env, z. B. NAS_TARGET=rsync://backup@nas.local/backup/buchhaltung/
[ -n "${NAS_TARGET:-}" ] || exit 0
STATUS=data/nas-sync.status
umask 077
mkdir -p data/backups-nas
exec 9> data/.nas-sync.lock; flock -n 9 || exit 0

fail() { echo "$(date '+%Y-%m-%dT%H:%M:%S') FEHLER $*" > "$STATUS"; echo "FEHLER: $*" >&2; exit 1; }

# 1) Fehlende verschlüsselte Kopien erzeugen
for f in data/backups/buchhaltung-*.zip; do
  [ -e "$f" ] || continue
  out="data/backups-nas/$(basename "$f").enc"
  [ -s "$out" ] && continue
  openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt -pass file:data/backup-verschluesselung.key \
      -in "$f" -out "$out.tmp" && mv "$out.tmp" "$out" || fail "Verschlüsselung von $(basename "$f")"
done
# 2) Verschlüsselte Kopien entfernen, deren lokale Sicherung rotiert wurde
for e in data/backups-nas/*.enc; do
  [ -e "$e" ] || continue
  [ -e "data/backups/$(basename "$e" .enc)" ] || rm -f "$e"
done
cat > data/backups-nas/ENTSCHLUESSELN.txt <<'TXT'
Verschlüsselte Sicherungen der Buchhaltung (AES-256-CBC, PBKDF2 mit 600000 Iterationen).
Entschlüsseln (Linux/macOS, Kennwort wird abgefragt):
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -in buchhaltung-JJJJ-MM-TT_HHMMSS.zip.enc -out sicherung.zip
Danach sicherung.zip entpacken und buchhaltung.db in den Ordner data/ der App kopieren (App vorher stoppen).
TXT
# 3) Spiegeln – --delete wirkt nur innerhalb von NAS_TARGET
n=$(ls data/backups-nas/*.enc 2>/dev/null | wc -l)
[ "$n" -gt 0 ] || fail "keine Sicherungen vorhanden"
out=$(rsync -rt --delete --timeout=60 --password-file=data/nas-rsync.pass data/backups-nas/ "$NAS_TARGET" 2>&1) \
  || fail "Übertragung: $(echo "$out" | tail -1)"
echo "$(date '+%Y-%m-%dT%H:%M:%S') OK $n" > "$STATUS"
