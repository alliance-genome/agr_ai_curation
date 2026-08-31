"""Upgrade/downgrade and physical-schema checks for benchmark persistence."""

from pathlib import Path

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from sqlalchemy import inspect

from src.models.sql.database import engine


BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG = Config(str(BACKEND_ROOT / "alembic.ini"))
TABLES = {
    "benchmark_jobs",
    "benchmark_cells",
    "benchmark_invocations",
    "benchmark_events",
}


def test_benchmark_migration_upgrade_indexes_and_downgrade():
    command.upgrade(ALEMBIC_CONFIG, "head")
    inspector = inspect(engine)
    assert TABLES <= set(inspector.get_table_names())

    expected_indexes = {
        "benchmark_jobs": {
            "ix_benchmark_jobs_owner_status_created",
            "ix_benchmark_jobs_queued_claim",
            "ix_benchmark_jobs_expired_lease_claim",
        },
        "benchmark_cells": {
            "ix_benchmark_cells_job_page",
            "ix_benchmark_cells_queued_claim",
            "ix_benchmark_cells_expired_lease_claim",
        },
        "benchmark_invocations": {"ix_benchmark_invocations_cell_order"},
        "benchmark_events": {"ix_benchmark_events_replay"},
    }
    for table, names in expected_indexes.items():
        assert names <= {item["name"] for item in inspector.get_indexes(table)}

    command.downgrade(ALEMBIC_CONFIG, "d1e2f3a4b5c6")
    assert TABLES.isdisjoint(inspect(engine).get_table_names())

    command.upgrade(ALEMBIC_CONFIG, "head")
    assert TABLES <= set(inspect(engine).get_table_names())
