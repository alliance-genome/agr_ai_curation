#!/usr/bin/env python3
"""Inventory or transactionally rehearse the generated flow-group repair.

Run inside the candidate backend with its intended database configuration.
Default mode is read-only. --rehearse performs the real writes and rolls back;
committed application belongs to Alembic upgrade, after drain and backups.
Output can contain private flow configuration: retain it as private evidence.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy import text
from src.models.sql.database import engine
from src.lib.flows.unbound_metadata_repair import migrate_metadata_groups


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rehearse", action="store_true")
    args = parser.parse_args()
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            if not args.rehearse:
                connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            report = migrate_metadata_groups(connection, apply=args.rehearse)
            report["committed"] = False
            report["mode"] = "rollback_rehearsal" if args.rehearse else "read_only_inventory"
            print(json.dumps(report, indent=2))
        finally:
            transaction.rollback()


if __name__ == "__main__":
    main()
