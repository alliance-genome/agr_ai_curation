"""Scoped read-only API for immutable benchmark source materialization."""

from __future__ import annotations

from collections.abc import Iterable
import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
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
    BenchmarkSourceMetadata,
    BenchmarkSourceProvenance,
    MaterializedBenchmarkInput,
)
from src.lib.benchmarks.document_inputs import decode_frozen_document
from src.lib.benchmarks.loader import BenchmarkCatalogError
from src.lib.benchmarks.models import BenchmarkInputReference
from src.lib.benchmarks.observability import sanitized_benchmark_error
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
from src.models.sql.benchmark import BenchmarkInputSnapshot
from src.models.sql.database import get_db, SessionLocal
from src.schemas.benchmark_sources import BenchmarkSnapshotUploadMetadata


class BenchmarkSourceRoute(APIRoute):
    """Normalize request-schema failures to the benchmark source error contract."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request):
            try:
                response = await original_handler(request)
            except RequestValidationError:
                pass
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            else:
                response.headers["Cache-Control"] = "no-store"
                return response
            # Validation values must not survive in chained exceptions.
            raise HTTPException(422, {
                "error": "invalid_reference", "message": "Benchmark input reference is invalid",
            }, headers={"Cache-Control": "no-store"})

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


def _transfer_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"error": code, "message": message},
                         headers={"Cache-Control": "no-store"})


def _require_saved_byte_transfer(request: Request) -> None:
    if not get_benchmark_enabled():
        raise _transfer_error(404, "missing_source", "Benchmark API is disabled")
    if _DELEGATED_HEADER in request.headers:
        raise _transfer_error(400, "unexpected_delegated_authorization",
                              "Saved input transfer does not accept source credentials")


def _freeze_uploaded_content(
    content: bytes, metadata: BenchmarkSnapshotUploadMetadata, owner: str, service: str,
) -> FrozenBenchmarkInputSnapshot:
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if digest != metadata.digest:
        raise _transfer_error(409, "digest_conflict", "Uploaded input digest does not match")
    try:
        decode_frozen_document(content, content_type=metadata.content_type)
        text_content = content.decode("utf-8")
    except Exception:
        pass
    else:
        # This provenance attests only to bytes uploaded by the authenticated
        # owner. It never claims authorization against an original source.
        reference = json.dumps({
            "schema": "uploaded_document/v1", "content_type": metadata.content_type,
            "digest": digest,
        }, sort_keys=True, separators=(",", ":"))
        provenance = BenchmarkSourceProvenance(
            resolver="uploaded_document", reference=reference, version="1", digest=digest,
        )
        source = MaterializedBenchmarkInput(
            resolver=provenance.resolver, reference=reference, version="1", digest=digest,
            content=text_content,
            metadata=BenchmarkSourceMetadata(content_type=metadata.content_type,
                                             content_bytes=len(content)),
            provenance=provenance,
        )
        try:
            with SessionLocal() as db:
                repository = BenchmarkSnapshotRepository(db, configured_benchmark_snapshot_store())
                snapshot = repository.freeze_input(source, owner_subject=owner,
                                                    service_principal=service)
                receipt = repository.receipt(snapshot)
                db.commit()
                return receipt
        except (BenchmarkSnapshotError, SQLAlchemyError, OSError) as exc:
            error_type = type(exc).__name__
        raise_sanitized_http_exception(
            logger,
            status_code=503,
            detail={"error": "source_unavailable", "message": "Benchmark input could not be stored"},
            log_message="Uploaded benchmark snapshot could not be stored",
            exc=sanitized_benchmark_error("snapshot_upload", error_type),
        )
    raise _transfer_error(422, "invalid_document", "Uploaded input is not a supported document")


@router.post(
    "/snapshots", response_model=FrozenBenchmarkInputSnapshot,
    openapi_extra={"requestBody": {"required": True, "content": {
        media: {"schema": {"type": "string", "format": "binary"}}
        for media in ("text/plain", "text/markdown", "application/json", "application/xml")
    }}},
)
async def upload_benchmark_snapshot(
    request: Request,
    content_digest: str = Header(alias="X-Benchmark-Content-Digest", pattern=r"^sha256:[0-9a-f]{64}$"),
    principal: dict[str, Any] = Depends(require_benchmark_source_read),
) -> FrozenBenchmarkInputSnapshot:
    """Freeze exact UTF-8 bytes, with server-derived uploaded-content provenance."""
    _require_saved_byte_transfer(request)
    media, separator, parameter = request.headers.get("content-type", "").partition(";")
    if (separator and parameter.strip().lower() != "charset=utf-8"
            or request.headers.get("content-encoding", "identity") != "identity"):
        raise _transfer_error(415, "invalid_content_type", "Unsupported upload encoding")
    try:
        metadata = BenchmarkSnapshotUploadMetadata.model_validate({
            "content_type": media.strip().lower(), "digest": content_digest,
        })
    except ValueError:
        pass
    else:
        maximum = get_benchmark_max_input_bytes()
        body = bytearray()
        try:
            async with asyncio.timeout(get_benchmark_source_timeout_seconds()):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > maximum:
                        raise _transfer_error(413, "oversize_payload", "Uploaded input exceeds limit")
                    body.extend(chunk)
        except TimeoutError:
            pass
        else:
            return await asyncio.to_thread(
                _freeze_uploaded_content, bytes(body), metadata,
                str(principal["sub"]), str(principal["client_id"]),
            )
        raise _transfer_error(408, "source_timeout", "Uploaded input was not received in time")
    raise _transfer_error(415, "invalid_content_type", "Unsupported document content type")


def _read_snapshot_content(snapshot_id: UUID, owner: str, maximum: int) -> Response:
    try:
        with SessionLocal() as db:
            snapshot = db.scalar(select(BenchmarkInputSnapshot).where(
                BenchmarkInputSnapshot.id == snapshot_id,
                BenchmarkInputSnapshot.owner_subject == owner,
            ))
            if snapshot is None:
                raise _transfer_error(404, "missing_source", "Frozen benchmark input was not found")
            if snapshot.content_bytes > maximum:
                raise _transfer_error(413, "oversize_payload", "Frozen benchmark input exceeds limit")
            repository = BenchmarkSnapshotRepository(db, configured_benchmark_snapshot_store())
            content = repository.read_verified(snapshot_id, owner_subject=owner, max_bytes=maximum)
            return Response(content, media_type=snapshot.content_type, headers={
                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'attachment; filename="benchmark-input"',
                "X-Benchmark-Content-Digest": snapshot.digest,
            })
    except (BenchmarkSnapshotError, SQLAlchemyError, OSError) as exc:
        error_type = type(exc).__name__
    raise_sanitized_http_exception(
        logger,
        status_code=503,
        detail={"error": "source_unavailable", "message": "Frozen benchmark input could not be read"},
        log_message="Frozen benchmark snapshot could not be read",
        exc=sanitized_benchmark_error("snapshot_download", error_type),
    )


@router.get("/snapshots/{snapshot_id}/content", response_class=Response, responses={
    200: {"description": "Exact verified canonical bytes (never a blob URL)", "content": {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
    }},
})
async def download_benchmark_snapshot(
    snapshot_id: UUID, request: Request,
    principal: dict[str, Any] = Depends(require_benchmark_source_read),
) -> Response:
    """Read owner-scoped canonical bytes, without a source lookup or blob URL."""
    _require_saved_byte_transfer(request)
    return await asyncio.to_thread(_read_snapshot_content, snapshot_id,
                                   str(principal["sub"]), get_benchmark_max_input_bytes())


__all__ = [
    "build_default_input_resolver_catalog",
    "delegated_source_request_context",
    "install_benchmark_input_resolvers",
    "materialize_benchmark_source",
    "router",
]
