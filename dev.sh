#!/usr/bin/env bash
#
# Starts CanvasFlow — backend and frontend together, on ports that are actually free.
#
# Exists because two things bite otherwise: running the system `uvicorn` instead of the
# venv's (ModuleNotFoundError on a dependency that is definitely installed), and Next
# quietly landing on a port where some other project is already serving.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

find_port() {
  local port=$1
  while ! port_free "$port"; do
    port=$((port + 1))
    if [ "$port" -gt $((${1} + 40)) ]; then
      echo "no free port near $1" >&2
      exit 1
    fi
  done
  echo "$port"
}

# --- backend ------------------------------------------------------------------------

if [ ! -x "$BACKEND/.venv/bin/uvicorn" ]; then
  echo "Setting up the Python environment (first run only)…"
  python3 -m venv "$BACKEND/.venv"
  "$BACKEND/.venv/bin/pip" install -q --upgrade pip
  "$BACKEND/.venv/bin/pip" install -q -r "$BACKEND/requirements.txt"
fi

API_PORT="$(find_port 8000)"
[ "$API_PORT" != "8000" ] && echo "note: port 8000 was busy, using $API_PORT"

# .venv/bin/uvicorn, never plain `uvicorn` — the system one can't see these deps.
( cd "$BACKEND" && exec "$BACKEND/.venv/bin/uvicorn" main:app --reload --port "$API_PORT" ) &
API_PID=$!

# --- frontend -----------------------------------------------------------------------

if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "Installing frontend dependencies (first run only)…"
  ( cd "$FRONTEND" && npm install )
fi

WEB_PORT="$(find_port 3000)"
[ "$WEB_PORT" != "3000" ] && echo "note: port 3000 was busy, using $WEB_PORT"

( cd "$FRONTEND" && NEXT_PUBLIC_CANVASFLOW_API="http://localhost:$API_PORT" \
    exec npx next dev -p "$WEB_PORT" ) &
WEB_PID=$!

cleanup() { kill "$API_PID" "$WEB_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo
echo "  CanvasFlow"
echo "  ───────────────────────────────────────"
echo "  Canvas    http://localhost:$WEB_PORT"
echo "  API       http://localhost:$API_PORT"
echo
echo "  Ctrl-C to stop both."
echo

wait
