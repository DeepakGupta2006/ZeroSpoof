#!/usr/bin/env bash
# One-command local dev (macOS / Linux): inference API on :8000 + web app on :3000.
# Uses SQLite and the in-memory store, so no Redis/Postgres/Go needed.   Usage: bash scripts/dev.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT/inference"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
pip install -q -r requirements.txt
uvicorn zerospoof.main:app --port 8000 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null || true' EXIT

cd "$ROOT/web"
[ -f .env.local ] || cp .env.example .env.local
[ -d node_modules ] || npm install --no-audit --no-fund
echo "Demo: http://localhost:3000   Dashboard: http://localhost:3000/dashboard   API docs: http://localhost:8000/docs"
npm run dev
