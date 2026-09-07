"""Canonical result bytes survive real PostgreSQL without JSONB normalization."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select, update
from sqlalchemy.exc import DBAPIError

from src.lib.benchmarks.persistence import BenchmarkRepository, BenchmarkResultArtifactError
from src.models.sql.benchmark import BenchmarkCell, BenchmarkCellStatus
from src.models.sql.database import SessionLocal
from tests.integration.persistence.test_benchmark_repository import (
    _create_job, migrated_database,  # noqa: F401 — shared autouse migration fixture
)


def claim(db):
    job = _create_job(db, cells=1)
    repository = BenchmarkRepository(db)
    now, owner = datetime.now(timezone.utc), uuid4()
    assert repository.claim_next_job(
        lease_owner=owner, lease_expires_at=now + timedelta(minutes=5), now=now,
    ).id == job.id
    cell = repository.claim_next_cell(
        job_id=job.id, lease_owner=owner,
        lease_expires_at=now + timedelta(minutes=5), now=now,
    )
    return repository, job, cell, owner, now


def test_canonical_bytes_round_trip_and_terminal_immutability(monkeypatch):
    with SessionLocal() as db:
        repository, job, cell, owner, now = claim(db)
        envelope = {"large": 1e20, "small": 1e-20, "zero": -0.0,
                    "integer": 9007199254740993, "text": "β🧬"}
        result = {"output": envelope, "invocations": []}
        envelope["decimal_string"] = "0.000123"
        expected = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        repository.finish_cell(
            cell_id=cell.id, lease_owner=owner, status=BenchmarkCellStatus.SUCCEEDED,
            completed_at=now, generated_envelope=envelope, result=result,
        )
        cell_id, job_id = cell.id, job.id
        db.expunge_all()
        loaded = db.get(BenchmarkCell, cell_id)
        assert "result_artifact" in inspect(loaded).unloaded
        artifact = repository.get_result_artifact(cell_id=cell_id, job_id=job_id, owner_subject="owner-a")
        assert artifact.content == expected
        assert artifact.digest == "sha256:" + hashlib.sha256(expected).hexdigest()
        assert artifact.attempt_count == 1
        assert repository.get_cell(cell_id=cell_id, job_id=job_id, owner_subject="owner-a").attempt_count == 1
        with pytest.raises(LookupError):
            repository.get_result_artifact(cell_id=cell_id, job_id=job_id, owner_subject="foreign")
        with pytest.raises(LookupError):
            repository.get_result_artifact(cell_id=uuid4(), job_id=job_id, owner_subject="owner-a")
        monkeypatch.setenv("BENCHMARK_MAX_RESULT_ARTIFACT_BYTES", str(len(expected) - 1))
        with pytest.raises(BenchmarkResultArtifactError, match="result_artifact_oversize"):
            repository.get_result_artifact(cell_id=cell_id, job_id=job_id, owner_subject="owner-a")
        with pytest.raises(DBAPIError, match="immutable"):
            with db.begin_nested():
                db.execute(update(BenchmarkCell).where(BenchmarkCell.id == cell_id).values(result_artifact=b"{}"))
        db.rollback()


@pytest.mark.parametrize("status", [BenchmarkCellStatus.FAILED, BenchmarkCellStatus.CANCELLED])
def test_terminal_without_invocations_retains_attempt_count(status):
    with SessionLocal() as db:
        repository, job, cell, owner, now = claim(db)
        repository.finish_cell(
            cell_id=cell.id, lease_owner=owner, status=status, completed_at=now,
            failure={"category": "runtime_error"} if status == BenchmarkCellStatus.FAILED else None,
        )
        detail = repository.get_cell(cell_id=cell.id, job_id=job.id, owner_subject="owner-a")
        assert detail.attempt_count == 1
        assert detail.generated_envelope is None
        with pytest.raises(BenchmarkResultArtifactError, match="result_artifact_unavailable"):
            repository.get_result_artifact(cell_id=cell.id, job_id=job.id, owner_subject="owner-a")
        db.rollback()


def test_oversize_publication_leaves_cell_running(monkeypatch):
    monkeypatch.setenv("BENCHMARK_MAX_RESULT_ARTIFACT_BYTES", "1")
    with SessionLocal() as db:
        repository, job, cell, owner, now = claim(db)
        with pytest.raises(ValueError, match="configured byte limit"):
            repository.finish_cell(
                cell_id=cell.id, lease_owner=owner, status=BenchmarkCellStatus.SUCCEEDED,
                completed_at=now, generated_envelope={}, result={"output": {}, "invocations": []},
            )
        assert db.scalar(select(BenchmarkCell.status).where(BenchmarkCell.id == cell.id)) == BenchmarkCellStatus.RUNNING
        db.rollback()


@pytest.mark.parametrize("content,code", [
    (None, "result_artifact_unavailable"),
    (b'{"output":{}}', "result_artifact_corrupt"),
])
def test_historical_or_corrupt_success_retains_original_digest(content, code):
    # Model an already terminal historical row and a damaged persisted artifact
    # without weakening the terminal-update trigger for the test database.
    with SessionLocal() as db:
        repository, job, cell, _, now = claim(db)
        original_digest = "sha256:" + "a" * 64
        db.execute(update(BenchmarkCell).where(BenchmarkCell.id == cell.id).values(
            status=BenchmarkCellStatus.SUCCEEDED, completed_at=now,
            generated_envelope={}, envelope_digest="sha256:" + hashlib.sha256(b"{}").hexdigest(),
            result_digest=original_digest, result_artifact=content,
            lease_owner=None, lease_expires_at=None, lease_heartbeat_at=None,
        ))
        with pytest.raises(BenchmarkResultArtifactError, match=code):
            repository.get_result_artifact(cell_id=cell.id, job_id=job.id, owner_subject="owner-a")
        assert repository.get_cell(cell_id=cell.id, job_id=job.id, owner_subject="owner-a").result_digest == original_digest
        db.rollback()


@pytest.mark.parametrize("result", [
    {"output": {"different": True}, "invocations": []},
    {"not_a_worker_result": True},
])
def test_mismatched_result_does_not_publish(result):
    with SessionLocal() as db:
        repository, _, cell, owner, now = claim(db)
        with pytest.raises(ValueError, match="does not match"):
            repository.finish_cell(
                cell_id=cell.id, lease_owner=owner, status=BenchmarkCellStatus.SUCCEEDED,
                completed_at=now, generated_envelope={}, result=result,
            )
        db.refresh(cell)
        assert cell.status == BenchmarkCellStatus.RUNNING
        assert cell.result_digest is None
        assert cell.generated_envelope is None
        db.rollback()
