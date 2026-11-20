"""Markdown table structure shared by chunking and evidence spans.

Structure only: unescaped ``|`` cell separators and the separator line. Nothing
here reads cell words.
"""

from __future__ import annotations

import re
from typing import List

_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_SEPARATOR_CELL = re.compile(r"^\s*:?-+:?\s*$")


def split_table_cells(row: str) -> List[str]:
    """Split one Markdown table row into cells on unescaped pipes."""

    stripped = row.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return _UNESCAPED_PIPE.split(stripped)


def is_table_separator_row(row: str) -> bool:
    """Whether a line is a Markdown table separator line such as ``|---|:-:|``."""

    cells = split_table_cells(row)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)


__all__ = ["is_table_separator_row", "split_table_cells"]
