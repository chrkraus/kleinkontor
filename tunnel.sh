#!/usr/bin/env bash
# Startet den Cloudflare-Tunnel (für cron: beim Booten und alle 5 Minuten als Wächter).
# flock verhindert doppelte Instanzen. Der Token liegt in data/cloudflared.token (nur für diesen Benutzer lesbar).
cd "$(dirname "$0")"
[ -s data/cloudflared.token ] || exit 0
exec flock -n data/.tunnel.lock bin/cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:20241 \
     run --token-file data/cloudflared.token >> data/tunnel.log 2>&1
