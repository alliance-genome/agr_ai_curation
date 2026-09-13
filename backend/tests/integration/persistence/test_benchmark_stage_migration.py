"""Real PostgreSQL proof for stage attribution and historical unknowns."""

from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from src.models.sql.database import engine
from src.models.sql.benchmark import BenchmarkStage


@pytest.mark.parametrize("pipeline_complete", [False, True])
def test_stage_repository_fences_leases_and_preserves_interrupted_unknowns(monkeypatch, pipeline_complete):
    from datetime import datetime, timedelta, timezone
    from alembic import command
    from alembic.config import Config
    from src.models.sql.database import SessionLocal
    from src.lib.benchmarks.persistence import BenchmarkRepository, BenchmarkLeaseLostError
    from src.lib.benchmarks.stage_measurements import StageStart, StageFinish, StageIdentity
    from tests.integration.persistence.test_benchmark_repository import _create_job

    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")
    with SessionLocal() as session:
        repository = BenchmarkRepository(session)
        job = _create_job(session, owner="stage-owner-" + uuid4().hex, cells=1)
        now = datetime.now(timezone.utc)
        owner = uuid4()
        repository.claim_next_job(lease_owner=owner, lease_expires_at=now + timedelta(minutes=5), now=now)
        cell = repository.claim_next_cell(job_id=job.id, lease_owner=owner,
                                         lease_expires_at=now + timedelta(minutes=5), now=now)
        stage = StageStart(uuid4(), StageIdentity("extractor", "extraction"), None, None, now)
        for wrong_owner, wrong_attempt in ((uuid4(), cell.attempt_count), (owner, cell.attempt_count + 1)):
            with pytest.raises(BenchmarkLeaseLostError):
                repository.append_stage(cell_id=cell.id, lease_owner=wrong_owner, attempt=wrong_attempt, stage=stage, now=now)
        row = repository.append_stage(cell_id=cell.id, lease_owner=owner, attempt=cell.attempt_count, stage=stage, now=now)
        repository.start_pipeline(cell_id=cell.id, lease_owner=owner, attempt=cell.attempt_count, started_at=now)
        if pipeline_complete:
            repository.finish_pipeline(cell_id=cell.id, lease_owner=owner, attempt=cell.attempt_count,
                                       completed_at=now + timedelta(seconds=2), elapsed_ms=2000)
        completed = StageFinish(stage, now + timedelta(seconds=1), 1000, "succeeded")
        with pytest.raises(BenchmarkLeaseLostError):
            repository.finish_stage(cell_id=cell.id, lease_owner=uuid4(), attempt=cell.attempt_count, stage=completed, now=now)
        repository.finish_stage(cell_id=cell.id, lease_owner=owner, attempt=cell.attempt_count, stage=completed, now=now)
        assert row.elapsed_ms == 1000 and row.status == "succeeded"
        interrupted = StageStart(uuid4(), StageIdentity("second", "validation"), None, None, now)
        unfinished = repository.append_stage(cell_id=cell.id, lease_owner=owner, attempt=cell.attempt_count, stage=interrupted, now=now)
        assert row.ordinal == 0 and unfinished.ordinal == 1
        assert repository.list_stages(job_id=job.id, cell_id=cell.id, owner_subject=job.owner_subject, limit=1) == (row,)
        assert repository.list_stages(job_id=job.id, cell_id=cell.id, owner_subject=job.owner_subject, after_ordinal=0) == (unfinished,)
        with pytest.raises(LookupError):
            repository.list_stages(job_id=job.id, cell_id=cell.id, owner_subject="foreign")
        monkeypatch.setenv("BENCHMARK_MAX_STAGES_PER_CELL", "2")
        with pytest.raises(ValueError, match="retention limit"):
            repository.append_stage(cell_id=cell.id, lease_owner=owner, attempt=cell.attempt_count,
                                    stage=StageStart(uuid4(), StageIdentity("over-cap", "other"), None, None, now), now=now)
        assert repository.recover_expired_cells(now=now + timedelta(minutes=6)) == (cell.id,)
        session.refresh(unfinished)
        assert unfinished.status == "interrupted"
        assert unfinished.completed_at is None and unfinished.elapsed_ms is None
        detail = repository.get_cell(cell_id=cell.id, job_id=job.id, owner_subject=job.owner_subject)
        assert detail.timing["started_at"] == now
        assert detail.timing["elapsed_ms"] == (2000 if pipeline_complete else None)
        assert (detail.timing["completed_at"] is not None) == pipeline_complete
        session.refresh(row)
        assert row.status == "succeeded" and row.elapsed_ms == 1000
        session.rollback()


def test_stage_migration_retains_unknowns_and_enforces_cell_attempt_boundary():
    script = ScriptDirectory(str(Path(__file__).resolve().parents[3] / "alembic"))
    migration = script.get_revision("7b3168a940de").module
    schema = "stage_test_" + uuid4().hex
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(text("CREATE TABLE benchmark_cells (id uuid PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE benchmark_invocations (id uuid PRIMARY KEY, cell_id uuid NOT NULL, attempt integer NOT NULL)"))
            cell, other, invocation, parent = uuid4(), uuid4(), uuid4(), uuid4()
            connection.execute(text("INSERT INTO benchmark_cells (id) VALUES (:id), (:other)"), {"id": cell, "other": other})
            connection.execute(text("INSERT INTO benchmark_invocations VALUES (:id, :cell, 1)"), {"id": invocation, "cell": cell})
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert connection.execute(text("SELECT stage_execution_id, parent_invocation_sequence FROM benchmark_invocations")).one() == (None, None)
            assert {column["name"] for column in inspect(connection).get_columns("benchmark_stages")} == set(BenchmarkStage.__table__.columns.keys())
            insert = text("""INSERT INTO benchmark_stages
                (id, cell_id, attempt, stage_id, role, status, started_at, parent_execution_id, ordinal)
                SELECT :id, :cell, :attempt, 'stage', 'validation', :status, now(), :parent, count(*) FROM benchmark_stages""")
            connection.execute(insert, {"id": parent, "cell": cell, "attempt": 1, "status": "running", "parent": None})
            for wrong_cell, wrong_attempt in ((other, 1), (cell, 2)):
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(insert, {"id": uuid4(), "cell": wrong_cell, "attempt": wrong_attempt, "status": "running", "parent": parent})
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(text("INSERT INTO benchmark_invocations VALUES (:id, :cell, :attempt, :stage, NULL)"),
                                       {"id": uuid4(), "cell": wrong_cell, "attempt": wrong_attempt, "stage": parent})
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text("UPDATE benchmark_stages SET status='succeeded' WHERE id=:id"), {"id": parent})
            connection.execute(text("UPDATE benchmark_stages SET status='interrupted' WHERE id=:id"), {"id": parent})
            assert connection.execute(text("SELECT completed_at, elapsed_ms FROM benchmark_stages WHERE id=:id"), {"id": parent}).one() == (None, None)
            connection.execute(text("UPDATE benchmark_invocations SET stage_execution_id=:stage WHERE id=:id"), {"stage": parent, "id": invocation})
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert connection.execute(text("SELECT count(*) FROM benchmark_invocations")).scalar_one() == 1
            assert "benchmark_stages" not in inspect(connection).get_table_names()
        finally:
            # Schema, fixtures, DDL and downgrade all belong to this transaction.
            transaction.rollback()
