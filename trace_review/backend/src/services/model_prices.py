"""Estimate from exported Langfuse model definitions, never a shadow price list."""
from __future__ import annotations

import operator
import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping


def _time(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def matching_definition(definitions, model: str, timestamp: str):
    matches = []
    for definition in definitions:
        pattern = definition.get("matchPattern") or definition.get("match_pattern")
        start = definition.get("startDate") or definition.get("start_date") or definition.get("createdAt")
        if not pattern or (start and _time(start) > _time(timestamp)):
            continue
        if re.search(pattern, model):
            matches.append(definition)
    matches.sort(key=lambda d: (
        not d.get("isLangfuseManaged", d.get("is_langfuse_managed", True)),
        str(d.get("startDate") or d.get("start_date") or d.get("createdAt") or ""),
    ), reverse=True)
    return matches[0] if matches else None


def _condition(condition: Mapping[str, Any], usage: Mapping[str, int], event: Mapping[str, Any]):
    source = condition.get("source", "usage_details")
    operation = condition.get("operator")
    if source in {"model_parameters", "metadata"}:
        value = event.get(condition.get("key"))
        if value is None or value == "unknown":
            return None
        if operation not in {"in", "not_in"}:
            raise ValueError("Unsupported Langfuse pricing condition")
        matched = str(value) in [str(v) for v in condition.get("values", [])]
        return matched if operation == "in" else not matched
    if source != "usage_details":
        raise ValueError("Unsupported Langfuse pricing condition source")
    pattern = condition.get("usageDetailPattern") or condition.get("usage_detail_pattern")
    if not pattern:
        raise ValueError("Missing Langfuse usage condition pattern")
    flags = 0 if condition.get("caseSensitive", condition.get("case_sensitive", False)) else re.I
    total = sum(v for k, v in usage.items() if re.search(pattern, k, flags))
    operators = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt,
                 "lte": operator.le, "eq": operator.eq, "neq": operator.ne}
    if operation not in operators:
        raise ValueError("Unsupported Langfuse pricing comparison")
    return operators[operation](total, condition["value"])


def estimate(event: Mapping[str, Any], definitions) -> dict[str, Any]:
    if event["usage_status"] != "recorded":
        return {"estimate_unavailable_reason": "missing_or_inconsistent_usage"}
    definition = matching_definition(definitions, event["model"], event["timestamp"])
    if definition is None:
        return {"estimate_unavailable_reason": "no_matching_model_price"}
    usage = event["usage"]
    buckets = {
        "input": usage["uncached_input_tokens"],
        "input_cached_tokens": usage["cache_read_tokens"],
        "input_cache_creation": usage["cache_write_tokens"],
        "output": usage["output_tokens"] - usage["reasoning_tokens"],
        "output_reasoning_tokens": usage["reasoning_tokens"],
    }
    tiers = definition.get("pricingTiers") or definition.get("pricing_tiers") or []
    if not tiers:
        # Simple flat pricing is a supported Langfuse model-definition shape.
        tiers = [{"name": "flat", "isDefault": True, "priority": 0,
                  "prices": definition.get("prices") or {"input": definition.get("inputPrice"), "output": definition.get("outputPrice")}}]
    ordered = sorted(tiers, key=lambda t: (bool(t.get("isDefault", t.get("is_default"))), t.get("priority", 0)))
    possible = []
    uncertain_tier = False
    for tier in ordered:
        results = [_condition(c, buckets, event) for c in tier.get("conditions", [])]
        if False in results:
            continue
        possible.append(tier)
        if None in results:
            uncertain_tier = True
        else:
            break
    amounts = []
    reasons = []
    for tier in possible:
        prices = tier.get("prices") or {}
        if any(amount and prices.get(key) is None for key, amount in buckets.items()):
            return {"estimate_unavailable_reason": "missing_usage_type_price"}
        total = sum((Decimal(str(prices[key])) * amount for key, amount in buckets.items() if amount), Decimal(0))
        upper = total
        if usage["cache_write_status"] == "not_recorded" and buckets["input"]:
            write_rate = prices.get("input_cache_creation")
            if write_rate is not None:
                adjustment = (Decimal(str(write_rate)) - Decimal(str(prices["input"]))) * buckets["input"]
                lower = total + min(adjustment, Decimal(0))
                upper = total + max(adjustment, Decimal(0))
                total = lower
                reasons.append("cache_writes_not_recorded")
        if total < 0 or not total.is_finite() or not upper.is_finite():
            raise ValueError("Invalid model prices")
        amounts.append((total, upper))
    if not amounts:
        return {"estimate_unavailable_reason": "no_matching_price_tier"}
    if uncertain_tier:
        reasons.append("service_tier_or_metadata_not_recorded")
    lower, upper = min(a[0] for a in amounts), max(a[1] for a in amounts)
    return {
        "cost": str(lower), "estimated_cost_upper": str(upper),
        "pricing_status": "estimated_range" if lower != upper else "estimated",
        "pricing_source": "langfuse_model_definition",
        "pricing_model_id": definition.get("id"),
        "pricing_effective_date": definition.get("startDate") or definition.get("start_date") or definition.get("createdAt"),
        "pricing_tiers": [t["name"] for t in possible],
        "pricing_uncertainty": sorted(set(reasons)),
    }
