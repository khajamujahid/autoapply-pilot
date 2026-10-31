#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || bash install.sh
PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d= -f2); PORT=${PORT:-8077}
echo "ApplyPilot → http://localhost:$PORT"
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
