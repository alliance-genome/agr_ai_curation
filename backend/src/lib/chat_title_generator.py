"""Best-effort helpers for generating durable chat titles."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Iterable


TITLE_MAX_LENGTH = 80

_WHITESPACE_RE = re.compile(r"\s+")
_FIRST_CLAUSE_SPLIT_RE = re.compile(r"(?:\r?\n)+|(?<=[.!?])\s+")
_LOW_SIGNAL_PATTERNS = (
    re.compile(r"^(?:hi|hello|hey)(?: there)?[.!?]*$", re.IGNORECASE),
    re.compile(r"^(?:thanks|thank you)[.!?]*$", re.IGNORECASE),
    re.compile(r"^(?:ok|okay|got it|sounds good)[.!?]*$", re.IGNORECASE),
    re.compile(r"^flow execution summary for follow-up questions[.!?]*$", re.IGNORECASE),
)


@dataclass(frozen=True)
class ChatTitleSource:
    """One transcript snippet that can be considered for title generation."""

    role: str
    content: str


def _strip_surrounding_quotes(value: str) -> str:
    normalized = value
    while len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in "\"'`":
        normalized = normalized[1:-1].strip()
    return normalized


def _truncate_preserving_words(value: str, *, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    if max_length <= 3:
        return value[:max_length]

    cutoff = max_length - 3
    truncated = value[:cutoff].rstrip()
    if " " in truncated:
        truncated = truncated[:truncated.rfind(" ")].rstrip()
    truncated = truncated.rstrip(" -:;,.")
    if not truncated:
        truncated = value[:cutoff].rstrip(" -:;,.")
    return f"{truncated}..."


def normalize_generated_chat_title(
    value: str | None,
    *,
    max_length: int = TITLE_MAX_LENGTH,
) -> str | None:
    """Normalize one candidate title and reject blank or low-signal values."""

    if value is None:
        return None

    normalized = html.unescape(value).replace("\u00a0", " ")
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    if not normalized:
        return None

    normalized = _strip_surrounding_quotes(normalized)
    normalized = _FIRST_CLAUSE_SPLIT_RE.split(normalized, maxsplit=1)[0].strip()
    normalized = normalized.strip(" -:;,.")
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    if not normalized:
        return None

    if any(pattern.fullmatch(normalized) for pattern in _LOW_SIGNAL_PATTERNS):
        return None

    alnum_count = len(re.sub(r"[^A-Za-z0-9]+", "", normalized))
    if alnum_count < 3:
        return None

    return _truncate_preserving_words(normalized, max_length=max_length)


def generate_chat_title(sources: Iterable[ChatTitleSource]) -> str | None:
    """Return the first valid title, preferring user prompts over replies."""

    ordered_sources = list(sources)
    for preferred_role in ("user", "assistant", "flow"):
        for source in ordered_sources:
            if source.role != preferred_role:
                continue
            title = normalize_generated_chat_title(source.content)
            if title is not None:
                return title
    return None
