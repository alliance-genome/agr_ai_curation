"""Synthetic numerical contracts; no operational price catalog."""
from decimal import Decimal, localcontext
import pytest
from agr_cost_pricing import estimate, matching_definition


def catalog():
    return [{"id": "synthetic", "unit": "TOKENS", "matchPattern": "^test$", "startDate": "2026-01-01T00:00:00Z",
             "prices": {"input": "0.00001", "input_cached_tokens": "0.000001",
                        "input_cache_creation": "0.0000125", "output": "0.00005", "output_reasoning_tokens": "0.00005"}}]


def event():
    return {"usage_status": "recorded", "model": "test", "timestamp": "2026-09-26T00:00:00Z",
            "usage": {"uncached_input_tokens": 100, "cache_read_tokens": 600, "cache_write_tokens": 300,
                      "output_tokens": 200, "reasoning_tokens": 150}}


def test_decimal_independent_of_ambient_precision():
    with localcontext() as ctx:
        ctx.prec = 2
        result = estimate(event(), catalog())
    assert Decimal(result["cost"]) == Decimal("0.01535")
    assert result["cost"] == result["estimated_cost_upper"]


def test_unknown_cache_is_not_zero():
    sample = event()
    sample["usage"]["cache_write_tokens"] = None
    assert estimate(sample, catalog())["estimate_unavailable_reason"] == "missing_usage_buckets"


def test_inclusive_usage_bounds_missing_cache_write_and_reasoning():
    sample = event()
    sample['usage'].update(input_tokens=1000, cache_write_tokens=None, uncached_input_tokens=None, reasoning_tokens=None)
    result = estimate(sample, catalog())
    assert Decimal(result['cost']) == Decimal('0.0146')
    assert Decimal(result['estimated_cost_upper']) == Decimal('0.0156')
    assert 'cache_write_tokens_not_recorded' in result['pricing_uncertainty']
    assert 'reasoning_tokens_not_recorded' in result['pricing_uncertainty']
    prices = catalog()
    prices[0]['pricingTiers'] = None
    assert estimate(sample, prices) == result


def test_effective_date_timezone_and_ambiguity():
    prices = catalog()
    assert matching_definition(prices, "test", "2025-12-31T23:59:59Z") is None
    assert matching_definition(prices, "test", "2025-12-31T19:00:00-05:00") == prices[0]
    with pytest.raises(ValueError, match="Ambiguous"):
        matching_definition(prices * 2, "test", "2026-01-01T00:00:00Z")
    prices[0].pop("startDate")
    prices[0]["createdAt"] = "2020-01-01T00:00:00Z"
    assert matching_definition(prices, "test", "2026-01-01T00:00:00Z") is None


def test_zero_usage_is_estimated_zero_but_unknown_model_is_unpriced():
    sample = event()
    sample["usage"] = dict.fromkeys(sample["usage"], 0)
    assert estimate(sample, catalog())["cost"] == "0"
    assert estimate(sample, [])["estimate_unavailable_reason"] == "no_matching_model_price"


def test_invalid_prices_rejected_even_when_negative_total_would_be_masked():
    prices = catalog()
    prices[0]["prices"]["input"] = "-0.000001"
    with pytest.raises(ValueError, match="Invalid"):
        estimate(event(), prices)
