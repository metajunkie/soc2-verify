#!/bin/bash
# Start the local SOC 2 review service for an Open WebUI assistant. On macOS, run it from Terminal so it inherits
# Terminal's file access (folders like Desktop are protected from background agents).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if [ ! -x .venv/bin/python ]; then
  echo "Creating Python environment..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q pypdf openpyxl pyyaml
fi
if [ ! -s .token ]; then
  .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))' > .token
  chmod 600 .token
fi
if [ -f service.pid ] && kill -0 "$(cat service.pid)" 2>/dev/null; then
  echo "Already running (pid $(cat service.pid))."
else
  nohup .venv/bin/python soc2_service.py >> service.log 2>&1 &
  echo $! > service.pid
  sleep 2
fi
if curl -sf -H "Authorization: Bearer $(cat .token)" http://127.0.0.1:${SOC2_PORT:-8765}/health >/dev/null; then
  echo "SOC 2 review service is up on port ${SOC2_PORT:-8765}."
else
  echo "Service did not respond; see $DIR/service.log"; exit 1
fi
