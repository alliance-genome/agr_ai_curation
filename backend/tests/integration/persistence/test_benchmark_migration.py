"""Upgrade/downgrade and physical-schema checks for benchmark persistence."""

from pathlib import Path
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from alembic.operations import Operations  # pyright: ignore[reportMissingImports]
import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from src.lib.benchmarks.persistence import BenchmarkRepository
from src.lib.benchmarks.worker import BenchmarkWorker
from src.models.sql.database import SessionLocal, engine
from tests.integration.persistence.test_benchmark_repository import _create_job, _run_to_terminal


BACKEND_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG = Config(str(BACKEND_ROOT / "alembic.ini"))
TABLES = {
    "benchmark_jobs",
    "benchmark_cells",
    "benchmark_invocations",
    "benchmark_events",
    "benchmark_input_snapshots",
    "benchmark_job_input_snapshots",
}


def test_benchmark_migration_upgrade_indexes_and_downgrade():
    command.upgrade(ALEMBIC_CONFIG, "head")
    inspector = inspect(engine)
    assert TABLES <= set(inspector.get_table_names())
    assert {
        "envelope_digest",
        "result_digest",
    } <= {column["name"] for column in inspector.get_columns("benchmark_cells")}
    assert {
        "requested_provider",
        "requested_model",
        "reasoning_effort",
        "actual_provider",
        "actual_model",
        "routing_attempt",
        "sequence",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "billed_amount",
        "billed_unit",
        "billed_source",
    } <= {column["name"] for column in inspector.get_columns("benchmark_invocations")}

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
        "benchmark_input_snapshots": {"ix_benchmark_snapshots_digest"},
        "benchmark_job_input_snapshots": {"ix_benchmark_job_input_snapshot_id"},
    }
    for table, names in expected_indexes.items():
        assert names <= {item["name"] for item in inspector.get_indexes(table)}

    command.downgrade(ALEMBIC_CONFIG, "d1e2f3a4b5c6")
    assert TABLES.isdisjoint(inspect(engine).get_table_names())

    command.upgrade(ALEMBIC_CONFIG, "head")
    assert TABLES <= set(inspect(engine).get_table_names())


def test_curator_context_upgrade_does_not_invent_historical_identity():
    command.upgrade(ALEMBIC_CONFIG, "head")
    owner = f"historical-context-{uuid4()}"
    with SessionLocal() as session:
        job = _create_job(session, owner=owner, cells=1)
        job_id = job.id
        _run_to_terminal(session, job_id)
        session.commit()
    command.downgrade(ALEMBIC_CONFIG, "i6j7k8l9m0n1")
    command.upgrade(ALEMBIC_CONFIG, "head")
    try:
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT owner_subject, status, curator_context FROM benchmark_jobs WHERE id = :id"
            ), {"id": job_id}).one()
            assert row.owner_subject == owner
            assert row.status == "completed"
            assert row.curator_context is None
            cell_id = connection.scalar(text(
                "SELECT id FROM benchmark_cells WHERE job_id = :id"
            ), {"id": job_id})
        # Refuse execution before attempting to read the historical paper blob.
        with pytest.raises(ValidationError):
            BenchmarkWorker()._load_cell(cell_id)
        with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="curator context is immutable"):
                with connection.begin_nested():
                    connection.execute(text(
                        "UPDATE benchmark_jobs SET curator_context = '{}'::jsonb WHERE id = :id"
                    ), {"id": job_id})
    finally:
        with SessionLocal() as session:
            BenchmarkRepository(session).delete_terminal_job(job_id=job_id, owner_subject=owner)
            session.commit()


@pytest.mark.parametrize("interrupt_backfill", [False, True])
def test_telemetry_upgrade_backfills_terminal_invocations_and_restores_guards(
    monkeypatch, interrupt_backfill,
):
    command.upgrade(ALEMBIC_CONFIG, "head")
    owner = f"migration-review-{uuid4()}"
    with SessionLocal() as session:
        job = _create_job(session, owner=owner, cells=2)
        job_id = job.id
        _run_to_terminal(session, job_id)
        session.commit()

    historical = text("""
        SELECT inv.id, inv.cell_id, inv.status, inv.ordinal, inv.attempt,
               inv.route_slot, inv.request_digest, inv.response_digest,
               inv.started_at, inv.completed_at
        FROM benchmark_invocations AS inv JOIN benchmark_cells AS cell ON cell.id = inv.cell_id
        WHERE cell.job_id = :job_id ORDER BY inv.id
    """)
    with engine.connect() as connection:
        before = connection.execute(historical, {"job_id": job_id}).all()
    assert len(before) == 2

    command.downgrade(ALEMBIC_CONFIG, "g4b5c6d7e8f9")
    guard_query = text("""
        SELECT tgname, tgenabled FROM pg_trigger
        WHERE tgrelid = 'benchmark_invocations'::regclass AND NOT tgisinternal
        ORDER BY tgname
    """)
    if interrupt_backfill:
        with engine.connect() as connection:
            guards_before = connection.execute(guard_query).all()
        execute = Operations.execute

        def fail_after_backfill(operations, statement, *args, **kwargs):
            result = execute(operations, statement, *args, **kwargs)
            if str(statement) == "UPDATE benchmark_invocations SET sequence = ordinal + 1":
                raise RuntimeError("synthetic interruption before trigger restoration")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(Operations, "execute", fail_after_backfill)
            with pytest.raises(RuntimeError, match="synthetic interruption"):
                command.upgrade(ALEMBIC_CONFIG, "head")
        with engine.connect() as connection:
            assert connection.execute(guard_query).all() == guards_before
            assert connection.execute(historical, {"job_id": job_id}).all() == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "g4b5c6d7e8f9"
        assert "sequence" not in {
            column["name"] for column in inspect(engine).get_columns("benchmark_invocations")
        }
    command.upgrade(ALEMBIC_CONFIG, "head")
    try:
        with engine.connect() as connection:
            assert connection.execute(historical, {"job_id": job_id}).all() == before
            sequences = connection.execute(text("""
                SELECT inv.sequence, inv.ordinal FROM benchmark_invocations AS inv
                JOIN benchmark_cells AS cell ON cell.id = inv.cell_id
                WHERE cell.job_id = :job_id
            """), {"job_id": job_id}).all()
            assert all(sequence == ordinal + 1 for sequence, ordinal in sequences)
            guards = dict(connection.execute(guard_query).all())
            for name in (
                "trg_benchmark_invocations_terminal_update",
                "trg_benchmark_invocations_running_cell",
                "trg_benchmark_invocations_terminal_job_content",
            ):
                assert guards[name] == "O"
        with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="immutable|running cell"):
                with connection.begin_nested():
                    connection.execute(
                        text("UPDATE benchmark_invocations SET sequence = 99 WHERE id = :id"),
                        {"id": before[0].id},
                    )
    finally:
        with SessionLocal() as session:
            BenchmarkRepository(session).delete_terminal_job(job_id=job_id, owner_subject=owner)
            session.commit()
