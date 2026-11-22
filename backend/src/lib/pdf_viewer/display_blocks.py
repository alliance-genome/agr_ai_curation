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

Section fields are the ones the stored pieces carry (see ``file_display_blocks``).

Everything here reads structure only: element types, structural metadata and table
layout. Nothing reads words for meaning.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.lib.pipeline.chunk import is_chunked_element
from src.lib.pipeline.hierarchy_resolution import apply_section_hierarchy, stored_section_map
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


def _optional_text(metadata: Mapping[str, Any], key: str, element: Mapping[str, Any]) -> str | None:
    value = metadata.get(key)
    if value is not None and not isinstance(value, str):
        raise UnsupportedDisplayElementError(element.get("index"), f"{key} is not text")
    return value


def _sections(element: Mapping[str, Any]) -> dict[str, Any]:
    """The element's section fields, as the chunker copies them onto its pieces."""

    metadata = element.get("metadata")
    if not isinstance(metadata, Mapping):
        raise UnsupportedDisplayElementError(element.get("index"), "no metadata")
    section_path = metadata.get("section_path")
    if not isinstance(section_path, list) or not all(isinstance(part, str) for part in section_path):
        raise UnsupportedDisplayElementError(element.get("index"), "no section path")
    return {
        "section_title": _optional_text(metadata, "section_title", element),
        "parent_section": _optional_text(metadata, "parent_section", element),
        "subsection": _optional_text(metadata, "subsection", element),
        "section_path": section_path,
    }


def _heading_level(element: Mapping[str, Any]) -> int:
    level = element["metadata"].get("hierarchy_level")
    if isinstance(level, bool) or not isinstance(level, int) or level < 1:
        raise UnsupportedDisplayElementError(element.get("index"), "a heading without a level")
    return level


def _table_block(text: str, sections: dict[str, Any], rule: TableHeaderRule) -> DisplayBlock | None:
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
    return DisplayBlock(kind="table", text=layout.text, level=None, rows=rows, **sections)


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
        sections = _sections(element)

        if element_type == "Table":
            table = _table_block(text, sections, header_rule)
            if table is not None:
                blocks.append(table)
            continue

        if element_type == "Title":
            blocks.append(
                DisplayBlock(kind="heading", text=text, level=_heading_level(element), rows=None, **sections)
            )
            continue

        if element_type == "ListItem":
            kind = "list_item"
        elif element_type == "NarrativeText":
            content_type = element["metadata"].get("content_type")
            kind = "code" if content_type == _CODE_CONTENT_TYPE else "paragraph"
        else:
            raise UnsupportedDisplayElementError(element.get("index"), f"element type {element_type!r}")
        blocks.append(DisplayBlock(kind=kind, text=text, level=None, rows=None, **sections))
    return blocks


def file_display_blocks(
    elements: list[dict[str, Any]],
    hierarchy_metadata: Mapping[str, Any] | None,
) -> list[DisplayBlock]:
    """One file's display blocks, with the section names its stored pieces carry.

    Indexing applies the file's resolved section map to these same elements before
    cutting pieces, and stores the map on the file. Applying the stored map here
    with the same function gives each block its pieces' section fields. A file
    with no stored map was cut with its elements' own section fields, so those are
    used as they are. A stored map of an unknown shape raises
    ``StoredSectionMapError``.
    """

    if hierarchy_metadata is not None:
        apply_section_hierarchy(elements, stored_section_map(hierarchy_metadata))
    return build_display_blocks(elements)
