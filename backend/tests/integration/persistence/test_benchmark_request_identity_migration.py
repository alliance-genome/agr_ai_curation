"""Real PostgreSQL preservation/uniqueness checks for request-ID binding."""
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def test_request_binding_migration_keeps_historical_facts_and_unknown_identity():
    path = Path(__file__).resolve().parents[3] / "alembic/versions/a31c05e0925a_bind_benchmark_model_requests.py"
    spec = spec_from_file_location("request_binding_migration", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(os.environ["DATABASE_URL"])
    schema = "request_binding_" + uuid4().hex
    with engine.connect() as connection:
        # This whole isolated schema is transactional and rolled back below.
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(text("CREATE TABLE benchmark_invocations (id integer PRIMARY KEY, billed_amount numeric)"))
            connection.execute(text("INSERT INTO benchmark_invocations VALUES (1, 0.00123), (2, NULL)"))
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            assert connection.execute(text(
                "SELECT id, billed_amount::text, model_request_id FROM benchmark_invocations ORDER BY id"
            )).all() == [(1, "0.00123", None), (2, None, None)]
            request_id = uuid4()
            connection.execute(text("INSERT INTO benchmark_invocations (id, model_request_id) VALUES (3, :id)"), {"id": request_id})
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text("INSERT INTO benchmark_invocations (id, model_request_id) VALUES (4, :id)"), {"id": request_id})
            migration.downgrade()
            assert connection.execute(text("SELECT id, billed_amount::text FROM benchmark_invocations ORDER BY id")).all() == [
                (1, "0.00123"), (2, None), (3, None),
            ]
            migration.upgrade()
            assert connection.scalar(text("SELECT count(*) FROM benchmark_invocations WHERE model_request_id IS NULL")) == 3
        finally:
            transaction.rollback()
    engine.dispose()
