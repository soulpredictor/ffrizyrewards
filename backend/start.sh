#!/usr/bin/env bash
set -euo pipefail

CLOUDFLARED_BIN="./cloudflared"
if [ ! -x "$CLOUDFLARED_BIN" ]; then
  CLOUDFLARED_BIN="cloudflared"
fi

"$CLOUDFLARED_BIN" tunnel run pterodactyl2 &
CF_PID=$!
trap 'kill "$CF_PID" 2>/dev/null || true' EXIT INT TERM

export PORT="${PORT:-4636}"

python3 app.py
