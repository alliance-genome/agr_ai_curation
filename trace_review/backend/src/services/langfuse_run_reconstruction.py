"""TraceReview transforms for complete Langfuse trace inspection."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ..config import get_trace_review_payload_preview_max_chars

PAYLOAD_PREVIEW_CHARS = get_trace_review_payload_preview_max_chars()


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def serialize_payload(value: Any) -> str:
    """Serialize a Langfuse payload deterministically for sizing and hashing."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=_json_default)


def _payload_size(value: Any) -> Dict[str, Any]:
    serialized = serialize_payload(value)
    char_count = len(serialized)
    return {
        "char_count": char_count,
        "byte_count": len(serialized.encode("utf-8")),
        "rough_token_estimate": (char_count + 3) // 4,
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "preview": serialized[:PAYLOAD_PREVIEW_CHARS],
        "truncated_preview": char_count > PAYLOAD_PREVIEW_CHARS,
    }


def _first_present(mapping: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _observation_id(observation: Mapping[str, Any]) -> Optional[str]:
    value = _first_present(observation, ("id", "observationId", "observation_id"))
    return str(value) if value is not None else None


def _parent_observation_id(observation: Mapping[str, Any]) -> Optional[str]:
    value = _first_present(
        observation,
        ("parentObservationId", "parent_observation_id", "parentId", "parent_id"),
    )
    return str(value) if value is not None else None


def _trace_id(trace_data: Mapping[str, Any]) -> str:
    raw_trace = trace_data.get("raw_trace") or {}
    return str(raw_trace.get("id") or trace_data.get("trace_id") or "")


def _metadata(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    value = mapping.get("metadata") or {}
    return value if isinstance(value, Mapping) else {}


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _metadata_inventory(value: Any) -> Dict[str, Any]:
    """Describe aggregate metadata without replaying payload-like values."""
    metadata = _mapping_or_empty(value)
    serialized = serialize_payload(metadata)
    return {
        "keys": sorted(str(key) for key in metadata),
        "json_chars": len(serialized),
        "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    }


def _agent_name(observation: Mapping[str, Any], raw_trace: Mapping[str, Any]) -> Optional[str]:
    metadata = _metadata(observation)
    trace_metadata = _metadata(raw_trace)
    value = _first_present(
        metadata,
        (
            "agent_name",
            "agent",
            "current_agent",
            "handoff_agent",
            "tool_agent_name",
        ),
    )
    if value is None:
        value = _first_present(trace_metadata, ("agent_name", "agent", "current_agent"))
    return str(value) if value is not None else None


def _model_name(observation: Mapping[str, Any]) -> Optional[str]:
    value = _first_present(
        observation,
        (
            "providedModelName",
            "provided_model_name",
            "model",
            "modelName",
            "model_name",
            "internal_model_id",
            "model_id",
        ),
    )
    return str(value) if value is not None else None


def _observation_kind(observation: Mapping[str, Any]) -> str:
    obs_type = str(_first_present(observation, ("type", "observationType")) or "").lower()
    name = str(observation.get("name") or "").lower()
    metadata = _metadata(observation)

    if obs_type == "generation" or _model_name(observation):
        return "model"
    if "tool" in name or metadata.get("tool_name") or metadata.get("function_name"):
        return "tool"
    if "handoff" in name:
        return "handoff"
    if "guardrail" in name or "validation" in name or "validator" in name:
        return "validation"
    if "agent" in name or _agent_name(observation, {}):
        return "agent"
    if obs_type:
        return obs_type
    return "observation"


def _timestamp(observation: Mapping[str, Any]) -> Optional[str]:
    value = _first_present(
        observation,
        ("startTime", "start_time", "timestamp", "createdAt", "created_at"),
    )
    return str(value) if value is not None else None


def _end_timestamp(observation: Mapping[str, Any]) -> Optional[str]:
    value = _first_present(observation, ("endTime", "end_time", "updatedAt", "updated_at"))
    return str(value) if value is not None else None


def _duration_ms(observation: Mapping[str, Any]) -> Optional[float]:
    value = _first_present(
        observation,
        ("latency", "duration", "durationMs", "duration_ms"),
    )
    if isinstance(value, (int, float)):
        if "latency" in observation or "duration" in observation:
            return float(value) * 1000
        return float(value)
    return None


def _numeric(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _dict_value(mapping: Mapping[str, Any], keys: Iterable[str]) -> float:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _normalized_usage_key(value: Any) -> str:
    key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value))
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _flatten_numeric_details(
    mapping: Mapping[str, Any],
    *,
    prefix: str = "",
) -> Dict[str, float]:
    flattened: Dict[str, float] = {}
    for raw_key, value in mapping.items():
        key = _normalized_usage_key(raw_key)
        full_key = f"{prefix}_{key}" if prefix else key
        if isinstance(value, Mapping):
            flattened.update(_flatten_numeric_details(value, prefix=full_key))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            flattened[full_key] = float(value)
    return flattened


def _usage_bucket(
    details: Mapping[str, float],
    aliases: Iterable[str],
    *,
    allow_suffix: bool = True,
) -> float:
    normalized_aliases = tuple(_normalized_usage_key(alias) for alias in aliases)
    for alias in normalized_aliases:
        if alias in details:
            return details[alias]
    if allow_suffix:
        for key, value in details.items():
            if any(key.endswith(f"_{alias}") for alias in normalized_aliases):
                return value
    return 0.0


def usage_cost_summary(observation: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize mutually exclusive token buckets without summing detail subsets."""
    usage = _mapping_or_empty(observation.get("usage"))
    usage_details = _mapping_or_empty(
        observation.get("usageDetails") or observation.get("usage_details")
    )
    cost_details = _mapping_or_empty(
        observation.get("costDetails") or observation.get("cost_details")
    )
    usage_flat = _flatten_numeric_details(usage)
    detail_flat = _flatten_numeric_details(usage_details)
    combined = {**usage_flat, **detail_flat}

    input_tokens = _usage_bucket(
        combined,
        ("input", "prompt", "input_tokens", "prompt_tokens", "input_token_count"),
        allow_suffix=False,
    )
    output_tokens = _usage_bucket(
        combined,
        ("output", "completion", "output_tokens", "completion_tokens", "output_token_count"),
        allow_suffix=False,
    )
    cache_read_tokens = _usage_bucket(
        combined,
        (
            "cache_read",
            "cache_read_tokens",
            "cache_read_input_tokens",
            "input_cache_read",
            "input_cached_tokens",
            "cached_input_tokens",
            "input_tokens_cache_read",
            "input_token_details_cached_tokens",
            "cached_tokens",
        ),
    )
    cache_write_tokens = _usage_bucket(
        combined,
        (
            "cache_write",
            "cache_write_tokens",
            "cache_write_input_tokens",
            "input_cache_write",
            "input_cache_creation",
            "input_cache_creation_tokens",
            "cache_creation_input_tokens",
            "input_tokens_cache_write",
        ),
    )
    reasoning_tokens = _usage_bucket(
        combined,
        (
            "reasoning",
            "reasoning_tokens",
            "output_reasoning",
            "output_reasoning_tokens",
            "output_tokens_reasoning",
        ),
    )

    if not input_tokens and (cache_read_tokens or cache_write_tokens):
        input_tokens = cache_read_tokens + cache_write_tokens
    uncached_input_tokens = max(
        input_tokens - cache_read_tokens - cache_write_tokens,
        0,
    )
    total_tokens = _usage_bucket(
        combined,
        ("total", "total_tokens", "total_token_count"),
        allow_suffix=False,
    )
    if not total_tokens:
        total_tokens = input_tokens + output_tokens

    total_cost = _numeric(
        _first_present(
            observation,
            (
                "calculatedTotalCost",
                "calculated_total_cost",
                "totalCost",
                "total_cost",
            ),
        )
    )
    if not total_cost:
        total_cost = _usage_bucket(
            _flatten_numeric_details(cost_details),
            ("total", "total_cost"),
            allow_suffix=False,
        )

    has_cost_details = bool(cost_details) or any(
        key in observation
        for key in (
            "calculatedTotalCost",
            "calculated_total_cost",
            "totalCost",
            "total_cost",
        )
    )

    return {
        "input_tokens": int(input_tokens),
        "uncached_input_tokens": int(uncached_input_tokens),
        "output_tokens": int(output_tokens),
        "cached_tokens": int(cache_read_tokens),
        "cache_read_tokens": int(cache_read_tokens),
        "cache_write_tokens": int(cache_write_tokens),
        "reasoning_tokens": int(reasoning_tokens),
        "total_tokens": int(total_tokens),
        "total_cost": total_cost,
        "cost_source": "langfuse_calculated" if has_cost_details else "unavailable",
        "estimated_total_cost": None,
        "usage": dict(usage),
        "usage_details": dict(usage_details),
        "cost_details": dict(cost_details),
    }


def _payload_item(
    *,
    trace_id: str,
    scope: str,
    source_id: str,
    field: str,
    value: Any,
    source: Mapping[str, Any],
    include_value: bool,
) -> Dict[str, Any]:
    source_name = str(source.get("name") or source.get("trace_name") or scope)
    payload_id = f"{scope}:{source_id}:{field}"
    item = {
        "payload_id": payload_id,
        "trace_id": trace_id,
        "scope": scope,
        "source_id": source_id,
        "observation_id": source_id if scope == "observation" else None,
        "field": field,
        "name": source_name,
        "kind": _observation_kind(source) if scope == "observation" else "trace",
        "observation_type": source.get("type") or source.get("observationType"),
        "parent_observation_id": _parent_observation_id(source) if scope == "observation" else None,
        "start_time": _timestamp(source) if scope == "observation" else source.get("timestamp"),
        **_payload_size(value),
    }
    if include_value:
        item["value"] = value
        item["serialized"] = serialize_payload(value)
    return item


def build_payload_inventory(
    trace_data: Mapping[str, Any],
    *,
    include_values: bool = False,
) -> List[Dict[str, Any]]:
    """Return all trace/observation payloads with sizes."""
    raw_trace = trace_data.get("raw_trace") or {}
    trace_id = _trace_id(trace_data)
    payloads: List[Dict[str, Any]] = []

    for field in ("input", "output"):
        if raw_trace.get(field) is not None:
            payloads.append(
                _payload_item(
                    trace_id=trace_id,
                    scope="trace",
                    source_id=trace_id,
                    field=field,
                    value=raw_trace.get(field),
                    source=raw_trace,
                    include_value=include_values,
                )
            )

    for observation in trace_data.get("observations") or []:
        obs_id = _observation_id(observation)
        if not obs_id:
            continue
        for field in ("input", "output"):
            if observation.get(field) is not None:
                payloads.append(
                    _payload_item(
                        trace_id=trace_id,
                        scope="observation",
                        source_id=obs_id,
                        field=field,
                        value=observation.get(field),
                        source=observation,
                        include_value=include_values,
                    )
                )
        metadata = observation.get("metadata")
        if isinstance(metadata, Mapping):
            for metadata_key in ("agent_config", "event_payload"):
                if metadata.get(metadata_key) is not None:
                    payloads.append(
                        _payload_item(
                            trace_id=trace_id,
                            scope="observation",
                            source_id=obs_id,
                            field=f"metadata.{metadata_key}",
                            value=metadata.get(metadata_key),
                            source=observation,
                            include_value=include_values,
                        )
                    )

    return payloads


def _payload_refs_for_source(
    payloads: List[Dict[str, Any]],
    *,
    scope: str,
    source_id: str,
) -> List[Dict[str, Any]]:
    refs = []
    for item in payloads:
        if item["scope"] == scope and item["source_id"] == source_id:
            refs.append({key: value for key, value in item.items() if key not in {"value", "serialized"}})
    return refs


def build_trace_tree(trace_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a parent/child observation tree rooted at the Langfuse trace."""
    raw_trace = trace_data.get("raw_trace") or {}
    trace_id = _trace_id(trace_data)
    payloads = build_payload_inventory(trace_data)
    root = {
        "id": trace_id,
        "type": "trace",
        "name": raw_trace.get("name"),
        "timestamp": raw_trace.get("timestamp"),
        "session_id": raw_trace.get("sessionId") or raw_trace.get("session_id"),
        "user_id": raw_trace.get("userId") or raw_trace.get("user_id"),
        "metadata_inventory": _metadata_inventory(raw_trace.get("metadata")),
        "payloads": _payload_refs_for_source(payloads, scope="trace", source_id=trace_id),
        "children": [],
    }

    nodes: Dict[str, Dict[str, Any]] = {}
    parent_lookup: Dict[str, Optional[str]] = {}
    for observation in trace_data.get("observations") or []:
        obs_id = _observation_id(observation)
        if not obs_id:
            continue
        node = {
            "id": obs_id,
            "type": observation.get("type") or observation.get("observationType"),
            "kind": _observation_kind(observation),
            "name": observation.get("name"),
            "start_time": _timestamp(observation),
            "end_time": _end_timestamp(observation),
            "duration_ms": _duration_ms(observation),
            "parent_observation_id": _parent_observation_id(observation),
            "agent_name": _agent_name(observation, raw_trace),
            "model": _model_name(observation),
            "level": observation.get("level"),
            "status_message": observation.get("statusMessage") or observation.get("status_message"),
            "metadata_inventory": _metadata_inventory(observation.get("metadata")),
            "usage_cost": usage_cost_summary(observation),
            "payloads": _payload_refs_for_source(payloads, scope="observation", source_id=obs_id),
            "children": [],
        }
        nodes[obs_id] = node
        parent_lookup[obs_id] = _parent_observation_id(observation)

    for obs_id, node in nodes.items():
        parent_id = parent_lookup.get(obs_id)
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            root["children"].append(node)

    def sort_children(node: Dict[str, Any]) -> None:
        node["children"].sort(key=lambda child: (child.get("start_time") or "", child.get("id") or ""))
        for child in node["children"]:
            sort_children(child)

    sort_children(root)
    return root


def build_ordered_reconstruction(
    trace_data: Mapping[str, Any],
    *,
    include_payload_values: bool = False,
) -> Dict[str, Any]:
    """Return chronological trace/observation events with payload references."""
    raw_trace = trace_data.get("raw_trace") or {}
    trace_id = _trace_id(trace_data)
    payloads = build_payload_inventory(trace_data, include_values=include_payload_values)
    events: List[Dict[str, Any]] = []

    if raw_trace.get("input") is not None:
        events.append({
            "event_id": f"{trace_id}:trace:input",
            "kind": "trace_input",
            "trace_id": trace_id,
            "name": raw_trace.get("name"),
            "timestamp": raw_trace.get("timestamp"),
            "payloads": _payload_refs_for_source(payloads, scope="trace", source_id=trace_id),
        })

    sorted_observations = sorted(
        trace_data.get("observations") or [],
        key=lambda observation: (_timestamp(observation) or "", _observation_id(observation) or ""),
    )
    for index, observation in enumerate(sorted_observations):
        obs_id = _observation_id(observation) or f"observation-{index}"
        event = {
            "event_id": obs_id,
            "kind": _observation_kind(observation),
            "trace_id": trace_id,
            "observation_id": obs_id,
            "parent_observation_id": _parent_observation_id(observation),
            "name": observation.get("name"),
            "observation_type": observation.get("type") or observation.get("observationType"),
            "start_time": _timestamp(observation),
            "end_time": _end_timestamp(observation),
            "duration_ms": _duration_ms(observation),
            "agent_name": _agent_name(observation, raw_trace),
            "model": _model_name(observation),
            "level": observation.get("level"),
            "status_message": observation.get("statusMessage") or observation.get("status_message"),
            "metadata_inventory": _metadata_inventory(observation.get("metadata")),
            "usage_cost": usage_cost_summary(observation),
            "payloads": _payload_refs_for_source(payloads, scope="observation", source_id=obs_id),
        }
        if include_payload_values:
            event["input"] = observation.get("input")
            event["output"] = observation.get("output")
        events.append(event)

    if raw_trace.get("output") is not None:
        events.append({
            "event_id": f"{trace_id}:trace:output",
            "kind": "trace_output",
            "trace_id": trace_id,
            "name": raw_trace.get("name"),
            "timestamp": raw_trace.get("timestamp"),
            "payloads": _payload_refs_for_source(payloads, scope="trace", source_id=trace_id),
        })

    return {
        "trace_id": trace_id,
        "trace": {
            "id": trace_id,
            "name": raw_trace.get("name"),
            "timestamp": raw_trace.get("timestamp"),
            "session_id": raw_trace.get("sessionId") or raw_trace.get("session_id"),
            "user_id": raw_trace.get("userId") or raw_trace.get("user_id"),
            "metadata_inventory": _metadata_inventory(raw_trace.get("metadata")),
        },
        "event_count": len(events),
        "events": events,
    }


def _add_totals(target: Dict[str, Any], usage_cost: Mapping[str, Any]) -> None:
    target["input_tokens"] += int(usage_cost.get("input_tokens") or 0)
    target["uncached_input_tokens"] += int(usage_cost.get("uncached_input_tokens") or 0)
    target["output_tokens"] += int(usage_cost.get("output_tokens") or 0)
    target["cached_tokens"] += int(usage_cost.get("cached_tokens") or 0)
    target["cache_read_tokens"] += int(usage_cost.get("cache_read_tokens") or 0)
    target["cache_write_tokens"] += int(usage_cost.get("cache_write_tokens") or 0)
    target["reasoning_tokens"] += int(usage_cost.get("reasoning_tokens") or 0)
    target["total_tokens"] += int(usage_cost.get("total_tokens") or 0)
    target["total_cost"] += float(usage_cost.get("total_cost") or 0)


def _empty_totals() -> Dict[str, Any]:
    return {
        "input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "total_cost": 0.0,
        "observation_count": 0,
        "provider_call_count": 0,
    }


def build_cost_summary(trace_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Aggregate token/cost accounting by trace, agent, model, and kind."""
    raw_trace = trace_data.get("raw_trace") or {}
    trace_id = _trace_id(trace_data)
    trace_totals = _empty_totals()
    by_agent: Dict[str, Dict[str, Any]] = defaultdict(_empty_totals)
    by_model: Dict[str, Dict[str, Any]] = defaultdict(_empty_totals)
    by_kind: Dict[str, Dict[str, Any]] = defaultdict(_empty_totals)
    observations: List[Dict[str, Any]] = []

    for observation in trace_data.get("observations") or []:
        obs_id = _observation_id(observation)
        usage_cost = usage_cost_summary(observation)
        kind = _observation_kind(observation)
        agent_name = _agent_name(observation, raw_trace) or "unknown"
        model_name = _model_name(observation) or "unknown"

        for bucket in (trace_totals, by_agent[agent_name], by_kind[kind]):
            _add_totals(bucket, usage_cost)
            bucket["observation_count"] += 1

        if model_name != "unknown" or kind == "model":
            _add_totals(by_model[model_name], usage_cost)
            by_model[model_name]["observation_count"] += 1

        if kind == "model" and (
            usage_cost["total_tokens"] > 0 or usage_cost["total_cost"] > 0
        ):
            for bucket in (trace_totals, by_agent[agent_name], by_kind[kind]):
                bucket["provider_call_count"] += 1
            by_model[model_name]["provider_call_count"] += 1

        observations.append({
            "observation_id": obs_id,
            "name": observation.get("name"),
            "kind": kind,
            "agent_name": agent_name,
            "model": model_name,
            "start_time": _timestamp(observation),
            "usage_cost": usage_cost,
        })

    trace_usage = raw_trace.get("usage") if isinstance(raw_trace.get("usage"), Mapping) else {}
    if not trace_totals["total_tokens"] and trace_usage:
        trace_totals["total_tokens"] = int(_dict_value(trace_usage, ("total", "totalTokens", "total_tokens")))
    trace_cost = _numeric(_first_present(raw_trace, ("calculatedTotalCost", "totalCost", "total_cost")))
    if not trace_totals["total_cost"] and trace_cost:
        trace_totals["total_cost"] = trace_cost

    return {
        "trace_id": trace_id,
        "totals": trace_totals,
        "by_agent": dict(by_agent),
        "by_model": dict(by_model),
        "by_kind": dict(by_kind),
        "observations": observations,
    }


def build_duplicate_report(trace_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Group repeated payload fingerprints across all trace/observation IO."""
    payloads = build_payload_inventory(trace_data)
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for payload in payloads:
        if payload["char_count"] > 0:
            groups[payload["sha256"]].append(payload)

    duplicates = []
    for fingerprint, items in groups.items():
        if len(items) < 2:
            continue
        first = items[0]
        duplicates.append({
            "sha256": fingerprint,
            "count": len(items),
            "char_count": first["char_count"],
            "byte_count": first["byte_count"],
            "rough_token_estimate": first["rough_token_estimate"],
            "preview": first["preview"],
            "payloads": [
                {key: value for key, value in item.items() if key not in {"value", "serialized"}}
                for item in items
            ],
        })

    duplicates.sort(key=lambda item: (item["byte_count"] * item["count"], item["count"]), reverse=True)
    return {
        "trace_id": _trace_id(trace_data),
        "duplicate_group_count": len(duplicates),
        "duplicated_payload_count": sum(item["count"] for item in duplicates),
        "duplicates": duplicates,
    }


def find_payload(
    trace_data: Mapping[str, Any],
    *,
    payload_id: Optional[str] = None,
    scope: Optional[str] = None,
    observation_id: Optional[str] = None,
    field: Optional[str] = None,
    start: int = 0,
    max_chars: int = 0,
) -> Optional[Dict[str, Any]]:
    """Find and optionally chunk one exact Langfuse payload."""
    payloads = build_payload_inventory(trace_data, include_values=True)
    selected: Optional[Dict[str, Any]] = None

    if payload_id:
        selected = next((item for item in payloads if item["payload_id"] == payload_id), None)
    else:
        wanted_scope = scope or ("observation" if observation_id else "trace")
        wanted_source_id = observation_id or _trace_id(trace_data)
        selected = next(
            (
                item
                for item in payloads
                if item["scope"] == wanted_scope
                and item["source_id"] == wanted_source_id
                and item["field"] == field
            ),
            None,
        )

    if selected is None:
        return None

    serialized = selected.get("serialized") or serialize_payload(selected.get("value"))
    safe_start = min(max(start, 0), len(serialized))
    if max_chars and max_chars > 0:
        safe_end = min(safe_start + max_chars, len(serialized))
        chunk = serialized[safe_start:safe_end]
        include_value = False
    else:
        safe_end = len(serialized)
        chunk = serialized
        include_value = True

    response = {
        **{key: value for key, value in selected.items() if key not in {"value", "serialized"}},
        "start": safe_start,
        "end": safe_end,
        "returned_char_count": len(chunk),
        "total_char_count": len(serialized),
        "truncated": safe_end < len(serialized),
        "next_start": safe_end if safe_end < len(serialized) else None,
        "serialized": chunk,
    }
    if include_value:
        response["value"] = selected.get("value")
    return response


def paginate_payloads(
    payloads: List[Dict[str, Any]],
    *,
    limit: int,
    offset: int,
    sort: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Sort and page payload summaries."""
    if sort == "chronological":
        sorted_payloads = sorted(
            payloads,
            key=lambda item: (item.get("start_time") or "", item.get("payload_id") or ""),
        )
    else:
        sorted_payloads = sorted(payloads, key=lambda item: item["byte_count"], reverse=True)

    safe_offset = max(offset, 0)
    safe_limit = max(min(limit, 1000), 1)
    page = sorted_payloads[safe_offset:safe_offset + safe_limit]
    return page, {
        "limit": safe_limit,
        "offset": safe_offset,
        "total_items": len(sorted_payloads),
        "has_next": safe_offset + safe_limit < len(sorted_payloads),
        "next_offset": safe_offset + safe_limit if safe_offset + safe_limit < len(sorted_payloads) else None,
    }
