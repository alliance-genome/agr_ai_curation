"""Extraction timeline analysis for durable backend trace events."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .tool_calls import ToolCallAnalyzer

ANALYZER_SCHEMA_VERSION = "extraction_timeline_analyzer.v1"
EVENT_SCHEMA_VERSION = "extraction_trace_event.v1"
EVIDENCE_REVISIONS_SCHEMA_VERSION = "evidence_revisions.v1"
SUMMARY_TEXT_LIMIT = 500
PAYLOAD_SIZE_TOP_EVENT_COUNT = 20
PAYLOAD_SIZE_WARNING_THRESHOLDS = (100_000, 500_000, 1_000_000)
DIAGNOSTIC_STRING_PARSE_CHARS = 1_000_000
DIAGNOSTIC_MAX_DEPTH = 10
DIAGNOSTIC_MAX_LIST_ITEMS = 200


def _trace_event_dir() -> Path:
    configured = os.getenv("EXTRACTION_TRACE_EVENT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".ai_curation" / "extraction_trace_events"


def _trace_event_path(trace_id: str) -> Path:
    safe_trace_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(trace_id or "unknown"))
    return _trace_event_dir() / f"{safe_trace_id}.jsonl"


def _sort_key(item: Mapping[str, Any]) -> tuple[str, int]:
    return (
        str(item.get("timestamp") or item.get("time") or ""),
        int(item.get("sequence") or 0),
    )


def _bounded_summary_text(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(
        value,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    if len(text) <= SUMMARY_TEXT_LIMIT:
        return text
    return f"{text[: SUMMARY_TEXT_LIMIT - 3]}..."


def _estimated_tokens(char_count: int) -> int:
    return math.ceil(char_count / 4) if char_count > 0 else 0


def _json_size(value: Any) -> Dict[str, Any]:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    except Exception:
        text = str(value)
    json_chars = len(text)
    return {
        "json_chars": json_chars,
        "json_bytes": len(text.encode("utf-8")),
        "estimated_tokens": _estimated_tokens(json_chars),
        "source": "computed_json",
    }


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _normalized_size_payload(value: Mapping[str, Any], *, source: str) -> Dict[str, Any]:
    json_chars = _coerce_int(value.get("json_chars"))
    json_bytes = _coerce_int(value.get("json_bytes"))
    string_chars = _coerce_int(value.get("string_chars"))
    if json_chars is None:
        json_chars = string_chars or 0
    if json_bytes is None:
        json_bytes = json_chars
    estimated_tokens = _coerce_int(value.get("estimated_tokens"))
    if estimated_tokens is None:
        estimated_tokens = _estimated_tokens(json_chars)
    normalized = {
        "json_chars": json_chars,
        "json_bytes": json_bytes,
        "estimated_tokens": estimated_tokens,
        "source": value.get("source") or source,
    }
    for key in ("kind", "string_chars", "string_bytes", "item_count", "top_level_keys"):
        if key in value:
            normalized[key] = value[key]
    return normalized


def _summary_payload_size(summary: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(summary, Mapping):
        return {"json_chars": 0, "json_bytes": 0, "estimated_tokens": 0, "source": "missing"}
    size = summary.get("size")
    if isinstance(size, Mapping):
        return _normalized_size_payload(size, source="summary_size")
    preview = summary.get("preview")
    if isinstance(preview, Mapping):
        raw_length = _coerce_int(preview.get("length"))
        if raw_length is not None:
            return {
                "json_chars": raw_length,
                "json_bytes": raw_length,
                "estimated_tokens": _estimated_tokens(raw_length),
                "source": "truncated_preview_length",
                "kind": "string",
            }
    preview_length = _coerce_int(summary.get("preview_length"))
    if preview_length is not None:
        return {
            "json_chars": preview_length,
            "json_bytes": preview_length,
            "estimated_tokens": _estimated_tokens(preview_length),
            "source": "preview_length",
        }
    return _json_size(preview)


def _event_payload_size(event: Mapping[str, Any]) -> Dict[str, Any]:
    event_size = event.get("event_size")
    if isinstance(event_size, Mapping):
        return _normalized_size_payload(event_size, source="event_size")
    return _json_size(event)


def _size_chars(size: Mapping[str, Any]) -> int:
    value = size.get("json_chars")
    return value if isinstance(value, int) else 0


def _build_timeline_payload_size(
    event: Mapping[str, Any],
    *,
    input_size: Mapping[str, Any],
    output_size: Mapping[str, Any],
    event_size: Mapping[str, Any],
) -> Dict[str, Any]:
    input_chars = _size_chars(input_size)
    output_chars = _size_chars(output_size)
    event_chars = _size_chars(event_size)
    total_exchange_chars = input_chars + output_chars
    max_chars = max(input_chars, output_chars, event_chars)
    payload = {
        "input_json_chars": input_chars,
        "output_json_chars": output_chars,
        "event_json_chars": event_chars,
        "exchange_json_chars": total_exchange_chars,
        "max_json_chars": max_chars,
        "estimated_exchange_tokens": _estimated_tokens(total_exchange_chars),
        "estimated_max_tokens": _estimated_tokens(max_chars),
    }
    event_summary = event.get("payload_size_summary")
    if isinstance(event_summary, Mapping):
        payload["writer_summary"] = dict(event_summary)
    return payload


def _payload_size_summary(timeline: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = {
        "input_json_chars": 0,
        "output_json_chars": 0,
        "event_json_chars": 0,
        "exchange_json_chars": 0,
    }
    by_event_type: Dict[str, Dict[str, Any]] = {}
    largest: List[Dict[str, Any]] = []
    threshold_counts = {str(threshold): 0 for threshold in PAYLOAD_SIZE_WARNING_THRESHOLDS}

    for item in timeline:
        payload_size = item.get("payload_size")
        if not isinstance(payload_size, Mapping):
            continue
        event_type = str(item.get("event_type") or "unknown")
        bucket = by_event_type.setdefault(
            event_type,
            {
                "event_count": 0,
                "input_json_chars": 0,
                "output_json_chars": 0,
                "event_json_chars": 0,
                "exchange_json_chars": 0,
                "max_json_chars": 0,
            },
        )
        bucket["event_count"] += 1
        for key in totals:
            value = _coerce_int(payload_size.get(key)) or 0
            totals[key] += value
            bucket[key] += value
        max_chars = _coerce_int(payload_size.get("max_json_chars")) or 0
        bucket["max_json_chars"] = max(bucket["max_json_chars"], max_chars)
        for threshold in PAYLOAD_SIZE_WARNING_THRESHOLDS:
            if max_chars >= threshold:
                threshold_counts[str(threshold)] += 1
        for direction, size_key in (
            ("input", "input_size"),
            ("output", "output_size"),
            ("event", "event_size"),
        ):
            size = item.get(size_key)
            if not isinstance(size, Mapping):
                continue
            json_chars = _size_chars(size)
            if json_chars <= 0:
                continue
            largest.append(
                {
                    "rank": 0,
                    "direction": direction,
                    "json_chars": json_chars,
                    "json_bytes": size.get("json_bytes"),
                    "estimated_tokens": _estimated_tokens(json_chars),
                    "source": size.get("source"),
                    "timeline_index": item.get("index"),
                    "sequence": item.get("sequence"),
                    "event_trace_id": item.get("event_trace_id"),
                    "event_type": item.get("event_type"),
                    "event_id": item.get("event_id"),
                    "tool_name": item.get("tool_name"),
                    "agent": item.get("agent"),
                    "domain_pack_id": item.get("domain_pack_id"),
                }
            )

    largest = sorted(largest, key=lambda item: int(item.get("json_chars") or 0), reverse=True)[
        :PAYLOAD_SIZE_TOP_EVENT_COUNT
    ]
    for index, item in enumerate(largest, start=1):
        item["rank"] = index

    totals["estimated_exchange_tokens"] = _estimated_tokens(totals["exchange_json_chars"])
    totals["estimated_event_tokens"] = _estimated_tokens(totals["event_json_chars"])
    return {
        **totals,
        "largest_events": largest,
        "by_event_type": by_event_type,
        "threshold_counts": threshold_counts,
    }


def _summary_text(summary: Mapping[str, Any] | None) -> str:
    if not isinstance(summary, Mapping):
        return ""
    preview = summary.get("preview")
    if isinstance(preview, Mapping):
        if "summary_text" in preview:
            return str(preview.get("summary_text") or "")
        if "message" in preview:
            return str(preview.get("message") or "")
        parts = []
        for key in ("status", "summary"):
            value = preview.get(key)
            if value not in (None, ""):
                parts.append(f"{key}: {_bounded_summary_text(value)}")
        if parts:
            return "; ".join(parts)
        if preview:
            return _bounded_summary_text(preview)
    if isinstance(preview, str):
        return preview
    return ""


def _coerce_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def _coerce_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text or text[0] not in "{[" or len(text) > DIAGNOSTIC_STRING_PARSE_CHARS:
        return value

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _text_value(value: Any) -> str | None:
    value = _coerce_jsonish(value)
    if isinstance(value, Mapping):
        preview = value.get("preview")
        if isinstance(preview, str):
            text = " ".join(preview.split())
            return text or None
        return None
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _text_preview(value: Any, *, limit: int = 240) -> str | None:
    text = _text_value(value)
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _string_list(value: Any) -> List[str]:
    value = _coerce_jsonish(value)
    if not isinstance(value, list):
        return []
    strings: List[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text_value(item)
        if not text or text in seen:
            continue
        seen.add(text)
        strings.append(text)
    return strings


def _iter_path_values(
    value: Any,
    path: str,
    *,
    depth: int = 0,
) -> Iterable[tuple[str, Any]]:
    if depth > DIAGNOSTIC_MAX_DEPTH:
        return

    coerced = _coerce_jsonish(value)
    yield path, coerced

    if isinstance(coerced, Mapping):
        for key, nested in coerced.items():
            yield from _iter_path_values(
                nested,
                f"{path}.{key}" if path else str(key),
                depth=depth + 1,
            )
        return

    if isinstance(coerced, list):
        for index, nested in enumerate(coerced[:DIAGNOSTIC_MAX_LIST_ITEMS]):
            yield from _iter_path_values(nested, f"{path}[{index}]", depth=depth + 1)


def _normalized_evidence_targets(record: Mapping[str, Any]) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []

    def append_target(raw_target: Any) -> None:
        raw_target = _coerce_jsonish(raw_target)
        if not isinstance(raw_target, Mapping):
            return
        target = {
            key: text
            for key in (
                "object_id",
                "pending_ref_id",
                "object_type",
                "field_path",
                "validation_finding_id",
            )
            for text in [_text_value(raw_target.get(key))]
            if text
        }
        if target and target not in targets:
            targets.append(target)

    append_target(record.get("envelope_target"))
    raw_targets = _coerce_jsonish(record.get("envelope_targets"))
    if isinstance(raw_targets, list):
        for raw_target in raw_targets:
            append_target(raw_target)

    fallback_target = {
        key: text
        for key in ("object_id", "pending_ref_id", "field_path")
        for text in [_text_value(record.get(key))]
        if text
    }
    if fallback_target and fallback_target not in targets:
        targets.append(fallback_target)
    return targets


def _record_field_paths(record: Mapping[str, Any]) -> List[str]:
    paths = _string_list(record.get("field_paths"))
    primary = _text_value(record.get("field_path"))
    if primary and primary not in paths:
        paths.insert(0, primary)
    for target in _normalized_evidence_targets(record):
        field_path = _text_value(target.get("field_path"))
        if field_path and field_path not in paths:
            paths.append(field_path)
    return paths


def _source_snapshot_summary(source: Mapping[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    quote_preview = _text_preview(source.get("verified_quote"))
    if quote_preview:
        summary["verified_quote_preview"] = quote_preview

    span_ids = _string_list(source.get("source_span_ids")) or _string_list(source.get("span_ids"))
    if span_ids:
        summary["source_span_ids"] = span_ids

    chunk_ids = _string_list(source.get("chunk_ids"))
    chunk_id = _text_value(source.get("chunk_id"))
    if chunk_id:
        summary["chunk_id"] = chunk_id
        if chunk_id not in chunk_ids:
            chunk_ids.insert(0, chunk_id)
    if chunk_ids:
        summary["chunk_ids"] = chunk_ids

    for key in ("document_id", "page", "section", "subsection", "figure_reference"):
        text = _text_value(source.get(key))
        if text:
            summary[key] = text

    return summary


def _event_reference(item: Mapping[str, Any], source_path: str) -> Dict[str, Any]:
    return {
        "timeline_index": item.get("index"),
        "timestamp": item.get("timestamp"),
        "event_trace_id": item.get("event_trace_id"),
        "event_type": item.get("event_type"),
        "event_id": item.get("event_id"),
        "tool_name": item.get("tool_name"),
        "agent": item.get("agent"),
        "source": item.get("source"),
        "source_path": source_path,
    }


def _changed_by(item: Mapping[str, Any], revision: Mapping[str, Any]) -> Dict[str, Any]:
    raw_changed_by = revision.get("changed_by") or revision.get("changed_by_context")
    if isinstance(raw_changed_by, Mapping):
        changed_by = {
            key: value
            for key, value in raw_changed_by.items()
            if value not in (None, "", [])
        }
    else:
        changed_by = {}
        actor = _text_value(raw_changed_by)
        if actor:
            changed_by["actor"] = actor

    for key in ("agent", "tool_name", "event_type", "event_trace_id"):
        value = _text_value(item.get(key))
        if value and key not in changed_by:
            changed_by[key] = value
    return changed_by


def _revision_reason(item: Mapping[str, Any], revision: Mapping[str, Any]) -> str:
    explicit_reason = _text_value(revision.get("reason") or revision.get("change_reason"))
    if explicit_reason:
        return explicit_reason
    if item.get("tool_name") == "record_evidence":
        return "same_id_record_evidence_source_update"
    event_type = _text_value(item.get("event_type"))
    return event_type or "evidence_revision_history"


def _revision_summary(
    revision: Mapping[str, Any],
    *,
    record: Mapping[str, Any],
    item: Mapping[str, Any],
) -> Dict[str, Any]:
    previous_source = revision.get("previous_source")
    if not isinstance(previous_source, Mapping):
        previous_source = {}
    replaced_at = _text_value(
        revision.get("replaced_at")
        or revision.get("changed_at")
        or revision.get("updated_at")
    )
    summary: Dict[str, Any] = {
        "revision": revision.get("revision"),
        "replaced_at": replaced_at,
        "reason": _revision_reason(item, revision),
        "changed_by": _changed_by(item, revision),
        "before_quote_preview": _text_preview(previous_source.get("verified_quote")),
        "after_quote_preview": _text_preview(record.get("verified_quote")),
        "previous_source": _source_snapshot_summary(previous_source),
        "current_source": _source_snapshot_summary(record),
    }
    return {
        key: value
        for key, value in summary.items()
        if value not in (None, "", [], {})
    }


def _looks_like_evidence_record(payload: Mapping[str, Any]) -> bool:
    return bool(
        _text_value(payload.get("evidence_record_id"))
        and (
            "evidence_revision_history" in payload
            or "verified_quote" in payload
            or "source_span_ids" in payload
            or "span_ids" in payload
        )
    )


def _evidence_record_revision_summary(
    record: Mapping[str, Any],
    *,
    item: Mapping[str, Any],
    source_path: str,
) -> Dict[str, Any] | None:
    evidence_record_id = _text_value(record.get("evidence_record_id"))
    if not evidence_record_id:
        return None

    raw_history = _coerce_jsonish(record.get("evidence_revision_history"))
    history = [entry for entry in raw_history if isinstance(entry, Mapping)] if isinstance(raw_history, list) else []
    if not history:
        return None

    revision_summaries = [
        _revision_summary(revision, record=record, item=item)
        for revision in history
    ]
    replacement_times = [
        text
        for revision in revision_summaries
        for text in [_text_value(revision.get("replaced_at"))]
        if text
    ]

    target = {
        key: value
        for key, value in {
            "field_paths": _record_field_paths(record),
            "envelope_targets": _normalized_evidence_targets(record),
        }.items()
        if value
    }

    summary = {
        "evidence_record_id": evidence_record_id,
        "entity": _text_value(record.get("entity")),
        "changed": True,
        "revision_count": len(revision_summaries),
        "first_replaced_at": replacement_times[0] if replacement_times else None,
        "last_replaced_at": replacement_times[-1] if replacement_times else None,
        "current_source": _source_snapshot_summary(record),
        "target": target,
        "revisions": revision_summaries,
        "source_events": [_event_reference(item, source_path)],
    }
    return {
        key: value
        for key, value in summary.items()
        if value not in (None, "", [], {})
    }


def _merge_event_references(
    first: List[Dict[str, Any]],
    second: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for event in [*first, *second]:
        key = (
            event.get("timeline_index"),
            event.get("event_id"),
            event.get("source_path"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
    return merged


def _scope_refusal_diagnostic(message: str) -> str:
    lowered = message.lower()
    if "retarget" in lowered:
        return "Validator attempted to retarget evidence outside its supplied object or field scope; backend refused the mutation."
    if "only update" in lowered or "supplied" in lowered or "scope" in lowered:
        return "Validator attempted to update evidence outside its supplied target scope; backend refused the mutation."
    return "Backend refused an evidence mutation; inspect allowed evidence IDs and target fields before retrying."


def _evidence_scope_refusal(
    payload: Mapping[str, Any],
    *,
    item: Mapping[str, Any],
    source_path: str,
) -> Dict[str, Any] | None:
    status = (_text_value(payload.get("status")) or "").lower()
    if status != "forbidden":
        return None

    message = _text_value(payload.get("message") or payload.get("error")) or ""
    evidence_keys = {
        "evidence_record_id",
        "allowed_evidence_record_ids",
        "target_field_path",
        "allowed_object_id",
        "allowed_pending_ref_id",
    }
    if (
        not any(key in payload for key in evidence_keys)
        and "evidence" not in message.lower()
        and "validator" not in message.lower()
    ):
        return None

    refusal: Dict[str, Any] = {
        **_event_reference(item, source_path),
        "status": "forbidden",
        "message": message,
        "diagnostic": _scope_refusal_diagnostic(message),
        "evidence_record_id": _text_value(payload.get("evidence_record_id")),
        "allowed_evidence_record_ids": _string_list(payload.get("allowed_evidence_record_ids")),
        "target_field_path": _text_value(payload.get("target_field_path")),
        "allowed_object_id": _text_value(payload.get("allowed_object_id")),
        "allowed_pending_ref_id": _text_value(payload.get("allowed_pending_ref_id")),
    }
    return {
        key: value
        for key, value in refusal.items()
        if value not in (None, "", [], {})
    }


def _event_matches(
    event: Mapping[str, Any],
    *,
    tool_name: str | None,
    event_type: str | None,
    candidate_id: str | None,
) -> bool:
    if tool_name:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), Mapping) else {}
        input_summary = event.get("input_summary") if isinstance(event.get("input_summary"), Mapping) else {}
        haystack = json.dumps([metadata, input_summary], sort_keys=True, default=str)
        if tool_name not in haystack:
            return False
    if event_type and event.get("event_type") != event_type:
        return False
    if candidate_id:
        haystack = json.dumps(event, sort_keys=True, default=str)
        if candidate_id not in haystack:
            return False
    return True


def _langfuse_extraction_event_from_observation(observation: Mapping[str, Any]) -> Dict[str, Any] | None:
    if observation.get("name") != "extraction_trace_event":
        return None

    for key in ("input", "output", "metadata"):
        event = _coerce_mapping(observation.get(key))
        if event and event.get("schema_version") == EVENT_SCHEMA_VERSION:
            return dict(event)
    return None


def feedback_trace_sibling_ids(
    trace_id: str,
    feedback_trace_data: Mapping[str, Any] | None,
) -> List[str]:
    """Return sibling trace IDs present in a stored feedback trace artifact."""

    if not isinstance(feedback_trace_data, Mapping):
        return []

    traces = feedback_trace_data.get("traces")
    if not isinstance(traces, list):
        return []

    sibling_ids: List[str] = []
    seen = {trace_id}
    for trace in traces:
        if not isinstance(trace, Mapping):
            continue
        event_trace_id = str(trace.get("trace_id") or trace.get("id") or "")
        if not event_trace_id or event_trace_id in seen:
            continue
        seen.add(event_trace_id)
        sibling_ids.append(event_trace_id)
    return sibling_ids


def _feedback_trace_events(
    *,
    trace_id: str,
    feedback_trace_data: Mapping[str, Any] | None,
    sibling_trace_ids: Iterable[str],
) -> List[Dict[str, Any]]:
    if not isinstance(feedback_trace_data, Mapping):
        return []

    allowed_trace_ids = {trace_id, *sibling_trace_ids}
    events: List[Dict[str, Any]] = []
    traces = feedback_trace_data.get("traces")
    if not isinstance(traces, list):
        return events

    sequence = 1
    for trace in traces:
        if not isinstance(trace, Mapping):
            continue
        event_trace_id = str(trace.get("trace_id") or "")
        if not event_trace_id or event_trace_id not in allowed_trace_ids:
            continue

        timestamp = trace.get("timestamp") or feedback_trace_data.get("captured_at")
        tool_calls = trace.get("tool_calls")
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, Mapping):
                    continue
                tool_name = tool_call.get("name")
                events.append(
                    {
                        "schema_version": EVENT_SCHEMA_VERSION,
                        "event_type": "stored_feedback.tool_call",
                        "event_id": f"feedback-{event_trace_id}-tool-{sequence}",
                        "sequence": sequence,
                        "trace_id": event_trace_id,
                        "observation_id": None,
                        "domain_pack_id": None,
                        "tool_call_id": None,
                        "input_summary": {},
                        "output_summary": {
                            "preview": {
                                "status": tool_call.get("status"),
                                "duration_ms": tool_call.get("duration_ms"),
                            },
                            "bounded": True,
                        },
                        "validation": {},
                        "metadata": {
                            "tool_name": tool_name,
                            "source": "stored_feedback_trace_artifact",
                        },
                        "timestamp": timestamp,
                    }
                )
                sequence += 1

        if trace.get("capture_status") == "error":
            raw_error = trace.get("error")
            error: Mapping[str, Any] = raw_error if isinstance(raw_error, Mapping) else {}
            events.append(
                {
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "event_type": "stored_feedback.trace_capture_error",
                    "event_id": f"feedback-{event_trace_id}-error-{sequence}",
                    "sequence": sequence,
                    "trace_id": event_trace_id,
                    "observation_id": None,
                    "domain_pack_id": None,
                    "tool_call_id": None,
                    "input_summary": {},
                    "output_summary": {
                        "preview": {
                            "type": error.get("type"),
                            "message": error.get("message"),
                        },
                        "bounded": True,
                    },
                    "validation": {"status": "failed"},
                    "metadata": {"source": "stored_feedback_trace_artifact"},
                    "timestamp": timestamp,
                }
            )
            sequence += 1

    return events


class ExtractionTimelineAnalyzer:
    """Build ordered extraction diagnostics from durable events and observations."""

    @staticmethod
    def load_durable_events(trace_id: str) -> List[Dict[str, Any]]:
        path = _trace_event_path(trace_id)
        if not path.exists():
            return []
        events: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("schema_version") != EVENT_SCHEMA_VERSION:
                continue
            if str(event.get("trace_id") or "") != trace_id:
                continue
            events.append(event)
        return sorted(events, key=_sort_key)

    @staticmethod
    def _observation_tool_events(trace_id: str, observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        tool_calls = ToolCallAnalyzer.extract_tool_calls(observations).get("tool_calls", [])
        events: List[Dict[str, Any]] = []
        for index, call in enumerate(tool_calls, start=1):
            event_type = (
                "openai_agents.function_call"
                if call.get("call_id") and call.get("call_id") != "N/A"
                else "legacy_tool.call"
            )
            events.append(
                {
                    "schema_version": EVENT_SCHEMA_VERSION,
                    "event_type": event_type,
                    "event_id": f"obs-tool-{trace_id}-{index}",
                    "sequence": index,
                    "trace_id": trace_id,
                    "observation_id": call.get("id"),
                    "domain_pack_id": None,
                    "tool_call_id": call.get("call_id"),
                    "input_summary": {"preview": call.get("input") or {}, "bounded": True},
                    "output_summary": {
                        "preview": call.get("tool_result") or call.get("output") or {},
                        "bounded": True,
                    },
                    "validation": {},
                    "metadata": {
                        "tool_name": call.get("name"),
                        "model": call.get("model"),
                        "source": event_type,
                    },
                    "timestamp": call.get("time"),
                }
            )
        return events

    @staticmethod
    def _observation_durable_events(trace_id: str, observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for observation in observations:
            event = _langfuse_extraction_event_from_observation(observation)
            if event is not None:
                if not event.get("trace_id"):
                    event["trace_id"] = trace_id
                events.append(event)
        return sorted(events, key=_sort_key)

    @staticmethod
    def analyze(
        *,
        trace_id: str,
        raw_trace: Dict[str, Any] | None,
        observations: List[Dict[str, Any]],
        include_raw_args: bool = False,
        include_raw_outputs: bool = False,
        tool_name: str | None = None,
        event_type: str | None = None,
        candidate_id: str | None = None,
        sibling_trace_ids: Optional[Iterable[str]] = None,
        sibling_observations_by_trace_id: Optional[Mapping[str, List[Dict[str, Any]]]] = None,
        feedback_trace_data: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        sibling_ids = [sibling_id for sibling_id in (sibling_trace_ids or []) if sibling_id != trace_id]
        observations_by_trace_id = {trace_id: observations}
        for sibling_id in sibling_ids:
            sibling_observations = (sibling_observations_by_trace_id or {}).get(sibling_id)
            if isinstance(sibling_observations, list):
                observations_by_trace_id[sibling_id] = sibling_observations

        durable_events = ExtractionTimelineAnalyzer.load_durable_events(trace_id)
        for sibling_id in sibling_ids:
            durable_events.extend(ExtractionTimelineAnalyzer.load_durable_events(sibling_id))
        durable_event_ids = {event.get("event_id") for event in durable_events}
        observation_durable_events: List[Dict[str, Any]] = []
        observation_durable_event_ids = set(durable_event_ids)
        for observation_trace_id, trace_observations in observations_by_trace_id.items():
            for event in ExtractionTimelineAnalyzer._observation_durable_events(observation_trace_id, trace_observations):
                event_id = event.get("event_id")
                if event_id in observation_durable_event_ids:
                    continue
                observation_durable_event_ids.add(event_id)
                observation_durable_events.append(event)

        feedback_events = [
            event
            for event in _feedback_trace_events(
                trace_id=trace_id,
                feedback_trace_data=feedback_trace_data,
                sibling_trace_ids=sibling_ids,
            )
            if event.get("event_id") not in durable_event_ids
        ]
        observation_events = [
            event
            for observation_trace_id, trace_observations in observations_by_trace_id.items()
            for event in ExtractionTimelineAnalyzer._observation_tool_events(observation_trace_id, trace_observations)
        ]

        durable_sources = [*durable_events, *observation_durable_events, *feedback_events]
        durable_event_ids = {event.get("event_id") for event in durable_sources}
        combined = [*durable_sources, *observation_events]
        filtered = [
            event
            for event in sorted(combined, key=_sort_key)
            if _event_matches(
                event,
                tool_name=tool_name,
                event_type=event_type,
                candidate_id=candidate_id,
            )
        ]

        reasoning_events = [
            event for event in filtered if str(event.get("event_type") or "").startswith("model.reasoning_summary")
        ]
        summary_outputs = [
            _summary_text(event.get("output_summary"))
            for event in reasoning_events
            if event.get("event_type") == "model.reasoning_summary.output"
        ]
        request_events = [
            event for event in reasoning_events if event.get("event_type") == "model.reasoning_summary.request"
        ]
        request_statuses = [
            (
                (event.get("input_summary") or {}).get("preview", {}).get("availability")
                if isinstance((event.get("input_summary") or {}).get("preview"), Mapping)
                else None
            )
            for event in request_events
        ]
        if summary_outputs:
            reasoning_status = "present"
        elif "not_requested" in request_statuses:
            reasoning_status = "not_requested"
        elif "not_supported" in request_statuses:
            reasoning_status = "not_supported"
        else:
            reasoning_status = "unavailable"

        timeline = []
        for index, event in enumerate(filtered, start=1):
            input_size = _summary_payload_size(event.get("input_summary"))
            output_size = _summary_payload_size(event.get("output_summary"))
            event_size = _event_payload_size(event)
            item = {
                "index": index,
                "timestamp": event.get("timestamp"),
                "sequence": event.get("sequence"),
                "source": "durable_event"
                if event.get("event_id") in durable_event_ids
                else event.get("metadata", {}).get("source", "observation"),
                "event_trace_id": event.get("trace_id"),
                "event_type": event.get("event_type"),
                "event_id": event.get("event_id"),
                "observation_id": event.get("observation_id"),
                "tool_call_id": event.get("tool_call_id"),
                "domain_pack_id": event.get("domain_pack_id"),
                "tool_name": (event.get("metadata") or {}).get("tool_name"),
                "agent": (event.get("metadata") or {}).get("agent"),
                "input": event.get("input_summary") if include_raw_args else _summary_text(event.get("input_summary")),
                "output": event.get("output_summary") if include_raw_outputs else _summary_text(event.get("output_summary")),
                "input_size": input_size,
                "output_size": output_size,
                "event_size": event_size,
                "payload_size": _build_timeline_payload_size(
                    event,
                    input_size=input_size,
                    output_size=output_size,
                    event_size=event_size,
                ),
                "validation": event.get("validation") or {},
            }
            timeline.append(item)

        counts: Dict[str, int] = {}
        for event in filtered:
            key = str(event.get("event_type") or "unknown")
            counts[key] = counts.get(key, 0) + 1

        return {
            "schema_version": ANALYZER_SCHEMA_VERSION,
            "trace_id": trace_id,
            "trace_name": (raw_trace or {}).get("name"),
            "event_count": len(timeline),
            "durable_event_count": len(durable_sources),
            "local_durable_event_count": len(durable_events),
            "langfuse_durable_event_count": len(observation_durable_events),
            "feedback_artifact_event_count": len(feedback_events),
            "observation_event_count": len(observation_events),
            "event_type_counts": counts,
            "size_summary": _payload_size_summary(timeline),
            "reasoning_summary": {
                "status": reasoning_status,
                "request_settings": [
                    (event.get("input_summary") or {}).get("preview")
                    for event in request_events
                ],
                "summaries": [text for text in summary_outputs if text],
                "note": "Raw hidden chain-of-thought is not available.",
            },
            "sibling_trace_ids": sibling_ids,
            "timeline": timeline,
        }

    @staticmethod
    def diagnostic_report(timeline: Dict[str, Any]) -> Dict[str, Any]:
        validation_failures = [
            item for item in timeline.get("timeline", []) if item.get("validation", {}).get("status") in {"failed", "needs_patch"}
        ]
        tool_events = [
            item for item in timeline.get("timeline", []) if "tool" in str(item.get("event_type") or "")
        ]
        finalization_events = [
            item for item in timeline.get("timeline", []) if "final" in str(item.get("event_type") or "")
        ]
        return {
            "schema_version": timeline.get("schema_version"),
            "trace_id": timeline.get("trace_id"),
            "summary": {
                "event_count": timeline.get("event_count", 0),
                "durable_event_count": timeline.get("durable_event_count", 0),
                "tool_event_count": len(tool_events),
                "validation_failure_count": len(validation_failures),
                "finalization_event_count": len(finalization_events),
                "reasoning_summary_status": timeline.get("reasoning_summary", {}).get("status"),
                "payload_exchange_json_chars": timeline.get("size_summary", {}).get(
                    "exchange_json_chars",
                    0,
                ),
                "estimated_payload_exchange_tokens": timeline.get(
                    "size_summary", {}
                ).get("estimated_exchange_tokens", 0),
            },
            "size_summary": timeline.get("size_summary", {}),
            "reasoning_summary": timeline.get("reasoning_summary", {}),
            "validation_failures": validation_failures,
            "timeline": timeline.get("timeline", []),
        }

    @staticmethod
    def evidence_revisions(timeline: Dict[str, Any]) -> Dict[str, Any]:
        """Return an opt-in evidence revision diagnostic surface."""

        records_by_id: Dict[str, Dict[str, Any]] = {}
        scope_refusals: List[Dict[str, Any]] = []

        for item in timeline.get("timeline", []):
            if not isinstance(item, Mapping):
                continue
            searchable_payload = {
                "input": item.get("input"),
                "output": item.get("output"),
                "validation": item.get("validation"),
            }
            for source_path, payload in _iter_path_values(searchable_payload, "timeline_item"):
                if not isinstance(payload, Mapping):
                    continue
                if _looks_like_evidence_record(payload):
                    summary = _evidence_record_revision_summary(
                        payload,
                        item=item,
                        source_path=source_path,
                    )
                    if summary is not None:
                        evidence_record_id = str(summary["evidence_record_id"])
                        existing = records_by_id.get(evidence_record_id)
                        if existing is None:
                            records_by_id[evidence_record_id] = summary
                        else:
                            merged_events = _merge_event_references(
                                existing.get("source_events", []),
                                summary.get("source_events", []),
                            )
                            if int(summary.get("revision_count") or 0) >= int(
                                existing.get("revision_count") or 0
                            ):
                                summary["source_events"] = merged_events
                                records_by_id[evidence_record_id] = summary
                            else:
                                existing["source_events"] = merged_events

                refusal = _evidence_scope_refusal(
                    payload,
                    item=item,
                    source_path=source_path,
                )
                if refusal is not None:
                    refusal_key = (
                        refusal.get("timeline_index"),
                        refusal.get("event_id"),
                        refusal.get("source_path"),
                        refusal.get("message"),
                    )
                    existing_keys = {
                        (
                            existing.get("timeline_index"),
                            existing.get("event_id"),
                            existing.get("source_path"),
                            existing.get("message"),
                        )
                        for existing in scope_refusals
                    }
                    if refusal_key not in existing_keys:
                        scope_refusals.append(refusal)

        evidence_records = sorted(
            records_by_id.values(),
            key=lambda record: (
                min(
                    [
                        int(event.get("timeline_index") or 0)
                        for event in record.get("source_events", [])
                        if isinstance(event, Mapping)
                    ]
                    or [0]
                ),
                str(record.get("evidence_record_id") or ""),
            ),
        )
        revision_count = sum(int(record.get("revision_count") or 0) for record in evidence_records)
        return {
            "schema_version": EVIDENCE_REVISIONS_SCHEMA_VERSION,
            "trace_id": timeline.get("trace_id"),
            "summary": {
                "evidence_record_count": len(evidence_records),
                "updated_evidence_record_count": sum(1 for record in evidence_records if record.get("changed")),
                "revision_count": revision_count,
                "scope_refusal_count": len(scope_refusals),
                "diagnostic_note": (
                    "Live evidence fields are authoritative for product behavior; "
                    "revision history is exposed only through this diagnostic surface."
                ),
            },
            "evidence_records": evidence_records,
            "scope_refusals": scope_refusals,
            "query": timeline.get("query", {}),
        }
