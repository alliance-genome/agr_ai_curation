"""Service-owned conversion of papers into frozen benchmark documents.

A conversion record is owned by the calling benchmark service subject. Reads
and idempotency keys are scoped to that owner; the requesting curator is
recorded separately. Failed conversions are terminal and never retried.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.models.sql.benchmark import BenchmarkDocumentConversion, BenchmarkInputSnapshot


INPUT_KIND_PDF = "pdf"
INPUT_KIND_ABC_REFERENCE = "abc_reference"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
INTERRUPTED_ERROR_CODE = "interrupted"

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ABC_REFERENCE_PATTERN = re.compile(r"^AGRKB:[0-9]+$")
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
    abc_reference: str | None,
) -> None:
    if input_kind == INPUT_KIND_PDF:
        if source_digest is None or source_blob_reference is None or abc_reference is not None:
            raise ValueError(
                "PDF conversions require a source digest and blob reference and no ABC reference"
            )
        if not _DIGEST_PATTERN.fullmatch(source_digest):
            raise ValueError("PDF conversion source digest must be sha256:<64 lowercase hex>")
        _require_text(
            source_blob_reference,
            name="blob reference",
            max_length=_MAX_BLOB_REFERENCE_LENGTH,
        )
        return
    if input_kind == INPUT_KIND_ABC_REFERENCE:
        if source_digest is not None or source_blob_reference is not None:
            raise ValueError("ABC conversions must not carry PDF source fields")
        if abc_reference is None or not _ABC_REFERENCE_PATTERN.fullmatch(abc_reference):
            raise ValueError("ABC conversions require an AGRKB reference")
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
        abc_reference: str | None,
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
            abc_reference=abc_reference,
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
                abc_reference=abc_reference,
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
            or row.abc_reference != abc_reference
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
        row.error_code = code
        row.error_message = message
        row.completed_at = datetime.now(timezone.utc)
        db.flush()
        return row

    def fail_stale_running(
        self, db: Session, *, reason: str, created_before: datetime
    ) -> tuple[UUID, ...]:
        """Fail queued or running conversions created before ``created_before``.

        Used at process startup: work created before this process started can
        no longer be running anywhere, so it is recorded as interrupted.
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
                error_code=INTERRUPTED_ERROR_CODE,
                error_message=reason,
                completed_at=datetime.now(timezone.utc),
            )
            .returning(BenchmarkDocumentConversion.id)
            .execution_options(synchronize_session=False)
        ).all()
        return tuple(failed_ids)
