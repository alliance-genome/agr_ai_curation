"""Physical-schema coverage for immutable curation snapshot persistence."""

from pathlib import Path

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from alembic.migration import MigrationContext  # pyright: ignore[reportMissingImports]
from alembic.operations import Operations  # pyright: ignore[reportMissingImports]
from alembic.script import ScriptDirectory  # pyright: ignore[reportMissingImports]
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

    migration = ScriptDirectory.from_config(ALEMBIC_CONFIG).get_revision("g4b5c6d7e8f9").module
    # Keep the real merged stamp: historical f3 is intentionally ambiguous.
    # Always roll back the temporary shape, including later sender columns.
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            head = connection.scalar(text("SELECT version_num FROM alembic_version"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                assert {
                    "curation_benchmark_snapshots",
                    "curation_benchmark_handoff_attempts",
                }.isdisjoint(inspect(connection).get_table_names())
                migration.upgrade()
            assert {
                "curation_benchmark_snapshots",
                "curation_benchmark_handoff_attempts",
            } <= set(inspect(connection).get_table_names())
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == head
            assert connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'trg_curation_benchmark_snapshots_immutable' "
                "AND NOT tgisinternal)"
            )) is True
        finally:
            transaction.rollback()
