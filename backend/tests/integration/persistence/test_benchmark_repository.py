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


def _curator_context():
    from src.lib.benchmarks.execution_context import BenchmarkCuratorContext

    return BenchmarkCuratorContext(
        subject="synthetic-curator", auth_provider="oidc", db_user_id=42,
        active_groups=("group-alpha",),
    )


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
        curator_context=_curator_context(),
        suite=suite,
        plan=plan,
        config_digest=_digest("e"),
        code_digest=_digest("f"),
        inputs_digest=_digest("0"),
        snapshot_ids_by_case=snapshot_ids_by_case,
        rerun_of_job_id=rerun_of,
    )


def _run_to_terminal(db, job_id: UUID, *, billed_amount: Decimal | None = None) -> BenchmarkJob:
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
            billed_amount=billed_amount,
            billed_unit="credits" if billed_amount is not None else None,
            billed_source="audit-fixture" if billed_amount is not None else None,
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
            result={"output": {"schema_version": "1", "records": [{"position": cell.position}]}, "invocations": []},
        )
    return repository.complete_job(
        job_id=job_id, lease_owner=lease_owner, completed_at=now
    )


def test_model_request_binding_survives_commit_and_rejects_duplicate_or_stale_dispatch():
    with SessionLocal() as db:
        job = _create_job(db, owner="request-binding-owner", cells=1)
        repository = BenchmarkRepository(db)
        now, lease_owner = datetime.now(timezone.utc), uuid4()
        repository.claim_next_job(
            lease_owner=lease_owner, lease_expires_at=now + timedelta(minutes=5), now=now,
        )
        cell = repository.claim_next_cell(
            job_id=job.id, lease_owner=lease_owner,
            lease_expires_at=now + timedelta(minutes=5), now=now,
        )
        assert cell is not None
        request_id = uuid4()

        def append(ordinal, identity, owner=lease_owner):
            return repository.append_invocation(
                cell_id=cell.id, lease_owner=owner, ordinal=ordinal, attempt=1,
                route_slot="supervisor", request_digest=_digest("1"),
                requested_provider="openai", requested_model="fixture", reasoning_effort=None,
                sequence=ordinal + 1, started_at=now, model_request_id=identity,
            )

        first = append(0, request_id)
        with pytest.raises(IntegrityError, match="uq_benchmark_invocations_model_request"):
            with db.begin_nested():
                append(1, request_id)
        with pytest.raises(BenchmarkLeaseLostError):
            with db.begin_nested():
                append(1, uuid4(), uuid4())
        with pytest.raises(BenchmarkLeaseLostError):
            with db.begin_nested():
                repository.append_invocation(
                    cell_id=cell.id, lease_owner=lease_owner, ordinal=1, attempt=1,
                    route_slot="supervisor", request_digest=_digest("1"),
                    requested_provider="openai", requested_model="fixture", reasoning_effort=None,
                    sequence=2, started_at=now, model_request_id=uuid4(),
                    now=now + timedelta(minutes=6),
                )
        retry_id = uuid4()
        retry = append(1, retry_id)
        unknowns = [append(2, None), append(3, None)]
        ids = [row.id for row in [first, retry, *unknowns]]
        db.commit()
        with SessionLocal() as fresh:
            assert [fresh.get(BenchmarkInvocation, key).model_request_id for key in ids] == [
                request_id, retry_id, None, None,
            ]
        for row in [first, retry, *unknowns]:
            repository.finish_invocation(
                invocation_id=row.id, lease_owner=lease_owner,
                status=BenchmarkInvocationStatus.SUCCEEDED, completed_at=now,
                response_digest=_digest("2"), input_tokens=1, output_tokens=2, total_tokens=3,
            )
        repository.finish_cell(
            cell_id=cell.id, lease_owner=lease_owner, status=BenchmarkCellStatus.SUCCEEDED,
            completed_at=now, generated_envelope={"records": []},
            result={"output": {"records": []}, "invocations": []},
        )
        repository.complete_job(job_id=job.id, lease_owner=lease_owner, completed_at=now)
        db.commit()
        db.refresh(first)
        assert first.model_request_id == request_id
        repository.delete_terminal_job(job_id=job.id, owner_subject="request-binding-owner")
        db.commit()


def test_curator_context_is_persisted_separately_and_immutable_while_queued():
    with SessionLocal() as db:
        job = _create_job(db, owner="service:synthetic-portal", cells=1)
        assert job.curator_context == _curator_context().model_dump(mode="json")
        assert job.curator_context["subject"] != job.owner_subject
        for replacement in (None, {**job.curator_context, "active_groups": ["group-beta"]}):
            with pytest.raises(DBAPIError, match="curator context is immutable"):
                with db.begin_nested():
                    db.execute(
                        update(BenchmarkJob).where(BenchmarkJob.id == job.id)
                        .values(curator_context=replacement)
                    )
        db.rollback()


@pytest.mark.parametrize("problem", ["missing_agent_query", "mismatched_query"])
def test_job_admission_rejects_missing_or_inconsistent_curator_query(problem):
    suite, plan = _suite_and_plan(1)
    if problem == "missing_agent_query":
        target = suite.cases[0].target.model_copy(update={"kind": "agent"})
        suite = suite.model_copy(update={"cases": (suite.cases[0].model_copy(update={"target": target}),)})
        plan = plan.model_copy(update={
            "cases": (plan.cases[0].model_copy(update={"target": target}),),
            "cells": (plan.cells[0].model_copy(update={"target": target}),),
        })
    else:
        suite = suite.model_copy(update={
            "cases": (suite.cases[0].model_copy(update={"user_query": "Curator request"}),),
        })
    with SessionLocal() as session:
        with pytest.raises(ValueError, match="query"):
            BenchmarkRepository(session).create_job(
                owner_subject="synthetic-query-owner", curator_context=_curator_context(),
                suite=suite, plan=plan, config_digest=_digest("e"), code_digest=_digest("f"),
                inputs_digest=_digest("0"), snapshot_ids_by_case={},
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
            curator_context=_curator_context(),
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
            result={"output": {"ok": True}, "invocations": []},
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
                result={"output": boundary_envelope, "invocations": []},
            )
        monkeypatch.setenv("BENCHMARK_MAX_ENVELOPE_BYTES", "16")
        finished_cell = repository.finish_cell(
            cell_id=cell.id,
            lease_owner=lease_owner,
            status=BenchmarkCellStatus.SUCCEEDED,
            completed_at=now,
            generated_envelope=boundary_envelope,
            result={"output": boundary_envelope, "invocations": []},
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
            result={"output": envelope, "invocations": []},
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


def test_cost_audit_is_read_only_complete_across_pages_and_snapshot_consistent(monkeypatch):
    from sqlalchemy import text
    from src.lib.cost_ledger.benchmark_audit import audit_benchmark_costs

    scope = dict(deployment_id="fixture-deployment", source_namespace="fixture-execution")
    job_ids = []
    try:
        with SessionLocal() as db:
            job = _create_job(db, owner="audit-private-owner", cells=3)
            job_ids.append(job.id)
            _run_to_terminal(db, job.id, billed_amount=Decimal("0.000000000000000000123"))
            db.commit()
        with SessionLocal() as snapshot:
            snapshot.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            monkeypatch.setenv("COST_MIGRATION_AUDIT_PAGE_SIZE", "1")
            first = audit_benchmark_costs(snapshot, **scope)
            assert first["planned_invocations"] >= 3
            assert {"unit": "credits", "source": "audit-fixture", "amount": "3.69E-19", "invocations": 3} in first["recorded_charge_totals"]
            assert not first["write_side_enabled"] and first["dry_run"]
            assert "audit-private-owner" not in str(first)
            monkeypatch.setenv("COST_MIGRATION_AUDIT_PAGE_SIZE", "2")
            assert audit_benchmark_costs(snapshot, **scope) == first
            with pytest.raises(DBAPIError):
                with snapshot.begin_nested():
                    snapshot.execute(text("UPDATE cost_attempts SET owner_subject = 'not-allowed'"))
            with SessionLocal() as writer:
                queued = _create_job(writer, owner="audit-queued-owner", cells=1)
                job_ids.append(queued.id)
                writer.commit()
            assert audit_benchmark_costs(snapshot, **scope) == first
        with SessionLocal() as fresh:
            fresh.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            updated = audit_benchmark_costs(fresh, **scope)
            assert updated["active_jobs"] == first["active_jobs"] + 1
            assert updated["cutover_blockers"]["active_jobs"] >= 1
        with SessionLocal() as unsafe:
            with pytest.raises(ValueError, match="read-only"):
                audit_benchmark_costs(unsafe, **scope)
    finally:
        with SessionLocal() as cleanup:
            for job_id in job_ids:
                job = cleanup.get(BenchmarkJob, job_id)
                if job.status == BenchmarkJobStatus.QUEUED:
                    _run_to_terminal(cleanup, job_id)
                BenchmarkRepository(cleanup).delete_terminal_job(job_id=job_id, owner_subject=job.owner_subject)
            cleanup.commit()


def test_accounting_read_requires_owned_membership_and_verified_binding(monkeypatch):
    from src.lib.cost_ledger.benchmark_reads import BenchmarkAccountingUnavailable, read_benchmark_accounting
    from src.lib.cost_ledger.benchmark_migration import plan_benchmark_cost_migration
    from src.lib.cost_ledger.persistence import record_cost_facts
    from src.lib.cost_ledger.facts import TokenUsage

    deployment, namespace, owner = uuid4().hex, "fixture-source", "accounting-owner"
    monkeypatch.setenv("COST_LEDGER_DEPLOYMENT_ID", deployment)
    monkeypatch.setenv("COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE", namespace)
    job_id = None
    try:
        with SessionLocal() as db:
            job = _create_job(db, owner=owner, cells=1)
            job_id = job.id
            _run_to_terminal(db, job.id, billed_amount=Decimal("7"))
            row = db.scalar(select(BenchmarkInvocation).join(BenchmarkCell).where(BenchmarkCell.job_id == job.id))
            scope = dict(job_id=job.id, cell_id=row.cell_id, invocation_id=row.id, owner_subject=owner)
            # Inline charge exists, but no ledger binding: never serve old copies.
            with pytest.raises(BenchmarkAccountingUnavailable):
                read_benchmark_accounting(db, **scope)
            entry = plan_benchmark_cost_migration(row, deployment_id=deployment, source_namespace=namespace, owner_subject=owner)
            binding = dict(deployment_id=deployment, source_namespace=namespace, owner_subject=owner,
                           attempt_id=entry.attempt_id, source_system="benchmark", source_id=str(row.id))
            record_cost_facts(db, **binding, usage=entry.usage, charge=entry.charge)
            first = read_benchmark_accounting(db, **scope)
            assert first.recorded_charge.amount == Decimal("7") and first.reference.fact_revision == 1
            record_cost_facts(db, **binding, usage=TokenUsage(input_tokens=10))
            assert read_benchmark_accounting(db, **scope).reference.fact_revision == 2
            assert read_benchmark_accounting(db, **scope, revision=1) == first
            assert read_benchmark_accounting(db, **scope, revision=0).recorded_charge is None
            for field, value in (("job_id", uuid4()), ("cell_id", uuid4()), ("invocation_id", uuid4()), ("owner_subject", "another-owner")):
                with pytest.raises(LookupError):
                    read_benchmark_accounting(db, **{**scope, field: value})
            with pytest.raises(LookupError):
                read_benchmark_accounting(db, **scope, revision=3)
            monkeypatch.setenv("COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE", "wrong-source")
            with pytest.raises(BenchmarkAccountingUnavailable):
                read_benchmark_accounting(db, **scope)
            monkeypatch.delenv("COST_LEDGER_DEPLOYMENT_ID")
            with pytest.raises(BenchmarkAccountingUnavailable):
                read_benchmark_accounting(db, **scope)
            # Membership remains the first boundary even when unconfigured.
            with pytest.raises(LookupError):
                read_benchmark_accounting(db, **{**scope, "owner_subject": "another-owner"})
            db.commit()
    finally:
        if job_id is not None:
            with SessionLocal() as db:
                BenchmarkRepository(db).delete_terminal_job(job_id=job_id, owner_subject=owner)
                db.commit()


@pytest.fixture
def cost_backfill_case(monkeypatch):
    from sqlalchemy import text
    from src.lib.cost_ledger.benchmark_audit import audit_benchmark_costs

    scope = dict(deployment_id=uuid4().hex, source_namespace="backfill-fixture")
    monkeypatch.setenv("COST_LEDGER_DEPLOYMENT_ID", scope["deployment_id"])
    monkeypatch.setenv("COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE", scope["source_namespace"])
    monkeypatch.setenv("COST_MIGRATION_AUDIT_PAGE_SIZE", "1")
    with SessionLocal() as db:
        job = _create_job(db, owner="backfill-owner", cells=2)
        _run_to_terminal(db, job.id, billed_amount=Decimal("7.000000000000000000123"))
        job_id = job.id
        db.commit()
    with SessionLocal() as audit:
        audit.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        receipt = audit_benchmark_costs(audit, **scope)
    try:
        yield scope, receipt, job_id
    finally:
        with SessionLocal() as db:
            BenchmarkRepository(db).delete_terminal_job(job_id=job_id, owner_subject="backfill-owner")
            db.commit()


def _backfill_fact_count(db, scope):
    from src.models.sql.cost_ledger import CostFactRevision
    return db.scalar(select(func.count()).select_from(CostFactRevision).where(
        CostFactRevision.deployment_id == scope["deployment_id"],
    ))


def test_offline_backfill_parity_replay_and_write_fencing(cost_backfill_case):
    from sqlalchemy import text
    from src.lib.cost_ledger.benchmark_backfill import backfill_benchmark_costs
    from src.lib.cost_ledger.benchmark_reads import read_benchmark_accounting

    scope, audit, job_id = cost_backfill_case
    args = {**scope, "expected_planned_facts_sha256": audit["planned_facts_sha256"]}
    with SessionLocal() as migration:
        receipt = backfill_benchmark_costs(migration, **args)
        assert receipt["verified_invocations"] == 2
        assert receipt["committed"] is False and receipt["writer_cutover_complete"] is False
        with SessionLocal() as reader:
            assert _backfill_fact_count(reader, scope) == 0
        with SessionLocal() as blocked_writer:
            blocked_writer.execute(text("SET LOCAL lock_timeout = '20ms'"))
            with pytest.raises(DBAPIError):
                blocked_writer.execute(text("UPDATE benchmark_invocations SET latency_ms = latency_ms WHERE false"))
        migration.commit()
    with SessionLocal() as replay:
        assert backfill_benchmark_costs(replay, **args)["verified_invocations"] == 2
        replay.commit()
    with SessionLocal() as db:
        assert _backfill_fact_count(db, scope) == 2
        for row in db.scalars(select(BenchmarkInvocation).join(BenchmarkCell).where(BenchmarkCell.job_id == job_id)):
            projection = read_benchmark_accounting(db, job_id=job_id, cell_id=row.cell_id,
                                                   invocation_id=row.id, owner_subject="backfill-owner")
            assert projection.recorded_charge.amount == row.billed_amount == Decimal("7.000000000000000000123")
            assert projection.usage.input_tokens is None


def test_backfill_fingerprint_failure_rolls_back_all_pages_even_if_caught(cost_backfill_case):
    from src.lib.cost_ledger.benchmark_backfill import BenchmarkBackfillError, backfill_benchmark_costs
    from src.models.sql.cost_ledger import CostAttempt, CostSourceReference

    scope, _, _ = cost_backfill_case
    with SessionLocal() as migration:
        with pytest.raises(BenchmarkBackfillError, match="source_fingerprint_changed"):
            backfill_benchmark_costs(migration, **scope, expected_planned_facts_sha256="0" * 64)
        migration.commit()
    with SessionLocal() as db:
        assert _backfill_fact_count(db, scope) == 0
        for model in (CostAttempt, CostSourceReference):
            assert db.scalar(select(func.count()).select_from(model).where(model.deployment_id == scope["deployment_id"])) == 0


def test_backfill_existing_fact_conflict_preserves_only_original_data(cost_backfill_case):
    from src.lib.cost_ledger.benchmark_backfill import backfill_benchmark_costs
    from src.lib.cost_ledger.benchmark_migration import plan_benchmark_cost_migration
    from src.lib.cost_ledger.facts import CostFactConflict, RecordedCharge
    from src.lib.cost_ledger.persistence import record_cost_facts

    scope, audit, job_id = cost_backfill_case
    with SessionLocal() as db:
        last = db.scalars(select(BenchmarkInvocation).join(BenchmarkCell).where(
            BenchmarkCell.job_id == job_id,
        ).order_by(BenchmarkInvocation.id.desc())).first()
        entry = plan_benchmark_cost_migration(last, **scope, owner_subject="backfill-owner")
        record_cost_facts(db, **scope, owner_subject="backfill-owner", attempt_id=entry.attempt_id,
                          source_system="benchmark", source_id=str(last.id), usage=entry.usage,
                          charge=RecordedCharge(Decimal("8"), "credits", "audit-fixture"))
        db.commit()
    with SessionLocal() as migration:
        with pytest.raises(CostFactConflict):
            backfill_benchmark_costs(migration, **scope, expected_planned_facts_sha256=audit["planned_facts_sha256"])
        migration.commit()
    with SessionLocal() as db:
        assert _backfill_fact_count(db, scope) == 1


def test_backfill_rejects_queued_jobs_and_scope_mismatch(cost_backfill_case, monkeypatch):
    from src.lib.cost_ledger.benchmark_backfill import BenchmarkBackfillError, backfill_benchmark_costs

    scope, audit, _ = cost_backfill_case
    args = {**scope, "expected_planned_facts_sha256": audit["planned_facts_sha256"]}
    with SessionLocal() as db:
        queued = _create_job(db, owner="backfill-queued", cells=1)
        queued_id = queued.id
        db.commit()
    try:
        with SessionLocal() as migration:
            with pytest.raises(BenchmarkBackfillError, match="active_benchmark_jobs"):
                backfill_benchmark_costs(migration, **args)
            migration.commit()
        monkeypatch.setenv("COST_LEDGER_DEPLOYMENT_ID", "wrong-deployment")
        with SessionLocal() as migration:
            with pytest.raises(BenchmarkBackfillError, match="verified_scope_configuration_required"):
                backfill_benchmark_costs(migration, **args)
        with SessionLocal() as db:
            assert _backfill_fact_count(db, scope) == 0
    finally:
        with SessionLocal() as db:
            _run_to_terminal(db, queued_id)
            BenchmarkRepository(db).delete_terminal_job(job_id=queued_id, owner_subject="backfill-queued")
            db.commit()


def test_backfill_refuses_prelock_repeatable_read_snapshot(cost_backfill_case):
    from sqlalchemy.orm import Session
    from src.models.sql.database import engine
    from src.lib.cost_ledger.benchmark_backfill import BenchmarkBackfillError, backfill_benchmark_costs

    scope, audit, _ = cost_backfill_case
    with Session(engine.execution_options(isolation_level="REPEATABLE READ")) as migration:
        with pytest.raises(BenchmarkBackfillError, match="read_committed_required"):
            backfill_benchmark_costs(migration, **scope, expected_planned_facts_sha256=audit["planned_facts_sha256"])
        migration.commit()
    with SessionLocal() as db:
        assert _backfill_fact_count(db, scope) == 0


@pytest.mark.parametrize("scenario", ["exact", "units", "duplicate", "unknown"])
def test_job_accounting_uses_unique_ledger_attempts_and_exact_buckets(cost_backfill_case, monkeypatch, scenario):
    from decimal import localcontext
    from sqlalchemy import text
    from src.lib.cost_ledger.benchmark_summary import read_benchmark_job_accounting
    from src.lib.cost_ledger.facts import RecordedCharge, TokenUsage
    from src.lib.cost_ledger.persistence import record_cost_facts

    scope, _, job_id = cost_backfill_case
    monkeypatch.setenv("COST_LEDGER_READ_PAGE_SIZE", "1")
    attempt_ids = [uuid4(), uuid4()]
    if scenario == "duplicate":
        attempt_ids[1] = attempt_ids[0]
    with SessionLocal() as db:
        invocations = list(db.scalars(select(BenchmarkInvocation).join(BenchmarkCell).where(BenchmarkCell.job_id == job_id)))
        for index, invocation in enumerate(invocations):
            amount = Decimal("1000000000000000000000") if index == 0 else Decimal("0.000000000000000000123")
            usage = TokenUsage(input_tokens=10, cache_read_tokens=2) if index == 0 else TokenUsage()
            charge = RecordedCharge(amount, "credits", "fixture")
            if scenario == "units" and index == 1:
                charge = RecordedCharge(Decimal(0), "USD", "other-source")
            if scenario in ("unknown", "duplicate") and index == 1:
                charge = None
            record_cost_facts(db, **scope, attempt_id=attempt_ids[index], owner_subject="backfill-owner",
                              source_system="benchmark", source_id=str(invocation.id), usage=usage, charge=charge)
        db.commit()
    with SessionLocal() as db, localcontext() as context:
        context.prec = 6
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        report = read_benchmark_job_accounting(db, job_id=job_id, owner_subject="backfill-owner")
    assert report.invocation_count == 2
    assert report.attempt_count == (1 if scenario == "duplicate" else 2)
    assert report.usage["input_tokens"].known_total == 10
    assert report.usage["input_tokens"].unknown_attempts == (0 if scenario == "duplicate" else 1)
    assert report.usage["total_tokens"].known_total is None
    assert report.unknown_charge_attempts == (1 if scenario == "unknown" else 0)
    if scenario == "exact":
        assert report.recorded_charges[0].amount == Decimal("1000000000000000000000.000000000000000000123")
        assert '"amount":"1000000000000000000000.000000000000000000123"' in report.model_dump_json()
    elif scenario == "units":
        assert len(report.recorded_charges) == 2
        assert report.recorded_charges[0].amount == 0
        assert report.recorded_charges[0].unit == "USD"
    else:
        assert report.recorded_charges[0].amount == Decimal("1000000000000000000000")


def test_job_accounting_rejects_missing_foreign_and_wrong_scope(cost_backfill_case, monkeypatch):
    from sqlalchemy import text
    from src.lib.cost_ledger.benchmark_summary import read_benchmark_job_accounting
    from src.lib.cost_ledger.benchmark_reads import BenchmarkAccountingUnavailable
    from src.lib.cost_ledger.facts import TokenUsage
    from src.lib.cost_ledger.persistence import record_cost_facts

    scope, _, job_id = cost_backfill_case
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="repeatable-read"):
            read_benchmark_job_accounting(db, job_id=job_id, owner_subject="backfill-owner")
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        for owner, target in (("foreign-owner", job_id), ("backfill-owner", uuid4())):
            with pytest.raises(LookupError):
                read_benchmark_job_accounting(db, job_id=target, owner_subject=owner)
        with pytest.raises(BenchmarkAccountingUnavailable):
            read_benchmark_job_accounting(db, job_id=job_id, owner_subject="backfill-owner")
    with SessionLocal() as db:
        invocations = list(db.scalars(select(BenchmarkInvocation).join(BenchmarkCell).where(BenchmarkCell.job_id == job_id)))
        for invocation in invocations:
            record_cost_facts(db, **scope, attempt_id=uuid4(), owner_subject="foreign-owner",
                              source_system="benchmark", source_id=str(invocation.id), usage=TokenUsage())
        db.commit()
    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        with pytest.raises(BenchmarkAccountingUnavailable):
            read_benchmark_job_accounting(db, job_id=job_id, owner_subject="backfill-owner")
        monkeypatch.setenv("COST_LEDGER_BENCHMARK_SOURCE_NAMESPACE", "wrong-source")
        with pytest.raises(BenchmarkAccountingUnavailable):
            read_benchmark_job_accounting(db, job_id=job_id, owner_subject="backfill-owner")
        monkeypatch.setenv("COST_LEDGER_DEPLOYMENT_ID", "")
        with pytest.raises(BenchmarkAccountingUnavailable):
            read_benchmark_job_accounting(db, job_id=job_id, owner_subject="backfill-owner")


def test_job_accounting_snapshot_empty_and_late_enrichment(cost_backfill_case, monkeypatch):
    from sqlalchemy import text
    from src.lib.cost_ledger.benchmark_summary import read_benchmark_job_accounting
    from src.lib.cost_ledger.facts import RecordedCharge, TokenUsage
    from src.lib.cost_ledger.persistence import record_cost_facts

    scope, _, job_id = cost_backfill_case
    monkeypatch.setenv("COST_LEDGER_READ_PAGE_SIZE", "1")
    bindings = []
    with SessionLocal() as db:
        for invocation in db.scalars(select(BenchmarkInvocation).join(BenchmarkCell).where(BenchmarkCell.job_id == job_id)):
            binding = dict(**scope, attempt_id=uuid4(), owner_subject="backfill-owner",
                           source_system="benchmark", source_id=str(invocation.id))
            record_cost_facts(db, **binding, usage=TokenUsage())
            bindings.append(binding)
        db.commit()
    with SessionLocal() as reader:
        reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        before = read_benchmark_job_accounting(reader, job_id=job_id, owner_subject="backfill-owner")
        assert before.unknown_charge_attempts == 2
        assert before.usage["input_tokens"].known_total is None
        with SessionLocal() as writer:
            record_cost_facts(writer, **bindings[0], usage=TokenUsage(input_tokens=0))
            record_cost_facts(writer, **bindings[0], usage=TokenUsage(output_tokens=3, total_tokens=3),
                              charge=RecordedCharge(Decimal(0), "credits", "fixture"))
            record_cost_facts(writer, **bindings[1], usage=TokenUsage(input_tokens=1, cache_read_tokens=2))
            writer.commit()
        assert read_benchmark_job_accounting(reader, job_id=job_id, owner_subject="backfill-owner") == before
    with SessionLocal() as reader:
        reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        after = read_benchmark_job_accounting(reader, job_id=job_id, owner_subject="backfill-owner")
        assert after.usage["input_tokens"].known_total == 1
        assert after.usage["input_tokens"].known_attempts == 2
        assert after.usage["output_tokens"].known_total == 3
        assert after.recorded_charges[0].amount == 0 and after.recorded_charges[0].attempts == 1
        assert after.unknown_charge_attempts == 1 and after.attempt_count == 2
        assert after.inconsistent_usage_attempts == 1
