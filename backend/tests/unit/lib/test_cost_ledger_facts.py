"""Accounting facts never manufacture zero usage or duplicate replayed cost."""

from decimal import Decimal

import pytest

from src.lib.cost_ledger.facts import (
    CostFactConflict, RecordedCharge, TokenUsage, enrich_charge, enrich_usage,
)


def test_unknown_is_distinct_from_recorded_zero():
    unknown = TokenUsage()
    zero = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    assert unknown.status == "missing"
    assert zero.status == "recorded"
    assert zero.cache_read_tokens is None
    assert zero.uncached_input_tokens is None
    assert zero.nonreasoning_output_tokens is None
    assert TokenUsage(cache_read_tokens=0).status == "partial"


def test_inclusive_totals_do_not_double_count_subsets():
    usage = TokenUsage(100, 40, 140, 30, 10, 15)
    assert usage.status == "recorded"
    assert usage.uncached_input_tokens == 60
    assert usage.nonreasoning_output_tokens == 25
    assert usage.total_tokens == 140


@pytest.mark.parametrize("usage, issue", [
    (TokenUsage(input_tokens=10, cache_read_tokens=11), "cache_subsets_exceed_input"),
    (TokenUsage(input_tokens=10, cache_read_tokens=6, cache_write_tokens=5), "cache_subsets_exceed_input"),
    (TokenUsage(output_tokens=10, reasoning_tokens=11), "reasoning_exceeds_output"),
    (TokenUsage(input_tokens=11, total_tokens=10), "total_tokens_mismatch"),
    (TokenUsage(cache_read_tokens=7, reasoning_tokens=4, total_tokens=10), "total_tokens_mismatch"),
    (TokenUsage(input_tokens=5, output_tokens=5, total_tokens=11), "total_tokens_mismatch"),
])
def test_inconsistent_evidence_is_preserved_not_clamped(usage, issue):
    assert usage.status == "inconsistent"
    assert issue in usage.issues
    assert usage.uncached_input_tokens is None
    assert usage.nonreasoning_output_tokens is None


@pytest.mark.parametrize("value", [-1, True, 1.5, "10"])
def test_tokens_reject_lossy_coercion(value):
    with pytest.raises(ValueError):
        TokenUsage(input_tokens=value)


def test_late_details_and_repeated_evidence_are_not_additive():
    original = TokenUsage(input_tokens=100, output_tokens=40)
    details = TokenUsage(total_tokens=140, cache_read_tokens=30, cache_write_tokens=10, reasoning_tokens=15)
    enriched = enrich_usage(original, details)
    assert enriched == TokenUsage(100, 40, 140, 30, 10, 15)
    assert enrich_usage(enriched, details) == enriched
    assert enrich_usage(enriched, TokenUsage()) == enriched
    assert original.total_tokens is None


def test_late_contradiction_requires_reconciliation():
    with pytest.raises(CostFactConflict):
        enrich_usage(TokenUsage(input_tokens=10), TokenUsage(input_tokens=11))
    contradictory = enrich_usage(TokenUsage(input_tokens=10), TokenUsage(cache_read_tokens=11))
    assert contradictory.status == "inconsistent"


def test_recorded_charge_preserves_exact_decimal_zero_and_units():
    charge = RecordedCharge(Decimal("0.000000000000000000123"), "credits", "provider-usage")
    assert enrich_charge(None, charge) == charge
    assert enrich_charge(charge, charge) == charge
    assert enrich_charge(charge, None) == charge
    assert enrich_charge(None, None) is None
    zero = RecordedCharge(Decimal("0"), "USD", "provider-invoice")
    assert enrich_charge(None, zero) == zero


@pytest.mark.parametrize("amount", [Decimal("NaN"), Decimal("Infinity"), Decimal("-1"), 0.1, True])
def test_recorded_charge_rejects_invalid_or_inexact_input(amount):
    with pytest.raises(ValueError):
        RecordedCharge(amount, "USD", "provider")


@pytest.mark.parametrize("other", [
    RecordedCharge(Decimal("2"), "credits", "provider"),
    RecordedCharge(Decimal("1"), "USD", "provider"),
    RecordedCharge(Decimal("1"), "credits", "different-source"),
])
def test_charges_do_not_silently_overwrite_or_convert(other):
    with pytest.raises(CostFactConflict):
        enrich_charge(RecordedCharge(Decimal("1"), "credits", "provider"), other)


@pytest.mark.parametrize("unit, source", [("", "provider"), ("USD", " ")])
def test_charge_requires_provenance(unit, source):
    with pytest.raises(ValueError):
        RecordedCharge(Decimal("1"), unit, source)
