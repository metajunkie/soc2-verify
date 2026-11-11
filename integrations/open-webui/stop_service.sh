#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$DIR/service.pid" ] && kill "$(cat "$DIR/service.pid")" 2>/dev/null; then
  echo "Stopped."; rm -f "$DIR/service.pid"
else
  echo "Not running."
fi
