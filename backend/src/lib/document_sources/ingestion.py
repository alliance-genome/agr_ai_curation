"""Provider-normalized Markdown ingestion into the local document pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from src.lib.document_sources.models import DocumentSourceError
from src.lib.document_sources.provenance import sanitize_document_source_provenance
from src.lib.pipeline.orchestrator import ProcessingResult
from src.lib.storage_permissions import ensure_writable_directory
from src.models.pipeline import ProcessingStage
from src.models.strategy import ChunkingStrategy

logger = logging.getLogger(__name__)


class DocumentSourceIngestionError(DocumentSourceError):
    """Raised when provider-backed Markdown cannot be ingested safely."""


class DocumentSourceMarkdownValidationError(DocumentSourceIngestionError):
    """Raised when provider-backed Markdown fails validation."""


@dataclass(frozen=True, slots=True)
class ProviderMarkdownIngestionRequest:
    """Input for ingesting downloaded provider Markdown into AI Curation."""

    document_id: str
    user_id: str
    document_owner_user_id: int
    markdown: str
    source_provenance: Mapping[str, Any]
    filename: str | None = None
    strategy: ChunkingStrategy | None = None
    viewer_mode: str = "local_pdf"


@dataclass(frozen=True, slots=True)
class ProviderMarkdownIngestionResult:
    """Result of provider Markdown ingestion."""

    processing_result: ProcessingResult
    element_count: int
    chunk_count: int
    source_markdown_path: str
    processed_json_path: str
    validation_warnings: list[str] = field(default_factory=list)


async def ingest_provider_markdown_document(
    request: ProviderMarkdownIngestionRequest,
    *,
    weaviate_client: Any,
) -> ProviderMarkdownIngestionResult:
    """Validate and ingest provider Markdown without invoking PDFX."""

    started_at = datetime.now(timezone.utc)
    stages_completed: list[ProcessingStage] = []
    strategy = request.strategy or ChunkingStrategy.get_research_strategy()
    document_id = _require_non_empty("document_id", request.document_id)
    user_id = _require_non_empty("user_id", request.user_id)
    owner_user_id = _require_positive_int(
        "document_owner_user_id", request.document_owner_user_id
    )
    markdown = _require_non_empty_preserving_text("markdown", request.markdown)
    source_provenance = _require_ingestable_provenance(request.source_provenance)

    try:
        await _require_owned_document(document_id, user_id, owner_user_id)
        await _sync_sql_document_status(
            document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
            status="processing",
        )
        warnings = _validate_provider_markdown(markdown)
        element_markdown = _strip_markdown_image_assets(markdown)

        from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements

        elements = markdown_to_pipeline_elements(element_markdown)
        if not elements:
            raise DocumentSourceMarkdownValidationError(
                "Provider Markdown produced no usable pipeline elements"
            )
        stages_completed.append(ProcessingStage.PARSING)

        source_markdown_path = await _save_source_markdown(
            markdown=markdown,
            document_id=document_id,
            user_id=user_id,
        )
        processed_json_path = await _save_processed_json(
            elements=elements,
            document_id=document_id,
            user_id=user_id,
        )
        await _persist_ingestion_metadata(
            document_id=document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
            source_provenance=source_provenance,
            source_markdown_path=source_markdown_path,
            processed_json_path=processed_json_path,
            viewer_mode=request.viewer_mode,
            filename=request.filename,
        )

        try:
            from src.lib.pipeline.hierarchy_resolution import resolve_document_hierarchy

            elements, hierarchy_metadata = await resolve_document_hierarchy(elements)
            if hierarchy_metadata:
                await _store_hierarchy_metadata(
                    document_id,
                    user_id,
                    owner_user_id,
                    hierarchy_metadata,
                )
        except Exception as exc:
            logger.warning(
                "Provider Markdown hierarchy resolution failed for %s; continuing flat: %s",
                document_id,
                exc,
            )

        await _sync_sql_document_status(
            document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
            status="processing",
        )
        from src.lib.pipeline.chunk import chunk_parsed_document

        chunks = await chunk_parsed_document(elements, strategy, document_id)
        stages_completed.append(ProcessingStage.CHUNKING)

        await _sync_sql_document_status(
            document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
            status="processing",
        )
        from src.lib.pipeline.store import store_to_weaviate

        await store_to_weaviate(chunks, document_id, weaviate_client, user_id)
        stages_completed.append(ProcessingStage.STORING)

        await _sync_sql_document_status(
            document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
            status="completed",
        )
        duration_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
        processing_result = ProcessingResult(
            success=True,
            document_id=document_id,
            stages_completed=stages_completed,
            total_chunks=len(chunks),
            total_embeddings=len(chunks),
            duration_seconds=duration_seconds,
        )
        return ProviderMarkdownIngestionResult(
            processing_result=processing_result,
            element_count=len(elements),
            chunk_count=len(chunks),
            source_markdown_path=source_markdown_path,
            processed_json_path=processed_json_path,
            validation_warnings=warnings,
        )

    except Exception as exc:
        await _sync_sql_document_status(
            document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
            status="failed",
            error_message=_safe_error_message(exc),
        )
        if isinstance(exc, DocumentSourceIngestionError):
            raise
        raise DocumentSourceIngestionError("Provider Markdown ingestion failed") from exc


def _validate_provider_markdown(markdown: str) -> list[str]:
    try:
        from agr_abc_document_parsers import validate_markdown
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise DocumentSourceMarkdownValidationError(
            "Markdown validator package is unavailable"
        ) from exc

    result = validate_markdown(markdown)
    errors = getattr(result, "errors", None) or []
    if errors:
        raise DocumentSourceMarkdownValidationError(
            "Provider Markdown failed schema validation"
        )
    warnings = getattr(result, "warnings", None) or []
    return [_validation_issue_message(warning) for warning in warnings]


def _strip_markdown_image_assets(markdown: str) -> str:
    """Drop image URLs while keeping alt text available to chunking."""

    return re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", markdown)


def _validation_issue_message(issue: object) -> str:
    rule_id = getattr(issue, "rule_id", None)
    message = getattr(issue, "message", None)
    line = getattr(issue, "line", None)
    parts = []
    if rule_id:
        parts.append(str(rule_id))
    if line:
        parts.append(f"line {line}")
    if message:
        parts.append(str(message))
    return ": ".join(parts) if parts else str(issue)


def _require_ingestable_provenance(raw: Mapping[str, Any]) -> dict[str, Any]:
    provenance = sanitize_document_source_provenance(raw)
    if not provenance:
        raise DocumentSourceIngestionError(
            "Provider Markdown ingestion requires source provenance"
        )
    access_scope = str(provenance.get("access_scope") or "").strip().lower()
    if not access_scope:
        raise DocumentSourceIngestionError(
            "Provider Markdown ingestion requires source access scope"
        )
    if access_scope not in {"global", "restricted"}:
        raise DocumentSourceIngestionError(
            "Provider Markdown ingestion requires source access scope to be global or restricted"
        )
    provenance["access_scope"] = access_scope
    if access_scope == "restricted":
        mods = (provenance.get("access_mods") or {}).get("mods") or []
        if not mods:
            raise DocumentSourceIngestionError(
                "Restricted provider Markdown ingestion requires source access MODs"
            )
    return provenance


async def _save_source_markdown(
    *,
    markdown: str,
    document_id: str,
    user_id: str,
) -> str:
    from src.config import get_pdf_storage_path

    pdf_storage = get_pdf_storage_path()
    output_dir = ensure_writable_directory(Path(pdf_storage) / user_id / "source_markdown")
    file_path = output_dir / f"{document_id}.md"
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: file_path.write_text(markdown, encoding="utf-8"),
    )
    return str(file_path.relative_to(pdf_storage))


async def _save_processed_json(
    *,
    elements: list[dict[str, Any]],
    document_id: str,
    user_id: str,
) -> str:
    from src.config import get_pdf_storage_path

    pdf_storage = get_pdf_storage_path()
    output_dir = ensure_writable_directory(Path(pdf_storage) / user_id / "processed_json")
    file_path = output_dir / f"{document_id}.json"
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: file_path.write_text(json.dumps(elements, indent=2), encoding="utf-8"),
    )
    return str(file_path.relative_to(pdf_storage))


async def _persist_ingestion_metadata(
    *,
    document_id: str,
    user_id: str,
    owner_user_id: int,
    source_provenance: Mapping[str, Any],
    source_markdown_path: str,
    processed_json_path: str,
    viewer_mode: str,
    filename: str | None,
) -> None:
    from src.models.sql.database import SessionLocal
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    session = SessionLocal()
    try:
        document = _query_owned_document(
            session,
            PDFDocument,
            User,
            document_id=document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
        )
        if not document:
            raise DocumentSourceIngestionError(
                "Document row not found or user mismatch for provider ingestion"
            )

        if filename:
            document.filename = filename
        document.processed_json_path = processed_json_path
        document.source_markdown_path = source_markdown_path
        document.viewer_mode = viewer_mode
        document.source_provider = source_provenance.get("provider")
        document.source_provider_reference_id = source_provenance.get("reference_id")
        document.source_provider_reference_curie = source_provenance.get("reference_curie")
        document.source_provider_source_file_id = source_provenance.get("source_file_id")
        document.source_provider_pdf_artifact_id = source_provenance.get("pdf_artifact_id")
        document.source_provider_converted_artifact_id = source_provenance.get(
            "converted_artifact_id"
        )
        document.source_external_ids = source_provenance.get("external_ids")
        document.source_md5 = source_provenance.get("source_md5")
        document.source_file_class = source_provenance.get("file_class")
        document.source_file_extension = source_provenance.get("file_extension")
        document.source_artifact_status = source_provenance.get("artifact_status")
        document.source_import_status = "processing"
        document.source_imported_at = datetime.now(timezone.utc)
        document.source_access_scope = source_provenance.get("access_scope")
        document.source_access_mods = source_provenance.get("access_mods")
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def _store_hierarchy_metadata(
    document_id: str,
    user_id: str,
    owner_user_id: int,
    hierarchy_metadata: Any,
) -> None:
    from src.models.sql.database import SessionLocal
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    session = SessionLocal()
    try:
        document = _query_owned_document(
            session,
            PDFDocument,
            User,
            document_id=document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
        )
        if document:
            document.hierarchy_metadata = hierarchy_metadata.model_dump()
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def _sync_sql_document_status(
    document_id: str,
    *,
    user_id: str,
    owner_user_id: int,
    status: str,
    error_message: str | None = None,
) -> None:
    from src.models.sql.database import SessionLocal
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    now = datetime.now(timezone.utc)
    session = SessionLocal()
    try:
        document = _query_owned_document(
            session,
            PDFDocument,
            User,
            document_id=document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
        )
        if not document:
            return

        document.status = status
        document.source_import_status = status
        if status == "processing":
            if document.processing_started_at is None:
                document.processing_started_at = now
            document.processing_completed_at = None
            document.error_message = None
        elif status == "completed":
            if document.processing_started_at is None:
                document.processing_started_at = now
            document.processing_completed_at = now
            document.error_message = None
        elif status == "failed":
            if document.processing_started_at is None:
                document.processing_started_at = now
            document.processing_completed_at = now
            document.error_message = (error_message or "Provider Markdown ingestion failed")[:1000]
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def _require_owned_document(
    document_id: str,
    user_id: str,
    owner_user_id: int,
) -> None:
    from src.models.sql.database import SessionLocal
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    session = SessionLocal()
    try:
        document = _query_owned_document(
            session,
            PDFDocument,
            User,
            document_id=document_id,
            user_id=user_id,
            owner_user_id=owner_user_id,
        )
        if not document:
            raise DocumentSourceIngestionError(
                "Document row not found or user mismatch for provider ingestion"
            )
    finally:
        session.close()


def _query_owned_document(
    session: Any,
    document_model: Any,
    user_model: Any,
    *,
    document_id: str,
    user_id: str,
    owner_user_id: int,
) -> Any:
    return (
        session.query(document_model)
        .join(user_model, document_model.user_id == user_model.id)
        .filter(
            document_model.id == UUID(document_id),
            document_model.user_id == owner_user_id,
            user_model.auth_sub == user_id,
            user_model.is_active.is_(True),
        )
        .first()
    )


def _require_non_empty(field_name: str, value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise DocumentSourceIngestionError(f"{field_name} is required")
    return value


def _require_non_empty_preserving_text(field_name: str, value: str) -> str:
    raw_value = value or ""
    if not raw_value.strip():
        raise DocumentSourceIngestionError(f"{field_name} is required")
    return raw_value


def _require_positive_int(field_name: str, value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DocumentSourceIngestionError(f"{field_name} is required") from exc
    if parsed <= 0:
        raise DocumentSourceIngestionError(f"{field_name} is required")
    return parsed


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, DocumentSourceMarkdownValidationError):
        return str(exc)
    if isinstance(exc, DocumentSourceIngestionError):
        return str(exc)
    return "Provider Markdown ingestion failed"
