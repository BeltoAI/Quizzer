#!/usr/bin/env bash
set -euo pipefail
echo "Using Python: $(python3 --version 2>&1)"
python3 -m venv .venv >/dev/null 2>&1 || true
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip >/dev/null
pip install -q -r requirements.txt
python -m py_compile app/*.py
echo "Server ready. Opening at http://127.0.0.1:5055 ..."
uvicorn app.main:app --host 127.0.0.1 --port 5055
