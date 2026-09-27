"""Benchmark API: convert an uploaded PDF or an ABC paper into a frozen input.

A conversion runs in the background with AI Curation's standard document
conversion. The result is frozen as an ``application/json`` benchmark input
snapshot owned by the calling service, so that service's benchmark jobs can use
it. The verified curator is recorded on the conversion, never as the owner.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from src.api.benchmark_auth import require_benchmark_source_read
from src.api.benchmark_curator import require_benchmark_source_curator
from src.api.benchmark_sources import BenchmarkSourceRoute
from src.lib.benchmarks.document_conversions import (
    INPUT_KIND_ABC_REFERENCE,
    INPUT_KIND_PDF,
    STATUS_FAILED,
    ConversionIdempotencyConflict,
    DocumentConversionRepository,
    reconcile_stale_conversions,
    run_conversion,
)
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotError,
    BenchmarkSnapshotRepository,
    configured_benchmark_snapshot_store,
)
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.observability.background_tasks import add_observed_background_task
from src.lib.openai_agents.config import (
    get_benchmark_enabled,
    get_benchmark_max_input_bytes,
    get_benchmark_source_selection_max_bytes,
    get_benchmark_source_timeout_seconds,
)
from src.models.sql.benchmark import BenchmarkInputSnapshot
from src.models.sql.database import SessionLocal
from src.schemas.benchmark_document_conversions import (
    BenchmarkDocumentConversionAbcRequest,
    BenchmarkDocumentConversionAccepted,
    BenchmarkDocumentConversionFailure,
    BenchmarkDocumentConversionStatus,
)

PREFIX = "/api/v1/benchmarks/sources/document-conversions"

router = APIRouter(
    prefix=PREFIX,
    tags=["Benchmarks - Sources"],
    route_class=BenchmarkSourceRoute,
)
logger = logging.getLogger(__name__)

_DELEGATED_HEADER = "x-benchmark-delegated-source-authorization"
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_IDEMPOTENCY_KEY_LENGTH = 255
_PDF_MEDIA = "application/pdf"
_JSON_MEDIA = "application/json"
_PDF_SIGNATURE = b"%PDF-"


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"error": code, "message": message},
                         headers={"Cache-Control": "no-store"})


def _unavailable(operation: str, exc: Exception) -> NoReturn:
    raise_sanitized_http_exception(
        logger,
        status_code=503,
        detail={"error": "source_unavailable",
                "message": "Document conversion is unavailable right now"},
        log_message="Benchmark document conversion storage is unavailable",
        exc=sanitized_benchmark_error(operation, type(exc).__name__),
    )


def _require_conversion_request(request: Request) -> None:
    if not get_benchmark_enabled():
        raise _error(404, "not_found", "Benchmark API is disabled")
    if _DELEGATED_HEADER in request.headers:
        raise _error(400, "unexpected_delegated_authorization",
                     "Document conversion does not accept source credentials")


def _idempotency_key(value: str | None) -> str:
    if (
        value is None
        or not value.strip()
        or value != value.strip()
        or len(value) > _MAX_IDEMPOTENCY_KEY_LENGTH
    ):
        raise _error(400, "invalid_reference",
                     "Idempotency-Key is required and must be at most 255 characters")
    return value


async def _read_body(request: Request, maximum: int) -> bytes:
    body = bytearray()
    try:
        async with asyncio.timeout(get_benchmark_source_timeout_seconds()):
            async for chunk in request.stream():
                if len(body) + len(chunk) > maximum:
                    raise _error(413, "oversize_payload", "Uploaded document exceeds the limit")
                body.extend(chunk)
    except TimeoutError:
        raise _error(408, "source_timeout", "Uploaded document was not received in time") from None
    return bytes(body)


def _store_pdf(content: bytes, digest: str) -> str:
    try:
        return configured_benchmark_snapshot_store().put(digest=digest, content=content)
    except (BenchmarkSnapshotError, OSError) as exc:
        _unavailable("document_conversion_upload", exc)


def _create_conversion(
    *,
    principal: dict[str, Any],
    curator: BenchmarkCuratorContext,
    input_kind: str,
    source_digest: str | None,
    source_blob_reference: str | None,
    abc_reference: str | None,
    idempotency_key: str,
) -> tuple[BenchmarkDocumentConversionAccepted, bool]:
    try:
        reconcile_stale_conversions()
        with SessionLocal() as db:
            row, created = DocumentConversionRepository().create_or_get(
                db,
                owner_subject=str(principal["sub"]),
                service_principal=str(principal["client_id"]),
                curator=curator,
                input_kind=input_kind,
                source_digest=source_digest,
                source_blob_reference=source_blob_reference,
                abc_reference=abc_reference,
                idempotency_key=idempotency_key,
            )
            accepted = BenchmarkDocumentConversionAccepted.model_validate(
                {"conversion_id": row.id, "status": row.status}
            )
            db.commit()
            return accepted, created
    except ConversionIdempotencyConflict:
        raise _error(409, "conflict",
                     "Idempotency-Key is already used for a different document") from None
    except SQLAlchemyError as exc:
        _unavailable("document_conversion_create", exc)


@router.post(
    "", status_code=202, response_model=BenchmarkDocumentConversionAccepted,
    openapi_extra={"requestBody": {"required": True, "content": {
        _PDF_MEDIA: {"schema": {"type": "string", "format": "binary"}},
        _JSON_MEDIA: {"schema": BenchmarkDocumentConversionAbcRequest.model_json_schema()},
    }}},
)
async def start_document_conversion(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    content_digest: str | None = Header(default=None, alias="X-Benchmark-Content-Digest"),
    principal: dict[str, Any] = Depends(require_benchmark_source_read),
    curator: BenchmarkCuratorContext = Depends(require_benchmark_source_curator),
) -> BenchmarkDocumentConversionAccepted:
    """Start converting exact PDF bytes or an ABC paper; replays return the same conversion."""
    _require_conversion_request(request)
    key = _idempotency_key(idempotency_key)
    media, separator, parameter = request.headers.get("content-type", "").partition(";")
    media = media.strip().lower()
    if request.headers.get("content-encoding", "identity") != "identity" or (
        separator and (media != _JSON_MEDIA or parameter.strip().lower() != "charset=utf-8")
    ):
        raise _error(415, "invalid_content_type", "Unsupported upload encoding")

    source_digest = source_blob_reference = abc_reference = None
    if media == _PDF_MEDIA:
        if content_digest is None or not _DIGEST_PATTERN.fullmatch(content_digest):
            raise _error(400, "invalid_reference",
                         "X-Benchmark-Content-Digest must be sha256:<64 lowercase hex>")
        content = await _read_body(request, get_benchmark_max_input_bytes())
        if f"sha256:{hashlib.sha256(content).hexdigest()}" != content_digest:
            raise _error(400, "invalid_document", "Uploaded PDF does not match its digest")
        if not content.startswith(_PDF_SIGNATURE):
            raise _error(400, "invalid_document", "Uploaded document is not a PDF")
        input_kind, source_digest = INPUT_KIND_PDF, content_digest
        source_blob_reference = await asyncio.to_thread(_store_pdf, content, content_digest)
    elif media == _JSON_MEDIA:
        body = await _read_body(request, get_benchmark_source_selection_max_bytes())
        try:
            selection = BenchmarkDocumentConversionAbcRequest.model_validate_json(body)
        except ValidationError:
            # Validation errors echo input values; the reference stays out of responses.
            raise _error(400, "invalid_reference",
                         "Body must be {\"abc_reference\": \"AGRKB:<digits>\"}") from None
        input_kind, abc_reference = INPUT_KIND_ABC_REFERENCE, selection.abc_reference
    else:
        raise _error(415, "invalid_content_type",
                     "Send application/pdf bytes or an application/json ABC reference")

    accepted, created = await asyncio.to_thread(
        _create_conversion,
        principal=principal, curator=curator, input_kind=input_kind,
        source_digest=source_digest, source_blob_reference=source_blob_reference,
        abc_reference=abc_reference, idempotency_key=key,
    )
    if created:
        add_observed_background_task(
            background_tasks,
            run_conversion,
            accepted.conversion_id,
            authorized_group_ids=curator.active_groups,
            task_name="benchmark.document_conversion",
            tags={"component": "benchmark_document_conversion",
                  "conversion_id": str(accepted.conversion_id)},
        )
    response.headers["Location"] = f"{PREFIX}/{accepted.conversion_id}"
    return accepted


def _read_conversion(conversion_id: UUID, owner: str) -> BenchmarkDocumentConversionStatus:
    try:
        # A conversion lost to a restart must not read as unfinished forever.
        reconcile_stale_conversions()
        with SessionLocal() as db:
            row = DocumentConversionRepository().get_for_owner(db, conversion_id, owner)
            if row is None:
                raise _error(404, "not_found", "Document conversion was not found")
            snapshot = None
            if row.snapshot_id is not None:
                snapshot = BenchmarkSnapshotRepository.receipt(
                    db.get(BenchmarkInputSnapshot, row.snapshot_id)
                )
            error = None
            if row.status == STATUS_FAILED:
                error = BenchmarkDocumentConversionFailure(
                    code=row.error_code, message=row.error_message,
                )
            return BenchmarkDocumentConversionStatus(
                conversion_id=row.id, status=row.status, error=error, snapshot=snapshot,
                conversion_identity=row.conversion_identity, created_at=row.created_at,
                completed_at=row.completed_at,
            )
    except SQLAlchemyError as exc:
        _unavailable("document_conversion_status", exc)


@router.get("/{conversion_id}", response_model=BenchmarkDocumentConversionStatus)
async def get_document_conversion(
    conversion_id: UUID,
    request: Request,
    principal: dict[str, Any] = Depends(require_benchmark_source_read),
) -> BenchmarkDocumentConversionStatus:
    """Read one conversion started by the calling service."""
    _require_conversion_request(request)
    return await asyncio.to_thread(_read_conversion, conversion_id, str(principal["sub"]))


__all__ = ["router"]
