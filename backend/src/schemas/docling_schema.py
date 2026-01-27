"""Normalization utilities for Docling service responses."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

_LIGATURE_REPLACEMENTS = {
    0: "ff",
    1: "fi",
    2: "fl",
    3: "ffi",
    4: "ffl",
}

_LIGATURE_PATTERN = re.compile(r"/?uniFB0([0-4])", re.IGNORECASE)
_UNICODE_ESCAPE_PATTERN = re.compile(r"/u([0-9A-Fa-f]{4})")


class DoclingElement(BaseModel):
    """Single element returned by the Docling service."""

    index: int
    type: str
    original_type: Optional[str] = None
    level: int = Field(ge=1)
    text: Optional[str] = ""
    section_title: Optional[str] = None
    section_path: List[str] = Field(default_factory=list)
    content_type: str = Field(default="paragraph")
    is_heading: bool = False
    is_list_item: bool = False
    list_prefix: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def cleaned_text(self) -> str:
        """Return the text payload stripped of surrounding whitespace."""
        return (self.text or "").strip()

    def page_number(self) -> Optional[int]:
        """Best-effort extraction of page number from metadata."""
        page = self.metadata.get("page_number")
        if page is None:
            provenance = self.metadata.get("provenance")
            if provenance and isinstance(provenance, list):
                page = provenance[0].get("page_no")
            if page is None:
                return None
        try:
            return int(page)
        except (TypeError, ValueError):  # pragma: no cover - non-integer fallback
            return None


class DoclingResponse(BaseModel):
    """Full response envelope from the Docling service."""

    success: bool
    elements: List[DoclingElement]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class NormalizedElement(BaseModel):
    """Embedding-ready representation of a Docling element."""

    index: int
    content_type: str
    text: str
    embedding_text: str
    section_title: Optional[str]
    section_path: List[str]
    hierarchy_level: int
    page_number: Optional[int]
    list_prefix: Optional[str] = None
    original_type: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def pipeline_type(self) -> str:
        """Map logical content types to pipeline element types."""
        mapping = {
            "heading": "Title",
            "table": "Table",
            "list_item": "ListItem",
        }
        return mapping.get(self.content_type, "NarrativeText")


def _normalize_text(value: str) -> str:
    """Normalize extracted text: collapse unicode ligatures and escapes."""

    if not value:
        return ""

    normalized = unicodedata.normalize("NFKC", value)

    def _replace_ligature(match: re.Match) -> str:
        idx = int(match.group(1))
        return _LIGATURE_REPLACEMENTS.get(idx, "")

    normalized = _LIGATURE_PATTERN.sub(_replace_ligature, normalized)

    def _replace_unicode_escape(match: re.Match) -> str:
        try:
            return chr(int(match.group(1), 16))
        except ValueError:
            return match.group(0)

    normalized = _UNICODE_ESCAPE_PATTERN.sub(_replace_unicode_escape, normalized)
    return normalized


def normalize_elements(response: DoclingResponse) -> List[NormalizedElement]:
    """Convert raw Docling elements into embedding-ready objects."""

    normalized: List[NormalizedElement] = []
    active_section: Optional[str] = None
    last_prefixed_section: Optional[str] = None
    warned_types: Set[str] = set()

    for elem_idx, element in enumerate(response.elements):
        # Check for headers and footers to filter out
        label = element.metadata.get("doc_item_label")
        if label and label.lower() in {"page_footer", "page_header"}:
            # Drop header/footer boilerplate entirely
            logger.debug(
                "Filtering out %s element at index %s: %s",
                label,
                element.index,
                element.cleaned_text()[:50] if element.cleaned_text() else "(empty)"
            )
            continue

        # Also check the type field for Footer/Header markers from Docling service
        element_type = element.type
        if element_type in {"Footer", "Header"}:
            logger.debug(
                "Filtering out %s element at index %s: %s",
                element_type,
                element.index,
                element.cleaned_text()[:50] if element.cleaned_text() else "(empty)"
            )
            continue

        text = _normalize_text(element.cleaned_text())

        if element.is_heading:
            heading_text = text or element.section_title
            if not heading_text:
                logger.warning(
                    "Skipping heading element %s (%s) with no textual content",
                    element.original_type or element.type,
                    element.index,
                )
                continue
            active_section = heading_text
            last_prefixed_section = None

            heading_metadata = dict(element.metadata)
            heading_metadata["section_title"] = heading_text
            heading_metadata.setdefault("section_path", element.section_path or [heading_text])
            if heading_text:
                heading_metadata["character_count"] = len(heading_text)

            normalized.append(
                NormalizedElement(
                    index=element.index,
                    content_type="heading",
                    text=heading_text,
                    embedding_text=heading_text,
                    section_title=heading_text,
                    section_path=element.section_path or [heading_text],
                    hierarchy_level=element.level,
                    page_number=element.page_number(),
                    list_prefix=None,
                    original_type=element.original_type or element.type,
                    metadata=heading_metadata,
                )
            )
            continue

        if not text:
            if element.content_type not in {"table", "equation"}:
                logger.warning(
                    "Ignoring element %s (%s) at index %s due to empty text",
                    element.original_type or element.type,
                    element.content_type,
                    element.index,
                )
            continue

        section_title = element.section_title or active_section
        section_path = element.section_path or ([section_title] if section_title else [])
        embedding_text = text

        # Lists: prepend bullet/number marker if available
        list_prefix = element.list_prefix
        normalized_prefix = _normalize_text(list_prefix) if list_prefix else None
        if element.is_list_item and normalized_prefix:
            embedding_text = f"{normalized_prefix} {embedding_text}".strip()

        # Only prefix the section heading once per contiguous block
        if section_title and section_title != last_prefixed_section:
            embedding_text = f"{section_title}\n\n{embedding_text}" if embedding_text else section_title
            last_prefixed_section = section_title

        if element.content_type == "other":
            original = element.original_type or element.type
            if original and original not in warned_types:
                logger.warning(
                    "Encountered unhandled Docling element type '%s'; treating as narrative text",
                    original,
                )
                warned_types.add(original)

        element_metadata = dict(element.metadata)
        element_metadata["section_title"] = section_title
        element_metadata["section_path"] = section_path
        if text:
            element_metadata["character_count"] = len(text)

        normalized.append(
            NormalizedElement(
                index=element.index,
                content_type=element.content_type,
                text=text,
                embedding_text=embedding_text,
                section_title=section_title,
                section_path=section_path,
                hierarchy_level=element.level,
                page_number=element.page_number(),
                list_prefix=normalized_prefix,
                original_type=element.original_type or element.type,
                metadata=element_metadata,
            )
        )

    return normalized


def build_pipeline_elements(elements: List[NormalizedElement]) -> List[Dict[str, Any]]:
    """Create pipeline-ready dictionaries from normalized elements."""

    pipeline_elements: List[Dict[str, Any]] = []

    for elem in elements:
        metadata = dict(elem.metadata)
        metadata.update(
            {
                "section_title": elem.section_title,
                "section_path": elem.section_path,
                "hierarchy_level": elem.hierarchy_level,
                "page_number": elem.page_number,
                "content_type": elem.content_type,
                "original_type": elem.original_type,
            }
        )

        if not elem.embedding_text.strip():
            logger.warning(
                "Element %s (%s) produced empty embedding text; skipping",
                elem.index,
                elem.original_type,
            )
            continue

        element_dict = {
            "index": elem.index,
            "type": elem.pipeline_type(),
            "text": elem.embedding_text,
            "metadata": metadata,
        }
        pipeline_elements.append(element_dict)

    return pipeline_elements
