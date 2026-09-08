"""Exercise the sender migration with retained historical rows in PostgreSQL."""

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.models.sql.database import engine


def test_sender_migration_preserves_history_and_rejects_partial_identity():
    path = Path(__file__).resolve().parents[3] / "alembic/versions/l9m0n1o2p3q4_bind_benchmark_handoff_sender.py"
    spec = importlib.util.spec_from_file_location("handoff_sender_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as connection:
        # A connection-local table exercises the actual migration without
        # mutating the shared schema or requiring unrelated fixture data.
        connection.execute(text(
            "CREATE TEMP TABLE curation_benchmark_handoff_attempts "
            "(id integer PRIMARY KEY, receipt_id text, status text) ON COMMIT DROP"
        ))
        connection.execute(text(
            "INSERT INTO curation_benchmark_handoff_attempts VALUES (1, 'historical-receipt', 'unknown')"
        ))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            row = connection.execute(text(
                "SELECT receipt_id, status, sender_version, sender_issuer, sender_subject "
                "FROM curation_benchmark_handoff_attempts WHERE id=1"
            )).one()
            assert tuple(row) == ("historical-receipt", "unknown", None, None, None)
            for version, issuer, subject in [
                (None, "https://identity.example", "curator"),
                ("1", None, "curator"), ("1", "https://identity.example", None),
                ("2", "https://identity.example", "curator"),
                ("1", "", "curator"), ("1", "https://identity.example", ""),
            ]:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text(
                        "INSERT INTO curation_benchmark_handoff_attempts "
                        "(id, sender_version, sender_issuer, sender_subject) VALUES (2, :version, :issuer, :subject)"
                    ), {"version": version, "issuer": issuer, "subject": subject})
            connection.execute(text(
                "INSERT INTO curation_benchmark_handoff_attempts "
                "(id, sender_version, sender_issuer, sender_subject) "
                "VALUES (2, '1', 'https://identity.example', 'curator')"
            ))
            module.downgrade()
            assert connection.execute(text(
                "SELECT receipt_id, status FROM curation_benchmark_handoff_attempts WHERE id=1"
            )).one() == ("historical-receipt", "unknown")
