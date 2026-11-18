"""Lossless document views: source text once for models, geometry for the UI."""

from __future__ import annotations

from copy import deepcopy
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
    if raw.get("error_code") == "invalid_result_cursor":
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

    The three reading tools show source text the same way: numbered lines, one
    evidence span per line, ``[span_index] exact span text``. The numbers are the
    canonical span indexes, so ``(chunk_index, span_index)`` means the same from
    any tool. Character positions, span IDs and chunk IDs stay in the canonical
    result for provenance; evidence recording resolves them from issued spans.
    """
    if tool_name not in DOCUMENT_READ_TOOLS:
        return result, []
    raw = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    view = deepcopy(raw)
    events = []
    if tool_name == "search_document":
        records = view.get("hits") or []
    elif tool_name == "read_chunk":
        records = [view["chunk"]] if view.get("chunk") is not None else []
    else:
        section = view.get(_SECTION_RESULT_KEYS[tool_name])
        records = section["passages"] if section is not None else []
        if section is not None:
            _drop_none(section)
        geometry = [item for record in records for item in record.pop("doc_items", None) or []]
        if geometry:
            events.append({"type": "CHUNK_PROVENANCE", "message_id": str(uuid4()),
                           "chunk_id": f"section:{section['requested_section']}",
                           "doc_items": geometry, "source_tool": tool_name})
    for record in records:
        geometry = record.pop("doc_items", None)
        if not geometry and record.get("page_number"):
            geometry = [{"page": record["page_number"]}]
        if geometry and tool_name not in _SECTION_RESULT_KEYS:
            events.append({"type": "CHUNK_PROVENANCE", "message_id": str(uuid4()),
                           "chunk_id": record.get("chunk_id"), "doc_items": geometry,
                           "source_tool": tool_name})
        for key in ("chunk_id", "chunk_number", "previous_chunk_id", "next_chunk_id"):
            record.pop(key, None)
        if "evidence_spans" in record:
            record["text"] = _numbered_span_text(record)
        _drop_none(record)
    _drop_none(view)
    return view, events


_SECTION_RESULT_KEYS = {"read_section": "section", "read_subsection": "subsection"}


def _numbered_span_text(record: dict) -> str:
    """Replace a record's exact content and span list with its numbered lines.

    Every span is checked against the exact returned text before it is shown, so
    the model sees only sentences the canonical source actually contains. A span
    can hold single line breaks (a table row, a primer list); its continuation
    lines are indented so that only a real span number ever starts a line.
    """
    text = record.pop("content")
    spans = record.pop("evidence_spans")
    start = (record.pop("content_range", None) or {}).get("start", 0)
    if not isinstance(text, str):
        raise ValueError("Document record has no exact text")
    lines = []
    for span in spans:
        lo, hi = span["char_start"] - start, span["char_end"] - start
        if lo < 0 or hi > len(text) or hi <= lo or text[lo:hi] != span.get("text"):
            raise ValueError("Evidence span does not match the exact returned document text")
        shown = span["text"].replace("\n", "\n" + _CONTINUATION_INDENT)
        lines.append(f"[{span['span_index']}] {shown}")
    return "\n".join(lines)


_CONTINUATION_INDENT = "    "


def _drop_none(mapping: dict) -> None:
    for key in [key for key, value in mapping.items() if value is None]:
        del mapping[key]
