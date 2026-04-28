#!/usr/bin/env sh
set -eu

PORT="${1:-8010}"

if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -ti TCP:"$PORT" 2>/dev/null || true)"
elif command -v fuser >/dev/null 2>&1; then
  PIDS="$(fuser "$PORT"/tcp 2>/dev/null || true)"
else
  echo "Neither lsof nor fuser is available; cannot inspect port $PORT." >&2
  exit 1
fi

if [ -z "$PIDS" ]; then
  echo "Port $PORT is free."
  exit 0
fi

for PID in $PIDS; do
  echo "Killing process $PID using port $PORT."
  kill "$PID" 2>/dev/null || true
done

sleep 1

for PID in $PIDS; do
  if kill -0 "$PID" 2>/dev/null; then
    echo "Force killing process $PID using port $PORT."
    kill -9 "$PID" 2>/dev/null || true
  fi
done

exit 0
