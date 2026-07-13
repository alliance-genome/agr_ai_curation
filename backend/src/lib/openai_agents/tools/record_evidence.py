"""Span-backed evidence-recording tool for document extraction agents."""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from agents import function_tool

from src.lib.openai_agents.config import get_record_evidence_preview_chars
from src.lib.document_sources.figure_metadata import (
    PROVIDER_FIGURE_METADATA_SECTION,
    is_provider_figure_subsection,
)
from src.lib.openai_agents.evidence_spans import (
    EvidenceSpan,
    EvidenceSpanResolutionError,
    parse_evidence_span_id,
    resolve_evidence_span_id,
)
from src.lib.openai_agents.evidence_summary import (
    build_evidence_record_id,
    evidence_record_status,
)
from src.lib.openai_agents.tools.evidence_workspace import (
    find_active_evidence_record,
    find_evidence_record_in_records,
)
from src.lib.weaviate_client.chunks import get_chunk_by_id

if TYPE_CHECKING:
    from ..guardrails import ToolCallTracker


logger = logging.getLogger(__name__)

_FIGURE_REFERENCE_PATTERN = re.compile(
    r"\b(?:Fig(?:ure)?\.?\s*\d+[A-Za-z0-9-]*)\b",
    re.IGNORECASE,
)
_TABLE_REFERENCE_PATTERN = re.compile(
    r"\b(?:Table\.?\s*\d+[A-Za-z0-9-]*)\b",
    re.IGNORECASE,
)
_MULTI_REFERENCE_SEPARATOR = (
    r"(?:,\s*|/\s*|&\s*|\band\s+|\bto\s+|\bthrough\s+|\bversus\s+|"
    r"[-\u2013\u2014]\s*)"
)
_MULTI_REFERENCE_PATTERN = re.compile(
    rf"\b(?:Figs?\.?|Figures?\.?|Tables?\.?)\s*\d+[A-Za-z]?\s*"
    rf"{_MULTI_REFERENCE_SEPARATOR}"
    r"(?:(?:Figs?\.?|Figures?\.?|Tables?\.?|panels?)\s*)?"
    r"(?:[A-Za-z]|\d+[A-Za-z]?)\b",
    re.IGNORECASE,
)
_PROSE_MULTI_PANEL_PATTERN = re.compile(
    rf"\bpanels?\s+(?:[A-Za-z]\d*|\d+)\s*{_MULTI_REFERENCE_SEPARATOR}"
    r"(?:panels?\s+)?"
    r"(?:[A-Za-z]\d*|\d+)\b",
    re.IGNORECASE,
)
# Env-configurable via RECORD_EVIDENCE_PREVIEW_CHARS (default 300); see config.py.
_PREVIEW_CHARS = get_record_evidence_preview_chars()
_SPAN_RETRY_INSTRUCTIONS = (
    "Call read_chunk for the source chunk again and select current "
    "evidence_spans[].span_id values. Do not provide model-authored source text."
)
_SOURCE_REVISION_FIELDS = (
    "verified_quote",
    "span_ids",
    "source_span_ids",
    "source_fragments",
    "document_id",
    "chunk_id",
    "chunk_ids",
    "page",
    "section",
    "subsection",
    "figure_reference",
)
_ATTACHMENT_METADATA_FIELDS = (
    "object_id",
    "pending_ref_id",
    "object_ref",
    "envelope_target",
    "envelope_targets",
    "field_path",
    "field_paths",
)
_PRESERVED_METADATA_FIELDS = (
    "agent_note",
    "created_at",
)


@dataclass(frozen=True)
class _ResolvedSpanFragment:
    span: EvidenceSpan
    chunk: dict[str, Any]
    chunk_id: str
    document_id: str
    page: int | None
    section: str | None
    subsection: str | None
    figure_reference: str | None

    def to_source_fragment(self) -> dict[str, Any]:
        parsed_span = parse_evidence_span_id(self.span.span_id)
        fragment: dict[str, Any] = {
            "span_id": self.span.span_id,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "text": self.span.text,
            "char_start": self.span.char_start,
            "char_end": self.span.char_end,
            "text_hash": parsed_span.text_hash,
            "span_index": self.span.span_index,
            "span_type": self.span.span_type,
            "spanizer_version": self.span.spanizer_version,
        }
        if self.page is not None:
            fragment["page"] = self.page
        if self.section:
            fragment["section"] = self.section
        if self.subsection:
            fragment["subsection"] = self.subsection
        if self.figure_reference:
            fragment["figure_reference"] = self.figure_reference
        return fragment


def _build_preview(chunk_text: str) -> str:
    stripped_text = chunk_text.strip()
    if len(stripped_text) <= _PREVIEW_CHARS:
        return stripped_text

    preview = stripped_text[:_PREVIEW_CHARS].rstrip()
    if len(preview) < len(stripped_text):
        preview = preview.rstrip(" ,;:") + "..."
    return preview


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return None


def _metadata_dict(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata = chunk.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _resolve_chunk_section(chunk: dict[str, Any]) -> str | None:
    metadata = _metadata_dict(chunk)
    return _first_non_empty(
        chunk.get("parent_section"),
        chunk.get("section_title"),
        metadata.get("parent_section"),
        metadata.get("parentSection"),
        metadata.get("section_title"),
        metadata.get("sectionTitle"),
    )


def _resolve_chunk_subsection(chunk: dict[str, Any]) -> str | None:
    metadata = _metadata_dict(chunk)
    return _first_non_empty(
        chunk.get("subsection"),
        metadata.get("subsection"),
        metadata.get("subSection"),
    )


def _coerce_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None

    try:
        page = int(value)
    except (TypeError, ValueError):
        return None

    return page if page > 0 else None


def _resolve_doc_item_page(item: dict[str, Any]) -> int | None:
    return (
        _coerce_positive_int(item.get("page"))
        or _coerce_positive_int(item.get("page_no"))
        or _coerce_positive_int(item.get("page_number"))
        or _coerce_positive_int(item.get("pageNumber"))
    )


def _resolve_chunk_page(chunk: dict[str, Any]) -> int | None:
    metadata = _metadata_dict(chunk)
    page = (
        _coerce_positive_int(chunk.get("page_number"))
        or _coerce_positive_int(metadata.get("page_number"))
        or _coerce_positive_int(metadata.get("pageNumber"))
    )

    raw_doc_items = chunk.get("doc_items")
    if not isinstance(raw_doc_items, list):
        raw_doc_items = metadata.get("doc_items")
    doc_items = raw_doc_items if isinstance(raw_doc_items, list) else []
    doc_item_pages = [
        resolved_page
        for item in doc_items
        if isinstance(item, dict)
        for resolved_page in [_resolve_doc_item_page(item)]
        if resolved_page is not None
    ]
    doc_item_pages = list(dict.fromkeys(doc_item_pages))

    if not doc_item_pages:
        return page

    if page is not None and page in doc_item_pages:
        return page

    return doc_item_pages[0]


def _resolve_exact_chunk_text(chunk: dict[str, Any]) -> str | None:
    text = chunk.get("text")
    return text if isinstance(text, str) else None


def _extract_figure_reference(
    chunk: dict[str, Any],
    chunk_text: str,
    span_text: str | None = None,
) -> str | None:
    selected_text = span_text or chunk_text
    if _is_provider_figure_metadata_chunk(chunk):
        selected_text = _strip_provider_figure_metadata_wrapper(selected_text)

    # Span-derived provenance is authoritative. Ambiguous shorthand must be
    # rejected before regex extraction can collapse it to its first locator.
    if _has_ambiguous_figure_reference(selected_text):
        return None
    span_candidates = _reference_candidates((selected_text,))
    if len(span_candidates) == 1:
        return span_candidates[0]
    if span_candidates:
        return None

    # Structured provenance is only a fallback when the selected text contains
    # neither a locator nor evidence that it refers to multiple panels.
    structured_sources = _structured_figure_reference_sources(chunk)
    if any(_has_ambiguous_figure_reference(source) for source in structured_sources):
        return None
    structured_candidates = _reference_candidates(structured_sources)
    if len(structured_candidates) == 1:
        return structured_candidates[0]
    return None


def _has_ambiguous_figure_reference(text: str | None) -> bool:
    if not text:
        return False
    return bool(
        _MULTI_REFERENCE_PATTERN.search(text)
        or _PROSE_MULTI_PANEL_PATTERN.search(text)
    )


def _reference_candidates(source_texts: tuple[str | None, ...]) -> list[str]:
    candidates: list[str] = []
    for source_text in source_texts:
        if not source_text:
            continue
        candidates.extend(_FIGURE_REFERENCE_PATTERN.findall(source_text))
        candidates.extend(_TABLE_REFERENCE_PATTERN.findall(source_text))

    unique_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", candidate.strip())
        normalized_key = re.sub(
            r"^fig(?:ure)?\.?",
            "figure",
            normalized.lower(),
        ).replace(" ", "")
        if normalized_key in seen:
            continue
        seen.add(normalized_key)
        unique_candidates.append(normalized)
    return unique_candidates


def _structured_figure_reference_sources(
    chunk: dict[str, Any],
) -> tuple[str | None, ...]:
    metadata = _metadata_dict(chunk)
    sources: list[str | None] = [
        _first_non_empty(
            chunk.get("figure_reference"),
            chunk.get("figureReference"),
        ),
        _first_non_empty(
            metadata.get("figure_reference"),
            metadata.get("figureReference"),
        ),
    ]

    if _is_provider_figure_metadata_chunk(chunk):
        sources.append(_resolve_chunk_subsection(chunk))
        sources.extend(
            _first_non_empty(chunk.get(key), metadata.get(key))
            for key in ("figure_label", "figureLabel")
        )
        for key in ("figure_number", "figureNumber"):
            number = _first_non_empty(chunk.get(key), metadata.get(key))
            if number and not _FIGURE_REFERENCE_PATTERN.search(number):
                number = f"Figure {number}"
            sources.append(number)
    else:
        sources.extend(
            (
                _resolve_chunk_section(chunk),
                _resolve_chunk_subsection(chunk),
            )
        )
    return tuple(sources)


def _is_provider_figure_metadata_chunk(chunk: dict[str, Any]) -> bool:
    section = _resolve_chunk_section(chunk)
    subsection = _resolve_chunk_subsection(chunk)
    return (
        section == PROVIDER_FIGURE_METADATA_SECTION
        or subsection == PROVIDER_FIGURE_METADATA_SECTION
        or is_provider_figure_subsection(subsection)
    )


def _strip_provider_figure_metadata_wrapper(text: str) -> str:
    stripped_lines = []
    for line in text.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        if (
            normalized == PROVIDER_FIGURE_METADATA_SECTION
            or is_provider_figure_subsection(normalized)
            or normalized.startswith(
                (
                    "Figure label:",
                    "Figure number:",
                    "Source figure artifact:",
                    "Metadata artifact:",
                    "Source display name:",
                    "Source file class:",
                    "PDFX page_index:",
                    "PDFX bbox:",
                    "PDFX polygon:",
                    "Filename:",
                )
            )
        ):
            continue
        stripped_lines.append(line)
    return "\n".join(stripped_lines)


def _optional_output_string(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_envelope_target_fields(
    *,
    object_id: Any = None,
    pending_ref_id: Any = None,
    object_type: Any = None,
    field_path: Any = None,
    validation_finding_id: Any = None,
) -> dict[str, Any]:
    target: dict[str, Any] = {}
    normalized_object_id = _optional_output_string(object_id)
    normalized_pending_ref_id = _optional_output_string(pending_ref_id)
    normalized_object_type = _optional_output_string(object_type)
    normalized_field_path = _optional_output_string(field_path)
    normalized_validation_finding_id = _optional_output_string(validation_finding_id)

    if (
        normalized_object_id
        or normalized_pending_ref_id
        or normalized_object_type
        or normalized_validation_finding_id
    ) and not normalized_field_path:
        raise ValueError(
            "field_path is required when evidence target identity is supplied"
        )

    if normalized_object_id:
        target["object_id"] = normalized_object_id
    elif normalized_pending_ref_id:
        target["pending_ref_id"] = normalized_pending_ref_id
    if normalized_object_type:
        target["object_type"] = normalized_object_type
    if normalized_field_path:
        target["field_path"] = normalized_field_path
    if normalized_validation_finding_id:
        target["validation_finding_id"] = normalized_validation_finding_id

    return {"envelope_target": target} if target else {}


def _merge_extra_fields(*field_groups: dict[str, Any]) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    for fields in field_groups:
        merged.update(fields)
    return merged or None


def _copy_existing_record_fields(
    payload: dict[str, Any],
    existing_record: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    for key in fields:
        value = existing_record.get(key)
        if value not in (None, "", []):
            payload[key] = deepcopy(value)


def _project_primary_target_fields(payload: dict[str, Any]) -> None:
    raw_target = payload.get("envelope_target")
    if not isinstance(raw_target, dict) or not raw_target:
        return

    target = {
        key: normalized
        for key in (
            "object_id",
            "pending_ref_id",
            "object_type",
            "field_path",
            "validation_finding_id",
        )
        for normalized in [_optional_output_string(raw_target.get(key))]
        if normalized
    }
    if not target:
        return

    payload["envelope_target"] = target
    payload["envelope_targets"] = [dict(target)]
    if target.get("object_id"):
        payload["object_id"] = target["object_id"]
    if target.get("pending_ref_id"):
        payload["pending_ref_id"] = target["pending_ref_id"]
    if target.get("object_id") or target.get("pending_ref_id"):
        payload["object_ref"] = {
            key: target[key]
            for key in ("object_id", "pending_ref_id")
            if target.get(key)
        }
    if target.get("field_path"):
        payload["field_path"] = target["field_path"]
        payload["field_paths"] = [target["field_path"]]


def _previous_source_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in _SOURCE_REVISION_FIELDS:
        value = record.get(key)
        if value not in (None, "", []):
            snapshot[key] = deepcopy(value)
    return snapshot


def _revision_history_with_previous_source(
    existing_record: dict[str, Any],
) -> list[dict[str, Any]] | None:
    raw_history = existing_record.get("evidence_revision_history")
    history = [
        deepcopy(item)
        for item in raw_history
        if isinstance(item, dict)
    ] if isinstance(raw_history, list) else []

    previous_source = _previous_source_snapshot(existing_record)
    if not previous_source:
        return history or None

    history.append(
        {
            "revision": len(history) + 1,
            "replaced_at": _now_iso(),
            "previous_source": previous_source,
        }
    )
    return history


def _strip_hidden_revision_history(payload: dict[str, Any]) -> dict[str, Any]:
    visible_payload = dict(payload)
    visible_payload.pop("evidence_revision_history", None)
    return visible_payload


def _normalize_span_ids(span_ids: Any) -> tuple[list[str], dict[str, Any] | None]:
    if not isinstance(span_ids, list):
        return [], {
            "failed_span_id": None,
            "failed_span_error": "span_ids must be a non-empty list of span ID strings",
        }

    normalized_span_ids: list[str] = []
    for index, span_id in enumerate(span_ids):
        if not isinstance(span_id, str):
            return [], {
                "failed_span_id": None,
                "failed_span_index": index,
                "failed_span_error": "span_ids must contain only strings",
            }
        normalized = span_id.strip()
        if not normalized:
            return [], {
                "failed_span_id": span_id,
                "failed_span_index": index,
                "failed_span_error": "span_ids must not contain blank values",
            }
        normalized_span_ids.append(normalized)

    if not normalized_span_ids:
        return [], {
            "failed_span_id": None,
            "failed_span_error": "span_ids must contain at least one span ID",
        }
    return normalized_span_ids, None


def _build_span_resolution_error_result(
    *,
    entity: str,
    span_ids: list[str],
    failed_span_id: str | None,
    failed_span_error: str,
    failed_span_index: int | None = None,
    chunk_id: str | None = None,
    chunk_text: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "span_id": failed_span_id,
        "message": failed_span_error,
    }
    if failed_span_index is not None:
        error["span_index"] = failed_span_index
    if chunk_id:
        error["chunk_id"] = chunk_id

    payload: dict[str, Any] = {
        "status": "not_found",
        "entity": entity,
        "span_ids": span_ids,
        "failed_span_id": failed_span_id,
        "failed_span_error": failed_span_error,
        "span_resolution_errors": [error],
        "message": (
            f"Evidence span could not be resolved: {failed_span_error}. "
            "The evidence record was not created."
        ),
        "retry_instructions": _SPAN_RETRY_INSTRUCTIONS,
    }
    if failed_span_index is not None:
        payload["failed_span_index"] = failed_span_index
    if chunk_id:
        payload["chunk_id"] = chunk_id
    if chunk_text:
        payload["chunk_content_preview"] = _build_preview(chunk_text)
    if extra_fields:
        payload.update(extra_fields)
    return payload


def _unique_non_empty(values: list[str | None]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _log_record_evidence_result(
    result: dict[str, Any],
    *,
    document_id: str,
    verification_method: str,
) -> dict[str, Any]:
    logger.info(
        "record_evidence result status=%s method=%s entity=%r chunk_id=%s document=%s "
        "evidence_record_id=%s page=%r section=%r message=%r span_ids=%r "
        "verified_quote_preview=%r chunk_preview=%r",
        result.get("status"),
        verification_method,
        result.get("entity"),
        result.get("chunk_id"),
        document_id[:8],
        result.get("evidence_record_id"),
        result.get("page"),
        result.get("section"),
        result.get("message"),
        result.get("span_ids"),
        _preview_for_log(result.get("verified_quote")),
        _preview_for_log(result.get("chunk_content_preview")),
    )
    return result


def _preview_for_log(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip().replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def create_record_evidence_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
    *,
    workspace_records: list[dict[str, Any]] | None = None,
    allowed_evidence_record_ids: set[str] | frozenset[str] | None = None,
    allow_create: bool = True,
    required_object_id: str | None = None,
    required_pending_ref_id: str | None = None,
    required_field_path: str | None = None,
):
    """Create a record_evidence tool bound to one document and user."""

    allowed_ids = {
        normalized
        for raw in (allowed_evidence_record_ids or set())
        for normalized in [_optional_output_string(raw)]
        if normalized
    } if allowed_evidence_record_ids is not None else None

    def _scope_error_for_record_id(
        normalized_evidence_record_id: str | None,
    ) -> dict[str, Any] | None:
        if normalized_evidence_record_id is None:
            if allow_create:
                return None
            return {
                "status": "forbidden",
                "message": "This validator evidence tool may only update supplied evidence records; provide evidence_record_id.",
                "allowed_evidence_record_ids": sorted(allowed_ids or []),
            }
        if allowed_ids is None or normalized_evidence_record_id in allowed_ids:
            return None
        return {
            "status": "forbidden",
            "evidence_record_id": normalized_evidence_record_id,
            "allowed_evidence_record_ids": sorted(allowed_ids),
            "message": "Validators may only update evidence records supplied for this validation target.",
        }

    def _target_scope_error(
        *,
        object_id: Any = None,
        pending_ref_id: Any = None,
        field_path: Any = None,
    ) -> dict[str, Any] | None:
        normalized_object_id = _optional_output_string(object_id)
        normalized_pending_ref_id = _optional_output_string(pending_ref_id)
        normalized_field_path = _optional_output_string(field_path)
        allowed_target_ids = {
            target_id
            for target_id in (required_object_id, required_pending_ref_id)
            if target_id
        }
        supplied_target_ids = [
            target_id
            for target_id in (normalized_object_id, normalized_pending_ref_id)
            if target_id
        ]
        if allowed_target_ids and supplied_target_ids and not all(
            target_id in allowed_target_ids for target_id in supplied_target_ids
        ):
            return {
                "status": "forbidden",
                "message": "Validators may not retarget evidence to another object or pending ref.",
                "allowed_object_id": required_object_id,
                "allowed_pending_ref_id": required_pending_ref_id,
            }
        if (
            required_field_path
            and normalized_field_path
            and normalized_field_path != required_field_path
        ):
            return {
                "status": "forbidden",
                "message": "Validators may not retarget evidence to another field path.",
                "target_field_path": required_field_path,
            }
        return None

    def _target_argument_error(
        *,
        object_id: Any = None,
        pending_ref_id: Any = None,
        object_type: Any = None,
        field_path: Any = None,
        validation_finding_id: Any = None,
    ) -> dict[str, Any] | None:
        normalized_field_path = _optional_output_string(field_path)
        supplied_target = {
            key: value
            for key, value in (
                ("object_id", _optional_output_string(object_id)),
                ("pending_ref_id", _optional_output_string(pending_ref_id)),
                ("object_type", _optional_output_string(object_type)),
                (
                    "validation_finding_id",
                    _optional_output_string(validation_finding_id),
                ),
            )
            if value
        }
        if supplied_target and not normalized_field_path:
            return {
                "status": "forbidden",
                "message": (
                    "record_evidence target arguments require field_path. "
                    "Omit object/pending target arguments to create unattached "
                    "source evidence, or provide field_path to attach the "
                    "evidence to a concrete curatable field."
                ),
                "target_requires_field_path": True,
                "supplied_target": supplied_target,
            }
        return None

    @function_tool
    async def record_evidence(
        entity: str,
        span_ids: list[str],
        evidence_record_id: str | None = None,
        object_id: str | None = None,
        pending_ref_id: str | None = None,
        field_path: str | None = None,
        object_type: str | None = None,
        validation_finding_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or replace verified evidence from read_chunk evidence span IDs.

        The backend resolves span_ids, copies exact source text into
        verified_quote, and preserves span provenance. Multiple span_ids in one
        call form one evidence unit and one evidence record; use separate calls
        for truly disjoint support. Supplying evidence_record_id replaces the
        source quote/provenance on that active-run record while preserving its
        object/field attachments unless new target args are supplied.

        Args:
            entity: Entity or object label this evidence supports.
            span_ids: Non-empty list copied from read_chunk(...).chunk.evidence_spans[].span_id.
            evidence_record_id: Existing active-run evidence record ID to update in place.
            object_id: Optional stable curatable object ID to attach this evidence to.
            pending_ref_id: Optional pending object/reference ID to attach this evidence to.
            field_path: Concrete domain payload field path supported by this evidence when attaching it at record time.
            object_type: Optional curatable object type.
            validation_finding_id: Optional validation finding this evidence addresses.
        """
        if tracker:
            tracker.record_call("record_evidence")

        normalized_entity = str(entity or "").strip()
        normalized_evidence_record_id = _optional_output_string(evidence_record_id)
        scope_error = _scope_error_for_record_id(normalized_evidence_record_id)
        if scope_error is not None:
            return _log_record_evidence_result(
                {"entity": normalized_entity, **scope_error},
                document_id=document_id,
                verification_method="evidence_record_scope",
            )
        target_scope_error = _target_scope_error(
            object_id=object_id,
            pending_ref_id=pending_ref_id,
            field_path=field_path,
        )
        if target_scope_error is not None:
            return _log_record_evidence_result(
                {
                    "entity": normalized_entity,
                    **(
                        {"evidence_record_id": normalized_evidence_record_id}
                        if normalized_evidence_record_id
                        else {}
                    ),
                    **target_scope_error,
                },
                document_id=document_id,
                verification_method="evidence_target_scope",
            )
        target_argument_error = _target_argument_error(
            object_id=object_id,
            pending_ref_id=pending_ref_id,
            object_type=object_type,
            field_path=field_path,
            validation_finding_id=validation_finding_id,
        )
        if target_argument_error is not None:
            return _log_record_evidence_result(
                {
                    "entity": normalized_entity,
                    **(
                        {"evidence_record_id": normalized_evidence_record_id}
                        if normalized_evidence_record_id
                        else {}
                    ),
                    **target_argument_error,
                },
                document_id=document_id,
                verification_method="evidence_target_arguments",
            )
        normalized_span_ids, span_ids_error = _normalize_span_ids(span_ids)
        envelope_target_fields = _build_envelope_target_fields(
            object_id=object_id,
            pending_ref_id=pending_ref_id,
            object_type=object_type,
            field_path=field_path,
            validation_finding_id=validation_finding_id,
        )
        error_extra_fields = _merge_extra_fields(
            envelope_target_fields,
            (
                {"evidence_record_id": normalized_evidence_record_id}
                if normalized_evidence_record_id
                else {}
            ),
        )
        existing_record: dict[str, Any] | None = None

        if normalized_evidence_record_id:
            if workspace_records is not None:
                existing_record = find_evidence_record_in_records(
                    workspace_records,
                    normalized_evidence_record_id,
                    document_id=document_id,
                )
            else:
                try:
                    existing_record = find_active_evidence_record(
                        normalized_evidence_record_id,
                        document_id=document_id,
                    )
                except RuntimeError:
                    return _log_record_evidence_result(
                        {
                            "status": "forbidden",
                            "entity": normalized_entity,
                            "evidence_record_id": normalized_evidence_record_id,
                            "message": (
                                "Existing evidence updates require an active evidence workspace. "
                                "The evidence record was not updated."
                            ),
                        },
                        document_id=document_id,
                        verification_method="missing_active_workspace",
                    )

            if existing_record is None:
                return _log_record_evidence_result(
                    {
                        "status": "not_found",
                        "entity": normalized_entity,
                        "evidence_record_id": normalized_evidence_record_id,
                        "message": (
                            "Evidence record was not found in the active run workspace. "
                            "The evidence record was not updated."
                        ),
                    },
                    document_id=document_id,
                    verification_method="unknown_evidence_record_id",
                )

            if evidence_record_status(existing_record) == "discarded":
                return _log_record_evidence_result(
                    {
                        "status": "discarded",
                        "entity": normalized_entity,
                        "evidence_record_id": normalized_evidence_record_id,
                        "message": "Discarded evidence cannot be source-updated.",
                    },
                    document_id=document_id,
                    verification_method="discarded_evidence_record_id",
                )

        if span_ids_error is not None:
            return _log_record_evidence_result(
                _build_span_resolution_error_result(
                    entity=normalized_entity,
                    span_ids=normalized_span_ids,
                    extra_fields=error_extra_fields,
                    **span_ids_error,
                ),
                document_id=document_id,
                verification_method="invalid_span_ids",
            )

        logger.info(
            "Resolving evidence spans for entity '%s' for document %s",
            normalized_entity,
            document_id[:8],
        )

        chunk_ids_by_span_id: dict[str, str] = {}
        for index, span_id in enumerate(normalized_span_ids):
            try:
                parsed = parse_evidence_span_id(span_id)
            except (TypeError, EvidenceSpanResolutionError) as exc:
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=span_id,
                        failed_span_index=index,
                        failed_span_error=f"{exc}. Call read_chunk again for current span IDs.",
                        extra_fields=error_extra_fields,
                    ),
                    document_id=document_id,
                    verification_method="invalid_span_id",
                )
            chunk_ids_by_span_id[span_id] = parsed.chunk_id

        chunks_by_id: dict[str, dict[str, Any]] = {}
        for chunk_id in dict.fromkeys(chunk_ids_by_span_id.values()):
            try:
                chunk = await get_chunk_by_id(
                    chunk_id=chunk_id,
                    user_id=user_id,
                    document_id=document_id,
                )
            except Exception as exc:
                logger.error("Failed to load chunk %s for record_evidence: %s", chunk_id, exc, exc_info=True)
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=next(
                            span_id
                            for span_id, span_chunk_id in chunk_ids_by_span_id.items()
                            if span_chunk_id == chunk_id
                        ),
                        failed_span_error="Chunk could not be loaded for this span ID",
                        chunk_id=chunk_id,
                        extra_fields=error_extra_fields,
                    ),
                    document_id=document_id,
                    verification_method="chunk_load_error",
                )

            if chunk is None:
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=next(
                            span_id
                            for span_id, span_chunk_id in chunk_ids_by_span_id.items()
                            if span_chunk_id == chunk_id
                        ),
                        failed_span_error=(
                            "Chunk referenced by this span ID was not found in the active document"
                        ),
                        chunk_id=chunk_id,
                        extra_fields=error_extra_fields,
                    ),
                    document_id=document_id,
                    verification_method="missing_chunk",
                )

            chunks_by_id[chunk_id] = chunk

        fragments: list[_ResolvedSpanFragment] = []
        for index, span_id in enumerate(normalized_span_ids):
            chunk_id = chunk_ids_by_span_id[span_id]
            chunk = chunks_by_id[chunk_id]
            chunk_text = _resolve_exact_chunk_text(chunk)
            if chunk_text is None:
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=span_id,
                        failed_span_index=index,
                        failed_span_error=(
                            "Chunk referenced by this span ID has no exact raw text content"
                        ),
                        chunk_id=chunk_id,
                        extra_fields=error_extra_fields,
                    ),
                    document_id=document_id,
                    verification_method="missing_chunk_text",
                )

            page = _resolve_chunk_page(chunk)
            section = _resolve_chunk_section(chunk)
            subsection = _resolve_chunk_subsection(chunk)
            try:
                span = resolve_evidence_span_id(
                    span_id=span_id,
                    chunk_text=chunk_text,
                    expected_chunk_id=chunk_id,
                    page_number=page,
                    section_title=section,
                )
            except EvidenceSpanResolutionError as exc:
                return _log_record_evidence_result(
                    _build_span_resolution_error_result(
                        entity=normalized_entity,
                        span_ids=normalized_span_ids,
                        failed_span_id=span_id,
                        failed_span_index=index,
                        failed_span_error=f"{exc}. Call read_chunk again for current span IDs.",
                        chunk_id=chunk_id,
                        chunk_text=chunk_text,
                        extra_fields=error_extra_fields,
                    ),
                    document_id=document_id,
                    verification_method="stale_span_id",
                )

            fragments.append(
                _ResolvedSpanFragment(
                    span=span,
                    chunk=chunk,
                    chunk_id=chunk_id,
                    document_id=document_id,
                    page=page,
                    section=section,
                    subsection=subsection,
                    figure_reference=_extract_figure_reference(
                        chunk,
                        chunk_text,
                        span.text,
                    ),
                )
            )

        verified_quote = "\n\n".join(fragment.span.text for fragment in fragments)
        first_fragment = fragments[0]
        chunk_ids = _unique_non_empty([fragment.chunk_id for fragment in fragments])
        figure_references = _unique_non_empty(
            [fragment.figure_reference for fragment in fragments]
        )

        payload: dict[str, Any] = {
            "status": "verified",
            "entity": normalized_entity,
            "span_ids": normalized_span_ids,
            "source_span_ids": normalized_span_ids,
            "verified_quote": verified_quote,
            "document_id": document_id,
            "chunk_id": first_fragment.chunk_id,
            "chunk_ids": chunk_ids,
            "source_fragments": [
                fragment.to_source_fragment()
                for fragment in fragments
            ],
        }
        payload.update(envelope_target_fields)
        if first_fragment.page is not None:
            payload["page"] = first_fragment.page
        if first_fragment.section:
            payload["section"] = first_fragment.section
        if first_fragment.subsection:
            payload["subsection"] = first_fragment.subsection
        if len(figure_references) == 1:
            payload["figure_reference"] = figure_references[0]

        payload["evidence_record_id"] = (
            normalized_evidence_record_id
            or build_evidence_record_id(
                evidence_record={
                    "entity": normalized_entity,
                    "verified_quote": verified_quote,
                    "page": first_fragment.page,
                    "section": first_fragment.section,
                    "chunk_id": first_fragment.chunk_id,
                    "subsection": first_fragment.subsection,
                    "figure_reference": payload.get("figure_reference"),
                    "source_span_ids": normalized_span_ids,
                }
            )
        )

        if existing_record is not None:
            revision_history = _revision_history_with_previous_source(existing_record)
            if envelope_target_fields:
                _project_primary_target_fields(payload)
            else:
                _copy_existing_record_fields(
                    payload,
                    existing_record,
                    _ATTACHMENT_METADATA_FIELDS,
                )
            _copy_existing_record_fields(
                payload,
                existing_record,
                _PRESERVED_METADATA_FIELDS,
            )
            if revision_history:
                payload["evidence_revision_history"] = revision_history
            payload["updated_at"] = _now_iso()
            existing_record.clear()
            existing_record.update(payload)

        return _log_record_evidence_result(
            _strip_hidden_revision_history(payload),
            document_id=document_id,
            verification_method="span_ids",
        )

    return record_evidence


__all__ = [
    "create_record_evidence_tool",
]
