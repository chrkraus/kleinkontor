#!/usr/bin/env bash
# Startet genau diese Instanz neu (über data/app.pid) und prüft, ob sie wieder antwortet.
cd "$(dirname "$0")"
[ -f instance.env ] && set -a && . ./instance.env && set +a
PORT="${PORT:-5000}"
if [ -f data/app.pid ] && kill -0 "$(cat data/app.pid)" 2>/dev/null; then
  kill "$(cat data/app.pid)"
  for _ in $(seq 1 20); do kill -0 "$(cat data/app.pid)" 2>/dev/null || break; sleep 0.5; done
fi
nohup ./autostart.sh >/dev/null 2>&1 &
for _ in $(seq 1 30); do
  sleep 1
  if python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${PORT}/anmelden', timeout=3).status == 200 else 1)" 2>/dev/null; then
    echo "OK: Instanz auf Port ${PORT} läuft (PID $(cat data/app.pid))."; exit 0
  fi
done
echo "FEHLER: Instanz auf Port ${PORT} antwortet nicht – siehe data/server.log" >&2; exit 1
