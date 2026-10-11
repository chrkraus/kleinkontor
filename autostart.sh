#!/usr/bin/env bash
# Startet die App, falls sie nicht läuft (für cron: beim Booten und alle 5 Minuten als Wächter).
# flock verhindert, dass zwei Instanzen aus diesem Ordner gleichzeitig laufen.
cd "$(dirname "$0")"
mkdir -p data
export HOST="${HOST:-0.0.0.0}" PORT="${PORT:-5000}"
exec flock -n data/.run.lock ./run.sh >> data/server.log 2>&1
