"""Sections of a file from its heading structure alone, with no model (KANBAN-1976 / ALL-1460, P7c1).

A paper's main file gets its sections from the hierarchy model
(``hierarchy_resolution.resolve_document_hierarchy``). A supplement gets them here:
the heading levels already recorded on each element (``metadata.section_path``,
written by ``markdown_to_pipeline_elements``) decide, the same way every time.
The fields written are exactly the hierarchy step's, so the pieces, the saved
processed JSON and every reader see one shape.

The rule:
- When the provider says the file's first top-level title is the file's title
  (``file_title_is_first_heading``), the next heading level is the section (one per
  workbook sheet for converted spreadsheets) and the level after it the subsection.
- Otherwise the first level is the section and the second the subsection.
- Deeper headings belong to their subsection.
- Text under the file's title only is a section named after that title; text before
  any heading is a section named ``fallback_section`` (the file's display name).
"""

from __future__ import annotations

import copy
from typing import Any


def apply_structural_sections(
    elements: list[dict[str, Any]],
    *,
    file_title_is_first_heading: bool,
    fallback_section: str,
) -> list[dict[str, Any]]:
    """Return copies of ``elements`` with their section fields set from the heading structure."""

    if not fallback_section:
        raise ValueError("A structural section needs the file's name for text before any heading")
    sectioned: list[dict[str, Any]] = []
    for element in elements:
        item = copy.deepcopy(element)
        path = [str(title) for title in (item.get("metadata", {}).get("section_path") or [])]
        parent, subsection = _section_of(path, file_title_is_first_heading, fallback_section)
        fields = {
            "parent_section": parent,
            "subsection": subsection,
            "is_top_level": subsection is None,
            "section_title": f"{parent} > {subsection}" if subsection else parent,
            "section_path": [parent, subsection] if subsection else [parent],
        }
        item.setdefault("metadata", {}).update(copy.deepcopy(fields))
        item.update(fields)
        sectioned.append(item)
    return sectioned


def _section_of(path: list[str], file_title_is_first_heading: bool, fallback: str) -> tuple[str, str | None]:
    levels = path[1:] if file_title_is_first_heading and len(path) > 1 else path
    if not levels:
        return fallback, None
    return levels[0], levels[1] if len(levels) > 1 else None
