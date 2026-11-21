"""Structural checks on one provider file's converted text (KANBAN-1976 / ALL-1460, P6).

Every check counts bytes, characters, cells, pipes, headings or tokens; none
reads words for meaning. A file is refused by the first failing check; the
few-characters result (R3) is a note only and never refuses a file. Results
are codes and measured values: the words curators read live in the screens.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class FileCheckReason(str, Enum):
    """Why a file is refused. Values are stored; never rename one."""

    TOO_LARGE = "too_large"                          # R1
    INVALID_TEXT = "invalid_text"                    # R2
    TABLE_CELLS_MISMATCH = "table_cells_mismatch"    # R4
    TABLE_ROW_TOO_WIDE = "table_row_too_wide"        # R5
    TABLE_STRAY_SEPARATOR = "table_stray_separator"  # R6
    TITLE_COUNT = "title_count"                      # R7


# Bump when a check's code changes; thresholds are added to the version separately.
# "2": the shared cell splitter now treats an even run of backslashes before a pipe as a
# cell boundary (ALL-1460 P1), which can change R4 results.
_CHECK_CODE_VERSION = "2"

_ALLOWED_CONTROL_CHARACTERS = frozenset("\t\n\r")


@dataclass(frozen=True, slots=True)
class FileProfile:
    """What the provider says about a file; core never assumes a provider's conventions."""

    # The first top-level title is the file's title, so it must have exactly one (R7).
    single_title_required: bool


@dataclass(frozen=True, slots=True)
class FileCheckLimits:
    max_bytes: int
    few_characters_below: int
    row_token_limit: int
    count_tokens: Callable[[str], int]
    # Names the token counter, so a different encoding re-checks stored R5 results.
    token_encoding: str

    @property
    def check_version(self) -> str:
        """Code version plus every threshold and the token encoding, so a change re-checks stored results."""

        thresholds = (
            f"{self.max_bytes}:{self.few_characters_below}:{self.row_token_limit}:{self.token_encoding}"
        )
        digest = hashlib.sha256(thresholds.encode("utf-8")).hexdigest()[:16]
        return f"{_CHECK_CODE_VERSION}-{digest}"


@dataclass(frozen=True, slots=True)
class FileAssessment:
    refused: bool
    reason: FileCheckReason | None = None
    measured_value: int | None = None
    limit_value: int | None = None
    # R3: set only when the readable text is below the note threshold.
    few_characters_count: int | None = None


def _refused(reason: FileCheckReason, measured: int | None = None, limit: int | None = None) -> FileAssessment:
    return FileAssessment(refused=True, reason=reason, measured_value=measured, limit_value=limit)


def assess_converted_file(content: bytes, profile: FileProfile, limits: FileCheckLimits) -> FileAssessment:
    """Run checks R1 to R7 on one converted text file."""

    from src.lib.pipeline.table_chunking import is_table_separator_row, split_table_cells

    if len(content) > limits.max_bytes:
        return _refused(FileCheckReason.TOO_LARGE, len(content), limits.max_bytes)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return _refused(FileCheckReason.INVALID_TEXT)
    bad_characters = sum(
        1 for character in text
        if character == "�"
        or (ord(character) < 32 and character not in _ALLOWED_CONTROL_CHARACTERS)
        or ord(character) == 127
    )
    if bad_characters:
        return _refused(FileCheckReason.INVALID_TEXT, bad_characters, 0)

    lines = text.splitlines()
    readable = 0
    titles = 0
    table: list[str] = []

    def check_table(rows: list[str]) -> FileAssessment | None:
        if len(rows) < 2 or not is_table_separator_row(rows[1]):
            return _refused(FileCheckReason.TABLE_CELLS_MISMATCH)
        width = len(split_table_cells(rows[0]))
        for row in rows[2:]:
            if is_table_separator_row(row):
                return _refused(FileCheckReason.TABLE_STRAY_SEPARATOR)
            cells = len(split_table_cells(row))
            if cells != width:
                return _refused(FileCheckReason.TABLE_CELLS_MISMATCH, cells, width)
        for row in [rows[0], *rows[2:]]:
            tokens = limits.count_tokens(row)
            if tokens > limits.row_token_limit:
                return _refused(FileCheckReason.TABLE_ROW_TOO_WIDE, tokens, limits.row_token_limit)
        return None

    for line in [*lines, ""]:
        stripped = line.strip()
        if stripped.startswith("|"):
            table.append(stripped)
            continue
        if table:
            outcome = check_table(table)
            if outcome is not None:
                return outcome
            readable += sum(
                len("".join(cell.split()))
                for row in [table[0], *table[2:]]
                for cell in split_table_cells(row)
            )
            table = []
        if stripped.startswith("#"):
            if stripped.startswith("# ") or stripped == "#":
                titles += 1
            continue
        readable += len("".join(stripped.split()))

    if profile.single_title_required and titles != 1:
        return _refused(FileCheckReason.TITLE_COUNT, titles, 1)
    return FileAssessment(
        refused=False,
        few_characters_count=readable if readable < limits.few_characters_below else None,
    )


def summarize_assessments(contents, profile: FileProfile, limits: FileCheckLimits) -> dict:
    """Counts by result and reason over many files' bytes; no names, ids or text."""

    from collections import Counter

    results: Counter[str] = Counter()
    notes = 0
    for content in contents:
        assessment = assess_converted_file(content, profile, limits)
        results[assessment.reason.value if assessment.refused else "ok"] += 1
        notes += 1 if assessment.few_characters_count is not None else 0
    return {
        "files": sum(results.values()),
        "results": dict(sorted(results.items())),
        "few_characters_notes": notes,
        "check_version": limits.check_version,
    }
