"""Span-backed evidence-recording tool for document extraction agents."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from agents import function_tool

from src.lib.openai_agents.evidence_spans import (
    EvidenceSpan,
    EvidenceSpanResolutionError,
    parse_evidence_span_id,
    resolve_evidence_span_id,
)
from src.lib.openai_agents.evidence_summary import build_evidence_record_id
from src.lib.weaviate_client.chunks import get_chunk_by_id

if TYPE_CHECKING:
    from ..guardrails import ToolCallTracker


logger = logging.getLogger(__name__)

_FIGURE_REFERENCE_PATTERN = re.compile(
    r"\b(?:Fig(?:ure)?\.?\s*\d+[A-Za-z0-9-]*)\b",
    re.IGNORECASE,
)
_TABLE_REFERENCE_PATTERN = re.compile(
    r"\b(?:Table\.?\s*\d+[A-Za-z0-9-]*)\b",
    re.IGNORECASE,
)
_PREVIEW_CHARS = 300
_SPAN_RETRY_INSTRUCTIONS = (
    "Call read_chunk for the source chunk again and select current "
    "evidence_spans[].span_id values. Do not provide model-authored source text."
)


@dataclass(frozen=True)
class _ResolvedSpanFragment:
    span: EvidenceSpan
    chunk: dict[str, Any]
    chunk_id: str
    document_id: str
    page: int | None
    section: str | None
    subsection: str | None
    figure_reference: str | None

    def to_source_fragment(self) -> dict[str, Any]:
        parsed_span = parse_evidence_span_id(self.span.span_id)
        fragment: dict[str, Any] = {
            "span_id": self.span.span_id,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "text": self.span.text,
            "char_start": self.span.char_start,
            "char_end": self.span.char_end,
            "text_hash": parsed_span.text_hash,
            "span_index": self.span.span_index,
            "span_type": self.span.span_type,
            "spanizer_version": self.span.spanizer_version,
        }
        if self.page is not None:
            fragment["page"] = self.page
        if self.section:
            fragment["section"] = self.section
        if self.subsection:
            fragment["subsection"] = self.subsection
        if self.figure_reference:
            fragment["figure_reference"] = self.figure_reference
        return fragment


def _build_preview(chunk_text: str) -> str:
    stripped_text = chunk_text.strip()
    if len(stripped_text) <= _PREVIEW_CHARS:
        return stripped_text

    preview = stripped_text[:_PREVIEW_CHARS].rstrip()
    if len(preview) < len(stripped_text):
        preview = preview.rstrip(" ,;:") + "..."
    return preview


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return None


def _metadata_dict(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata = chunk.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _resolve_chunk_section(chunk: dict[str, Any]) -> str | None:
    metadata = _metadata_dict(chunk)
    return _first_non_empty(
        chunk.get("parent_section"),
        chunk.get("section_title"),
        metadata.get("parent_section"),
        metadata.get("parentSection"),
        metadata.get("section_title"),
        metadata.get("sectionTitle"),
    )


def _resolve_chunk_subsection(chunk: dict[str, Any]) -> str | None:
    metadata = _metadata_dict(chunk)
    return _first_non_empty(
        chunk.get("subsection"),
        metadata.get("subsection"),
        metadata.get("subSection"),
    )


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None

    try:
        page = int(value)
    except (TypeError, ValueError):
        return None

    return page if page > 0 else None


def _resolve_doc_item_page(item: dict[str, Any]) -> int | None:
    return (
        _coerce_positive_int(item.get("page"))
        or _coerce_positive_int(item.get("page_no"))
        or _coerce_positive_int(item.get("page_number"))
        or _coerce_positive_int(item.get("pageNumber"))
    )


def _resolve_chunk_page(chunk: dict[str, Any]) -> int | None:
    metadata = _metadata_dict(chunk)
    page = (
        _coerce_positive_int(chunk.get("page_number"))
        or _coerce_positive_int(metadata.get("page_number"))
        or _coerce_positive_int(metadata.get("pageNumber"))
    )

    raw_doc_items = chunk.get("doc_items")
    if not isinstance(raw_doc_items, list):
        raw_doc_items = metadata.get("doc_items")
    doc_items = raw_doc_items if isinstance(raw_doc_items, list) else []
    doc_item_pages = [
        resolved_page
        for item in doc_items
        if isinstance(item, dict)
        for resolved_page in [_resolve_doc_item_page(item)]
        if resolved_page is not None
    ]
    doc_item_pages = list(dict.fromkeys(doc_item_pages))

    if not doc_item_pages:
        return page

    if page is not None and page in doc_item_pages:
        return page

    return doc_item_pages[0]


def _resolve_exact_chunk_text(chunk: dict[str, Any]) -> str | None:
    text = chunk.get("text")
    return text if isinstance(text, str) else None


def _extract_figure_reference(chunk: dict[str, Any], chunk_text: str) -> str | None:
    metadata = _metadata_dict(chunk)
    candidates: list[str | None] = [
        _first_non_empty(
            metadata.get("figure_reference"),
            metadata.get("figureReference"),
        ),
    ]

    for source_text in (chunk_text, _resolve_chunk_section(chunk), _resolve_chunk_subsection(chunk)):
        if not source_text:
            continue
        candidates.extend(_FIGURE_REFERENCE_PATTERN.findall(source_text))
        candidates.extend(_TABLE_REFERENCE_PATTERN.findall(source_text))

    unique_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", str(candidate or "").strip())
        if not normalized:
            continue
        normalized_key = normalized.lower()
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        unique_candidates.append(normalized)

    # If a chunk clearly contains multiple figure/table references, avoid choosing
    # one so downstream anchors do not inherit ambiguous provenance.
    if len(unique_candidates) == 1:
        return unique_candidates[0]
    return None


def _optional_output_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _build_envelope_target_fields(
    *,
    object_id: Any = None,
    pending_ref_id: Any = None,
    object_type: Any = None,
    field_path: Any = None,
    validation_finding_id: Any = None,
) -> dict[str, Any]:
    target: dict[str, Any] = {}
    normalized_object_id = _optional_output_string(object_id)
    normalized_pending_ref_id = _optional_output_string(pending_ref_id)
    normalized_object_type = _optional_output_string(object_type)
    normalized_field_path = _optional_output_string(field_path)
    normalized_validation_finding_id = _optional_output_string(validation_finding_id)

    if normalized_object_id:
        target["object_id"] = normalized_object_id
    elif normalized_pending_ref_id:
        target["pending_ref_id"] = normalized_pending_ref_id
    if normalized_object_type:
        target["object_type"] = normalized_object_type
    if normalized_field_path:
        target["field_path"] = normalized_field_path
    if normalized_validation_finding_id:
        target["validation_finding_id"] = normalized_validation_finding_id

    return {"envelope_target": target} if target else {}


def _merge_extra_fields(*field_groups: dict[str, Any]) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    for fields in field_groups:
        merged.update(fields)
    return merged or None


def _normalize_span_ids(span_ids: Any) -> tuple[list[str], dict[str, Any] | None]:
    if not isinstance(span_ids, list):
        return [], {
            "failed_span_id": None,
            "failed_span_error": "span_ids must be a non-empty list of span ID strings",
        }

    normalized_span_ids: list[str] = []
    for index, span_id in enumerate(span_ids):
        if not isinstance(span_id, str):
            return [], {
                "failed_span_id": None,
                "failed_span_index": index,
                "failed_span_error": "span_ids must contain only strings",
            }
        normalized = span_id.strip()
        if not normalized:
            return [], {
                "failed_span_id": span_id,
                "failed_span_index": index,
                "failed_span_error": "span_ids must not contain blank values",
            }
        normalized_span_ids.append(normalized)

    if not normalized_span_ids:
        return [], {
            "failed_span_id": None,
            "failed_span_error": "span_ids must contain at least one span ID",
        }
    return normalized_span_ids, None


def _build_span_resolution_error_result(
    *,
    entity: str,
    span_ids: list[str],
    failed_span_id: str | None,
    failed_span_error: str,
    failed_span_index: int | None = None,
    chunk_id: str | None = None,
    chunk_text: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "span_id": failed_span_id,
        "message": failed_span_error,
    }
    if failed_span_index is not None:
        error["span_index"] = failed_span_index
    if chunk_id:
        error["chunk_id"] = chunk_id

    payload: dict[str, Any] = {
        "status": "not_found",
        "entity": entity,
        "span_ids": span_ids,
        "failed_span_id": failed_span_id,
        "failed_span_error": failed_span_error,
        "span_resolution_errors": [error],
        "message": (
            f"Evidence span could not be resolved: {failed_span_error}. "
            "The evidence record was not created."
        ),
        "retry_instructions": _SPAN_RETRY_INSTRUCTIONS,
    }
    if failed_span_index is not None:
        payload["failed_span_index"] = failed_span_index
    if chunk_id:
        payload["chunk_id"] = chunk_id
    if chunk_text:
        payload["chunk_content_preview"] = _build_preview(chunk_text)
    if extra_fields:
        payload.update(extra_fields)
    return payload


def _unique_non_empty(values: list[str | None]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _log_record_evidence_result(
    result: dict[str, Any],
    *,
    document_id: str,
    verification_method: str,
) -> dict[str, Any]:
    logger.info(
        "record_evidence result status=%s method=%s entity=%r chunk_id=%s document=%s "
        "evidence_record_id=%s page=%r section=%r message=%r span_ids=%r "
        "verified_quote_preview=%r chunk_preview=%r",
        result.get("status"),
        verification_method,
        result.get("entity"),
        result.get("chunk_id"),
        document_id[:8],
        result.get("evidence_record_id"),
        result.get("page"),
        result.get("section"),
        result.get("message"),
        result.get("span_ids"),
        _preview_for_log(result.get("verified_quote")),
        _preview_for_log(result.get("chunk_content_preview")),
    )
    return result


def _preview_for_log(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip().replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def create_record_evidence_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a record_evidence tool bound to one document and user."""

    @function_tool
    async def record_evidence(
        entity: str,
        span_ids: list[str],
        object_id: str | None = None,
        pending_ref_id: str | None = None,
        field_path: str | None = None,
        object_type: str | None = None,
        validation_finding_id: str | None = None,
    ) -> dict[str, Any]:
        """Create verified evidence from read_chunk evidence span IDs.

        The backend resolves span_ids, copies exact source text into
        verified_quote, and preserves span provenance. Multiple span_ids in one
        call form one evidence unit and one evidence record; use separate calls
        for truly disjoint support.

        Args:
            entity: Entity or object label this evidence supports.
            span_ids: Non-empty list copied from read_chunk(...).chunk.evidence_spans[].span_id.
            object_id: Optional stable curatable object ID to attach this evidence to.
            pending_ref_id: Optional pending object/reference ID to attach this evidence to.
            field_path: Optional domain payload field path supported by this evidence.
            object_type: Optional curatable object type.
            validation_finding_id: Optional validation finding this evidence addresses.
        """
        if tracker:
            tracker.record_call("record_evidence")

        normalized_entity = str(entity or "").strip()
        normalized_span_ids, span_ids_error = _normalize_span_ids(span_ids)
        envelope_target_fields = _build_envelope_target_fields(
            object_id=object_id,
            pending_ref_id=pending_ref_id,
            object_type=object_type,
            field_path=field_path,
            validation_finding_id=validation_finding_id,
        )

        if span_ids_error is not None:
            return _log_record_evidence_result(
                _build_span_resolution_error_result(
                    entity=normalized_entity,
                    span_ids=normalized_span_ids,
                    extra_fields=envelope_target_fields,
                    **span_ids_error,
                ),
                document_id=document_id,
                verification_method="invalid_span_ids",
            )

        logger.info(
            "Resolving evidence spans for entity '%s' for document %s",
            normalized_entity,
            document_id[:8],
        )

        chunk_ids_by_span_id: dict[str, str] = {}
        for index, span_id in enumerate(normalized_span_ids):
            try:
                parsed = parse_evidence_span_id(span_id)
            except (TypeError, EvidenceSpanResolutionError) as exc:
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=span_id,
                        failed_span_index=index,
                        failed_span_error=f"{exc}. Call read_chunk again for current span IDs.",
                        extra_fields=envelope_target_fields,
                    ),
                    document_id=document_id,
                    verification_method="invalid_span_id",
                )
            chunk_ids_by_span_id[span_id] = parsed.chunk_id

        chunks_by_id: dict[str, dict[str, Any]] = {}
        for chunk_id in dict.fromkeys(chunk_ids_by_span_id.values()):
            try:
                chunk = await get_chunk_by_id(
                    chunk_id=chunk_id,
                    user_id=user_id,
                    document_id=document_id,
                )
            except Exception as exc:
                logger.error("Failed to load chunk %s for record_evidence: %s", chunk_id, exc, exc_info=True)
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=next(
                            span_id
                            for span_id, span_chunk_id in chunk_ids_by_span_id.items()
                            if span_chunk_id == chunk_id
                        ),
                        failed_span_error="Chunk could not be loaded for this span ID",
                        chunk_id=chunk_id,
                        extra_fields=envelope_target_fields,
                    ),
                    document_id=document_id,
                    verification_method="chunk_load_error",
                )

            if chunk is None:
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=next(
                            span_id
                            for span_id, span_chunk_id in chunk_ids_by_span_id.items()
                            if span_chunk_id == chunk_id
                        ),
                        failed_span_error=(
                            "Chunk referenced by this span ID was not found in the active document"
                        ),
                        chunk_id=chunk_id,
                        extra_fields=envelope_target_fields,
                    ),
                    document_id=document_id,
                    verification_method="missing_chunk",
                )

            chunks_by_id[chunk_id] = chunk

        fragments: list[_ResolvedSpanFragment] = []
        for index, span_id in enumerate(normalized_span_ids):
            chunk_id = chunk_ids_by_span_id[span_id]
            chunk = chunks_by_id[chunk_id]
            chunk_text = _resolve_exact_chunk_text(chunk)
            if chunk_text is None:
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=span_id,
                        failed_span_index=index,
                        failed_span_error=(
                            "Chunk referenced by this span ID has no exact raw text content"
                        ),
                        chunk_id=chunk_id,
                        extra_fields=envelope_target_fields,
                    ),
                    document_id=document_id,
                    verification_method="missing_chunk_text",
                )

            page = _resolve_chunk_page(chunk)
            section = _resolve_chunk_section(chunk)
            subsection = _resolve_chunk_subsection(chunk)
            try:
                span = resolve_evidence_span_id(
                    span_id=span_id,
                    chunk_text=chunk_text,
                    expected_chunk_id=chunk_id,
                    page_number=page,
                    section_title=section,
                )
            except EvidenceSpanResolutionError as exc:
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=span_id,
                        failed_span_index=index,
                        failed_span_error=f"{exc}. Call read_chunk again for current span IDs.",
                        chunk_id=chunk_id,
                        chunk_text=chunk_text,
                        extra_fields=envelope_target_fields,
                    ),
                    document_id=document_id,
                    verification_method="stale_span_id",
                )

            fragments.append(
                _ResolvedSpanFragment(
                    span=span,
                    chunk=chunk,
                    chunk_id=chunk_id,
                    document_id=document_id,
                    page=page,
                    section=section,
                    subsection=subsection,
                    figure_reference=_extract_figure_reference(chunk, chunk_text),
                )
            )

        verified_quote = "\n\n".join(fragment.span.text for fragment in fragments)
        first_fragment = fragments[0]
        chunk_ids = _unique_non_empty([fragment.chunk_id for fragment in fragments])
        figure_references = _unique_non_empty(
            [fragment.figure_reference for fragment in fragments]
        )

        payload: dict[str, Any] = {
            "status": "verified",
            "entity": normalized_entity,
            "span_ids": normalized_span_ids,
            "source_span_ids": normalized_span_ids,
            "verified_quote": verified_quote,
            "document_id": document_id,
            "chunk_id": first_fragment.chunk_id,
            "chunk_ids": chunk_ids,
            "source_fragments": [
                fragment.to_source_fragment()
                for fragment in fragments
            ],
        }
        payload.update(envelope_target_fields)
        if first_fragment.page is not None:
            payload["page"] = first_fragment.page
        if first_fragment.section:
            payload["section"] = first_fragment.section
        if first_fragment.subsection:
            payload["subsection"] = first_fragment.subsection
        if len(figure_references) == 1:
            payload["figure_reference"] = figure_references[0]

        payload["evidence_record_id"] = build_evidence_record_id(
            evidence_record={
                "entity": normalized_entity,
                "verified_quote": verified_quote,
                "page": first_fragment.page,
                "section": first_fragment.section,
                "chunk_id": first_fragment.chunk_id,
                "subsection": first_fragment.subsection,
                "figure_reference": payload.get("figure_reference"),
                "source_span_ids": normalized_span_ids,
            }
        )

        return _log_record_evidence_result(
            payload,
            document_id=document_id,
            verification_method="span_ids",
        )

    return record_evidence


__all__ = [
    "create_record_evidence_tool",
]
