"""Transactional repository for durable benchmark jobs and result envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from collections.abc import Mapping
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.lib.benchmarks.models import BenchmarkSuite, ResolvedBenchmarkPlan
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.openai_agents.config import (
    get_benchmark_default_page_size,
    get_benchmark_event_retention_count,
    get_benchmark_max_envelope_bytes,
    get_benchmark_max_page_size,
)
from src.models.sql.benchmark import (
    BenchmarkCell,
    BenchmarkCellStatus,
    BenchmarkEvent,
    BenchmarkInputSnapshot,
    BenchmarkInvocation,
    BenchmarkInvocationStatus,
    BenchmarkJob,
    BenchmarkJobIdempotency,
    BenchmarkJobInputSnapshot,
    BenchmarkJobStatus,
)


T = TypeVar("T")
CursorT = TypeVar("CursorT")
_TERMINAL_JOB_STATUSES = {
    BenchmarkJobStatus.COMPLETED,
    BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
    BenchmarkJobStatus.CANCELLED,
    BenchmarkJobStatus.FAILED,
}
_TERMINAL_CELL_STATUSES = {
    BenchmarkCellStatus.SUCCEEDED,
    BenchmarkCellStatus.FAILED,
    BenchmarkCellStatus.CANCELLED,
}


@dataclass(frozen=True)
class BenchmarkJobCursor:
    created_at: datetime
    job_id: UUID


@dataclass(frozen=True)
class BenchmarkCellCursor:
    position: int
    cell_id: UUID


@dataclass(frozen=True)
class BenchmarkJobSummary:
    id: UUID
    owner_subject: str
    status: BenchmarkJobStatus
    suite_id: str
    suite_digest: str
    catalog_digest: str
    plan_digest: str
    config_digest: str
    code_digest: str
    inputs_digest: str
    total_cells: int
    queued_cells: int
    running_cells: int
    succeeded_cells: int
    failed_cells: int
    cancelled_cells: int
    rerun_of_job_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class BenchmarkJobDetail:
    summary: BenchmarkJobSummary
    suite_specification: dict[str, Any]
    resolved_plan: dict[str, Any]
    suite_digest: str
    catalog_digest: str
    config_digest: str
    code_digest: str
    inputs_digest: str
    cancel_requested_at: datetime | None
    lease_owner: UUID | None
    lease_expires_at: datetime | None
    lease_heartbeat_at: datetime | None


@dataclass(frozen=True)
class BenchmarkCellSummary:
    id: UUID
    job_id: UUID
    cell_key: str
    position: int
    case_id: str
    configuration_id: str
    repetition: int
    status: BenchmarkCellStatus
    input_digest: str
    source_cell_id: UUID | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class BenchmarkCellDetail:
    summary: BenchmarkCellSummary
    target_kind: str
    target_id: str
    routes: dict[str, Any]
    input_resolver: str
    input_reference: str
    input_version: str
    generated_envelope: dict[str, Any] | None
    envelope_size_bytes: int | None
    envelope_digest: str | None
    result_digest: str | None
    failure: dict[str, Any] | None


@dataclass(frozen=True)
class BenchmarkPage(Generic[T, CursorT]):
    items: tuple[T, ...]
    next_cursor: CursorT | None


def _page_size(limit: int | None) -> int:
    requested = get_benchmark_default_page_size() if limit is None else limit
    if requested < 1:
        raise ValueError("benchmark page size must be positive")
    return min(requested, get_benchmark_max_page_size())


def canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class BenchmarkLeaseLostError(RuntimeError):
    """A stale worker attempted to publish after losing its durable lease."""


class BenchmarkCancellationRequestedError(RuntimeError):
    """A worker reached provider dispatch after its job was cancelled."""


class BenchmarkIdempotencyConflictError(ValueError):
    """A caller reused one operation key for different immutable work."""


def _job_summary(job: BenchmarkJob) -> BenchmarkJobSummary:
    return BenchmarkJobSummary(
        id=job.id,
        owner_subject=job.owner_subject,
        status=job.status,
        suite_id=job.suite_id,
        suite_digest=job.suite_digest,
        catalog_digest=job.catalog_digest,
        plan_digest=job.plan_digest,
        config_digest=job.config_digest,
        code_digest=job.code_digest,
        inputs_digest=job.inputs_digest,
        total_cells=job.total_cells,
        queued_cells=job.queued_cells,
        running_cells=job.running_cells,
        succeeded_cells=job.succeeded_cells,
        failed_cells=job.failed_cells,
        cancelled_cells=job.cancelled_cells,
        rerun_of_job_id=job.rerun_of_job_id,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _cell_summary(cell: BenchmarkCell) -> BenchmarkCellSummary:
    return BenchmarkCellSummary(
        id=cell.id,
        job_id=cell.job_id,
        cell_key=cell.cell_key,
        position=cell.position,
        case_id=cell.case_id,
        configuration_id=cell.configuration_id,
        repetition=cell.repetition,
        status=cell.status,
        input_digest=cell.input_digest,
        source_cell_id=cell.source_cell_id,
        created_at=cell.created_at,
        started_at=cell.started_at,
        completed_at=cell.completed_at,
    )


class BenchmarkRepository:
    """Owner-scoped transaction operations over the benchmark persistence schema."""

    def __init__(self, session: Session):
        self.session = session

    def create_job(
        self,
        *,
        owner_subject: str,
        suite: BenchmarkSuite,
        curator_context: BenchmarkCuratorContext,
        plan: ResolvedBenchmarkPlan,
        config_digest: str,
        code_digest: str,
        inputs_digest: str,
        snapshot_ids_by_case: Mapping[str, UUID],
        rerun_of_job_id: UUID | None = None,
    ) -> BenchmarkJob:
        """Persist a frozen plan and trusted context in one transaction.

        The admission layer must authenticate the curator and authorize the
        operation before calling this internal repository, including reruns.
        A typed context alone is not proof of authentication.
        """
        if not isinstance(curator_context, BenchmarkCuratorContext):
            raise ValueError("verified curator context is required for new jobs")
        if not owner_subject:
            raise ValueError("benchmark owner subject is required")
        if not plan.cells:
            raise ValueError("benchmark plan must contain at least one cell")
        if suite.suite_id != plan.suite_id:
            raise ValueError("suite and resolved plan IDs differ")
        case_ids = {case.case_id for case in plan.cases}
        suite_queries = {case.case_id: case.user_query for case in suite.cases}
        for planned_case in plan.cases:
            if planned_case.user_query != suite_queries.get(planned_case.case_id):
                raise ValueError("resolved case query differs from suite")
            if planned_case.target.kind == "agent" and not (planned_case.user_query or "").strip():
                raise ValueError("agent benchmark cases require an explicit curator query")
        for cell in plan.cells:
            if cell.user_query != suite_queries.get(cell.case_id):
                raise ValueError("resolved cell query differs from suite")
        if set(snapshot_ids_by_case) != case_ids:
            raise ValueError("every plan case must reference exactly one frozen snapshot")
        snapshots = {
            snapshot.id: snapshot
            for snapshot in self.session.scalars(
                select(BenchmarkInputSnapshot).where(
                    BenchmarkInputSnapshot.id.in_(snapshot_ids_by_case.values()),
                    BenchmarkInputSnapshot.owner_subject == owner_subject,
                )
            )
        }
        if len(snapshots) != len(set(snapshot_ids_by_case.values())):
            raise ValueError("frozen input snapshots do not exist for this owner")
        inputs_by_case = {case.case_id: case.input for case in plan.cases}
        for case_id, snapshot_id in snapshot_ids_by_case.items():
            planned_input = inputs_by_case[case_id]
            snapshot = snapshots[snapshot_id]
            if (
                snapshot.resolver_id != planned_input.resolver
                or snapshot.source_reference != planned_input.reference
                or snapshot.source_version != planned_input.version
                or snapshot.digest != planned_input.digest
            ):
                raise ValueError("frozen snapshot does not match the resolved plan")

        source_cells: dict[str, BenchmarkCell] = {}
        if rerun_of_job_id is not None:
            source_job = self.session.scalar(
                select(BenchmarkJob).where(
                    BenchmarkJob.id == rerun_of_job_id,
                    BenchmarkJob.owner_subject == owner_subject,
                )
            )
            if source_job is None:
                raise ValueError("rerun parent does not exist for this owner")
            if source_job.status not in _TERMINAL_JOB_STATUSES:
                raise ValueError("rerun parent must be terminal")
            source_cells = {
                cell.cell_key: cell
                for cell in self.session.scalars(
                    select(BenchmarkCell).where(BenchmarkCell.job_id == rerun_of_job_id)
                )
            }
            missing = {cell.cell_id for cell in plan.cells} - source_cells.keys()
            if missing:
                raise ValueError("rerun plan contains cells absent from its source job")

        job = BenchmarkJob(
            id=uuid4(),
            owner_subject=owner_subject,
            status=BenchmarkJobStatus.QUEUED,
            suite_id=suite.suite_id,
            suite_specification=suite.model_dump(mode="json"),
            resolved_plan=plan.model_dump(mode="json"),
            curator_context=curator_context.model_dump(mode="json"),
            suite_digest=plan.suite_digest,
            catalog_digest=plan.catalog_digest,
            plan_digest=plan.plan_digest,
            config_digest=config_digest,
            code_digest=code_digest,
            inputs_digest=inputs_digest,
            rerun_of_job_id=rerun_of_job_id,
            total_cells=len(plan.cells),
            queued_cells=len(plan.cells),
            running_cells=0,
            succeeded_cells=0,
            failed_cells=0,
            cancelled_cells=0,
        )
        self.session.add(job)
        self.session.flush()
        self.session.add_all(
            BenchmarkJobInputSnapshot(
                job_id=job.id, case_id=case_id, snapshot_id=snapshot_id
            )
            for case_id, snapshot_id in snapshot_ids_by_case.items()
        )
        for position, planned in enumerate(plan.cells):
            source = source_cells.get(planned.cell_id)
            self.session.add(
                BenchmarkCell(
                    id=uuid4(),
                    job_id=job.id,
                    cell_key=planned.cell_id,
                    position=position,
                    case_id=planned.case_id,
                    configuration_id=planned.configuration_id,
                    repetition=planned.repetition,
                    target_kind=planned.target.kind,
                    target_id=planned.target.id,
                    routes={
                        slot: route.model_dump(mode="json")
                        for slot, route in planned.routes.items()
                    },
                    input_resolver=planned.input.resolver,
                    input_reference=planned.input.reference,
                    input_version=planned.input.version,
                    input_digest=planned.input.digest,
                    input_snapshot_id=snapshot_ids_by_case[planned.case_id],
                    status=BenchmarkCellStatus.QUEUED,
                    attempt_count=0,
                    source_cell_id=source.id if source else None,
                    source_job_id=source.job_id if source else None,
                )
            )
        self.session.flush()
        return job

    def reserve_idempotency(
        self,
        *,
        owner_subject: str,
        operation: str,
        idempotency_key: str,
        request_digest: str,
        curator_context_digest: str,
    ) -> tuple[BenchmarkJobIdempotency, bool]:
        """Atomically reserve a key or lock and return its completed outcome."""

        if operation not in {"submit", "rerun"}:
            raise ValueError("unsupported benchmark idempotency operation")
        inserted_id = self.session.scalar(
            insert(BenchmarkJobIdempotency)
            .values(
                id=uuid4(),
                owner_subject=owner_subject,
                operation=operation,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                curator_context_digest=curator_context_digest,
                outcome="pending",
            )
            .on_conflict_do_nothing(
                constraint="uq_benchmark_job_idempotency_owner_operation_key"
            )
            .returning(BenchmarkJobIdempotency.id)
        )
        if inserted_id is not None:
            record = self.session.get(BenchmarkJobIdempotency, inserted_id)
            if record is None:
                raise RuntimeError("benchmark idempotency reservation disappeared")
            return record, True

        record = self.session.scalar(
            select(BenchmarkJobIdempotency)
            .where(
                BenchmarkJobIdempotency.owner_subject == owner_subject,
                BenchmarkJobIdempotency.operation == operation,
                BenchmarkJobIdempotency.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        if record is None:
            raise RuntimeError("benchmark idempotency reservation is unavailable")
        if (
            record.request_digest != request_digest
            or record.curator_context_digest != curator_context_digest
        ):
            raise BenchmarkIdempotencyConflictError(
                "Idempotency key is already bound to a different request or curator context"
            )
        if record.outcome == "pending":
            raise RuntimeError("benchmark idempotency reservation has no durable outcome")
        return record, False

    def accept_idempotency(
        self, *, reservation: BenchmarkJobIdempotency, job_id: UUID
    ) -> None:
        if reservation.outcome != "pending":
            raise ValueError("benchmark idempotency reservation is already complete")
        reservation.outcome = "accepted"
        reservation.job_id = job_id
        self.session.flush()

    def fail_idempotency(
        self,
        *,
        reservation: BenchmarkJobIdempotency,
        error_code: str,
        error_message: str,
        error_status: int,
    ) -> None:
        if reservation.outcome != "pending":
            raise ValueError("benchmark idempotency reservation is already complete")
        reservation.outcome = "failed"
        reservation.error_code = error_code
        reservation.error_message = error_message
        reservation.error_status = error_status
        self.session.flush()

    def list_jobs(
        self,
        *,
        owner_subject: str,
        status: BenchmarkJobStatus | None = None,
        cursor: BenchmarkJobCursor | None = None,
        limit: int | None = None,
    ) -> BenchmarkPage[BenchmarkJobSummary, BenchmarkJobCursor]:
        size = _page_size(limit)
        statement = select(BenchmarkJob).where(
            BenchmarkJob.owner_subject == owner_subject
        )
        if status is not None:
            statement = statement.where(BenchmarkJob.status == status)
        if cursor is not None:
            statement = statement.where(
                or_(
                    BenchmarkJob.created_at < cursor.created_at,
                    (
                        (BenchmarkJob.created_at == cursor.created_at)
                        & (BenchmarkJob.id < cursor.job_id)
                    ),
                )
            )
        rows = self.session.scalars(
            statement.order_by(BenchmarkJob.created_at.desc(), BenchmarkJob.id.desc()).limit(
                size + 1
            )
        ).all()
        page_rows = rows[:size]
        next_cursor = (
            BenchmarkJobCursor(page_rows[-1].created_at, page_rows[-1].id)
            if len(rows) > size
            else None
        )
        return BenchmarkPage(tuple(_job_summary(row) for row in page_rows), next_cursor)

    def get_job(
        self, *, job_id: UUID, owner_subject: str
    ) -> BenchmarkJobDetail | None:
        job = self.session.scalar(
            select(BenchmarkJob).where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.owner_subject == owner_subject,
            )
        )
        if job is None:
            return None
        return BenchmarkJobDetail(
            summary=_job_summary(job),
            suite_specification=job.suite_specification,
            resolved_plan=job.resolved_plan,
            suite_digest=job.suite_digest,
            catalog_digest=job.catalog_digest,
            config_digest=job.config_digest,
            code_digest=job.code_digest,
            inputs_digest=job.inputs_digest,
            cancel_requested_at=job.cancel_requested_at,
            lease_owner=job.lease_owner,
            lease_expires_at=job.lease_expires_at,
            lease_heartbeat_at=job.lease_heartbeat_at,
        )

    def list_cells(
        self,
        *,
        job_id: UUID,
        owner_subject: str,
        cursor: BenchmarkCellCursor | None = None,
        limit: int | None = None,
    ) -> BenchmarkPage[BenchmarkCellSummary, BenchmarkCellCursor]:
        self._owned_job(job_id, owner_subject)
        size = _page_size(limit)
        statement = select(BenchmarkCell).where(BenchmarkCell.job_id == job_id)
        if cursor is not None:
            statement = statement.where(
                or_(
                    BenchmarkCell.position > cursor.position,
                    (
                        (BenchmarkCell.position == cursor.position)
                        & (BenchmarkCell.id > cursor.cell_id)
                    ),
                )
            )
        rows = self.session.scalars(
            statement.order_by(BenchmarkCell.position, BenchmarkCell.id).limit(size + 1)
        ).all()
        page_rows = rows[:size]
        next_cursor = (
            BenchmarkCellCursor(page_rows[-1].position, page_rows[-1].id)
            if len(rows) > size
            else None
        )
        return BenchmarkPage(tuple(_cell_summary(row) for row in page_rows), next_cursor)

    def get_cell(
        self, *, cell_id: UUID, job_id: UUID, owner_subject: str
    ) -> BenchmarkCellDetail | None:
        self._owned_job(job_id, owner_subject)
        cell = self.session.scalar(
            select(BenchmarkCell).where(
                BenchmarkCell.id == cell_id, BenchmarkCell.job_id == job_id
            )
        )
        if cell is None:
            return None
        return BenchmarkCellDetail(
            summary=_cell_summary(cell),
            target_kind=cell.target_kind,
            target_id=cell.target_id,
            routes=cell.routes,
            input_resolver=cell.input_resolver,
            input_reference=cell.input_reference,
            input_version=cell.input_version,
            generated_envelope=cell.generated_envelope,
            envelope_size_bytes=cell.envelope_size_bytes,
            envelope_digest=cell.envelope_digest,
            result_digest=cell.result_digest,
            failure=cell.failure,
        )

    def claim_next_job(
        self, *, lease_owner: UUID, lease_expires_at: datetime, now: datetime | None = None
    ) -> BenchmarkJob | None:
        current = now or datetime.now(timezone.utc)
        job = self.session.scalar(
            select(BenchmarkJob)
            .where(
                or_(
                    BenchmarkJob.status == BenchmarkJobStatus.QUEUED,
                    (
                        BenchmarkJob.status == BenchmarkJobStatus.RUNNING
                    )
                    & (BenchmarkJob.lease_expires_at < current),
                    (
                        BenchmarkJob.status == BenchmarkJobStatus.CANCEL_REQUESTED
                    )
                    & (BenchmarkJob.lease_expires_at < current),
                )
            )
            .order_by(
                case((BenchmarkJob.status == BenchmarkJobStatus.QUEUED, 0), else_=1),
                BenchmarkJob.created_at,
                BenchmarkJob.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        if job.status != BenchmarkJobStatus.CANCEL_REQUESTED:
            job.status = BenchmarkJobStatus.RUNNING
        job.started_at = job.started_at or current
        job.lease_owner = lease_owner
        job.lease_expires_at = lease_expires_at
        job.lease_heartbeat_at = current
        self.session.flush()
        return job

    def claim_next_cell(
        self,
        *,
        job_id: UUID,
        lease_owner: UUID,
        lease_expires_at: datetime,
        now: datetime | None = None,
    ) -> BenchmarkCell | None:
        current = now or datetime.now(timezone.utc)
        job = self.session.scalar(
            select(BenchmarkJob)
            .where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.status == BenchmarkJobStatus.RUNNING,
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > current,
            )
            .with_for_update()
        )
        if job is None:
            raise BenchmarkLeaseLostError("benchmark job lease is no longer owned")
        cell = self.session.scalar(
            select(BenchmarkCell)
            .where(
                BenchmarkCell.job_id == job_id,
                BenchmarkCell.status == BenchmarkCellStatus.QUEUED,
            )
            .order_by(
                BenchmarkCell.position,
                BenchmarkCell.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if cell is None:
            return None
        cell.attempt_count += 1
        cell.status = BenchmarkCellStatus.RUNNING
        cell.started_at = cell.started_at or current
        cell.lease_owner = lease_owner
        cell.lease_expires_at = lease_expires_at
        cell.lease_heartbeat_at = current
        self.session.flush()
        self._refresh_job_counters(job_id)
        return cell

    def recover_expired_cells(self, *, now: datetime | None = None) -> tuple[UUID, ...]:
        """Atomically fail expired paid work without making it claimable again."""

        current = now or datetime.now(timezone.utc)
        candidate_job_ids = tuple(
            self.session.scalars(
                select(BenchmarkCell.job_id)
                .where(
                    BenchmarkCell.status == BenchmarkCellStatus.RUNNING,
                    or_(
                        BenchmarkCell.lease_expires_at.is_(None),
                        BenchmarkCell.lease_expires_at <= current,
                    ),
                )
                .distinct()
                .order_by(BenchmarkCell.job_id)
            )
        )
        if not candidate_job_ids:
            return ()
        tuple(
            self.session.scalars(
                select(BenchmarkJob)
                .where(BenchmarkJob.id.in_(candidate_job_ids))
                .order_by(BenchmarkJob.id)
                .with_for_update()
            )
        )
        cells = tuple(
            self.session.scalars(
                select(BenchmarkCell)
                .where(
                    BenchmarkCell.job_id.in_(candidate_job_ids),
                    BenchmarkCell.status == BenchmarkCellStatus.RUNNING,
                    or_(
                        BenchmarkCell.lease_expires_at.is_(None),
                        BenchmarkCell.lease_expires_at <= current,
                    ),
                )
                .order_by(BenchmarkCell.job_id, BenchmarkCell.position)
                .with_for_update(skip_locked=True)
            )
        )
        affected_jobs: set[UUID] = set()
        failure = {"category": "interrupted_uncertain", "retryable": False}
        for cell in cells:
            self.session.execute(
                update(BenchmarkInvocation)
                .where(
                    BenchmarkInvocation.cell_id == cell.id,
                    BenchmarkInvocation.status == BenchmarkInvocationStatus.RUNNING,
                )
                .values(
                    status=BenchmarkInvocationStatus.FAILED,
                    completed_at=current,
                    failure=failure,
                )
            )
            cell.status = BenchmarkCellStatus.FAILED
            cell.completed_at = current
            cell.generated_envelope = None
            cell.envelope_size_bytes = None
            cell.envelope_digest = None
            cell.result_digest = None
            cell.failure = failure
            cell.lease_owner = None
            cell.lease_expires_at = None
            cell.lease_heartbeat_at = None
            affected_jobs.add(cell.job_id)
        self.session.flush()
        for job_id in affected_jobs:
            self._refresh_job_counters(job_id)
        return tuple(cell.id for cell in cells)

    def heartbeat_leases(
        self,
        *,
        job_id: UUID,
        cell_id: UUID,
        lease_owner: UUID,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        expires = current + timedelta(seconds=lease_seconds)
        job = self.session.scalar(
            select(BenchmarkJob)
            .where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.status.in_(
                    (BenchmarkJobStatus.RUNNING, BenchmarkJobStatus.CANCEL_REQUESTED)
                ),
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > current,
            )
            .with_for_update()
        )
        cell = self.session.scalar(
            select(BenchmarkCell)
            .where(
                BenchmarkCell.id == cell_id,
                BenchmarkCell.job_id == job_id,
                BenchmarkCell.status == BenchmarkCellStatus.RUNNING,
                BenchmarkCell.lease_owner == lease_owner,
                BenchmarkCell.lease_expires_at > current,
            )
            .with_for_update()
        )
        if job is None or cell is None:
            return False
        job.lease_expires_at = expires
        job.lease_heartbeat_at = current
        cell.lease_expires_at = expires
        cell.lease_heartbeat_at = current
        self.session.flush()
        return True

    def finish_cell(
        self,
        *,
        cell_id: UUID,
        lease_owner: UUID,
        status: BenchmarkCellStatus,
        completed_at: datetime,
        generated_envelope: dict[str, Any] | None = None,
        result: Any | None = None,
        failure: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> BenchmarkCell:
        if status not in _TERMINAL_CELL_STATUSES:
            raise ValueError("finish_cell requires a terminal cell status")
        current = now or datetime.now(timezone.utc)
        job = self.session.scalar(
            select(BenchmarkJob)
            .join(BenchmarkCell, BenchmarkCell.job_id == BenchmarkJob.id)
            .where(
                BenchmarkCell.id == cell_id,
                BenchmarkJob.status.in_(
                    (BenchmarkJobStatus.RUNNING, BenchmarkJobStatus.CANCEL_REQUESTED)
                ),
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > current,
            )
            .with_for_update(of=BenchmarkJob)
        )
        if job is None:
            raise BenchmarkLeaseLostError("benchmark job lease is no longer owned")
        cell = self.session.scalar(
            select(BenchmarkCell)
            .where(
                BenchmarkCell.id == cell_id,
                BenchmarkCell.status == BenchmarkCellStatus.RUNNING,
                BenchmarkCell.lease_owner == lease_owner,
                BenchmarkCell.lease_expires_at > current,
            )
            .with_for_update()
        )
        if cell is None:
            raise BenchmarkLeaseLostError("benchmark cell lease is no longer owned")
        running_invocation = self.session.scalar(
            select(BenchmarkInvocation.id)
            .where(
                BenchmarkInvocation.cell_id == cell_id,
                BenchmarkInvocation.status == BenchmarkInvocationStatus.RUNNING,
            )
            .with_for_update()
            .limit(1)
        )
        if running_invocation is not None:
            raise ValueError("benchmark cell has running invocations")
        envelope_size = None
        if status == BenchmarkCellStatus.SUCCEEDED:
            if generated_envelope is None:
                raise ValueError("successful benchmark cell requires an envelope")
            if result is None:
                raise ValueError("successful benchmark cell requires a result")
            envelope_size = self.session.scalar(
                text(
                    "SELECT octet_length("
                    "convert_to(CAST(:payload AS jsonb)::text, 'UTF8')"
                    ")"
                ),
                {"payload": json.dumps(generated_envelope, ensure_ascii=False)},
            )
            if envelope_size > get_benchmark_max_envelope_bytes():
                raise ValueError("generated benchmark envelope exceeds configured byte limit")
        elif generated_envelope is not None:
            raise ValueError("only successful benchmark cells may store envelopes")
        elif result is not None:
            raise ValueError("only successful benchmark cells may store result digests")
        if status == BenchmarkCellStatus.FAILED and failure is None:
            raise ValueError("failed benchmark cell requires a failure object")
        if status != BenchmarkCellStatus.FAILED and failure is not None:
            raise ValueError("only failed benchmark cells may store failures")

        cell.status = status
        cell.completed_at = completed_at
        cell.generated_envelope = generated_envelope
        cell.envelope_size_bytes = envelope_size
        cell.envelope_digest = (
            canonical_digest(generated_envelope) if generated_envelope is not None else None
        )
        cell.result_digest = canonical_digest(result) if result is not None else None
        cell.failure = failure
        cell.lease_owner = None
        cell.lease_expires_at = None
        cell.lease_heartbeat_at = None
        self.session.execute(
            text(
                "SELECT set_config('app.benchmark_max_envelope_bytes', :limit, true)"
            ),
            {"limit": str(get_benchmark_max_envelope_bytes())},
        )
        self.session.flush()
        if generated_envelope is not None:
            self.session.refresh(cell, attribute_names=["envelope_size_bytes"])
        self._refresh_job_counters(cell.job_id)
        return cell

    def append_invocation(
        self,
        *,
        cell_id: UUID,
        lease_owner: UUID,
        ordinal: int,
        attempt: int,
        route_slot: str,
        request_digest: str,
        requested_provider: str,
        requested_model: str,
        reasoning_effort: str | None,
        sequence: int,
        started_at: datetime,
        now: datetime | None = None,
    ) -> BenchmarkInvocation:
        current = now or datetime.now(timezone.utc)
        job = self.session.scalar(
            select(BenchmarkJob)
            .join(BenchmarkCell, BenchmarkCell.job_id == BenchmarkJob.id)
            .where(
                BenchmarkCell.id == cell_id,
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > current,
            )
            .with_for_update(of=BenchmarkJob)
        )
        if job is None:
            raise BenchmarkLeaseLostError("benchmark job lease is no longer owned")
        if job.status == BenchmarkJobStatus.CANCEL_REQUESTED:
            raise BenchmarkCancellationRequestedError(
                "benchmark cancellation was requested before provider dispatch"
            )
        if job.status != BenchmarkJobStatus.RUNNING:
            raise BenchmarkLeaseLostError("benchmark job is no longer active")
        cell = self.session.scalar(
            select(BenchmarkCell)
            .where(
                BenchmarkCell.id == cell_id,
                BenchmarkCell.status == BenchmarkCellStatus.RUNNING,
                BenchmarkCell.lease_owner == lease_owner,
                BenchmarkCell.lease_expires_at > current,
            )
            .with_for_update()
        )
        if cell is None:
            raise BenchmarkLeaseLostError("benchmark cell lease is no longer owned")
        invocation = BenchmarkInvocation(
            id=uuid4(),
            cell_id=cell_id,
            ordinal=ordinal,
            attempt=attempt,
            route_slot=route_slot,
            request_digest=request_digest,
            requested_provider=requested_provider,
            requested_model=requested_model,
            reasoning_effort=reasoning_effort,
            sequence=sequence,
            status=BenchmarkInvocationStatus.RUNNING,
            started_at=started_at,
        )
        self.session.add(invocation)
        self.session.flush()
        return invocation

    def finish_invocation(
        self,
        *,
        invocation_id: UUID,
        lease_owner: UUID,
        status: BenchmarkInvocationStatus,
        completed_at: datetime,
        response_digest: str | None = None,
        actual_provider: str | None = None,
        actual_model: str | None = None,
        routing_attempt: int | None = None,
        latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        billed_amount: Decimal | None = None,
        billed_unit: str | None = None,
        billed_source: str | None = None,
        failure: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> BenchmarkInvocation:
        if status == BenchmarkInvocationStatus.RUNNING:
            raise ValueError("finish_invocation requires a terminal status")
        current = now or datetime.now(timezone.utc)
        cell_id = self.session.scalar(
            select(BenchmarkInvocation.cell_id).where(
                BenchmarkInvocation.id == invocation_id
            )
        )
        if cell_id is None:
            raise LookupError("benchmark invocation not found")
        job = self.session.scalar(
            select(BenchmarkJob)
            .join(BenchmarkCell, BenchmarkCell.job_id == BenchmarkJob.id)
            .where(
                BenchmarkCell.id == cell_id,
                BenchmarkJob.status.in_(
                    (BenchmarkJobStatus.RUNNING, BenchmarkJobStatus.CANCEL_REQUESTED)
                ),
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > current,
            )
            .with_for_update(of=BenchmarkJob)
        )
        if job is None:
            raise BenchmarkLeaseLostError("benchmark job lease is no longer owned")
        cell = self.session.scalar(
            select(BenchmarkCell)
            .where(
                BenchmarkCell.id == cell_id,
                BenchmarkCell.status == BenchmarkCellStatus.RUNNING,
                BenchmarkCell.lease_owner == lease_owner,
                BenchmarkCell.lease_expires_at > current,
            )
            .with_for_update()
        )
        if cell is None:
            raise BenchmarkLeaseLostError("benchmark cell lease is no longer owned")
        invocation = self.session.scalar(
            select(BenchmarkInvocation)
            .where(BenchmarkInvocation.id == invocation_id)
            .with_for_update()
        )
        if invocation is None:
            raise LookupError("benchmark invocation not found")
        if invocation.status != BenchmarkInvocationStatus.RUNNING:
            raise ValueError("only a running benchmark invocation may finish")
        if status == BenchmarkInvocationStatus.SUCCEEDED:
            if response_digest is None or failure is not None:
                raise ValueError("successful invocation requires only a response digest")
        elif status == BenchmarkInvocationStatus.FAILED:
            if failure is None or response_digest is not None:
                raise ValueError("failed invocation requires only a failure object")
        elif response_digest is not None or failure is not None:
            raise ValueError("cancelled invocation stores no response or failure")
        invocation.status = status
        invocation.completed_at = completed_at
        invocation.response_digest = response_digest
        invocation.failure = failure
        invocation.actual_provider = actual_provider
        invocation.actual_model = actual_model
        invocation.routing_attempt = routing_attempt
        invocation.latency_ms = latency_ms
        invocation.input_tokens = input_tokens
        invocation.output_tokens = output_tokens
        invocation.total_tokens = total_tokens
        invocation.billed_amount = billed_amount
        invocation.billed_unit = billed_unit
        invocation.billed_source = billed_source
        self.session.flush()
        return invocation

    def list_invocations(
        self,
        *,
        job_id: UUID,
        cell_id: UUID,
        owner_subject: str,
        after_ordinal: int = -1,
        limit: int | None = None,
    ) -> tuple[BenchmarkInvocation, ...]:
        self._owned_job(job_id, owner_subject)
        if not self.session.scalar(
            select(BenchmarkCell.id).where(
                BenchmarkCell.id == cell_id, BenchmarkCell.job_id == job_id
            )
        ):
            raise LookupError("benchmark cell not found in owned job")
        size = _page_size(limit)
        return tuple(
            self.session.scalars(
                select(BenchmarkInvocation)
                .where(
                    BenchmarkInvocation.cell_id == cell_id,
                    BenchmarkInvocation.ordinal > after_ordinal,
                )
                .order_by(BenchmarkInvocation.ordinal, BenchmarkInvocation.id)
                .limit(size)
            )
        )

    def append_event(
        self, *, job_id: UUID, event_type: str, payload: dict[str, Any]
    ) -> BenchmarkEvent:
        job = self.session.scalar(
            select(BenchmarkJob).where(BenchmarkJob.id == job_id).with_for_update()
        )
        if job is None:
            raise LookupError("benchmark job not found")
        if job.status in _TERMINAL_JOB_STATUSES:
            raise ValueError("terminal benchmark jobs cannot accept events")
        sequence = (
            self.session.scalar(
                select(func.coalesce(func.max(BenchmarkEvent.sequence), 0)).where(
                    BenchmarkEvent.job_id == job_id
                )
            )
            + 1
        )
        event = BenchmarkEvent(
            job_id=job_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(event)
        self.session.flush()
        self._prune_ordinary_events(job_id)
        return event

    def _prune_ordinary_events(self, job_id: UUID) -> None:
        """Bound ordinary replay history without removing preparation receipts."""

        protected = (
            "document_preparation.started",
            "document_preparation.completed",
        )
        retained_ids = (
            select(BenchmarkEvent.id)
            .where(
                BenchmarkEvent.job_id == job_id,
                BenchmarkEvent.event_type.not_in(protected),
            )
            .order_by(BenchmarkEvent.sequence.desc(), BenchmarkEvent.id.desc())
            .limit(get_benchmark_event_retention_count())
        )
        self.session.execute(
            delete(BenchmarkEvent).where(
                BenchmarkEvent.job_id == job_id,
                BenchmarkEvent.event_type.not_in(protected),
                BenchmarkEvent.id.not_in(retained_ids),
            )
        )

    def request_cancellation(
        self, *, job_id: UUID, owner_subject: str, requested_at: datetime
    ) -> BenchmarkJob:
        job = self.session.scalar(
            select(BenchmarkJob)
            .where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.owner_subject == owner_subject,
            )
            .with_for_update()
        )
        if job is None:
            raise LookupError("benchmark job not found for owner")
        if job.status in _TERMINAL_JOB_STATUSES or job.status == BenchmarkJobStatus.CANCEL_REQUESTED:
            return job
        if job.status == BenchmarkJobStatus.QUEUED:
            self.session.execute(
                update(BenchmarkCell)
                .where(
                    BenchmarkCell.job_id == job_id,
                    BenchmarkCell.status == BenchmarkCellStatus.QUEUED,
                )
                .values(
                    status=BenchmarkCellStatus.CANCELLED,
                    completed_at=requested_at,
                )
            )
            job.status = BenchmarkJobStatus.CANCELLED
            job.cancel_requested_at = requested_at
            job.completed_at = requested_at
            self._refresh_job_counters(job_id)
        elif job.status == BenchmarkJobStatus.RUNNING:
            job.status = BenchmarkJobStatus.CANCEL_REQUESTED
            job.cancel_requested_at = requested_at
        self.session.flush()
        return job

    def cancellation_requested(
        self, *, job_id: UUID, lease_owner: UUID, now: datetime | None = None
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        status = self.session.scalar(
            select(BenchmarkJob.status).where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > current,
            )
        )
        if status is None:
            raise BenchmarkLeaseLostError("benchmark job lease is no longer owned")
        return status == BenchmarkJobStatus.CANCEL_REQUESTED

    def cancel_queued_cells(
        self,
        *,
        job_id: UUID,
        lease_owner: UUID,
        cancelled_at: datetime,
        now: datetime | None = None,
    ) -> int:
        current = now or datetime.now(timezone.utc)
        job = self.session.scalar(
            select(BenchmarkJob)
            .where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.status == BenchmarkJobStatus.CANCEL_REQUESTED,
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > current,
            )
            .with_for_update()
        )
        if job is None:
            raise BenchmarkLeaseLostError("benchmark job lease is no longer owned")
        count = self.session.execute(
            update(BenchmarkCell)
            .where(
                BenchmarkCell.job_id == job_id,
                BenchmarkCell.status == BenchmarkCellStatus.QUEUED,
            )
            .values(status=BenchmarkCellStatus.CANCELLED, completed_at=cancelled_at)
        ).rowcount
        self.session.flush()
        self._refresh_job_counters(job_id)
        return count

    def complete_job(
        self,
        *,
        job_id: UUID,
        lease_owner: UUID,
        completed_at: datetime,
        now: datetime | None = None,
    ) -> BenchmarkJob:
        """Seal a fully processed job with its counter-derived terminal outcome."""
        current = now or datetime.now(timezone.utc)
        job = self.session.scalar(
            select(BenchmarkJob)
            .where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.lease_owner == lease_owner,
                BenchmarkJob.lease_expires_at > current,
            )
            .with_for_update()
        )
        if job is None:
            raise BenchmarkLeaseLostError("benchmark job lease is no longer owned")
        self._refresh_job_counters(job_id)
        if job.queued_cells or job.running_cells:
            raise ValueError("benchmark job still has unfinished cells")
        if job.status not in {
            BenchmarkJobStatus.RUNNING,
            BenchmarkJobStatus.CANCEL_REQUESTED,
        }:
            raise ValueError("only an active benchmark job may complete")
        if job.status == BenchmarkJobStatus.CANCEL_REQUESTED:
            status = BenchmarkJobStatus.CANCELLED
        elif job.failed_cells:
            status = BenchmarkJobStatus.COMPLETED_WITH_FAILURES
        else:
            status = BenchmarkJobStatus.COMPLETED
        job.status = status
        job.completed_at = completed_at
        job.lease_owner = None
        job.lease_expires_at = None
        job.lease_heartbeat_at = None
        self.session.flush()
        return job

    def replay_events(
        self,
        *,
        job_id: UUID,
        owner_subject: str,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> tuple[BenchmarkEvent, ...]:
        self._owned_job(job_id, owner_subject)
        size = _page_size(limit)
        return tuple(
            self.session.scalars(
                select(BenchmarkEvent)
                .where(
                    BenchmarkEvent.job_id == job_id,
                    BenchmarkEvent.sequence > after_sequence,
                )
                .order_by(BenchmarkEvent.sequence, BenchmarkEvent.id)
                .limit(size)
            )
        )

    def delete_terminal_job(self, *, job_id: UUID, owner_subject: str) -> bool:
        """Delete a terminal SQL-only job, retaining prepared-copy recovery IDs.

        Terminal state does not prove external preparation writes are quiescent.
        Prepared jobs need coordinated vector/file/SQL cleanup before journal
        deletion, which this synchronous repository method cannot provide.
        """
        job = self.session.scalar(
            select(BenchmarkJob)
            .where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.owner_subject == owner_subject,
            )
            .with_for_update()
        )
        if job is None:
            return False
        if job.status not in _TERMINAL_JOB_STATUSES:
            raise ValueError("only terminal benchmark jobs may be deleted")
        has_reruns = self.session.scalar(
            select(
                select(BenchmarkJob.id)
                .where(BenchmarkJob.rerun_of_job_id == job.id)
                .exists()
            )
        )
        if has_reruns:
            raise ValueError("benchmark jobs with rerun lineage may not be deleted")
        has_preparation = self.session.scalar(
            select(select(BenchmarkEvent.id).where(
                BenchmarkEvent.job_id == job.id,
                BenchmarkEvent.event_type == "document_preparation.started",
            ).exists())
        )
        if has_preparation:
            raise ValueError("prepared benchmark jobs require coordinated document cleanup before deletion")
        self.session.execute(delete(BenchmarkJob).where(BenchmarkJob.id == job.id))
        self.session.flush()
        return True

    def _owned_job(self, job_id: UUID, owner_subject: str) -> BenchmarkJob:
        job = self.session.scalar(
            select(BenchmarkJob).where(
                BenchmarkJob.id == job_id,
                BenchmarkJob.owner_subject == owner_subject,
            )
        )
        if job is None:
            raise LookupError("benchmark job not found for owner")
        return job

    def _refresh_job_counters(self, job_id: UUID) -> None:
        job = self.session.scalar(
            select(BenchmarkJob).where(BenchmarkJob.id == job_id).with_for_update()
        )
        if job is None:
            raise LookupError("benchmark job not found")
        counts = dict(
            self.session.execute(
                select(BenchmarkCell.status, func.count(BenchmarkCell.id))
                .where(BenchmarkCell.job_id == job_id)
                .group_by(BenchmarkCell.status)
            ).all()
        )
        job.queued_cells = counts.get(BenchmarkCellStatus.QUEUED, 0)
        job.running_cells = counts.get(BenchmarkCellStatus.RUNNING, 0)
        job.succeeded_cells = counts.get(BenchmarkCellStatus.SUCCEEDED, 0)
        job.failed_cells = counts.get(BenchmarkCellStatus.FAILED, 0)
        job.cancelled_cells = counts.get(BenchmarkCellStatus.CANCELLED, 0)
        self.session.flush()
