"""Postgres coverage for the private-review-session migration (ALL-1547)."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from src.lib.curation_workspace.saved_view_service import list_saved_views
from src.models.sql.database import SessionLocal


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREVIOUS_HEAD = "a2b3c4d5e6f7"
REVISION = "b5c6d7e8f9a0"
OWNER = "migration-saved-view-owner"


def _has_assignment_column(db) -> bool:
    return bool(db.scalar(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'curation_review_sessions' AND column_name = 'assigned_curator_id'"
    )))


def test_saved_views_with_retired_keys_load_after_the_migration(historical_migration_database):
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, PREVIOUS_HEAD)
    view_id = uuid4()
    legacy_filters = {
        "inventory_scope": "show_all",
        "curator_ids": ["someone-else"],
        "statuses": ["new"],
        "adapter_keys": ["gene"],
        "tags": [],
    }
    with SessionLocal.begin() as db:
        assert _has_assignment_column(db)
        db.execute(
            text(
                "INSERT INTO curation_saved_views "
                "(id, name, filters, sort_by, sort_direction, is_default, created_by_id) "
                "VALUES (:id, 'legacy', CAST(:filters AS jsonb), 'curator', 'asc', false, :owner)"
            ),
            {"id": view_id, "filters": json.dumps(legacy_filters), "owner": OWNER},
        )

    command.upgrade(config, REVISION)

    with SessionLocal() as db:
        assert not _has_assignment_column(db)
        stored = db.execute(
            text("SELECT filters, sort_by FROM curation_saved_views WHERE id = :id"), {"id": view_id}
        ).one()
        assert stored.filters == {"statuses": ["new"], "adapter_keys": ["gene"], "tags": []}
        assert stored.sort_by == "prepared_at"
        views = list_saved_views(db, current_user_id=OWNER).views
    assert [view.view_id for view in views] == [str(view_id)]
    assert views[0].filters.statuses == ["new"]
    assert views[0].sort_by.value == "prepared_at"

    command.downgrade(config, PREVIOUS_HEAD)
    with SessionLocal() as db:
        assert _has_assignment_column(db)
    command.upgrade(config, "head")
