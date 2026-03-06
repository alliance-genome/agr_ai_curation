#!/usr/bin/env bash
set -euo pipefail

# Best-effort local DB bootstrap for developer/Symphony workspaces.
# This avoids startup failures when a reused local Postgres volume exists
# without the expected application database.

python - <<'PY'
import os
import sys
from urllib.parse import unquote, urlparse

import psycopg2
from psycopg2 import sql


def log(msg: str) -> None:
    print(f"[db-bootstrap] {msg}")


db_url = os.getenv("DATABASE_URL", "").strip()
if not db_url:
    log("DATABASE_URL is not set; skipping auto-create.")
    sys.exit(0)

parsed = urlparse(db_url)
target_db = (parsed.path or "").lstrip("/")
if not target_db:
    log("DATABASE_URL has no database name; skipping auto-create.")
    sys.exit(0)

host = (parsed.hostname or "").strip()
local_hosts = {"postgres", "localhost", "127.0.0.1"}
is_local_host = host in local_hosts or host.endswith("-postgres-1")
if not is_local_host:
    log(f"Host '{host or '<empty>'}' is not local compose Postgres; skipping auto-create.")
    sys.exit(0)

conn_kwargs = {
    "user": unquote(parsed.username or "postgres"),
    "password": unquote(parsed.password or ""),
    "host": host or "postgres",
    "port": parsed.port or 5432,
    "connect_timeout": 5,
}

try:
    with psycopg2.connect(dbname=target_db, **conn_kwargs):
        log(f"Database '{target_db}' already exists.")
        sys.exit(0)
except psycopg2.OperationalError as exc:
    if "does not exist" not in str(exc):
        log(f"Could not connect to target database '{target_db}': {exc}")
        raise

maintenance_db = os.getenv("POSTGRES_MAINTENANCE_DB", "postgres")
log(f"Database '{target_db}' missing; creating via maintenance db '{maintenance_db}'.")

with psycopg2.connect(dbname=maintenance_db, **conn_kwargs) as conn:
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
        exists = cur.fetchone() is not None
        if exists:
            log(f"Database '{target_db}' already exists (race-safe check).")
        else:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
            log(f"Created database '{target_db}'.")
PY
