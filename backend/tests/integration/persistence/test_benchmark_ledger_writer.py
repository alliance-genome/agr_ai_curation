"""Cutover candidate: ledger and execution completion share one lease transaction."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.lib.benchmarks.persistence import BenchmarkLeaseLostError, BenchmarkRepository
from src.lib.cost_ledger.benchmark_reads import read_benchmark_accounting
from src.lib.cost_ledger.facts import CostFactConflict, RecordedCharge, TokenUsage
from src.lib.cost_ledger.persistence import record_cost_facts
from src.models.sql.benchmark import BenchmarkInvocation, BenchmarkInvocationStatus
from src.models.sql.cost_ledger import CostFactRevision, CostSourceReference
from src.models.sql.database import SessionLocal
from tests.integration.persistence.test_benchmark_repository import (
    _create_job, _digest, migrated_database as migrated_database,
)


@pytest.fixture
def owned_call(monkeypatch):
    deployment = "writer-" + uuid4().hex
    monkeypatch.setenv("COST_LEDGER_DEPLOYMENT_ID", deployment)
    monkeypatch.setenv("COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE", "writer-fixture")
    with SessionLocal() as db:
        repo = BenchmarkRepository(db)
        job = _create_job(db, owner="writer-owner", cells=1)
        now, lease = datetime.now(timezone.utc), uuid4()
        assert repo.claim_next_job(lease_owner=lease, lease_expires_at=now + timedelta(minutes=5), now=now).id == job.id
        cell = repo.claim_next_cell(job_id=job.id, lease_owner=lease,
                                    lease_expires_at=now + timedelta(minutes=5), now=now)
        db.commit()
        yield db, repo, job, cell, lease, now, deployment


def append(case, *, measured=None):
    db, repo, _, cell, lease, now, _ = case
    return repo.append_invocation(
        cell_id=cell.id, lease_owner=lease, ordinal=0, attempt=cell.attempt_count,
        route_slot="supervisor", request_digest=_digest("a"), requested_provider="fixture",
        requested_model="fixture", reasoning_effort=None, sequence=1, started_at=now,
        model_request_id=measured, now=now,
    )


def complete(case, invocation, **kwargs):
    _, repo, _, _, lease, now, _ = case
    return repo.finish_invocation(
        invocation_id=invocation.id, lease_owner=lease, now=now,
        completed_at=now, status=BenchmarkInvocationStatus.SUCCEEDED,
        response_digest=_digest("b"), **kwargs,
    )


def fact_count(db, deployment):
    return db.scalar(select(func.count()).select_from(CostFactRevision).where(
        CostFactRevision.deployment_id == deployment,
    ))


@pytest.mark.parametrize("measured", [False, True])
def test_dispatch_binding_then_atomic_completion_without_inline_costs(owned_call, measured):
    db, _, job, cell, _, _, deployment = owned_call
    measured_id = uuid4() if measured else None
    invocation = append(owned_call, measured=measured_id)
    db.commit()
    before = read_benchmark_accounting(db, job_id=job.id, cell_id=cell.id,
                                      invocation_id=invocation.id, owner_subject="writer-owner")
    assert before.reference.fact_revision == 0
    if measured:
        assert before.reference.attempt_id == measured_id
    usage = TokenUsage(100, 40, 140, 30, 10, 25)
    charge = RecordedCharge(Decimal("0.000000000000000123"), "credits", "provider-fixture")
    complete(owned_call, invocation, usage=usage, charge=charge)
    with SessionLocal() as reader:
        assert fact_count(reader, deployment) == 0
        assert reader.get(BenchmarkInvocation, invocation.id).status == BenchmarkInvocationStatus.RUNNING
    db.commit()
    after = read_benchmark_accounting(db, job_id=job.id, cell_id=cell.id,
                                     invocation_id=invocation.id, owner_subject="writer-owner")
    assert after.usage == usage and after.recorded_charge == charge
    assert invocation.input_tokens is None and invocation.billed_amount is None
    assert invocation.model_request_id == measured_id


def test_completion_failure_rolls_back_ledger_even_when_caught(owned_call):
    db, _, _, _, _, _, deployment = owned_call
    invocation = append(owned_call)
    db.commit()
    with pytest.raises(IntegrityError):
        complete(owned_call, invocation, usage=TokenUsage(input_tokens=10), latency_ms=-1)
    db.commit()
    db.refresh(invocation)
    assert invocation.status == BenchmarkInvocationStatus.RUNNING
    assert fact_count(db, deployment) == 0


def test_conflicting_facts_do_not_terminalize_invocation(owned_call):
    db, _, _, _, _, _, deployment = owned_call
    invocation = append(owned_call)
    db.commit()
    binding = db.scalar(select(CostSourceReference).where(CostSourceReference.deployment_id == deployment))
    record_cost_facts(db, deployment_id=deployment, source_namespace="writer-fixture",
                      source_system="benchmark", source_id=str(invocation.id),
                      attempt_id=binding.attempt_id, owner_subject="writer-owner", usage=TokenUsage(input_tokens=1))
    db.commit()
    with pytest.raises(CostFactConflict):
        complete(owned_call, invocation, usage=TokenUsage(input_tokens=2))
    db.commit()
    db.refresh(invocation)
    assert invocation.status == BenchmarkInvocationStatus.RUNNING
    assert fact_count(db, deployment) == 1


def test_expired_lease_cannot_commit_accounting(owned_call):
    db, _, job, _, _, now, deployment = owned_call
    invocation = append(owned_call)
    db.commit()
    job.lease_expires_at = now - timedelta(seconds=1)
    db.commit()
    with pytest.raises(BenchmarkLeaseLostError):
        complete(owned_call, invocation, usage=TokenUsage(input_tokens=10))
    db.commit()
    assert fact_count(db, deployment) == 0


def test_unconfigured_dispatch_leaves_no_invocation(owned_call, monkeypatch):
    db, _, _, cell, _, _, deployment = owned_call
    monkeypatch.setenv("COST_LEDGER_DEPLOYMENT_ID", "")
    with pytest.raises(ValueError, match="scope is not configured"):
        append(owned_call)
    db.commit()
    assert db.scalar(select(func.count()).select_from(BenchmarkInvocation).where(BenchmarkInvocation.cell_id == cell.id)) == 0
    assert db.scalar(select(func.count()).select_from(CostSourceReference).where(CostSourceReference.deployment_id == deployment)) == 0


def test_completion_does_not_rebind_after_scope_change(owned_call, monkeypatch):
    db, _, _, _, _, _, deployment = owned_call
    invocation = append(owned_call)
    db.commit()
    monkeypatch.setenv("COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE", "changed-scope")
    with pytest.raises(ValueError, match="pre-dispatch ledger binding"):
        complete(owned_call, invocation, usage=TokenUsage(input_tokens=10))
    db.commit()
    assert fact_count(db, deployment) == 0
    assert invocation.status == BenchmarkInvocationStatus.RUNNING


@pytest.mark.parametrize("status", [BenchmarkInvocationStatus.FAILED, BenchmarkInvocationStatus.CANCELLED])
def test_failed_or_cancelled_call_retains_unknown_not_free_facts(owned_call, status):
    db, repo, job, cell, lease, now, deployment = owned_call
    invocation = append(owned_call)
    db.commit()
    repo.finish_invocation(invocation_id=invocation.id, lease_owner=lease, status=status,
                           completed_at=now, now=now,
                           failure={"category": "fixture"} if status == BenchmarkInvocationStatus.FAILED else None)
    db.commit()
    projection = read_benchmark_accounting(db, job_id=job.id, cell_id=cell.id,
                                          invocation_id=invocation.id, owner_subject="writer-owner")
    assert projection.reference.fact_revision == 0 and projection.recorded_charge is None
    assert invocation.status == status and fact_count(db, deployment) == 0
