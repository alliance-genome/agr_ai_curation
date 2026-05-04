"""Evidence-verification tool for document extraction agents."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from agents import function_tool

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
_NOT_FOUND_MESSAGE = (
    "Exact quote not found in this chunk. Retry with exact text copied from the chunk "
    "or drop this evidence."
)
_RETRY_EXHAUSTED_MESSAGE = (
    "Exact quote not found in this chunk after repeated attempts for this entity and chunk. "
    "Stop retrying this evidence; re-search for a better chunk or drop it."
)
_INVALID_CHUNK_LOAD_MESSAGE = (
    "Chunk could not be loaded. Retry with a chunk_id returned by search_document "
    "or read_section source_chunks, or drop this evidence."
)
_MISSING_CHUNK_MESSAGE = (
    "Chunk not found in the active document. Retry with a chunk_id returned by search_document "
    "or read_section source_chunks, or drop this evidence."
)
_PREVIEW_CHARS = 300
_MAX_UNVERIFIED_ATTEMPTS_PER_ENTITY_CHUNK = 3
_LEGACY_SYMBOLIC_CHUNK_ID_PATTERN = re.compile(r"^chunk-", re.IGNORECASE)
_TRAILING_SECTION_INDEX_PATTERN = re.compile(r"(?:[_\s-]+(?:chunk)?\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class _QuoteMatch:
    raw_start: int
    raw_end: int


def _find_verified_quote(claimed_quote: str, chunk_text: str) -> tuple[str | None, _QuoteMatch | None]:
    """Return a verified quote only when the stripped claim is exact source text."""
    source_quote = claimed_quote.strip()
    if not source_quote:
        return None, None

    # record_evidence is a strict source-provenance verifier. PDF text-layer
    # normalization and fuzzy localization belong in the viewer, not here.
    raw_start = chunk_text.find(source_quote)
    if raw_start < 0:
        return None, None

    raw_end = raw_start + len(source_quote)
    return chunk_text[raw_start:raw_end], _QuoteMatch(raw_start=raw_start, raw_end=raw_end)


def _build_preview(chunk_text: str, match: _QuoteMatch | None = None) -> str:
    stripped_text = chunk_text.strip()
    if len(stripped_text) <= _PREVIEW_CHARS:
        return stripped_text

    if match is None:
        preview = stripped_text[:_PREVIEW_CHARS].rstrip()
        if len(preview) < len(stripped_text):
            preview = preview.rstrip(" ,;:") + "..."
        return preview

    center = (match.raw_start + match.raw_end) // 2
    start = max(0, center - (_PREVIEW_CHARS // 2))
    end = min(len(chunk_text), start + _PREVIEW_CHARS)
    start = max(0, end - _PREVIEW_CHARS)
    preview = chunk_text[start:end].strip()
    if start > 0:
        preview = "..." + preview.lstrip()
    if end < len(chunk_text):
        preview = preview.rstrip() + "..."
    return preview


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return None


def _resolve_chunk_section(chunk: dict[str, Any]) -> str | None:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    return _first_non_empty(
        chunk.get("parent_section"),
        chunk.get("section_title"),
        metadata.get("parent_section"),
        metadata.get("parentSection"),
        metadata.get("section_title"),
        metadata.get("sectionTitle"),
    )


def _resolve_chunk_subsection(chunk: dict[str, Any]) -> str | None:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
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


def _resolve_chunk_text(chunk: dict[str, Any]) -> str:
    return str(chunk.get("text") or chunk.get("content") or chunk.get("content_preview") or "").strip()


def _is_uuid_like(value: str) -> bool:
    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True


def _normalize_section_label(value: Any) -> str:
    label = str(value or "").strip()
    label = _TRAILING_SECTION_INDEX_PATTERN.sub("", label)
    label = label.replace("_", " ")
    label = re.sub(r"[^a-z0-9]+", " ", label.lower())
    return re.sub(r"\s+", " ", label).strip()


def _section_label_from_chunk_id(chunk_id: str) -> str | None:
    raw_label = str(chunk_id or "").strip()
    if not raw_label or _is_uuid_like(raw_label):
        return None
    if _LEGACY_SYMBOLIC_CHUNK_ID_PATTERN.match(raw_label):
        return None

    normalized_label = _normalize_section_label(raw_label)
    if not normalized_label:
        return None

    return _TRAILING_SECTION_INDEX_PATTERN.sub("", raw_label).replace("_", " ").strip()


def _resolve_chunk_page(chunk: dict[str, Any]) -> int | None:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
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


def _extract_figure_reference(chunk: dict[str, Any], chunk_text: str) -> str | None:
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
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

    # If a chunk clearly contains multiple figure/table references, we deliberately
    # avoid choosing one to prevent sending ambiguous evidence anchors downstream.
    if len(unique_candidates) == 1:
        return unique_candidates[0]
    return None


def _build_not_found_result(
    chunk_text: str,
    *,
    entity: str,
    chunk_id: str,
    claimed_quote: str,
    page: int | None,
    section: str | None,
    subsection: str | None,
    best_match: _QuoteMatch | None = None,
    message: str = _NOT_FOUND_MESSAGE,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "not_found",
        "entity": entity,
        "chunk_id": chunk_id,
        "claimed_quote": claimed_quote,
        "chunk_content_preview": _build_preview(chunk_text, best_match),
        "message": message,
        "retry_instructions": (
            "Use an exact contiguous substring copied from this chunk, retry with another "
            "chunk_id returned by search_document/read_section, or drop this evidence."
        ),
    }
    if page is not None:
        payload["page"] = page
    if section:
        payload["section"] = section
    if subsection:
        payload["subsection"] = subsection
    if extra_fields:
        payload.update(extra_fields)
    return payload


def _build_section_label_retry_fields(chunk_id: str, claimed_quote: str) -> dict[str, Any]:
    return {
        "invalid_chunk_id": chunk_id,
        "invalid_chunk_id_reason": "not_a_tool_returned_chunk_id",
        "retry_tool": "search_document",
        "retry_query": claimed_quote[:240],
        "retry_instructions": (
            "Call search_document with the evidence quote or entity, then pass the returned "
            "hit.chunk_id to record_evidence. If you already used read_section, pass a value from "
            "section.source_chunks[].chunk_id. Only keep evidence after record_evidence returns verified."
        ),
    }


def _section_label_not_found_message(chunk_id: str) -> str:
    return (
        f"chunk_id '{chunk_id}' is not a chunk identifier returned by the document tools. "
        "record_evidence requires a chunk_id from search_document hits or read_section "
        "source_chunks. Retry with search_document using the evidence quote or entity and "
        "pass the returned hit.chunk_id, use section.source_chunks[].chunk_id from read_section, "
        "or drop this evidence."
    )


def _retry_key(entity: str, chunk_id: str) -> tuple[str, str]:
    return (entity.casefold(), chunk_id)


def create_record_evidence_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a record_evidence tool bound to one document and user."""
    unverified_attempts_by_entity_chunk: dict[tuple[str, str], int] = {}

    @function_tool
    async def record_evidence(entity: str, chunk_id: str, claimed_quote: str) -> dict[str, Any]:
        """Verify exact source-corpus text against a specific Weaviate chunk."""
        if tracker:
            tracker.record_call("record_evidence")

        normalized_entity = str(entity or "").strip()
        normalized_chunk_id = str(chunk_id or "").strip()
        normalized_claimed_quote = str(claimed_quote or "").strip()

        logger.info(
            "Verifying evidence for entity '%s' in chunk %s for document %s",
            normalized_entity,
            normalized_chunk_id,
            document_id[:8],
        )

        section_label = _section_label_from_chunk_id(normalized_chunk_id)
        if section_label:
            return _build_not_found_result(
                "",
                entity=normalized_entity,
                chunk_id=normalized_chunk_id,
                claimed_quote=normalized_claimed_quote,
                page=None,
                section=section_label,
                subsection=None,
                message=_section_label_not_found_message(normalized_chunk_id),
                extra_fields=_build_section_label_retry_fields(
                    normalized_chunk_id,
                    normalized_claimed_quote,
                ),
            )
        else:
            try:
                chunk = await get_chunk_by_id(
                    chunk_id=normalized_chunk_id,
                    user_id=user_id,
                    document_id=document_id,
                )
            except Exception as exc:
                logger.error("Failed to load chunk %s for record_evidence: %s", chunk_id, exc, exc_info=True)
                return _build_not_found_result(
                    "",
                    entity=normalized_entity,
                    chunk_id=normalized_chunk_id,
                    claimed_quote=normalized_claimed_quote,
                    page=None,
                    section=None,
                    subsection=None,
                    message=_INVALID_CHUNK_LOAD_MESSAGE,
                )

        if chunk is None:
            return _build_not_found_result(
                "",
                entity=normalized_entity,
                chunk_id=normalized_chunk_id,
                claimed_quote=normalized_claimed_quote,
                page=None,
                section=None,
                subsection=None,
                message=_MISSING_CHUNK_MESSAGE,
            )

        chunk_text = _resolve_chunk_text(chunk)
        page = _resolve_chunk_page(chunk)
        section = _resolve_chunk_section(chunk)
        subsection = _resolve_chunk_subsection(chunk)

        if not chunk_text:
            return _build_not_found_result(
                "",
                entity=normalized_entity,
                chunk_id=normalized_chunk_id,
                claimed_quote=normalized_claimed_quote,
                page=page,
                section=section,
                subsection=subsection,
                message="This chunk has no text content. Drop this evidence or retry with another chunk.",
            )

        verified_quote, best_match = _find_verified_quote(normalized_claimed_quote, chunk_text)
        if verified_quote is None:
            attempt_key = _retry_key(normalized_entity, normalized_chunk_id)
            attempt_count = unverified_attempts_by_entity_chunk.get(attempt_key, 0) + 1
            unverified_attempts_by_entity_chunk[attempt_key] = attempt_count
            retry_exhausted = attempt_count >= _MAX_UNVERIFIED_ATTEMPTS_PER_ENTITY_CHUNK
            return _build_not_found_result(
                chunk_text,
                entity=normalized_entity,
                chunk_id=normalized_chunk_id,
                claimed_quote=normalized_claimed_quote,
                page=page,
                section=section,
                subsection=subsection,
                best_match=best_match,
                message=_RETRY_EXHAUSTED_MESSAGE if retry_exhausted else _NOT_FOUND_MESSAGE,
                extra_fields={
                    "unverified_attempts": attempt_count,
                    "max_unverified_attempts": _MAX_UNVERIFIED_ATTEMPTS_PER_ENTITY_CHUNK,
                    "retry_exhausted": retry_exhausted,
                    "terminal": retry_exhausted,
                    "retry_instructions": (
                        "Stop retrying this entity/chunk pair; use search_document or read_section "
                        "to find exact source text in a better chunk, or drop this evidence."
                    )
                    if retry_exhausted
                    else (
                        "Retry with an exact contiguous substring copied from this chunk, "
                        "or re-search/drop the evidence if the chunk does not contain the claim."
                    ),
                },
            )

        unverified_attempts_by_entity_chunk.pop(_retry_key(normalized_entity, normalized_chunk_id), None)
        payload: dict[str, Any] = {
            "status": "verified",
            "entity": normalized_entity,
            "chunk_id": normalized_chunk_id,
            "claimed_quote": normalized_claimed_quote,
            "verified_quote": verified_quote,
        }
        if page is not None:
            payload["page"] = page
        if section:
            payload["section"] = section
        if subsection:
            payload["subsection"] = subsection

        figure_reference = _extract_figure_reference(chunk, chunk_text)
        if figure_reference:
            payload["figure_reference"] = figure_reference

        payload["evidence_record_id"] = build_evidence_record_id(
            evidence_record={
                "entity": normalized_entity,
                "verified_quote": verified_quote,
                "page": page,
                "section": section,
                "chunk_id": normalized_chunk_id,
                "subsection": subsection,
                "figure_reference": figure_reference,
            }
        )

        return payload

    return record_evidence


__all__ = [
    "create_record_evidence_tool",
]
