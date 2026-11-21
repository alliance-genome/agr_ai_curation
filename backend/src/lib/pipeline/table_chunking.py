"""Whole-row chunking of Markdown tables.

A table element is cut into pieces only between rows, never inside one. Each
piece records which rows it holds and the table's header rows, so readers can
show the column names beside any piece without copying them into stored text.

Everything here reads table structure only: line breaks, unescaped ``|`` cell
separators, the Markdown separator line and whether a cell is empty. Nothing
reads cell words for meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Optional

from src.lib.markdown_plain_text import split_table_row_cells
from src.lib.openai_agents.config import (
    get_document_table_header_max_rows,
    get_document_table_header_min_filled_fraction,
)

_SEPARATOR_CELL = re.compile(r"^\s*:?-+:?\s*$")
_LINE_BREAK_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_SPACE_RUN = re.compile(r"[ \t]{2,}")


@dataclass(frozen=True)
class TableHeaderRule:
    """Parameters of the header rule, read from settings in one place."""

    max_rows: int
    min_filled_fraction: float


def current_table_header_rule() -> TableHeaderRule:
    """Return the header rule configured for this process."""

    return TableHeaderRule(
        max_rows=get_document_table_header_max_rows(),
        min_filled_fraction=get_document_table_header_min_filled_fraction(),
    )


@dataclass(frozen=True)
class TableRowsSegment:
    """Rows of one table held in one piece, with offsets into the piece text."""

    table_index: int
    char_start: int
    char_end: int
    first_data_row: Optional[int]
    last_data_row: Optional[int]
    total_data_rows: int
    header_row_count: int
    header_text: str
    separator_char_start: Optional[int]

    def to_metadata(self) -> dict:
        return {
            "table_index": self.table_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "first_data_row": self.first_data_row,
            "last_data_row": self.last_data_row,
            "total_data_rows": self.total_data_rows,
            "header_row_count": self.header_row_count,
            "header_text": self.header_text,
            "separator_char_start": self.separator_char_start,
        }


@dataclass(frozen=True)
class TablePiece:
    """One piece of a table: its text and the rows it holds."""

    text: str
    first_data_row: Optional[int]
    last_data_row: Optional[int]
    separator_offset: Optional[int]


@dataclass(frozen=True)
class TableLayout:
    """A table split into lines, with the header rows counted."""

    lines: List[str]
    separator_line_index: Optional[int]
    header_row_count: int
    total_data_rows: int

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def header_text(self) -> str:
        return "\n".join(
            line
            for line_index, line in enumerate(self.lines)
            if line_index != self.separator_line_index
            and self._row_number(line_index) <= self.header_row_count
        )

    def _row_number(self, line_index: int) -> int:
        """1-based row number of a line; the separator line is not a row."""

        if self.separator_line_index is not None and line_index > self.separator_line_index:
            return line_index
        return line_index + 1

    def data_row_number(self, line_index: int) -> Optional[int]:
        """1-based data row number of a line, or None for header rows and the separator."""

        if line_index == self.separator_line_index:
            return None
        row_number = self._row_number(line_index) - self.header_row_count
        return row_number if row_number >= 1 else None

    def segment(
        self,
        *,
        table_index: int,
        char_start: int,
        piece: TablePiece,
    ) -> TableRowsSegment:
        return TableRowsSegment(
            table_index=table_index,
            char_start=char_start,
            char_end=char_start + len(piece.text),
            first_data_row=piece.first_data_row,
            last_data_row=piece.last_data_row,
            total_data_rows=self.total_data_rows,
            header_row_count=self.header_row_count,
            header_text=self.header_text,
            separator_char_start=(
                None if piece.separator_offset is None else char_start + piece.separator_offset
            ),
        )

    def separator_offset(self, line_indexes: List[int]) -> Optional[int]:
        """Offset of the separator line in a piece holding these lines, if it holds it.

        The separator is always line 1, packed right after line 0, which
        starts the table's first piece.
        """
        if self.separator_line_index is None or self.separator_line_index not in line_indexes:
            return None
        return len(self.lines[0]) + 1

    def whole_table_piece(self) -> TablePiece:
        data_rows = [
            number
            for number in (self.data_row_number(index) for index in range(len(self.lines)))
            if number is not None
        ]
        return TablePiece(
            text=self.text,
            first_data_row=data_rows[0] if data_rows else None,
            last_data_row=data_rows[-1] if data_rows else None,
            separator_offset=self.separator_offset(list(range(len(self.lines)))),
        )


def split_table_cells(row: str) -> List[str]:
    """Split one Markdown table row into its cells, without the outer pipes.

    Which pipes separate cells is decided by ``split_table_row_cells``, the one
    splitter the stored-text clean-up also uses: a pipe after an odd number of
    backslashes is part of its cell, and an escaped backslash before a pipe
    (``\\\\|``) does not escape that pipe.
    """

    cells = split_table_row_cells(row.strip())
    if len(cells) > 1 and cells[0] == "":
        cells = cells[1:]
    if len(cells) > 1 and cells[-1] == "":
        cells = cells[:-1]
    return cells


def table_row_search_text(row: str) -> str:
    """The text a PDF shows for one stored table row: its cells joined by single spaces.

    Cells come from ``split_table_cells`` (the one shared splitter). In each cell
    the escapes stored text keeps (``\\|`` and ``\\\\``) are undone, the cell is
    trimmed, and empty cells are left out. Used only to search a PDF's text layer
    for table-row evidence; the stored quote is never changed.
    """

    parts = []
    for cell in split_table_cells(row):
        unescaped: list[str] = []
        index = 0
        while index < len(cell):
            if cell[index] == "\\" and index + 1 < len(cell) and cell[index + 1] in "\\|":
                unescaped.append(cell[index + 1])
                index += 2
            else:
                unescaped.append(cell[index])
                index += 1
        text = "".join(unescaped).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def is_table_separator_row(row: str) -> bool:
    """Whether a line is a Markdown table separator line such as ``|---|:-:|``."""

    cells = split_table_cells(row)
    return bool(cells) and all(_SEPARATOR_CELL.match(cell) for cell in cells)


def normalize_table_row(row: str) -> str:
    """Turn ``<br>`` into a space and runs of spaces into one, inside a row."""

    return _SPACE_RUN.sub(" ", _LINE_BREAK_TAG.sub(" ", row)).strip()


def count_table_header_rows(rows: List[str], rule: TableHeaderRule) -> int:
    """The header rule: count the header rows of a table.

    ``rows`` are the table's rows in source order without the separator line;
    the first is the Markdown header row. The header is that row plus each
    following row up to and including the first row whose filled share of
    cells is at least ``rule.min_filled_fraction``, and never more than
    ``rule.max_rows`` rows. Only empty cells are counted.
    """

    header_rows = 0
    for row in rows[: rule.max_rows]:
        header_rows += 1
        cells = split_table_cells(row)
        filled = sum(1 for cell in cells if cell.strip())
        if filled >= rule.min_filled_fraction * len(cells):
            break
    return header_rows


def layout_table(table_text: str, rule: TableHeaderRule) -> TableLayout:
    """Split a table element's text into normalized lines and count its header."""

    lines = [normalize_table_row(line) for line in table_text.split("\n")]
    lines = [line for line in lines if line]
    separator_line_index = 1 if len(lines) > 1 and is_table_separator_row(lines[1]) else None
    rows = [line for index, line in enumerate(lines) if index != separator_line_index]
    header_row_count = count_table_header_rows(rows, rule) if rows else 0
    return TableLayout(
        lines=lines,
        separator_line_index=separator_line_index,
        header_row_count=header_row_count,
        total_data_rows=len(rows) - header_row_count,
    )


def pack_table_rows(layout: TableLayout, max_characters: int) -> List[TablePiece]:
    """Pack whole rows into pieces of at most ``max_characters``.

    The separator line always stays with the Markdown header row above it. A
    row longer than ``max_characters`` becomes a piece of its own, whole.
    """

    units: List[tuple[str, List[int]]] = []
    for line_index, line in enumerate(layout.lines):
        if line_index == layout.separator_line_index and units:
            previous_text, previous_lines = units[-1]
            units[-1] = (f"{previous_text}\n{line}", previous_lines + [line_index])
            continue
        units.append((line, [line_index]))

    pieces: List[TablePiece] = []
    current_text = ""
    current_lines: List[int] = []

    def flush() -> None:
        data_rows = [
            number
            for number in (layout.data_row_number(index) for index in current_lines)
            if number is not None
        ]
        pieces.append(
            TablePiece(
                text=current_text,
                first_data_row=data_rows[0] if data_rows else None,
                last_data_row=data_rows[-1] if data_rows else None,
                separator_offset=layout.separator_offset(current_lines),
            )
        )

    for unit_text, unit_lines in units:
        if current_text and len(current_text) + 1 + len(unit_text) > max_characters:
            flush()
            current_text = ""
            current_lines = []
        current_text = f"{current_text}\n{unit_text}" if current_text else unit_text
        current_lines.extend(unit_lines)
    if current_text:
        flush()
    return pieces


__all__ = [
    "TableHeaderRule",
    "TableLayout",
    "TablePiece",
    "TableRowsSegment",
    "count_table_header_rows",
    "current_table_header_rule",
    "is_table_separator_row",
    "layout_table",
    "normalize_table_row",
    "pack_table_rows",
    "split_table_cells",
    "table_row_search_text",
]
