"""Runtime service for upload intake choreography.

The intake output contract is `UploadIntakeResult`, which includes:
- `document_id`
- `job_id`
- initial `status` (always `PENDING`)
- ownership and tenant metadata used by the upload response
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import BackgroundTasks, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import get_pdf_storage_path
from src.lib.document_cleanup import cleanup_document_curation_dependencies
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.import_selection import (
    ChecksumImportDecision,
    ChecksumImportCandidate,
    ChecksumImportDecisionStatus,
    select_checksum_import_candidate,
)
from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceError,
    DocumentSourceProvider,
    SourceArtifact,
    ViewerMode,
)
from src.lib.document_sources.provenance import (
    find_existing_document_by_source,
    sanitize_document_source_provenance,
)
from src.lib.document_sources.registry import (
    LOCAL_PDF_PROVIDER_ID,
    get_configured_document_source_provider,
)
from src.lib.openai_agents.config import (
    get_document_source_import_enabled,
    get_document_source_provider,
    get_pdf_upload_max_page_count,
)
from src.lib.pdf_limits import (
    MAX_PDF_FILE_SIZE_BYTES,
    pdf_file_size_limit_message,
)
from src.lib.pdf_jobs.upload_execution_service import (
    ProviderConversionExecutionRequest,
    ProviderMarkdownExecutionRequest,
    UploadExecutionRequest,
    UploadExecutionService,
)
from src.lib.pipeline.upload import PDFUploadHandler, UploadError, generate_checksum
from src.lib.storage_permissions import ensure_writable_directory
from src.lib.weaviate_client.documents import create_document, delete_document, get_document
from src.lib.weaviate_helpers import get_tenant_name
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument as ViewerPDFDocument
from src.services.user_service import principal_from_claims, provision_user
from . import service as pdf_job_service

logger = logging.getLogger(__name__)

_PDF_DOCUMENT_DUPLICATE_CONSTRAINTS = frozenset(
    {
        "uq_pdf_documents_file_hash",
        "uq_pdf_documents_file_path",
    }
)

_CHECKSUM_DECISION_ERROR_FIELDS: dict[
    ChecksumImportDecisionStatus,
    tuple[int, str, str, str],
] = {
    ChecksumImportDecisionStatus.NO_MATCH: (
        404,
        "document_source_no_match",
        "No matching source document was found for this PDF.",
        "Choose a PDF already available from the configured document source.",
    ),
    ChecksumImportDecisionStatus.NO_SOURCE_ARTIFACT: (
        422,
        "document_source_no_source_artifact",
        "The document source match did not include an importable source PDF.",
        "Contact support with the uploaded paper details.",
    ),
    ChecksumImportDecisionStatus.ACCESS_DENIED: (
        403,
        "document_source_access_denied",
        "No matching source document is accessible to this curator.",
        "Confirm you are signed in with the correct MOD account.",
    ),
    ChecksumImportDecisionStatus.AMBIGUOUS_MATCH: (
        409,
        "document_source_ambiguous_match",
        "Multiple accessible source documents matched this PDF.",
        "Import by reference identifier when selection is available.",
    ),
    ChecksumImportDecisionStatus.NO_CONVERTED_TEXT: (
        409,
        "document_source_no_converted_text",
        "The source document does not have converted Markdown available.",
        "Try again after the document source has converted text for this paper.",
    ),
    ChecksumImportDecisionStatus.CONVERSION_RUNNING: (
        409,
        "document_source_conversion_running",
        "The source document conversion is still running.",
        "Try again after conversion completes.",
    ),
    ChecksumImportDecisionStatus.CONVERSION_FAILED: (
        422,
        "document_source_conversion_failed",
        "The source document conversion failed.",
        "Contact support with the uploaded paper details.",
    ),
    ChecksumImportDecisionStatus.READY: (
        503,
        "document_source_ready_import_unavailable",
        "The source document has converted Markdown, but upload import is not fully enabled yet.",
        "Try again after provider Markdown upload import is enabled.",
    ),
}


@dataclass(frozen=True)
class ProviderChecksumImportPlan:
    """Ready provider-backed import selected during upload intake."""

    provider: str
    checksum: str
    source_artifact: SourceArtifact
    converted_artifact: SourceArtifact | None
    source_provenance: Dict[str, Any]
    curator_token: str = field(repr=False)
    wait_for_conversion: bool = False
    figure_metadata_artifact_ids: tuple[str, ...] = ()


class UploadIntakeValidationError(ValueError):
    """Raised when an upload request fails validation checks."""

    def __init__(self, message: str, *, client_detail: object | None = None):
        super().__init__(message)
        self.client_detail = client_detail


class UploadIntakeDuplicateError(Exception):
    """Raised when upload intake detects a duplicate document."""

    def __init__(self, detail: Dict[str, Any]):
        super().__init__("duplicate upload")
        self.detail = detail


class UploadIntakeProviderDecisionError(Exception):
    """Raised when provider-backed upload intake cannot continue locally."""

    def __init__(self, *, status_code: int, detail: Dict[str, Any]):
        super().__init__(str(detail.get("message") or "provider import unavailable"))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class UploadIntakeResult:
    """Intake output contract consumed by the HTTP endpoint layer."""

    document_id: str
    job_id: str
    user_id: int
    filename: str
    status: str
    upload_timestamp: datetime
    processing_started_at: Optional[datetime]
    processing_completed_at: Optional[datetime]
    file_size_bytes: int
    weaviate_tenant: str
    chunk_count: Optional[int]
    error_message: Optional[str]


def external_document_source_import_enabled() -> bool:
    """Return whether upload intake should use an external source provider."""

    return (
        get_document_source_import_enabled()
        and get_document_source_provider().strip().lower() != LOCAL_PDF_PROVIDER_ID
    )


def _enum_or_string(value: object) -> str | None:
    if isinstance(value, Enum):
        return str(value.value)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


class UploadIntakeService:
    """Orchestrate upload intake from file receipt through durable job dispatch."""

    def __init__(
        self,
        *,
        upload_execution_service: UploadExecutionService,
        session_factory: Callable[[], Session] = SessionLocal,
        storage_path_provider: Callable[[], Path] = get_pdf_storage_path,
        upload_handler_factory: Optional[Callable[[Path], PDFUploadHandler]] = None,
        principal_from_claims_fn: Callable[[Dict[str, Any]], Any] = principal_from_claims,
        provision_user_fn: Callable[[Session, Any], Any] = provision_user,
        create_document_fn: Callable[[str, Any], Any] = create_document,
        get_document_fn: Callable[[str, str], Any] = get_document,
        delete_document_fn: Callable[[str, str], Any] = delete_document,
        create_job_fn: Callable[..., Any] = pdf_job_service.create_job,
        tenant_name_resolver: Callable[[str], str] = get_tenant_name,
        cleanup_document_dependencies_fn: Callable[[Session, uuid.UUID], Any] = cleanup_document_curation_dependencies,
        document_source_import_enabled_fn: Callable[[], bool] = external_document_source_import_enabled,
        document_source_provider_factory: Callable[[], DocumentSourceProvider] = get_configured_document_source_provider,
        checksum_import_selector: Callable[
            ...,
            Awaitable[ChecksumImportDecision],
        ] = select_checksum_import_candidate,
        find_existing_source_document_fn: Callable[..., Any] = find_existing_document_by_source,
        max_page_count_provider: Callable[[], int] = get_pdf_upload_max_page_count,
    ) -> None:
        self.upload_execution_service = upload_execution_service
        self._session_factory = session_factory
        self._storage_path_provider = storage_path_provider
        self._upload_handler_factory = upload_handler_factory or self._default_upload_handler_factory
        self._principal_from_claims = principal_from_claims_fn
        self._provision_user = provision_user_fn
        self._create_document = create_document_fn
        self._get_document = get_document_fn
        self._delete_document = delete_document_fn
        self._create_job = create_job_fn
        self._tenant_name_resolver = tenant_name_resolver
        self._cleanup_document_dependencies = cleanup_document_dependencies_fn
        self._document_source_import_enabled = document_source_import_enabled_fn
        self._document_source_provider_factory = document_source_provider_factory
        self._checksum_import_selector = checksum_import_selector
        self._find_existing_source_document = find_existing_source_document_fn
        self._max_page_count_provider = max_page_count_provider

    @staticmethod
    def _default_upload_handler_factory(storage_path: Path) -> PDFUploadHandler:
        return PDFUploadHandler(storage_path=storage_path)

    async def intake_upload(
        self,
        *,
        background_tasks: BackgroundTasks,
        file: UploadFile,
        user: Dict[str, Any],
        document_source_context: DocumentSourceRequestContext | None = None,
    ) -> UploadIntakeResult:
        """Execute upload intake choreography and return response-ready output."""
        self._validate_pdf_filename(file.filename)

        user_sub = user["sub"]
        base_storage = self._storage_path_provider()
        user_storage_path = ensure_writable_directory(base_storage / user_sub)

        upload_handler = self._upload_handler_factory(user_storage_path)
        try:
            saved_path, document = await upload_handler.save_uploaded_pdf(file)
        except UploadError as upload_error:
            raise UploadIntakeValidationError(str(upload_error)) from upload_error
        file_size_bytes = saved_path.stat().st_size
        self._validate_saved_pdf_size(saved_path, file_size_bytes)
        page_count = document.metadata.page_count
        self._validate_pdf_page_count(
            saved_path,
            page_count=page_count,
            max_page_count=self._max_page_count_provider(),
        )

        session = self._session_factory()
        db_user = None
        weaviate_document_created = False

        try:
            db_user = self._provision_user(session, self._principal_from_claims(user))

            raw_checksum = document.metadata.checksum
            scoped_file_hash = hashlib.sha256(f"{db_user.id}:{raw_checksum}".encode("utf-8")).hexdigest()

            existing = (
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

            if existing:
                existing = await self._resolve_phantom_duplicate(
                    session=session,
                    existing=existing,
                    user_sub=user_sub,
                )

            if existing:
                self._cleanup_saved_file_artifacts(saved_path)
                raise UploadIntakeDuplicateError(self._duplicate_detail(existing))

            provider_import_plan: ProviderChecksumImportPlan | None = None
            if self._document_source_import_enabled():
                provider_checksum = await generate_checksum(saved_path, algorithm="md5")
                provider_import_plan = await self._select_provider_checksum_import_or_raise(
                    checksum=provider_checksum,
                    document_source_context=document_source_context,
                    saved_path=saved_path,
                    session=session,
                    owner_user_id=db_user.id,
                )
                if provider_import_plan is not None:
                    document.source_provenance = provider_import_plan.source_provenance

            await self._create_document(user_sub, document)
            weaviate_document_created = True

            if provider_import_plan is not None:
                relative_path = saved_path.relative_to(base_storage)
                relative_path_string = str(relative_path).replace("\\", "/")
                viewer_mode = ViewerMode.LOCAL_PDF.value
                source_provenance = provider_import_plan.source_provenance
            else:
                relative_path = saved_path.relative_to(base_storage)
                relative_path_string = str(relative_path).replace("\\", "/")
                viewer_mode = None
                source_provenance = {}

            record = ViewerPDFDocument(
                id=uuid.UUID(str(document.id)),
                filename=document.filename,
                file_path=relative_path_string,
                file_hash=scoped_file_hash,
                file_size=file_size_bytes,
                page_count=page_count,
                user_id=db_user.id,
                viewer_mode=viewer_mode,
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
                    "pending" if provider_import_plan is not None else None
                ),
                source_access_scope=source_provenance.get("access_scope"),
                source_access_mods=source_provenance.get("access_mods"),
            )
            session.add(record)
            session.commit()

        except UploadIntakeDuplicateError:
            raise
        except UploadIntakeProviderDecisionError:
            raise
        except IntegrityError as integrity_error:
            session.rollback()
            logger.warning(
                "Integrity error persisting PDF metadata for document %s (user_id=%s): %s",
                document.id,
                getattr(db_user, "id", "unknown"),
                integrity_error,
            )
            await self._compensate_persistence_failure(
                user_sub=user_sub,
                document_id=str(document.id),
                saved_path=saved_path,
                weaviate_document_created=weaviate_document_created,
            )
            constraint_name = self._extract_integrity_constraint_name(integrity_error)
            if constraint_name == "ck_pdf_documents_file_size":
                raise UploadIntakeValidationError(
                    pdf_file_size_limit_message(file_size_bytes)
                ) from integrity_error
            if self._is_duplicate_integrity_error(integrity_error, constraint_name):
                existing_after_conflict = (
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
                if existing_after_conflict:
                    raise UploadIntakeDuplicateError(
                        self._duplicate_detail(existing_after_conflict)
                    ) from integrity_error
                raise UploadIntakeDuplicateError(
                    {
                        "error": "duplicate_file",
                        "message": "This file appears to have already been uploaded for your account.",
                        "suggestion": "Refresh Documents and search by filename to load the existing document instead of deleting and re-uploading it.",
                    }
                ) from integrity_error
            raise
        except Exception:
            session.rollback()
            await self._compensate_persistence_failure(
                user_sub=user_sub,
                document_id=str(document.id),
                saved_path=saved_path,
                weaviate_document_created=weaviate_document_created,
            )
            raise
        finally:
            session.close()

        try:
            job = self._create_job(
                document_id=document.id,
                user_id=db_user.id,
                filename=document.filename,
            )
        except Exception:
            await self._compensate_job_creation_failure(
                user_sub=user_sub,
                document_id=str(document.id),
                saved_path=saved_path,
                weaviate_document_created=weaviate_document_created,
            )
            raise
        job_id = job.job_id

        if provider_import_plan is not None and provider_import_plan.converted_artifact is not None:
            provider_execution_request = ProviderMarkdownExecutionRequest(
                document_id=document.id,
                job_id=job_id,
                user_id=user_sub,
                owner_user_id=db_user.id,
                filename=document.filename,
                converted_artifact_id=provider_import_plan.converted_artifact.artifact_id,
                curator_token=provider_import_plan.curator_token,
                source_provenance=provider_import_plan.source_provenance,
                figure_metadata_artifact_ids=provider_import_plan.figure_metadata_artifact_ids,
            )
            await self.upload_execution_service.dispatch_provider_markdown_execution(
                background_tasks=background_tasks,
                request=provider_execution_request,
            )
        elif provider_import_plan is not None and provider_import_plan.wait_for_conversion:
            await self.upload_execution_service.dispatch_provider_conversion_execution(
                background_tasks=background_tasks,
                request=ProviderConversionExecutionRequest(
                    document_id=document.id,
                    job_id=job_id,
                    user_id=user_sub,
                    owner_user_id=db_user.id,
                    filename=document.filename,
                    reference=_provider_reference_from_provenance(
                        provider_import_plan.source_provenance
                    ),
                    source_artifact_id=provider_import_plan.source_artifact.artifact_id,
                    curator_token=provider_import_plan.curator_token,
                    source_provenance=provider_import_plan.source_provenance,
                    figure_metadata_artifact_ids=provider_import_plan.figure_metadata_artifact_ids,
                ),
            )
        else:
            execution_request = UploadExecutionRequest(
                document_id=document.id,
                job_id=job_id,
                user_id=user_sub,
                file_path=saved_path,
            )
            await self.upload_execution_service.dispatch_upload_execution(
                background_tasks=background_tasks,
                request=execution_request,
            )

        return UploadIntakeResult(
            document_id=document.id,
            job_id=job_id,
            user_id=db_user.id,
            filename=document.filename,
            status="PENDING",
            upload_timestamp=datetime.now(timezone.utc),
            processing_started_at=None,
            processing_completed_at=None,
            file_size_bytes=file_size_bytes,
            weaviate_tenant=self._tenant_name_resolver(user_sub),
            chunk_count=None,
            error_message=None,
        )

    async def _select_provider_checksum_import_or_raise(
        self,
        *,
        checksum: str,
        document_source_context: DocumentSourceRequestContext | None,
        saved_path: Path,
        session: Session,
        owner_user_id: int,
    ) -> ProviderChecksumImportPlan | None:
        if document_source_context is None:
            self._cleanup_saved_file_artifacts(saved_path)
            raise UploadIntakeProviderDecisionError(
                status_code=503,
                detail={
                    "error": "document_source_context_unavailable",
                    "message": (
                        "Document-source import is enabled, but request authorization "
                        "context is unavailable."
                    ),
                    "suggestion": "Refresh the page and try the upload again.",
                },
            )
        curator_token = (document_source_context.curator_token or "").strip()

        provider: DocumentSourceProvider | None = None
        try:
            provider = self._document_source_provider_factory()
            decision = await self._checksum_import_selector(
                provider=provider,
                checksum=checksum,
                authorized_group_ids=document_source_context.authorized_group_ids,
                request_bearer_token=curator_token or None,
                allow_conversion_request=bool(curator_token),
            )
        except (DocumentSourceConfigError, DocumentSourceError) as exc:
            self._cleanup_saved_file_artifacts(saved_path)
            raise UploadIntakeProviderDecisionError(
                status_code=503,
                detail={
                    "error": "document_source_unavailable",
                    "message": "Document-source lookup is unavailable.",
                    "suggestion": "Try again later or contact support if this persists.",
                },
            ) from exc
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception as cleanup_err:
                    logger.warning(
                        "Best-effort document-source provider cleanup failed: %s",
                        cleanup_err,
                    )

        if decision.status is ChecksumImportDecisionStatus.NO_MATCH:
            logger.info(
                "Document-source checksum lookup found no match for uploaded PDF; "
                "continuing with local PDF processing."
            )
            return None

        wait_for_conversion = (
            decision.provider == "abc_literature"
            and decision.status is ChecksumImportDecisionStatus.CONVERSION_RUNNING
            and decision.selected is not None
        )
        if not decision.is_ready and not wait_for_conversion:
            self._cleanup_saved_file_artifacts(saved_path)
            raise self._provider_decision_error(decision)
        if decision.selected is None:
            self._cleanup_saved_file_artifacts(saved_path)
            raise self._provider_decision_error(decision)
        if (
            decision.provider == "abc_literature"
            and decision.selected.converted_artifact is None
            and not wait_for_conversion
        ):
            self._cleanup_saved_file_artifacts(saved_path)
            raise UploadIntakeProviderDecisionError(
                status_code=409,
                detail={
                    "error": "document_source_no_converted_text",
                    "message": (
                        "The ABC Literature match does not have converted Markdown "
                        "available for import."
                    ),
                    "provider": decision.provider,
                    "status": decision.status.value,
                    "suggestion": (
                        "Try again after ABC Literature conversion completes for this paper."
                    ),
                },
            )
        if (
            (decision.selected.converted_artifact is not None or wait_for_conversion)
            and not curator_token
        ):
            self._cleanup_saved_file_artifacts(saved_path)
            raise UploadIntakeProviderDecisionError(
                status_code=503,
                detail={
                    "error": "document_source_curator_token_unavailable",
                    "message": (
                        "Document-source import requires a request curator token for "
                        "provider conversion or download."
                    ),
                    "provider": decision.provider,
                    "status": decision.status.value,
                    "suggestion": "Refresh the page and try the upload again.",
                },
            )

        try:
            source_provenance = self._build_provider_source_provenance(
                provider=decision.provider,
                checksum=decision.checksum,
                selected=decision.selected,
            )
        except UploadIntakeProviderDecisionError:
            self._cleanup_saved_file_artifacts(saved_path)
            raise
        existing_source_document = self._find_existing_source_document(
            session,
            user_id=owner_user_id,
            source_provider=decision.provider,
            reference_id=source_provenance.get("reference_id"),
            reference_curie=source_provenance.get("reference_curie"),
            converted_artifact_id=source_provenance.get("converted_artifact_id"),
            source_md5=source_provenance.get("source_md5"),
        )
        if existing_source_document:
            self._cleanup_saved_file_artifacts(saved_path)
            raise UploadIntakeDuplicateError(
                {
                    "error": "duplicate_file",
                    "message": (
                        "This provider document has already been imported on "
                        f"{existing_source_document.upload_timestamp.strftime('%B %d, %Y at %I:%M %p')}"
                    ),
                    "existing_document_id": str(existing_source_document.id),
                    "uploaded_at": existing_source_document.upload_timestamp.isoformat(),
                    "suggestion": (
                        "If you want to re-process this file, delete the existing document "
                        "first and then upload again."
                    ),
                }
            )

        return ProviderChecksumImportPlan(
            provider=decision.provider,
            checksum=decision.checksum,
            source_artifact=decision.selected.source_artifact,
            converted_artifact=decision.selected.converted_artifact,
            source_provenance=source_provenance,
            curator_token=curator_token,
            wait_for_conversion=wait_for_conversion,
            figure_metadata_artifact_ids=tuple(
                artifact.artifact_id
                for artifact in decision.selected.provider_metadata_artifacts
            ),
        )

    @staticmethod
    def _build_provider_source_provenance(
        *,
        provider: str,
        checksum: str,
        selected: ChecksumImportCandidate,
    ) -> Dict[str, Any]:
        source_artifact = selected.source_artifact
        converted_artifact = selected.converted_artifact
        raw_provenance = {
            "provider": provider,
            "reference_id": source_artifact.reference_id
            or (converted_artifact.reference_id if converted_artifact is not None else None),
            "reference_curie": source_artifact.reference_curie
            or (converted_artifact.reference_curie if converted_artifact is not None else None),
            "source_file_id": source_artifact.metadata.get("source_file_id")
            or source_artifact.artifact_id,
            "pdf_artifact_id": source_artifact.artifact_id,
            "source_md5": source_artifact.md5sum or checksum,
            "file_class": source_artifact.metadata.get("file_class")
            or _enum_or_string(source_artifact.role),
            "file_extension": source_artifact.metadata.get("file_extension")
            or _enum_or_string(source_artifact.artifact_format),
            "artifact_status": _enum_or_string(source_artifact.status),
            "access_scope": _enum_or_string(source_artifact.access_policy.scope),
            "access_mods": {"mods": list(source_artifact.access_policy.mods)},
            "viewer_mode": ViewerMode.LOCAL_PDF.value,
        }
        if converted_artifact is not None:
            raw_provenance.update(
                {
                    "converted_artifact_id": converted_artifact.artifact_id,
                    "file_class": converted_artifact.metadata.get("file_class")
                    or _enum_or_string(converted_artifact.role),
                    "file_extension": converted_artifact.metadata.get("file_extension")
                    or _enum_or_string(converted_artifact.artifact_format),
                    "artifact_status": _enum_or_string(converted_artifact.status),
                }
            )
        external_ids = source_artifact.metadata.get("external_ids")
        if external_ids:
            raw_provenance["external_ids"] = external_ids
        sanitized = sanitize_document_source_provenance(raw_provenance)
        if not sanitized:
            raise UploadIntakeProviderDecisionError(
                status_code=503,
                detail={
                    "error": "document_source_provenance_unavailable",
                    "message": "Document-source import could not build safe source provenance.",
                    "provider": provider,
                    "status": ChecksumImportDecisionStatus.READY.value,
                    "suggestion": "Try again later or contact support if this persists.",
                },
            )
        return sanitized

    @staticmethod
    def _provider_decision_error(
        decision: ChecksumImportDecision,
    ) -> UploadIntakeProviderDecisionError:
        status_code, error, message, suggestion = _provider_decision_error_fields(
            decision.status
        )
        detail: Dict[str, Any] = {
            "error": error,
            "message": message,
            "provider": decision.provider,
            "status": decision.status.value,
            "suggestion": suggestion,
        }
        if decision.status is ChecksumImportDecisionStatus.AMBIGUOUS_MATCH:
            detail["match_count"] = decision.metadata.get("match_count")
        return UploadIntakeProviderDecisionError(
            status_code=status_code,
            detail=detail,
        )

    async def _compensate_job_creation_failure(
        self,
        *,
        user_sub: str,
        document_id: str,
        saved_path: Path,
        weaviate_document_created: bool,
    ) -> None:
        cleanup_session = self._session_factory()
        try:
            persisted = (
                cleanup_session.execute(
                    select(ViewerPDFDocument).where(
                        ViewerPDFDocument.id == uuid.UUID(document_id),
                    )
                )
                .scalars()
                .first()
            )
            if persisted:
                cleanup_session.delete(persisted)
                cleanup_session.commit()
        except Exception as cleanup_err:
            cleanup_session.rollback()
            logger.warning(
                "Best-effort SQL cleanup failed after durable job creation error for document %s: %s",
                document_id,
                cleanup_err,
            )
        finally:
            cleanup_session.close()

        await self._compensate_persistence_failure(
            user_sub=user_sub,
            document_id=document_id,
            saved_path=saved_path,
            weaviate_document_created=weaviate_document_created,
        )

    async def _resolve_phantom_duplicate(
        self,
        *,
        session: Session,
        existing: ViewerPDFDocument,
        user_sub: str,
    ) -> Optional[ViewerPDFDocument]:
        try:
            existing_weaviate_doc = await self._get_document(user_sub, str(existing.id))
            if existing_weaviate_doc:
                return existing

            logger.warning(
                "Phantom document detected (hash match in PG, missing in Weaviate): %s. Cleaning up old record.",
                existing.id,
            )
            self._cleanup_document_dependencies(session, existing.id)
            session.delete(existing)
            session.commit()
            return None
        except ValueError as not_found_err:
            logger.warning(
                "Phantom document detected (ValueError - not in Weaviate): %s. Cleaning up: %s",
                existing.id,
                not_found_err,
            )
            self._cleanup_document_dependencies(session, existing.id)
            session.delete(existing)
            session.commit()
            return None
        except Exception as check_err:
            logger.error("Error checking phantom status: %s", check_err)
            return existing

    async def _compensate_persistence_failure(
        self,
        *,
        user_sub: str,
        document_id: str,
        saved_path: Path,
        weaviate_document_created: bool,
    ) -> None:
        """Best-effort compensation for partial persistence failures."""
        if weaviate_document_created:
            try:
                await self._delete_document(user_sub, document_id)
            except Exception as cleanup_err:
                logger.warning(
                    "Best-effort Weaviate cleanup failed for document %s: %s",
                    document_id,
                    cleanup_err,
                )

        self._cleanup_saved_file_artifacts(saved_path)

    @staticmethod
    def _cleanup_saved_file_artifacts(saved_path: Path) -> None:
        if saved_path.exists():
            shutil.rmtree(saved_path.parent, ignore_errors=True)

    @staticmethod
    def _duplicate_detail(existing: ViewerPDFDocument) -> Dict[str, Any]:
        return {
            "error": "duplicate_file",
            "message": (
                "This file has already been uploaded on "
                f"{existing.upload_timestamp.strftime('%B %d, %Y at %I:%M %p')}"
            ),
            "existing_document_id": str(existing.id),
            "existing_filename": existing.filename,
            "uploaded_at": existing.upload_timestamp.isoformat(),
            "suggestion": (
                f'The existing document is available in Documents as "{existing.filename}". '
                "Search for that filename to load it."
            ),
        }

    @staticmethod
    def _validate_pdf_filename(filename: Optional[str]) -> None:
        if not filename or not filename.lower().endswith(".pdf"):
            raise UploadIntakeValidationError(f"File must be a PDF. Got: {filename}")

    @classmethod
    def _validate_saved_pdf_size(cls, saved_path: Path, file_size_bytes: int) -> None:
        if file_size_bytes <= MAX_PDF_FILE_SIZE_BYTES:
            return

        cls._cleanup_saved_file_artifacts(saved_path)
        raise UploadIntakeValidationError(pdf_file_size_limit_message(file_size_bytes))

    @classmethod
    def _validate_pdf_page_count(
        cls,
        saved_path: Path,
        *,
        page_count: int,
        max_page_count: int,
    ) -> None:
        if 1 <= page_count <= max_page_count:
            return

        cls._cleanup_saved_file_artifacts(saved_path)
        message = (
            f"PDF page count ({page_count}) exceeds the configured maximum "
            f"({max_page_count})."
        )
        raise UploadIntakeValidationError(
            message,
            client_detail={
                "error": "pdf_page_count_exceeded",
                "message": message,
                "actual_page_count": page_count,
                "max_page_count": max_page_count,
            },
        )

    @staticmethod
    def _extract_integrity_constraint_name(error: IntegrityError) -> Optional[str]:
        diag = getattr(getattr(error, "orig", None), "diag", None)
        constraint_name = getattr(diag, "constraint_name", None)
        if isinstance(constraint_name, str) and constraint_name.strip():
            return constraint_name

        error_text = " ".join(
            str(part)
            for part in (error, getattr(error, "orig", None))
            if part is not None
        )
        for known_name in ("ck_pdf_documents_file_size", *_PDF_DOCUMENT_DUPLICATE_CONSTRAINTS):
            if known_name in error_text:
                return known_name
        return None

    @staticmethod
    def _is_duplicate_integrity_error(
        error: IntegrityError,
        constraint_name: Optional[str],
    ) -> bool:
        if constraint_name in _PDF_DOCUMENT_DUPLICATE_CONSTRAINTS:
            return True

        error_text = " ".join(
            str(part).lower()
            for part in (error, getattr(error, "orig", None))
            if part is not None
        )
        return "duplicate key" in error_text or "unique constraint" in error_text


def _provider_decision_error_fields(
    status: ChecksumImportDecisionStatus,
) -> tuple[int, str, str, str]:
    return _CHECKSUM_DECISION_ERROR_FIELDS[status]


def _provider_reference_from_provenance(source_provenance: Dict[str, Any]) -> str:
    reference = (
        source_provenance.get("reference_curie")
        or source_provenance.get("reference_id")
        or ""
    )
    normalized = str(reference).strip()
    if not normalized:
        raise UploadIntakeProviderDecisionError(
            status_code=503,
            detail={
                "error": "document_source_reference_unavailable",
                "message": "Document-source conversion could not determine a reference to poll.",
                "suggestion": "Try again later or contact support if this persists.",
            },
        )
    return normalized
