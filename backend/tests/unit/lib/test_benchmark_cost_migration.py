"""Lossless migration planning without guessing historical telemetry joins."""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.lib.cost_ledger.benchmark_migration import plan_benchmark_cost_migration, verify_benchmark_cost_receipt
from src.lib.cost_ledger.facts import RecordedCharge, TokenUsage
from src.lib.cost_ledger.persistence import CostFacts
from src.models.sql.benchmark import BenchmarkInvocation, BenchmarkInvocationStatus


def invocation(**overrides):
    values = dict(
        id=UUID("10000000-0000-0000-0000-000000000001"),
        status=BenchmarkInvocationStatus.SUCCEEDED, model_request_id=None,
        input_tokens=None, output_tokens=None, total_tokens=None,
        billed_amount=None, billed_unit=None, billed_source=None,
    )
    return BenchmarkInvocation(**{**values, **overrides})


def plan(row, **overrides):
    return plan_benchmark_cost_migration(row, **{
        "deployment_id": "fixture-production", "source_namespace": "execution-a",
        "owner_subject": "job-owner", **overrides,
    })


def test_historical_identity_is_stable_but_does_not_claim_measurement():
    row = invocation()
    entry = plan(row)
    assert plan(row) == entry
    assert entry.identity_basis == "historical_benchmark_source"
    assert entry.model_request_id is None
    assert entry.invocation_id == row.id
    assert entry.attempt_id != row.id
    assert entry.attempt_id == UUID("163c26b9-bb92-57df-9a46-2a232ddf66a8")
    assert plan(row, source_namespace="execution-b").attempt_id != entry.attempt_id
    assert plan(row, deployment_id="fixture-dev").attempt_id != entry.attempt_id
    # Ownership is not an identity alias: changing it must conflict at binding.
    assert plan(row, owner_subject="other-owner").attempt_id == entry.attempt_id


def test_measured_identity_is_preserved_and_real_retry_is_distinct():
    measured = uuid4()
    entry = plan(invocation(model_request_id=measured, input_tokens=10))
    assert entry.attempt_id == entry.model_request_id == measured
    assert entry.identity_basis == "measured_request"
    assert plan(invocation(id=uuid4(), model_request_id=uuid4(), input_tokens=10)).attempt_id != measured


@pytest.mark.parametrize("status", [BenchmarkInvocationStatus.SUCCEEDED, BenchmarkInvocationStatus.FAILED, BenchmarkInvocationStatus.CANCELLED])
def test_terminal_outcome_does_not_suppress_cost(status):
    entry = plan(invocation(status=status, input_tokens=10, billed_amount=Decimal("0.000000000000123"), billed_unit="credits", billed_source="provider"))
    assert entry.usage == TokenUsage(input_tokens=10)
    assert entry.charge == RecordedCharge(Decimal("0.000000000000123"), "credits", "provider")
    assert entry.usage.total_tokens is None
    assert entry.usage.cache_read_tokens is None


def test_unknown_and_zero_and_inconsistent_are_preserved():
    missing = plan(invocation())
    zero = plan(invocation(input_tokens=0, output_tokens=0, billed_amount=Decimal("0"), billed_unit="USD", billed_source="provider"))
    bad = plan(invocation(input_tokens=10, output_tokens=20, total_tokens=1))
    assert missing.usage.status == "missing" and missing.charge is None
    assert zero.usage.status == "recorded" and zero.charge.amount == 0
    assert bad.usage.status == "inconsistent" and bad.usage.total_tokens == 1


@pytest.mark.parametrize("overrides", [
    {"status": BenchmarkInvocationStatus.RUNNING},
    {"id": None}, {"model_request_id": "unverified"},
    {"billed_amount": Decimal("1")}, {"billed_unit": "USD"},
    {"billed_amount": Decimal("NaN"), "billed_unit": "USD", "billed_source": "provider"},
])
def test_unsafe_rows_fail_preflight(overrides):
    with pytest.raises(ValueError):
        plan(invocation(**overrides))


@pytest.mark.parametrize("field", ["deployment_id", "source_namespace", "owner_subject"])
def test_migration_does_not_default_scope(field):
    with pytest.raises(ValueError):
        plan(invocation(), **{field: " "})


def test_receipt_requires_exact_unknowns_units_and_revision_coverage():
    entry = plan(invocation(input_tokens=10, billed_amount=Decimal("1"), billed_unit="credits", billed_source="provider"))
    receipt = CostFacts(1, entry.usage, entry.charge)
    verify_benchmark_cost_receipt(entry, receipt)
    for changed in (
        replace(receipt, usage=TokenUsage(input_tokens=10, output_tokens=0)),
        replace(receipt, charge=RecordedCharge(Decimal("1"), "USD", "provider")),
        replace(receipt, revision=None),
    ):
        with pytest.raises(ValueError):
            verify_benchmark_cost_receipt(entry, changed)
    missing = plan(invocation())
    verify_benchmark_cost_receipt(missing, CostFacts(None, TokenUsage(), None))
    with pytest.raises(ValueError):
        verify_benchmark_cost_receipt(missing, CostFacts(1, TokenUsage(), None))
