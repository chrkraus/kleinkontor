#!/bin/sh
# Startet Kleinkontor ohne Root-Rechte.
# Läuft der Container als root (kein "user:" angegeben), übernimmt er den Besitzer
# des Datenordners – auf einer NAS also den Benutzer, der den Ordner angelegt hat –
# und gibt alle Rechte ab. PUID/PGID legen den Benutzer ausdrücklich fest.
set -e
DATA="${BUCHHALTUNG_DATA:-/data}"

if [ "$(id -u)" != "0" ]; then
    exec "$@"          # Benutzer per "user:" vorgegeben
fi

mkdir -p "$DATA"
if [ -n "$PUID" ]; then
    uid="$PUID"; gid="${PGID:-$PUID}"
else
    uid="$(stat -c %u "$DATA")"; gid="$(stat -c %g "$DATA")"
fi
if [ "$uid" = "0" ]; then
    # Ordner gehört root (z. B. von Docker selbst angelegt): eigener Benutzer 1000
    uid=1000; gid=1000
    chown -R "$uid:$gid" "$DATA"
fi

echo "Kleinkontor läuft als uid=$uid gid=$gid (Besitzer von $DATA)"
export HOME=/tmp
exec setpriv --reuid="$uid" --regid="$gid" --clear-groups --no-new-privs -- "$@"
