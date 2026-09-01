"""Transactional repository for durable benchmark jobs and result envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from collections.abc import Mapping
from typing import Any, Generic, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, or_, select, text
from sqlalchemy.orm import Session

from src.lib.benchmarks.models import BenchmarkSuite, ResolvedBenchmarkPlan
from src.lib.openai_agents.config import (
    get_benchmark_default_page_size,
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
    plan_digest: str
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


def _job_summary(job: BenchmarkJob) -> BenchmarkJobSummary:
    return BenchmarkJobSummary(
        id=job.id,
        owner_subject=job.owner_subject,
        status=job.status,
        suite_id=job.suite_id,
        plan_digest=job.plan_digest,
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
        plan: ResolvedBenchmarkPlan,
        config_digest: str,
        code_digest: str,
        inputs_digest: str,
        snapshot_ids_by_case: Mapping[str, UUID],
        rerun_of_job_id: UUID | None = None,
    ) -> BenchmarkJob:
        """Persist a frozen plan and all cells in one caller-owned transaction."""
        if not owner_subject:
            raise ValueError("benchmark owner subject is required")
        if not plan.cells:
            raise ValueError("benchmark plan must contain at least one cell")
        if suite.suite_id != plan.suite_id:
            raise ValueError("suite and resolved plan IDs differ")
        case_ids = {case.case_id for case in plan.cases}
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
        cell = self.session.scalar(
            select(BenchmarkCell)
            .where(
                BenchmarkCell.job_id == job_id,
                or_(
                    BenchmarkCell.status == BenchmarkCellStatus.QUEUED,
                    (
                        BenchmarkCell.status == BenchmarkCellStatus.RUNNING
                    )
                    & (BenchmarkCell.lease_expires_at < current),
                ),
            )
            .order_by(
                case((BenchmarkCell.status == BenchmarkCellStatus.QUEUED, 0), else_=1),
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

    def finish_cell(
        self,
        *,
        cell_id: UUID,
        status: BenchmarkCellStatus,
        completed_at: datetime,
        generated_envelope: dict[str, Any] | None = None,
        failure: dict[str, Any] | None = None,
    ) -> BenchmarkCell:
        if status not in _TERMINAL_CELL_STATUSES:
            raise ValueError("finish_cell requires a terminal cell status")
        cell = self.session.scalar(
            select(BenchmarkCell).where(BenchmarkCell.id == cell_id).with_for_update()
        )
        if cell is None:
            raise LookupError("benchmark cell not found")
        if cell.status != BenchmarkCellStatus.RUNNING:
            raise ValueError("only a running benchmark cell may finish")
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
        if status == BenchmarkCellStatus.FAILED and failure is None:
            raise ValueError("failed benchmark cell requires a failure object")
        if status != BenchmarkCellStatus.FAILED and failure is not None:
            raise ValueError("only failed benchmark cells may store failures")

        cell.status = status
        cell.completed_at = completed_at
        cell.generated_envelope = generated_envelope
        cell.envelope_size_bytes = envelope_size
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
        ordinal: int,
        attempt: int,
        route_slot: str,
        request_digest: str,
        started_at: datetime,
    ) -> BenchmarkInvocation:
        cell = self.session.scalar(
            select(BenchmarkCell)
            .where(BenchmarkCell.id == cell_id)
            .with_for_update()
        )
        if cell is None:
            raise LookupError("benchmark cell not found")
        if cell.status != BenchmarkCellStatus.RUNNING:
            raise ValueError("benchmark invocations require a running cell")
        invocation = BenchmarkInvocation(
            id=uuid4(),
            cell_id=cell_id,
            ordinal=ordinal,
            attempt=attempt,
            route_slot=route_slot,
            request_digest=request_digest,
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
        status: BenchmarkInvocationStatus,
        completed_at: datetime,
        response_digest: str | None = None,
        failure: dict[str, Any] | None = None,
    ) -> BenchmarkInvocation:
        if status == BenchmarkInvocationStatus.RUNNING:
            raise ValueError("finish_invocation requires a terminal status")
        cell_id = self.session.scalar(
            select(BenchmarkInvocation.cell_id).where(
                BenchmarkInvocation.id == invocation_id
            )
        )
        if cell_id is None:
            raise LookupError("benchmark invocation not found")
        cell = self.session.scalar(
            select(BenchmarkCell)
            .where(BenchmarkCell.id == cell_id)
            .with_for_update()
        )
        if cell is None:
            raise LookupError("benchmark cell not found")
        if cell.status != BenchmarkCellStatus.RUNNING:
            raise ValueError("benchmark invocations require a running cell")
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
        return event

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
        if job.status != BenchmarkJobStatus.RUNNING:
            raise ValueError("only a running benchmark job may request cancellation")
        job.status = BenchmarkJobStatus.CANCEL_REQUESTED
        job.cancel_requested_at = requested_at
        self.session.flush()
        return job

    def complete_job(self, *, job_id: UUID, completed_at: datetime) -> BenchmarkJob:
        """Seal a fully processed job with its counter-derived terminal outcome."""
        job = self.session.scalar(
            select(BenchmarkJob).where(BenchmarkJob.id == job_id).with_for_update()
        )
        if job is None:
            raise LookupError("benchmark job not found")
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
