#!/usr/bin/env python3
"""
Extract distinct identifier prefixes from the curation database and store them in JSON.

This script is intended to be run on-demand (not at runtime) to refresh the set of
allowed CURIE prefixes for validation. It supports two ways to get DB credentials:
  1) --database-url (a full Postgres connection string)
  2) --secret-name (an AWS Secrets Manager secret containing the DB creds/URL)

Example usage:
    # Using DATABASE_URL env var (psql URI)
    DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<database> \
      ./scripts/extract_identifier_prefixes.py \
      --outfile backend/config/identifier_prefixes.json \
      --query \"SELECT DISTINCT split_part(identifier, ':', 1) AS prefix FROM identifiers WHERE identifier LIKE '%:%';\"

    # Using AWS secret (must contain a \"DATABASE_URL\" field or a full URI)
    ./scripts/extract_identifier_prefixes.py \
      --secret-name my/curation/db/secret \
      --aws-region us-east-1 \
      --outfile backend/config/identifier_prefixes.json \
      --query \"SELECT DISTINCT split_part(identifier, ':', 1) AS prefix FROM identifiers WHERE identifier LIKE '%:%';\"

Notes:
- You MUST provide at least one SQL query via --query (can be repeated). The first column
  of each query result is treated as the prefix.
- This script does not run in production; it’s a maintenance helper.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Set, Optional

import psycopg2
import psycopg2.extras

try:
    import boto3  # type: ignore
except ImportError:
    boto3 = None


def load_db_url_from_secret(secret_name: str, region: str) -> str:
    if boto3 is None:
        raise RuntimeError("boto3 is not installed; cannot fetch secret from AWS")
    client = boto3.client("secretsmanager", region_name=region)
    resp = client.get_secret_value(SecretId=secret_name)
    secret_str = resp.get("SecretString")
    if not secret_str:
        raise RuntimeError(f"Secret {secret_name} has no SecretString")
    try:
        data = json.loads(secret_str)
    except json.JSONDecodeError:
        # If the secret is a raw connection string
        return secret_str

    # Common patterns: { "DATABASE_URL": "...", "host": "...", "username": "...", ... }
    if "DATABASE_URL" in data:
        return data["DATABASE_URL"]
    # Fallback: try to assemble if keys exist
    keys = ("username", "password", "host", "port", "dbname")
    if all(k in data for k in keys):
        return f"postgresql://{data['username']}:{data['password']}@{data['host']}:{data['port']}/{data['dbname']}"
    raise RuntimeError(f"Secret {secret_name} does not contain DATABASE_URL or standard fields")


def run_queries(conn_str: str, queries: List[str]) -> Set[str]:
    prefixes: Set[str] = set()
    with psycopg2.connect(conn_str) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            for q in queries:
                cur.execute(q)
                for row in cur.fetchall():
                    if not row:
                        continue
                    prefix = str(row[0]).strip()
                    if not prefix:
                        continue
                    # keep simple prefixes (letters, digits, underscore, dash)
                    if all(c.isalnum() or c in "-._" for c in prefix):
                        prefixes.add(prefix)
    return prefixes


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract distinct identifier prefixes from curation DB")
    parser.add_argument("--database-url", help="Postgres connection string (overrides env DATABASE_URL)")
    parser.add_argument("--secret-name", help="AWS Secrets Manager secret name containing DB creds/URL")
    parser.add_argument("--aws-region", default=os.getenv("AWS_REGION", "us-east-1"), help="AWS region for secrets")
    parser.add_argument("--outfile", default="backend/config/identifier_prefixes.json", help="Output JSON file")
    parser.add_argument(
        "--query",
        action="append",
        help="SQL query that returns prefix in first column. Can be specified multiple times.",
    )

    args = parser.parse_args()

    if not args.query:
        print("ERROR: At least one --query is required.", file=sys.stderr)
        return 1

    conn_str: Optional[str] = None
    if args.database_url:
        conn_str = args.database_url
    elif args.secret_name:
        conn_str = load_db_url_from_secret(args.secret_name, args.aws_region)
    else:
        conn_str = os.getenv("DATABASE_URL")

    if not conn_str:
        print("ERROR: No database connection string provided (use --database-url, --secret-name, or DATABASE_URL).", file=sys.stderr)
        return 1

    print(f"Using DB connection: {conn_str[:6]}... (redacted)")
    print(f"Running {len(args.query)} queries to collect prefixes...")
    prefixes = run_queries(conn_str, args.query)

    print(f"Collected {len(prefixes)} distinct prefixes")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prefixes": sorted(prefixes),
        "source": {
            "secret_name": args.secret_name,
            "aws_region": args.aws_region,
            "queries": args.query,
        },
    }

    out_path = os.path.abspath(args.outfile)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"✅ Wrote prefixes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
