"""Curator-consistent catalog discovery and side-effect-free plan preview."""

import json
import logging
from pathlib import Path
from typing import Any, Sequence, TypeVar

from anyio.to_thread import run_sync
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from src.api.benchmark_curator import require_benchmark_read_curator
from src.api.benchmark_gate import require_benchmark_api
from src.api.benchmark_sources import _catalog as input_resolver_catalog
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import BenchmarkRouteCatalog, BenchmarkSuite
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.benchmarks.planning import resolve_execution_plan
from src.lib.benchmarks.runtime_catalog import build_curator_route_catalog
from src.lib.benchmarks.suites import _digest, load_checked_in_suites
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.openai_agents.config import (
    get_benchmark_admission_max_bytes, get_benchmark_api_enabled,
    get_benchmark_catalog_max_response_bytes, get_benchmark_default_page_size,
    get_benchmark_environment_id, get_benchmark_execution_enabled,
    get_benchmark_max_page_size, get_benchmark_root, get_benchmark_worker_enabled,
)
from src.models.sql.database import SessionLocal
from src.schemas.benchmark_catalog import (
    BenchmarkCatalogPage, BenchmarkPlanPreviewRequest, BenchmarkPlanPreviewResponse,
    BenchmarkSuitePage, BenchmarkSuiteResponse, BenchmarkSuiteSummary, CatalogSection,
)
from src.schemas.benchmark_jobs import BenchmarkErrorResponse, admission_body_schema
from src.schemas import benchmark_catalog_examples as examples

logger = logging.getLogger(__name__)
Item = TypeVar("Item")


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


class BenchmarkCatalogRoute(APIRoute):
    """Keep validation/auth/dependency errors stable and free of caller data."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise _error(422, "invalid_request", "Invalid benchmark catalog request") from None
            except HTTPException as exc:
                if isinstance(exc.detail, dict):
                    raise
                code, message = {
                    401: ("authorization_required", "Verified benchmark identity required"),
                    403: ("capability_required", "Benchmark read capability required"),
                    503: ("authorization_unavailable", "Benchmark authentication unavailable"),
                }.get(exc.status_code, ("request_failed", "Benchmark request failed"))
                raise HTTPException(exc.status_code, {"code": code, "message": message}, headers=exc.headers) from None
            except Exception as exc:
                raise_sanitized_http_exception(
                    logger, status_code=503,
                    detail={"code": "catalog_unavailable", "message": "Benchmark catalog is unavailable"},
                    log_message="Benchmark catalog dependency unavailable",
                    exc=sanitized_benchmark_error("catalog_preview", type(exc).__name__),
                )

        return wrapped


_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {
        "model": BenchmarkErrorResponse,
        "content": {"application/json": {"example": {"detail": {"code": code, "message": message}}}},
    }
    for status, code, message in (
        (401, "authorization_required", "Verified benchmark identity required"),
        (403, "capability_required", "Benchmark read capability required"),
        (404, "not_found", "Checked-in benchmark suite not found or API disabled"),
        (409, "catalog_drift", "Benchmark catalog changed; refresh discovery"),
        (413, "response_too_large", "Benchmark response exceeds configured limit"),
        (415, "invalid_content_type", "Benchmark preview requires application/json"),
        (422, "invalid_plan", "Benchmark suite cannot be resolved"),
        (503, "catalog_unavailable", "Benchmark catalog is unavailable"),
    )
}

router = APIRouter(
    prefix="/api/v1/benchmarks", tags=["Benchmarks - Catalog"],
    dependencies=[Depends(require_benchmark_api)],
    route_class=BenchmarkCatalogRoute, responses=_ERRORS,
)


def _current_catalog(curator: BenchmarkCuratorContext) -> BenchmarkRouteCatalog:
    # This sync dependency/worker function owns its Session on a worker thread.
    # It only reads visible agents/configuration and never creates benchmark rows.
    with SessionLocal() as session:
        return build_curator_route_catalog(session, curator)


def _catalog_dependency(curator: BenchmarkCuratorContext = Depends(require_benchmark_read_curator)) -> BenchmarkRouteCatalog:
    return _current_catalog(curator)


def _visible_suites(catalog: BenchmarkRouteCatalog) -> tuple[BenchmarkSuite, ...]:
    visible = {(item.target.kind, item.target.id) for item in catalog.targets}
    return tuple(sorted(
        (suite for suite in load_checked_in_suites(Path(get_benchmark_root()))
         if all((case.target.kind, case.target.id) in visible for case in suite.cases)),
        key=lambda suite: suite.suite_id,
    ))


def _page(items: Sequence[Item], keys: Sequence[str], *, revision: str,
          expected_revision: str | None, cursor: str | None, limit: int | None) -> tuple[tuple[Item, ...], str | None]:
    if expected_revision is not None and expected_revision != revision:
        raise _error(409, "catalog_drift", "Benchmark catalog changed; refresh discovery")
    if cursor is not None and expected_revision is None:
        raise _error(422, "invalid_cursor", "A continuation cursor requires its catalog digest")
    start = 0
    if cursor is not None:
        if cursor not in keys:
            raise _error(422, "invalid_cursor", "Cursor does not belong to this catalog section")
        start = keys.index(cursor) + 1
    size = min(limit if limit is not None else get_benchmark_default_page_size(), get_benchmark_max_page_size())
    stop = min(start + size, len(items))
    return tuple(items[start:stop]), keys[stop - 1] if stop < len(items) else None


def _bounded_response(payload: BaseModel) -> JSONResponse:
    data = payload.model_dump(mode="json")
    response = JSONResponse(data, headers={"Cache-Control": "no-store"})
    if len(response.body) > get_benchmark_catalog_max_response_bytes():
        raise _error(413, "response_too_large", "Benchmark response exceeds configured limit; request a smaller page or suite")
    return response


@router.get("/catalog", response_model=BenchmarkCatalogPage, responses=examples.response(examples.CATALOG))
def get_catalog(
    request: Request,
    section: CatalogSection = "targets",
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = None,
    catalog_digest: str | None = Query(default=None, pattern=r"^sha256:[0-9a-f]{64}$"),
    catalog: BenchmarkRouteCatalog = Depends(_catalog_dependency),
):
    """Page one canonical section; all sections share the whole-catalog digest.

    Continue with section, next_cursor and catalog_digest. Reassemble the three
    sections to obtain the existing BenchmarkRouteCatalog shape.
    """
    entries = getattr(catalog, section)
    key = {
        "targets": lambda item: json.dumps([item.target.kind, item.target.id], separators=(",", ":")),
        "route_slots": lambda item: item.slot,
        "models": lambda item: json.dumps([item.provider, item.model], separators=(",", ":")),
    }[section]
    # Preserve the factory's canonical array order (notably supervisor first),
    # so clients can reconstruct the exact catalog used by its digest.
    ordered = entries
    revision = _digest(catalog.model_dump(mode="json"))
    items, next_cursor = _page(ordered, [key(item) for item in ordered], revision=revision,
                              expected_revision=catalog_digest, cursor=cursor, limit=limit)
    return _bounded_response(BenchmarkCatalogPage(
        catalog_digest=revision, environment_id=get_benchmark_environment_id(),
        api_enabled=get_benchmark_api_enabled(), execution_enabled=get_benchmark_execution_enabled(),
        worker_enabled=get_benchmark_worker_enabled(), resolver_ids=input_resolver_catalog(request).resolver_ids,
        section=section, items=items, total_items=len(ordered), next_cursor=next_cursor,
    ))


@router.get("/suites", response_model=BenchmarkSuitePage, responses=examples.response(examples.SUITES))
def list_suites(
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = None,
    suite_catalog_digest: str | None = Query(default=None, pattern=r"^sha256:[0-9a-f]{64}$"),
    catalog: BenchmarkRouteCatalog = Depends(_catalog_dependency),
):
    """Discover checked-in suites whose targets are visible to this curator."""
    suites = _visible_suites(catalog)
    summaries = tuple(BenchmarkSuiteSummary(
        suite_id=suite.suite_id, suite_digest=_digest(suite.model_dump(mode="json")),
        case_count=len(suite.cases), configuration_count=len(suite.configurations), repetitions=suite.repetitions,
    ) for suite in suites)
    revision = _digest([item.model_dump(mode="json") for item in summaries])
    items, next_cursor = _page(summaries, [suite.suite_id for suite in suites], revision=revision,
                              expected_revision=suite_catalog_digest, cursor=cursor, limit=limit)
    return _bounded_response(BenchmarkSuitePage(suite_catalog_digest=revision, items=items,
                                              total_items=len(summaries), next_cursor=next_cursor))


def _find_suite(catalog: BenchmarkRouteCatalog, suite_id: str) -> BenchmarkSuite:
    for suite in _visible_suites(catalog):
        if suite.suite_id == suite_id:
            return suite
    raise _error(404, "not_found", "Checked-in benchmark suite not found")


@router.get("/suites/{suite_id}", response_model=BenchmarkSuiteResponse, responses=examples.response(examples.SUITE))
def get_suite(suite_id: str, catalog: BenchmarkRouteCatalog = Depends(_catalog_dependency)):
    """Return one checked-in execution-only suite, never a caller-selected path."""
    suite = _find_suite(catalog, suite_id)
    return _bounded_response(BenchmarkSuiteResponse(suite=suite, suite_digest=_digest(suite.model_dump(mode="json"))))


async def _preview_body(request: Request) -> BenchmarkPlanPreviewRequest:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise _error(415, "invalid_content_type", "Benchmark preview requires application/json")
    payload = bytearray()
    limit = get_benchmark_admission_max_bytes()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > limit:
            raise _error(413, "oversize_submission", "Benchmark preview body exceeds configured limit")
        payload.extend(chunk)
    try:
        return BenchmarkPlanPreviewRequest.model_validate_json(payload)
    except (ValueError, ValidationError):
        raise _error(422, "invalid_request", "Invalid benchmark preview request") from None


def _preview(request: Request, payload: BenchmarkPlanPreviewRequest, catalog: BenchmarkRouteCatalog) -> JSONResponse:
    if payload.catalog_digest != _digest(catalog.model_dump(mode="json")):
        raise _error(409, "catalog_drift", "Benchmark catalog changed; refresh discovery")
    suite = payload.suite
    if payload.checked_in_suite is not None:
        suite = _find_suite(catalog, payload.checked_in_suite.suite_id)
        if _digest(suite.model_dump(mode="json")) != payload.checked_in_suite.suite_digest:
            raise _error(409, "suite_drift", "Checked-in benchmark suite changed; refresh discovery")
    assert suite is not None  # XOR enforced by the strict request model.
    registered = input_resolver_catalog(request).resolver_ids
    if any(case.input.resolver not in registered for case in suite.cases):
        raise _error(422, "unknown_resolver", "Benchmark input resolver is not registered")
    try:
        plan = resolve_execution_plan(suite, catalog)
    except BenchmarkCatalogError:
        raise _error(422, "invalid_plan", "Benchmark suite cannot be resolved") from None
    return _bounded_response(BenchmarkPlanPreviewResponse(plan=plan, cell_count=len(plan.cells)))


@router.post("/plans/validate", response_model=BenchmarkPlanPreviewResponse,
             responses=examples.response(examples.PREVIEW),
             openapi_extra=admission_body_schema(BenchmarkPlanPreviewRequest, example=examples.PREVIEW_REQUEST))
async def validate_plan(request: Request, catalog: BenchmarkRouteCatalog = Depends(_catalog_dependency)):
    """Preview only. Admission recomputes the plan and verifies/freeze inputs anew."""
    payload = await _preview_body(request)
    return await run_sync(_preview, request, payload, catalog)
