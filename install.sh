#!/usr/bin/env bash
# ApplyPilot installer for macOS / Linux (no Docker needed)
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "Python 3.10+ is required: https://www.python.org/downloads/"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo "Python 3.10+ is required"; exit 1; }
echo "→ Creating virtual environment (.venv)"
"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
echo "→ Installing Python packages"
.venv/bin/pip install -r requirements.txt -q
echo "→ Installing Chromium for Playwright"
.venv/bin/playwright install chromium
if [ "$(uname)" = "Linux" ]; then .venv/bin/playwright install-deps chromium 2>/dev/null || echo "  (if Chromium fails to start, run: sudo .venv/bin/playwright install-deps chromium)"; fi
[ -f .env ] || cp .env.example .env
mkdir -p data
echo "✓ Installed. Start with: ./start.sh   then open http://localhost:8077"
