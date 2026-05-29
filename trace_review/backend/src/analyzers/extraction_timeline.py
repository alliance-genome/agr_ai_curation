"""Extraction timeline analysis for durable backend trace events."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .tool_calls import ToolCallAnalyzer

ANALYZER_SCHEMA_VERSION = "extraction_timeline_analyzer.v1"
EVENT_SCHEMA_VERSION = "extraction_trace_event.v1"
SUMMARY_TEXT_LIMIT = 500


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
    def _observation_tool_events(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
                    "event_id": f"obs-tool-{index}",
                    "sequence": index,
                    "trace_id": "",
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
    def _observation_durable_events(observations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for observation in observations:
            event = _langfuse_extraction_event_from_observation(observation)
            if event is not None:
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
        feedback_trace_data: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        sibling_ids = [sibling_id for sibling_id in (sibling_trace_ids or []) if sibling_id != trace_id]
        durable_events = ExtractionTimelineAnalyzer.load_durable_events(trace_id)
        for sibling_id in sibling_ids:
            durable_events.extend(ExtractionTimelineAnalyzer.load_durable_events(sibling_id))
        durable_event_ids = {event.get("event_id") for event in durable_events}
        observation_durable_events = [
            event
            for event in ExtractionTimelineAnalyzer._observation_durable_events(observations)
            if event.get("event_id") not in durable_event_ids
        ]
        feedback_events = [
            event
            for event in _feedback_trace_events(
                trace_id=trace_id,
                feedback_trace_data=feedback_trace_data,
                sibling_trace_ids=sibling_ids,
            )
            if event.get("event_id") not in durable_event_ids
        ]
        observation_events = ExtractionTimelineAnalyzer._observation_tool_events(observations)
        for event in observation_events:
            event["trace_id"] = trace_id

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
            },
            "reasoning_summary": timeline.get("reasoning_summary", {}),
            "validation_failures": validation_failures,
            "timeline": timeline.get("timeline", []),
        }
