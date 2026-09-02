"""Durable, lease-fenced execution of isolated benchmark cells."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Callable
from uuid import UUID, uuid4

from src.lib.benchmarks.models import ResolvedBenchmarkCell
from src.lib.benchmarks.persistence import (
    BenchmarkCancellationRequestedError,
    BenchmarkLeaseLostError,
    BenchmarkRepository,
    canonical_digest,
)
from src.lib.benchmarks.runtime import (
    execute_resolved_agent_cell,
    execute_resolved_flow_cell,
)
from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotRepository,
    configured_benchmark_snapshot_store,
)
from src.lib.openai_agents.config import (
    get_benchmark_cell_timeout_seconds,
    get_benchmark_execution_enabled,
    get_benchmark_worker_concurrency,
    get_benchmark_worker_enabled,
    get_benchmark_worker_heartbeat_seconds,
    get_benchmark_worker_lease_seconds,
)
from src.lib.openai_agents.provider_usage import (
    PendingProviderInvocation,
    ProviderUsageRecord,
    observe_provider_invocations,
    provider_usage_metadata,
)
from src.models.sql.benchmark import (
    BenchmarkCell,
    BenchmarkCellStatus,
    BenchmarkInvocationStatus,
    BenchmarkJob,
)
from src.models.sql.database import SessionLocal


logger = logging.getLogger(__name__)


class _BenchmarkWorkerError(RuntimeError):
    """Content-free caught worker failure safe for operational reporting."""


def _report_failure(
    exc: BaseException,
    *,
    job_id: UUID,
    cell_id: UUID,
    operation: str = "cell_execution_failed",
) -> None:
    try:
        raise _BenchmarkWorkerError(
            f"Benchmark worker operation failed ({operation}; {type(exc).__name__})"
        ) from None
    except _BenchmarkWorkerError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        from src.lib.observability.runtime import report_runtime_exception

        report_runtime_exception(
            sanitized,
            component="benchmark_worker",
            operation=operation,
            context={"job_id": str(job_id), "cell_id": str(cell_id)},
        )
    logger.error(
        "Benchmark worker operation failed: operation=%s error_type=%s",
        operation,
        type(exc).__name__,
        exc_info=False,
        extra={"sentry_skip_event": True},
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_failure(category: str, exc: BaseException) -> dict[str, Any]:
    return {
        "category": category,
        "retryable": False,
        "detail": type(exc).__name__,
    }


class _DurableInvocationObserver:
    def __init__(
        self,
        *,
        cell: BenchmarkCell,
        lease_owner: UUID,
        session_factory: Callable[..., Any] = SessionLocal,
    ) -> None:
        self.cell_id = cell.id
        self.job_id = cell.job_id
        self.attempt = cell.attempt_count
        self.input_digest = cell.input_digest
        self.lease_owner = lease_owner
        self.session_factory = session_factory
        self.invocation_ids: dict[int, UUID] = {}

    def started(self, pending: PendingProviderInvocation) -> None:
        # Provider instrumentation uses a monotonic clock for latency; the
        # durable checkpoint needs an independent wall-clock timestamp.
        started_at = _utcnow()
        request_digest = canonical_digest(
            {
                "cell_id": str(self.cell_id),
                "input_digest": self.input_digest,
                "route_slot": pending.route_slot,
                "requested_provider": pending.requested_provider,
                "requested_model": pending.requested_model,
                "reasoning_effort": pending.reasoning_effort,
                "sequence": pending.sequence,
            }
        )
        if pending.route_slot is None:
            raise ValueError("benchmark provider invocation has no frozen route slot")
        with self.session_factory() as session:
            repository = BenchmarkRepository(session)
            if repository.cancellation_requested(
                job_id=self.job_id,
                lease_owner=self.lease_owner,
                now=started_at,
            ):
                raise BenchmarkCancellationRequestedError(
                    "benchmark cancellation was requested before provider dispatch"
                )
            invocation = repository.append_invocation(
                cell_id=self.cell_id,
                lease_owner=self.lease_owner,
                ordinal=pending.sequence - 1,
                attempt=self.attempt,
                route_slot=pending.route_slot,
                request_digest=request_digest,
                requested_provider=pending.requested_provider,
                requested_model=pending.requested_model,
                reasoning_effort=pending.reasoning_effort,
                sequence=pending.sequence,
                started_at=started_at,
            )
            session.commit()
            self.invocation_ids[pending.sequence] = invocation.id

    def completed(
        self, pending: PendingProviderInvocation, record: ProviderUsageRecord
    ) -> None:
        invocation_id = self.invocation_ids[pending.sequence]
        completed_at = _utcnow()
        billed = record.billed_cost
        succeeded = record.status == "completed"
        failure = (
            None
            if succeeded
            else {
                "category": "provider_error",
                "retryable": False,
                "detail": record.failure_detail,
            }
        )
        with self.session_factory() as session:
            BenchmarkRepository(session).finish_invocation(
                invocation_id=invocation_id,
                lease_owner=self.lease_owner,
                status=(
                    BenchmarkInvocationStatus.SUCCEEDED
                    if succeeded
                    else BenchmarkInvocationStatus.FAILED
                ),
                completed_at=completed_at,
                response_digest=(
                    canonical_digest(provider_usage_metadata(record))
                    if succeeded
                    else None
                ),
                actual_provider=record.actual_provider,
                actual_model=record.actual_model,
                routing_attempt=record.routing_attempt,
                latency_ms=record.latency_ms,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                total_tokens=record.total_tokens,
                billed_amount=billed.amount if billed is not None else None,
                billed_unit=billed.unit if billed is not None else None,
                billed_source=billed.source if billed is not None else None,
                failure=failure,
            )
            session.commit()


class BenchmarkWorker:
    """Process one leased job at a time while isolating every cell outcome."""

    def __init__(
        self,
        *,
        worker_id: UUID | None = None,
        session_factory: Callable[..., Any] = SessionLocal,
        agent_executor: Callable[..., Any] = execute_resolved_agent_cell,
        flow_executor: Callable[..., Any] = execute_resolved_flow_cell,
    ) -> None:
        self.worker_id = worker_id or uuid4()
        self.session_factory = session_factory
        self.agent_executor = agent_executor
        self.flow_executor = flow_executor
        self.lease_seconds = get_benchmark_worker_lease_seconds()
        self.heartbeat_seconds = get_benchmark_worker_heartbeat_seconds()
        self.cell_timeout_seconds = get_benchmark_cell_timeout_seconds()

    def recover_expired(self) -> tuple[UUID, ...]:
        with self.session_factory() as session:
            recovered = BenchmarkRepository(session).recover_expired_cells()
            session.commit()
            return recovered

    def _claim_job(self) -> UUID | None:
        now = _utcnow()
        with self.session_factory() as session:
            job = BenchmarkRepository(session).claim_next_job(
                lease_owner=self.worker_id,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                now=now,
            )
            session.commit()
            return job.id if job is not None else None

    def _claim_cell(self, job_id: UUID) -> UUID | None:
        now = _utcnow()
        with self.session_factory() as session:
            cell = BenchmarkRepository(session).claim_next_cell(
                job_id=job_id,
                lease_owner=self.worker_id,
                lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                now=now,
            )
            session.commit()
            return cell.id if cell is not None else None

    def _load_cell(self, cell_id: UUID) -> tuple[BenchmarkCell, str, dict[str, Any]]:
        with self.session_factory() as session:
            cell = session.get(BenchmarkCell, cell_id)
            if cell is None:
                raise LookupError("benchmark cell not found")
            job = session.get(BenchmarkJob, cell.job_id)
            if job is None:
                raise LookupError("benchmark job not found")
            content = BenchmarkSnapshotRepository(
                session, configured_benchmark_snapshot_store()
            ).read_verified(cell.input_snapshot_id, owner_subject=job.owner_subject)
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("frozen benchmark input must be a JSON object")
            session.expunge(cell)
            return cell, job.owner_subject, parsed

    async def _heartbeat(self, job_id: UUID, cell_id: UUID, stopped: asyncio.Event) -> None:
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), timeout=self.heartbeat_seconds)
                return
            except TimeoutError:
                pass
            with self.session_factory() as session:
                owned = BenchmarkRepository(session).heartbeat_leases(
                    job_id=job_id,
                    cell_id=cell_id,
                    lease_owner=self.worker_id,
                    lease_seconds=self.lease_seconds,
                )
                if not owned:
                    session.rollback()
                    raise BenchmarkLeaseLostError("benchmark lease heartbeat was fenced")
                session.commit()

    async def _execute_cell(self, cell_id: UUID) -> None:
        cell: BenchmarkCell | None = None
        stopped: asyncio.Event | None = None
        heartbeat: asyncio.Task[None] | None = None
        try:
            cell, _, case_input = self._load_cell(cell_id)
            resolved = ResolvedBenchmarkCell.model_validate(
                {
                    "cell_id": cell.cell_key,
                    "case_id": cell.case_id,
                    "configuration_id": cell.configuration_id,
                    "repetition": cell.repetition,
                    "target": {"kind": cell.target_kind, "id": cell.target_id},
                    "input": {
                        "resolver": cell.input_resolver,
                        "reference": cell.input_reference,
                        "version": cell.input_version,
                        "digest": cell.input_digest,
                    },
                    "routes": cell.routes,
                }
            )
            observer = _DurableInvocationObserver(
                cell=cell,
                lease_owner=self.worker_id,
                session_factory=self.session_factory,
            )
            stopped = asyncio.Event()
            heartbeat = asyncio.create_task(
                self._heartbeat(cell.job_id, cell.id, stopped)
            )
            with observe_provider_invocations(observer):
                executor = (
                    self.agent_executor
                    if cell.target_kind == "agent"
                    else self.flow_executor
                )
                outcome = await asyncio.wait_for(
                    executor(resolved, case_input, str(cell.id)),
                    timeout=self.cell_timeout_seconds,
                )
            if heartbeat.done():
                heartbeat.result()
            if any(invocation.status == "failed" for invocation in outcome.invocations):
                raise RuntimeError("benchmark cell has a failed provider invocation")
            if not isinstance(outcome.output, dict):
                raise ValueError("benchmark target result must be a JSON object envelope")
            completed_at = _utcnow()
            with self.session_factory() as session:
                repository = BenchmarkRepository(session)
                if repository.cancellation_requested(
                    job_id=cell.job_id,
                    lease_owner=self.worker_id,
                    now=completed_at,
                ):
                    repository.finish_cell(
                        cell_id=cell.id,
                        lease_owner=self.worker_id,
                        status=BenchmarkCellStatus.CANCELLED,
                        completed_at=completed_at,
                    )
                else:
                    repository.finish_cell(
                        cell_id=cell.id,
                        lease_owner=self.worker_id,
                        status=BenchmarkCellStatus.SUCCEEDED,
                        completed_at=completed_at,
                        generated_envelope=outcome.output,
                        result=outcome.model_dump(mode="json"),
                    )
                session.commit()
        except BenchmarkLeaseLostError:
            raise
        except BenchmarkCancellationRequestedError:
            if cell is None:
                raise
            with self.session_factory() as session:
                BenchmarkRepository(session).finish_cell(
                    cell_id=cell.id,
                    lease_owner=self.worker_id,
                    status=BenchmarkCellStatus.CANCELLED,
                    completed_at=_utcnow(),
                )
                session.commit()
        except Exception as exc:
            if cell is None:
                with self.session_factory() as lookup_session:
                    cell = lookup_session.get(BenchmarkCell, cell_id)
                    if cell is None:
                        raise
                    lookup_session.expunge(cell)
            category = "timeout" if isinstance(exc, TimeoutError) else "runtime_error"
            if isinstance(exc, (ValueError, LookupError)):
                category = "configuration_error"
            with self.session_factory() as session:
                try:
                    BenchmarkRepository(session).finish_cell(
                        cell_id=cell.id,
                        lease_owner=self.worker_id,
                        status=BenchmarkCellStatus.FAILED,
                        completed_at=_utcnow(),
                        failure=_bounded_failure(category, exc),
                    )
                    session.commit()
                except BenchmarkLeaseLostError:
                    session.rollback()
                    raise
                except Exception as terminalization_exc:
                    session.rollback()
                    _report_failure(
                        terminalization_exc,
                        job_id=cell.job_id,
                        cell_id=cell.id,
                        operation="cell_terminalization_failed",
                    )
            _report_failure(exc, job_id=cell.job_id, cell_id=cell.id)
        finally:
            if stopped is not None:
                stopped.set()
            if heartbeat is not None:
                if not heartbeat.done():
                    heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat

    def _finish_or_cancel_job(self, job_id: UUID) -> bool:
        now = _utcnow()
        with self.session_factory() as session:
            repository = BenchmarkRepository(session)
            if repository.cancellation_requested(
                job_id=job_id, lease_owner=self.worker_id, now=now
            ):
                repository.cancel_queued_cells(
                    job_id=job_id, lease_owner=self.worker_id, cancelled_at=now
                )
            job = session.get(BenchmarkJob, job_id)
            if job is None:
                raise LookupError("benchmark job not found")
            if job.queued_cells or job.running_cells:
                session.commit()
                return False
            repository.complete_job(
                job_id=job_id, lease_owner=self.worker_id, completed_at=now
            )
            session.commit()
            return True

    async def run_once(self) -> bool:
        if not (get_benchmark_worker_enabled() and get_benchmark_execution_enabled()):
            return False
        self.recover_expired()
        job_id = self._claim_job()
        if job_id is None:
            return False
        while True:
            try:
                if self._finish_or_cancel_job(job_id):
                    return True
                cell_id = self._claim_cell(job_id)
                if cell_id is None:
                    return False
                await self._execute_cell(cell_id)
            except BenchmarkLeaseLostError:
                return False

    async def run_forever(self) -> None:
        if not (get_benchmark_worker_enabled() and get_benchmark_execution_enabled()):
            logger.info("Benchmark worker is disabled by deployment gates")
            return
        while True:
            worked = await self.run_once()
            if not worked:
                await asyncio.sleep(1)


async def _main() -> None:
    workers = [BenchmarkWorker() for _ in range(get_benchmark_worker_concurrency())]
    await asyncio.gather(*(worker.run_forever() for worker in workers))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())


if __name__ == "__main__":
    main()
