"""Scoped read-only API for immutable benchmark source materialization."""

from __future__ import annotations

from collections.abc import Iterable
import logging
from pathlib import Path
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from src.api.benchmark_auth import require_benchmark_source_read
from src.config import get_pdf_storage_path
from src.lib.benchmarks.input_resolvers import (
    BenchmarkInputResolver,
    BenchmarkInputResolverCatalog,
    BenchmarkSourceRequestContext,
    BenchmarkSourceError,
    CheckedInFixtureResolver,
    DelegatedSourceAuthorization,
)
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import BenchmarkInputReference
from src.lib.benchmarks.suites import load_checked_in_suites
from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotError,
    BenchmarkSnapshotRepository,
    FrozenBenchmarkInputSnapshot,
    FrozenBenchmarkSnapshotResolver,
    configured_benchmark_snapshot_store,
)
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.openai_agents.config import (
    get_benchmark_enabled,
    get_benchmark_delegated_source_auth_max_bytes,
    get_benchmark_max_input_bytes,
    get_benchmark_root,
    get_benchmark_source_timeout_seconds,
)
from src.services.benchmark_document_source import LocalDocumentResolver
from src.models.sql.database import get_db


class BenchmarkSourceRoute(APIRoute):
    """Normalize request-schema failures to the benchmark source error contract."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request):
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "invalid_reference",
                        "message": "Benchmark input reference is invalid",
                    },
                ) from exc

        return custom_handler


router = APIRouter(
    prefix="/api/v1/benchmarks/sources",
    tags=["Benchmarks - Sources"],
    route_class=BenchmarkSourceRoute,
)
logger = logging.getLogger(__name__)

_ERROR_STATUS = {
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

_DELEGATED_HEADER = "x-benchmark-delegated-source-authorization"


def delegated_source_request_context(
    request: Request, *, principal_subject: str
) -> BenchmarkSourceRequestContext:
    """Parse the dedicated bearer without copying it into validation models."""

    raw = request.headers.get(_DELEGATED_HEADER)
    if raw is None:
        return BenchmarkSourceRequestContext(principal_subject=principal_subject)
    if len(raw.encode("utf-8")) > get_benchmark_delegated_source_auth_max_bytes():
        raise BenchmarkSourceError(
            "invalid_delegated_authorization",
            "Delegated source authorization header is invalid",
        )
    scheme, separator, bearer = raw.partition(" ")
    if (
        separator != " "
        or scheme != "Bearer"
        or not bearer
        or bearer.strip() != bearer
        or any(character.isspace() for character in bearer)
    ):
        raise BenchmarkSourceError(
            "invalid_delegated_authorization",
            "Delegated source authorization header is invalid",
        )
    return BenchmarkSourceRequestContext(
        principal_subject=principal_subject,
        delegated_authorization=DelegatedSourceAuthorization(bearer),
    )


def build_default_input_resolver_catalog(
    *,
    extra_resolvers: Iterable[BenchmarkInputResolver] = (),
) -> BenchmarkInputResolverCatalog:
    """Build the startup-owned catalog; duplicate IDs fail construction."""

    benchmark_root = Path(get_benchmark_root())
    fixture_references = {
        case.input.reference
        for suite in load_checked_in_suites(benchmark_root)
        for case in suite.cases
        if case.input.resolver == CheckedInFixtureResolver.resolver_id
    }
    resolvers: tuple[BenchmarkInputResolver, ...] = (
        CheckedInFixtureResolver(
            benchmark_root, allowed_references=fixture_references
        ),
        LocalDocumentResolver(storage_root_provider=get_pdf_storage_path),
        FrozenBenchmarkSnapshotResolver(),
        *tuple(extra_resolvers),
    )
    return BenchmarkInputResolverCatalog(
        resolvers,
        timeout_seconds=get_benchmark_source_timeout_seconds(),
        max_input_bytes=get_benchmark_max_input_bytes(),
    )


def install_benchmark_input_resolvers(
    application: Any,
    *,
    extra_resolvers: Iterable[BenchmarkInputResolver] = (),
) -> None:
    """Register reviewed resolver extensions without loading source metadata."""

    extensions = tuple(extra_resolvers)
    BenchmarkInputResolverCatalog.validate_registration(
        (
            CheckedInFixtureResolver(Path("."), allowed_references=()),
            LocalDocumentResolver(storage_root_provider=get_pdf_storage_path),
            FrozenBenchmarkSnapshotResolver(),
            *extensions,
        )
    )
    application.state.benchmark_input_resolver_extensions = extensions


def _catalog(request: Request) -> BenchmarkInputResolverCatalog:
    catalog = getattr(request.app.state, "benchmark_input_resolvers", None)
    if isinstance(catalog, BenchmarkInputResolverCatalog):
        return catalog
    extensions = getattr(request.app.state, "benchmark_input_resolver_extensions", ())
    try:
        catalog = build_default_input_resolver_catalog(extra_resolvers=extensions)
    except BenchmarkCatalogError:
        raise BenchmarkSourceError(
            "source_unavailable",
            "Benchmark input resolver catalog is unavailable",
        )
    request.app.state.benchmark_input_resolvers = catalog
    return catalog


def _sanitized_source_error(exc: BenchmarkSourceError) -> BenchmarkSourceError:
    try:
        raise BenchmarkSourceError(exc.code, str(exc)) from None
    except BenchmarkSourceError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def _raise_source_error(exc: BenchmarkSourceError) -> NoReturn:
    status_code = _ERROR_STATUS.get(exc.code, 503)
    detail = {"error": exc.code, "message": str(exc)}
    if status_code >= 500:
        raise_sanitized_http_exception(
            logger,
            status_code=status_code,
            detail=detail,
            log_message="Registered benchmark input source is unavailable",
            exc=_sanitized_source_error(exc),
        )
    raise HTTPException(status_code=status_code, detail=detail) from exc


@router.post("/materialize", response_model=FrozenBenchmarkInputSnapshot)
async def materialize_benchmark_source(
    request: Request,
    payload: BenchmarkInputReference,
    principal: dict[str, Any] = Depends(require_benchmark_source_read),
    db: Session = Depends(get_db),
) -> FrozenBenchmarkInputSnapshot:
    """Synchronously materialize and durably freeze an allowlisted source."""

    if not get_benchmark_enabled():
        raise HTTPException(status_code=404, detail="Benchmark API is disabled")
    try:
        context = delegated_source_request_context(
            request, principal_subject=str(principal["sub"])
        )
        catalog = _catalog(request)
        catalog.validate_delegated_selection((payload,), context)
        materialized = await catalog.materialize(
            payload,
            request_context=context,
        )
        snapshots = BenchmarkSnapshotRepository(
            db, configured_benchmark_snapshot_store()
        )
        snapshot = snapshots.freeze_input(
            materialized,
            owner_subject=context.principal_subject,
            service_principal=str(principal["client_id"]),
        )
        db.commit()
        return snapshots.receipt(snapshot)
    except BenchmarkSourceError as exc:
        db.rollback()
        _raise_source_error(exc)
    except BenchmarkSnapshotError as exc:
        db.rollback()
        raise_sanitized_http_exception(
            logger,
            status_code=503,
            detail={
                "error": "source_unavailable",
                "message": "Benchmark input snapshot could not be committed",
            },
            log_message="Benchmark input snapshot could not be committed",
            exc=exc,
        )


__all__ = [
    "build_default_input_resolver_catalog",
    "delegated_source_request_context",
    "install_benchmark_input_resolvers",
    "materialize_benchmark_source",
    "router",
]
