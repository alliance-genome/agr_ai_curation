"""The ``removed`` file status migration (KANBAN-1976 / ALL-1460, P10b).

It only widens ``ck_pdf_document_files_status``: before it, ``removed`` is refused;
after it, it is allowed and every other value is still refused. It refuses downgrade.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from src.models.sql.database import SessionLocal

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREVIOUS_HEAD = "b1460f8e9a0c"
REVISION = "d1460fa1b2c3"


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


def _status_check() -> str:
    with SessionLocal() as db:
        return db.scalar(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_pdf_document_files_status'"
        ))


def test_the_migration_widens_the_status_check_and_refuses_downgrade(historical_migration_database):
    command.upgrade(_config(), PREVIOUS_HEAD)
    before = _status_check()
    assert "'removed'" not in before and "'failed'" in before

    command.upgrade(_config(), REVISION)
    after = _status_check()
    for value in ("pending", "ready", "failed", "removed"):
        assert f"'{value}'" in after
    with SessionLocal() as db:
        convalidated = db.scalar(text(
            "SELECT convalidated FROM pg_constraint WHERE conname = 'ck_pdf_document_files_status'"
        ))
    assert convalidated is True  # checked against every existing row, not NOT VALID

    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(_config(), PREVIOUS_HEAD)
