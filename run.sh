#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv; .venv/bin/pip install -r requirements.txt; .venv/bin/playwright install --with-deps chromium || .venv/bin/playwright install chromium; }
[ -f .env ] && set -a && . ./.env && set +a
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8077}"
