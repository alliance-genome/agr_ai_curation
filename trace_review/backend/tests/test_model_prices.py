from decimal import Decimal

from src.services.cost_report import build_report
from agr_cost_pricing import estimate, matching_definition
from src.services.langfuse_run_reconstruction import usage_cost_summary


def definitions():
    # Synthetic prices, not an operational catalog. Numerical acceptance fixture.
    prices = {"input": .000010, "input_cached_tokens": .000001,
              "input_cache_creation": .0000125, "output": .000050,
              "output_reasoning_tokens": .000050}
    return [{"id": "fixture", "matchPattern": "^fixture$", "startDate": "2026-09-01T00:00:00Z",
             "pricingTiers": [
                 {"name": "Standard", "isDefault": True, "priority": 0, "prices": prices},
                 {"name": "Priority", "priority": 1, "conditions": [
                     {"source": "model_parameters", "key": "service_tier", "operator": "in", "values": ["priority"]},
                 ], "prices": {k: v * 2 for k, v in prices.items()}},
             ]}]


def event(tier="default", writes=True):
    details = {"input": 100, "input_cached_tokens": 600, "output": 50, "output_reasoning_tokens": 150}
    if writes:
        details["input_cache_creation"] = 300
    return {"usage": usage_cost_summary({"usageDetails": details}), "usage_status": "recorded", "model": "fixture",
            "timestamp": "2026-09-07T00:00:00Z", "service_tier": tier}


def test_acceptance_price_and_service_tier():
    assert Decimal(estimate(event(), definitions())["cost"]) == Decimal(".01535")
    assert Decimal(estimate(event("priority"), definitions())["cost"]) == Decimal(".03070")
    unknown = estimate(event("unknown"), definitions())
    assert unknown["pricing_status"] == "estimated_range"
    assert Decimal(unknown["cost"]) == Decimal(".01535")
    assert Decimal(unknown["estimated_cost_upper"]) == Decimal(".03070")


def test_missing_writes_missing_model_and_effective_date():
    unknown = estimate(event(writes=False), definitions())
    assert unknown["pricing_status"] == "estimated_range"
    assert "cache_writes_not_recorded" in unknown["pricing_uncertainty"]
    assert matching_definition(definitions(), "fixture", "2026-08-01T00:00:00Z") is None
    assert matching_definition(definitions(), "unrelated", "2026-09-07T00:00:00Z") is None
    assert estimate(event(), [])["estimate_unavailable_reason"] == "no_matching_model_price"


def test_exact_decimal_and_duplicate_conflict_is_not_two_charges():
    observation = {"id": "one", "traceId": "trace", "type": "GENERATION",
                   "startTime": "2026-09-07T00:00:00Z", "costDetails": {"total": "0.123456789123456789"}}
    kwargs = {"start": "2026-09-07T00:00:00Z", "end": "2026-09-08T00:00:00Z"}
    report = build_report([{"observations": [observation]}], **kwargs)
    assert report["totals"]["total_cost"] == "0.123456789123456789"
    report = build_report([{"observations": [observation, {**observation, "costDetails": {"total": 2}}]}], **kwargs)
    assert report["totals"]["calls"] == 1
    assert report["totals"]["total_cost"] is None
    assert report["duplicate_conflicts"] == 1
