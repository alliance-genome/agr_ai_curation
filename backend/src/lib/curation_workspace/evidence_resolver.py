"""Deterministic evidence-anchor resolver for prep evidence and workspace enrichment."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.lib.curation_workspace.session_service import PreparedEvidenceRecordInput
from src.models.sql.database import SessionLocal
from src.schemas.curation_prep import CurationPrepCandidate, CurationPrepEvidenceRecord
from src.schemas.curation_workspace import (
    CurationEvidenceSource,
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
)

if TYPE_CHECKING:
    from src.lib.curation_workspace.pipeline import EvidenceResolutionContext, NormalizedCandidate


logger = logging.getLogger(__name__)

MERGED_MARKDOWN_SEPARATOR = "\n\n"
QUOTE_FRAGMENT_WORDS = 24
OPENING_BRACKETS = "([{"
CLOSING_BRACKETS = ")]}"
PUNCTUATION_WITHOUT_LEADING_SPACE = ",.;:!?"
DASH_CHARACTERS = {"\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"}
APOSTROPHE_CHARACTERS = {"\u2018", "\u2019", "\u201A", "\u201B"}
DOUBLE_QUOTE_CHARACTERS = {"\u201C", "\u201D", "\u201E", "\u201F"}
FIGURE_REFERENCE_PATTERN = re.compile(
    r"\b(?:Fig(?:ure)?\.?\s*\d+[A-Za-z0-9-]*)\b",
    re.IGNORECASE,
)
TABLE_REFERENCE_PATTERN = re.compile(
    r"\b(?:Table\.?\s*\d+[A-Za-z0-9-]*)\b",
    re.IGNORECASE,
)

ChunkLoader = Callable[[str, str], Sequence[Mapping[str, Any]]]
SessionFactory = Callable[[], Session]
UserIdResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class _ResolutionChunk:
    id: str
    chunk_index: int
    text: str
    page_number: int | None
    section_title: str | None
    parent_section: str | None
    subsection: str | None
    section_path: tuple[str, ...]


@dataclass(frozen=True)
class _ChunkSpan:
    chunk: _ResolutionChunk
    raw_start: int
    raw_end: int


@dataclass(frozen=True)
class _PreparedDocument:
    chunks: tuple[_ResolutionChunk, ...]
    chunk_spans: tuple[_ChunkSpan, ...]
    raw_text: str
    normalized_text: str
    normalized_index_map: tuple[int, ...]

    @classmethod
    def from_chunks(cls, chunks: Sequence[_ResolutionChunk]) -> "_PreparedDocument":
        normalized_chunks = tuple(
            sorted(chunks, key=lambda chunk: (chunk.chunk_index, chunk.page_number or 0))
        )
        if not normalized_chunks:
            return cls(
                chunks=(),
                chunk_spans=(),
                raw_text="",
                normalized_text="",
                normalized_index_map=(),
            )

        parts: list[str] = []
        spans: list[_ChunkSpan] = []
        cursor = 0
        for index, chunk in enumerate(normalized_chunks):
            if index:
                parts.append(MERGED_MARKDOWN_SEPARATOR)
                cursor += len(MERGED_MARKDOWN_SEPARATOR)
            start = cursor
            parts.append(chunk.text)
            cursor += len(chunk.text)
            spans.append(_ChunkSpan(chunk=chunk, raw_start=start, raw_end=cursor))

        raw_text = "".join(parts)
        normalized_text, normalized_index_map = _normalize_text_with_mapping(raw_text)
        return cls(
            chunks=normalized_chunks,
            chunk_spans=tuple(spans),
            raw_text=raw_text,
            normalized_text=normalized_text,
            normalized_index_map=tuple(normalized_index_map),
        )


@dataclass(frozen=True)
class _QuoteCandidate:
    query: str
    locator_quality: EvidenceLocatorQuality
    fragment: bool = False


@dataclass(frozen=True)
class _ResolvedSpan:
    raw_start: int
    raw_end: int
    matched_text: str
    chunk_ids: tuple[str, ...]
    page_number: int | None
    section_title: str | None
    subsection_title: str | None
    section_labels: tuple[str, ...]


@dataclass(frozen=True)
class _QuoteResolution:
    locator_quality: EvidenceLocatorQuality
    viewer_search_text: str
    normalized_text: str | None
    matched_text: str
    chunk_ids: tuple[str, ...]
    page_number: int | None
    section_title: str | None
    subsection_title: str | None
    fragment: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _SectionResolution:
    chunk_ids: tuple[str, ...]
    page_number: int | None
    section_title: str | None
    subsection_title: str | None
    warnings: tuple[str, ...] = ()


def _default_chunk_loader(document_id: str, user_id: str) -> Sequence[Mapping[str, Any]]:
    from src.lib.weaviate_client.chunks import fetch_document_chunks_for_resolution

    return fetch_document_chunks_for_resolution(document_id, user_id)


class DeterministicEvidenceAnchorResolver:
    """Preserve verified prep anchors or enrich evidence anchors against PDFX chunks."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory = SessionLocal,
        user_id_resolver: UserIdResolver | None = None,
        chunk_loader: ChunkLoader | None = None,
        resolve_against_document: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._user_id_resolver = user_id_resolver or self._resolve_user_id
        self._chunk_loader = chunk_loader or _default_chunk_loader
        self._resolve_against_document = resolve_against_document

    def resolve(
        self,
        candidate: CurationPrepCandidate,
        *,
        normalized_candidate: "NormalizedCandidate",
        context: "EvidenceResolutionContext",
    ) -> list[PreparedEvidenceRecordInput]:
        # The protocol includes the normalized candidate for future adapter-aware
        # enrichment, even though this resolver currently resolves from evidence
        # references plus document chunks only when explicitly requested.
        _ = normalized_candidate

        primary_fields: set[str] = set()
        resolved_records: list[PreparedEvidenceRecordInput] = []

        document = _PreparedDocument.from_chunks(())
        load_warning: str | None = None
        if self._resolve_against_document:
            user_id = self._safe_resolve_user_id(context.prep_extraction_result_id)
            document, load_warning = self._prepare_document(context.document_id, user_id)

        for evidence_record in candidate.evidence_records:
            field_keys = list(evidence_record.field_paths)
            resolved_anchor, warnings = self._resolve_evidence_record(
                evidence_record,
                document=document,
                load_warning=load_warning,
            )
            resolved_records.append(
                PreparedEvidenceRecordInput(
                    source=CurationEvidenceSource.EXTRACTED,
                    field_keys=field_keys,
                    field_group_keys=_field_group_keys(field_keys),
                    is_primary=(
                        not field_keys
                        or any(field_key not in primary_fields for field_key in field_keys)
                    ),
                    anchor=resolved_anchor.model_dump(mode="json"),
                    warnings=warnings,
                )
            )
            primary_fields.update(field_keys)

        return resolved_records

    def _safe_resolve_user_id(self, prep_extraction_result_id: str) -> str | None:
        try:
            return self._user_id_resolver(prep_extraction_result_id)
        except Exception:  # pragma: no cover - defensive logging only
            logger.exception(
                "Evidence resolution could not resolve a user id for prep extraction result %s",
                prep_extraction_result_id,
            )
            return None

    def _prepare_document(
        self,
        document_id: str,
        user_id: str | None,
    ) -> tuple[_PreparedDocument, str | None]:
        if not user_id:
            return _PreparedDocument.from_chunks(()), (
                "Evidence resolution skipped PDFX chunk lookup because the prep extraction result has no user_id."
            )

        try:
            raw_chunks = self._chunk_loader(document_id, user_id)
        except Exception:  # pragma: no cover - defensive logging only
            logger.exception(
                "Evidence resolution failed to load PDFX chunks for document %s",
                document_id,
            )
            return _PreparedDocument.from_chunks(()), (
                "Evidence resolution could not load PDFX chunks for this document."
            )

        return _PreparedDocument.from_chunks(_coerce_resolution_chunks(raw_chunks)), None

    def _resolve_evidence_record(
        self,
        evidence_record: CurationPrepEvidenceRecord,
        *,
        document: _PreparedDocument,
        load_warning: str | None,
    ) -> tuple[EvidenceAnchor, list[str]]:
        incoming_anchor = _normalized_anchor(evidence_record.anchor)
        if not self._resolve_against_document:
            return incoming_anchor, []

        warnings: list[str] = [load_warning] if load_warning else []

        quote_resolution = _resolve_quote_reference(document, incoming_anchor)
        if quote_resolution is not None:
            warnings.extend(quote_resolution.warnings)
            return _normalized_anchor(
                _build_quote_anchor(incoming_anchor, quote_resolution)
            ), _dedupe_strings(warnings)

        section_resolution = _resolve_section_reference(document, incoming_anchor)
        if section_resolution is not None:
            warnings.extend(section_resolution.warnings)
            return _normalized_anchor(
                _build_section_anchor(incoming_anchor, section_resolution)
            ), _dedupe_strings(warnings)

        if _has_existing_location(incoming_anchor):
            return incoming_anchor, _dedupe_strings(warnings)

        if _is_document_only_anchor(incoming_anchor):
            return _normalized_anchor(_build_document_anchor(incoming_anchor)), _dedupe_strings(
                warnings
            )

        if incoming_anchor.page_number is not None:
            return _normalized_anchor(_build_page_anchor(incoming_anchor)), _dedupe_strings(
                warnings
            )

        return _normalized_anchor(_build_unresolved_anchor(incoming_anchor)), _dedupe_strings(
            warnings
        )

    def _resolve_user_id(self, prep_extraction_result_id: str) -> str | None:
        with self._session_factory() as session:
            result = session.scalar(
                select(ExtractionResultModel.user_id).where(
                    ExtractionResultModel.id == prep_extraction_result_id
                )
            )
            return (str(result).strip() or None) if result is not None else None


def _resolve_quote_reference(
    document: _PreparedDocument,
    anchor: EvidenceAnchor,
) -> _QuoteResolution | None:
    if not document.raw_text:
        return None

    raw_quote = _quote_source_text(anchor)
    normalized_quote = _normalized_quote_text(anchor)
    if not raw_quote and not normalized_quote:
        return None

    candidates = _build_quote_candidates(anchor)
    for candidate in candidates:
        if candidate.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE:
            spans = _resolve_raw_quote_spans(document, candidate.query)
            viewer_search_text = candidate.query
        else:
            normalized_query = _normalize_text(candidate.query)
            if not normalized_query:
                continue
            spans = _resolve_normalized_quote_spans(document, normalized_query)
            viewer_search_text = normalized_query

        if not spans:
            continue

        selected_span = _select_best_span(spans, anchor)
        warnings: tuple[str, ...] = ()
        if len(spans) > 1:
            warnings = (
                "Multiple PDFX quote matches found; selected the closest page/section-biased span.",
            )

        return _QuoteResolution(
            locator_quality=candidate.locator_quality,
            viewer_search_text=viewer_search_text,
            normalized_text=normalized_quote,
            matched_text=selected_span.matched_text,
            chunk_ids=selected_span.chunk_ids,
            page_number=selected_span.page_number,
            section_title=selected_span.section_title,
            subsection_title=selected_span.subsection_title,
            fragment=candidate.fragment,
            warnings=warnings,
        )

    return None


def _resolve_section_reference(
    document: _PreparedDocument,
    anchor: EvidenceAnchor,
) -> _SectionResolution | None:
    if not document.chunks:
        return None

    section_candidates = _build_section_candidates(anchor)
    for section_candidate, is_subsection in section_candidates:
        matches: list[tuple[int, _ResolutionChunk]] = []
        for chunk in document.chunks:
            score = _section_match_score(section_candidate, chunk)
            if score:
                matches.append((score, chunk))

        if not matches:
            continue

        matches.sort(
            key=lambda item: (
                -item[0],
                _page_distance(item[1].page_number, anchor.page_number),
                item[1].chunk_index,
            )
        )
        selected_chunk = matches[0][1]
        warnings: tuple[str, ...] = ()
        if len(matches) > 1:
            warnings = (
                "Multiple PDFX section matches found; selected the closest page-biased section chunk.",
            )

        return _SectionResolution(
            chunk_ids=(selected_chunk.id,),
            page_number=selected_chunk.page_number or anchor.page_number,
            section_title=selected_chunk.section_title
            or selected_chunk.parent_section
            or anchor.section_title,
            subsection_title=selected_chunk.subsection if is_subsection else anchor.subsection_title,
            warnings=warnings,
        )

    return None


def _build_quote_anchor(
    incoming_anchor: EvidenceAnchor,
    resolution: _QuoteResolution,
) -> EvidenceAnchor:
    raw_quote = _quote_source_text(incoming_anchor)
    snippet_text = _first_non_empty(incoming_anchor.snippet_text)
    if snippet_text is None and not resolution.fragment:
        snippet_text = resolution.matched_text

    sentence_text = _first_non_empty(incoming_anchor.sentence_text)
    if sentence_text is None and incoming_anchor.anchor_kind is EvidenceAnchorKind.SENTENCE:
        sentence_text = resolution.matched_text

    figure_reference = _coalesce_reference(
        incoming_anchor.figure_reference,
        resolution.matched_text,
        FIGURE_REFERENCE_PATTERN,
    )
    table_reference = _coalesce_reference(
        incoming_anchor.table_reference,
        resolution.matched_text,
        TABLE_REFERENCE_PATTERN,
    )

    return EvidenceAnchor(
        anchor_kind=_anchor_kind_for_quality(
            incoming_anchor,
            resolution.locator_quality,
            raw_quote_present=bool(raw_quote),
        ),
        locator_quality=resolution.locator_quality,
        supports_decision=incoming_anchor.supports_decision,
        snippet_text=snippet_text,
        sentence_text=sentence_text,
        normalized_text=resolution.normalized_text,
        viewer_search_text=resolution.viewer_search_text,
        page_number=resolution.page_number or incoming_anchor.page_number,
        page_label=incoming_anchor.page_label,
        section_title=resolution.section_title or incoming_anchor.section_title,
        subsection_title=resolution.subsection_title or incoming_anchor.subsection_title,
        figure_reference=figure_reference,
        table_reference=table_reference,
        chunk_ids=list(resolution.chunk_ids),
    )


def _build_section_anchor(
    incoming_anchor: EvidenceAnchor,
    resolution: _SectionResolution,
) -> EvidenceAnchor:
    return EvidenceAnchor(
        anchor_kind=_anchor_kind_for_quality(
            incoming_anchor,
            EvidenceLocatorQuality.SECTION_ONLY,
            raw_quote_present=bool(_quote_source_text(incoming_anchor)),
        ),
        locator_quality=EvidenceLocatorQuality.SECTION_ONLY,
        supports_decision=incoming_anchor.supports_decision,
        snippet_text=_first_non_empty(incoming_anchor.snippet_text),
        sentence_text=_first_non_empty(incoming_anchor.sentence_text),
        normalized_text=_normalized_quote_text(incoming_anchor),
        viewer_search_text=None,
        page_number=resolution.page_number,
        page_label=incoming_anchor.page_label,
        section_title=resolution.section_title or incoming_anchor.section_title,
        subsection_title=resolution.subsection_title or incoming_anchor.subsection_title,
        figure_reference=_first_non_empty(incoming_anchor.figure_reference),
        table_reference=_first_non_empty(incoming_anchor.table_reference),
        chunk_ids=list(resolution.chunk_ids),
    )


def _build_page_anchor(incoming_anchor: EvidenceAnchor) -> EvidenceAnchor:
    return EvidenceAnchor(
        anchor_kind=EvidenceAnchorKind.PAGE,
        locator_quality=EvidenceLocatorQuality.PAGE_ONLY,
        supports_decision=incoming_anchor.supports_decision,
        snippet_text=_first_non_empty(incoming_anchor.snippet_text),
        sentence_text=_first_non_empty(incoming_anchor.sentence_text),
        normalized_text=_normalized_quote_text(incoming_anchor),
        viewer_search_text=None,
        page_number=incoming_anchor.page_number,
        page_label=incoming_anchor.page_label,
        section_title=None,
        subsection_title=None,
        figure_reference=_first_non_empty(incoming_anchor.figure_reference),
        table_reference=_first_non_empty(incoming_anchor.table_reference),
        chunk_ids=[],
    )


def _build_document_anchor(incoming_anchor: EvidenceAnchor) -> EvidenceAnchor:
    return EvidenceAnchor(
        anchor_kind=EvidenceAnchorKind.DOCUMENT,
        locator_quality=EvidenceLocatorQuality.DOCUMENT_ONLY,
        supports_decision=incoming_anchor.supports_decision,
        snippet_text=_first_non_empty(incoming_anchor.snippet_text),
        sentence_text=_first_non_empty(incoming_anchor.sentence_text),
        normalized_text=_normalized_quote_text(incoming_anchor),
        viewer_search_text=None,
        page_number=None,
        page_label=None,
        section_title=None,
        subsection_title=None,
        figure_reference=_first_non_empty(incoming_anchor.figure_reference),
        table_reference=_first_non_empty(incoming_anchor.table_reference),
        chunk_ids=[],
    )


def _build_unresolved_anchor(incoming_anchor: EvidenceAnchor) -> EvidenceAnchor:
    return EvidenceAnchor(
        anchor_kind=_anchor_kind_for_quality(
            incoming_anchor,
            EvidenceLocatorQuality.UNRESOLVED,
            raw_quote_present=bool(_quote_source_text(incoming_anchor)),
        ),
        locator_quality=EvidenceLocatorQuality.UNRESOLVED,
        supports_decision=incoming_anchor.supports_decision,
        snippet_text=_first_non_empty(incoming_anchor.snippet_text),
        sentence_text=_first_non_empty(incoming_anchor.sentence_text),
        normalized_text=_normalized_quote_text(incoming_anchor),
        viewer_search_text=None,
        page_number=None,
        page_label=None,
        section_title=None,
        subsection_title=None,
        figure_reference=_first_non_empty(incoming_anchor.figure_reference),
        table_reference=_first_non_empty(incoming_anchor.table_reference),
        chunk_ids=[],
    )


def _build_quote_candidates(anchor: EvidenceAnchor) -> list[_QuoteCandidate]:
    raw_quote = _quote_source_text(anchor)
    normalized_quote = _normalized_quote_text(anchor)
    if not raw_quote and not normalized_quote:
        return []

    base_quote = raw_quote or normalized_quote or ""
    whitespace_normalized = re.sub(r"\s+", " ", base_quote).strip()
    ascii_normalized = normalized_quote or _normalize_text(base_quote)
    first_sentence = _extract_first_sentence(base_quote)
    words = _split_words(base_quote)

    candidates = [
        _QuoteCandidate(
            query=base_quote,
            locator_quality=EvidenceLocatorQuality.EXACT_QUOTE,
            fragment=False,
        ),
        _QuoteCandidate(
            query=whitespace_normalized,
            locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
            fragment=False,
        ),
        _QuoteCandidate(
            query=ascii_normalized,
            locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
            fragment=False,
        ),
    ]

    for extra_query in (
        _first_non_empty(anchor.viewer_search_text),
        _first_non_empty(anchor.sentence_text),
    ):
        if extra_query:
            candidates.append(
                _QuoteCandidate(
                    query=_normalize_text(extra_query),
                    locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
                    fragment=False,
                )
            )

    if first_sentence:
        candidates.append(
            _QuoteCandidate(
                query=first_sentence,
                locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
                fragment=True,
            )
        )

    if len(words) > QUOTE_FRAGMENT_WORDS + 6:
        candidates.append(
            _QuoteCandidate(
                query=" ".join(words[:QUOTE_FRAGMENT_WORDS]),
                locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
                fragment=True,
            )
        )
        candidates.append(
            _QuoteCandidate(
                query=" ".join(words[-QUOTE_FRAGMENT_WORDS:]),
                locator_quality=EvidenceLocatorQuality.NORMALIZED_QUOTE,
                fragment=True,
            )
        )

    deduped: list[_QuoteCandidate] = []
    seen_queries: set[str] = set()
    for candidate in candidates:
        normalized_query = candidate.query.strip().lower()
        if not normalized_query or normalized_query in seen_queries:
            continue
        seen_queries.add(normalized_query)
        deduped.append(candidate)
    return deduped


def _build_section_candidates(anchor: EvidenceAnchor) -> list[tuple[str, bool]]:
    candidates: list[tuple[str, bool]] = []
    normalized_section = _first_non_empty(anchor.section_title)
    normalized_subsection = _first_non_empty(anchor.subsection_title)

    if normalized_section:
        candidates.append((normalized_section, False))

    if normalized_subsection and _normalize_text(normalized_subsection) != _normalize_text(
        normalized_section or ""
    ):
        candidates.append((normalized_subsection, True))

    return candidates


def _resolve_raw_quote_spans(document: _PreparedDocument, query: str) -> list[_ResolvedSpan]:
    if not query:
        return []

    spans: list[_ResolvedSpan] = []
    start_index = 0
    while True:
        match_index = document.raw_text.find(query, start_index)
        if match_index < 0:
            break
        spans.append(_materialize_span(document, match_index, match_index + len(query)))
        start_index = match_index + 1
    return spans


def _resolve_normalized_quote_spans(
    document: _PreparedDocument,
    query: str,
) -> list[_ResolvedSpan]:
    if not query or not document.normalized_text or not document.normalized_index_map:
        return []

    match_indexes = _find_all_indexes(document.normalized_text, query)
    if not match_indexes:
        match_indexes = _find_all_indexes(document.normalized_text.lower(), query.lower())

    resolved_spans: list[_ResolvedSpan] = []
    seen_spans: set[tuple[int, int]] = set()
    for match_index in match_indexes:
        raw_start = document.normalized_index_map[match_index]
        raw_end = document.normalized_index_map[match_index + len(query) - 1] + 1
        span_key = (raw_start, raw_end)
        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)
        resolved_spans.append(_materialize_span(document, raw_start, raw_end))
    return resolved_spans


def _materialize_span(
    document: _PreparedDocument,
    raw_start: int,
    raw_end: int,
) -> _ResolvedSpan:
    contributing_spans = [
        chunk_span
        for chunk_span in document.chunk_spans
        if raw_start < chunk_span.raw_end and raw_end > chunk_span.raw_start
    ]
    if not contributing_spans:
        raise ValueError("Resolved quote span must intersect at least one chunk")

    first_chunk = contributing_spans[0].chunk
    chunk_ids = tuple(chunk_span.chunk.id for chunk_span in contributing_spans)
    section_labels = tuple(_section_labels_for_chunk(first_chunk))
    return _ResolvedSpan(
        raw_start=raw_start,
        raw_end=raw_end,
        matched_text=document.raw_text[raw_start:raw_end],
        chunk_ids=chunk_ids,
        page_number=first_chunk.page_number,
        section_title=first_chunk.section_title or first_chunk.parent_section,
        subsection_title=first_chunk.subsection,
        section_labels=section_labels,
    )


def _select_best_span(
    spans: Sequence[_ResolvedSpan],
    anchor: EvidenceAnchor,
) -> _ResolvedSpan:
    ranked_spans = sorted(
        spans,
        key=lambda span: (
            -int(span.page_number == anchor.page_number if anchor.page_number is not None else False),
            -_label_match_strength(anchor.subsection_title, span.section_labels),
            -_label_match_strength(anchor.section_title, span.section_labels),
            _page_distance(span.page_number, anchor.page_number),
            _raw_span_length(span),
            span.raw_start,
        ),
    )
    return ranked_spans[0]


def _section_match_score(candidate: str, chunk: _ResolutionChunk) -> int:
    labels = _section_labels_for_chunk(chunk)
    return _label_match_strength(candidate, labels)


def _section_labels_for_chunk(chunk: _ResolutionChunk) -> list[str]:
    labels: list[str] = []
    for value in (
        chunk.section_title,
        chunk.parent_section,
        chunk.subsection,
        *chunk.section_path,
    ):
        normalized = _first_non_empty(value)
        if normalized and normalized not in labels:
            labels.append(normalized)
    return labels


def _label_match_strength(
    candidate: str | None,
    labels: Sequence[str],
) -> int:
    normalized_candidate = _normalize_text(candidate or "")
    if not normalized_candidate:
        return 0

    best_score = 0
    for label in labels:
        normalized_label = _normalize_text(label)
        if not normalized_label:
            continue
        if normalized_label == normalized_candidate:
            best_score = max(best_score, 2)
        elif normalized_candidate in normalized_label or normalized_label in normalized_candidate:
            best_score = max(best_score, 1)
    return best_score


def _normalized_anchor(anchor: EvidenceAnchor) -> EvidenceAnchor:
    figure_reference, table_reference = _normalized_references(
        anchor.figure_reference,
        anchor.table_reference,
    )
    return anchor.model_copy(
        update={
            "figure_reference": figure_reference,
            "table_reference": table_reference,
        }
    )


def _normalized_references(
    figure_reference: str | None,
    table_reference: str | None,
) -> tuple[str | None, str | None]:
    normalized_figure = _first_non_empty(figure_reference)
    normalized_table = _first_non_empty(table_reference)

    if normalized_figure and TABLE_REFERENCE_PATTERN.search(normalized_figure):
        return None, normalized_figure
    if normalized_table and FIGURE_REFERENCE_PATTERN.search(normalized_table):
        return normalized_table, None

    return normalized_figure, normalized_table


def _has_existing_location(anchor: EvidenceAnchor) -> bool:
    return bool(
        anchor.chunk_ids
        or anchor.page_number is not None
        or _first_non_empty(
            anchor.section_title,
            anchor.subsection_title,
            anchor.figure_reference,
            anchor.table_reference,
        )
    )


def _anchor_kind_for_quality(
    incoming_anchor: EvidenceAnchor,
    locator_quality: EvidenceLocatorQuality,
    *,
    raw_quote_present: bool,
) -> EvidenceAnchorKind:
    if locator_quality is EvidenceLocatorQuality.PAGE_ONLY:
        return EvidenceAnchorKind.PAGE
    if locator_quality is EvidenceLocatorQuality.SECTION_ONLY:
        if incoming_anchor.anchor_kind is EvidenceAnchorKind.FIGURE and incoming_anchor.figure_reference:
            return EvidenceAnchorKind.FIGURE
        if incoming_anchor.anchor_kind is EvidenceAnchorKind.TABLE and incoming_anchor.table_reference:
            return EvidenceAnchorKind.TABLE
        return EvidenceAnchorKind.SECTION
    if locator_quality is EvidenceLocatorQuality.DOCUMENT_ONLY:
        return EvidenceAnchorKind.DOCUMENT
    if locator_quality in {
        EvidenceLocatorQuality.EXACT_QUOTE,
        EvidenceLocatorQuality.NORMALIZED_QUOTE,
    }:
        if incoming_anchor.anchor_kind in {
            EvidenceAnchorKind.FIGURE,
            EvidenceAnchorKind.TABLE,
            EvidenceAnchorKind.SENTENCE,
        }:
            return incoming_anchor.anchor_kind
        if raw_quote_present:
            return EvidenceAnchorKind.SNIPPET
    return incoming_anchor.anchor_kind


def _is_document_only_anchor(anchor: EvidenceAnchor) -> bool:
    return (
        anchor.anchor_kind is EvidenceAnchorKind.DOCUMENT
        or anchor.locator_quality is EvidenceLocatorQuality.DOCUMENT_ONLY
    )


def _quote_source_text(anchor: EvidenceAnchor) -> str | None:
    return _first_non_empty(
        anchor.snippet_text,
        anchor.sentence_text,
        anchor.normalized_text,
        anchor.viewer_search_text if _can_trust_viewer_search_text(anchor) else None,
    )


def _can_trust_viewer_search_text(anchor: EvidenceAnchor) -> bool:
    return (
        anchor.locator_quality in {
            EvidenceLocatorQuality.EXACT_QUOTE,
            EvidenceLocatorQuality.NORMALIZED_QUOTE,
        }
        or anchor.anchor_kind in {
            EvidenceAnchorKind.SNIPPET,
            EvidenceAnchorKind.SENTENCE,
            EvidenceAnchorKind.FIGURE,
            EvidenceAnchorKind.TABLE,
        }
    )


def _normalized_quote_text(anchor: EvidenceAnchor) -> str | None:
    existing = _first_non_empty(anchor.normalized_text)
    if existing:
        return _normalize_text(existing)

    raw_quote = _quote_source_text(anchor)
    if raw_quote:
        return _normalize_text(raw_quote)
    return None


def _extract_first_sentence(value: str) -> str | None:
    normalized_value = _normalize_text(value)
    match = re.match(r"^(.{40,}?[.!?])(?:\s|$)", normalized_value)
    return match.group(1).strip() if match else None


def _split_words(value: str) -> list[str]:
    normalized_value = _normalize_text(value)
    return normalized_value.split() if normalized_value else []


def _normalize_text(value: str) -> str:
    return _normalize_text_with_mapping(value)[0]


def _normalize_text_with_mapping(value: str) -> tuple[str, list[int]]:
    output: list[str] = []
    index_map: list[int] = []

    for raw_index, raw_char in enumerate(value):
        for normalized_char in unicodedata.normalize("NFKC", raw_char):
            canonical_char = _canonicalize_character(normalized_char)
            if canonical_char is None:
                continue

            if canonical_char == " ":
                if not output or output[-1] == " " or output[-1] in OPENING_BRACKETS:
                    continue
                output.append(" ")
                index_map.append(raw_index)
                continue

            if canonical_char in PUNCTUATION_WITHOUT_LEADING_SPACE or canonical_char in CLOSING_BRACKETS:
                while output and output[-1] == " ":
                    output.pop()
                    index_map.pop()

            output.append(canonical_char)
            index_map.append(raw_index)

    while output and output[-1] == " ":
        output.pop()
        index_map.pop()

    return "".join(output), index_map


def _canonicalize_character(value: str) -> str | None:
    if value == "\u00ad":
        return None
    if value == "\u00a0" or value.isspace():
        return " "
    if value in DASH_CHARACTERS:
        return "-"
    if value in APOSTROPHE_CHARACTERS:
        return "'"
    if value in DOUBLE_QUOTE_CHARACTERS:
        return '"'
    return value


def _coerce_resolution_chunks(raw_chunks: Sequence[Mapping[str, Any]]) -> list[_ResolutionChunk]:
    chunks: list[_ResolutionChunk] = []
    for fallback_index, raw_chunk in enumerate(raw_chunks):
        metadata = raw_chunk.get("metadata")
        if not isinstance(metadata, Mapping):
            metadata = {}

        text = _first_non_empty(raw_chunk.get("text"), raw_chunk.get("content"))
        if not text:
            continue

        section_path = _coerce_string_tuple(
            raw_chunk.get("section_path")
            or raw_chunk.get("sectionPath")
            or metadata.get("section_path")
            or metadata.get("sectionPath")
        )
        section_title = _first_non_empty(
            raw_chunk.get("section_title"),
            raw_chunk.get("sectionTitle"),
        )
        parent_section = _first_non_empty(
            raw_chunk.get("parent_section"),
            raw_chunk.get("parentSection"),
        )
        subsection = _first_non_empty(raw_chunk.get("subsection"))

        if section_title is None and section_path:
            section_title = section_path[-1]
        if parent_section is None and section_path:
            parent_section = section_path[0]

        chunks.append(
            _ResolutionChunk(
                id=_first_non_empty(raw_chunk.get("id"), raw_chunk.get("chunk_id"))
                or f"chunk-{fallback_index + 1}",
                chunk_index=_coerce_int(raw_chunk.get("chunk_index"), raw_chunk.get("chunkIndex"))
                or fallback_index,
                text=text,
                page_number=_coerce_positive_int(
                    raw_chunk.get("page_number"),
                    raw_chunk.get("pageNumber"),
                ),
                section_title=section_title,
                parent_section=parent_section,
                subsection=subsection,
                section_path=section_path,
            )
        )

    return chunks


def _coalesce_reference(
    existing_value: str | None,
    source_text: str | None,
    pattern: re.Pattern[str],
) -> str | None:
    preserved_value = _first_non_empty(existing_value)
    if preserved_value:
        return preserved_value
    if not source_text:
        return None

    match = pattern.search(source_text)
    return match.group(0).strip() if match else None


def _find_all_indexes(haystack: str, needle: str) -> list[int]:
    indexes: list[int] = []
    start_index = 0
    while True:
        match_index = haystack.find(needle, start_index)
        if match_index < 0:
            return indexes
        indexes.append(match_index)
        start_index = match_index + 1


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    normalized_values = [
        normalized
        for normalized in (_first_non_empty(item) for item in value)
        if normalized is not None
    ]
    return tuple(normalized_values)


def _coerce_int(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            return value
    return None


def _coerce_positive_int(*values: Any) -> int | None:
    for value in values:
        coerced = _coerce_int(value)
        if coerced is not None and coerced > 0:
            return coerced
    return None


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def _page_distance(page_number: int | None, preferred_page: int | None) -> int:
    if preferred_page is None:
        return 0
    if page_number is None:
        return 10**9
    return abs(page_number - preferred_page)


def _field_group_key(field_path: str) -> str | None:
    segments = [segment for segment in field_path.split(".") if segment]
    if len(segments) <= 1:
        return None
    return ".".join(segments[:-1])


def _field_group_keys(field_paths: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    group_keys: list[str] = []
    for field_path in field_paths:
        group_key = _field_group_key(field_path)
        if not group_key or group_key in seen:
            continue
        seen.add(group_key)
        group_keys.append(group_key)
    return group_keys


def _raw_span_length(span: _ResolvedSpan) -> int:
    return span.raw_end - span.raw_start


__all__ = ["DeterministicEvidenceAnchorResolver"]
