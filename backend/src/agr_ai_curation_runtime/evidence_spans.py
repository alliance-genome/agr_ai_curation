"""Deterministic source-text evidence spans for PDF chunks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any

EVIDENCE_SPANIZER_VERSION = "pdf_sentence_v1"
EVIDENCE_SPAN_HASH_ALGORITHM = "sha256"
EVIDENCE_SPAN_HASH_LENGTH = 8
EVIDENCE_SPAN_HASH_POLICY = (
    f"{EVIDENCE_SPAN_HASH_ALGORITHM}:{EVIDENCE_SPANIZER_VERSION}:"
    f"utf8:exact-source-text:{EVIDENCE_SPAN_HASH_LENGTH}"
)

_SPAN_ID_PATTERN = re.compile(
    r"^(?P<chunk_id>.+):s(?P<span_index>\d+):"
    r"c(?P<char_start>\d+)-c(?P<char_end>\d+):(?P<text_hash>[0-9a-f]{8})$"
)
_SENTENCE_TERMINATORS = frozenset(".!?")
_CLOSING_PUNCTUATION = frozenset("\"')]}”’")
_COMMON_ABBREVIATIONS = frozenset(
    {
        "al.",
        "approx.",
        "cf.",
        "dr.",
        "e.g.",
        "eq.",
        "etc.",
        "fig.",
        "i.e.",
        "inc.",
        "jr.",
        "mr.",
        "mrs.",
        "ms.",
        "no.",
        "prof.",
        "sr.",
        "vs.",
    }
)


class EvidenceSpanResolutionError(ValueError):
    """Raised when a span ID does not resolve against exact chunk text."""


@dataclass(frozen=True)
class EvidenceSpan:
    span_id: str
    span_index: int
    span_type: str
    text: str
    char_start: int
    char_end: int
    page_number: int | None = None
    section_title: str | None = None
    spanizer_version: str = EVIDENCE_SPANIZER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedEvidenceSpanId:
    chunk_id: str
    span_index: int
    char_start: int
    char_end: int
    text_hash: str


def _span_text_hash(text: str) -> str:
    digest = hashlib.sha256()
    digest.update(EVIDENCE_SPANIZER_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()[:EVIDENCE_SPAN_HASH_LENGTH]


def _format_span_id(
    *,
    chunk_id: str,
    span_index: int,
    char_start: int,
    char_end: int,
    text: str,
) -> str:
    text_hash = _span_text_hash(text)
    return f"{chunk_id}:s{span_index:04d}:c{char_start:04d}-c{char_end:04d}:{text_hash}"


def parse_evidence_span_id(span_id: str) -> ParsedEvidenceSpanId:
    if not isinstance(span_id, str):
        raise TypeError("span_id must be a string")

    match = _SPAN_ID_PATTERN.match(span_id)
    if not match:
        raise EvidenceSpanResolutionError("Invalid evidence span ID format")

    return ParsedEvidenceSpanId(
        chunk_id=match.group("chunk_id"),
        span_index=int(match.group("span_index")),
        char_start=int(match.group("char_start")),
        char_end=int(match.group("char_end")),
        text_hash=match.group("text_hash"),
    )


def build_evidence_spans(
    *,
    chunk_id: str,
    chunk_text: str,
    page_number: int | None = None,
    section_title: str | None = None,
) -> list[EvidenceSpan]:
    """Split raw chunk text into deterministic exact-text sentence spans."""

    spans: list[EvidenceSpan] = []
    for span_index, (char_start, char_end) in enumerate(_iter_sentence_offsets(chunk_text)):
        text = chunk_text[char_start:char_end]
        spans.append(
            EvidenceSpan(
                span_id=_format_span_id(
                    chunk_id=chunk_id,
                    span_index=span_index,
                    char_start=char_start,
                    char_end=char_end,
                    text=text,
                ),
                span_index=span_index,
                span_type="sentence",
                text=text,
                char_start=char_start,
                char_end=char_end,
                page_number=page_number,
                section_title=section_title,
            )
        )
    return spans


def resolve_evidence_span_id(
    *,
    span_id: str,
    chunk_text: str,
    expected_chunk_id: str | None = None,
    page_number: int | None = None,
    section_title: str | None = None,
) -> EvidenceSpan:
    """Resolve a span ID by chunk ID, offsets, and exact source-text hash."""

    parsed = parse_evidence_span_id(span_id)
    if expected_chunk_id is not None and parsed.chunk_id != expected_chunk_id:
        raise EvidenceSpanResolutionError("Evidence span chunk ID does not match chunk")

    if parsed.char_start < 0 or parsed.char_end <= parsed.char_start:
        raise EvidenceSpanResolutionError("Evidence span offsets are invalid")

    if parsed.char_end > len(chunk_text):
        raise EvidenceSpanResolutionError("Evidence span offsets exceed chunk text length")

    text = chunk_text[parsed.char_start:parsed.char_end]
    if _span_text_hash(text) != parsed.text_hash:
        raise EvidenceSpanResolutionError("Evidence span text hash does not match chunk text")

    return EvidenceSpan(
        span_id=span_id,
        span_index=parsed.span_index,
        span_type="sentence",
        text=text,
        char_start=parsed.char_start,
        char_end=parsed.char_end,
        page_number=page_number,
        section_title=section_title,
    )


def _iter_sentence_offsets(text: str) -> list[tuple[int, int]]:
    if not text:
        return []

    start = _next_non_whitespace(text, 0)
    if start >= len(text):
        return []

    spans: list[tuple[int, int]] = []
    cursor = start
    index = start
    while index < len(text):
        if text[index] not in _SENTENCE_TERMINATORS:
            index += 1
            continue

        end = index + 1
        while end < len(text) and text[end] in _CLOSING_PUNCTUATION:
            end += 1

        next_start = _next_non_whitespace(text, end)
        if next_start >= len(text):
            break

        if _is_sentence_boundary(text, punctuation_index=index, next_start=next_start):
            if end > cursor:
                spans.append((cursor, end))
            cursor = next_start
            index = next_start
            continue

        index = end

    final_end = _previous_non_whitespace_end(text, len(text))
    if cursor < final_end:
        spans.append((cursor, final_end))
    return spans


def _next_non_whitespace(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _previous_non_whitespace_end(text: str, end: int) -> int:
    index = end
    while index > 0 and text[index - 1].isspace():
        index -= 1
    return index


def _is_sentence_boundary(text: str, *, punctuation_index: int, next_start: int) -> bool:
    if not _looks_like_sentence_start(text[next_start]):
        return False

    if _is_decimal_point(text, punctuation_index):
        return False

    if _is_abbreviation_period(text, punctuation_index):
        return False

    return True


def _looks_like_sentence_start(char: str) -> bool:
    return char.isupper() or char.isdigit() or char in "\"'([{“‘"


def _is_decimal_point(text: str, punctuation_index: int) -> bool:
    return (
        text[punctuation_index] == "."
        and punctuation_index > 0
        and punctuation_index + 1 < len(text)
        and text[punctuation_index - 1].isdigit()
        and text[punctuation_index + 1].isdigit()
    )


def _is_abbreviation_period(text: str, punctuation_index: int) -> bool:
    if text[punctuation_index] != ".":
        return False

    tail = text[max(0, punctuation_index - 16): punctuation_index + 1].lower()
    if any(tail.endswith(abbreviation) for abbreviation in _COMMON_ABBREVIATIONS):
        return True

    prefix = text[:punctuation_index].rstrip()
    token_match = re.search(r"([A-Za-z]+)$", prefix)
    return bool(token_match and len(token_match.group(1)) == 1)


__all__ = [
    "EVIDENCE_SPAN_HASH_ALGORITHM",
    "EVIDENCE_SPAN_HASH_LENGTH",
    "EVIDENCE_SPAN_HASH_POLICY",
    "EVIDENCE_SPANIZER_VERSION",
    "EvidenceSpan",
    "EvidenceSpanResolutionError",
    "ParsedEvidenceSpanId",
    "build_evidence_spans",
    "parse_evidence_span_id",
    "resolve_evidence_span_id",
]
