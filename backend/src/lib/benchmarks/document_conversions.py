"""Service-owned conversion of papers into frozen benchmark documents.

A conversion record is owned by the calling benchmark service subject. Reads
and idempotency keys are scoped to that owner; the requesting curator is
recorded separately. Failed conversions are terminal and never retried.

The conversion service turns an uploaded PDF, or a paper named by a reference
that the deployment's configured document-source provider resolves, into the
application's standard pipeline elements and freezes them as an
``application/json`` benchmark input snapshot owned by the calling service.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import tempfile
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.config import get_app_version
from src.lib.benchmarks.document_inputs import decode_frozen_document
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.input_resolvers import (
    BenchmarkSourceMetadata,
    BenchmarkSourceProvenance,
    MaterializedBenchmarkInput,
)
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.benchmarks.snapshots import (
    BenchmarkSnapshotError,
    BenchmarkSnapshotRepository,
    configured_benchmark_snapshot_store,
)
from src.lib.document_sources.figure_metadata import normalize_provider_figure_metadata_sidecar
from src.lib.document_sources.identifier_import import (
    ReferenceImportDecisionStatus,
    _validate_source_pdf_bytes,
    select_reference_import_candidate,
)
from src.lib.document_sources.ingestion import (
    DocumentSourceIngestionError,
    provider_markdown_to_pipeline_elements,
)
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceError,
    DocumentSourceProvider,
    ProviderBearerKind,
)
from src.lib.document_sources.registry import get_configured_document_source_provider
from src.lib.exceptions import ConfigurationError, PDFCancellationError, PDFParsingError
from src.lib.observability.runtime import report_runtime_exception
from src.lib.openai_agents.config import (
    get_benchmark_document_conversion_stale_seconds,
    get_benchmark_max_input_bytes,
)
from src.lib.pipeline.pdfx_parser import PDFX_FAILURE_DETAILS_KEY, PDFXParser
from src.models.sql.benchmark import BenchmarkDocumentConversion, BenchmarkInputSnapshot
from src.models.sql.database import SessionLocal


logger = logging.getLogger(__name__)


INPUT_KIND_PDF = "pdf"
INPUT_KIND_SOURCE_REFERENCE = "source_reference"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
INTERRUPTED_ERROR_CODE = "interrupted"
# The coarse steps a running conversion goes through, in order: getting the PDF
# (the upload, or the paper from the configured document source), extracting its
# text, then saving the converted document as a frozen benchmark input.
STAGE_FETCHING_SOURCE = "fetching_source"
STAGE_EXTRACTING_TEXT = "extracting_text"
STAGE_SAVING = "saving"
CONVERSION_STAGES = (STAGE_FETCHING_SOURCE, STAGE_EXTRACTING_TEXT, STAGE_SAVING)

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
# A provider-neutral identifier: bounded, non-empty, no surrounding whitespace
# and no control characters. The configured document-source provider decides
# whether it names a paper. The bound keeps the snapshot reference that embeds
# it within the snapshot reference column.
MAX_SOURCE_REFERENCE_LENGTH = 256
SOURCE_REFERENCE_PATTERN = (
    r"^[^\s\x00-\x1f\x7f-\x9f](?:[^\x00-\x1f\x7f-\x9f]*[^\s\x00-\x1f\x7f-\x9f])?$"
)
_SOURCE_REFERENCE_RE = re.compile(SOURCE_REFERENCE_PATTERN)
_ERROR_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_SUBJECT_LENGTH = 255
_MAX_IDEMPOTENCY_KEY_LENGTH = 255
_MAX_BLOB_REFERENCE_LENGTH = 2048
_MAX_ERROR_MESSAGE_LENGTH = 512


class ConversionIdempotencyConflict(ValueError):
    """An idempotency key is already bound to a different conversion input."""


class ConversionStateError(RuntimeError):
    """A conversion cannot make the requested lifecycle transition."""


def _require_text(value: str | None, *, name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"Conversion {name} must be a nonempty normalized string")
    if len(value) > max_length:
        raise ValueError(f"Conversion {name} is too long")
    return value


def _validate_input(
    *,
    input_kind: str,
    source_digest: str | None,
    source_blob_reference: str | None,
    source_reference: str | None,
) -> None:
    if input_kind == INPUT_KIND_PDF:
        if source_digest is None or source_blob_reference is None or source_reference is not None:
            raise ValueError(
                "PDF conversions require a source digest and blob reference and no source reference"
            )
        if not _DIGEST_PATTERN.fullmatch(source_digest):
            raise ValueError("PDF conversion source digest must be sha256:<64 lowercase hex>")
        _require_text(
            source_blob_reference,
            name="blob reference",
            max_length=_MAX_BLOB_REFERENCE_LENGTH,
        )
        return
    if input_kind == INPUT_KIND_SOURCE_REFERENCE:
        if source_digest is not None or source_blob_reference is not None:
            raise ValueError("Source reference conversions must not carry PDF source fields")
        if (
            not isinstance(source_reference, str)
            or len(source_reference) > MAX_SOURCE_REFERENCE_LENGTH
            or not _SOURCE_REFERENCE_RE.fullmatch(source_reference)
        ):
            raise ValueError(
                "Source reference conversions require a nonempty normalized reference "
                f"of at most {MAX_SOURCE_REFERENCE_LENGTH} characters"
            )
        return
    raise ValueError("Unsupported conversion input kind")


class DocumentConversionRepository:
    """Owner-scoped conversion rows; callers own the transaction and commit."""

    def create_or_get(
        self,
        db: Session,
        *,
        owner_subject: str,
        service_principal: str,
        curator: BenchmarkCuratorContext,
        input_kind: str,
        source_digest: str | None,
        source_blob_reference: str | None,
        source_reference: str | None,
        idempotency_key: str,
    ) -> tuple[BenchmarkDocumentConversion, bool]:
        """Create a queued conversion, or return the one already bound to this key.

        Reusing a key for different input or a different curator raises
        ``ConversionIdempotencyConflict``. A returned existing row may be in any
        state, including ``failed``; it is never reset.
        """

        _require_text(owner_subject, name="owner subject", max_length=_MAX_SUBJECT_LENGTH)
        _require_text(
            service_principal, name="service principal", max_length=_MAX_SUBJECT_LENGTH
        )
        _require_text(
            idempotency_key, name="idempotency key", max_length=_MAX_IDEMPOTENCY_KEY_LENGTH
        )
        _validate_input(
            input_kind=input_kind,
            source_digest=source_digest,
            source_blob_reference=source_blob_reference,
            source_reference=source_reference,
        )

        inserted_id = db.scalar(
            insert(BenchmarkDocumentConversion)
            .values(
                id=uuid4(),
                owner_subject=owner_subject,
                service_principal=service_principal,
                curator_subject=curator.subject,
                curator_db_user_id=curator.db_user_id,
                input_kind=input_kind,
                source_digest=source_digest,
                source_blob_reference=source_blob_reference,
                source_reference=source_reference,
                status=STATUS_QUEUED,
                idempotency_key=idempotency_key,
            )
            .on_conflict_do_nothing(constraint="uq_benchmark_document_conversions_owner_key")
            .returning(BenchmarkDocumentConversion.id)
        )
        if inserted_id is not None:
            row = db.get(BenchmarkDocumentConversion, inserted_id)
            if row is None:
                raise RuntimeError("benchmark document conversion disappeared after insert")
            return row, True

        row = db.scalar(
            select(BenchmarkDocumentConversion)
            .where(
                BenchmarkDocumentConversion.owner_subject == owner_subject,
                BenchmarkDocumentConversion.idempotency_key == idempotency_key,
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RuntimeError("benchmark document conversion reservation is unavailable")
        if (
            row.input_kind != input_kind
            or row.source_digest != source_digest
            or row.source_reference != source_reference
            or row.curator_subject != curator.subject
            or row.curator_db_user_id != curator.db_user_id
        ):
            raise ConversionIdempotencyConflict(
                "Idempotency key is already bound to a different document or curator"
            )
        return row, False

    def get_for_owner(
        self, db: Session, conversion_id: UUID, owner_subject: str
    ) -> BenchmarkDocumentConversion | None:
        return db.scalar(
            select(BenchmarkDocumentConversion)
            .where(
                BenchmarkDocumentConversion.id == conversion_id,
                BenchmarkDocumentConversion.owner_subject == owner_subject,
            )
            .execution_options(populate_existing=True)
        )

    def _lock(self, db: Session, conversion_id: UUID) -> BenchmarkDocumentConversion:
        row = db.scalar(
            select(BenchmarkDocumentConversion)
            .where(BenchmarkDocumentConversion.id == conversion_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise LookupError("benchmark document conversion does not exist")
        return row

    def mark_running(self, db: Session, conversion_id: UUID) -> BenchmarkDocumentConversion:
        row = self._lock(db, conversion_id)
        if row.status != STATUS_QUEUED:
            raise ConversionStateError("Only a queued conversion can start")
        row.status = STATUS_RUNNING
        row.started_at = datetime.now(timezone.utc)
        row.stage = STAGE_FETCHING_SOURCE
        db.flush()
        return row

    def mark_stage(
        self, db: Session, conversion_id: UUID, stage: str
    ) -> BenchmarkDocumentConversion:
        """Move a running conversion on to a later stage; stages never go back."""

        if stage not in CONVERSION_STAGES:
            raise ValueError("Unknown conversion stage")
        row = self._lock(db, conversion_id)
        if row.status != STATUS_RUNNING:
            raise ConversionStateError("Only a running conversion has a stage")
        if row.stage is not None and (
            CONVERSION_STAGES.index(stage) <= CONVERSION_STAGES.index(row.stage)
        ):
            raise ConversionStateError("A conversion stage can only move forward")
        row.stage = stage
        db.flush()
        return row

    def mark_succeeded(
        self,
        db: Session,
        conversion_id: UUID,
        *,
        snapshot_id: UUID,
        identity: dict[str, Any],
    ) -> BenchmarkDocumentConversion:
        if not isinstance(identity, dict) or not identity:
            raise ValueError("Conversion identity must be a nonempty object")
        row = self._lock(db, conversion_id)
        if row.status != STATUS_RUNNING:
            raise ConversionStateError("Only a running conversion can succeed")
        snapshot_owner = db.scalar(
            select(BenchmarkInputSnapshot.owner_subject).where(
                BenchmarkInputSnapshot.id == snapshot_id
            )
        )
        if snapshot_owner != row.owner_subject:
            raise ConversionStateError("Conversion snapshot must belong to the conversion owner")
        row.status = STATUS_SUCCEEDED
        row.stage = None
        row.snapshot_id = snapshot_id
        row.conversion_identity = identity
        row.completed_at = datetime.now(timezone.utc)
        db.flush()
        return row

    def mark_failed(
        self,
        db: Session,
        conversion_id: UUID,
        *,
        code: str,
        message: str,
    ) -> BenchmarkDocumentConversion:
        """Record a terminal, already-sanitized failure; never schedules a retry."""

        if not isinstance(code, str) or not _ERROR_CODE_PATTERN.fullmatch(code):
            raise ValueError("Conversion error code must be a lowercase identifier")
        _require_text(message, name="error message", max_length=_MAX_ERROR_MESSAGE_LENGTH)
        row = self._lock(db, conversion_id)
        if row.status not in (STATUS_QUEUED, STATUS_RUNNING):
            raise ConversionStateError("Only an unfinished conversion can fail")
        row.status = STATUS_FAILED
        row.stage = None
        row.error_code = code
        row.error_message = message
        row.completed_at = datetime.now(timezone.utc)
        db.flush()
        return row

    def fail_stale_running(
        self, db: Session, *, reason: str, created_before: datetime
    ) -> tuple[UUID, ...]:
        """Fail queued or running conversions created before ``created_before``.

        Several API worker processes may run conversions at once, so a process
        start time says nothing about another process's work. Callers pass an
        age cutoff (see ``reconcile_stale_conversions``): unfinished work older
        than it is treated as lost and recorded as interrupted.
        """

        _require_text(reason, name="error message", max_length=_MAX_ERROR_MESSAGE_LENGTH)
        if created_before.tzinfo is None:
            raise ValueError("Stale conversion cutoff must be timezone-aware")
        failed_ids = db.scalars(
            update(BenchmarkDocumentConversion)
            .where(
                BenchmarkDocumentConversion.status.in_((STATUS_QUEUED, STATUS_RUNNING)),
                BenchmarkDocumentConversion.created_at < created_before,
            )
            .values(
                status=STATUS_FAILED,
                stage=None,
                error_code=INTERRUPTED_ERROR_CODE,
                error_message=reason,
                completed_at=datetime.now(timezone.utc),
            )
            .returning(BenchmarkDocumentConversion.id)
            .execution_options(synchronize_session=False)
        ).all()
        return tuple(failed_ids)


STALE_CONVERSION_MESSAGE = "The conversion was interrupted. Start it again."


def reconcile_stale_conversions(
    *,
    session_factory: Callable[[], Any] = SessionLocal,
    repository: DocumentConversionRepository | None = None,
    now: datetime | None = None,
) -> tuple[UUID, ...]:
    """Fail unfinished conversions older than the configured stale window.

    Runs at API startup and before each new conversion. Nothing is retried:
    the caller starts a new conversion if it still wants one.
    """

    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(seconds=get_benchmark_document_conversion_stale_seconds())
    with session_factory() as db:
        failed = (repository or DocumentConversionRepository()).fail_stale_running(
            db, reason=STALE_CONVERSION_MESSAGE, created_before=cutoff,
        )
        db.commit()
    if failed:
        logger.warning("Marked %d interrupted benchmark document conversion(s) failed", len(failed))
    return failed


# --- Conversion service -----------------------------------------------------

CONVERSION_RESOLVER_ID = "document_conversion"
CONVERSION_REFERENCE_SCHEMA = "document_conversion/v1"
CONVERTED_CONTENT_TYPE = "application/json"
PARSER_PDFX = "pdfx"
PARSER_SOURCE_MAIN_TEXT = "source_main_text"
SOURCE_MAIN_TEXT_CONTENT_FORMAT = "provider_markdown"
SOURCE_NOT_FOUND_MESSAGE = (
    "The configured document source has no usable text or PDF for this paper."
)


class _ConversionFailure(Exception):
    """A known, terminal failure with a plain curator-facing message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def _not_found() -> _ConversionFailure:
    return _ConversionFailure("not_found", SOURCE_NOT_FOUND_MESSAGE)


def _source_access_denied() -> _ConversionFailure:
    return _ConversionFailure(
        "access_denied",
        "The configured document source does not allow this curator's groups "
        "to read this paper.",
    )


def _source_ambiguous() -> _ConversionFailure:
    return _ConversionFailure(
        "ambiguous_source",
        "The configured document source has more than one equally preferred "
        "text or PDF for this paper.",
    )


def _source_unavailable() -> _ConversionFailure:
    return _ConversionFailure(
        "source_unavailable",
        "The configured document source could not provide this paper.",
    )


def _uploaded_pdf_unavailable() -> _ConversionFailure:
    return _ConversionFailure("source_unavailable", "The uploaded PDF is unavailable.")


def _extraction_failed() -> _ConversionFailure:
    return _ConversionFailure("extraction_failed", "Text could not be extracted from the PDF.")


def _invalid_source_pdf() -> _ConversionFailure:
    return _ConversionFailure(
        "invalid_document",
        "The configured document source's PDF for this paper is empty, too large, "
        "or not a PDF.",
    )


def _source_access(source_artifact: Any) -> dict[str, Any]:
    policy = source_artifact.access_policy
    return {
        "source_artifact_id": source_artifact.artifact_id,
        "scope": policy.scope.value,
        "group_ids": sorted(policy.group_ids),
    }


def _invalid_document() -> _ConversionFailure:
    return _ConversionFailure("invalid_document", "The converted document has no usable text.")


def _oversize() -> _ConversionFailure:
    return _ConversionFailure(
        "oversize_payload",
        "The converted document exceeds the benchmark input size limit.",
    )


def _storage_unavailable() -> _ConversionFailure:
    return _ConversionFailure(
        "storage_unavailable", "The converted document could not be stored."
    )


_UNEXPECTED_FAILURE = ("conversion_failed", "The document could not be converted.")


def conversion_identity(
    *,
    input_kind: str,
    parser: str,
    methods: list[str] | None,
    merge: bool | None,
    content_format: str,
    page_provenance_receipt: dict[str, Any] | None,
    source_provider: str | None = None,
    source_artifact: dict[str, str] | None = None,
    source_figure_metadata: list[dict[str, str]] | None = None,
    source_access: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe everything that determined a conversion's output elements.

    ``methods`` and ``merge`` are the PDF extraction settings and are ``None``
    when the provider's main text was used instead of PDF extraction. The
    application version is included so the identity changes when the pipeline
    changes. For source references, ``source_provider`` is the configured
    provider's ID, ``source_artifact`` is the provider artifact whose bytes were
    converted, and ``source_access`` records the access policy of the provider
    source PDF that the requesting curator's groups were authorized against,
    for audit.
    """

    identity: dict[str, Any] = {
        "input_kind": input_kind,
        "parser": parser,
        "methods": methods,
        "merge": merge,
        "content_format": content_format,
        "page_provenance_receipt": page_provenance_receipt,
        "application_version": get_app_version(),
    }
    if source_provider is not None:
        identity["source_provider"] = source_provider
    if source_access is not None:
        identity["source_access"] = source_access
    if source_artifact is not None:
        identity["source_artifact"] = source_artifact
    if source_figure_metadata:
        identity["source_figure_metadata"] = source_figure_metadata
    return identity


def identity_version(identity: dict[str, Any]) -> str:
    """Return the sha256 hex digest of the identity's canonical JSON."""

    canonical = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


@dataclass(frozen=True, slots=True)
class _ConversionJob:
    """Detached copy of the fields a running conversion needs."""

    id: UUID
    owner_subject: str
    service_principal: str
    curator_subject: str
    curator_db_user_id: int
    input_kind: str
    source_digest: str | None
    source_blob_reference: str | None
    source_reference: str | None

    @classmethod
    def from_row(cls, row: Any) -> "_ConversionJob":
        return cls(
            id=row.id,
            owner_subject=row.owner_subject,
            service_principal=row.service_principal,
            curator_subject=row.curator_subject,
            curator_db_user_id=row.curator_db_user_id,
            input_kind=row.input_kind,
            source_digest=row.source_digest,
            source_blob_reference=row.source_blob_reference,
            source_reference=row.source_reference,
        )


# Records that a running conversion moved on to the named stage.
_Advance = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _ConvertedDocument:
    elements: list[dict[str, Any]]
    identity: dict[str, Any]


class DocumentConversionService:
    """Run one queued conversion to a terminal state; never retries."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any] = SessionLocal,
        repository: Any | None = None,
        snapshot_store_factory: Callable[[], Any] = configured_benchmark_snapshot_store,
        snapshot_repository_factory: Callable[[Session, Any], Any] = BenchmarkSnapshotRepository,
        max_input_bytes: Callable[[], int] = get_benchmark_max_input_bytes,
    ) -> None:
        self._session_factory = session_factory
        self._repository = repository or DocumentConversionRepository()
        self._snapshot_store_factory = snapshot_store_factory
        self._snapshot_repository_factory = snapshot_repository_factory
        self._max_input_bytes = max_input_bytes

    async def run(
        self, conversion_id: UUID, *, authorized_group_ids: Sequence[str]
    ) -> None:
        """Convert and freeze one queued conversion created by the API layer.

        ``authorized_group_ids`` are the requesting curator's active groups;
        they gate which provider source PDFs may be used, exactly as for
        curator reference imports.
        """

        try:
            job = await asyncio.to_thread(self._start, conversion_id)
        except (ConversionStateError, LookupError):
            logger.info("Benchmark document conversion %s is not queued; skipping", conversion_id)
            return
        except Exception as exc:
            _report("document_conversion_start", exc)
            return

        async def advance(stage: str) -> None:
            await asyncio.to_thread(self._record_stage, job.id, stage)

        try:
            converted = await self._convert(job, tuple(authorized_group_ids), advance)
            await advance(STAGE_SAVING)
            source = await asyncio.to_thread(self._materialize, job, converted)
            await asyncio.to_thread(self._freeze_and_succeed, job, source, converted.identity)
        except ConversionStateError:
            # Another process already finished this row (for example the
            # startup sweep); its snapshot transaction was rolled back.
            logger.info("Benchmark document conversion %s finished elsewhere", job.id)
        except _ConversionFailure as failure:
            logger.warning(
                "Benchmark document conversion %s failed: %s",
                job.id,
                failure.code,
                extra={"sentry_skip_event": True},
            )
            await self._fail(job.id, failure.code, failure.message)
        except Exception as exc:
            _report("document_conversion", exc)
            code, message = _UNEXPECTED_FAILURE
            await self._fail(job.id, code, message)

    def _start(self, conversion_id: UUID) -> _ConversionJob:
        with self._session_factory() as db:
            row = self._repository.mark_running(db, conversion_id)
            job = _ConversionJob.from_row(row)
            db.commit()
        return job

    def _record_stage(self, conversion_id: UUID, stage: str) -> None:
        with self._session_factory() as db:
            self._repository.mark_stage(db, conversion_id, stage)
            db.commit()

    async def _fail(self, conversion_id: UUID, code: str, message: str) -> None:
        try:
            await asyncio.to_thread(self._record_failure, conversion_id, code, message)
        except ConversionStateError:
            logger.info(
                "Benchmark document conversion %s already finished; failure not recorded",
                conversion_id,
            )
        except Exception as exc:
            _report("document_conversion_failure_record", exc)

    def _record_failure(self, conversion_id: UUID, code: str, message: str) -> None:
        with self._session_factory() as db:
            self._repository.mark_failed(db, conversion_id, code=code, message=message)
            db.commit()

    async def _convert(
        self, job: _ConversionJob, authorized_group_ids: tuple[str, ...], advance: _Advance,
    ) -> _ConvertedDocument:
        if job.input_kind == INPUT_KIND_PDF:
            content = await asyncio.to_thread(self._read_uploaded_pdf, job)
            await advance(STAGE_EXTRACTING_TEXT)
            elements, pdfx_identity = await _parse_pdf(content, job)
            return _ConvertedDocument(
                elements=elements,
                identity=conversion_identity(input_kind=INPUT_KIND_PDF, **pdfx_identity),
            )
        if job.input_kind == INPUT_KIND_SOURCE_REFERENCE:
            return await _convert_source_reference(job, authorized_group_ids, advance)
        raise ValueError("Unsupported conversion input kind")

    def _read_uploaded_pdf(self, job: _ConversionJob) -> bytes:
        try:
            content = self._snapshot_store_factory().read(
                blob_reference=job.source_blob_reference,
                max_bytes=self._max_input_bytes(),
            )
        except BenchmarkSnapshotError:
            raise _uploaded_pdf_unavailable() from None
        if _sha256(content) != job.source_digest:
            raise _uploaded_pdf_unavailable()
        return content

    def _materialize(
        self, job: _ConversionJob, converted: _ConvertedDocument
    ) -> MaterializedBenchmarkInput:
        content = json.dumps(converted.elements, ensure_ascii=False).encode("utf-8")
        if len(content) > self._max_input_bytes():
            raise _oversize()
        try:
            decode_frozen_document(content, content_type=CONVERTED_CONTENT_TYPE)
        except (ValueError, UnicodeDecodeError):
            raise _invalid_document() from None

        reference_fields: dict[str, str] = {
            "schema": CONVERSION_REFERENCE_SCHEMA,
            "input_kind": job.input_kind,
            "curator_subject": job.curator_subject,
        }
        if job.input_kind == INPUT_KIND_PDF:
            reference_fields["source_digest"] = str(job.source_digest)
        else:
            reference_fields["source_reference"] = str(job.source_reference)
        reference = json.dumps(
            reference_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        version = identity_version(converted.identity)
        digest = _sha256(content)
        provenance = BenchmarkSourceProvenance(
            resolver=CONVERSION_RESOLVER_ID, reference=reference, version=version, digest=digest,
        )
        return MaterializedBenchmarkInput(
            resolver=CONVERSION_RESOLVER_ID,
            reference=reference,
            version=version,
            digest=digest,
            content=content.decode("utf-8"),
            metadata=BenchmarkSourceMetadata(
                content_type=CONVERTED_CONTENT_TYPE, content_bytes=len(content),
            ),
            provenance=provenance,
        )

    def _freeze_and_succeed(
        self,
        job: _ConversionJob,
        source: MaterializedBenchmarkInput,
        identity: dict[str, Any],
    ) -> None:
        with self._session_factory() as db:
            try:
                snapshot = self._snapshot_repository_factory(
                    db, self._snapshot_store_factory()
                ).freeze_input(
                    source,
                    owner_subject=job.owner_subject,
                    service_principal=job.service_principal,
                )
                self._repository.mark_succeeded(
                    db, job.id, snapshot_id=snapshot.id, identity=identity,
                )
                db.commit()
            except (BenchmarkSnapshotError, SQLAlchemyError, OSError) as exc:
                db.rollback()
                _report("document_conversion_snapshot", exc)
                raise _storage_unavailable() from None
            except Exception:
                db.rollback()
                raise


async def _parse_pdf(
    content: bytes, job: _ConversionJob
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract PDF text with a fresh parser and no per-user artifacts."""

    try:
        parser = PDFXParser()
        with tempfile.TemporaryDirectory(prefix="benchmark-conversion-") as directory:
            pdf_path = Path(directory) / "document.pdf"
            pdf_path.write_bytes(content)
            result = await parser.parse_pdf_document(
                pdf_path,
                document_id=str(job.id),
                user_id=str(job.curator_db_user_id),
                save_artifacts=False,
            )
    except ConfigurationError as exc:
        _report("document_conversion_configuration", exc)
        raise _extraction_failed() from None
    except PDFParsingError as exc:
        # PDFX marks failures at its provider boundary; local invalid-input
        # and empty-document errors carry no provider failure metadata.
        if isinstance(exc.details.get(PDFX_FAILURE_DETAILS_KEY), dict):
            _report("document_conversion_extraction", exc)
        raise _extraction_failed() from None
    except PDFCancellationError:
        raise _extraction_failed() from None
    return result["elements"], {
        "parser": PARSER_PDFX,
        "methods": [method for method in parser.methods.split(",") if method],
        "merge": parser.merge_enabled,
        "content_format": f"{parser.download_variant}_markdown",
        "page_provenance_receipt": result["page_provenance"],
    }


async def _convert_source_reference(
    job: _ConversionJob, authorized_group_ids: tuple[str, ...], advance: _Advance,
) -> _ConvertedDocument:
    """Use the curator reference-import selection with the application's own source access."""

    try:
        provider = get_configured_document_source_provider()
    except DocumentSourceError as exc:
        _report("document_conversion_source", exc)
        raise _source_unavailable() from None
    try:
        decision = await select_reference_import_candidate(
            provider=provider,
            identifier=str(job.source_reference),
            authorized_group_ids=authorized_group_ids,
            # The application's own source credential, never the curator's: a
            # machine reader, so only text bound to the authorized PDF is used.
            bearer_kind=ProviderBearerKind.SERVICE,
            request_bearer_token=None,
            allow_conversion_request=False,
        )
        if decision.status == ReferenceImportDecisionStatus.ACCESS_DENIED:
            raise _source_access_denied()
        if decision.status == ReferenceImportDecisionStatus.AMBIGUOUS_MATCH:
            raise _source_ambiguous()
        selected = decision.selected
        if selected is None:
            raise _not_found()
        if selected.converted_artifact is not None:
            return await _convert_source_main_text(provider, selected, advance)
        pdf_bytes = await provider.download_artifact(
            selected.source_artifact.artifact_id, request_bearer_token=None,
        )
        try:
            _validate_source_pdf_bytes(pdf_bytes)
        except DocumentSourceError:
            raise _invalid_source_pdf() from None
        await advance(STAGE_EXTRACTING_TEXT)
        elements, pdfx_identity = await _parse_pdf(pdf_bytes, job)
        return _ConvertedDocument(
            elements=elements,
            identity=conversion_identity(
                input_kind=INPUT_KIND_SOURCE_REFERENCE,
                source_provider=provider.provider_id,
                source_artifact={
                    "id": selected.source_artifact.artifact_id,
                    "checksum": _sha256(pdf_bytes),
                },
                source_access=_source_access(selected.source_artifact),
                **pdfx_identity,
            ),
        )
    except DocumentSourceAccessDenied:
        raise _source_access_denied() from None
    except DocumentSourceIngestionError:
        raise _invalid_document() from None
    except DocumentSourceError as exc:
        _report("document_conversion_source", exc)
        raise _source_unavailable() from None
    finally:
        try:
            await provider.aclose()
        except Exception as cleanup_error:
            logger.warning(
                "Document-source provider cleanup failed: %s", type(cleanup_error).__name__,
            )


async def _convert_source_main_text(
    provider: DocumentSourceProvider, selected: Any, advance: _Advance,
) -> _ConvertedDocument:
    """Convert provider main-text Markdown exactly as curator reference imports do."""

    converted = selected.converted_artifact
    markdown_bytes = await provider.download_artifact(
        converted.artifact_id, request_bearer_token=None,
    )
    figure_entries = []
    figure_identity = []
    for artifact in selected.provider_metadata_artifacts:
        raw = await provider.download_artifact(artifact.artifact_id, request_bearer_token=None)
        figure_identity.append({"id": artifact.artifact_id, "checksum": _sha256(raw)})
        try:
            figure_entries.append(
                normalize_provider_figure_metadata_sidecar(
                    raw, metadata_artifact_id=artifact.artifact_id,
                )
            )
        except ValueError:
            raise _invalid_document() from None
    await advance(STAGE_EXTRACTING_TEXT)
    try:
        elements, _warnings = provider_markdown_to_pipeline_elements(
            markdown_bytes.decode("utf-8"), tuple(figure_entries),
        )
    except UnicodeDecodeError:
        raise _invalid_document() from None
    return _ConvertedDocument(
        elements=elements,
        identity=conversion_identity(
            input_kind=INPUT_KIND_SOURCE_REFERENCE,
            parser=PARSER_SOURCE_MAIN_TEXT,
            methods=None,
            merge=None,
            content_format=SOURCE_MAIN_TEXT_CONTENT_FORMAT,
            page_provenance_receipt=None,
            source_provider=provider.provider_id,
            source_artifact={"id": converted.artifact_id, "checksum": _sha256(markdown_bytes)},
            source_figure_metadata=figure_identity,
            source_access=_source_access(selected.source_artifact),
        ),
    )


def _report(operation: str, exc: BaseException) -> None:
    """Report only the operation and exception type, never provider or SQL text."""

    try:
        report_runtime_exception(
            sanitized_benchmark_error(operation, type(exc).__name__),
            component="benchmark_document_conversion",
            operation=operation,
        )
    except Exception:
        logger.warning(
            "Benchmark conversion failure reporting is unavailable",
            extra={"sentry_skip_event": True},
        )


async def run_conversion(
    conversion_id: UUID, *, authorized_group_ids: Sequence[str]
) -> None:
    """Background entry point: run one conversion; never raises, never retries."""

    try:
        await DocumentConversionService().run(
            conversion_id, authorized_group_ids=authorized_group_ids,
        )
    except Exception as exc:
        _report("document_conversion_run", exc)
