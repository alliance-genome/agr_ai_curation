"""One estimator for application, benchmark and TraceReview accounting.

This package contains no operational prices, network access or persistence.
Callers supply a pinned, reviewed catalog and exclusive usage buckets.
"""
from datetime import datetime
from decimal import Decimal, localcontext
import operator
import re

ALGORITHM_REVISION = "langfuse-exclusive-v1"


def _time(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Pricing timestamps require a timezone")
    return result


def matching_definition(definitions, model: str, timestamp: str):
    matches = []
    at = _time(timestamp)
    for definition in definitions:
        pattern = definition.get("matchPattern") or definition.get("match_pattern")
        start = definition.get("startDate") or definition.get("start_date")
        # Capture/creation time is NOT the effective date of a price.
        if not pattern or not start or _time(start) > at:
            continue
        if re.search(pattern, model):
            rank = (not definition.get("isLangfuseManaged", definition.get("is_langfuse_managed", True)), _time(start))
            matches.append((rank, definition))
    matches.sort(key=lambda item: item[0], reverse=True)
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        raise ValueError("Ambiguous equally ranked model prices")
    return matches[0][1] if matches else None


def _condition(condition, usage, event):
    source, operation = condition.get("source", "usage_details"), condition.get("operator")
    if source in {"model_parameters", "metadata"}:
        value = event.get(condition.get("key"))
        if value is None or value == "unknown":
            return None
        if operation not in {"in", "not_in"}:
            raise ValueError("Unsupported pricing condition")
        matched = str(value) in [str(v) for v in condition.get("values", [])]
        return matched if operation == "in" else not matched
    if source != "usage_details":
        raise ValueError("Unsupported pricing condition source")
    pattern = condition.get("usageDetailPattern") or condition.get("usage_detail_pattern")
    if not pattern:
        raise ValueError("Missing usage condition pattern")
    flags = 0 if condition.get("caseSensitive", condition.get("case_sensitive", False)) else re.I
    total = sum(v for k, v in usage.items() if re.search(pattern, k, flags))
    operators = {"gt": operator.gt, "gte": operator.ge, "lt": operator.lt,
                 "lte": operator.le, "eq": operator.eq, "neq": operator.ne}
    if operation not in operators:
        raise ValueError("Unsupported pricing comparison")
    return operators[operation](total, Decimal(str(condition["value"])))


def _sum_products(prices, buckets):
    values = []
    for key, amount in buckets.items():
        if not amount:
            continue
        price = Decimal(str(prices[key]))
        if not price.is_finite() or price < 0:
            raise ValueError("Invalid model price")
        with localcontext() as ctx:
            ctx.prec = len(price.as_tuple().digits) + len(str(amount)) + 1
            values.append(price * amount)
    if not values:
        return Decimal(0)
    with localcontext() as ctx:
        ctx.prec = max(v.adjusted() for v in values) - min(int(v.as_tuple().exponent) for v in values) + len(str(len(values))) + 2
        return sum(values, Decimal(0))


def estimate(event, definitions):
    """Price known buckets, or bounds over unknown disjoint cache allocations.

    The canonical ledger supplies inclusive input/output. Unknown cache tokens
    can occupy any portion of the remaining input; unknown reasoning is a
    subset of output. Evaluate the vertices, never silently replace unknowns
    with zero. Tier conditions depending on individual uncertain buckets are
    refused: endpoint samples cannot prove their interior extrema.
    """
    usage = event["usage"]
    unknown_cache = [key for key in ("cache_read_tokens", "cache_write_tokens") if usage.get(key) is None]
    unknown_reasoning = usage.get("reasoning_tokens") is None
    if not unknown_cache and not unknown_reasoning:
        return _estimate_known(event, definitions)
    if event["usage_status"] != "recorded" or usage.get("input_tokens") is None or usage.get("output_tokens") is None:
        return {"estimate_unavailable_reason": "missing_usage_buckets"}
    definition = matching_definition(definitions, event["model"], event["timestamp"])
    if definition is None:
        return {"estimate_unavailable_reason": "no_matching_model_price"}
    # Only conditions on inclusive input (the established Langfuse shape) are
    # invariant over these allocations. Other conditions need richer evidence.
    for tier in definition.get("pricingTiers") or definition.get("pricing_tiers") or []:
        for condition in tier.get("conditions", []):
            if condition.get("source", "usage_details") == "usage_details":
                pattern = condition.get("usageDetailPattern") or condition.get("usage_detail_pattern")
                flags = 0 if condition.get("caseSensitive", condition.get("case_sensitive", False)) else re.I
                selected = {key for key in ("input", "input_cached_tokens", "input_cache_creation", "output", "output_reasoning_tokens") if re.search(pattern, key, flags)}
                if selected not in ({"input", "input_cached_tokens", "input_cache_creation"}, {"output", "output_reasoning_tokens"}):
                    return {"estimate_unavailable_reason": "uncertain_bucket_price_condition"}
    remaining = usage["input_tokens"] - sum(usage.get(key) or 0 for key in ("cache_read_tokens", "cache_write_tokens"))
    if remaining < 0:
        return {"estimate_unavailable_reason": "missing_or_inconsistent_usage"}
    scenarios = []
    for allocation in [None, *unknown_cache]:
        known = {**usage, **{key: 0 for key in unknown_cache}, "uncached_input_tokens": remaining}
        if allocation:
            known[allocation], known["uncached_input_tokens"] = remaining, 0
        for reasoning in ([0, usage["output_tokens"]] if unknown_reasoning else [usage["reasoning_tokens"]]):
            scenarios.append(_estimate_known({**event, "usage": {**known, "reasoning_tokens": reasoning}}, definitions))
    unavailable = next((row for row in scenarios if "cost" not in row), None)
    if unavailable is not None:
        return unavailable
    lower = min(Decimal(row["cost"]) for row in scenarios)
    upper = max(Decimal(row["estimated_cost_upper"]) for row in scenarios)
    reasons = {reason for row in scenarios for reason in row["pricing_uncertainty"]}
    reasons.update(f"{key}_not_recorded" for key in unknown_cache)
    if unknown_reasoning:
        reasons.add("reasoning_tokens_not_recorded")
    return {**scenarios[0], "cost": str(lower), "estimated_cost_upper": str(upper),
            "pricing_status": "estimated_range" if lower != upper else "estimated",
            "pricing_uncertainty": sorted(reasons),
            "pricing_tiers": sorted({tier for row in scenarios for tier in row["pricing_tiers"]})}


def _estimate_known(event, definitions):
    if event["usage_status"] != "recorded":
        return {"estimate_unavailable_reason": "missing_or_inconsistent_usage"}
    definition = matching_definition(definitions, event["model"], event["timestamp"])
    if definition is None:
        return {"estimate_unavailable_reason": "no_matching_model_price"}
    if definition.get("unit", "TOKENS") != "TOKENS":
        return {"estimate_unavailable_reason": "unsupported_price_unit"}
    usage = event["usage"]
    required = ("uncached_input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens")
    if any(usage.get(k) is None for k in required):
        return {"estimate_unavailable_reason": "missing_usage_buckets"}
    if any(type(usage[k]) is not int or usage[k] < 0 for k in required) or usage["reasoning_tokens"] > usage["output_tokens"]:
        return {"estimate_unavailable_reason": "missing_or_inconsistent_usage"}
    buckets = {"input": usage["uncached_input_tokens"], "input_cached_tokens": usage["cache_read_tokens"],
               "input_cache_creation": usage["cache_write_tokens"],
               "output": usage["output_tokens"] - usage["reasoning_tokens"],
               "output_reasoning_tokens": usage["reasoning_tokens"]}
    tiers = definition.get("pricingTiers") or definition.get("pricing_tiers") or []
    if not tiers:
        tiers = [{"name": "flat", "isDefault": True, "priority": 0,
                  "prices": definition.get("prices") or {"input": definition.get("inputPrice"), "output": definition.get("outputPrice")}}]
    ordered = sorted(tiers, key=lambda t: (bool(t.get("isDefault", t.get("is_default"))), t.get("priority", 0)))
    possible, reasons = [], set()
    for tier in ordered:
        results = [_condition(c, buckets, event) for c in tier.get("conditions", [])]
        if False in results:
            continue
        possible.append(tier)
        if None in results:
            reasons.add("service_tier_or_metadata_not_recorded")
        else:
            break
    amounts = []
    for tier in possible:
        prices = tier.get("prices") or {}
        if any(amount and prices.get(key) is None for key, amount in buckets.items()):
            return {"estimate_unavailable_reason": "missing_usage_type_price"}
        total = _sum_products(prices, buckets)
        upper = total
        if usage.get("cache_write_status") == "not_recorded" and buckets["input"]:
            write_rate = prices.get("input_cache_creation")
            if write_rate is None:
                return {"estimate_unavailable_reason": "missing_cache_write_price"}
            alternative = _sum_products(prices, {**buckets, "input": 0, "input_cache_creation": buckets["input_cache_creation"] + buckets["input"]})
            total, upper = min(total, alternative), max(total, alternative)
            reasons.add("cache_writes_not_recorded")
        amounts.append((total, upper))
    if not amounts:
        return {"estimate_unavailable_reason": "no_matching_price_tier"}
    lower, upper = min(a[0] for a in amounts), max(a[1] for a in amounts)
    return {"cost": str(lower), "estimated_cost_upper": str(upper),
            "pricing_status": "estimated_range" if lower != upper else "estimated",
            "pricing_source": "langfuse_model_definition", "pricing_model_id": definition.get("id"),
            "pricing_effective_date": definition.get("startDate") or definition.get("start_date"),
            "pricing_tiers": [t["name"] for t in possible], "pricing_uncertainty": sorted(reasons),
            "valuation_algorithm": ALGORITHM_REVISION}
