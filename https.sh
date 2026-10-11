#!/usr/bin/env bash
# Startet Caddy (HTTPS im Heimnetz) – für cron: beim Booten und alle 5 Minuten als Wächter.
cd "$(dirname "$0")"
[ -s data/tls/fullchain.pem ] || exit 0
export BUCHHALTUNG_DIR="$PWD"
exec flock -n data/.https.lock bin/caddy run --config Caddyfile --adapter caddyfile >> data/caddy.log 2>&1
