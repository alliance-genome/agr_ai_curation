"""Evidence-verification tool for document extraction agents."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
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
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_ABBREVIATION_END_PATTERN = re.compile(
    r"\b(?:no|dr|mr|mrs|ms|prof|fig|ref|refs?|eq|eqs|vs)\.$",
    re.IGNORECASE,
)
_IDENTITY_TOKEN_PATTERN = re.compile(
    r"\b(?:[A-Za-z]+[A-Za-z0-9]*[-/][A-Za-z0-9+_.:/-]+|"
    r"[A-Za-z]*\d[A-Za-z0-9+_.:/-]*|"
    r"[A-Za-z]+[A-Z][A-Za-z0-9]*)\b"
)
_WORD_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9]+\b")
_SOURCE_TRAILING_PUNCTUATION = ".,;:!?)]}"
_FUZZY_CANDIDATE_LIMIT = 5
_FUZZY_REVIEW_CANDIDATE_LIMIT = 3
_FUZZY_MIN_SCORE = 0.78
_MIN_CLAIM_TOKEN_COVERAGE = 0.90
_EVIDENCE_CONFIRMATION_MODEL_ENV = "EVIDENCE_CONFIRMATION_MODEL"
_EVIDENCE_CONFIRMATION_ENABLED_ENV = "EVIDENCE_CONFIRMATION_ENABLED"
_EVIDENCE_CONFIRMATION_TIMEOUT_ENV = "EVIDENCE_CONFIRMATION_TIMEOUT_SECONDS"
_DEFAULT_EVIDENCE_CONFIRMATION_MODEL = "gpt-5.4-mini"
_DEFAULT_EVIDENCE_CONFIRMATION_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class _QuoteMatch:
    raw_start: int
    raw_end: int


@dataclass(frozen=True)
class _FuzzyQuoteCandidate:
    text: str
    raw_start: int
    raw_end: int
    score: float


def _find_verified_quote(claimed_quote: str, chunk_text: str) -> tuple[str | None, _QuoteMatch | None]:
    """Return a verified quote only when the stripped claim is exact source text."""
    source_quote = claimed_quote.strip()
    if not source_quote:
        return None, None

    # Exact source text is the trusted fast path. Bounded fuzzy confirmation is
    # handled separately so nearby spans cannot be silently blessed here.
    raw_start = chunk_text.find(source_quote)
    if raw_start < 0:
        return None, None

    raw_end = raw_start + len(source_quote)
    return chunk_text[raw_start:raw_end], _QuoteMatch(raw_start=raw_start, raw_end=raw_end)


def _normalize_for_similarity(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _is_token_char(value: str) -> bool:
    return value.isalnum() or value == "_"


def _expand_match_to_source_boundaries(text: str, start: int, end: int) -> tuple[int, int]:
    bounded_start = max(0, min(start, len(text)))
    bounded_end = max(bounded_start, min(end, len(text)))

    while (
        bounded_start > 0
        and bounded_start < len(text)
        and _is_token_char(text[bounded_start - 1])
        and _is_token_char(text[bounded_start])
    ):
        bounded_start -= 1

    nearby_prefix_start = max(0, bounded_start - 80)
    nearby_prefix = text[nearby_prefix_start:bounded_start]
    sentence_boundaries = list(_SENTENCE_BOUNDARY_PATTERN.finditer(nearby_prefix))
    if sentence_boundaries:
        sentence_start = nearby_prefix_start + sentence_boundaries[-1].end()
        if bounded_start - sentence_start <= 40:
            bounded_start = sentence_start
    elif bounded_start <= 40:
        bounded_start = 0

    while (
        bounded_end > 0
        and bounded_end < len(text)
        and _is_token_char(text[bounded_end - 1])
        and _is_token_char(text[bounded_end])
    ):
        bounded_end += 1

    while bounded_end < len(text) and text[bounded_end] in _SOURCE_TRAILING_PUNCTUATION:
        bounded_end += 1

    return bounded_start, bounded_end


def _iter_sentence_spans(chunk_text: str) -> list[tuple[int, int]]:
    text = chunk_text.strip()
    if not text:
        return []

    offset = chunk_text.find(text)
    spans: list[tuple[int, int]] = []
    start = offset
    for boundary in _SENTENCE_BOUNDARY_PATTERN.finditer(text):
        end = offset + boundary.start()
        if _ABBREVIATION_END_PATTERN.search(chunk_text[start:end].rstrip()):
            continue
        if end > start:
            spans.append((start, end))
        start = offset + boundary.end()

    final_end = offset + len(text)
    if final_end > start:
        spans.append((start, final_end))

    return spans or [(0, len(chunk_text))]


def _sentence_spans_overlapping(
    sentence_spans: list[tuple[int, int]],
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    overlaps: list[tuple[int, int]] = []
    for span_start, span_end in sentence_spans:
        if span_start < end and start < span_end:
            overlaps.append((span_start, span_end))
    return overlaps


def _rapidfuzz_partial_alignment(claimed_quote: str, chunk_text: str) -> tuple[float, int, int] | None:
    try:
        from rapidfuzz import fuzz

        claimed_for_alignment = claimed_quote.lower()
        chunk_for_alignment = chunk_text.lower()
        if len(claimed_for_alignment) != len(claimed_quote) or len(chunk_for_alignment) != len(chunk_text):
            claimed_for_alignment = claimed_quote
            chunk_for_alignment = chunk_text
        alignment = fuzz.partial_ratio_alignment(
            claimed_for_alignment,
            chunk_for_alignment,
        )
    except Exception:
        return None

    score = float(alignment.score) / 100.0
    start = int(alignment.dest_start)
    end = int(alignment.dest_end)
    if end <= start:
        return None
    return score, start, end


def _rapidfuzz_partial_score(claimed_quote: str, candidate_text: str) -> float | None:
    try:
        from rapidfuzz import fuzz

        return float(fuzz.partial_ratio(claimed_quote.lower(), candidate_text.lower())) / 100.0
    except Exception:
        return None


def _candidate_similarity_score(claimed_quote: str, candidate_text: str) -> float:
    rapidfuzz_score = _rapidfuzz_partial_score(claimed_quote, candidate_text)
    if rapidfuzz_score is not None:
        return rapidfuzz_score

    return SequenceMatcher(
        None,
        _normalize_for_similarity(claimed_quote),
        _normalize_for_similarity(candidate_text),
    ).ratio()


def _identity_tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _IDENTITY_TOKEN_PATTERN.findall(str(value or ""))
        if token.strip()
    }


def _entity_identity_tokens(entity: str) -> set[str]:
    tokens = [
        token.casefold()
        for token in _WORD_TOKEN_PATTERN.findall(str(entity or ""))
        if token.strip()
    ]
    if tokens and len(tokens) <= 3 and ":" not in str(entity or ""):
        return set(tokens)
    return _identity_tokens(entity)


def _required_identity_tokens(*, entity: str, claimed_quote: str) -> set[str]:
    return _entity_identity_tokens(entity) | _identity_tokens(claimed_quote)


def _identity_tokens_preserved(*, entity: str, claimed_quote: str, candidate_text: str) -> bool:
    required_tokens = _required_identity_tokens(
        entity=entity,
        claimed_quote=claimed_quote,
    )
    if not required_tokens:
        return False

    candidate_tokens = set(
        token.casefold()
        for token in _WORD_TOKEN_PATTERN.findall(str(candidate_text or ""))
        if token.strip()
    ) | _identity_tokens(candidate_text)
    return required_tokens <= candidate_tokens


def _candidate_satisfies_required_identity(
    *,
    entity: str,
    claimed_quote: str,
    candidate_text: str,
) -> bool:
    required_tokens = _required_identity_tokens(
        entity=entity,
        claimed_quote=claimed_quote,
    )
    if not required_tokens:
        return True
    candidate_tokens = set(
        token.casefold()
        for token in _WORD_TOKEN_PATTERN.findall(str(candidate_text or ""))
        if token.strip()
    ) | _identity_tokens(candidate_text)
    return required_tokens <= candidate_tokens


def _claim_token_coverage(claimed_quote: str, candidate_text: str) -> float:
    claimed_tokens = [
        token.casefold()
        for token in _WORD_TOKEN_PATTERN.findall(str(claimed_quote or ""))
        if token.strip()
    ]
    if not claimed_tokens:
        return 0.0

    remaining_candidate_tokens: dict[str, int] = {}
    for token in _WORD_TOKEN_PATTERN.findall(str(candidate_text or "")):
        normalized = token.casefold()
        if not normalized:
            continue
        remaining_candidate_tokens[normalized] = remaining_candidate_tokens.get(normalized, 0) + 1

    matched = 0
    for token in claimed_tokens:
        count = remaining_candidate_tokens.get(token, 0)
        if count <= 0:
            continue
        matched += 1
        remaining_candidate_tokens[token] = count - 1

    return matched / len(claimed_tokens)


def _candidate_satisfies_claim_coverage(*, claimed_quote: str, candidate_text: str) -> bool:
    return _claim_token_coverage(claimed_quote, candidate_text) >= _MIN_CLAIM_TOKEN_COVERAGE


def _rank_fuzzy_candidates_for_review(
    *,
    entity: str,
    claimed_quote: str,
    candidates: list[_FuzzyQuoteCandidate],
) -> list[_FuzzyQuoteCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            _candidate_satisfies_required_identity(
                entity=entity,
                claimed_quote=claimed_quote,
                candidate_text=candidate.text,
            ),
            candidate.score,
            -len(candidate.text),
        ),
        reverse=True,
    )


def _fuzzy_quote_candidates(claimed_quote: str, chunk_text: str) -> list[_FuzzyQuoteCandidate]:
    normalized_claim = _normalize_for_similarity(claimed_quote)
    if not normalized_claim:
        return []

    sentence_spans = _iter_sentence_spans(chunk_text)
    candidate_spans: list[tuple[int, int]] = []

    alignment = _rapidfuzz_partial_alignment(claimed_quote, chunk_text)
    if alignment is not None:
        alignment_score, alignment_start, alignment_end = alignment
        if alignment_score >= _FUZZY_MIN_SCORE:
            expanded_start, expanded_end = _expand_match_to_source_boundaries(
                chunk_text,
                alignment_start,
                alignment_end,
            )
            candidate_spans.append((expanded_start, expanded_end))

            overlapping_sentence_spans = _sentence_spans_overlapping(
                sentence_spans,
                alignment_start,
                alignment_end,
            )
            candidate_spans.extend(overlapping_sentence_spans)

    for index, (start, end) in enumerate(sentence_spans):
        candidate_spans.append((start, end))
        if index + 1 < len(sentence_spans):
            candidate_spans.append((start, sentence_spans[index + 1][1]))

    seen: set[tuple[int, int]] = set()
    candidates: list[_FuzzyQuoteCandidate] = []
    for start, end in candidate_spans:
        if (start, end) in seen:
            continue
        seen.add((start, end))

        candidate_text = chunk_text[start:end].strip()
        if not candidate_text:
            continue

        score = _candidate_similarity_score(claimed_quote, candidate_text)
        if score < _FUZZY_MIN_SCORE:
            continue

        candidates.append(_FuzzyQuoteCandidate(
            text=candidate_text,
            raw_start=start,
            raw_end=end,
            score=score,
        ))

    candidates.sort(key=lambda candidate: (candidate.score, -len(candidate.text)), reverse=True)
    return candidates[:_FUZZY_CANDIDATE_LIMIT]


def _evidence_confirmation_enabled() -> bool:
    raw = os.getenv(_EVIDENCE_CONFIRMATION_ENABLED_ENV, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _openai_key_allows_evidence_confirmation() -> bool:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    return bool(api_key) and not api_key.startswith("test-")


def _evidence_confirmation_timeout_seconds() -> float:
    raw = os.getenv(
        _EVIDENCE_CONFIRMATION_TIMEOUT_ENV,
        str(_DEFAULT_EVIDENCE_CONFIRMATION_TIMEOUT_SECONDS),
    ).strip()
    try:
        timeout = float(raw)
    except ValueError:
        return _DEFAULT_EVIDENCE_CONFIRMATION_TIMEOUT_SECONDS
    return timeout if timeout > 0 else _DEFAULT_EVIDENCE_CONFIRMATION_TIMEOUT_SECONDS


def _accepted_candidate_index(payload: dict[str, Any], *, candidate_count: int) -> int | None:
    decision = str(payload.get("decision") or "").strip().lower()
    selected_index = payload.get("selected_index")
    if decision != "accept" or not isinstance(selected_index, int) or isinstance(selected_index, bool):
        return None
    if selected_index < 0 or selected_index >= candidate_count:
        return None
    return selected_index


async def _confirm_fuzzy_evidence_with_llm(
    *,
    entity: str,
    claimed_quote: str,
    candidates: list[_FuzzyQuoteCandidate],
) -> int | None:
    """Return the accepted candidate index, or None when the arbiter rejects/abstains."""

    if not candidates or not _evidence_confirmation_enabled() or not _openai_key_allows_evidence_confirmation():
        return None

    try:
        from openai import AsyncOpenAI

        model = os.getenv(
            _EVIDENCE_CONFIRMATION_MODEL_ENV,
            _DEFAULT_EVIDENCE_CONFIRMATION_MODEL,
        ).strip() or _DEFAULT_EVIDENCE_CONFIRMATION_MODEL
        client = AsyncOpenAI(timeout=_evidence_confirmation_timeout_seconds())

        candidate_payload = [
            {
                "index": index,
                "score": round(candidate.score, 4),
                "text": candidate.text,
            }
            for index, candidate in enumerate(candidates[:_FUZZY_REVIEW_CANDIDATE_LIMIT])
        ]
        completion_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a strict evidence quote arbiter. Decide whether one candidate "
                        "span is the same paper evidence as the claimed quote. Accept citation "
                        "marker, whitespace, and typography differences only when the biological "
                        "entity, stock numbers, allele/genotype labels, measurements, and other "
                        "identity-bearing tokens are preserved. Reject neighboring sentences, "
                        "changed identifiers, changed numbers, or merely related claims. Return "
                        "only JSON: {\"decision\":\"accept|reject|ambiguous\","
                        "\"selected_index\":0,\"reason\":\"short\"}."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "entity": entity,
                            "claimed_quote": claimed_quote,
                            "candidates": candidate_payload,
                        },
                        ensure_ascii=True,
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        if not model.startswith("gpt-5"):
            completion_kwargs["temperature"] = 0

        response = await client.chat.completions.create(**completion_kwargs)
        content = response.choices[0].message.content
        payload = json.loads(content or "{}")
        decision = str(payload.get("decision") or "").strip().lower()
        selected_index = payload.get("selected_index")
        reason = _preview_for_log(payload.get("reason"), limit=180)
        logger.info(
            "record_evidence LLM confirmation decision=%s selected_index=%r reason=%r model=%s",
            decision,
            selected_index,
            reason,
            model,
        )
        return _accepted_candidate_index(
            payload,
            candidate_count=min(len(candidates), _FUZZY_REVIEW_CANDIDATE_LIMIT),
        )
    except Exception as exc:
        logger.warning("record_evidence LLM confirmation failed: %s: %s", type(exc).__name__, exc)
        return None


async def _find_fuzzy_verified_quote(
    *,
    entity: str,
    claimed_quote: str,
    chunk_text: str,
) -> tuple[str | None, _QuoteMatch | None, list[_FuzzyQuoteCandidate]]:
    candidates = _fuzzy_quote_candidates(claimed_quote, chunk_text)
    if not candidates:
        return None, None, []
    candidates = _rank_fuzzy_candidates_for_review(
        entity=entity,
        claimed_quote=claimed_quote,
        candidates=candidates,
    )

    best = candidates[0]
    second_score = candidates[1].score if len(candidates) > 1 else 0.0
    margin = best.score - second_score
    identity_preserved = _identity_tokens_preserved(
        entity=entity,
        claimed_quote=claimed_quote,
        candidate_text=best.text,
    )

    logger.info(
        "record_evidence fuzzy candidates entity=%r best_score=%.4f second_score=%.4f "
        "margin=%.4f identity_preserved=%s candidate_count=%d",
        entity,
        best.score,
        second_score,
        margin,
        identity_preserved,
        len(candidates),
    )

    selected_index = await _confirm_fuzzy_evidence_with_llm(
        entity=entity,
        claimed_quote=claimed_quote,
        candidates=candidates,
    )
    if selected_index is None:
        return None, _QuoteMatch(raw_start=best.raw_start, raw_end=best.raw_end), candidates

    selected = candidates[selected_index]
    if not _candidate_satisfies_claim_coverage(
        claimed_quote=claimed_quote,
        candidate_text=selected.text,
    ):
        logger.warning(
            "record_evidence rejected LLM-accepted fuzzy candidate because claim coverage was too low "
            "entity=%r selected_index=%d score=%.4f coverage=%.4f",
            entity,
            selected_index,
            selected.score,
            _claim_token_coverage(claimed_quote, selected.text),
        )
        return None, _QuoteMatch(raw_start=best.raw_start, raw_end=best.raw_end), candidates

    if not _candidate_satisfies_required_identity(
        entity=entity,
        claimed_quote=claimed_quote,
        candidate_text=selected.text,
    ):
        logger.warning(
            "record_evidence rejected LLM-accepted fuzzy candidate because identity tokens changed "
            "entity=%r selected_index=%d score=%.4f",
            entity,
            selected_index,
            selected.score,
        )
        return None, _QuoteMatch(raw_start=best.raw_start, raw_end=best.raw_end), candidates

    logger.info(
        "record_evidence accepted fuzzy candidate after LLM confirmation index=%d score=%.4f",
        selected_index,
        selected.score,
    )
    return selected.text, _QuoteMatch(raw_start=selected.raw_start, raw_end=selected.raw_end), candidates


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


def _preview_for_log(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip().replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


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


def _log_record_evidence_result(
    result: dict[str, Any],
    *,
    document_id: str,
    verification_method: str,
) -> dict[str, Any]:
    logger.info(
        "record_evidence result status=%s method=%s entity=%r chunk_id=%s document=%s "
        "evidence_record_id=%s page=%r section=%r terminal=%r retry_exhausted=%r "
        "message=%r claimed_quote_preview=%r verified_quote_preview=%r chunk_preview=%r",
        result.get("status"),
        verification_method,
        result.get("entity"),
        result.get("chunk_id"),
        document_id[:8],
        result.get("evidence_record_id"),
        result.get("page"),
        result.get("section"),
        result.get("terminal"),
        result.get("retry_exhausted"),
        result.get("message"),
        _preview_for_log(result.get("claimed_quote")),
        _preview_for_log(result.get("verified_quote")),
        _preview_for_log(result.get("chunk_content_preview")),
    )
    return result


def create_record_evidence_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a record_evidence tool bound to one document and user."""
    unverified_attempts_by_entity_chunk: dict[tuple[str, str], int] = {}

    @function_tool
    async def record_evidence(
        entity: str,
        chunk_id: str,
        claimed_quote: str,
        object_id: str | None = None,
        pending_ref_id: str | None = None,
        field_path: str | None = None,
        object_type: str | None = None,
        validation_finding_id: str | None = None,
    ) -> dict[str, Any]:
        """Verify exact source-corpus text against a specific Weaviate chunk."""
        if tracker:
            tracker.record_call("record_evidence")

        normalized_entity = str(entity or "").strip()
        normalized_chunk_id = str(chunk_id or "").strip()
        normalized_claimed_quote = str(claimed_quote or "").strip()
        envelope_target_fields = _build_envelope_target_fields(
            object_id=object_id,
            pending_ref_id=pending_ref_id,
            object_type=object_type,
            field_path=field_path,
            validation_finding_id=validation_finding_id,
        )

        logger.info(
            "Verifying evidence for entity '%s' in chunk %s for document %s",
            normalized_entity,
            normalized_chunk_id,
            document_id[:8],
        )

        section_label = _section_label_from_chunk_id(normalized_chunk_id)
        if section_label:
            return _log_record_evidence_result(
                _build_not_found_result(
                    "",
                    entity=normalized_entity,
                    chunk_id=normalized_chunk_id,
                    claimed_quote=normalized_claimed_quote,
                    page=None,
                    section=section_label,
                    subsection=None,
                    message=_section_label_not_found_message(normalized_chunk_id),
                    extra_fields=_merge_extra_fields(
                        envelope_target_fields,
                        _build_section_label_retry_fields(
                            normalized_chunk_id,
                            normalized_claimed_quote,
                        ),
                    ),
                ),
                document_id=document_id,
                verification_method="invalid_chunk_id",
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
                return _log_record_evidence_result(
                    _build_not_found_result(
                        "",
                        entity=normalized_entity,
                        chunk_id=normalized_chunk_id,
                        claimed_quote=normalized_claimed_quote,
                        page=None,
                        section=None,
                        subsection=None,
                        message=_INVALID_CHUNK_LOAD_MESSAGE,
                        extra_fields=envelope_target_fields,
                    ),
                    document_id=document_id,
                    verification_method="chunk_load_error",
                )

        if chunk is None:
            return _log_record_evidence_result(
                _build_not_found_result(
                    "",
                    entity=normalized_entity,
                    chunk_id=normalized_chunk_id,
                    claimed_quote=normalized_claimed_quote,
                    page=None,
                    section=None,
                    subsection=None,
                    message=_MISSING_CHUNK_MESSAGE,
                    extra_fields=envelope_target_fields,
                ),
                document_id=document_id,
                verification_method="missing_chunk",
            )

        chunk_text = _resolve_chunk_text(chunk)
        page = _resolve_chunk_page(chunk)
        section = _resolve_chunk_section(chunk)
        subsection = _resolve_chunk_subsection(chunk)

        if not chunk_text:
            return _log_record_evidence_result(
                _build_not_found_result(
                    "",
                    entity=normalized_entity,
                    chunk_id=normalized_chunk_id,
                    claimed_quote=normalized_claimed_quote,
                    page=page,
                    section=section,
                    subsection=subsection,
                    message="This chunk has no text content. Drop this evidence or retry with another chunk.",
                    extra_fields=envelope_target_fields,
                ),
                document_id=document_id,
                verification_method="empty_chunk",
            )

        verified_quote, best_match = _find_verified_quote(normalized_claimed_quote, chunk_text)
        verification_method = "exact"
        if verified_quote is None:
            verified_quote, best_match, fuzzy_candidates = await _find_fuzzy_verified_quote(
                entity=normalized_entity,
                claimed_quote=normalized_claimed_quote,
                chunk_text=chunk_text,
            )
            if verified_quote is not None:
                verification_method = "fuzzy_confirmed"
            elif fuzzy_candidates:
                logger.info(
                    "record_evidence rejected fuzzy candidates entity=%r best_score=%.4f candidate_count=%d",
                    normalized_entity,
                    fuzzy_candidates[0].score,
                    len(fuzzy_candidates),
                )

        if verified_quote is None:
            attempt_key = _retry_key(normalized_entity, normalized_chunk_id)
            attempt_count = unverified_attempts_by_entity_chunk.get(attempt_key, 0) + 1
            unverified_attempts_by_entity_chunk[attempt_key] = attempt_count
            retry_exhausted = attempt_count >= _MAX_UNVERIFIED_ATTEMPTS_PER_ENTITY_CHUNK
            return _log_record_evidence_result(
                _build_not_found_result(
                    chunk_text,
                    entity=normalized_entity,
                    chunk_id=normalized_chunk_id,
                    claimed_quote=normalized_claimed_quote,
                    page=page,
                    section=section,
                    subsection=subsection,
                    best_match=best_match,
                    message=_RETRY_EXHAUSTED_MESSAGE if retry_exhausted else _NOT_FOUND_MESSAGE,
                    extra_fields=_merge_extra_fields(
                        envelope_target_fields,
                        {
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
                    ),
                ),
                document_id=document_id,
                verification_method="not_found",
            )

        unverified_attempts_by_entity_chunk.pop(_retry_key(normalized_entity, normalized_chunk_id), None)
        payload: dict[str, Any] = {
            "status": "verified",
            "entity": normalized_entity,
            "chunk_id": normalized_chunk_id,
            "claimed_quote": normalized_claimed_quote,
            "verified_quote": verified_quote,
        }
        payload.update(envelope_target_fields)
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

        return _log_record_evidence_result(
            payload,
            document_id=document_id,
            verification_method=verification_method,
        )

    return record_evidence


__all__ = [
    "create_record_evidence_tool",
]
