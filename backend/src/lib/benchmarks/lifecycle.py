"""Admission orchestration for the stable asynchronous benchmark lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping
from uuid import UUID

from anyio.from_thread import run as run_on_event_loop
from anyio.to_thread import run_sync
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_app_version
from src.lib.observability.runtime import report_runtime_exception
from src.lib.openai_agents.config import (
    get_benchmark_max_cells,
    get_benchmark_max_materialized_submission_bytes,
)
from src.models.sql.benchmark import (
    BenchmarkCell,
    BenchmarkCellStatus,
    BenchmarkJob,
    BenchmarkJobStatus,
    BenchmarkJobIdempotency,
    BenchmarkJobInputSnapshot,
)
from src.models.sql.database import SessionLocal

from .execution_context import (
    BenchmarkCuratorContext,
    require_matching_curator_context,
)
from .input_resolvers import (
    BenchmarkInputResolverCatalog,
    BenchmarkSourceError,
    BenchmarkSourceRequestContext,
    materialize_plan_inputs,
)
from .loader import BenchmarkCatalogError
from .models import BenchmarkRouteCatalog, BenchmarkSuite, ResolvedBenchmarkPlan
from .observability import sanitized_benchmark_error
from .planning import resolve_execution_plan
from .persistence import BenchmarkRepository, canonical_digest
from .snapshots import (
    BenchmarkSnapshotError,
    BenchmarkSnapshotRepository,
    BenchmarkSnapshotStore,
    configured_benchmark_snapshot_store,
)
from .suites import validate_suite


@dataclass(frozen=True)
class BenchmarkLifecycleFailure(Exception):
    code: str
    message: str
    status_code: int


@dataclass(frozen=True)
class BenchmarkAdmissionResult:
    job_id: UUID
    replayed: bool


_SOURCE_ERROR_STATUS = {
    "invalid_reference": 422,
    "unknown_resolver": 422,
    "forbidden_source": 403,
    "version_conflict": 409,
    "digest_conflict": 409,
    "oversize_payload": 413,
    "missing_source": 404,
    "source_unavailable": 503,
    "invalid_delegated_authorization": 400,
    "unexpected_delegated_authorization": 400,
    "missing_delegated_authorization": 401,
    "oversize_submission": 413,
}


def authoritative_plan(
    *,
    suite_value: Mapping[str, Any],
    submitted_plan: ResolvedBenchmarkPlan,
    catalog: BenchmarkRouteCatalog,
) -> tuple[BenchmarkSuite, ResolvedBenchmarkPlan]:
    """Revalidate and resolve caller data against the server-owned catalog."""

    try:
        suite = validate_suite(suite_value)
        resolved = resolve_execution_plan(suite, catalog)
    except BenchmarkCatalogError as exc:
        raise BenchmarkLifecycleFailure(
            "invalid_plan", "Benchmark suite cannot be resolved", 422
        ) from exc
    if resolved != submitted_plan:
        raise BenchmarkLifecycleFailure(
            "plan_drift",
            "Submitted normalized plan does not match the authoritative plan",
            409,
        )
    return suite, resolved


def _job_digests(plan: ResolvedBenchmarkPlan) -> tuple[str, str, str]:
    config_digest = canonical_digest(
        [item.model_dump(mode="json") for item in plan.configurations]
    )
    code_digest = canonical_digest({"app_version": get_app_version()})
    inputs_digest = canonical_digest(
        [
            {"case_id": item.case_id, "input": item.input.model_dump(mode="json")}
            for item in plan.cases
        ]
    )
    return config_digest, code_digest, inputs_digest


def _replay(
    repository: BenchmarkRepository,
    reservation: BenchmarkJobIdempotency,
) -> BenchmarkAdmissionResult:
    if reservation.outcome == "failed":
        raise BenchmarkLifecycleFailure(
            reservation.error_code or "submission_failed",
            reservation.error_message or "Benchmark submission failed",
            reservation.error_status or 503,
        )
    if reservation.job_id is None:
        raise BenchmarkLifecycleFailure(
            "lifecycle_conflict",
            "Previously accepted benchmark work is no longer available",
            409,
        )
    if repository.get_job(
        job_id=reservation.job_id, owner_subject=reservation.owner_subject
    ) is None:
        raise BenchmarkLifecycleFailure(
            "lifecycle_conflict",
            "Previously accepted benchmark work is no longer available",
            409,
        )
    return BenchmarkAdmissionResult(reservation.job_id, True)


async def submit_job(
    *,
    owner_subject: str,
    service_principal: str,
    idempotency_key: str,
    suite_value: Mapping[str, Any],
    submitted_plan: ResolvedBenchmarkPlan,
    route_catalog: BenchmarkRouteCatalog | None,
    input_catalog: BenchmarkInputResolverCatalog | Callable[[], BenchmarkInputResolverCatalog],
    source_context: BenchmarkSourceRequestContext,
    curator_context: BenchmarkCuratorContext,
    snapshot_store: BenchmarkSnapshotStore | None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> BenchmarkAdmissionResult:
    """Own the complete SQL transaction in one worker thread.

    Duplicate-key lock waits never block the ASGI loop. Materializers run back
    on the originating loop, so registered async clients keep their loop affinity.
    The admission Session is never passed to a materializer or another thread.
    """
    def admit() -> BenchmarkAdmissionResult:
        with session_factory() as session:
            return _submit_job(
                session=session,
                owner_subject=owner_subject,
                service_principal=service_principal,
                idempotency_key=idempotency_key,
                suite_value=suite_value,
                submitted_plan=submitted_plan,
                route_catalog=route_catalog,
                input_catalog=input_catalog,
                source_context=source_context,
                curator_context=curator_context,
                snapshot_store=snapshot_store,
            )

    # Default non-abandoning behavior lets accepted work survive client cancellation.
    return await run_sync(admit)


def _submit_job(
    *,
    session: Session,
    owner_subject: str,
    service_principal: str,
    idempotency_key: str,
    suite_value: Mapping[str, Any],
    submitted_plan: ResolvedBenchmarkPlan,
    route_catalog: BenchmarkRouteCatalog | None,
    input_catalog: BenchmarkInputResolverCatalog | Callable[[], BenchmarkInputResolverCatalog],
    source_context: BenchmarkSourceRequestContext,
    curator_context: BenchmarkCuratorContext,
    snapshot_store: BenchmarkSnapshotStore | None,
) -> BenchmarkAdmissionResult:
    # Existing accepted work is identified by the immutable submitted request,
    # not today's catalog, defaults or admission limits.
    try:
        suite = validate_suite(suite_value)
    except BenchmarkCatalogError as exc:
        raise BenchmarkLifecycleFailure(
            "invalid_plan", "Benchmark suite is invalid", 422
        ) from exc
    context_digest = canonical_digest(curator_context.model_dump(mode="json"))
    request_digest = canonical_digest(
        {
            "operation": "submit",
            "suite": suite.model_dump(mode="json"),
            "plan": submitted_plan.model_dump(mode="json"),
            "curator_context_digest": context_digest,
        }
    )
    repository = BenchmarkRepository(session)
    reservation, created = repository.reserve_idempotency(
        owner_subject=owner_subject,
        operation="submit",
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        curator_context_digest=context_digest,
    )
    if not created:
        return _replay(repository, reservation)

    try:
        if route_catalog is None:
            from .runtime_catalog import build_curator_route_catalog
            try:
                route_catalog = build_curator_route_catalog(session, curator_context)
            except Exception as exc:
                report_runtime_exception(
                    sanitized_benchmark_error("catalog_resolution", type(exc).__name__),
                    component="benchmark_lifecycle", operation="catalog_resolution",
                )
                raise BenchmarkLifecycleFailure("catalog_unavailable", "Benchmark route catalog is unavailable", 503) from None
        suite, plan = authoritative_plan(
            suite_value=suite_value,
            submitted_plan=submitted_plan,
            catalog=route_catalog,
        )
        async def materialize(request_context: BenchmarkSourceRequestContext):
            catalog = input_catalog() if callable(input_catalog) else input_catalog
            return await materialize_plan_inputs(
                plan, catalog,
                request_context=request_context,
                max_submission_bytes=get_benchmark_max_materialized_submission_bytes(),
            )
        materialized = run_on_event_loop(materialize, source_context)
        # The delegated bearer exists only in source_context. It never crosses
        # into snapshot, job, cell, or worker serialization.
        del source_context
        with session.begin_nested():
            snapshots = BenchmarkSnapshotRepository(
                session, snapshot_store if snapshot_store is not None else configured_benchmark_snapshot_store(),
            )
            snapshot_ids = snapshots.freeze_plan(
                materialized,
                owner_subject=owner_subject,
                service_principal=service_principal,
            )
            config_digest, code_digest, inputs_digest = _job_digests(plan)
            job = repository.create_job(
                owner_subject=owner_subject,
                suite=suite,
                curator_context=curator_context,
                plan=plan,
                config_digest=config_digest,
                code_digest=code_digest,
                inputs_digest=inputs_digest,
                snapshot_ids_by_case=snapshot_ids,
            )
        repository.accept_idempotency(reservation=reservation, job_id=job.id)
        session.commit()
        return BenchmarkAdmissionResult(job.id, False)
    except BenchmarkLifecycleFailure as exc:
        repository.fail_idempotency(
            reservation=reservation,
            error_code=exc.code,
            error_message=exc.message,
            error_status=exc.status_code,
        )
        session.commit()
        raise
    except BenchmarkSourceError as exc:
        repository.fail_idempotency(
            reservation=reservation,
            error_code=exc.code,
            error_message=str(exc),
            error_status=_SOURCE_ERROR_STATUS.get(exc.code, 503),
        )
        session.commit()
        raise BenchmarkLifecycleFailure(
            exc.code,
            str(exc),
            _SOURCE_ERROR_STATUS.get(exc.code, 503),
        ) from exc
    except BenchmarkSnapshotError as exc:
        repository.fail_idempotency(
            reservation=reservation,
            error_code="source_unavailable",
            error_message="Benchmark input snapshot could not be committed",
            error_status=503,
        )
        session.commit()
        raise BenchmarkLifecycleFailure(
            "source_unavailable",
            "Benchmark input snapshot could not be committed",
            503,
        ) from exc


async def rerun_job(
    *,
    owner_subject: str,
    source_job_id: UUID,
    requested_cell_ids: tuple[UUID, ...],
    idempotency_key: str,
    current_context: BenchmarkCuratorContext,
    session_factory: Callable[[], Session] = SessionLocal,
) -> BenchmarkAdmissionResult:
    """Admit linked work with one thread-owned transaction, without source I/O."""
    def admit() -> BenchmarkAdmissionResult:
        with session_factory() as session:
            return _rerun_job(
                session=session, owner_subject=owner_subject,
                source_job_id=source_job_id, requested_cell_ids=requested_cell_ids,
                idempotency_key=idempotency_key, current_context=current_context,
            )
    return await run_sync(admit)


def _rerun_job(
    *,
    session: Session,
    owner_subject: str,
    source_job_id: UUID,
    requested_cell_ids: tuple[UUID, ...],
    idempotency_key: str,
    current_context: BenchmarkCuratorContext,
) -> BenchmarkAdmissionResult:
    repository = BenchmarkRepository(session)
    source_job = session.scalar(
        select(BenchmarkJob).where(
            BenchmarkJob.id == source_job_id,
            BenchmarkJob.owner_subject == owner_subject,
        ).with_for_update()
    )
    if source_job is None:
        raise BenchmarkLifecycleFailure("not_found", "Benchmark job not found", 404)
    if source_job.status not in (
        BenchmarkJobStatus.COMPLETED, BenchmarkJobStatus.COMPLETED_WITH_FAILURES,
        BenchmarkJobStatus.CANCELLED, BenchmarkJobStatus.FAILED,
    ):
        raise BenchmarkLifecycleFailure("lifecycle_conflict", "Rerun source job must be terminal", 409)
    if source_job.curator_context is None:
        raise BenchmarkLifecycleFailure(
            "authorization_required", "Trusted curator context is required", 403
        )
    frozen_context = BenchmarkCuratorContext.model_validate_json(json.dumps(source_job.curator_context))
    try:
        require_matching_curator_context(frozen_context, current=current_context)
    except PermissionError as exc:
        raise BenchmarkLifecycleFailure(
            "authorization_required",
            "Current curator authorization does not permit this rerun",
            403,
        ) from exc

    failed_cells = tuple(
        session.scalars(
            select(BenchmarkCell).where(
                BenchmarkCell.job_id == source_job_id,
                BenchmarkCell.status == BenchmarkCellStatus.FAILED,
            )
        )
    )
    failed_by_id = {cell.id: cell for cell in failed_cells}
    selected_ids = requested_cell_ids or tuple(sorted(failed_by_id, key=str))
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise BenchmarkLifecycleFailure(
            "lifecycle_conflict", "Rerun must select failed cells exactly once", 409
        )
    if any(cell_id not in failed_by_id for cell_id in selected_ids):
        raise BenchmarkLifecycleFailure(
            "lifecycle_conflict", "Rerun may select only failed cells", 409
        )

    source_plan = ResolvedBenchmarkPlan.model_validate_json(json.dumps(source_job.resolved_plan))
    selected_keys = {failed_by_id[cell_id].cell_key for cell_id in selected_ids}
    selected_cells = tuple(
        cell for cell in source_plan.cells if cell.cell_id in selected_keys
    )
    case_ids = {cell.case_id for cell in selected_cells}
    rerun_plan = source_plan.model_copy(
        update={
            "cases": tuple(case for case in source_plan.cases if case.case_id in case_ids),
            "cells": selected_cells,
            "plan_digest": canonical_digest(
                {
                    "source_plan_digest": source_plan.plan_digest,
                    "selected_cell_keys": sorted(selected_keys),
                }
            ),
        }
    )
    context_digest = canonical_digest(frozen_context.model_dump(mode="json"))
    request_digest = canonical_digest(
        {
            "operation": "rerun",
            "source_job_id": str(source_job_id),
            "selected_cell_ids": sorted(str(value) for value in selected_ids),
            "curator_context_digest": context_digest,
        }
    )
    reservation, created = repository.reserve_idempotency(
        owner_subject=owner_subject,
        operation="rerun",
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        curator_context_digest=context_digest,
    )
    if not created:
        return _replay(repository, reservation)

    if len(selected_ids) > get_benchmark_max_cells():
        failure = BenchmarkLifecycleFailure("invalid_request", "Rerun selection exceeds configured cell limit", 422)
        repository.fail_idempotency(
            reservation=reservation, error_code=failure.code,
            error_message=failure.message, error_status=failure.status_code,
        )
        session.commit()
        raise failure

    snapshots_by_case = dict(
        session.execute(
            select(
                BenchmarkJobInputSnapshot.case_id,
                BenchmarkJobInputSnapshot.snapshot_id,
            ).where(
                BenchmarkJobInputSnapshot.job_id == source_job_id,
                BenchmarkJobInputSnapshot.case_id.in_(case_ids),
            )
        ).all()
    )
    suite = BenchmarkSuite.model_validate_json(json.dumps(source_job.suite_specification))
    config_digest, code_digest, inputs_digest = _job_digests(rerun_plan)
    job = repository.create_job(
        owner_subject=owner_subject,
        suite=suite,
        curator_context=frozen_context,
        plan=rerun_plan,
        config_digest=config_digest,
        code_digest=code_digest,
        inputs_digest=inputs_digest,
        snapshot_ids_by_case=snapshots_by_case,
        rerun_of_job_id=source_job_id,
    )
    repository.accept_idempotency(reservation=reservation, job_id=job.id)
    session.commit()
    return BenchmarkAdmissionResult(job.id, False)


__all__ = [
    "BenchmarkAdmissionResult",
    "BenchmarkLifecycleFailure",
    "authoritative_plan",
    "rerun_job",
    "submit_job",
]
