#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .local/venv/bin/python ]; then
    python3 -m venv .local/venv
fi
if ! .local/venv/bin/python -c 'import numpy, psycopg' 2>/dev/null; then
    .local/venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -e .env ]; then
    cp .env.example .env
    printf '%s\n' 'Created .env from .env.example. Add your AI provider settings there.'
fi

database_status() {
    .local/venv/bin/python -c '
import os
import sys
import psycopg
from financial_twin.server import load_env
load_env()
url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit(2)
try:
    with psycopg.connect(url, connect_timeout=2):
        pass
except psycopg.Error:
    sys.exit(1 if url == "postgresql://financial_twin:financial_twin@127.0.0.1:5433/financial_twin" else 2)
'
}

status=0
database_status || status=$?
if [ "$status" -eq 1 ]; then
    if [ -d .local/postgres ] && [ -x /usr/lib/postgresql/16/bin/pg_ctl ]; then
        /usr/lib/postgresql/16/bin/pg_ctl -D .local/postgres -l .local/postgres.log -o '-p 5433 -k /tmp' start
    elif command -v docker >/dev/null 2>&1; then
        docker compose up -d db
    else
        printf '%s\n' 'PostgreSQL is not running. Install Docker with Compose or start the configured database, then retry.' >&2
        exit 1
    fi
    for attempt in {1..30}; do
        if database_status; then
            status=0
            break
        fi
        sleep 1
    done
fi
if [ "$status" -ne 0 ]; then
    printf '%s\n' 'Cannot connect to DATABASE_URL. Check the database and its settings in .env.' >&2
    exit 1
fi

exec .local/venv/bin/python -m financial_twin.server
