"""Deployment-local, owner-scoped durable benchmark lifecycle API."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from typing import TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from src.api.benchmark_auth import (
    require_benchmark_cancel,
    require_benchmark_delete,
    require_benchmark_read,
    require_benchmark_run,
)
from src.api.benchmark_gate import require_benchmark_api, require_benchmark_execution
from src.api.benchmark_curator import require_benchmark_curator
from src.api.benchmark_events import create_event_response
from src.api.benchmark_sources import _catalog as input_resolver_catalog, delegated_source_request_context
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.lifecycle import BenchmarkAdmissionResult, BenchmarkLifecycleFailure, rerun_job, submit_job
from src.lib.benchmarks.input_resolvers import BenchmarkSourceError
from src.lib.benchmarks.persistence import (
    BenchmarkIdempotencyConflictError,
    BenchmarkCellCursor,
    BenchmarkCellDetail,
    BenchmarkCellSummary,
    BenchmarkJobCursor,
    BenchmarkJobDetail,
    BenchmarkJobSummary,
    BenchmarkPage,
    BenchmarkRepository,
)
from src.models.sql.benchmark import BenchmarkJobStatus
from src.models.sql.database import SessionLocal
from src.schemas.benchmark_jobs import (
    BenchmarkInvocationPage, BenchmarkInvocationResponse, BenchmarkRerunRequest,
    BenchmarkSubmitRequest, admission_body_schema, lifecycle_error_responses,
)
from src.schemas import benchmark_job_examples as examples
from src.lib.openai_agents.config import get_benchmark_admission_max_bytes


AdmissionBody = TypeVar("AdmissionBody", bound=BaseModel)


async def _admission_body(request: Request, model: type[AdmissionBody]) -> AdmissionBody:
    """Read bounded JSON only after feature/capability/human dependencies pass."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise _error(415, "invalid_content_type", "Benchmark admission requires application/json")
    payload = bytearray()
    limit = get_benchmark_admission_max_bytes()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > limit:
            raise _error(413, "oversize_submission", "Benchmark admission body exceeds configured limit")
        payload.extend(chunk)
    try:
        return model.model_validate_json(payload)
    except (ValueError, ValidationError):
        raise _error(422, "invalid_request", "Invalid benchmark request") from None


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class BenchmarkLifecycleRoute(APIRoute):
    """Do not reflect invalid bodies, credentials, or internal exceptions."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise _error(422, "invalid_request", "Invalid benchmark request") from exc
            except LookupError as exc:
                raise _error(404, "not_found", "Benchmark resource not found") from exc
            except BenchmarkIdempotencyConflictError:
                raise _error(409, "idempotency_conflict", "Idempotency key belongs to different benchmark work") from None
            except BenchmarkLifecycleFailure as exc:
                raise _error(exc.status_code, exc.code, exc.message) from None
            except HTTPException as exc:
                # Shared benchmark auth also serves older APIs and uses string
                # details. Give this versioned API a stable machine-readable
                # envelope without changing those sibling-owned surfaces.
                if isinstance(exc.detail, dict):
                    raise
                code, message = {
                    401: ("authorization_required", "Verified benchmark identity required"),
                    403: ("capability_required", "Benchmark capability required"),
                    503: ("authorization_unavailable", "Benchmark authentication unavailable"),
                }.get(exc.status_code, ("request_failed", "Benchmark request failed"))
                raise HTTPException(exc.status_code, {"code": code, "message": message}, headers=exc.headers) from None

        return wrapped


router = APIRouter(
    prefix="/api/v1/benchmarks/jobs",
    tags=["Benchmarks - Jobs"],
    dependencies=[Depends(require_benchmark_api)],
    route_class=BenchmarkLifecycleRoute,
    responses=lifecycle_error_responses(),
)


def _owner(principal: dict[str, Any]) -> str:
    subject = principal.get("sub")
    if not isinstance(subject, str) or not subject:
        raise _error(401, "authorization_required", "Verified benchmark identity required")
    return subject


@router.post(
    "", response_model=BenchmarkAdmissionResult, status_code=202,
    dependencies=[Depends(require_benchmark_execution)],
    openapi_extra=admission_body_schema(BenchmarkSubmitRequest, example=examples.SUBMIT),
    responses=examples.json_example(examples.ACCEPTED, status=202),
)
async def create_job(
    request: Request,
    response: Response,
    principal: dict[str, Any] = Depends(require_benchmark_run),
    curator: BenchmarkCuratorContext = Depends(require_benchmark_curator),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255, pattern=r"^[!-~]+$"),
) -> BenchmarkAdmissionResult:
    """Freeze inputs and durably queue an authoritative normalized plan once."""
    payload = await _admission_body(request, BenchmarkSubmitRequest)
    try:
        source_context = delegated_source_request_context(request, principal_subject=_owner(principal))
    except BenchmarkSourceError:
        raise _error(400, "invalid_delegated_authorization", "Invalid delegated source authorization") from None
    result = await submit_job(
        owner_subject=_owner(principal), service_principal=str(principal["client_id"]),
        idempotency_key=idempotency_key, suite_value=payload.suite.model_dump(mode="json"),
        submitted_plan=payload.plan, route_catalog=None,
        input_catalog=lambda: input_resolver_catalog(request), source_context=source_context,
        curator_context=curator, snapshot_store=None, session_factory=SessionLocal,
    )
    response.headers["Location"] = f"/api/v1/benchmarks/jobs/{result.job_id}"
    return result


@router.post(
    "/{job_id}/rerun", response_model=BenchmarkAdmissionResult, status_code=202,
    dependencies=[Depends(require_benchmark_execution)],
    openapi_extra=admission_body_schema(BenchmarkRerunRequest, example={"cell_ids": [examples.CELL_ID]}),
    responses=examples.json_example(examples.ACCEPTED, status=202),
)
async def rerun_failed_cells(
    job_id: UUID,
    request: Request,
    response: Response,
    principal: dict[str, Any] = Depends(require_benchmark_run),
    curator: BenchmarkCuratorContext = Depends(require_benchmark_curator),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255, pattern=r"^[!-~]+$"),
) -> BenchmarkAdmissionResult:
    """Create linked failed-cell work; an empty cell_ids selects all failed cells.

    Replay returns the same accepted job. Frozen inputs are reused without ABC.
    """
    if request.headers.get("x-benchmark-delegated-source-authorization") is not None:
        raise _error(400, "unexpected_delegated_authorization", "Frozen reruns do not accept source credentials")
    payload = await _admission_body(request, BenchmarkRerunRequest)
    result = await rerun_job(
        owner_subject=_owner(principal), source_job_id=job_id,
        requested_cell_ids=payload.cell_ids, idempotency_key=idempotency_key,
        current_context=curator, session_factory=SessionLocal,
    )
    response.headers["Location"] = f"/api/v1/benchmarks/jobs/{result.job_id}"
    return result


@router.get("", response_model=BenchmarkPage[BenchmarkJobSummary, BenchmarkJobCursor],
            responses=examples.json_example({"items": [examples.JOB_SUMMARY], "next_cursor": None}))
def list_jobs(
    principal: dict[str, Any] = Depends(require_benchmark_read),
    status: BenchmarkJobStatus | None = None,
    limit: int | None = Query(default=None, ge=1),
    cursor_created_at: datetime | None = None,
    cursor_job_id: UUID | None = None,
):
    """Summary-only page. Pass both fields of next_cursor to resume the same filter."""
    if (cursor_created_at is None) != (cursor_job_id is None):
        raise _error(422, "invalid_cursor", "Both job cursor fields are required")
    cursor = None
    if cursor_created_at is not None and cursor_job_id is not None:
        if cursor_created_at.utcoffset() is None:
            raise _error(422, "invalid_cursor", "Job cursor timestamp requires a timezone")
        cursor = BenchmarkJobCursor(cursor_created_at, cursor_job_id)
    with SessionLocal() as session:
        return BenchmarkRepository(session).list_jobs(
            owner_subject=_owner(principal), status=status, cursor=cursor, limit=limit,
        )


@router.get("/{job_id}", response_model=BenchmarkJobDetail, responses=examples.json_example(examples.JOB))
def get_job(job_id: UUID, principal: dict[str, Any] = Depends(require_benchmark_read)):
    with SessionLocal() as session:
        result = BenchmarkRepository(session).get_job(
            job_id=job_id, owner_subject=_owner(principal),
        )
        if result is None:
            raise _error(404, "not_found", "Benchmark resource not found")
        return result


@router.get("/{job_id}/cells", response_model=BenchmarkPage[BenchmarkCellSummary, BenchmarkCellCursor],
            responses=examples.json_example({"items": [examples.CELL_SUMMARY], "next_cursor": None}))
def list_cells(
    job_id: UUID,
    principal: dict[str, Any] = Depends(require_benchmark_read),
    limit: int | None = Query(default=None, ge=1),
    cursor_position: int | None = Query(default=None, ge=0),
    cursor_cell_id: UUID | None = None,
):
    """Summary-only page; complete envelopes belong to individual cell detail."""
    if (cursor_position is None) != (cursor_cell_id is None):
        raise _error(422, "invalid_cursor", "Both cell cursor fields are required")
    cursor = (
        BenchmarkCellCursor(cursor_position, cursor_cell_id)
        if cursor_position is not None and cursor_cell_id is not None else None
    )
    with SessionLocal() as session:
        return BenchmarkRepository(session).list_cells(
            job_id=job_id, owner_subject=_owner(principal), cursor=cursor, limit=limit,
        )


@router.get("/{job_id}/cells/{cell_id}", response_model=BenchmarkCellDetail, responses=examples.json_example(examples.CELL))
def get_cell(
    job_id: UUID, cell_id: UUID,
    principal: dict[str, Any] = Depends(require_benchmark_read),
):
    with SessionLocal() as session:
        result = BenchmarkRepository(session).get_cell(
            job_id=job_id, cell_id=cell_id, owner_subject=_owner(principal),
        )
        if result is None:
            raise _error(404, "not_found", "Benchmark resource not found")
        return result


@router.post("/{job_id}/cancel", response_model=BenchmarkJobDetail, responses=examples.json_example(examples.CANCELLED))
def cancel_job(job_id: UUID, principal: dict[str, Any] = Depends(require_benchmark_cancel)):
    with SessionLocal() as session:
        repository = BenchmarkRepository(session)
        repository.request_cancellation(
            job_id=job_id, owner_subject=_owner(principal),
            requested_at=datetime.now(timezone.utc),
        )
        # Freeze the response before releasing the job lock: a terminal SQL-only
        # job can be deleted by another authorized request immediately afterward.
        result = repository.get_job(job_id=job_id, owner_subject=_owner(principal))
        session.commit()
        return result


@router.delete("/{job_id}", status_code=204,
               responses={204: {"description": "Empty response, also for unknown or already deleted jobs"}})
def delete_job(job_id: UUID, principal: dict[str, Any] = Depends(require_benchmark_delete)):
    """Unknown, foreign-owned and previously deleted jobs share the same response."""
    with SessionLocal() as session:
        try:
            BenchmarkRepository(session).delete_terminal_job(
                job_id=job_id, owner_subject=_owner(principal),
            )
        except ValueError as exc:
            raise _error(409, "lifecycle_conflict", "Benchmark job must be retained") from exc
        session.commit()
    return Response(status_code=204)


@router.get("/{job_id}/cells/{cell_id}/invocations", response_model=BenchmarkInvocationPage,
            responses=examples.json_example({"items": [], "next_after_ordinal": None}))
def list_invocations(
    job_id: UUID,
    cell_id: UUID,
    principal: dict[str, Any] = Depends(require_benchmark_read),
    after_ordinal: int = Query(default=-1, ge=-1),
    limit: int | None = Query(default=None, ge=1),
):
    """Page all stored invocation telemetry, preserving unavailable values as null."""
    with SessionLocal() as session:
        repository = BenchmarkRepository(session)
        rows = repository.list_invocations(
            job_id=job_id, cell_id=cell_id, owner_subject=_owner(principal),
            after_ordinal=after_ordinal, limit=limit,
        )
        items = tuple(BenchmarkInvocationResponse.model_validate(row) for row in rows)
        has_more = bool(items) and bool(repository.list_invocations(
            job_id=job_id, cell_id=cell_id, owner_subject=_owner(principal),
            after_ordinal=items[-1].ordinal, limit=1,
        ))
        return BenchmarkInvocationPage(
            items=items, next_after_ordinal=items[-1].ordinal if has_more else None,
        )


@router.get("/{job_id}/events", response_class=Response, responses={
    200: {"content": {"text/event-stream": {"schema": {"type": "string"},
        "example": f'id: {examples.JOB_ID}:1\nevent: benchmark.event\ndata: {{"event_type":"job.created","payload":{{}}}}\n\n'}}},
    410: {"description": "Replay history expired; refresh status and use resume_after"},
    429: {"description": "Principal event connection limit reached"},
})
async def stream_events(
    request: Request, job_id: UUID,
    principal: dict[str, Any] = Depends(require_benchmark_read),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    """SSE with job-scoped durable IDs; expired history explicitly requires resync."""
    return await create_event_response(request, job_id, _owner(principal))
