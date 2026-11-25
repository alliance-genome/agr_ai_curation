"""Import provider documents by curator-supplied source identifiers."""

# Provider and cleanup boundaries deliberately catch third-party failures.
# ruff: noqa: BLE001

from __future__ import annotations

import asyncio
import hashlib
from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from src.lib.observability.sentry import hash_sentry_identifier
import logging
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config import get_pdf_storage_path
from src.lib.document_cleanup import (
    cleanup_document_curation_dependencies,
    drain_shielded,
    report_compensation_file_cleanup_failure,
)
from src.lib.document_files.paper_lock import (
    PaperOperationInProgress,
    open_paper_lock_session,
    paper_operation_lock,
)
from src.lib.document_files.service import (
    owned_paper_by_main_file_hash,
    remove_committed_paper_stored_files,
)
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.supplement_plan import ChosenSupplement, SupplementPlan
from src.lib.document_sources.import_selection import (
    artifact_is_bound_to_source,
    provider_metadata_artifacts_for_source,
    source_artifact_is_authorized,
)
from src.lib.document_sources.main_text import select_preferred_main_text_artifact
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceConfigError,
    DocumentSourceError,
    DocumentSourceLoginExpired,
    DocumentSourceReferenceNotFound,
    DocumentSourceProvider,
    NormalizedSourceIdentifier,
    ProviderBearerKind,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionResult,
    SourceConversionStatus,
    SourceReference,
    ViewerMode,
)
from src.lib.document_sources.provenance import (
    find_existing_document_by_source,
    sanitize_document_source_provenance,
)
from src.lib.document_sources.registry import (
    get_configured_document_source_provider,
    get_document_source_provider_metadata,
)
from src.lib.openai_agents.config import (
    get_document_max_files_per_paper,
    get_document_source_import_batch_limit,
)
from src.lib.pdf_jobs import service as pdf_job_service
from src.lib.pdf_jobs.upload_execution_service import (
    ProviderConversionExecutionRequest,
    ProviderMarkdownExecutionRequest,
    UploadExecutionRequest,
    UploadExecutionService,
)
from src.lib.pdf_limits import MAX_PDF_FILE_SIZE_BYTES, pdf_file_size_limit_message
from src.lib.storage_permissions import ensure_writable_directory
from src.lib.weaviate_client.documents import (
    create_document,
    delete_document,
    get_document,
)
from src.lib.weaviate_helpers import get_tenant_name
from src.models.document import (
    DocumentMetadata,
    EmbeddingStatus,
    PDFDocument,
    ProcessingStatus,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import (
    DOCUMENT_KIND_LIBRARY,
    FILE_ROLE_MAIN,
    FILE_STATUS_PENDING,
    PDFDocument as ViewerPDFDocument,
    PDFDocumentFile,
)
from src.services.user_service import principal_from_claims, provision_user_async, ProvisionedUser

logger = logging.getLogger(__name__)

_PMID_RE = re.compile(r"^(?:PMID|PUBMED(?:\s+ID)?)\s*[:#]?\s*(\d+)$", re.IGNORECASE)


def _report_source_failure(exc: Exception, operation: str) -> None:
    """Capture only unexpected source failures; reporting never changes imports."""
    if isinstance(exc, DocumentSourceAccessDenied):
        return
    try:
        report_runtime_exception(exc, component="identifier_import", operation=operation)
    except Exception:
        pass


def _report_service_login_expired(
    exc: DocumentSourceLoginExpired,
    operation: str,
    *,
    bearer_kind: ProviderBearerKind,
) -> None:
    """Report a rejected non-curator (service) token once with sanitized details."""
    if bearer_kind is ProviderBearerKind.CURATOR:
        # An expired curator login is the curator's own condition, not a server failure.
        return
    try:
        report_runtime_exception(
            sanitized_runtime_error("Document-source service bearer was rejected"),
            component="identifier_import",
            operation=operation,
            context={
                "bearer_kind": bearer_kind.value,
                "exception_type": type(exc).__name__,
            },
            include_request=False,
        )
    except Exception:
        pass


class IdentifierImportValidationError(ValueError):
    """Raised when the identifier import request itself is invalid."""


class SupplementSelectionError(ValueError):
    """The chosen supplements cannot be imported as asked; ``code`` says why (codes only).

    ``supplement_identifier_unknown``, ``file_chosen_twice`` or ``paper_file_limit``.
    """

    def __init__(self, code: str, *, limit_value: int | None = None):
        super().__init__(code)
        self.code = code
        self.limit_value = limit_value


def _provider_short_label(provider_id: str | None) -> str:
    resolved_id = (provider_id or "").strip()
    if resolved_id:
        metadata = get_document_source_provider_metadata(resolved_id)
        if isinstance(metadata, Mapping):
            for key in ("short_label", "display_label"):
                raw = metadata.get(key)
                if isinstance(raw, str) and raw.strip():
                    return raw.strip()
    return "the document source"


def file_not_tickable_message(provider_id: str | None = None) -> str:
    """Curator-facing message for an unconverted supplement this import will not ask to convert.

    Approved wording (Chris, 23 Nov 2026, card 41), parameterized by registered provider label.
    """
    label = _provider_short_label(provider_id)
    return (
        f"This file can no longer be chosen: {label} has not converted it "
        f"and this import will not ask {label} to."
    )


class ReferenceImportDecisionStatus(str):
    """Decision categories for reference-backed imports."""

    READY = "ready"
    REFERENCE_NOT_FOUND = "reference_not_found"
    NO_SOURCE_ARTIFACT = "no_source_artifact"
    ACCESS_DENIED = "access_denied"
    AMBIGUOUS_MATCH = "ambiguous_match"
    NO_CONVERTED_TEXT = "no_converted_text"
    CONVERSION_RUNNING = "conversion_running"
    CONVERSION_FAILED = "conversion_failed"


@dataclass(frozen=True, slots=True)
class ReferenceImportCandidate:
    """Source PDF plus optional converted text artifact selected for import."""

    reference: SourceReference
    source_artifact: SourceArtifact
    converted_artifact: SourceArtifact | None = None
    provider_metadata_artifacts: tuple[SourceArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceImportDecision:
    """Provider-neutral reference import decision."""

    status: str
    provider: str
    identifier: str
    reference: SourceReference | None = None
    selected: ReferenceImportCandidate | None = None
    candidates: tuple[ReferenceImportCandidate, ...] = ()
    message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # The provider's answer when this decision sent the conversion request, else ``None``.
    conversion_answer: SourceConversionResult | None = None
    # When that request was sent (taken just before sending, so never later than it).
    conversion_requested_at: datetime | None = None
    # Listed artifacts fetched during decision making, used to resolve supplement display names.
    artifacts: tuple[SourceArtifact, ...] = ()

    @property
    def is_ready(self) -> bool:
        return self.status == ReferenceImportDecisionStatus.READY and self.selected is not None

    @property
    def waits_for_conversion(self) -> bool:
        """The provider is still converting the selected main text; the import waits for it."""

        return self.status == ReferenceImportDecisionStatus.CONVERSION_RUNNING and self.selected is not None

    @property
    def can_import(self) -> bool:
        """Whether an import of this reference goes ahead; anything else ends it with this decision's error.

        The one rule for the identifier import, its resolve step, and the file listing's
        whole-paper check (``file_listing._paper_refusal``).
        """

        return self.is_ready or self.waits_for_conversion


@dataclass(frozen=True, slots=True)
class IdentifierImportItemResult:
    """Per-identifier import result for frontend partial-success rendering."""

    identifier: str
    normalized_identifier: str | None
    status: str
    message: str
    document_id: str | None = None
    job_id: str | None = None
    filename: str | None = None
    error_code: str | None = None
    existing_document_id: str | None = None
    source_provenance: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "identifier": self.identifier,
            "normalized_identifier": self.normalized_identifier,
            "status": self.status,
            "message": self.message,
            "document_id": self.document_id,
            "job_id": self.job_id,
            "filename": self.filename,
            "error_code": self.error_code,
            "existing_document_id": self.existing_document_id,
            "source_provenance": self.source_provenance,
        }
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True, slots=True)
class IdentifierImportBatchResult:
    """Batch response from an identifier import request."""

    results: tuple[IdentifierImportItemResult, ...]
    requested_count: int
    imported_count: int
    duplicate_count: int
    error_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [result.to_dict() for result in self.results],
            "requested_count": self.requested_count,
            "imported_count": self.imported_count,
            "duplicate_count": self.duplicate_count,
            "error_count": self.error_count,
        }


def _batch_document_source_token_unavailable(
    raw_identifiers: tuple[str, ...],
) -> IdentifierImportBatchResult:
    results = tuple(
        IdentifierImportItemResult(
            identifier=raw_identifier,
            normalized_identifier=None,
            status="error",
            error_code="document_source_curator_token_unavailable",
            message="Document-source import requires a curator bearer token.",
        )
        for raw_identifier in raw_identifiers
    )
    return IdentifierImportBatchResult(
        results=results,
        requested_count=len(raw_identifiers),
        imported_count=0,
        duplicate_count=0,
        error_count=len(raw_identifiers),
    )


def parse_source_identifier_batch(raw_identifiers: str, *, batch_limit: int | None = None) -> tuple[str, ...]:
    """Split a comma/newline separated identifier string and enforce the batch cap."""

    limit = batch_limit or get_document_source_import_batch_limit()
    identifiers = tuple(
        part.strip()
        for part in re.split(r"[,\n]+", raw_identifiers or "")
        if part.strip()
    )
    if not identifiers:
        raise IdentifierImportValidationError("At least one identifier is required")
    if len(identifiers) > limit:
        raise IdentifierImportValidationError(
            f"At most {limit} identifiers can be imported at once"
        )
    return identifiers


def normalize_source_identifier(
    identifier: str,
    *,
    provider: DocumentSourceProvider | None = None,
) -> NormalizedSourceIdentifier:
    """Normalize generic PMID/PubMed identifiers or delegate provider syntax."""

    original = (identifier or "").strip()
    if not original:
        return NormalizedSourceIdentifier(
            original=identifier,
            normalized=None,
            error="Identifier is empty",
        )

    normalize_identifier = getattr(provider, "normalize_identifier", None)
    if callable(normalize_identifier):
        typed_normalize_identifier = cast(
            Callable[[str], NormalizedSourceIdentifier],
            normalize_identifier,
        )
        return typed_normalize_identifier(original)

    if original.isdigit():
        return NormalizedSourceIdentifier(original=original, normalized=f"PMID:{original}")

    pmid_match = _PMID_RE.match(original)
    if pmid_match:
        return NormalizedSourceIdentifier(
            original=original,
            normalized=f"PMID:{pmid_match.group(1)}",
        )

    return NormalizedSourceIdentifier(
        original=original,
        normalized=None,
        error="Unsupported identifier. Use PMID, PubMed ID, or a provider-supported identifier.",
    )


async def select_reference_import_candidate(
    *,
    provider: DocumentSourceProvider,
    identifier: str,
    authorized_group_ids: tuple[str, ...] | list[str] | set[str],
    bearer_kind: ProviderBearerKind,
    request_bearer_token: str | None = None,
    allow_conversion_request: bool = True,
) -> ReferenceImportDecision:
    """Resolve a provider reference to exactly one authorized source PDF.

    Converted main Markdown is preferred when it is ready. Import callers may
    request provider conversion for known unconverted references; dry-run
    resolve callers must leave provider state untouched.

    Under a ``SERVICE`` bearer only derived artifacts bound to the authorized
    source PDF are used. Without ready bound Markdown the decision is the
    source PDF alone, so callers parse that PDF instead of requesting or
    waiting for provider conversion.
    """

    try:
        reference = await provider.resolve_reference(
            identifier,
            request_bearer_token=request_bearer_token,
        )
    except DocumentSourceReferenceNotFound as exc:
        return ReferenceImportDecision(
            status=ReferenceImportDecisionStatus.REFERENCE_NOT_FOUND,
            provider=provider.provider_id,
            identifier=identifier,
            message=str(exc),
        )
    artifacts = await provider.list_artifacts(
        reference,
        request_bearer_token=request_bearer_token,
    )
    return await decide_reference_import(
        provider=provider,
        identifier=identifier,
        reference=reference,
        artifacts=artifacts,
        authorized_group_ids=authorized_group_ids,
        bearer_kind=bearer_kind,
        request_bearer_token=request_bearer_token,
        allow_conversion_request=allow_conversion_request,
    )


async def decide_reference_import(
    *,
    provider: DocumentSourceProvider,
    identifier: str,
    reference: SourceReference,
    artifacts: list[SourceArtifact],
    authorized_group_ids: tuple[str, ...] | list[str] | set[str],
    bearer_kind: ProviderBearerKind,
    request_bearer_token: str | None = None,
    allow_conversion_request: bool = True,
) -> ReferenceImportDecision:
    """Choose the source PDF and converted main text from a reference's listed artifacts.

    The rule ``select_reference_import_candidate`` applies after resolving and
    listing; the file listing calls it on the listing it already holds, so the
    main paper it shows is the one an import would choose.
    """

    source_artifacts: tuple[SourceArtifact, ...] = tuple(
        artifact
        for artifact in artifacts
        if artifact.role is SourceArtifactRole.SOURCE_PDF
        and provider.is_reference_source_artifact(artifact)
    )
    if not source_artifacts:
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.NO_SOURCE_ARTIFACT,
            message="No source PDF artifact is available for this reference",
        )

    authorized_sources: tuple[SourceArtifact, ...] = tuple(
        artifact
        for artifact in source_artifacts
        if source_artifact_is_authorized(
            artifact,
            authorized_group_ids=authorized_group_ids,
        )
    )
    if not authorized_sources:
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.ACCESS_DENIED,
            message="No source PDF artifact is accessible to this curator",
        )
    source_artifact, ambiguous_source_count = _select_preferred_reference_source_artifact(
        provider=provider,
        artifacts=authorized_sources,
        authorized_group_ids=authorized_group_ids,
    )
    if ambiguous_source_count > 1:
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.AMBIGUOUS_MATCH,
            message="Multiple source PDFs require curator selection",
            metadata={"match_count": ambiguous_source_count},
        )

    if source_artifact is None:
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.ACCESS_DENIED,
            message="No source PDF artifact is accessible to this curator",
        )
    provider_metadata_artifacts = provider_metadata_artifacts_for_source(
        provider=provider,
        source_artifact=source_artifact,
        artifacts=artifacts,
        bearer_kind=bearer_kind,
    )
    markdown_artifacts = _converted_markdown_artifacts_for_source(
        source_artifact=source_artifact,
        artifacts=artifacts,
        bearer_kind=bearer_kind,
    )
    selected_artifact, ambiguous_count = select_preferred_main_text_artifact(
        provider,
        markdown_artifacts,
    )
    if ambiguous_count > 1:
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.AMBIGUOUS_MATCH,
            message=(
                "Multiple equally preferred converted main Markdown artifacts require "
                "curator selection"
            ),
            metadata={"match_count": ambiguous_count},
        )

    if selected_artifact is not None:
        candidate = ReferenceImportCandidate(
            reference=reference,
            source_artifact=source_artifact,
            converted_artifact=selected_artifact,
            provider_metadata_artifacts=provider_metadata_artifacts,
        )
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.READY,
            selected=candidate,
            candidates=(candidate,),
            message="One authorized converted Markdown artifact is ready",
            artifacts=tuple(artifacts),
        )
    if bearer_kind is ProviderBearerKind.SERVICE:
        # No provider conversion or reference-level text under a machine reader:
        # the authorized source PDF is parsed instead.
        source_only_candidate = ReferenceImportCandidate(
            reference=reference,
            source_artifact=source_artifact,
            provider_metadata_artifacts=provider_metadata_artifacts,
        )
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.READY,
            selected=source_only_candidate,
            candidates=(source_only_candidate,),
            message="No converted Markdown is bound to the authorized source PDF",
            artifacts=tuple(artifacts),
        )
    if any(
        artifact.status is SourceArtifactStatus.RUNNING
        and provider.is_main_text_artifact(artifact)
        for artifact in markdown_artifacts
    ):
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.CONVERSION_RUNNING,
            selected=ReferenceImportCandidate(
                reference=reference,
                source_artifact=source_artifact,
                provider_metadata_artifacts=provider_metadata_artifacts,
            ),
            message="Provider conversion is still running",
            artifacts=tuple(artifacts),
        )
    if any(
        artifact.status is SourceArtifactStatus.FAILED
        and provider.is_main_text_artifact(artifact)
        for artifact in markdown_artifacts
    ):
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.CONVERSION_FAILED,
            selected=ReferenceImportCandidate(
                reference=reference,
                source_artifact=source_artifact,
            ),
            message="Provider conversion failed",
        )

    conversion_result = None
    requested_at = None
    if allow_conversion_request:
        requested_at = datetime.now(timezone.utc)
        conversion_result = await _request_reference_conversion_if_supported(
            provider=provider,
            reference=reference,
            source_artifact=source_artifact,
            request_bearer_token=request_bearer_token,
        )
    if conversion_result is not None:
        conversion_metadata = _reference_conversion_metadata(conversion_result)
        if provider.conversion_exposes_main_text(conversion_result):
            refreshed_artifacts = await provider.list_artifacts(
                reference,
                request_bearer_token=request_bearer_token,
            )
            converted_artifact, ambiguous_count = _select_reference_level_markdown_artifact(
                provider=provider,
                source_artifact=source_artifact,
                artifacts=refreshed_artifacts,
            )
            refreshed_metadata_artifacts = provider_metadata_artifacts_for_source(
                provider=provider,
                source_artifact=source_artifact,
                artifacts=refreshed_artifacts,
                bearer_kind=bearer_kind,
            )
            if ambiguous_count > 1:
                return _reference_decision(
                    conversion_answer=conversion_result,
                    conversion_requested_at=requested_at,
                    provider=provider.provider_id,
                    identifier=identifier,
                    reference=reference,
                    status=ReferenceImportDecisionStatus.AMBIGUOUS_MATCH,
                    message="Provider conversion produced multiple equally preferred Markdown artifacts",
                    metadata={**conversion_metadata, "match_count": ambiguous_count},
                )
            if converted_artifact is not None:
                candidate = ReferenceImportCandidate(
                    reference=reference,
                    source_artifact=source_artifact,
                    converted_artifact=converted_artifact,
                    provider_metadata_artifacts=refreshed_metadata_artifacts,
                )
                return _reference_decision(
                    conversion_answer=conversion_result,
                    conversion_requested_at=requested_at,
                    provider=provider.provider_id,
                    identifier=identifier,
                    reference=reference,
                    status=ReferenceImportDecisionStatus.READY,
                    selected=candidate,
                    candidates=(candidate,),
                    message="Provider conversion produced main Markdown",
                    metadata=conversion_metadata,
                    artifacts=tuple(refreshed_artifacts),
                )
        if conversion_result.status is SourceConversionStatus.RUNNING:
            return _reference_decision(
                conversion_answer=conversion_result,
                conversion_requested_at=requested_at,
                provider=provider.provider_id,
                identifier=identifier,
                reference=reference,
                status=ReferenceImportDecisionStatus.CONVERSION_RUNNING,
                selected=ReferenceImportCandidate(
                    reference=reference,
                    source_artifact=source_artifact,
                    provider_metadata_artifacts=provider_metadata_artifacts,
                ),
                message="Provider conversion is still running",
                metadata=conversion_metadata,
                artifacts=tuple(artifacts),
            )
        if conversion_result.status is SourceConversionStatus.FAILED:
            return _reference_decision(
                conversion_answer=conversion_result,
                conversion_requested_at=requested_at,
                provider=provider.provider_id,
                identifier=identifier,
                reference=reference,
                status=ReferenceImportDecisionStatus.CONVERSION_FAILED,
                selected=ReferenceImportCandidate(
                    reference=reference,
                    source_artifact=source_artifact,
                ),
                message="Provider conversion failed",
                metadata=conversion_metadata,
            )
        if conversion_result.status is SourceConversionStatus.NO_SOURCES:
            return _reference_decision(
                conversion_answer=conversion_result,
                conversion_requested_at=requested_at,
                provider=provider.provider_id,
                identifier=identifier,
                reference=reference,
                status=ReferenceImportDecisionStatus.NO_CONVERTED_TEXT,
                selected=ReferenceImportCandidate(
                    reference=reference,
                    source_artifact=source_artifact,
                ),
                message="Provider has no convertible source for this reference",
                metadata=conversion_metadata,
            )
        return _reference_decision(
            conversion_answer=conversion_result,
            conversion_requested_at=requested_at,
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.NO_CONVERTED_TEXT,
            selected=ReferenceImportCandidate(
                reference=reference,
                source_artifact=source_artifact,
            ),
            message="Provider conversion did not expose usable main Markdown",
            metadata=conversion_metadata,
        )

    source_only_candidate = ReferenceImportCandidate(
        reference=reference,
        source_artifact=source_artifact,
        provider_metadata_artifacts=provider_metadata_artifacts,
    )
    return _reference_decision(
        provider=provider.provider_id,
        identifier=identifier,
        reference=reference,
        status=ReferenceImportDecisionStatus.READY,
        selected=source_only_candidate,
        candidates=(source_only_candidate,),
        message="No converted main Markdown artifact is available for this reference",
        artifacts=tuple(artifacts),
    )


class IdentifierImportService:
    """Orchestrate provider identifier import into local PDF-backed documents."""

    def __init__(
        self,
        *,
        upload_execution_service: UploadExecutionService,
        session_factory: Callable[[], Session] = SessionLocal,
        storage_path_provider: Callable[[], Path] = get_pdf_storage_path,
        principal_from_claims_fn: Callable[[dict[str, Any]], Any] = principal_from_claims,
        provision_user_fn: Callable[[Any], Awaitable[ProvisionedUser]] = provision_user_async,
        provider_factory: Callable[[], DocumentSourceProvider] = get_configured_document_source_provider,
        reference_import_selector: Callable[..., Awaitable[ReferenceImportDecision]] = select_reference_import_candidate,
        create_document_fn: Callable[[str, Any], Awaitable[dict[str, Any]]] = create_document,
        create_job_fn: Callable[..., Any] = pdf_job_service.create_job,
        get_document_fn: Callable[[str, str], Awaitable[Any]] = get_document,
        delete_document_fn: Callable[[str, str], Awaitable[Any]] = delete_document,
        find_existing_source_document_fn: Callable[..., Any] = find_existing_document_by_source,
        cleanup_document_dependencies_fn: Callable[[Session, uuid.UUID], Any] = cleanup_document_curation_dependencies,
        tenant_name_resolver: Callable[[str], str] = get_tenant_name,
        import_batch_limit_provider: Callable[[], int] = get_document_source_import_batch_limit,
        get_active_job_fn: Callable[..., Any] | None = None,
        mark_job_failed_fn: Callable[..., Any] | None = None,
        paper_operation_lock_fn: Callable[[uuid.UUID | str], Any] | None = None,
    ) -> None:
        self.upload_execution_service = upload_execution_service
        self._session_factory = session_factory
        self._storage_path_provider = storage_path_provider
        self._principal_from_claims = principal_from_claims_fn
        self._provision_user = provision_user_fn
        self._provider_factory = provider_factory
        self._reference_import_selector = reference_import_selector
        self._create_document = create_document_fn
        self._create_job = create_job_fn
        self._get_document = get_document_fn
        self._delete_document = delete_document_fn
        self._find_existing_source_document = find_existing_source_document_fn
        self._cleanup_document_dependencies = cleanup_document_dependencies_fn
        self._tenant_name_resolver = tenant_name_resolver
        self._import_batch_limit_provider = import_batch_limit_provider
        self._get_active_job_fn = get_active_job_fn
        self._mark_job_failed_fn = mark_job_failed_fn
        self._paper_operation_lock_fn = paper_operation_lock_fn

    async def import_identifiers(
        self,
        *,
        background_tasks: BackgroundTasks,
        identifiers: str,
        user: Mapping[str, Any],
        document_source_context: DocumentSourceRequestContext,
        supplements: Sequence[Any] = (),
    ) -> IdentifierImportBatchResult:
        """Import a batch of source identifiers and return per-item status.

        ``supplements`` are the curator's ticked supplements, each naming its
        ``identifier`` exactly as in ``identifiers`` (interface I6). They are
        checked before anything is imported (``SupplementSelectionError``).
        """

        raw_identifiers = parse_source_identifier_batch(
            identifiers,
            batch_limit=self._import_batch_limit_provider(),
        )
        chosen_by_identifier = _chosen_supplements_by_identifier(raw_identifiers, supplements)
        login_expires_at = _login_expires_at(user, document_source_context.bearer_kind)
        db_user = await self._provision_user(self._principal_from_claims(dict(user)))

        curator_token = (document_source_context.curator_token or "").strip()
        if not curator_token:
            return _batch_document_source_token_unavailable(raw_identifiers)

        results: list[IdentifierImportItemResult] = []
        normalization_provider: DocumentSourceProvider | None = None
        try:
            normalization_provider = self._provider_factory()
        except (DocumentSourceConfigError, DocumentSourceError) as exc:
            _report_source_failure(exc, "source_normalization_failed")
            logger.warning("Document-source identifier normalization unavailable: %s", exc,
                           extra={"sentry_skip_event": True})
            return IdentifierImportBatchResult(
                results=tuple(
                    IdentifierImportItemResult(
                        identifier=raw_identifier,
                        normalized_identifier=None,
                        status="error",
                        error_code="document_source_unavailable",
                        message="Document-source lookup is unavailable.",
                    )
                    for raw_identifier in raw_identifiers
                ),
                requested_count=len(raw_identifiers),
                imported_count=0,
                duplicate_count=0,
                error_count=len(raw_identifiers),
            )
        for raw_identifier in raw_identifiers:
            normalized = normalize_source_identifier(
                raw_identifier,
                provider=normalization_provider,
            )
            if not normalized.is_valid:
                results.append(
                    IdentifierImportItemResult(
                        identifier=raw_identifier,
                        normalized_identifier=None,
                        status="error",
                        error_code="invalid_identifier",
                        message=normalized.error or "Invalid identifier",
                    )
                )
                continue

            result = await self._import_one_identifier(
                background_tasks=background_tasks,
                normalized=normalized,
                user=user,
                db_user=db_user,
                document_source_context=document_source_context,
                chosen=chosen_by_identifier.get(raw_identifier, ()),
                login_expires_at=login_expires_at,
            )
            results.append(result)
        if normalization_provider is not None:
            await normalization_provider.aclose()

        return IdentifierImportBatchResult(
            results=tuple(results),
            requested_count=len(raw_identifiers),
            imported_count=sum(1 for result in results if result.status == "imported"),
            duplicate_count=sum(1 for result in results if result.status == "duplicate"),
            error_count=sum(1 for result in results if result.status == "error"),
        )

    async def resolve_identifiers(
        self,
        *,
        identifiers: str,
        user: Mapping[str, Any],
        document_source_context: DocumentSourceRequestContext,
    ) -> IdentifierImportBatchResult:
        """Resolve a batch of source identifiers without importing documents."""

        raw_identifiers = parse_source_identifier_batch(
            identifiers,
            batch_limit=self._import_batch_limit_provider(),
        )
        db_user = await self._provision_user(self._principal_from_claims(dict(user)))

        curator_token = (document_source_context.curator_token or "").strip()
        if not curator_token:
            return _batch_document_source_token_unavailable(raw_identifiers)

        results: list[IdentifierImportItemResult] = []
        normalization_provider: DocumentSourceProvider | None = None
        try:
            normalization_provider = self._provider_factory()
        except (DocumentSourceConfigError, DocumentSourceError) as exc:
            _report_source_failure(exc, "source_normalization_failed")
            logger.warning("Document-source identifier normalization unavailable: %s", exc,
                           extra={"sentry_skip_event": True})
            return IdentifierImportBatchResult(
                results=tuple(
                    IdentifierImportItemResult(
                        identifier=raw_identifier,
                        normalized_identifier=None,
                        status="error",
                        error_code="document_source_unavailable",
                        message="Document-source lookup is unavailable.",
                    )
                    for raw_identifier in raw_identifiers
                ),
                requested_count=len(raw_identifiers),
                imported_count=0,
                duplicate_count=0,
                error_count=len(raw_identifiers),
            )
        for raw_identifier in raw_identifiers:
            normalized = normalize_source_identifier(
                raw_identifier,
                provider=normalization_provider,
            )
            if not normalized.is_valid:
                results.append(
                    IdentifierImportItemResult(
                        identifier=raw_identifier,
                        normalized_identifier=None,
                        status="error",
                        error_code="invalid_identifier",
                        message=normalized.error or "Invalid identifier",
                    )
                )
                continue

            result = await self._resolve_one_identifier(
                normalized=normalized,
                db_user=db_user,
                user_sub=str(user["sub"]),
                document_source_context=document_source_context,
            )
            results.append(result)
        if normalization_provider is not None:
            await normalization_provider.aclose()

        return IdentifierImportBatchResult(
            results=tuple(results),
            requested_count=len(raw_identifiers),
            imported_count=0,
            duplicate_count=sum(1 for result in results if result.status == "duplicate"),
            error_count=sum(1 for result in results if result.status == "error"),
        )

    async def _resolve_one_identifier(
        self,
        *,
        normalized: NormalizedSourceIdentifier,
        db_user: Any,
        user_sub: str,
        document_source_context: DocumentSourceRequestContext,
    ) -> IdentifierImportItemResult:
        assert normalized.normalized is not None
        curator_token = (document_source_context.curator_token or "").strip()
        provider: DocumentSourceProvider | None = None
        try:
            if not curator_token:
                return IdentifierImportItemResult(
                    identifier=normalized.original,
                    normalized_identifier=normalized.normalized,
                    status="error",
                    error_code="document_source_curator_token_unavailable",
                    message="Document-source import requires a curator bearer token.",
                )
            provider = self._provider_factory()
            decision = await self._reference_import_selector(
                provider=provider,
                identifier=normalized.normalized,
                authorized_group_ids=document_source_context.authorized_group_ids,
                bearer_kind=document_source_context.bearer_kind,
                request_bearer_token=curator_token,
                allow_conversion_request=False,
            )
            if not decision.can_import:
                return self._error_result_from_decision(normalized, decision)

            source_provenance = _build_reference_source_provenance(decision)
            existing_source_document = await self._find_active_source_duplicate(
                user_id=db_user.id,
                user_sub=user_sub,
                source_provider=decision.provider,
                source_provenance=source_provenance,
            )
            if existing_source_document:
                return IdentifierImportItemResult(
                    identifier=normalized.original,
                    normalized_identifier=normalized.normalized,
                    status="duplicate",
                    message="This provider document has already been imported.",
                    existing_document_id=str(existing_source_document.id),
                    filename=getattr(existing_source_document, "filename", None),
                    source_provenance=source_provenance,
                )

            filename = _safe_pdf_filename(
                decision.selected.source_artifact.display_name
                or (decision.reference.title if decision.reference else None)
                or normalized.normalized.replace(":", "_")
            )
            return IdentifierImportItemResult(
                identifier=normalized.original,
                normalized_identifier=normalized.normalized,
                status="resolved",
                message="Ready to import.",
                filename=filename,
                source_provenance=source_provenance,
            )
        except (DocumentSourceConfigError, DocumentSourceError) as exc:
            if isinstance(exc, DocumentSourceLoginExpired):
                _report_service_login_expired(
                    exc,
                    "source_resolve_failed",
                    bearer_kind=document_source_context.bearer_kind,
                )
            else:
                _report_source_failure(exc, "source_resolve_failed")
            logger.warning(
                "Document-source identifier resolve failed for %s: %s",
                normalized.normalized,
                exc,
                extra={"sentry_skip_event": True},
            )
            return IdentifierImportItemResult(
                identifier=normalized.original,
                normalized_identifier=normalized.normalized,
                status="error",
                error_code="document_source_unavailable",
                message="Document-source lookup is unavailable.",
            )
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception as cleanup_err:
                    logger.warning(
                        "Best-effort document-source provider cleanup failed: %s",
                        cleanup_err,
                    )

    async def _import_one_identifier(
        self,
        *,
        background_tasks: BackgroundTasks,
        normalized: NormalizedSourceIdentifier,
        user: Mapping[str, Any],
        db_user: Any,
        document_source_context: DocumentSourceRequestContext,
        chosen: tuple[Any, ...] = (),
        login_expires_at: float | None = None,
    ) -> IdentifierImportItemResult:
        assert normalized.normalized is not None
        curator_token = (document_source_context.curator_token or "").strip()
        provider: DocumentSourceProvider | None = None
        try:
            if not curator_token:
                return IdentifierImportItemResult(
                    identifier=normalized.original,
                    normalized_identifier=normalized.normalized,
                    status="error",
                    error_code="document_source_curator_token_unavailable",
                    message="Document-source import requires a curator bearer token.",
                )
            provider = self._provider_factory()
            decision = await self._reference_import_selector(
                provider=provider,
                identifier=normalized.normalized,
                authorized_group_ids=document_source_context.authorized_group_ids,
                bearer_kind=document_source_context.bearer_kind,
                request_bearer_token=curator_token,
            )
            if not decision.can_import:
                return self._error_result_from_decision(normalized, decision)
            if (
                any(item.converted_md5 is None for item in chosen)
                and decision.conversion_answer is None
            ):
                # An unconverted file can be chosen only when this import asks
                # the provider to convert the paper (it did not: for example the
                # main text became converted after the listing).
                return IdentifierImportItemResult(
                    identifier=normalized.original,
                    normalized_identifier=normalized.normalized,
                    status="error",
                    error_code="file_not_tickable",
                    message=file_not_tickable_message(decision.provider),
                )
            plan = None
            if chosen:
                resolved_chosen = _resolve_chosen_display_names(
                    chosen,
                    decision.artifacts,
                    provider=provider,
                    authorized_group_ids=document_source_context.authorized_group_ids,
                )
                plan = SupplementPlan(
                    chosen=resolved_chosen,
                    request_context=document_source_context,
                    login_expires_at=login_expires_at,
                    owner_user_id=db_user.id,
                )

            source_provenance = _build_reference_source_provenance(decision)
            existing_source_document = await self._find_active_source_duplicate(
                user_id=db_user.id,
                user_sub=str(user["sub"]),
                source_provider=decision.provider,
                source_provenance=source_provenance,
            )
            if existing_source_document:
                return IdentifierImportItemResult(
                    identifier=normalized.original,
                    normalized_identifier=normalized.normalized,
                    status="duplicate",
                    message="This provider document has already been imported.",
                    existing_document_id=str(existing_source_document.id),
                    filename=getattr(existing_source_document, "filename", None),
                    source_provenance=source_provenance,
                )

            source_pdf_bytes = await provider.download_artifact(
                decision.selected.source_artifact.artifact_id,
                request_bearer_token=curator_token,
            )
            _validate_source_pdf_bytes(source_pdf_bytes)
            return await self._persist_pdf_backed_import(
                background_tasks=background_tasks,
                normalized=normalized,
                user=user,
                db_user=db_user,
                source_pdf_bytes=source_pdf_bytes,
                decision=decision,
                source_provenance=source_provenance,
                curator_token=curator_token,
                bearer_kind=document_source_context.bearer_kind,
                wait_for_conversion=decision.waits_for_conversion,
                supplements=plan,
                login_expires_at=login_expires_at,
            )
        except DocumentSourceAccessDenied as exc:
            logger.warning(
                "Document-source PDF access denied for %s: %s",
                normalized.normalized,
                exc,
                extra={"sentry_skip_event": True},
            )
            return IdentifierImportItemResult(
                identifier=normalized.original,
                normalized_identifier=normalized.normalized,
                status="error",
                error_code="document_source_access_denied",
                message="Access to the document-source PDF was denied.",
            )
        except (DocumentSourceConfigError, DocumentSourceError) as exc:
            if isinstance(exc, DocumentSourceLoginExpired):
                _report_service_login_expired(
                    exc,
                    "source_import_failed",
                    bearer_kind=document_source_context.bearer_kind,
                )
            else:
                _report_source_failure(exc, "source_import_failed")
            logger.warning(
                "Document-source identifier import failed for %s: %s",
                normalized.normalized,
                exc,
                extra={"sentry_skip_event": True},
            )
            return IdentifierImportItemResult(
                identifier=normalized.original,
                normalized_identifier=normalized.normalized,
                status="error",
                error_code="document_source_unavailable",
                message="Document-source lookup is unavailable.",
            )
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception as cleanup_err:
                    logger.warning(
                        "Best-effort document-source provider cleanup failed: %s",
                        cleanup_err,
                    )

    async def _persist_pdf_backed_import(
        self,
        *,
        background_tasks: BackgroundTasks,
        normalized: NormalizedSourceIdentifier,
        user: Mapping[str, Any],
        db_user: Any,
        source_pdf_bytes: bytes,
        decision: ReferenceImportDecision,
        source_provenance: Mapping[str, Any],
        curator_token: str,
        bearer_kind: ProviderBearerKind,
        wait_for_conversion: bool = False,
        supplements: Any = None,
        login_expires_at: float | None = None,
    ) -> IdentifierImportItemResult:
        assert normalized.normalized is not None
        assert decision.selected is not None
        user_sub = str(user["sub"])
        document_id = str(uuid.uuid4())
        base_storage = Path(self._storage_path_provider())
        reference_title = decision.reference.title if decision.reference else None
        filename = _safe_pdf_filename(
            decision.selected.source_artifact.display_name
            or reference_title
            or f"{normalized.normalized.replace(':', '_')}.pdf"
        )
        saved_path = _write_source_pdf(
            base_storage=base_storage,
            user_sub=user_sub,
            document_id=document_id,
            filename=filename,
            content=source_pdf_bytes,
        )
        file_size_bytes = saved_path.stat().st_size
        raw_checksum = hashlib.sha256(source_pdf_bytes).hexdigest()
        scoped_file_hash = hashlib.sha256(f"{db_user.id}:{raw_checksum}".encode()).hexdigest()
        created_weaviate_document = False

        document = PDFDocument(
            id=document_id,
            filename=filename,
            file_size=file_size_bytes,
            creation_date=datetime.now(timezone.utc),
            last_accessed_date=datetime.now(timezone.utc),
            processing_status=ProcessingStatus.PENDING,
            embedding_status=EmbeddingStatus.PENDING,
            chunk_count=0,
            vector_count=0,
            metadata=DocumentMetadata(
                page_count=1,
                author=None,
                title=(decision.reference.title if decision.reference else None) or filename,
                checksum=raw_checksum,
                document_type="provider_import",
                last_processed_stage="upload",
            ),
            source_provenance=dict(source_provenance),
        )

        session = self._session_factory()
        try:
            existing_hash_document = (
                session.execute(
                    owned_paper_by_main_file_hash(db_user.id, (scoped_file_hash, raw_checksum))
                )
                .scalars()
                .first()
            )
            if existing_hash_document:
                existing_hash_document = await self._resolve_phantom_duplicate(
                    session,
                    existing_hash_document,
                    user_sub=user_sub,
                )
            if existing_hash_document:
                _cleanup_saved_pdf(saved_path)
                return IdentifierImportItemResult(
                    identifier=normalized.original,
                    normalized_identifier=normalized.normalized,
                    status="duplicate",
                    message="This PDF has already been imported.",
                    existing_document_id=str(existing_hash_document.id),
                    filename=getattr(existing_hash_document, "filename", None),
                    source_provenance=source_provenance,
                )

            await self._create_document(user_sub, document)
            created_weaviate_document = True
            relative_path = str(saved_path.relative_to(base_storage)).replace("\\", "/")
            record = ViewerPDFDocument(
                id=uuid.UUID(document_id),
                filename=filename,
                user_id=db_user.id,
                document_kind=DOCUMENT_KIND_LIBRARY,
                next_chunk_index=0,
                source_provider=source_provenance.get("provider"),
                source_provider_reference_id=source_provenance.get("reference_id"),
                source_provider_reference_curie=source_provenance.get("reference_curie"),
                source_external_ids=source_provenance.get("external_ids"),
            )
            record.files.append(PDFDocumentFile(
                role=FILE_ROLE_MAIN,
                display_name=filename,
                status=FILE_STATUS_PENDING,
                tab_order=0,
                display_mode=ViewerMode.LOCAL_PDF.value,
                file_path=relative_path,
                file_hash=scoped_file_hash,
                file_size=file_size_bytes,
                page_count=1,
                source_provider_source_file_id=source_provenance.get("source_file_id"),
                source_provider_pdf_artifact_id=source_provenance.get("pdf_artifact_id"),
                source_provider_converted_artifact_id=source_provenance.get(
                    "converted_artifact_id"
                ),
                source_md5=source_provenance.get("source_md5"),
                source_file_class=source_provenance.get("file_class"),
                source_file_extension=source_provenance.get("file_extension"),
                source_artifact_status=source_provenance.get("artifact_status"),
                source_import_status=(
                    "pending"
                    if source_provenance.get("converted_artifact_id") or wait_for_conversion
                    else None
                ),
                source_access_scope=source_provenance.get("access_scope"),
                source_access_group_ids=source_provenance.get("access_group_ids"),
            ))
            session.add(record)
            session.commit()
        except Exception:
            session.rollback()
            await self._compensate_partial_import(
                user_sub=user_sub,
                document_id=document_id,
                saved_path=saved_path,
                weaviate_document_created=created_weaviate_document,
            )
            raise
        finally:
            session.close()

        created_job_id: str | None = None
        try:
            job_values: dict[str, Any] = {}
            job_metadata: dict[str, Any] = {}
            if wait_for_conversion and decision.conversion_answer is not None:
                # This import sent the provider's on-demand request: the job keeps
                # that fact, so its wait never sends it again (KANBAN-2191 / ALL-1725).
                job_metadata[pdf_job_service.CONVERSION_REQUESTS_KEY] = {
                    "on_demand_sent_at": decision.conversion_requested_at.isoformat(),
                }
            if supplements is not None and supplements.chosen:
                from src.lib.document_sources.supplement_wait import queued_entries

                job_metadata["supplements"] = queued_entries(supplements)
            if job_metadata:
                job_values["metadata"] = job_metadata
            job = self._create_job(
                document_id=document_id,
                user_id=db_user.id,
                filename=filename,
                **job_values,
            )
            created_job_id = str(job.job_id)
            if decision.selected.converted_artifact is not None:
                await self.upload_execution_service.dispatch_provider_markdown_execution(
                    background_tasks=background_tasks,
                    request=ProviderMarkdownExecutionRequest(
                        document_id=document_id,
                        job_id=job.job_id,
                        user_id=user_sub,
                        owner_user_id=db_user.id,
                        filename=filename,
                        converted_artifact_id=decision.selected.converted_artifact.artifact_id,
                        curator_token=curator_token,
                        bearer_kind=bearer_kind,
                        source_provenance=source_provenance,
                        figure_metadata_artifact_ids=tuple(
                            artifact.artifact_id
                            for artifact in decision.selected.provider_metadata_artifacts
                        ),
                        file_path=saved_path,
                        supplements=supplements,
                        login_expires_at=login_expires_at,
                        conversion_requested=decision.conversion_answer is not None,
                        last_conversion_answer=decision.conversion_answer,
                    ),
                )
            elif wait_for_conversion:
                await self.upload_execution_service.dispatch_provider_conversion_execution(
                    background_tasks=background_tasks,
                    request=ProviderConversionExecutionRequest(
                        document_id=document_id,
                        job_id=job.job_id,
                        user_id=user_sub,
                        owner_user_id=db_user.id,
                        filename=filename,
                        reference=_reference_poll_value(decision),
                        source_artifact_id=decision.selected.source_artifact.artifact_id,
                        curator_token=curator_token,
                        bearer_kind=bearer_kind,
                        source_provenance=source_provenance,
                        figure_metadata_artifact_ids=tuple(
                            artifact.artifact_id
                            for artifact in decision.selected.provider_metadata_artifacts
                        ),
                        file_path=saved_path,
                        supplements=supplements,
                        login_expires_at=login_expires_at,
                        conversion_answer=decision.conversion_answer,
                    ),
                )
            else:
                await self.upload_execution_service.dispatch_upload_execution(
                    background_tasks=background_tasks,
                    request=UploadExecutionRequest(
                        document_id=document_id,
                        job_id=job.job_id,
                        user_id=user_sub,
                        file_path=saved_path,
                        supplements=supplements,
                        login_expires_at=login_expires_at,
                    ),
                )
        except Exception:
            await self._compensate_partial_import(
                user_sub=user_sub,
                document_id=document_id,
                saved_path=saved_path,
                weaviate_document_created=True,
                sql_record_committed=True,
                created_job_id=created_job_id,
            )
            raise

        return IdentifierImportItemResult(
            identifier=normalized.original,
            normalized_identifier=normalized.normalized,
            status="imported",
            message="Import queued for background processing.",
            document_id=document_id,
            job_id=job.job_id,
            filename=filename,
            source_provenance=source_provenance,
        )

    async def _find_active_source_duplicate(
        self,
        *,
        user_id: int,
        user_sub: str,
        source_provider: str,
        source_provenance: Mapping[str, Any],
    ) -> Any | None:
        duplicate_session = self._session_factory()
        try:
            existing_source_document = self._find_existing_source_document(
                duplicate_session,
                user_id=user_id,
                source_provider=source_provider,
                reference_id=source_provenance.get("reference_id"),
                reference_curie=source_provenance.get("reference_curie"),
                converted_artifact_id=source_provenance.get("converted_artifact_id"),
                source_md5=source_provenance.get("source_md5"),
            )
            if existing_source_document:
                existing_source_document = await self._resolve_phantom_duplicate(
                    duplicate_session,
                    existing_source_document,
                    user_sub=user_sub,
                )
            return existing_source_document
        finally:
            duplicate_session.close()

    async def _resolve_phantom_duplicate(
        self,
        session: Session,
        existing: Any,
        *,
        user_sub: str,
    ) -> Any | None:
        try:
            existing_weaviate_doc = await self._get_document(user_sub, str(existing.id))
            if existing_weaviate_doc:
                return existing
            self._cleanup_phantom_duplicate_row(session, existing)
            return None
        except ValueError:
            self._cleanup_phantom_duplicate_row(session, existing)
            return None
        except Exception:
            return existing

    def _cleanup_phantom_duplicate_row(self, session: Session, existing: Any) -> None:
        try:
            self._cleanup_document_dependencies(session, existing.id)
            session.delete(existing)
            session.commit()
        except Exception:
            session.rollback()
            raise

    async def _fail_undispatched_import_job(
        self,
        *,
        job_id: str,
        document_id_hash: str | None,
    ) -> bool:
        mark_job_failed = self._mark_job_failed_fn or pdf_job_service.mark_failed
        try:
            await asyncio.to_thread(
                mark_job_failed,
                job_id=job_id,
                message="Import dispatch failed before background processing started",
                stage="dispatch",
            )
            return True
        except Exception as exc:
            logger.warning(
                "Failed to mark undispatched import job failed (%s, document_id_hash=%s)",
                type(exc).__name__,
                document_id_hash,
                extra={"sentry_skip_event": True},
            )
            return False

    def _remove_committed_paper_files(
        self,
        *,
        document_id: str,
        user_sub: str,
        saved_path: Path,
        lock_conn: Any,
        document_id_hash: str | None,
    ) -> bool:
        bound_session = open_paper_lock_session(lock_conn)
        session = bound_session if bound_session is not None else self._session_factory()
        try:
            return remove_committed_paper_stored_files(
                session,
                document_id=document_id,
                user_sub=user_sub,
                base_storage=self._storage_path_provider(),
                fallback_cleanup=lambda: _cleanup_saved_pdf(saved_path),
            )
        except Exception as cleanup_err:
            session.rollback()
            logger.warning(
                "Failed to snapshot/remove committed import PDF artifacts (%s, document_id_hash=%s)",
                type(cleanup_err).__name__,
                document_id_hash,
                extra={"sentry_skip_event": True},
            )
            return False
        finally:
            session.close()

    def _delete_committed_sql_row(
        self,
        *,
        document_id: str,
        lock_conn: Any,
        document_id_hash: str | None,
    ) -> None:
        bound_session = open_paper_lock_session(lock_conn)
        session = bound_session if bound_session is not None else self._session_factory()
        try:
            persisted = (
                session.execute(
                    select(ViewerPDFDocument).where(
                        ViewerPDFDocument.id == uuid.UUID(document_id),
                    )
                )
                .scalars()
                .first()
            )
            if persisted:
                session.delete(persisted)
                session.commit()
        except Exception as cleanup_err:
            session.rollback()
            logger.warning(
                "Best-effort SQL cleanup failed during identifier import compensation "
                "(%s, document_id_hash=%s)",
                type(cleanup_err).__name__,
                document_id_hash,
                extra={"sentry_skip_event": True},
            )
        finally:
            session.close()

    async def _compensate_partial_import(
        self,
        *,
        user_sub: str,
        document_id: str,
        saved_path: Path,
        weaviate_document_created: bool,
        sql_record_committed: bool = False,
        created_job_id: str | None = None,
    ) -> None:
        document_id_hash = hash_sentry_identifier(document_id)

        if not sql_record_committed:
            # Pre-commit compensation: no SQL owner exists yet. If the staged PDF cannot
            # be removed, retain the Weaviate owner and report so no private file is stranded.
            if not _cleanup_saved_pdf(saved_path):
                report_compensation_file_cleanup_failure(
                    component="document_sources",
                    document_id=document_id,
                    sql_record_committed=False,
                    weaviate_document_created=weaviate_document_created,
                )
                return
            if weaviate_document_created:
                try:
                    await self._delete_document(user_sub, document_id)
                except Exception as cleanup_err:
                    logger.warning(
                        "Best-effort Weaviate cleanup failed for identifier import %s: %s",
                        document_id,
                        cleanup_err,
                    )
            return

        # Fail any undispatched pending job created by this import BEFORE acquiring
        # paper_operation_lock (shielded across cancellation) so neither a 1-connection
        # DB pool nor cancellation during lock acquisition can leave a 2-hour pending job.
        if created_job_id is not None:
            job_failed = await drain_shielded(
                self._fail_undispatched_import_job(
                    job_id=created_job_id,
                    document_id_hash=document_id_hash,
                )
            )
            if not job_failed:
                return

        lock_fn = self._paper_operation_lock_fn or paper_operation_lock
        get_active_job = self._get_active_job_fn or pdf_job_service.get_active_job_for_document
        try:
            async with lock_fn(document_id) as lock_conn:
                try:
                    active_job = await asyncio.to_thread(
                        get_active_job,
                        document_id=document_id,
                        reconcile_stale=False,
                        connection=lock_conn,
                    )
                except Exception as job_check_err:
                    logger.warning(
                        "Skipping import compensation because active-job check failed "
                        "(%s, document_id_hash=%s)",
                        type(job_check_err).__name__,
                        document_id_hash,
                        extra={"sentry_skip_event": True},
                    )
                    return
                if active_job is not None:
                    logger.warning(
                        "Skipping import compensation because an active job exists "
                        "(document_id_hash=%s)",
                        document_id_hash,
                        extra={"sentry_skip_event": True},
                    )
                    return

                # Snapshot and remove live SQL-owned stored files under the paper row lock
                # first; if cleanup fails, retain both Weaviate and SQL ownership for retry.
                files_removed = await asyncio.to_thread(
                    self._remove_committed_paper_files,
                    document_id=document_id,
                    user_sub=user_sub,
                    saved_path=saved_path,
                    lock_conn=lock_conn,
                    document_id_hash=document_id_hash,
                )
                if not files_removed:
                    report_compensation_file_cleanup_failure(
                        component="document_sources",
                        document_id=document_id,
                        sql_record_committed=True,
                        weaviate_document_created=weaviate_document_created,
                    )
                    return

                # Let the Weaviate wipe outcome govern SQL owner deletion: if delete_document
                # reports an error or incomplete chunk wipe (or raises CancelledError after an
                # incomplete/unstarted wipe), retain the SQL owner alongside the Weaviate parent.
                # When delete_document owns SQL deletion (reporting "postgres_deleted"), do not
                # issue a second SQL delete pass.
                weaviate_cleanup_ok = True
                sql_owned_by_delete_document = False
                if weaviate_document_created:
                    try:
                        delete_result = await self._delete_document(user_sub, document_id)
                    except Exception as cleanup_err:
                        logger.warning(
                            "Best-effort Weaviate cleanup failed for identifier import %s: %s",
                            document_id,
                            cleanup_err,
                        )
                        weaviate_cleanup_ok = False
                    else:
                        if isinstance(delete_result, dict):
                            if "error" in delete_result or "chunks_failed" in delete_result:
                                weaviate_cleanup_ok = False
                            elif "postgres_deleted" in delete_result:
                                sql_owned_by_delete_document = True

                if not weaviate_cleanup_ok or sql_owned_by_delete_document:
                    return

                await asyncio.to_thread(
                    self._delete_committed_sql_row,
                    document_id=document_id,
                    lock_conn=lock_conn,
                    document_id_hash=document_id_hash,
                )
        except PaperOperationInProgress:
            logger.warning(
                "Skipping import compensation because another operation holds the paper lock "
                "(document_id_hash=%s)",
                document_id_hash,
                extra={"sentry_skip_event": True},
            )
        except Exception as lock_err:
            logger.warning(
                "Skipping import compensation because acquiring the paper lock failed "
                "(%s, document_id_hash=%s)",
                type(lock_err).__name__,
                document_id_hash,
                extra={"sentry_skip_event": True},
            )

    @staticmethod
    def _error_result_from_decision(
        normalized: NormalizedSourceIdentifier,
        decision: ReferenceImportDecision,
    ) -> IdentifierImportItemResult:
        error_code_by_status = {
            ReferenceImportDecisionStatus.REFERENCE_NOT_FOUND: "document_source_reference_not_found",
            ReferenceImportDecisionStatus.NO_SOURCE_ARTIFACT: "document_source_no_source_artifact",
            ReferenceImportDecisionStatus.ACCESS_DENIED: "document_source_access_denied",
            ReferenceImportDecisionStatus.AMBIGUOUS_MATCH: "document_source_ambiguous_match",
            ReferenceImportDecisionStatus.NO_CONVERTED_TEXT: "document_source_no_converted_text",
            ReferenceImportDecisionStatus.CONVERSION_RUNNING: "document_source_conversion_running",
            ReferenceImportDecisionStatus.CONVERSION_FAILED: "document_source_conversion_failed",
        }
        return IdentifierImportItemResult(
            identifier=normalized.original,
            normalized_identifier=normalized.normalized,
            status="error",
            error_code=error_code_by_status.get(
                decision.status,
                "document_source_import_unavailable",
            ),
            message=decision.message or "Document-source import is unavailable.",
        )


def _reference_decision(
    *,
    provider: str,
    identifier: str,
    reference: SourceReference | None,
    status: str,
    selected: ReferenceImportCandidate | None = None,
    candidates: tuple[ReferenceImportCandidate, ...] = (),
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
    conversion_answer: SourceConversionResult | None = None,
    conversion_requested_at: datetime | None = None,
    artifacts: tuple[SourceArtifact, ...] = (),
) -> ReferenceImportDecision:
    return ReferenceImportDecision(
        status=status,
        provider=provider,
        identifier=identifier,
        reference=reference,
        selected=selected,
        candidates=candidates,
        message=message,
        metadata=metadata or {},
        conversion_answer=conversion_answer,
        conversion_requested_at=conversion_requested_at,
        artifacts=artifacts,
    )


def _converted_markdown_artifacts_for_source(
    *,
    source_artifact: SourceArtifact,
    artifacts: list[SourceArtifact],
    bearer_kind: ProviderBearerKind,
) -> tuple[SourceArtifact, ...]:
    converted: list[SourceArtifact] = []
    for artifact in artifacts:
        if artifact.role is not SourceArtifactRole.CONVERTED_TEXT:
            continue
        if artifact.artifact_format is not SourceArtifactFormat.MARKDOWN:
            continue
        if bearer_kind is ProviderBearerKind.SERVICE:
            if artifact_is_bound_to_source(artifact, source_artifact):
                converted.append(artifact)
            continue
        if artifact.parent_artifact_id:
            if artifact.parent_artifact_id == source_artifact.artifact_id:
                converted.append(artifact)
            continue
        if _same_reference(source_artifact, artifact):
            converted.append(artifact)
    return tuple(converted)


def _select_preferred_reference_source_artifact(
    *,
    provider: DocumentSourceProvider,
    artifacts: tuple[SourceArtifact, ...],
    authorized_group_ids: tuple[str, ...] | list[str] | set[str],
) -> tuple[SourceArtifact | None, int]:
    """Select a provider-ranked source PDF without guessing on equal ranks."""

    if not artifacts:
        return None, 0
    if len(artifacts) == 1:
        return artifacts[0], 1

    source_artifact_sort_key = getattr(
        provider,
        "reference_source_artifact_sort_key",
        None,
    )
    if not callable(source_artifact_sort_key):
        return None, len(artifacts)
    typed_source_artifact_sort_key = cast(
        Callable[
            [SourceArtifact, tuple[str, ...] | list[str] | set[str]],
            Iterable[int],
        ],
        source_artifact_sort_key,
    )
    ranked = sorted(
        (
            (
                tuple(
                    typed_source_artifact_sort_key(
                        artifact,
                        authorized_group_ids,
                    )
                ),
                artifact,
            )
            for artifact in artifacts
        ),
        key=lambda item: (item[0], item[1].artifact_id),
    )
    best_rank = ranked[0][0]
    best = [artifact for rank, artifact in ranked if rank == best_rank]
    if len(best) > 1:
        return None, len(best)
    return best[0], 1


def _same_reference(source_artifact: SourceArtifact, artifact: SourceArtifact) -> bool:
    if source_artifact.reference_id and artifact.reference_id:
        return source_artifact.reference_id == artifact.reference_id
    if source_artifact.reference_curie and artifact.reference_curie:
        return source_artifact.reference_curie == artifact.reference_curie
    return not artifact.reference_id and not artifact.reference_curie


async def _request_reference_conversion_if_supported(
    *,
    provider: DocumentSourceProvider,
    reference: SourceReference,
    source_artifact: SourceArtifact,
    request_bearer_token: str | None,
) -> SourceConversionResult | None:
    if not (
        reference.reference_curie
        or reference.reference_id
        or source_artifact.reference_curie
        or source_artifact.reference_id
    ):
        return None
    try:
        return await provider.request_conversion(
            reference,
            wait=False,
            request_bearer_token=request_bearer_token,
        )
    except (AttributeError, NotImplementedError):
        return None


def _select_reference_level_markdown_artifact(
    *,
    provider: DocumentSourceProvider,
    source_artifact: SourceArtifact,
    artifacts: list[SourceArtifact],
) -> tuple[SourceArtifact | None, int]:
    candidates = tuple(
        artifact
        for artifact in artifacts
        if artifact.role is SourceArtifactRole.CONVERTED_TEXT
        and artifact.artifact_format is SourceArtifactFormat.MARKDOWN
        and _same_reference(source_artifact, artifact)
    )
    return select_preferred_main_text_artifact(provider, candidates)


def _reference_conversion_metadata(result: SourceConversionResult) -> dict[str, Any]:
    metadata: dict[str, Any] = {"conversion_status": result.status.value}
    if result.job_id:
        metadata["conversion_job_id"] = result.job_id
    if result.converted_classes:
        metadata["converted_classes"] = list(result.converted_classes)
    if result.per_file_progress:
        metadata["per_file_progress"] = list(result.per_file_progress)
    return metadata


def _normalize_display_name(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None


def _resolve_chosen_display_names(
    chosen: Sequence[ChosenSupplement],
    artifacts: Sequence[SourceArtifact],
    *,
    provider: DocumentSourceProvider,
    authorized_group_ids: tuple[str, ...] | list[str] | set[str],
) -> tuple[ChosenSupplement, ...]:
    """Attach the authoritative human-readable display name from visible listed supplements.

    Applies the exact visible-supplement access rule from ``file_listing`` (checking
    both the original and any listed converted supplement text access policies) and
    never falls back to untrusted caller-supplied display names or raw ``provider_file_id``.
    """
    from src.lib.document_sources.file_listing import visible_supplement_candidates

    visible_candidates, _ = visible_supplement_candidates(
        provider,
        artifacts,
        authorized_group_ids=authorized_group_ids,
    )
    listed_names: dict[str, str] = {}
    for candidate in visible_candidates:
        name = _normalize_display_name(candidate.original.display_name)
        if name is not None:
            listed_names[candidate.original.artifact_id] = name
    return tuple(
        ChosenSupplement(
            provider_file_id=item.provider_file_id,
            md5=item.md5,
            converted_md5=item.converted_md5,
            display_name=listed_names.get(item.provider_file_id),
        )
        for item in chosen
    )


def _chosen_supplements_by_identifier(
    raw_identifiers: Sequence[str], supplements: Sequence[Any],
) -> dict[str, tuple[Any, ...]]:
    """Group the ticked supplements by identifier; refuse a selection that cannot be imported."""

    known = set(raw_identifiers)
    grouped: dict[str, list[ChosenSupplement]] = {}
    for choice in supplements:
        if choice.identifier not in known:
            raise SupplementSelectionError("supplement_identifier_unknown")
        grouped.setdefault(choice.identifier, []).append(
            ChosenSupplement(
                provider_file_id=choice.provider_file_id,
                md5=choice.md5,
                converted_md5=choice.converted_md5,
            )
        )
    file_limit = get_document_max_files_per_paper()
    for chosen in grouped.values():
        ids = [item.provider_file_id for item in chosen]
        if len(set(ids)) != len(ids):
            raise SupplementSelectionError("file_chosen_twice")
        if len(chosen) + 1 > file_limit:  # the main paper counts
            raise SupplementSelectionError("paper_file_limit", limit_value=file_limit)
    return {identifier: tuple(chosen) for identifier, chosen in grouped.items()}


def _login_expires_at(user: Mapping[str, Any], bearer_kind: ProviderBearerKind) -> float | None:
    """The curator's login expiry from the validated claims (``exp``, optional in the claims)."""

    if bearer_kind is not ProviderBearerKind.CURATOR:
        return None
    expires = user.get("exp")
    return float(expires) if expires is not None else None


def _reference_poll_value(decision: ReferenceImportDecision) -> str:
    if decision.reference is not None:
        reference = decision.reference.reference_curie or decision.reference.reference_id
        if reference:
            return reference
    if decision.selected is not None:
        artifact = decision.selected.source_artifact
        reference = artifact.reference_curie or artifact.reference_id
        if reference:
            return reference
    raise DocumentSourceError("Reference import could not determine a conversion poll reference")


def _build_reference_source_provenance(decision: ReferenceImportDecision) -> dict[str, Any]:
    if decision.selected is None:
        raise DocumentSourceError("Reference import requires a selected source artifact")
    reference = decision.selected.reference
    source_artifact = decision.selected.source_artifact
    converted_artifact = decision.selected.converted_artifact
    raw_provenance = {
        "provider": decision.provider,
        "reference_id": reference.reference_id
        or source_artifact.reference_id
        or (converted_artifact.reference_id if converted_artifact is not None else None),
        "reference_curie": reference.reference_curie
        or source_artifact.reference_curie
        or (converted_artifact.reference_curie if converted_artifact is not None else None),
        "source_file_id": source_artifact.metadata.get("source_file_id")
        or source_artifact.artifact_id,
        "pdf_artifact_id": source_artifact.artifact_id,
        "source_md5": source_artifact.md5sum,
        "file_class": source_artifact.metadata.get("file_class")
        or _enum_value(source_artifact.role),
        "file_extension": source_artifact.metadata.get("file_extension")
        or _enum_value(source_artifact.artifact_format),
        "artifact_status": _enum_value(source_artifact.status),
        "access_scope": _enum_value(source_artifact.access_policy.scope),
        "access_group_ids": list(source_artifact.access_policy.group_ids),
        "viewer_mode": ViewerMode.LOCAL_PDF.value,
    }
    if converted_artifact is not None:
        raw_provenance.update(
            {
                "converted_artifact_id": converted_artifact.artifact_id,
                "file_class": converted_artifact.metadata.get("file_class")
                or _enum_value(converted_artifact.role),
                "file_extension": converted_artifact.metadata.get("file_extension")
                or _enum_value(converted_artifact.artifact_format),
                "artifact_status": _enum_value(converted_artifact.status),
            }
        )
    if reference.external_ids:
        raw_provenance["external_ids"] = reference.external_ids
    sanitized = sanitize_document_source_provenance(raw_provenance)
    if not sanitized:
        raise DocumentSourceError("Reference import could not build source provenance")
    return sanitized


def _enum_value(value: object) -> str | None:
    return getattr(value, "value", str(value) if value is not None else None)


def _validate_source_pdf_bytes(content: bytes) -> None:
    if not content:
        raise DocumentSourceError("Source PDF download was empty")
    if len(content) > MAX_PDF_FILE_SIZE_BYTES:
        raise DocumentSourceError(pdf_file_size_limit_message(len(content)))
    if not content.startswith(b"%PDF-"):
        raise DocumentSourceError("Source artifact download did not look like a PDF")


def _safe_pdf_filename(value: str | None) -> str:
    filename = Path((value or "document.pdf").strip()).name
    filename = re.sub(r"[^A-Za-z0-9._ -]+", "_", filename).strip(" .")
    if not filename:
        filename = "document.pdf"
    if not filename.lower().endswith(".pdf"):
        filename = f"{filename}.pdf"
    return filename[:255]


def _write_source_pdf(
    *,
    base_storage: Path,
    user_sub: str,
    document_id: str,
    filename: str,
    content: bytes,
) -> Path:
    doc_dir = ensure_writable_directory(base_storage / user_sub / document_id)
    saved_path = doc_dir / filename
    saved_path.write_bytes(content)
    return saved_path


def _cleanup_saved_pdf(saved_path: Path) -> bool:
    try:
        if not saved_path.exists() and not saved_path.is_symlink():
            return True
        try:
            shutil.rmtree(saved_path.parent)
        except FileNotFoundError:
            pass
        return not saved_path.exists() and not saved_path.is_symlink()
    except OSError as exc:
        logger.warning(
            "Failed to remove staged PDF artifacts (%s)",
            type(exc).__name__,
            extra={"sentry_skip_event": True},
        )
        return False
