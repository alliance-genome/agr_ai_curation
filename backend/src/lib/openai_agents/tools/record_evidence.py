"""Evidence-verification tool for document extraction agents."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Optional

from agents import function_tool

from src.lib.openai_agents.evidence_summary import build_evidence_record_id
from src.lib.weaviate_client.chunks import get_chunk_by_id

if TYPE_CHECKING:
    from ..guardrails import ToolCallTracker


logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_FIGURE_REFERENCE_PATTERN = re.compile(
    r"\b(?:Fig(?:ure)?\.?\s*\d+[A-Za-z0-9-]*)\b",
    re.IGNORECASE,
)
_TABLE_REFERENCE_PATTERN = re.compile(
    r"\b(?:Table\.?\s*\d+[A-Za-z0-9-]*)\b",
    re.IGNORECASE,
)
_NOT_FOUND_MESSAGE = (
    "Quote not found in this chunk. Retry with text from the chunk or drop this evidence."
)
_PREVIEW_CHARS = 300


@dataclass(frozen=True)
class _QuoteMatch:
    raw_start: int
    raw_end: int
    score: float


@dataclass(frozen=True)
class _TokenSpan:
    token: str
    start: int
    end: int


def _canonicalize_character(value: str) -> str | None:
    if value == "\u00ad":
        return None
    if value == "\u00a0" or value.isspace():
        return " "
    if value.isalnum():
        return value.lower()
    return " "


def _normalize_text_with_index_map(text: str) -> tuple[str, tuple[int, ...]]:
    normalized: list[str] = []
    index_map: list[int] = []

    for raw_index, raw_character in enumerate(text):
        for normalized_character in unicodedata.normalize("NFKC", raw_character):
            canonical = _canonicalize_character(normalized_character)
            if canonical is None:
                continue
            if canonical == " ":
                if not normalized or normalized[-1] == " ":
                    continue
                normalized.append(" ")
                index_map.append(raw_index)
                continue
            normalized.append(canonical)
            index_map.append(raw_index)

    while normalized and normalized[-1] == " ":
        normalized.pop()
        index_map.pop()

    return "".join(normalized), tuple(index_map)


def _tokenize_with_spans(text: str) -> list[_TokenSpan]:
    return [
        _TokenSpan(token=match.group(0), start=match.start(), end=match.end())
        for match in _TOKEN_PATTERN.finditer(text)
    ]


def _minimum_fuzzy_score(token_count: int) -> float:
    if token_count >= 12:
        return 0.78
    if token_count >= 8:
        return 0.82
    if token_count >= 5:
        return 0.87
    return 0.92


def _extract_raw_span(
    raw_text: str,
    index_map: tuple[int, ...],
    normalized_start: int,
    normalized_end: int,
) -> str:
    raw_start = index_map[normalized_start]
    raw_end = index_map[normalized_end - 1] + 1

    while raw_start > 0 and raw_text[raw_start - 1] in "\"'([{":
        raw_start -= 1
    while raw_end < len(raw_text) and raw_text[raw_end] in "\"').,;:!?]}":
        raw_end += 1

    return raw_text[raw_start:raw_end].strip()


def _best_fuzzy_match(
    claimed_normalized: str,
    chunk_normalized: str,
    chunk_index_map: tuple[int, ...],
    raw_text: str,
) -> tuple[str | None, _QuoteMatch | None]:
    exact_start = chunk_normalized.find(claimed_normalized)
    if exact_start >= 0:
        exact_end = exact_start + len(claimed_normalized)
        return (
            _extract_raw_span(raw_text, chunk_index_map, exact_start, exact_end),
            _QuoteMatch(
                raw_start=chunk_index_map[exact_start],
                raw_end=chunk_index_map[exact_end - 1] + 1,
                score=1.0,
            ),
        )

    claim_tokens = _tokenize_with_spans(claimed_normalized)
    chunk_tokens = _tokenize_with_spans(chunk_normalized)
    if len(claim_tokens) < 3 or not chunk_tokens:
        return None, None

    claim_text = " ".join(token.token for token in claim_tokens)
    claim_token_count = len(claim_tokens)
    max_delta = max(2, claim_token_count // 5)
    min_window = max(1, claim_token_count - 2)
    max_window = min(len(chunk_tokens), claim_token_count + max_delta)

    best_quote: str | None = None
    best_match: _QuoteMatch | None = None

    for window_size in range(min_window, max_window + 1):
        for start_index in range(0, len(chunk_tokens) - window_size + 1):
            window_tokens = chunk_tokens[start_index:start_index + window_size]
            candidate_text = " ".join(token.token for token in window_tokens)
            if best_match is not None:
                shorter = min(len(candidate_text), len(claim_text))
                longer = max(len(candidate_text), len(claim_text))
                max_possible_ratio = (2 * shorter) / (shorter + longer)
                if max_possible_ratio < best_match.score:
                    continue
            score = SequenceMatcher(None, claim_text, candidate_text).ratio()
            if best_match is not None and score < best_match.score:
                continue

            normalized_start = window_tokens[0].start
            normalized_end = window_tokens[-1].end
            raw_start = chunk_index_map[normalized_start]
            raw_end = chunk_index_map[normalized_end - 1] + 1
            candidate_quote = _extract_raw_span(
                raw_text,
                chunk_index_map,
                normalized_start,
                normalized_end,
            )

            if best_match is None or score > best_match.score or (
                score == best_match.score
                and (raw_end - raw_start) < (best_match.raw_end - best_match.raw_start)
            ):
                best_quote = candidate_quote
                best_match = _QuoteMatch(raw_start=raw_start, raw_end=raw_end, score=score)
            if best_match is not None and best_match.score >= 0.98:
                return best_quote, best_match

    if best_match is None or best_match.score < _minimum_fuzzy_score(claim_token_count):
        return None, best_match

    return best_quote, best_match


def _find_verified_quote(claimed_quote: str, chunk_text: str) -> tuple[str | None, _QuoteMatch | None]:
    claimed_normalized, _claimed_index_map = _normalize_text_with_index_map(claimed_quote)
    chunk_normalized, chunk_index_map = _normalize_text_with_index_map(chunk_text)
    if not claimed_normalized or not chunk_normalized:
        return None, None
    return _best_fuzzy_match(claimed_normalized, chunk_normalized, chunk_index_map, chunk_text)


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
    page: int | None,
    section: str | None,
    subsection: str | None,
    best_match: _QuoteMatch | None = None,
    message: str = _NOT_FOUND_MESSAGE,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "not_found",
        "chunk_content_preview": _build_preview(chunk_text, best_match),
        "message": message,
    }
    if page is not None:
        payload["page"] = page
    if section:
        payload["section"] = section
    if subsection:
        payload["subsection"] = subsection
    return payload


def create_record_evidence_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a record_evidence tool bound to one document and user."""

    @function_tool
    async def record_evidence(entity: str, chunk_id: str, claimed_quote: str) -> dict[str, Any]:
        """Verify a claimed quote against a specific Weaviate chunk before persisting evidence."""
        if tracker:
            tracker.record_call("record_evidence")

        logger.info(
            "Verifying evidence for entity '%s' in chunk %s for document %s",
            entity,
            chunk_id,
            document_id[:8],
        )

        try:
            chunk = await get_chunk_by_id(
                chunk_id=chunk_id,
                user_id=user_id,
                document_id=document_id,
            )
        except Exception as exc:
            logger.error("Failed to load chunk %s for record_evidence: %s", chunk_id, exc, exc_info=True)
            return _build_not_found_result(
                "",
                page=None,
                section=None,
                subsection=None,
                message="Chunk could not be loaded. Retry with a valid chunk_id or drop this evidence.",
            )

        if chunk is None:
            return _build_not_found_result(
                "",
                page=None,
                section=None,
                subsection=None,
                message="Chunk not found in the active document. Retry with a valid chunk_id or drop this evidence.",
            )

        chunk_text = str(chunk.get("text") or chunk.get("content_preview") or "").strip()
        page = _resolve_chunk_page(chunk)
        section = _resolve_chunk_section(chunk)
        subsection = _resolve_chunk_subsection(chunk)

        if not chunk_text:
            return _build_not_found_result(
                "",
                page=page,
                section=section,
                subsection=subsection,
                message="This chunk has no text content. Drop this evidence or retry with another chunk.",
            )

        verified_quote, best_match = _find_verified_quote(str(claimed_quote or "").strip(), chunk_text)
        if verified_quote is None:
            return _build_not_found_result(
                chunk_text,
                page=page,
                section=section,
                subsection=subsection,
                best_match=best_match,
            )

        payload: dict[str, Any] = {
            "status": "verified",
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
                "entity": str(entity or "").strip(),
                "verified_quote": verified_quote,
                "page": page,
                "section": section,
                "chunk_id": str(chunk_id or "").strip(),
                "subsection": subsection,
                "figure_reference": figure_reference,
            }
        )

        return payload

    return record_evidence


__all__ = [
    "create_record_evidence_tool",
]
