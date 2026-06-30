"""Import provider documents by curator-supplied source identifiers."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, cast

from fastapi import BackgroundTasks
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.config import get_pdf_storage_path
from src.lib.document_cleanup import cleanup_document_curation_dependencies
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.import_selection import (
    provider_metadata_artifacts_for_source,
    source_artifact_is_authorized,
)
from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceError,
    DocumentSourceProvider,
    NormalizedSourceIdentifier,
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
from src.lib.document_sources.registry import get_configured_document_source_provider
from src.lib.openai_agents.config import get_document_source_import_batch_limit
from src.lib.pdf_jobs import service as pdf_job_service
from src.lib.pdf_jobs.upload_execution_service import (
    ProviderConversionExecutionRequest,
    ProviderMarkdownExecutionRequest,
    UploadExecutionRequest,
    UploadExecutionService,
)
from src.lib.pdf_limits import MAX_PDF_FILE_SIZE_BYTES, pdf_file_size_limit_message
from src.lib.storage_permissions import ensure_writable_directory
from src.lib.weaviate_client.documents import create_document, delete_document, get_document
from src.lib.weaviate_helpers import get_tenant_name
from src.models.document import (
    DocumentMetadata,
    EmbeddingStatus,
    PDFDocument,
    ProcessingStatus,
)
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument as ViewerPDFDocument
from src.services.user_service import principal_from_claims, provision_user

logger = logging.getLogger(__name__)

_PMID_RE = re.compile(r"^(?:PMID|PUBMED(?:\s+ID)?)\s*[:#]?\s*(\d+)$", re.IGNORECASE)


class IdentifierImportValidationError(ValueError):
    """Raised when the identifier import request itself is invalid."""


class ReferenceImportDecisionStatus(str):
    """Decision categories for reference-backed imports."""

    READY = "ready"
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

    @property
    def is_ready(self) -> bool:
        return self.status == ReferenceImportDecisionStatus.READY and self.selected is not None


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
    request_bearer_token: str | None = None,
    allow_conversion_request: bool = True,
) -> ReferenceImportDecision:
    """Resolve a provider reference to exactly one authorized source PDF.

    Converted main Markdown is preferred when it is ready. Import callers may
    request provider conversion for known unconverted references; dry-run
    resolve callers must leave provider state untouched.
    """

    reference = await provider.resolve_reference(
        identifier,
        request_bearer_token=request_bearer_token,
    )
    artifacts = await provider.list_artifacts(
        reference,
        request_bearer_token=request_bearer_token,
    )
    source_artifacts: tuple[SourceArtifact, ...] = tuple(
        artifact for artifact in artifacts if artifact.role is SourceArtifactRole.SOURCE_PDF
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
    if len(authorized_sources) > 1:
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.AMBIGUOUS_MATCH,
            message="Multiple source PDFs require curator selection",
            metadata={"match_count": len(authorized_sources)},
        )

    source_artifact = next(iter(authorized_sources), None)
    if source_artifact is None:
        return _reference_decision(
            provider=provider.provider_id,
            identifier=identifier,
            reference=reference,
            status=ReferenceImportDecisionStatus.ACCESS_DENIED,
            message="No source PDF artifact is accessible to this curator",
        )
    provider_metadata_artifacts = provider_metadata_artifacts_for_source(
        source_artifact=source_artifact,
        artifacts=artifacts,
    )
    markdown_artifacts = _converted_markdown_artifacts_for_source(
        source_artifact=source_artifact,
        artifacts=artifacts,
    )
    ready_artifacts = tuple(
        artifact
        for artifact in markdown_artifacts
        if _converted_artifact_is_ready(artifact)
        and _provider_is_main_text_artifact(provider, artifact)
    )
    selected_artifact, ambiguous_count = _select_preferred_converted_markdown(
        provider,
        ready_artifacts
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
        )
    if any(
        artifact.status is SourceArtifactStatus.RUNNING
        and _provider_is_main_text_artifact(provider, artifact)
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
            ),
            message="Provider conversion is still running",
        )
    if any(
        artifact.status is SourceArtifactStatus.FAILED
        and _provider_is_main_text_artifact(provider, artifact)
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
    if allow_conversion_request:
        conversion_result = await _request_reference_conversion_if_supported(
            provider=provider,
            reference=reference,
            source_artifact=source_artifact,
            request_bearer_token=request_bearer_token,
        )
    if conversion_result is not None:
        conversion_metadata = _reference_conversion_metadata(conversion_result)
        if _reference_conversion_has_main_text(
            provider,
            conversion_result,
        ):
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
                source_artifact=source_artifact,
                artifacts=refreshed_artifacts,
            )
            if ambiguous_count > 1:
                return _reference_decision(
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
                    provider=provider.provider_id,
                    identifier=identifier,
                    reference=reference,
                    status=ReferenceImportDecisionStatus.READY,
                    selected=candidate,
                    candidates=(candidate,),
                    message="Provider conversion produced main Markdown",
                    metadata=conversion_metadata,
                )
        if conversion_result.status is SourceConversionStatus.RUNNING:
            return _reference_decision(
                provider=provider.provider_id,
                identifier=identifier,
                reference=reference,
                status=ReferenceImportDecisionStatus.CONVERSION_RUNNING,
                selected=ReferenceImportCandidate(
                    reference=reference,
                    source_artifact=source_artifact,
                ),
                message="Provider conversion is still running",
                metadata=conversion_metadata,
            )
        if conversion_result.status is SourceConversionStatus.FAILED:
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
                metadata=conversion_metadata,
            )
        if conversion_result.status is SourceConversionStatus.NO_SOURCES:
            return _reference_decision(
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
        provision_user_fn: Callable[[Session, Any], Any] = provision_user,
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

    async def import_identifiers(
        self,
        *,
        background_tasks: BackgroundTasks,
        identifiers: str,
        user: Mapping[str, Any],
        document_source_context: DocumentSourceRequestContext,
    ) -> IdentifierImportBatchResult:
        """Import a batch of source identifiers and return per-item status."""

        raw_identifiers = parse_source_identifier_batch(
            identifiers,
            batch_limit=self._import_batch_limit_provider(),
        )
        session = self._session_factory()
        try:
            db_user = self._provision_user(session, self._principal_from_claims(dict(user)))
        finally:
            session.close()

        curator_token = (document_source_context.curator_token or "").strip()
        if not curator_token:
            return _batch_document_source_token_unavailable(raw_identifiers)

        results: list[IdentifierImportItemResult] = []
        normalization_provider: DocumentSourceProvider | None = None
        try:
            normalization_provider = self._provider_factory()
        except (DocumentSourceConfigError, DocumentSourceError) as exc:
            logger.warning("Document-source identifier normalization unavailable: %s", exc)
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
        session = self._session_factory()
        try:
            db_user = self._provision_user(session, self._principal_from_claims(dict(user)))
        finally:
            session.close()

        curator_token = (document_source_context.curator_token or "").strip()
        if not curator_token:
            return _batch_document_source_token_unavailable(raw_identifiers)

        results: list[IdentifierImportItemResult] = []
        normalization_provider: DocumentSourceProvider | None = None
        try:
            normalization_provider = self._provider_factory()
        except (DocumentSourceConfigError, DocumentSourceError) as exc:
            logger.warning("Document-source identifier normalization unavailable: %s", exc)
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
                request_bearer_token=curator_token,
                allow_conversion_request=False,
            )
            wait_for_conversion = (
                decision.status == ReferenceImportDecisionStatus.CONVERSION_RUNNING
                and decision.selected is not None
            )
            if not decision.is_ready and not wait_for_conversion:
                return self._error_result_from_decision(normalized, decision)
            if decision.selected is None:
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
            logger.warning(
                "Document-source identifier resolve failed for %s: %s",
                normalized.normalized,
                exc,
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
                request_bearer_token=curator_token,
            )
            wait_for_conversion = (
                decision.status == ReferenceImportDecisionStatus.CONVERSION_RUNNING
                and decision.selected is not None
            )
            if not decision.is_ready and not wait_for_conversion:
                return self._error_result_from_decision(normalized, decision)
            if decision.selected is None:
                return self._error_result_from_decision(normalized, decision)

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
                wait_for_conversion=wait_for_conversion,
            )
        except (DocumentSourceConfigError, DocumentSourceError) as exc:
            logger.warning(
                "Document-source identifier import failed for %s: %s",
                normalized.normalized,
                exc,
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
        wait_for_conversion: bool = False,
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
        scoped_file_hash = hashlib.sha256(f"{db_user.id}:{raw_checksum}".encode("utf-8")).hexdigest()
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
                    select(ViewerPDFDocument).where(
                        ViewerPDFDocument.user_id == db_user.id,
                        or_(
                            ViewerPDFDocument.file_hash == scoped_file_hash,
                            ViewerPDFDocument.file_hash == raw_checksum,
                        ),
                    )
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
                file_path=relative_path,
                file_hash=scoped_file_hash,
                file_size=file_size_bytes,
                page_count=1,
                user_id=db_user.id,
                viewer_mode=ViewerMode.LOCAL_PDF.value,
                source_provider=source_provenance.get("provider"),
                source_provider_reference_id=source_provenance.get("reference_id"),
                source_provider_reference_curie=source_provenance.get("reference_curie"),
                source_provider_source_file_id=source_provenance.get("source_file_id"),
                source_provider_pdf_artifact_id=source_provenance.get("pdf_artifact_id"),
                source_provider_converted_artifact_id=source_provenance.get(
                    "converted_artifact_id"
                ),
                source_external_ids=source_provenance.get("external_ids"),
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
                source_access_mods=source_provenance.get("access_mods"),
            )
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

        try:
            job = self._create_job(
                document_id=document_id,
                user_id=db_user.id,
                filename=filename,
            )
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
                        source_provenance=source_provenance,
                        figure_metadata_artifact_ids=tuple(
                            artifact.artifact_id
                            for artifact in decision.selected.provider_metadata_artifacts
                        ),
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
                        source_provenance=source_provenance,
                        figure_metadata_artifact_ids=tuple(
                            artifact.artifact_id
                            for artifact in decision.selected.provider_metadata_artifacts
                        ),
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
                    ),
                )
        except Exception:
            await self._compensate_partial_import(
                user_sub=user_sub,
                document_id=document_id,
                saved_path=saved_path,
                weaviate_document_created=True,
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

    async def _compensate_partial_import(
        self,
        *,
        user_sub: str,
        document_id: str,
        saved_path: Path,
        weaviate_document_created: bool,
    ) -> None:
        if weaviate_document_created:
            try:
                await self._delete_document(user_sub, document_id)
            except Exception as cleanup_err:
                logger.warning(
                    "Best-effort Weaviate cleanup failed for identifier import %s: %s",
                    document_id,
                    cleanup_err,
                )
        session = self._session_factory()
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
        except Exception:
            session.rollback()
        finally:
            session.close()
        _cleanup_saved_pdf(saved_path)

    @staticmethod
    def _error_result_from_decision(
        normalized: NormalizedSourceIdentifier,
        decision: ReferenceImportDecision,
    ) -> IdentifierImportItemResult:
        error_code_by_status = {
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
    )


def _converted_markdown_artifacts_for_source(
    *,
    source_artifact: SourceArtifact,
    artifacts: list[SourceArtifact],
) -> tuple[SourceArtifact, ...]:
    converted: list[SourceArtifact] = []
    for artifact in artifacts:
        if artifact.role is not SourceArtifactRole.CONVERTED_TEXT:
            continue
        if artifact.artifact_format is not SourceArtifactFormat.MARKDOWN:
            continue
        if artifact.parent_artifact_id:
            if artifact.parent_artifact_id == source_artifact.artifact_id:
                converted.append(artifact)
            continue
        if _same_reference(source_artifact, artifact):
            converted.append(artifact)
    return tuple(converted)


def _same_reference(source_artifact: SourceArtifact, artifact: SourceArtifact) -> bool:
    if source_artifact.reference_id and artifact.reference_id:
        return source_artifact.reference_id == artifact.reference_id
    if source_artifact.reference_curie and artifact.reference_curie:
        return source_artifact.reference_curie == artifact.reference_curie
    return not artifact.reference_id and not artifact.reference_curie


def _converted_artifact_is_ready(artifact: SourceArtifact) -> bool:
    return artifact.status in {SourceArtifactStatus.AVAILABLE, SourceArtifactStatus.UNKNOWN}


def _select_preferred_converted_markdown(
    provider: DocumentSourceProvider,
    artifacts: tuple[SourceArtifact, ...],
) -> tuple[SourceArtifact | None, int]:
    if not artifacts:
        return None, 0
    ranked = sorted(
        ((_provider_main_text_sort_key(provider, artifact), artifact) for artifact in artifacts),
        key=lambda item: (item[0], item[1].artifact_id),
    )
    best_rank = ranked[0][0]
    best = [artifact for rank, artifact in ranked if rank == best_rank]
    if len(best) > 1:
        return None, len(best)
    return best[0], 1


def _provider_is_main_text_artifact(
    provider: DocumentSourceProvider,
    artifact: SourceArtifact,
) -> bool:
    is_main_text_artifact = getattr(provider, "is_main_text_artifact", None)
    if callable(is_main_text_artifact):
        typed_is_main_text_artifact = cast(
            Callable[[SourceArtifact], bool],
            is_main_text_artifact,
        )
        return bool(typed_is_main_text_artifact(artifact))
    return True


def _provider_main_text_sort_key(
    provider: DocumentSourceProvider,
    artifact: SourceArtifact,
) -> tuple[int, ...]:
    main_text_artifact_sort_key = getattr(provider, "main_text_artifact_sort_key", None)
    if callable(main_text_artifact_sort_key):
        typed_main_text_artifact_sort_key = cast(
            Callable[[SourceArtifact], Iterable[int]],
            main_text_artifact_sort_key,
        )
        return tuple(typed_main_text_artifact_sort_key(artifact))
    return (0,)


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


def _reference_conversion_has_main_text(
    provider: DocumentSourceProvider,
    result: SourceConversionResult,
) -> bool:
    conversion_exposes_main_text = getattr(provider, "conversion_exposes_main_text", None)
    if callable(conversion_exposes_main_text):
        typed_conversion_exposes_main_text = cast(
            Callable[[SourceConversionResult], bool],
            conversion_exposes_main_text,
        )
        return bool(typed_conversion_exposes_main_text(result))
    return (
        result.status in {
            SourceConversionStatus.CONVERTED,
            SourceConversionStatus.RUNNING,
        }
        and bool(result.converted_classes or result.per_file_progress)
    )


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
        and artifact.status in {SourceArtifactStatus.AVAILABLE, SourceArtifactStatus.UNKNOWN}
        and _provider_is_main_text_artifact(provider, artifact)
        and _same_reference(source_artifact, artifact)
    )
    return _select_preferred_converted_markdown(provider, candidates)


def _reference_conversion_metadata(result: SourceConversionResult) -> dict[str, Any]:
    metadata: dict[str, Any] = {"conversion_status": result.status.value}
    if result.job_id:
        metadata["conversion_job_id"] = result.job_id
    if result.converted_classes:
        metadata["converted_classes"] = list(result.converted_classes)
    if result.per_file_progress:
        metadata["per_file_progress"] = list(result.per_file_progress)
    if result.per_mod_status:
        metadata["per_mod_status"] = list(result.per_mod_status)
    return metadata


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
        "access_mods": {"mods": list(source_artifact.access_policy.mods)},
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


def _cleanup_saved_pdf(saved_path: Path) -> None:
    if saved_path.exists():
        shutil.rmtree(saved_path.parent, ignore_errors=True)
