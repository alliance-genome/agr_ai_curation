"""Durable extraction trace event writer for OpenAI agent runs."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "extraction_trace_event.v1"
DEFAULT_PREVIEW_LIMIT = 1200
DEFAULT_PAYLOAD_SIZE_LOG_THRESHOLD_CHARS = 500_000
MAX_EVENTS_PER_TRACE = 10000
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|authorization|bearer|password|secret|token|credential)",
    re.IGNORECASE,
)


@dataclass
class ExtractionTraceRun:
    trace_id: str
    session_id: str | None = None
    user_id_hash: str | None = None
    observation_id: str | None = None
    source: str = "backend"
    sequence: int = 0


_current_run: ContextVar[ExtractionTraceRun | None] = ContextVar(
    "extraction_trace_run",
    default=None,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _preview_limit() -> int:
    raw = os.getenv("EXTRACTION_TRACE_EVENT_PREVIEW_LIMIT", "").strip()
    if not raw:
        return DEFAULT_PREVIEW_LIMIT
    try:
        return max(100, min(int(raw), 10000))
    except ValueError:
        return DEFAULT_PREVIEW_LIMIT


def _payload_size_log_threshold_chars() -> int:
    raw = os.getenv("EXTRACTION_TRACE_EVENT_SIZE_LOG_THRESHOLD_CHARS", "").strip()
    if not raw:
        return DEFAULT_PAYLOAD_SIZE_LOG_THRESHOLD_CHARS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_PAYLOAD_SIZE_LOG_THRESHOLD_CHARS


def trace_event_dir() -> Path:
    configured = os.getenv("EXTRACTION_TRACE_EVENT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".ai_curation" / "extraction_trace_events"


def trace_event_path(trace_id: str) -> Path:
    safe_trace_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(trace_id or "unknown"))
    return trace_event_dir() / f"{safe_trace_id}.jsonl"


def _hash_user_id(user_id: str | None) -> str | None:
    if not user_id:
        return None
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:16]


def start_extraction_trace_run(
    *,
    trace_id: str,
    session_id: str | None = None,
    user_id: str | None = None,
    observation_id: str | None = None,
    source: str = "backend",
) -> ExtractionTraceRun:
    run = ExtractionTraceRun(
        trace_id=str(trace_id),
        session_id=session_id,
        user_id_hash=_hash_user_id(user_id),
        observation_id=observation_id,
        source=source,
    )
    _current_run.set(run)
    return run


def clear_extraction_trace_run() -> None:
    _current_run.set(None)


def get_current_extraction_trace_run() -> ExtractionTraceRun | None:
    return _current_run.get()


def _redact_value(value: Any, *, depth: int = 0) -> Any:
    limit = _preview_limit()
    if depth > 6:
        return "<redacted:depth_limit>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > limit:
            return {
                "preview": compact[:limit],
                "truncated": True,
                "length": len(compact),
            }
        return compact
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SECRET_KEY_PATTERN.search(key_text):
                redacted[key_text] = "<redacted>"
            else:
                redacted[key_text] = _redact_value(item, depth=depth + 1)
        return redacted
    if isinstance(value, (list, tuple)):
        items = list(value)
        visible = [_redact_value(item, depth=depth + 1) for item in items[:25]]
        if len(items) > 25:
            visible.append({"truncated": True, "omitted_count": len(items) - 25})
        return visible
    if hasattr(value, "model_dump"):
        try:
            return _redact_value(value.model_dump(mode="json"), depth=depth + 1)
        except Exception:
            pass
    return _redact_value(str(value), depth=depth + 1)


def _json_size(value: Any) -> tuple[int | None, int | None]:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
    except Exception:
        try:
            text = str(value)
        except Exception:
            return None, None
    return len(text), len(text.encode("utf-8"))


def _size_kind(value: Any) -> str:
    if hasattr(value, "model_dump"):
        return value.__class__.__name__
    if value is None:
        return "none"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, str):
        return "string"
    return value.__class__.__name__


def _payload_size(value: Any) -> dict[str, Any]:
    measured = value
    if hasattr(value, "model_dump"):
        try:
            measured = value.model_dump(mode="json")
        except Exception:
            measured = value
    json_chars, json_bytes = _json_size(measured)
    size: dict[str, Any] = {
        "kind": _size_kind(value),
        "json_chars": json_chars,
        "json_bytes": json_bytes,
        "estimated_tokens": math.ceil(json_chars / 4) if json_chars else 0,
    }
    if isinstance(value, str):
        size["string_chars"] = len(value)
        size["string_bytes"] = len(value.encode("utf-8"))
    elif isinstance(value, Mapping):
        size["top_level_keys"] = len(value)
    elif isinstance(value, (list, tuple)):
        size["item_count"] = len(value)
    return size


def _summary(value: Any) -> dict[str, Any]:
    redacted = _redact_value(value)
    size = _payload_size(value)
    return {
        "preview": redacted,
        "preview_length": len(json.dumps(redacted, sort_keys=True, default=str)),
        "size": size,
        "bounded": True,
    }


def _size_json_chars(size: Mapping[str, Any] | None) -> int:
    if not isinstance(size, Mapping):
        return 0
    value = size.get("json_chars")
    return value if isinstance(value, int) else 0


def _summary_size(summary: Mapping[str, Any]) -> Mapping[str, Any] | None:
    size = summary.get("size")
    return size if isinstance(size, Mapping) else None


def _event_payload_size_summary(
    *,
    input_summary: Mapping[str, Any],
    output_summary: Mapping[str, Any],
    validation: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    input_size = _summary_size(input_summary)
    output_size = _summary_size(output_summary)
    validation_size = _payload_size(validation)
    metadata_size = _payload_size(metadata)
    total_json_chars = (
        _size_json_chars(input_size)
        + _size_json_chars(output_size)
        + _size_json_chars(validation_size)
        + _size_json_chars(metadata_size)
    )
    return {
        "input_json_chars": _size_json_chars(input_size),
        "output_json_chars": _size_json_chars(output_size),
        "validation_json_chars": _size_json_chars(validation_size),
        "metadata_json_chars": _size_json_chars(metadata_size),
        "total_json_chars": total_json_chars,
        "estimated_tokens": math.ceil(total_json_chars / 4) if total_json_chars else 0,
    }


def _log_large_payloads(event: Mapping[str, Any]) -> None:
    threshold = _payload_size_log_threshold_chars()
    if threshold <= 0:
        return
    for direction in ("input_summary", "output_summary"):
        summary = event.get(direction)
        if not isinstance(summary, Mapping):
            continue
        size = summary.get("size")
        if not isinstance(size, Mapping):
            continue
        json_chars = size.get("json_chars")
        if not isinstance(json_chars, int) or json_chars < threshold:
            continue
        logger.warning(
            "Large extraction trace payload",
            extra={
                "trace_id": event.get("trace_id"),
                "event_type": event.get("event_type"),
                "sequence": event.get("sequence"),
                "direction": direction.replace("_summary", ""),
                "json_chars": json_chars,
                "json_bytes": size.get("json_bytes"),
                "estimated_tokens": size.get("estimated_tokens"),
                "tool_name": (event.get("metadata") or {}).get("tool_name")
                if isinstance(event.get("metadata"), Mapping)
                else None,
                "threshold_chars": threshold,
            },
        )


def _domain_pack_id_from_payload(*payloads: Any) -> str | None:
    for payload in payloads:
        candidate = _domain_pack_id_from_one(payload)
        if candidate:
            return candidate
    return None


def _domain_pack_id_from_one(payload: Any) -> str | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    if hasattr(payload, "model_dump"):
        try:
            payload = payload.model_dump(mode="json")
        except Exception:
            return None
    if isinstance(payload, Mapping):
        direct = payload.get("domain_pack_id")
        if direct:
            return str(direct)
        for key in ("details", "internal", "input_summary", "output_summary", "validation"):
            nested = payload.get(key)
            candidate = _domain_pack_id_from_one(nested)
            if candidate:
                return candidate
        for key in ("objects", "curatable_objects", "validation_findings"):
            nested_list = payload.get(key)
            if isinstance(nested_list, list):
                for item in nested_list:
                    candidate = _domain_pack_id_from_one(item)
                    if candidate:
                        return candidate
    return None


def _append_jsonl(trace_id: str, event: dict[str, Any]) -> None:
    path = trace_event_path(trace_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, default=str))
            handle.write("\n")
    except Exception:
        logger.warning(
            "Failed to write extraction trace event",
            extra={"trace_id": trace_id, "event_type": event.get("event_type")},
            exc_info=True,
        )


def _mirror_to_langfuse(event: dict[str, Any]) -> None:
    try:
        from .langfuse_client import get_langfuse

        langfuse = get_langfuse()
        if langfuse is None:
            return
        trace_id = event.get("trace_id")
        if not trace_id:
            return
        trace_context = {"trace_id": trace_id}
        observation_id = event.get("observation_id")
        if observation_id:
            trace_context["parent_span_id"] = observation_id
        langfuse.create_event(
            name="extraction_trace_event",
            # Store mirrored app events as event output so Langfuse does not
            # promote the latest event payload into the root trace input.
            output=event,
            trace_context=trace_context,
        )
    except Exception:
        logger.debug("Failed to mirror extraction trace event to Langfuse", exc_info=True)


def write_extraction_trace_event(
    *,
    event_type: str,
    trace_id: str | None = None,
    observation_id: str | None = None,
    tool_call_id: str | None = None,
    domain_pack_id: str | None = None,
    input_summary: Any = None,
    output_summary: Any = None,
    validation: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any] | None:
    run = _current_run.get()
    effective_trace_id = trace_id or (run.trace_id if run else None)
    if not effective_trace_id:
        logger.debug(
            "Dropping extraction trace event without trace context",
            extra={"event_type": event_type},
        )
        return None

    sequence = 1
    session_id = None
    user_id_hash = None
    source = "backend"
    if run is not None and run.trace_id == effective_trace_id:
        if run.sequence >= MAX_EVENTS_PER_TRACE:
            return None
        run.sequence += 1
        sequence = run.sequence
        session_id = run.session_id
        user_id_hash = run.user_id_hash
        source = run.source

    effective_observation_id = observation_id or (run.observation_id if run else None)
    effective_domain_pack_id = domain_pack_id or _domain_pack_id_from_payload(
        input_summary,
        output_summary,
        validation,
        metadata,
    )
    summarized_input = _summary(input_summary) if input_summary is not None else {}
    summarized_output = _summary(output_summary) if output_summary is not None else {}
    redacted_validation = _redact_value(dict(validation or {}))
    redacted_metadata = _redact_value(dict(metadata or {}))
    payload_size_summary = _event_payload_size_summary(
        input_summary=summarized_input,
        output_summary=summarized_output,
        validation=dict(validation or {}),
        metadata=dict(metadata or {}),
    )
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "event_id": f"evt-{uuid.uuid4().hex}",
        "sequence": sequence,
        "trace_id": effective_trace_id,
        "observation_id": effective_observation_id,
        "domain_pack_id": effective_domain_pack_id,
        "tool_call_id": tool_call_id,
        "session_id": session_id,
        "user_id_hash": user_id_hash,
        "source": source,
        "input_summary": summarized_input,
        "output_summary": summarized_output,
        "validation": redacted_validation,
        "metadata": redacted_metadata,
        "payload_size_summary": payload_size_summary,
        "timestamp": timestamp or _now_iso(),
    }
    event_json_chars, event_json_bytes = _json_size(event)
    event["event_size"] = {
        "json_chars": event_json_chars,
        "json_bytes": event_json_bytes,
        "estimated_tokens": math.ceil(event_json_chars / 4)
        if event_json_chars
        else 0,
    }
    _log_large_payloads(event)
    _append_jsonl(effective_trace_id, event)
    _mirror_to_langfuse(event)
    return event


def write_stream_event(
    event: Mapping[str, Any],
    *,
    trace_id: str | None = None,
    observation_id: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, Any] | None:
    event_type = str(event.get("type") or "runtime.event")
    raw_details = event.get("details")
    details: Mapping[str, Any] = raw_details if isinstance(raw_details, Mapping) else {}
    raw_internal = event.get("internal")
    internal: Mapping[str, Any] = raw_internal if isinstance(raw_internal, Mapping) else {}
    event_payload = details if details else {
        key: value
        for key, value in event.items()
        if key not in {"type", "timestamp", "message_id"}
    }
    mapped_type = _mapped_stream_event_type(event_type, details)
    validation = _validation_from_event(event_type, details)
    return write_extraction_trace_event(
        event_type=mapped_type,
        trace_id=trace_id,
        observation_id=observation_id,
        tool_call_id=tool_call_id or details.get("toolCallId"),
        domain_pack_id=_domain_pack_id_from_payload(details, internal),
        input_summary=details.get("toolArgs") if "toolArgs" in details else event_payload,
        output_summary=internal.get("tool_output") if "tool_output" in internal else event_payload,
        validation=validation,
        metadata={
            "stream_event_type": event_type,
            "friendly_name": details.get("friendlyName"),
            "agent": details.get("agent") or details.get("agentRole") or details.get("specialist"),
            "tool_name": details.get("toolName"),
        },
        timestamp=str(event.get("timestamp") or "") or None,
    )


def _mapped_stream_event_type(event_type: str, details: Mapping[str, Any]) -> str:
    if event_type == "TOOL_START":
        return "specialist_tool_call.started" if details.get("isSpecialistInternal") else "tool_call.started"
    if event_type == "TOOL_COMPLETE":
        return "specialist_tool_call.completed" if details.get("isSpecialistInternal") else "tool_call.completed"
    if event_type == "SPECIALIST_ERROR":
        return "validation.failure"
    if event_type == "SPECIALIST_SUMMARY":
        return "specialist.summary"
    if event_type == "evidence_summary":
        return "evidence.summary"
    if event_type == "INTERNAL_EXTRACTION_RESULT":
        return "extraction_builder.internal_result"
    if event_type == "STRUCTURED_RESULT":
        return "extraction_builder.structured_result"
    if event_type == "RUN_ERROR":
        return "run.error"
    return f"runtime.{event_type.lower()}"


def _validation_from_event(event_type: str, details: Mapping[str, Any]) -> dict[str, Any]:
    if event_type == "SPECIALIST_ERROR":
        return {
            "status": "failed",
            "errors": [
                {
                    "message": details.get("error") or details.get("message"),
                    "reason": details.get("reason"),
                    "severity": details.get("severity") or "error",
                }
            ],
        }
    if "validatorResultStatus" in details:
        return {
            "status": details.get("validatorResultStatus"),
            "statuses": details.get("validatorResultStatuses") or {},
        }
    if event_type == "TOOL_COMPLETE":
        return {"status": "ok" if details.get("success", True) else "failed"}
    return {}
