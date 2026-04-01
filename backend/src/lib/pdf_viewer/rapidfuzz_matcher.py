"""RapidFuzz-based quote localization against PDF.js page text."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

from rapidfuzz import fuzz


MIN_FUZZY_MATCH_SCORE = 70.0
STITCHED_CONTEXT_MIN_CHARS = 240
STITCHED_CONTEXT_MAX_CHARS = 1600
STITCHED_ADJACENT_WORD_MIN = 4
WORD_PATTERN = re.compile(r"\S+")


@dataclass(frozen=True)
class PdfPageText:
    page_number: int
    raw_text: str


@dataclass(frozen=True)
class MatchRange:
    page_number: int
    raw_start: int
    raw_end_exclusive: int
    query: str


@dataclass(frozen=True)
class FuzzyQuoteMatchResult:
    found: bool
    strategy: str
    score: float
    matched_page: int | None
    matched_query: str | None
    matched_range: MatchRange | None
    full_query: str | None
    page_ranges: tuple[MatchRange, ...]
    cross_page: bool
    note: str


@dataclass(frozen=True)
class _CorpusSlice:
    page_number: int
    raw_start: int
    raw_end_exclusive: int
    stitched_start: int
    stitched_end_exclusive: int
    text: str


@dataclass(frozen=True)
class _FuzzyCandidate:
    strategy: str
    score: float
    matched_page: int
    matched_query: str
    matched_range: MatchRange
    full_query: str
    page_ranges: tuple[MatchRange, ...]
    cross_page: bool
    anchor_word_count: int
    full_match_length: int


def _count_words(value: str) -> int:
    return len(WORD_PATTERN.findall(value))


def _page_distance(page_number: int, page_hints: Sequence[int]) -> int:
    if not page_hints:
        return 0
    return min(abs(page_number - page_hint) for page_hint in page_hints)


def _context_chars_for_quote(quote: str) -> int:
    return max(
        STITCHED_CONTEXT_MIN_CHARS,
        min(
            STITCHED_CONTEXT_MAX_CHARS,
            max(len(quote) * 2, STITCHED_CONTEXT_MIN_CHARS),
        ),
    )


def _is_token_char(value: str) -> bool:
    return value.isalnum() or value == "_"


def _expand_match_to_token_boundaries(text: str, start: int, end: int) -> tuple[int, int]:
    bounded_start = max(0, min(start, len(text)))
    bounded_end = max(bounded_start, min(end, len(text)))

    while (
        bounded_start > 0
        and bounded_start < len(text)
        and _is_token_char(text[bounded_start - 1])
        and _is_token_char(text[bounded_start])
    ):
        bounded_start -= 1

    while (
        bounded_end > 0
        and bounded_end < len(text)
        and _is_token_char(text[bounded_end - 1])
        and _is_token_char(text[bounded_end])
    ):
        bounded_end += 1

    return bounded_start, bounded_end


def _map_stitched_range_to_page_ranges(
    slices: Sequence[_CorpusSlice],
    start: int,
    end: int,
) -> tuple[MatchRange, ...]:
    page_ranges: list[MatchRange] = []
    for page_slice in slices:
        overlap_start = max(start, page_slice.stitched_start)
        overlap_end = min(end, page_slice.stitched_end_exclusive)
        if overlap_start >= overlap_end:
            continue

        local_start = overlap_start - page_slice.stitched_start
        local_end = overlap_end - page_slice.stitched_start
        raw_start = page_slice.raw_start + local_start
        raw_end_exclusive = page_slice.raw_start + local_end
        query = page_slice.text[local_start:local_end]
        page_ranges.append(
            MatchRange(
                page_number=page_slice.page_number,
                raw_start=raw_start,
                raw_end_exclusive=raw_end_exclusive,
                query=query,
            ),
        )

    return tuple(page_ranges)


def _build_single_page_candidate(page: PdfPageText, quote: str) -> _FuzzyCandidate | None:
    if not page.raw_text.strip():
        return None

    alignment = fuzz.partial_ratio_alignment(quote, page.raw_text)
    start, end = _expand_match_to_token_boundaries(
        page.raw_text,
        int(alignment.dest_start),
        int(alignment.dest_end),
    )
    if end <= start:
        return None

    matched_query = page.raw_text[start:end]
    matched_range = MatchRange(
        page_number=page.page_number,
        raw_start=start,
        raw_end_exclusive=end,
        query=matched_query,
    )
    return _FuzzyCandidate(
        strategy='rapidfuzz-single-page',
        score=float(alignment.score),
        matched_page=page.page_number,
        matched_query=matched_query,
        matched_range=matched_range,
        full_query=matched_query,
        page_ranges=(matched_range,),
        cross_page=False,
        anchor_word_count=_count_words(matched_query),
        full_match_length=max(0, end - start),
    )


def _build_stitched_page_candidate(
    pages: Sequence[PdfPageText],
    center_index: int,
    quote: str,
) -> _FuzzyCandidate | None:
    center_page = pages[center_index]
    context_chars = _context_chars_for_quote(quote)
    slices: list[_CorpusSlice] = []
    stitched_offset = 0

    if center_index > 0:
        previous_page = pages[center_index - 1]
        previous_start = max(0, len(previous_page.raw_text) - context_chars)
        previous_text = previous_page.raw_text[previous_start:]
        if previous_text:
            slices.append(
                _CorpusSlice(
                    page_number=previous_page.page_number,
                    raw_start=previous_start,
                    raw_end_exclusive=len(previous_page.raw_text),
                    stitched_start=stitched_offset,
                    stitched_end_exclusive=stitched_offset + len(previous_text),
                    text=previous_text,
                ),
            )
            stitched_offset += len(previous_text)

    if center_page.raw_text:
        slices.append(
            _CorpusSlice(
                page_number=center_page.page_number,
                raw_start=0,
                raw_end_exclusive=len(center_page.raw_text),
                stitched_start=stitched_offset,
                stitched_end_exclusive=stitched_offset + len(center_page.raw_text),
                text=center_page.raw_text,
            ),
        )
        stitched_offset += len(center_page.raw_text)

    if center_index + 1 < len(pages):
        next_page = pages[center_index + 1]
        next_end = min(len(next_page.raw_text), context_chars)
        next_text = next_page.raw_text[:next_end]
        if next_text:
            slices.append(
                _CorpusSlice(
                    page_number=next_page.page_number,
                    raw_start=0,
                    raw_end_exclusive=next_end,
                    stitched_start=stitched_offset,
                    stitched_end_exclusive=stitched_offset + len(next_text),
                    text=next_text,
                ),
            )

    stitched_text = ''.join(page_slice.text for page_slice in slices)
    if not stitched_text.strip():
        return None

    alignment = fuzz.partial_ratio_alignment(quote, stitched_text)
    start, end = _expand_match_to_token_boundaries(
        stitched_text,
        int(alignment.dest_start),
        int(alignment.dest_end),
    )
    if end <= start:
        return None

    page_ranges = _map_stitched_range_to_page_ranges(slices, start, end)
    if not page_ranges:
        return None

    center_page_range = next(
        (page_range for page_range in page_ranges if page_range.page_number == center_page.page_number),
        None,
    )
    if center_page_range is None or not center_page_range.query.strip():
        return None

    adjacent_word_count = sum(
        _count_words(page_range.query)
        for page_range in page_ranges
        if page_range.page_number != center_page.page_number
    )
    cross_page = len(page_ranges) > 1 and adjacent_word_count >= STITCHED_ADJACENT_WORD_MIN

    return _FuzzyCandidate(
        strategy='rapidfuzz-stitched-page',
        score=float(alignment.score),
        matched_page=center_page.page_number,
        matched_query=center_page_range.query,
        matched_range=center_page_range,
        full_query=stitched_text[start:end],
        page_ranges=page_ranges,
        cross_page=cross_page,
        anchor_word_count=_count_words(center_page_range.query),
        full_match_length=max(0, end - start),
    )


def _candidate_sort_key(candidate: _FuzzyCandidate, page_hints: Sequence[int]) -> tuple[float, int, int, int, int, int]:
    distance = _page_distance(candidate.matched_page, page_hints)
    return (
        candidate.score,
        1 if page_hints and distance == 0 else 0,
        -distance,
        candidate.anchor_word_count,
        candidate.full_match_length,
        1 if candidate.strategy == 'rapidfuzz-single-page' else 0,
    )


def match_quote_to_pdf_pages(
    quote: str,
    pages: Sequence[PdfPageText],
    *,
    page_hints: Sequence[int] | None = None,
    min_score: float = MIN_FUZZY_MATCH_SCORE,
) -> FuzzyQuoteMatchResult:
    normalized_quote = quote.strip()
    normalized_page_hints = tuple(page_hint for page_hint in page_hints or () if page_hint >= 1)

    if not normalized_quote:
        return FuzzyQuoteMatchResult(
            found=False,
            strategy='none',
            score=0.0,
            matched_page=None,
            matched_query=None,
            matched_range=None,
            full_query=None,
            page_ranges=(),
            cross_page=False,
            note='No quote text was provided for fuzzy PDF evidence matching.',
        )

    candidates: list[_FuzzyCandidate] = []
    for index, page in enumerate(pages):
        single_page_candidate = _build_single_page_candidate(page, normalized_quote)
        if single_page_candidate is not None:
            candidates.append(single_page_candidate)

        stitched_page_candidate = _build_stitched_page_candidate(pages, index, normalized_quote)
        if stitched_page_candidate is not None:
            candidates.append(stitched_page_candidate)

    if not candidates:
        return FuzzyQuoteMatchResult(
            found=False,
            strategy='none',
            score=0.0,
            matched_page=None,
            matched_query=None,
            matched_range=None,
            full_query=None,
            page_ranges=(),
            cross_page=False,
            note='No PDF.js page text was available for fuzzy PDF evidence matching.',
        )

    best_candidate = max(
        candidates,
        key=lambda candidate: _candidate_sort_key(candidate, normalized_page_hints),
    )

    if best_candidate.score < min_score:
        return FuzzyQuoteMatchResult(
            found=False,
            strategy=best_candidate.strategy,
            score=best_candidate.score,
            matched_page=best_candidate.matched_page,
            matched_query=best_candidate.matched_query,
            matched_range=best_candidate.matched_range,
            full_query=best_candidate.full_query,
            page_ranges=best_candidate.page_ranges,
            cross_page=best_candidate.cross_page,
            note=(
                f'Best RapidFuzz candidate scored {best_candidate.score:.2f}, '
                f'which is below the acceptance threshold of {min_score:.2f}.'
            ),
        )

    return FuzzyQuoteMatchResult(
        found=True,
        strategy=best_candidate.strategy,
        score=best_candidate.score,
        matched_page=best_candidate.matched_page,
        matched_query=best_candidate.matched_query,
        matched_range=best_candidate.matched_range,
        full_query=best_candidate.full_query,
        page_ranges=best_candidate.page_ranges,
        cross_page=best_candidate.cross_page,
        note='Localized quote text against PDF.js page text using RapidFuzz.',
    )
