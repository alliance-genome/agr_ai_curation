"""Read-only, exclusive model-call accounting over retained observations.

Amounts are Decimal strings in exports. No prompts, outputs, user IDs or
credentials are copied into the report. Unknown amounts are never zero.
"""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .langfuse_run_reconstruction import (
    _metadata, _model_name, _observation_id, _parent_observation_id,
    _timestamp, usage_cost_summary,
)

DIMENSIONS = frozenset({
    "environment", "activity", "agent_id", "agent_name", "agent_role",
    "agent_revision", "model", "effort", "provider", "status", "paper",
    "paper_category", "document_id", "run_id", "service_tier", "usage_status",
})
TOKEN_FIELDS = (
    "input_tokens", "uncached_input_tokens", "cache_read_tokens",
    "cache_write_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
)
# ALL-1288 spans declare why a model turn has no usable usage.
DECLARED_USAGE_STATUSES = ("recorded", "inconsistent", "provider_omitted", "failed", "cancelled")
# Older spans only show that usage is absent, not why.
LEGACY_MISSING_USAGE = "missing_status_unknown"
USAGE_STATUSES = (*DECLARED_USAGE_STATUSES, LEGACY_MISSING_USAGE)


def utc_time(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Cost report timestamps must include a UTC offset")
    return result.astimezone(timezone.utc)


def _context(observation: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _metadata(observation)
    context = metadata.get("cost_context")
    return dict(context) if isinstance(context, Mapping) else dict(metadata)


def _usage_status(declared: Any, usage: Mapping[str, Any]) -> str:
    """Report the span's declared usage status against the retained usage."""
    observed = usage["usage_status"]
    if declared not in DECLARED_USAGE_STATUSES:
        return LEGACY_MISSING_USAGE if observed == "missing" else observed
    if declared == "recorded" and observed != "recorded":
        # Usage the span recorded but the observation does not hold is not zero.
        return "inconsistent"
    return declared


def _ancestry(observation: Mapping[str, Any], index: Mapping[str, Any]):
    current = observation
    visited = set()
    while current:
        key = _observation_id(current)
        if key in visited:
            break
        visited.add(key)
        yield current
        current = index.get(_parent_observation_id(current))


def cost_events(trace_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Attribute each generation to its nearest owning agent, never wrappers."""
    observations = trace_data.get("observations") or []
    index = {_observation_id(item): item for item in observations if _observation_id(item)}
    trace = trace_data.get("raw_trace") or {}
    events = []
    for observation in observations:
        if str(observation.get("type", "")).upper() != "GENERATION":
            continue
        chain = list(_ancestry(observation, index))
        contexts = [_context(item) for item in chain]
        direct = contexts[0]
        def inherited(key: str):
            for context in contexts:
                if key in context:
                    return context[key]
            return None
        owner = next((item for item in chain if str(item.get("type", "")).upper() == "AGENT"), None)
        owner_context = _context(owner) if owner else {}
        agent_id = direct.get("agent_id") or owner_context.get("agent_id") or owner_context.get("agent_key")
        agent_name = direct.get("agent_name") or owner_context.get("agent_name") or (owner or {}).get("name")
        if not agent_name:
            agent_name = inherited("agent_name")
        # A historical display name is useful but is not a verified registry ID.
        agent_basis = "direct" if direct.get("agent_id") else "ancestor" if owner else "unknown"
        paper_context = next((c for c in contexts if "paper_category" in c or "paper" in c), {})
        paper = paper_context.get("paper")
        paper_key = None
        if isinstance(paper, Mapping) and paper.get("namespace") and paper.get("id"):
            paper_key = json.dumps([paper["namespace"], paper["id"]], separators=(",", ":"))
        category = paper_context.get("paper_category") or ("paper" if paper_key else "unknown")
        document_id = inherited("document_id")
        if not paper_key and category == "paper":
            category = "artifact_only" if document_id else "unknown"
        if category == "unknown" and document_id:
            category = "artifact_only"
        if category != "paper":
            paper_key = None
        parameters = observation.get("modelParameters") or observation.get("model_parameters") or {}
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters)
            except ValueError:
                parameters = {}
        if not isinstance(parameters, Mapping):
            parameters = {}
        reasoning = parameters.get("reasoning") or {}
        if isinstance(reasoning, str):
            try:
                reasoning = json.loads(reasoning)
            except ValueError:
                pass
        usage = usage_cost_summary(observation)
        trace_id = observation.get("traceId") or observation.get("trace_id") or trace.get("id") or trace_data.get("trace_id")
        event = {
            "trace_id": trace_id,
            "span_id": _observation_id(observation),
            "project_id": observation.get("projectId") or observation.get("project_id") or trace.get("projectId"),
            "timestamp": _timestamp(observation),
            "environment": direct.get("environment") or observation.get("environment") or inherited("environment") or "unknown",
            "deployment": inherited("deployment"),
            "activity": inherited("activity") or "unknown",
            "agent_id": agent_id,
            "agent_name": agent_name or "unknown",
            "agent_role": direct.get("agent_role") or owner_context.get("agent_role") or "unknown",
            "agent_revision": direct.get("agent_revision") or owner_context.get("agent_revision"),
            "agent_attribution": agent_basis,
            "paper": paper_key,
            "paper_category": category,
            "paper_attribution": "direct" if paper_context is direct else "ancestor" if paper_context else "unknown",
            "related_papers": paper_context.get("related_papers") or [],
            "document_id": document_id,
            "artifact_revision": inherited("artifact_revision"),
            "run_id": inherited("run_id"),
            "workflow_id": inherited("workflow_id"),
            "node_id": inherited("node_id"),
            "job_id": inherited("job_id"),
            "attempt_id": direct.get("attempt_id"),
            "provider_response_id": direct.get("provider_response_id"),
            "model_request_id": direct.get("model_request_id"),
            "provider": direct.get("provider") or inherited("provider") or "unknown",
            "model": _model_name(observation) or "unknown",
            "effort": direct.get("effort") or parameters.get("reasoning_effort") or (reasoning.get("effort") if isinstance(reasoning, Mapping) else reasoning) or "unknown",
            "service_tier": direct.get("service_tier") or parameters.get("service_tier") or "unknown",
            "status": direct.get("attempt_outcome") or ("error" if observation.get("level") == "ERROR" else "unknown"),
            "usage": usage,
            "usage_status": _usage_status(direct.get("usage_status"), usage),
            "usage_status_declared": direct.get("usage_status"),
            "cost": usage["total_cost_decimal"],
            "pricing_status": usage["pricing_status"],
            "pricing_source": usage["cost_source"],
            "currency": "USD",
            "owning_workflow_spans": [_observation_id(item) for item in chain[1:] if str(item.get("type", "")).upper() in {"AGENT", "CHAIN"}],
        }
        events.append(event)
    return events


def _deduplicate(events: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    retained: dict[tuple, dict[str, Any]] = {}
    anonymous = []
    duplicates = conflicts = 0
    for event in events:
        scope = (event["environment"], event.get("project_id"), event["provider"])
        if event.get("provider_response_id"):
            key = (*scope, "response", event["provider_response_id"])
        elif event.get("model_request_id"):
            key = (*scope, "request", event["model_request_id"])
        elif event.get("attempt_id"):
            key = (*scope, "attempt", event["attempt_id"])
        elif event.get("span_id") and event.get("trace_id"):
            key = (*scope, "span", event["trace_id"], event["span_id"])
        else:
            anonymous.append(event)
            continue
        previous = retained.get(key)
        if previous is None:
            retained[key] = event
        elif all(previous.get(field) == event.get(field) for field in (
            "cost", "usage", "agent_id", "paper", "run_id", "model",
        )):
            duplicates += 1
        else:
            # A proven duplicate cannot become two charges even when payloads
            # conflict. Exclude its ambiguous amount and retain both references.
            conflicts += 1
            previous["duplicate_conflict"] = True
            previous["cost"] = None
            previous["pricing_status"] = "duplicate_conflict"
            previous.setdefault("conflicting_references", []).append(
                {"trace_id": event["trace_id"], "span_id": event["span_id"]}
            )
    return [*retained.values(), *anonymous], duplicates, conflicts


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    measured = sum((Decimal(e["cost"]) for e in events if e["cost"] is not None and not e["pricing_status"].startswith("estimated")), Decimal(0))
    estimated = sum((Decimal(e["cost"]) for e in events if e["cost"] is not None and e["pricing_status"].startswith("estimated")), Decimal(0))
    estimated_upper = sum((Decimal(e.get("estimated_cost_upper", e["cost"])) for e in events if e["cost"] is not None and e["pricing_status"].startswith("estimated")), Decimal(0))
    unpriced = sum(e["cost"] is None for e in events)
    conflicts = sum(bool(e.get("duplicate_conflict")) for e in events)
    uncertain = sum(e["pricing_status"] == "estimated_range" for e in events)
    total = measured + estimated
    return {
        "calls": len(events),
        "known_runs": len({e["run_id"] for e in events if e["run_id"]}),
        "known_papers": len({e["paper"] for e in events if e["paper"]}),
        "missing_run_calls": sum(not e["run_id"] for e in events),
        "unpriced_calls": unpriced,
        "missing_usage_calls": sum(e["usage"]["usage_status"] == "missing" for e in events),
        **{status + "_usage_calls": sum(e["usage_status"] == status for e in events) for status in USAGE_STATUSES},
        "usage_complete": all(e["usage_status"] == "recorded" for e in events),
        "unknown_agent_calls": sum(not e["agent_id"] for e in events),
        "unknown_paper_calls": sum(e["paper_category"] == "unknown" for e in events),
        "measured_cost": str(measured),
        "estimated_cost": str(estimated),
        "estimated_cost_upper": str(estimated_upper),
        "uncertain_price_calls": uncertain,
        "priced_subtotal": str(total),
        "total_cost": None if unpriced or conflicts or uncertain else str(total),
        "cost_complete": not (unpriced or conflicts or uncertain),
        "currency": "USD",
        **{key: sum(e["usage"][key] for e in events) for key in TOKEN_FIELDS},
        "trace_references": sorted({str(e["trace_id"]) + "/" + str(e["span_id"]) for e in events}),
    }


def build_report(
    traces: Iterable[Mapping[str, Any]], *, start: str, end: str,
    group_by: Iterable[str] = ("agent_id", "agent_name"),
    filters: Mapping[str, str] | None = None, source_complete: bool = True,
    organization_cost: str | None = None,
    model_definitions: list[dict[str, Any]] | None = None,
    reconstruct_all: bool = False,
    assumed_service_tier: str | None = None,
) -> dict[str, Any]:
    start_time, end_time = utc_time(start), utc_time(end)
    if end_time <= start_time:
        raise ValueError("end must be after start")
    dimensions = tuple(group_by)
    filters = dict(filters or {})
    if not set(dimensions).issubset(DIMENSIONS) or not set(filters).issubset(DIMENSIONS):
        raise ValueError("Unsupported cost report dimension")
    # Names alone must never collapse distinct stable agent identities.
    if "agent_name" in dimensions and "agent_id" not in dimensions:
        dimensions += ("agent_id",)
    if "paper" in dimensions and "paper_category" not in dimensions:
        dimensions += ("paper_category",)
    candidates = []
    missing_timestamps = 0
    for trace in traces:
        for event in cost_events(trace):
            try:
                timestamp = utc_time(event["timestamp"])
            except (ValueError, TypeError):
                missing_timestamps += 1
                continue
            if start_time <= timestamp < end_time:
                if reconstruct_all:
                    event["recorded_cost"] = event["cost"]
                    event["cost"] = None
                    event["pricing_status"] = "unpriced"
                if event["cost"] is None and model_definitions is not None:
                    from .model_prices import estimate
                    estimate_input = event
                    if assumed_service_tier and event["service_tier"] == "unknown":
                        estimate_input = {**event, "service_tier": assumed_service_tier}
                        event["assumed_service_tier"] = assumed_service_tier
                    event.update(estimate(estimate_input, model_definitions))
                candidates.append(event)
    candidates, duplicates, conflicts = _deduplicate(candidates)
    events = [e for e in candidates if all(e.get(k) == v for k, v in filters.items())]
    groups = defaultdict(list)
    for event in events:
        groups[tuple(event.get(dimension) for dimension in dimensions)].append(event)
    rows = [{**dict(zip(dimensions, key)), **summarize(items)} for key, items in groups.items()]
    rows.sort(key=lambda row: Decimal(row["priced_subtotal"]), reverse=True)
    totals = summarize(events)
    complete = source_complete and not missing_timestamps and not conflicts
    if not complete:
        totals["total_cost"] = None
        totals["cost_complete"] = False
        for row in rows:
            row["total_cost"] = None
            row["cost_complete"] = False
    return {
        "boundary": "recorded AI Curation model cost; excludes PDFX, compute, storage, embeddings and reranking",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "start": start_time.isoformat(), "end": end_time.isoformat(),
        "window": "UTC [start,end), generation start time; run totals include in-window calls only",
        "retention": "Only retained source observations are covered; never a lifetime/all-service bill",
        "reconstruct_all": reconstruct_all, "assumed_service_tier": assumed_service_tier,
        "source_complete": complete, "missing_timestamp_observations": missing_timestamps,
        "duplicate_observations": duplicates, "duplicate_conflicts": conflicts,
        "group_by": dimensions, "filters": filters, "totals": totals,
        "rows": rows, "events": events,
        "organization_cost": organization_cost,
        "unexplained_residual": str(Decimal(organization_cost) - Decimal(totals["priced_subtotal"])) if organization_cost is not None else None,
        "unexplained_residual_lower": str(Decimal(organization_cost) - Decimal(totals["measured_cost"]) - Decimal(totals["estimated_cost_upper"])) if organization_cost is not None else None,
    }


def report_csv(report: Mapping[str, Any]) -> str:
    """Export identical group values, coverage, filters and interval to CSV."""
    rows = report["rows"]
    output = io.StringIO()
    columns = [*report["group_by"], *summarize([]), "start", "end", "as_of", "filters", "source_complete", "boundary"]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        values = {**row, **{key: report[key] for key in ("start", "end", "as_of", "filters", "source_complete", "boundary")}}
        writer.writerow({key: json.dumps(value, separators=(",", ":")) if isinstance(value, (list, dict)) else value for key, value in values.items()})
    return output.getvalue()
