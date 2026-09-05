"""PostgreSQL-backed durable benchmark worker behavior with fake providers."""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
from pydantic import RootModel
import pytest
from sqlalchemy import delete, select, update

from src.lib.benchmarks.input_resolvers import (
    BenchmarkInputResolverCatalog,
    BenchmarkSourceError,
    BenchmarkSourceMetadata,
    BenchmarkSourceProvenance,
    BenchmarkSourceRequestContext,
    DelegatedAuthorizationCapability,
    DelegatedSourceAuthorization,
    MaterializedBenchmarkInput,
)
from src.lib.benchmarks.models import BenchmarkCellExecutionResult, ProviderUsage
from src.lib.benchmarks.persistence import BenchmarkRepository
from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotRepository,
    FileSystemBenchmarkSnapshotStore,
    materialize_and_freeze_plan_inputs,
)
from src.lib.benchmarks.worker import BenchmarkWorker
from src.lib.openai_agents.provider_usage import (
    ProviderUsageRecord,
    begin_provider_invocation,
    capture_provider_usage,
    complete_provider_invocation,
    fail_provider_invocation,
)
from src.models.sql.benchmark import (
    BenchmarkCell,
    BenchmarkCellStatus,
    BenchmarkEvent,
    BenchmarkInputSnapshot,
    BenchmarkInvocation,
    BenchmarkJob,
    BenchmarkJobInputSnapshot,
    BenchmarkJobStatus,
)
from src.models.sql.database import SessionLocal
from tests.integration.persistence.test_benchmark_repository import (
    _create_job,
    _curator_context,
    _digest,
    _suite_and_plan,
)


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
def worker_environment(monkeypatch):
    async def synthetic_authorization(context, **kwargs):
        return context

    # This module exercises durable worker transactions using synthetic users;
    # live provider/local account checks have their own focused regression suite.
    monkeypatch.setattr(
        "src.lib.benchmarks.worker.authorize_benchmark_curator", synthetic_authorization,
    )
    async def synthetic_preparation(**kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(document_id=kwargs["snapshot_id"]), _curator_context()

    # Transaction/dispatch fixtures use synthetic preparation. The preparation
    # integration suite exercises the real coordinator, verified blobs and SQL.
    monkeypatch.setattr("src.lib.benchmarks.worker.prepare_job_document", synthetic_preparation)
    monkeypatch.setenv("BENCHMARK_WORKER_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("BENCHMARK_WORKER_LEASE_SECONDS", "300")
    monkeypatch.setenv("BENCHMARK_WORKER_HEARTBEAT_SECONDS", "30")
    monkeypatch.setenv("BENCHMARK_CELL_TIMEOUT_SECONDS", "30")
    monkeypatch.setattr(
        BenchmarkSnapshotRepository,
        "read_verified",
        lambda self, snapshot_id, *, owner_subject: b'{"messages": []}',
    )


def _provider_usage(*, failed: bool = False) -> ProviderUsage:
    return ProviderUsage(
        route_slot="supervisor",
        requested_provider="provider-a",
        requested_model="model-a",
        reasoning_effort="high",
        actual_provider=None if failed else "provider-a",
        actual_model=None if failed else "model-a",
        routing_attempt=None if failed else 0,
        latency_ms=10,
        input_tokens=None if failed else 2,
        output_tokens=None if failed else 3,
        total_tokens=None if failed else 5,
        billed_cost=None,
        sequence=1,
        status="failed" if failed else "completed",
        failure_detail="RuntimeError" if failed else None,
    )


async def _emit_fake_provider_call(*, fail: bool = False) -> BenchmarkCellExecutionResult:
    with capture_provider_usage(max_records=5, max_failure_detail_chars=128):
        pending = begin_provider_invocation(
            route_slot="supervisor",
            requested_provider="provider-a",
            requested_model="model-a",
            reasoning_effort="high",
            started_at=1.0,
        )
        if fail:
            error = RuntimeError("synthetic provider failure")
            fail_provider_invocation(pending, error, latency_ms=10)
            raise error
        complete_provider_invocation(
            pending,
            ProviderUsageRecord(
                requested_provider="provider-a",
                requested_model="model-a",
                actual_provider="provider-a",
                actual_model="model-a",
                routing_attempt=0,
                latency_ms=10,
                input_tokens=2,
                output_tokens=3,
                total_tokens=5,
                billed_cost=None,
            ),
        )
    return BenchmarkCellExecutionResult(
        output={"records": [{"ok": True}]}, invocations=[_provider_usage()]
    )


def _delete_job(job_id, owner: str) -> None:
    with SessionLocal() as session:
        job = session.get(BenchmarkJob, job_id)
        if job is not None and job.status in {
            BenchmarkJobStatus.COMPLETED,
            BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
            BenchmarkJobStatus.CANCELLED,
            BenchmarkJobStatus.FAILED,
        }:
            BenchmarkRepository(session).delete_terminal_job(
                job_id=job_id, owner_subject=owner
            )
            session.commit()


class _DelegatedReference(RootModel[str]):
    pass


class _DelegatedResolver:
    resolver_id = "fixture"
    reference_schema: type[RootModel[str]] = _DelegatedReference
    delegated_authorization = DelegatedAuthorizationCapability.REQUIRED

    def __init__(self, content: bytes, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.observed_bearer: str | None = None

    async def materialize(
        self,
        reference,
        validated_reference,
        *,
        max_bytes,
        request_context,
    ):
        assert request_context.delegated_authorization is not None
        self.observed_bearer = request_context.delegated_authorization.reveal()
        if self.fail:
            raise RuntimeError(f"upstream rejected {self.observed_bearer}")
        digest = f"sha256:{hashlib.sha256(self.content).hexdigest()}"
        provenance = BenchmarkSourceProvenance(
            resolver=self.resolver_id,
            reference=validated_reference,
            version=reference.version,
            digest=digest,
        )
        return MaterializedBenchmarkInput(
            resolver=provenance.resolver,
            reference=provenance.reference,
            version=provenance.version,
            digest=provenance.digest,
            content=self.content.decode(),
            metadata=BenchmarkSourceMetadata(
                content_type="application/json", content_bytes=len(self.content)
            ),
            provenance=provenance,
        )


def _plan_with_input_digest(content: bytes):
    suite, plan = _suite_and_plan(1)
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    input_reference = plan.cases[0].input.model_copy(update={"digest": digest})
    suite = suite.model_copy(
        update={
            "cases": (suite.cases[0].model_copy(update={"input": input_reference}),)
        }
    )
    plan = plan.model_copy(
        update={
            "cases": (plan.cases[0].model_copy(update={"input": input_reference}),),
            "cells": (plan.cells[0].model_copy(update={"input": input_reference}),),
        }
    )
    return suite, plan


def _serialized_benchmark_rows(session) -> str:
    durable_models = (
        BenchmarkInputSnapshot,
        BenchmarkJob,
        BenchmarkJobInputSnapshot,
        BenchmarkCell,
        BenchmarkInvocation,
        BenchmarkEvent,
    )
    rows = []
    for model in durable_models:
        for row in session.scalars(select(model)):
            rows.append(
                json.dumps(
                    {
                        column.name: getattr(row, column.name)
                        for column in model.__table__.columns
                    },
                    default=str,
                    sort_keys=True,
                )
            )
    return "\n".join(rows)


def _delete_owner_snapshots(owner: str) -> None:
    with SessionLocal() as session:
        session.execute(
            delete(BenchmarkInputSnapshot).where(
                BenchmarkInputSnapshot.owner_subject == owner
            )
        )
        session.commit()


def test_worker_requires_both_execution_gates(monkeypatch):
    monkeypatch.setenv("BENCHMARK_EXECUTION_ENABLED", "false")
    worker = BenchmarkWorker()
    worker.recover_expired = lambda: pytest.fail("disabled worker polled the database")
    assert asyncio.run(worker.run_once()) is False


def test_successful_delegated_submission_never_serializes_token(tmp_path):
    owner = f"delegated-success-{uuid4()}"
    token = f"distinctive-delegated-token-{uuid4()}"
    content = b'{"messages": []}'
    suite, plan = _plan_with_input_digest(content)
    resolver = _DelegatedResolver(content)
    catalog = BenchmarkInputResolverCatalog(
        [resolver], timeout_seconds=1, max_input_bytes=1024
    )
    job_id = None

    try:
        with SessionLocal() as session:
            snapshot_ids = asyncio.run(
                materialize_and_freeze_plan_inputs(
                    plan,
                    catalog,
                    BenchmarkSnapshotRepository(
                        session, FileSystemBenchmarkSnapshotStore(tmp_path)
                    ),
                    request_context=BenchmarkSourceRequestContext(
                        principal_subject=owner,
                        delegated_authorization=DelegatedSourceAuthorization(token),
                    ),
                    service_principal="benchmark-portal",
                    max_submission_bytes=1024,
                )
            )
            job = BenchmarkRepository(session).create_job(
                owner_subject=owner,
                curator_context=_curator_context(),
                suite=suite,
                plan=plan,
                config_digest=_digest("e"),
                code_digest=_digest("f"),
                inputs_digest=_digest("0"),
                snapshot_ids_by_case=snapshot_ids,
            )
            job_id = job.id
            session.commit()

        async def fake_executor(cell, case_input, run_id):
            return await _emit_fake_provider_call()

        assert asyncio.run(BenchmarkWorker(flow_executor=fake_executor).run_once()) is True
        assert resolver.observed_bearer == token
        with SessionLocal() as session:
            assert token not in _serialized_benchmark_rows(session)
        assert all(
            token not in path.read_text()
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    finally:
        if job_id is not None:
            _delete_job(job_id, owner)
        _delete_owner_snapshots(owner)


def test_failed_delegated_submission_never_serializes_token(tmp_path):
    owner = f"delegated-failure-{uuid4()}"
    token = f"distinctive-delegated-token-{uuid4()}"
    content = b'{"messages": []}'
    _, plan = _plan_with_input_digest(content)
    resolver = _DelegatedResolver(content, fail=True)
    catalog = BenchmarkInputResolverCatalog(
        [resolver], timeout_seconds=1, max_input_bytes=1024
    )

    with SessionLocal() as session:
        with pytest.raises(BenchmarkSourceError, match="unavailable"):
            asyncio.run(
                materialize_and_freeze_plan_inputs(
                    plan,
                    catalog,
                    BenchmarkSnapshotRepository(
                        session, FileSystemBenchmarkSnapshotStore(tmp_path)
                    ),
                    request_context=BenchmarkSourceRequestContext(
                        principal_subject=owner,
                        delegated_authorization=DelegatedSourceAuthorization(token),
                    ),
                    service_principal="benchmark-portal",
                    max_submission_bytes=1024,
                )
            )
        session.rollback()

    assert resolver.observed_bearer == token
    with SessionLocal() as session:
        assert token not in _serialized_benchmark_rows(session)
    assert not tuple(tmp_path.rglob("*"))


def test_failed_cell_does_not_stop_sibling_and_exposes_no_partial_envelope():
    owner = "worker-partial-owner"
    calls = 0

    async def fake_executor(cell, case_input, run_id):
        nonlocal calls
        calls += 1
        return await _emit_fake_provider_call(fail=calls == 1)

    with SessionLocal() as session:
        job = _create_job(session, owner=owner, cells=2)
        job_id = job.id
        session.commit()
    try:
        assert asyncio.run(
            BenchmarkWorker(flow_executor=fake_executor).run_once()
        ) is True
        with SessionLocal() as session:
            job = session.get(BenchmarkJob, job_id)
            cells = tuple(
                session.scalars(
                    select(BenchmarkCell)
                    .where(BenchmarkCell.job_id == job_id)
                    .order_by(BenchmarkCell.position)
                )
            )
            invocations = tuple(
                session.scalars(
                    select(BenchmarkInvocation)
                    .join(BenchmarkCell)
                    .where(BenchmarkCell.job_id == job_id)
                    .order_by(BenchmarkCell.position)
                )
            )
            assert job is not None
            assert job.status == BenchmarkJobStatus.COMPLETED_WITH_FAILURES
            assert calls == 2
            assert [cell.status for cell in cells] == [
                BenchmarkCellStatus.FAILED,
                BenchmarkCellStatus.SUCCEEDED,
            ]
            assert cells[0].generated_envelope is None
            assert cells[0].envelope_digest is None
            assert cells[0].result_digest is None
            assert cells[1].generated_envelope is not None
            assert cells[1].envelope_digest is not None
            assert cells[1].result_digest is not None
            assert len(invocations) == 2
            assert invocations[0].failure == {
                "category": "provider_error",
                "retryable": False,
                "detail": "RuntimeError",
            }
    finally:
        _delete_job(job_id, owner)


def test_worker_recovery_never_replays_uncertain_fake_provider_call():
    owner = "worker-recovery-owner"
    provider_calls = 1
    now = datetime.now(timezone.utc)
    first_owner = uuid4()
    with SessionLocal() as session:
        job = _create_job(session, owner=owner, cells=2)
        job_id = job.id
        repository = BenchmarkRepository(session)
        repository.claim_next_job(
            lease_owner=first_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        interrupted = repository.claim_next_cell(
            job_id=job_id,
            lease_owner=first_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        assert interrupted is not None
        repository.append_invocation(
            cell_id=interrupted.id,
            lease_owner=first_owner,
            ordinal=0,
            attempt=1,
            route_slot="supervisor",
            request_digest=_digest("1"),
            requested_provider="provider-a",
            requested_model="model-a",
            reasoning_effort="high",
            sequence=1,
            started_at=now,
        )
        session.execute(
            update(BenchmarkCell)
            .where(BenchmarkCell.id == interrupted.id)
            .values(lease_expires_at=now - timedelta(seconds=1))
        )
        session.execute(
            update(BenchmarkJob)
            .where(BenchmarkJob.id == job_id)
            .values(lease_expires_at=now - timedelta(seconds=1))
        )
        session.commit()

    async def fake_executor(cell, case_input, run_id):
        nonlocal provider_calls
        provider_calls += 1
        return await _emit_fake_provider_call()

    try:
        assert asyncio.run(
            BenchmarkWorker(flow_executor=fake_executor).run_once()
        ) is True
        with SessionLocal() as session:
            cells = tuple(
                session.scalars(
                    select(BenchmarkCell)
                    .where(BenchmarkCell.job_id == job_id)
                    .order_by(BenchmarkCell.position)
                )
            )
            assert provider_calls == 2
            assert cells[0].status == BenchmarkCellStatus.FAILED
            assert cells[0].attempt_count == 1
            assert cells[0].failure == {
                "category": "interrupted_uncertain",
                "retryable": False,
            }
            assert cells[1].status == BenchmarkCellStatus.SUCCEEDED
    finally:
        _delete_job(job_id, owner)


def test_worker_honors_cooperative_cancellation_without_exposing_envelope():
    owner = "worker-cancel-owner"
    with SessionLocal() as session:
        job = _create_job(session, owner=owner, cells=2)
        job_id = job.id
        session.commit()

    async def cancelling_executor(cell, case_input, run_id):
        outcome = await _emit_fake_provider_call()
        with SessionLocal() as session:
            BenchmarkRepository(session).request_cancellation(
                job_id=job_id,
                owner_subject=owner,
                requested_at=datetime.now(timezone.utc),
            )
            session.commit()
        return outcome

    try:
        assert asyncio.run(
            BenchmarkWorker(flow_executor=cancelling_executor).run_once()
        ) is True
        with SessionLocal() as session:
            job = session.get(BenchmarkJob, job_id)
            cells = tuple(
                session.scalars(
                    select(BenchmarkCell)
                    .where(BenchmarkCell.job_id == job_id)
                    .order_by(BenchmarkCell.position)
                )
            )
            assert job is not None and job.status == BenchmarkJobStatus.CANCELLED
            assert all(cell.status == BenchmarkCellStatus.CANCELLED for cell in cells)
            assert all(cell.generated_envelope is None for cell in cells)
    finally:
        _delete_job(job_id, owner)


def test_cancellation_checkpoint_prevents_provider_dispatch():
    owner = "worker-pre-dispatch-cancel-owner"
    dispatched = 0
    with SessionLocal() as session:
        job = _create_job(session, owner=owner, cells=1)
        job_id = job.id
        session.commit()

    async def cancelled_before_provider(cell, case_input, run_id):
        nonlocal dispatched
        with SessionLocal() as session:
            BenchmarkRepository(session).request_cancellation(
                job_id=job_id,
                owner_subject=owner,
                requested_at=datetime.now(timezone.utc),
            )
            session.commit()
        with capture_provider_usage(max_records=5, max_failure_detail_chars=128):
            begin_provider_invocation(
                route_slot="supervisor",
                requested_provider="provider-a",
                requested_model="model-a",
                reasoning_effort="high",
                started_at=1.0,
            )
            dispatched += 1
        raise AssertionError("provider dispatch checkpoint should have stopped execution")

    try:
        assert asyncio.run(
            BenchmarkWorker(flow_executor=cancelled_before_provider).run_once()
        ) is True
        with SessionLocal() as session:
            job = session.get(BenchmarkJob, job_id)
            cell = session.scalar(
                select(BenchmarkCell).where(BenchmarkCell.job_id == job_id)
            )
            assert dispatched == 0
            assert job is not None and job.status == BenchmarkJobStatus.CANCELLED
            assert cell is not None and cell.status == BenchmarkCellStatus.CANCELLED
            assert session.scalar(
                select(BenchmarkInvocation).where(BenchmarkInvocation.cell_id == cell.id)
            ) is None
    finally:
        _delete_job(job_id, owner)
