"""Physical-schema coverage for immutable curation snapshot persistence."""

from pathlib import Path

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from sqlalchemy import inspect, text

from src.models.sql.database import engine


BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG = Config(str(BACKEND_ROOT / "alembic.ini"))


def test_curation_snapshot_migration_upgrade_indexes_trigger_and_downgrade():
    command.upgrade(ALEMBIC_CONFIG, "head")
    inspector = inspect(engine)
    assert {
        "curation_benchmark_snapshots",
        "curation_benchmark_handoff_attempts",
    } <= set(inspector.get_table_names())
    assert {
        "ix_curation_benchmark_snapshots_envelope_revision",
        "ix_curation_benchmark_snapshots_session",
    } <= {
        item["name"]
        for item in inspector.get_indexes("curation_benchmark_snapshots")
    }
    assert {"ix_curation_benchmark_handoff_attempts_snapshot"} <= {
        item["name"]
        for item in inspector.get_indexes("curation_benchmark_handoff_attempts")
    }
    with engine.connect() as connection:
        trigger_exists = connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_curation_benchmark_snapshots_immutable' "
                "AND NOT tgisinternal)"
            )
        )
    assert trigger_exists is True

    command.downgrade(ALEMBIC_CONFIG, "f3a4b5c6d7e8")
    assert {
        "curation_benchmark_snapshots",
        "curation_benchmark_handoff_attempts",
    }.isdisjoint(inspect(engine).get_table_names())

    command.upgrade(ALEMBIC_CONFIG, "head")
