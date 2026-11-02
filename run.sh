#!/usr/bin/env bash
# 本地开发/测试启动（不装 Caddy，不建用户）
set -e
cd "$(dirname "$0")"
if [[ ! -d .venv ]]; then
    python3 -m venv .venv
    ./.venv/bin/pip install -q --upgrade pip
    ./.venv/bin/pip install -q -r requirements.txt
fi
exec ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
