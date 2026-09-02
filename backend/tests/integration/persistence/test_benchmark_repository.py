"""Real PostgreSQL coverage for durable benchmark repository invariants."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.lib.benchmarks.models import (
    BenchmarkConfiguration,
    BenchmarkExecutionTarget,
    BenchmarkInputReference,
    BenchmarkSuite,
    BenchmarkSuiteCase,
    BenchmarkSuiteRoute,
    ResolvedBenchmarkCase,
    ResolvedBenchmarkCell,
    ResolvedBenchmarkPlan,
)
from src.lib.benchmarks.persistence import (
    BenchmarkCellCursor,
    BenchmarkLeaseLostError,
    BenchmarkRepository,
)
from src.models.sql.benchmark import (
    BenchmarkCell,
    BenchmarkCellStatus,
    BenchmarkEvent,
    BenchmarkInputSnapshot,
    BenchmarkInvocation,
    BenchmarkInvocationStatus,
    BenchmarkJob,
    BenchmarkJobStatus,
)
from src.models.sql.database import SessionLocal


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"


def _suite_and_plan(cell_count: int = 3) -> tuple[BenchmarkSuite, ResolvedBenchmarkPlan]:
    route = BenchmarkSuiteRoute(
        provider="provider-a", model="model-a", reasoning_effort="high"
    )
    configuration = BenchmarkConfiguration(
        configuration_id="configuration-a", routes={"supervisor": route}
    )
    target = BenchmarkExecutionTarget(kind="flow", id="flow-a")
    cases = []
    resolved_cases = []
    cells = []
    for position in range(cell_count):
        input_reference = BenchmarkInputReference(
            resolver="fixture", reference=f"record-{position}", version="v1", digest=_digest("a")
        )
        case_id = f"case-{position}"
        cases.append(
            BenchmarkSuiteCase(case_id=case_id, target=target, input=input_reference)
        )
        resolved_cases.append(
            ResolvedBenchmarkCase(case_id=case_id, target=target, input=input_reference)
        )
        cells.append(
            ResolvedBenchmarkCell(
                cell_id=f"{case_id}:configuration-a:1",
                case_id=case_id,
                configuration_id="configuration-a",
                repetition=1,
                target=target,
                input=input_reference,
                routes={"supervisor": route},
            )
        )
    suite = BenchmarkSuite(
        schema_version=2,
        suite_id="suite-a",
        cases=tuple(cases),
        configurations=(configuration,),
        repetitions=1,
    )
    plan = ResolvedBenchmarkPlan(
        suite_id=suite.suite_id,
        suite_digest=_digest("b"),
        catalog_digest=_digest("c"),
        repetitions=1,
        cases=tuple(resolved_cases),
        configurations=(configuration,),
        cells=tuple(cells),
        plan_digest=_digest("d"),
    )
    return suite, plan


def _create_job(db, *, owner: str = "owner-a", cells: int = 3, rerun_of: UUID | None = None):
    suite, plan = _suite_and_plan(cells)
    snapshot_ids_by_case = {}
    for case in plan.cases:
        snapshot = db.scalar(
            select(BenchmarkInputSnapshot).where(
                BenchmarkInputSnapshot.owner_subject == owner,
                BenchmarkInputSnapshot.resolver_id == case.input.resolver,
                BenchmarkInputSnapshot.source_reference == case.input.reference,
                BenchmarkInputSnapshot.source_version == case.input.version,
                BenchmarkInputSnapshot.digest == case.input.digest,
            )
        )
        if snapshot is None:
            snapshot = BenchmarkInputSnapshot(
                id=uuid4(),
                digest=case.input.digest,
                source_version=case.input.version,
                content_type="application/json",
                content_bytes=2,
                resolver_id=case.input.resolver,
                source_reference=case.input.reference,
                sanitized_provenance={
                    "resolver": case.input.resolver,
                    "reference": case.input.reference,
                    "version": case.input.version,
                    "digest": case.input.digest,
                },
                owner_subject=owner,
                service_principal=owner,
                blob_reference=f"sha256/aa/{case.case_id}",
            )
            db.add(snapshot)
        snapshot_ids_by_case[case.case_id] = snapshot.id
    db.flush()
    return BenchmarkRepository(db).create_job(
        owner_subject=owner,
        suite=suite,
        plan=plan,
        config_digest=_digest("e"),
        code_digest=_digest("f"),
        inputs_digest=_digest("0"),
        snapshot_ids_by_case=snapshot_ids_by_case,
        rerun_of_job_id=rerun_of,
    )


def _run_to_terminal(db, job_id: UUID) -> BenchmarkJob:
    repository = BenchmarkRepository(db)
    now = datetime.now(timezone.utc)
    lease_owner = uuid4()
    claimed = repository.claim_next_job(
        lease_owner=lease_owner, lease_expires_at=now + timedelta(minutes=5), now=now
    )
    assert claimed is not None and claimed.id == job_id
    while True:
        cell = repository.claim_next_cell(
            job_id=job_id,
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        if cell is None:
            break
        invocation = repository.append_invocation(
            cell_id=cell.id,
            lease_owner=lease_owner,
            ordinal=0,
            attempt=cell.attempt_count,
            route_slot="supervisor",
            request_digest=_digest("1"),
            requested_provider="openai",
            requested_model="gpt-5.6-sol",
            reasoning_effort="high",
            sequence=1,
            started_at=now,
        )
        repository.finish_invocation(
            invocation_id=invocation.id,
            lease_owner=lease_owner,
            status=BenchmarkInvocationStatus.SUCCEEDED,
            completed_at=now,
            response_digest=_digest("2"),
        )
        repository.append_event(
            job_id=job_id, event_type="cell.succeeded", payload={"cell_id": str(cell.id)}
        )
        repository.finish_cell(
            cell_id=cell.id,
            lease_owner=lease_owner,
            status=BenchmarkCellStatus.SUCCEEDED,
            completed_at=now,
            generated_envelope={"schema_version": "1", "records": [{"position": cell.position}]},
            result={"schema_version": "1", "records": [{"position": cell.position}]},
        )
    return repository.complete_job(
        job_id=job_id, lease_owner=lease_owner, completed_at=now
    )


def test_repository_persists_plan_pages_without_envelopes_and_replays_events():
    db = SessionLocal()
    job_id = None
    try:
        job = _create_job(db)
        job_id = job.id
        db.commit()

        repository = BenchmarkRepository(db)
        first_page = repository.list_cells(
            job_id=job.id, owner_subject="owner-a", limit=2
        )
        assert len(first_page.items) == 2
        assert first_page.next_cursor is not None
        assert isinstance(first_page.next_cursor, BenchmarkCellCursor)
        assert all(not hasattr(item, "generated_envelope") for item in first_page.items)
        second_page = repository.list_cells(
            job_id=job.id,
            owner_subject="owner-a",
            cursor=first_page.next_cursor,
            limit=2,
        )
        assert len(second_page.items) == 1
        assert {item.id for item in first_page.items}.isdisjoint(
            {item.id for item in second_page.items}
        )
        with pytest.raises(LookupError):
            repository.list_cells(job_id=job.id, owner_subject="other-owner")

        terminal = _run_to_terminal(db, job.id)
        assert terminal.status == BenchmarkJobStatus.COMPLETED
        db.commit()

        job_page = repository.list_jobs(owner_subject="owner-a", limit=1)
        assert [item.id for item in job_page.items] == [job.id]
        job_detail = repository.get_job(job_id=job.id, owner_subject="owner-a")
        assert job_detail is not None
        assert job_detail.resolved_plan["plan_digest"] == _digest("d")
        assert repository.get_job(job_id=job.id, owner_subject="other-owner") is None
        cells = repository.list_cells(job_id=job.id, owner_subject="owner-a").items
        detail = repository.get_cell(
            cell_id=cells[0].id, job_id=job.id, owner_subject="owner-a"
        )
        assert detail is not None and detail.generated_envelope is not None
        assert detail.envelope_size_bytes is not None
        assert [
            event.sequence
            for event in repository.replay_events(
                job_id=job.id, owner_subject="owner-a"
            )
        ] == [1, 2, 3]
        assert [
            item.ordinal
            for item in repository.list_invocations(
                job_id=job.id,
                cell_id=cells[0].id,
                owner_subject="owner-a",
            )
        ] == [0]
    finally:
        db.rollback()
        if job_id is not None:
            job = db.get(BenchmarkJob, job_id)
            if job is not None and job.status in {
                BenchmarkJobStatus.COMPLETED,
                BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
                BenchmarkJobStatus.CANCELLED,
                BenchmarkJobStatus.FAILED,
            }:
                BenchmarkRepository(db).delete_terminal_job(
                    job_id=job_id, owner_subject="owner-a"
                )
                db.commit()
        db.close()


def test_job_cells_share_frozen_snapshot_and_snapshot_deletion_is_restricted():
    db = SessionLocal()
    job_id = None
    snapshot_id = None
    try:
        suite, plan = _suite_and_plan(1)
        # Expand repeated/configuration cells while retaining one case input.
        repeated_plan = plan.model_copy(
            update={"cells": (plan.cells[0], plan.cells[0].model_copy(update={"cell_id": "case-0:configuration-a:2", "repetition": 2}))}
        )
        snapshot = BenchmarkInputSnapshot(
            id=uuid4(),
            digest=plan.cases[0].input.digest,
            source_version="v1",
            content_type="application/json",
            content_bytes=2,
            resolver_id="fixture",
            source_reference="record-0",
            sanitized_provenance={"digest": plan.cases[0].input.digest},
            owner_subject="snapshot-owner",
            service_principal="portal",
            blob_reference="sha256/aa/shared",
        )
        db.add(snapshot)
        snapshot_id = snapshot.id
        db.flush()
        job = BenchmarkRepository(db).create_job(
            owner_subject="snapshot-owner",
            suite=suite,
            plan=repeated_plan,
            config_digest=_digest("e"),
            code_digest=_digest("f"),
            inputs_digest=_digest("0"),
            snapshot_ids_by_case={"case-0": snapshot.id},
        )
        job_id = job.id
        db.commit()

        cells = list(
            db.scalars(select(BenchmarkCell).where(BenchmarkCell.job_id == job.id))
        )
        assert len(cells) == 2
        assert {cell.input_snapshot_id for cell in cells} == {snapshot.id}
        db.delete(snapshot)
        with pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.rollback()
        if job_id is not None:
            _run_to_terminal(db, job_id)
            BenchmarkRepository(db).delete_terminal_job(
                job_id=job_id, owner_subject="snapshot-owner"
            )
        if snapshot_id is not None:
            db.execute(
                delete(BenchmarkInputSnapshot).where(
                    BenchmarkInputSnapshot.id == snapshot_id
                )
            )
            db.commit()
        db.close()


def test_terminal_cell_seals_invocations_and_requires_settled_invocations():
    db = SessionLocal()
    job_id = None
    try:
        job = _create_job(db, owner="cell-owner", cells=1)
        job_id = job.id
        db.commit()
        repository = BenchmarkRepository(db)
        now = datetime.now(timezone.utc)
        lease_owner = uuid4()
        assert repository.claim_next_job(
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        ) is not None
        cell = repository.claim_next_cell(
            job_id=job.id,
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        assert cell is not None
        invocation = repository.append_invocation(
            cell_id=cell.id,
            lease_owner=lease_owner,
            ordinal=0,
            attempt=cell.attempt_count,
            route_slot="supervisor",
            request_digest=_digest("1"),
            requested_provider="openai",
            requested_model="gpt-5.6-sol",
            reasoning_effort=None,
            sequence=1,
            started_at=now,
        )
        invocation_id = invocation.id
        db.commit()

        with pytest.raises(ValueError, match="running invocations"):
            repository.finish_cell(
                cell_id=cell.id,
                lease_owner=lease_owner,
                status=BenchmarkCellStatus.CANCELLED,
                completed_at=now,
            )

        with pytest.raises(DBAPIError, match="cannot have running invocations"):
            db.execute(
                update(BenchmarkCell)
                .where(BenchmarkCell.id == cell.id)
                .values(
                    status=BenchmarkCellStatus.CANCELLED,
                    completed_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    lease_heartbeat_at=None,
                )
            )
            db.commit()
        db.rollback()

        repository.finish_invocation(
            invocation_id=invocation_id,
            lease_owner=lease_owner,
            status=BenchmarkInvocationStatus.SUCCEEDED,
            completed_at=now,
            response_digest=_digest("2"),
        )
        repository.finish_cell(
            cell_id=cell.id,
            lease_owner=lease_owner,
            status=BenchmarkCellStatus.SUCCEEDED,
            completed_at=now,
            generated_envelope={"ok": True},
            result={"ok": True},
        )
        db.commit()

        with pytest.raises(BenchmarkLeaseLostError, match="lease is no longer owned"):
            repository.append_invocation(
                cell_id=cell.id,
                lease_owner=lease_owner,
                ordinal=1,
                attempt=cell.attempt_count,
                route_slot="supervisor",
                request_digest=_digest("3"),
                requested_provider="openai",
                requested_model="gpt-5.6-sol",
                reasoning_effort=None,
                sequence=2,
                started_at=now,
            )

        db.add(
            BenchmarkInvocation(
                cell_id=cell.id,
                ordinal=1,
                attempt=cell.attempt_count,
                route_slot="supervisor",
                request_digest=_digest("3"),
                requested_provider="openai",
                requested_model="gpt-5.6-sol",
                sequence=2,
                status=BenchmarkInvocationStatus.RUNNING,
                started_at=now,
            )
        )
        with pytest.raises(DBAPIError, match="requires a running cell"):
            db.commit()
        db.rollback()

        with pytest.raises(DBAPIError, match="requires a running cell"):
            db.execute(
                update(BenchmarkInvocation)
                .where(BenchmarkInvocation.id == invocation_id)
                .values(route_slot="mutated")
            )
            db.commit()
        db.rollback()

        persisted_invocation = db.get(BenchmarkInvocation, invocation_id)
        assert persisted_invocation is not None
        db.delete(persisted_invocation)
        with pytest.raises(DBAPIError, match="requires a running cell"):
            db.commit()
        db.rollback()

        repository.complete_job(
            job_id=job.id, lease_owner=lease_owner, completed_at=now
        )
        db.commit()
    finally:
        db.rollback()
        if job_id is not None:
            job = db.get(BenchmarkJob, job_id)
            if job is not None and job.status in {
                BenchmarkJobStatus.COMPLETED,
                BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
                BenchmarkJobStatus.CANCELLED,
                BenchmarkJobStatus.FAILED,
            }:
                BenchmarkRepository(db).delete_terminal_job(
                    job_id=job_id, owner_subject="cell-owner"
                )
                db.commit()
        db.close()


def test_envelope_limit_terminal_immutability_rerun_lineage_and_cascade(monkeypatch):
    db = SessionLocal()
    source_id = rerun_id = None
    try:
        source = _create_job(db, owner="lineage-owner", cells=1)
        source_id = source.id
        db.commit()
        _run_to_terminal(db, source.id)
        db.commit()

        rerun = _create_job(
            db, owner="lineage-owner", cells=1, rerun_of=source.id
        )
        rerun_id = rerun.id
        db.commit()
        rerun_cell = db.scalar(
            select(BenchmarkCell).where(BenchmarkCell.job_id == rerun.id)
        )
        assert rerun_cell is not None
        assert rerun_cell.source_job_id == source.id
        assert rerun_cell.source_cell_id is not None

        repository = BenchmarkRepository(db)
        now = datetime.now(timezone.utc)
        lease_owner = uuid4()
        claimed_job = repository.claim_next_job(
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        assert claimed_job is not None and claimed_job.id == rerun.id
        cell = repository.claim_next_cell(
            job_id=rerun.id,
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        assert cell is not None
        invocation = repository.append_invocation(
            cell_id=cell.id,
            lease_owner=lease_owner,
            ordinal=0,
            attempt=cell.attempt_count,
            route_slot="supervisor",
            request_digest=_digest("1"),
            requested_provider="openai",
            requested_model="gpt-5.6-sol",
            reasoning_effort=None,
            sequence=1,
            started_at=now,
        )
        repository.finish_invocation(
            invocation_id=invocation.id,
            lease_owner=lease_owner,
            status=BenchmarkInvocationStatus.SUCCEEDED,
            completed_at=now,
            response_digest=_digest("2"),
        )
        repository.append_event(
            job_id=rerun.id,
            event_type="cell.succeeded",
            payload={"cell_id": str(cell.id)},
        )
        boundary_envelope = {"a": 1, "b": 2}
        monkeypatch.setenv("BENCHMARK_MAX_ENVELOPE_BYTES", "15")
        with pytest.raises(ValueError, match="exceeds configured byte limit"):
            repository.finish_cell(
                cell_id=cell.id,
                lease_owner=lease_owner,
                status=BenchmarkCellStatus.SUCCEEDED,
                completed_at=now,
                generated_envelope=boundary_envelope,
                result=boundary_envelope,
            )
        monkeypatch.setenv("BENCHMARK_MAX_ENVELOPE_BYTES", "16")
        finished_cell = repository.finish_cell(
            cell_id=cell.id,
            lease_owner=lease_owner,
            status=BenchmarkCellStatus.SUCCEEDED,
            completed_at=now,
            generated_envelope=boundary_envelope,
            result=boundary_envelope,
        )
        assert finished_cell.envelope_size_bytes == 16
        repository.complete_job(
            job_id=rerun.id, lease_owner=lease_owner, completed_at=now
        )
        db.commit()

        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(
                update(BenchmarkCell)
                .where(BenchmarkCell.id == cell.id)
                .values(input_reference="mutated")
            )
            db.commit()
        db.rollback()

        db.add(
            BenchmarkEvent(
                job_id=rerun.id,
                sequence=2,
                event_type="late.event",
                payload={},
            )
        )
        with pytest.raises(DBAPIError, match="child content is immutable"):
            db.commit()
        db.rollback()

        loaded_job = db.get(BenchmarkJob, rerun.id)
        assert loaded_job is not None
        loaded_cells = tuple(loaded_job.cells)
        loaded_invocations = tuple(
            invocation
            for loaded_cell in loaded_cells
            for invocation in loaded_cell.invocations
        )
        loaded_events = tuple(loaded_job.events)
        assert loaded_cells and loaded_invocations and loaded_events

        assert repository.delete_terminal_job(
            job_id=rerun.id, owner_subject="lineage-owner"
        )
        db.commit()
        rerun_id = None
        assert db.scalar(
            select(func.count()).select_from(BenchmarkCell).where(BenchmarkCell.job_id == rerun.id)
        ) == 0
        assert db.scalar(
            select(func.count()).select_from(BenchmarkInvocation).join(BenchmarkCell).where(
                BenchmarkCell.job_id == rerun.id
            )
        ) == 0
        assert db.scalar(
            select(func.count()).select_from(BenchmarkEvent).where(BenchmarkEvent.job_id == rerun.id)
        ) == 0
        assert db.get(BenchmarkJob, source.id) is not None
    finally:
        db.rollback()
        for candidate in (rerun_id, source_id):
            if candidate is None:
                continue
            job = db.get(BenchmarkJob, candidate)
            if job is not None and job.status in {
                BenchmarkJobStatus.COMPLETED,
                BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
                BenchmarkJobStatus.CANCELLED,
                BenchmarkJobStatus.FAILED,
            }:
                BenchmarkRepository(db).delete_terminal_job(
                    job_id=candidate, owner_subject="lineage-owner"
                )
                db.commit()
        db.close()


def test_constraints_reject_invalid_status_owner_and_source_lineage():
    db = SessionLocal()
    source_id = rerun_id = None
    try:
        source = _create_job(db, owner="constraint-owner", cells=1)
        source_id = source.id
        db.commit()
        _run_to_terminal(db, source.id)
        db.commit()
        with pytest.raises(ValueError, match="does not exist for this owner"):
            _create_job(db, owner="different-owner", cells=1, rerun_of=source.id)
        rerun = _create_job(
            db, owner="constraint-owner", cells=1, rerun_of=source.id
        )
        rerun_id = rerun.id
        db.commit()

        with pytest.raises(IntegrityError):
            db.execute(
                update(BenchmarkJob)
                .where(BenchmarkJob.id == rerun.id)
                .values(status="not-a-status")
            )
            db.commit()
        db.rollback()

        unrelated = _create_job(db, owner="constraint-owner", cells=1)
        db.flush()
        unrelated_cell = db.scalar(
            select(BenchmarkCell).where(BenchmarkCell.job_id == unrelated.id)
        )
        rerun_cell = db.scalar(
            select(BenchmarkCell).where(BenchmarkCell.job_id == rerun.id)
        )
        assert unrelated_cell is not None
        assert rerun_cell is not None
        with pytest.raises(DBAPIError, match="source cell must belong"):
            rerun_cell.source_cell_id = unrelated_cell.id
            rerun_cell.source_job_id = unrelated.id
            db.flush()
        db.rollback()
    finally:
        db.rollback()
        if rerun_id is not None:
            rerun = db.get(BenchmarkJob, rerun_id)
            if rerun is not None and rerun.status == BenchmarkJobStatus.QUEUED:
                _run_to_terminal(db, rerun.id)
                db.commit()
        for candidate in (rerun_id, source_id):
            if candidate is None:
                continue
            job = db.get(BenchmarkJob, candidate)
            if job is not None and job.status in {
                BenchmarkJobStatus.COMPLETED,
                BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
                BenchmarkJobStatus.CANCELLED,
                BenchmarkJobStatus.FAILED,
            }:
                BenchmarkRepository(db).delete_terminal_job(
                    job_id=candidate, owner_subject="constraint-owner"
                )
                db.commit()
        db.close()


def test_expired_cell_is_failed_once_while_queued_sibling_remains_claimable():
    db = SessionLocal()
    job_id = None
    try:
        job = _create_job(db, owner="recovery-owner", cells=2)
        job_id = job.id
        db.commit()
        repository = BenchmarkRepository(db)
        now = datetime.now(timezone.utc)
        lease_owner = uuid4()
        repository.claim_next_job(
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        interrupted = repository.claim_next_cell(
            job_id=job.id,
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(seconds=1),
            now=now,
        )
        assert interrupted is not None
        running = repository.append_invocation(
            cell_id=interrupted.id,
            lease_owner=lease_owner,
            ordinal=0,
            attempt=1,
            route_slot="supervisor",
            request_digest=_digest("1"),
            requested_provider="openrouter",
            requested_model="model-a",
            reasoning_effort="high",
            sequence=1,
            started_at=now,
        )
        db.commit()

        recovered = repository.recover_expired_cells(now=now + timedelta(seconds=2))
        assert recovered == (interrupted.id,)
        db.commit()
        db.refresh(interrupted)
        db.refresh(running)
        assert interrupted.status == BenchmarkCellStatus.FAILED
        assert interrupted.attempt_count == 1
        assert interrupted.generated_envelope is None
        assert interrupted.failure == {
            "category": "interrupted_uncertain",
            "retryable": False,
        }
        assert running.status == BenchmarkInvocationStatus.FAILED
        assert running.failure == interrupted.failure

        with pytest.raises(BenchmarkLeaseLostError):
            repository.finish_cell(
                cell_id=interrupted.id,
                lease_owner=lease_owner,
                status=BenchmarkCellStatus.SUCCEEDED,
                completed_at=now + timedelta(seconds=2),
                generated_envelope={"invalid": "stale"},
                result={"invalid": "stale"},
            )

        sibling = repository.claim_next_cell(
            job_id=job.id,
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now + timedelta(seconds=2),
        )
        assert sibling is not None and sibling.id != interrupted.id
        receipt = repository.append_invocation(
            cell_id=sibling.id,
            lease_owner=lease_owner,
            ordinal=0,
            attempt=1,
            route_slot="supervisor",
            request_digest=_digest("3"),
            requested_provider="openrouter",
            requested_model="model-a",
            reasoning_effort="high",
            sequence=1,
            started_at=now + timedelta(seconds=2),
        )
        repository.finish_invocation(
            invocation_id=receipt.id,
            lease_owner=lease_owner,
            status=BenchmarkInvocationStatus.SUCCEEDED,
            completed_at=now + timedelta(seconds=3),
            response_digest=_digest("4"),
            actual_provider="openrouter",
            actual_model="actual-model",
            routing_attempt=2,
            latency_ms=1234,
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
            billed_amount=Decimal("0.0012300"),
            billed_unit="USD",
            billed_source="provider",
        )
        envelope = {"records": [{"ok": True}]}
        repository.finish_cell(
            cell_id=sibling.id,
            lease_owner=lease_owner,
            status=BenchmarkCellStatus.SUCCEEDED,
            completed_at=now + timedelta(seconds=3),
            generated_envelope=envelope,
            result=envelope["records"],
        )
        terminal = repository.complete_job(
            job_id=job.id,
            lease_owner=lease_owner,
            completed_at=now + timedelta(seconds=3),
        )
        db.commit()
        assert terminal.status == BenchmarkJobStatus.COMPLETED_WITH_FAILURES
        assert sibling.envelope_digest is not None
        assert sibling.result_digest is not None
        assert receipt.billed_amount == Decimal("0.0012300")
        assert receipt.billed_unit == "USD"
        assert receipt.billed_source == "provider"
    finally:
        db.rollback()
        if job_id is not None:
            job = db.get(BenchmarkJob, job_id)
            if job is not None and job.status in {
                BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
                BenchmarkJobStatus.COMPLETED,
            }:
                BenchmarkRepository(db).delete_terminal_job(
                    job_id=job_id, owner_subject="recovery-owner"
                )
                db.commit()
        db.close()


def test_claim_contention_and_heartbeat_are_lease_fenced():
    first = SessionLocal()
    second = SessionLocal()
    job_id = None
    try:
        job = _create_job(first, owner="contention-owner", cells=1)
        job_id = job.id
        first.commit()
        now = datetime.now(timezone.utc)
        lease_owner = uuid4()
        claimed = BenchmarkRepository(first).claim_next_job(
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        assert claimed is not None and claimed.id == job_id
        assert (
            BenchmarkRepository(second).claim_next_job(
                lease_owner=uuid4(),
                lease_expires_at=now + timedelta(minutes=5),
                now=now,
            )
            is None
        )
        second.rollback()
        first.commit()

        cell = BenchmarkRepository(first).claim_next_cell(
            job_id=job_id,
            lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        assert cell is not None
        first.commit()
        assert not BenchmarkRepository(second).heartbeat_leases(
            job_id=job_id,
            cell_id=cell.id,
            lease_owner=uuid4(),
            lease_seconds=300,
            now=now + timedelta(seconds=1),
        )
        second.rollback()
        assert BenchmarkRepository(first).heartbeat_leases(
            job_id=job_id,
            cell_id=cell.id,
            lease_owner=lease_owner,
            lease_seconds=300,
            now=now + timedelta(seconds=1),
        )
        BenchmarkRepository(first).finish_cell(
            cell_id=cell.id,
            lease_owner=lease_owner,
            status=BenchmarkCellStatus.CANCELLED,
            completed_at=now + timedelta(seconds=2),
        )
        BenchmarkRepository(first).complete_job(
            job_id=job_id,
            lease_owner=lease_owner,
            completed_at=now + timedelta(seconds=2),
        )
        first.commit()
    finally:
        first.rollback()
        second.rollback()
        if job_id is not None:
            job = first.get(BenchmarkJob, job_id)
            if job is not None and job.status == BenchmarkJobStatus.COMPLETED:
                BenchmarkRepository(first).delete_terminal_job(
                    job_id=job_id, owner_subject="contention-owner"
                )
                first.commit()
        first.close()
        second.close()


def test_heartbeat_and_all_worker_writes_are_lease_fenced():
    db = SessionLocal()
    job_id = None
    try:
        job = _create_job(db, owner="fence-owner", cells=1)
        job_id = job.id
        db.commit()
        repository = BenchmarkRepository(db)
        now = datetime.now(timezone.utc)
        owner = uuid4()
        other = uuid4()
        repository.claim_next_job(
            lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=10),
            now=now,
        )
        cell = repository.claim_next_cell(
            job_id=job.id,
            lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=10),
            now=now,
        )
        assert cell is not None
        db.commit()

        assert not repository.heartbeat_leases(
            job_id=job.id,
            cell_id=cell.id,
            lease_owner=other,
            lease_seconds=30,
            now=now + timedelta(seconds=1),
        )
        db.rollback()
        assert repository.heartbeat_leases(
            job_id=job.id,
            cell_id=cell.id,
            lease_owner=owner,
            lease_seconds=30,
            now=now + timedelta(seconds=1),
        )
        db.commit()

        with pytest.raises(BenchmarkLeaseLostError):
            repository.append_invocation(
                cell_id=cell.id,
                lease_owner=other,
                ordinal=0,
                attempt=1,
                route_slot="supervisor",
                request_digest=_digest("7"),
                requested_provider="openai",
                requested_model="model-a",
                reasoning_effort=None,
                sequence=1,
                started_at=now + timedelta(seconds=2),
            )
        db.rollback()
        repository.recover_expired_cells(now=now + timedelta(seconds=32))
        replacement = uuid4()
        reclaimed = repository.claim_next_job(
            lease_owner=replacement,
            lease_expires_at=now + timedelta(minutes=2),
            now=now + timedelta(seconds=32),
        )
        assert reclaimed is not None and reclaimed.id == job.id
        terminal = repository.complete_job(
            job_id=job.id,
            lease_owner=replacement,
            completed_at=now + timedelta(seconds=32),
            now=now + timedelta(seconds=32),
        )
        db.commit()
        assert terminal.status == BenchmarkJobStatus.COMPLETED_WITH_FAILURES
    finally:
        db.rollback()
        if job_id is not None:
            job = db.get(BenchmarkJob, job_id)
            if job is not None and job.status == BenchmarkJobStatus.COMPLETED_WITH_FAILURES:
                BenchmarkRepository(db).delete_terminal_job(
                    job_id=job_id, owner_subject="fence-owner"
                )
                db.commit()
        db.close()


def test_cooperative_cancellation_cancels_active_and_queued_cells():
    db = SessionLocal()
    job_id = None
    try:
        job = _create_job(db, owner="cancel-owner", cells=2)
        job_id = job.id
        db.commit()
        repository = BenchmarkRepository(db)
        now = datetime.now(timezone.utc)
        owner = uuid4()
        repository.claim_next_job(
            lease_owner=owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        active = repository.claim_next_cell(
            job_id=job.id,
            lease_owner=owner,
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        assert active is not None
        repository.request_cancellation(
            job_id=job.id, owner_subject="cancel-owner", requested_at=now
        )
        assert repository.cancellation_requested(
            job_id=job.id, lease_owner=owner, now=now
        )
        repository.finish_cell(
            cell_id=active.id,
            lease_owner=owner,
            status=BenchmarkCellStatus.CANCELLED,
            completed_at=now,
        )
        assert repository.cancel_queued_cells(
            job_id=job.id, lease_owner=owner, cancelled_at=now
        ) == 1
        terminal = repository.complete_job(
            job_id=job.id, lease_owner=owner, completed_at=now
        )
        db.commit()
        assert terminal.status == BenchmarkJobStatus.CANCELLED
        assert terminal.cancelled_cells == 2
    finally:
        db.rollback()
        if job_id is not None:
            job = db.get(BenchmarkJob, job_id)
            if job is not None and job.status == BenchmarkJobStatus.CANCELLED:
                BenchmarkRepository(db).delete_terminal_job(
                    job_id=job_id, owner_subject="cancel-owner"
                )
                db.commit()
        db.close()


def test_postgres_skip_locked_allows_only_one_job_claimant():
    first = SessionLocal()
    second = SessionLocal()
    job_id = None
    try:
        job = _create_job(first, owner="contention-owner", cells=1)
        job_id = job.id
        first.commit()
        now = datetime.now(timezone.utc)
        claimed = BenchmarkRepository(first).claim_next_job(
            lease_owner=uuid4(),
            lease_expires_at=now + timedelta(minutes=5),
            now=now,
        )
        assert claimed is not None and claimed.id == job.id
        assert (
            BenchmarkRepository(second).claim_next_job(
                lease_owner=uuid4(),
                lease_expires_at=now + timedelta(minutes=5),
                now=now,
            )
            is None
        )
        second.rollback()
        first.rollback()
        _run_to_terminal(first, job.id)
        first.commit()
    finally:
        second.rollback()
        second.close()
        first.rollback()
        if job_id is not None:
            job = first.get(BenchmarkJob, job_id)
            if job is not None and job.status == BenchmarkJobStatus.COMPLETED:
                BenchmarkRepository(first).delete_terminal_job(
                    job_id=job_id, owner_subject="contention-owner"
                )
                first.commit()
        first.close()
