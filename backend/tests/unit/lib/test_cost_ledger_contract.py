"""Pinned, strict accounting read contracts without silently changing history."""

from decimal import Decimal
import json
from unittest.mock import MagicMock
from uuid import uuid4

from pydantic import ValidationError
import pytest

from src.lib.cost_ledger.facts import RecordedCharge, TokenUsage
from src.lib.cost_ledger.projections import resolve_cost_reference
from src.schemas.cost_ledger import CostFactsProjection, CostLedgerReference


def reference(revision=1):
    return CostLedgerReference(schema_version=1, deployment_id="fixture", attempt_id=uuid4(), fact_revision=revision)


def test_projection_roundtrip_preserves_exact_decimal_and_unknowns():
    usage = TokenUsage(input_tokens=10)
    projection = CostFactsProjection(
        schema_version=1, reference=reference(), usage=usage, usage_status=usage.status,
        usage_issues=usage.issues,
        recorded_charge=RecordedCharge(Decimal("0.000000000000123"), "credits", "provider"),
    )
    payload = projection.model_dump_json()
    assert isinstance(json.loads(payload)["recorded_charge"]["amount"], str)
    assert CostFactsProjection.model_validate_json(payload) == projection
    assert projection.usage.output_tokens is None


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"schema_version": None},
    {"fact_revision": None}, {"fact_revision": -1}, {"fact_revision": True},
    {"deployment_id": " "}, {"extra_cost": 1},
])
def test_reference_rejects_ambiguous_versions_scope_and_latest(changes):
    payload = reference().model_dump(mode="json")
    payload.update(changes)
    with pytest.raises(ValidationError):
        CostLedgerReference.model_validate_json(json.dumps(payload))


def test_version_is_required_not_an_implicit_legacy_default():
    payload = reference().model_dump(mode="json")
    del payload["schema_version"]
    with pytest.raises(ValidationError):
        CostLedgerReference.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("changes", [
    {"usage_status": "recorded"},
    {"usage_issues": ("invented_issue",)},
    {"reference": reference(1)},
])
def test_projection_rejects_false_usage_or_empty_revision_claim(changes):
    with pytest.raises(ValidationError):
        CostFactsProjection(**{
            "schema_version": 1, "reference": reference(0), "usage": TokenUsage(),
            "usage_status": "missing", "usage_issues": (), "recorded_charge": None,
            **changes,
        })


def test_known_zero_charge_is_not_empty_snapshot():
    with pytest.raises(ValidationError):
        CostFactsProjection(
            schema_version=1, reference=reference(0), usage=TokenUsage(),
            usage_status="missing", usage_issues=(),
            recorded_charge=RecordedCharge(Decimal(0), "USD", "provider"),
        )


def test_dependency_outage_propagates_instead_of_becoming_unknown_cost():
    session = MagicMock()
    session.scalar.side_effect = RuntimeError("fixture database unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        resolve_cost_reference(session, reference=reference(), owner_subject="owner")


@pytest.mark.parametrize("bad_amount", [0.1, True, "NaN", "Infinity", "-1"])
def test_invalid_charge_transport_is_rejected(bad_amount):
    payload = {
        "schema_version": 1, "reference": reference().model_dump(mode="json"),
        "usage": {}, "usage_status": "missing", "usage_issues": [],
        "recorded_charge": {"amount": bad_amount, "unit": "USD", "source": "provider"},
    }
    with pytest.raises(ValidationError):
        CostFactsProjection.model_validate_json(json.dumps(payload))
