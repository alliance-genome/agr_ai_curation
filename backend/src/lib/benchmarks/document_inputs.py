"""Decode frozen paper bytes for the existing document ingestion pipeline.

This module handles document content only. Authentication, document identity,
and execution messages must be supplied separately by the worker, never read
from paper JSON or markup.
"""

from __future__ import annotations

import json
from typing import Any


def decode_frozen_document(content: bytes, *, content_type: str) -> list[dict[str, Any]]:
    """Normalize supported extracted formats without source or network reads.

    Local snapshots already contain pipeline elements. Markdown and XML use
    the application's installed document parsers, retaining scientific section,
    table, and figure structure rather than flattening the paper into a prompt.
    """

    text = content.decode("utf-8")
    if content_type == "application/json":
        elements = json.loads(text)
    elif content_type in {"text/plain", "text/markdown", "application/xml"}:
        from src.lib.document_sources.ingestion import _strip_markdown_image_assets
        from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements

        if content_type == "application/xml":
            from agr_abc_document_parsers import convert_xml_to_markdown

            # The installed parser supports JATS/TEI and disables network,
            # DTD loading, and entity resolution. Do not dereference XML URLs.
            text = convert_xml_to_markdown(content)
        elements = markdown_to_pipeline_elements(_strip_markdown_image_assets(text))
    else:
        raise ValueError("Unsupported frozen benchmark document content type")

    if not isinstance(elements, list) or not elements:
        raise ValueError("Frozen benchmark document must contain extracted elements")
    for element in elements:
        if (
            not isinstance(element, dict)
            or not isinstance(element.get("text"), str)
            or not isinstance(element.get("metadata", {}), dict)
        ):
            raise ValueError("Frozen benchmark document contains an invalid extracted element")
    if not any(element["text"].strip() for element in elements):
        raise ValueError("Frozen benchmark document contains no readable text")
    return elements
