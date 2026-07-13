"""Provider figure metadata normalization and Markdown enrichment."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import Any, cast

PROVIDER_FIGURE_METADATA_SECTION = "Provider Figure Metadata"
PROVIDER_FIGURE_SUBSECTION_PREFIX = "Provider Figure:"

_MARKDOWN_CONTROL_LINE_RE = re.compile(
    r"^\s{0,3}(?:#{1,6}\s+|````*|<!--\s*page\s*[:=]?\s*\d+\s*-->|\[\s*page\s+\d+\s*\])",
    re.IGNORECASE,
)


def normalize_provider_figure_metadata_sidecar(
    raw: bytes,
    *,
    metadata_artifact_id: str,
) -> dict[str, Any]:
    """Parse and normalize one provider figure-metadata JSON sidecar."""

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError(
            f"Provider figure metadata artifact {metadata_artifact_id} is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"Provider figure metadata artifact {metadata_artifact_id} must contain a JSON object"
        )
    normalized = normalize_provider_figure_metadata_payload(
        payload,
        metadata_artifact_id=metadata_artifact_id,
    )
    if normalized is None:
        raise ValueError(
            f"Provider figure metadata artifact {metadata_artifact_id} has no indexable figure metadata"
        )
    return normalized


def normalize_provider_figure_metadata_payload(
    payload: Mapping[str, Any],
    *,
    metadata_artifact_id: str | None = None,
) -> dict[str, Any] | None:
    """Return a compact sidecar shape used by ingestion and tests."""

    caption_text = _clean_text(payload.get("caption_text"))
    nearby_text = _clean_text(payload.get("nearby_text"))
    figure_label = _clean_text(payload.get("figure_label"))
    figure_number = _clean_text(payload.get("figure_number"))
    display_name = _clean_text(payload.get("display_name"))
    filename = _clean_text(payload.get("filename"))

    if not any((caption_text, nearby_text, figure_label, figure_number, display_name, filename)):
        return None

    page_index = _zero_based_page_index(payload.get("page_index"))
    entry: dict[str, Any] = {
        "metadata_artifact_id": _clean_text(metadata_artifact_id),
        "display_name": display_name,
        "figure_index": _primitive(payload.get("figure_index")),
        "source_display_name": _clean_text(payload.get("source_display_name")),
        "source_file_class": _clean_text(payload.get("source_file_class")),
        "figure_label": figure_label,
        "figure_number": figure_number,
        "caption_text": caption_text,
        "nearby_text": nearby_text,
        "page_index": page_index,
        "page_number": page_index + 1 if page_index is not None else None,
        "bbox": _json_like(payload.get("bbox")),
        "polygon": _json_like(payload.get("polygon")),
        "filename": filename,
    }
    image_review = payload.get("image_review")
    if isinstance(image_review, Mapping):
        entry["image_review"] = dict(image_review)
    return {key: value for key, value in entry.items() if value not in (None, "", [])}


def apply_provider_figure_page_provenance(
    elements: Sequence[dict[str, Any]],
    entries: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Attach canonical one-based pages to generated provider figure elements.

    Provider ``page_index`` is zero-based. Normalization converts it once to
    ``page_number``; this function only carries that canonical value into the
    project-agnostic element metadata consumed by chunking.
    """

    pages_by_heading: dict[str, deque[int | None]] = defaultdict(deque)
    for entry in sorted(
        (
            dict(candidate)
            for candidate in (entries or ())
            if _entry_has_indexable_content(candidate)
        ),
        key=_entry_sort_key,
    ):
        pages_by_heading[_entry_heading(entry)].append(
            _positive_page_number(entry.get("page_number"))
        )

    active_page: int | None = None
    for element in elements:
        metadata = element.get("metadata")
        if not isinstance(metadata, dict):
            continue
        section_path = metadata.get("section_path")
        if not isinstance(section_path, list) or not any(
            is_provider_figure_metadata_section(title) for title in section_path
        ):
            continue

        title = element.get("text") if element.get("type") == "Title" else None
        if is_provider_figure_metadata_section(title):
            active_page = None
        elif is_provider_figure_subsection(title):
            heading_pages = pages_by_heading.get(str(title))
            active_page = heading_pages.popleft() if heading_pages else None
        metadata["page_number"] = active_page


def append_provider_figure_metadata_markdown(
    markdown: str,
    entries: Sequence[Mapping[str, Any]] | None,
) -> str:
    """Append a generated provider figure metadata section when entries exist."""

    appendix = render_provider_figure_metadata_appendix(entries)
    if not appendix:
        return markdown
    base = markdown.rstrip()
    separator = "\n\n---\n\n" if base else ""
    return f"{base}{separator}{appendix}\n"


def render_provider_figure_metadata_appendix(
    entries: Sequence[Mapping[str, Any]] | None,
) -> str:
    """Render sidecar metadata as stable Markdown for indexing."""

    normalized_entries = [
        dict(entry)
        for entry in (entries or ())
        if _entry_has_indexable_content(entry)
    ]
    if not normalized_entries:
        return ""

    lines = [f"## {PROVIDER_FIGURE_METADATA_SECTION}", ""]
    for entry in sorted(normalized_entries, key=_entry_sort_key):
        heading = _entry_heading(entry)
        lines.extend([f"### {heading}", ""])
        for label, key in (
            ("Figure label", "figure_label"),
            ("Figure number", "figure_number"),
            ("Source figure artifact", "display_name"),
            ("Metadata artifact", "metadata_artifact_id"),
            ("Source display name", "source_display_name"),
            ("Source file class", "source_file_class"),
            ("PDFX page_index", "page_index"),
            ("Filename", "filename"),
        ):
            value = entry.get(key)
            if value not in (None, "", []):
                lines.append(f"{label}: {_inline_value(value)}")

        bbox = entry.get("bbox")
        if bbox not in (None, "", []):
            lines.append(f"PDFX bbox: {_inline_value(bbox)}")
        polygon = entry.get("polygon")
        if polygon not in (None, "", []):
            lines.append(f"PDFX polygon: {_inline_value(polygon)}")

        caption_text = _clean_text(entry.get("caption_text"))
        if caption_text:
            lines.extend(["", "Legend:", _escape_markdown_control_lines(caption_text)])

        nearby_text = _clean_text(entry.get("nearby_text"))
        if nearby_text and nearby_text != caption_text:
            lines.extend(["", "Nearby text:", _escape_markdown_control_lines(nearby_text)])

        lines.append("")

    return "\n".join(lines).rstrip()


def is_provider_figure_metadata_section(title: object) -> bool:
    return _clean_text(title) == PROVIDER_FIGURE_METADATA_SECTION


def is_provider_figure_subsection(title: object) -> bool:
    value = _clean_text(title) or ""
    return value.startswith(PROVIDER_FIGURE_SUBSECTION_PREFIX)


def _entry_has_indexable_content(entry: Mapping[str, Any]) -> bool:
    return any(
        _clean_text(entry.get(key))
        for key in (
            "caption_text",
            "nearby_text",
            "figure_label",
            "figure_number",
            "display_name",
            "filename",
        )
    )


def _entry_heading(entry: Mapping[str, Any]) -> str:
    label = _clean_text(entry.get("figure_label"))
    if label:
        return f"{PROVIDER_FIGURE_SUBSECTION_PREFIX} {label}"
    number = _clean_text(entry.get("figure_number"))
    if number:
        return f"{PROVIDER_FIGURE_SUBSECTION_PREFIX} Figure {number}"
    display_name = _clean_text(entry.get("display_name") or entry.get("filename"))
    if display_name:
        return f"{PROVIDER_FIGURE_SUBSECTION_PREFIX} {display_name}"
    artifact_id = _clean_text(entry.get("metadata_artifact_id"))
    if artifact_id:
        return f"{PROVIDER_FIGURE_SUBSECTION_PREFIX} metadata {artifact_id}"
    return f"{PROVIDER_FIGURE_SUBSECTION_PREFIX} unknown"


def _entry_sort_key(entry: Mapping[str, Any]) -> tuple[int, str, str]:
    index = entry.get("figure_index")
    if index is None:
        index_value = 10**9
    else:
        try:
            index_value = int(index)
        except (TypeError, ValueError):
            index_value = 10**9
    return (
        index_value,
        str(entry.get("display_name") or "").lower(),
        str(entry.get("metadata_artifact_id") or ""),
    )


def _inline_value(value: object) -> str:
    if isinstance(value, Mapping) or _is_non_string_sequence(value):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _escape_markdown_control_lines(text: str) -> str:
    escaped_lines = []
    for line in text.splitlines() or [text]:
        if _MARKDOWN_CONTROL_LINE_RE.match(line):
            escaped_lines.append(f"\\{line}")
        else:
            escaped_lines.append(line)
    return "\n".join(escaped_lines)


def _json_like(value: object) -> object | None:
    if isinstance(value, Mapping):
        return dict(value)
    if _is_non_string_sequence(value):
        return list(cast(Sequence[object], value))
    return _primitive(value)


def _primitive(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, (bool, int, float)):
        return value
    return None


def _zero_based_page_index(value: object) -> int | None:
    """Validate the provider contract for a zero-based integer page index."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _positive_page_number(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_non_string_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )
