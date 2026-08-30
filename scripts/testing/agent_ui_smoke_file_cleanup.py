#!/usr/bin/env python3
"""Delete one validated local Midscene smoke file row and storage object."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from uuid import UUID

from src.lib.file_outputs.storage import FileOutputStorageService
from src.models.sql.database import SessionLocal
from src.models.sql.file_output import FileOutput


PREFIX_PATTERN = re.compile(r"^agent-smoke-[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file-id", type=UUID, required=True)
    parser.add_argument("--run-prefix", required=True)
    parser.add_argument("--filename", required=True)
    args = parser.parse_args()
    if not PREFIX_PATTERN.fullmatch(args.run_prefix) or len(args.run_prefix) > 54:
        parser.error("run prefix is not a canonical agent-smoke prefix")
    if Path(args.filename).name != args.filename or not args.filename.startswith(
        f"{args.run_prefix}-"
    ):
        parser.error("filename is not owned by the exact smoke run prefix")
    return args


def main() -> None:
    args = parse_args()
    storage = FileOutputStorageService()
    base_path = storage.base_path.resolve()
    database_absent = False
    storage_absent = False
    db = SessionLocal()
    try:
        row = db.query(FileOutput).filter(FileOutput.id == args.file_id).one_or_none()
        if row is None:
            database_absent = True
            storage_absent = True
        else:
            file_path = Path(row.file_path).resolve()
            if row.filename != args.filename:
                raise RuntimeError("file row does not match the captured filename")
            if file_path.name != row.filename or not file_path.is_relative_to(base_path):
                raise RuntimeError("file row path is outside the configured storage root")
            if file_path.exists() and not storage.delete_output(str(file_path)):
                raise RuntimeError("storage service did not delete the recorded file")
            db.delete(row)
            db.commit()
            db.expire_all()
            database_absent = (
                db.query(FileOutput).filter(FileOutput.id == args.file_id).one_or_none()
                is None
            )
            storage_absent = not file_path.exists()
        if not database_absent or not storage_absent:
            raise RuntimeError("file cleanup could not verify database and storage absence")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(
        json.dumps(
            {
                "file_id": str(args.file_id),
                "database_absent": database_absent,
                "storage_absent": storage_absent,
            }
        )
    )


if __name__ == "__main__":
    main()
