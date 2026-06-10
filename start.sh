#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Backend setup ─────────────────────────────────────────────────────────────
if [ ! -d "$ROOT/venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv "$ROOT/venv"
fi

source "$ROOT/venv/bin/activate"
pip install -q -r "$ROOT/requirements.txt"

# ── Frontend setup ────────────────────────────────────────────────────────────
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "Installing frontend dependencies..."
  npm install --prefix "$ROOT/frontend"
fi

# ── Start both servers ────────────────────────────────────────────────────────
echo ""
echo "Starting TriageIQ..."
echo "  Backend  → http://localhost:8001"
echo "  Frontend → http://localhost:3001"
echo ""
echo "Press Ctrl+C to stop both servers."
echo ""

trap 'kill $(jobs -p) 2>/dev/null' EXIT

cd "$ROOT"
uvicorn backend.main:app --port 8001 &
npm run dev --prefix "$ROOT/frontend" &

wait
