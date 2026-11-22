"""Display blocks for a text file, built from its processed elements.

The processed elements are the file's text after the stored-text clean-up, before
it is cut into pieces. What a viewer may rely on when locating a quote (an exact
slice of one stored piece):

- every table row ``line`` is exactly one line of a stored piece; a table's rows can
  be spread over several pieces, whole rows only;
- block texts joined to their neighbours with a blank line (``"\n\n"``) contain
  every stored piece exactly, so a quote can cross blocks;
- except where an element longer than the chunk limit was cut at a sentence
  boundary: there the piece holds one space where the block holds the original
  whitespace.

Everything here reads structure only: element types, structural metadata and table
layout. Nothing reads words for meaning.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.lib.pipeline.chunk import is_chunked_element
from src.lib.pipeline.table_chunking import (
    TableHeaderRule,
    current_table_header_rule,
    layout_table,
    table_row_display_cells,
)
from src.schemas.viewer_files import DisplayBlock, DisplayTableRow

_CODE_CONTENT_TYPE = "code_block"


class UnsupportedDisplayElementError(ValueError):
    """A processed element has a type or shape the text view does not show."""

    def __init__(self, element_index: object, reason: str) -> None:
        super().__init__(f"Processed element {element_index} cannot be shown: {reason}")
        self.element_index = element_index


def _section_path(element: Mapping[str, Any]) -> list[str]:
    metadata = element.get("metadata")
    section_path = metadata.get("section_path") if isinstance(metadata, Mapping) else None
    if not isinstance(section_path, list) or not all(isinstance(part, str) for part in section_path):
        raise UnsupportedDisplayElementError(element.get("index"), "no section path")
    return section_path


def _table_block(text: str, section_path: list[str], rule: TableHeaderRule) -> DisplayBlock | None:
    """The table as the chunker lays it out, or None for a table it leaves out."""

    layout = layout_table(text, rule)
    if not layout.lines:
        return None
    rows = [
        DisplayTableRow(
            line=line,
            cells=table_row_display_cells(line),
            is_header=layout.data_row_number(line_index) is None,
        )
        for line_index, line in enumerate(layout.lines)
        if line_index != layout.separator_line_index
    ]
    return DisplayBlock(kind="table", text=layout.text, level=None, section_path=section_path, rows=rows)


def build_display_blocks(elements: Sequence[Mapping[str, Any]]) -> list[DisplayBlock]:
    """Turn one file's processed elements into display blocks, in reading order.

    Page footers and empty tables are left out, as the chunker leaves them out.
    """

    header_rule = current_table_header_rule()
    blocks: list[DisplayBlock] = []
    for element in elements:
        if not is_chunked_element(element):
            continue
        element_type = element.get("type")
        text = element.get("text")
        if not isinstance(text, str):
            raise UnsupportedDisplayElementError(element.get("index"), "no text")
        section_path = _section_path(element)

        if element_type == "Table":
            table = _table_block(text, section_path, header_rule)
            if table is not None:
                blocks.append(table)
            continue

        if element_type == "Title":
            if not section_path:
                raise UnsupportedDisplayElementError(element.get("index"), "a heading outside any section")
            blocks.append(
                DisplayBlock(
                    kind="heading", text=text, level=len(section_path), section_path=section_path, rows=None
                )
            )
            continue

        if element_type == "ListItem":
            kind = "list_item"
        elif element_type == "NarrativeText":
            content_type = element["metadata"].get("content_type")
            kind = "code" if content_type == _CODE_CONTENT_TYPE else "paragraph"
        else:
            raise UnsupportedDisplayElementError(element.get("index"), f"element type {element_type!r}")
        blocks.append(DisplayBlock(kind=kind, text=text, level=None, section_path=section_path, rows=None))
    return blocks
