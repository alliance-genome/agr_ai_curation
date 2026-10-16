"""Lossless document views: source text once for models, geometry for the UI."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any
from uuid import uuid4

from agr_ai_curation_runtime.document_errors import DocumentReadError


DOCUMENT_READ_TOOLS = frozenset({"read_chunk", "search_document", "read_section", "read_subsection"})


def require_document_read_result(tool_name: str, result: Any) -> None:
    """Failed required reads cannot be returned to the model as empty success.

    An empty search is valid. Section/subsection discovery can also find no
    matching section; only an explicitly selected chunk is a required locator.
    """
    if tool_name not in DOCUMENT_READ_TOOLS:
        return
    raw = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    if not isinstance(raw, dict):
        raise DocumentReadError(tool_name) from TypeError(
            f"{tool_name} must return a structured document result"
        )
    # Paging/request and response-size errors are not failed source retrievals.
    # Their existing structured recovery/reporting contracts remain in force.
    if raw.get("error_code") in {"invalid_result_cursor", "tool_result_budget_unmet"}:
        return
    if tool_name == "read_chunk" and raw.get("chunk") is None:
        raise DocumentReadError(tool_name)


def document_result_view(tool_name: str, result: Any) -> tuple[Any, list[dict]]:
    require_document_read_result(tool_name, result)
    try:
        return _document_result_view(tool_name, result)
    except DocumentReadError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise DocumentReadError(tool_name) from exc


def _document_result_view(tool_name: str, result: Any) -> tuple[Any, list[dict]]:
    """Return a separate model view and provenance events; never alter originals.

    Span positions remain absolute character offsets into the original chunk.
    A paged chunk declares the offset at which its exact content starts. Evidence
    recording still resolves the original span IDs against canonical source text.
    """
    if tool_name not in DOCUMENT_READ_TOOLS:
        return result, []
    raw = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    view = deepcopy(raw)
    if tool_name == "search_document":
        records = view.get("hits") or []
    else:
        key = {"read_chunk": "chunk", "read_section": "section", "read_subsection": "subsection"}[tool_name]
        records = [view[key]] if view.get(key) is not None else []
    events = []
    for record in records:
        geometry = record.pop("doc_items", None)
        if not geometry and record.get("page_number"):
            geometry = [{"page": record["page_number"]}]
        source_id = record.get("chunk_id") or f"section:{record.get('section_title')}"
        if geometry:
            events.append({"type": "CHUNK_PROVENANCE", "message_id": str(uuid4()),
                           "chunk_id": source_id, "doc_items": geometry, "source_tool": tool_name})
        record.pop("chunk_id", None)
        record.pop("chunk_number", None)
        record.pop("previous_chunk_id", None)
        record.pop("next_chunk_id", None)
        for source in record.get("source_chunks") or []:
            source.pop("chunk_id", None)
        text = record.get("content")
        if not isinstance(text, str):
            continue
        record["content_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if "evidence_spans" not in record:
            continue
        start = (record.get("content_range") or {}).get("start", 0)
        record["content_start"] = start
        record["span_text_access"] = (
            "Span text is content[char_start-content_start:char_end-content_start]. "
            "Offsets count Unicode characters, end exclusive. Use chunk_index and span_index with record_evidence."
        )
        for span in record["evidence_spans"]:
            lo, hi = span["char_start"] - start, span["char_end"] - start
            if lo < 0 or hi > len(text) or hi <= lo or text[lo:hi] != span.get("text"):
                raise ValueError("Evidence span does not match the exact returned document text")
            del span["text"]
            span.pop("span_id", None)
    return view, events
