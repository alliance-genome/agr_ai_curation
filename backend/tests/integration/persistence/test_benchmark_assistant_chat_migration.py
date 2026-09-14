"""Rehearse the migration on transaction-local temporary PostgreSQL tables only."""

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.skipif(
    not os.getenv("BENCHMARK_ASSISTANT_TEST_DATABASE_URL"),
    reason="requires explicitly selected isolated PostgreSQL",
)
def test_chat_kind_upgrade_preserves_rows_and_downgrade_refuses_retained_assistance():
    path = Path(__file__).resolve().parents[3] / "alembic/versions/8c4279ba51ef_add_benchmark_assistant_chat_kind.py"
    spec = importlib.util.spec_from_file_location("assistant_chat_migration_test", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(os.environ["BENCHMARK_ASSISTANT_TEST_DATABASE_URL"])
    try:
        with engine.connect() as connection, connection.begin():
            for table in ("chat_sessions", "chat_messages"):
                connection.execute(sa.text(
                    f"CREATE TEMP TABLE {table} (chat_kind text NOT NULL, "
                    f"CONSTRAINT ck_{table}_chat_kind "
                    "CHECK (chat_kind IN ('assistant_chat', 'agent_studio'))) ON COMMIT DROP"
                ))
                connection.execute(sa.text(
                    f"INSERT INTO {table} VALUES ('assistant_chat'), ('agent_studio')"
                ))
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            for table in ("chat_sessions", "chat_messages"):
                connection.execute(sa.text(f"INSERT INTO {table} VALUES ('benchmark_assistant')"))
                assert connection.scalar(sa.text(f"SELECT count(*) FROM {table}")) == 3
            with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
                migration.downgrade()
            for table in ("chat_sessions", "chat_messages"):
                assert connection.scalar(sa.text(f"SELECT count(*) FROM {table}")) == 3
    finally:
        engine.dispose()
