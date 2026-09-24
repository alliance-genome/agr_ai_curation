"""The full outcome matrix of an unresolved validator result's classification (ALL-1283 fix4).

A decisive classification (not_found, ambiguous, conflict, rejected_candidates)
overrules a value that reads as resolved; a non-decisive one never does.
"""

from __future__ import annotations

import pytest

from src.lib.domain_packs.resolvable_values import DECISIVE_OUTCOMES, lookup_outcome_for_failure
from src.lib.domain_packs.validator_result_classification import validator_failure_classification
from src.schemas.domain_validator import DomainValidatorResultBase

# Every single lookup outcome and every pair, as the lookups of one unresolved result.
MATRIX = {
    ("success",): "rejected_candidates",
    ("not_found",): "not_found",
    ("ambiguous",): "ambiguous",
    ("conflict",): "conflict",
    ("blocked",): "blocked",
    ("error",): "transient",
    ("success", "not_found"): "rejected_candidates",
    ("success", "ambiguous"): "ambiguous",
    ("success", "conflict"): "conflict",
    ("success", "blocked"): "blocked",
    ("success", "error"): "transient",
    ("not_found", "ambiguous"): "ambiguous",
    ("not_found", "conflict"): "conflict",
    ("not_found", "blocked"): "blocked",
    ("not_found", "error"): "transient",
    ("ambiguous", "conflict"): "ambiguous",
    ("ambiguous", "blocked"): "blocked",
    ("ambiguous", "error"): "transient",
    ("conflict", "blocked"): "blocked",
    ("conflict", "error"): "transient",
    ("blocked", "error"): "transient",
}


def _result(outcomes, *, missing=(), resolved_values=None, methods=None):
    return DomainValidatorResultBase.model_validate({
        "status": "unresolved", "request_id": "request-1", "validator_binding_id": "fixture.lookup",
        "validator_agent": {"package_id": "fixture.validators", "agent_id": "lookup"},
        "target": {"object_type": "Observation", "field_path": "site", "domain_pack_id": "fixture.pack"},
        "resolved_values": resolved_values or {}, "resolved_objects": [],
        "missing_expected_fields": list(missing), "candidates": [],
        "lookup_attempts": [
            {"provider": "fixture", "method": (methods or {}).get(index, "lookup"), "query": {},
             "result_count": 0, "outcome": outcome}
            for index, outcome in enumerate(outcomes)
        ],
        "curator_message": None, "explanation": "Fixture.",
    })


@pytest.mark.parametrize("missing", [(), ("curie", "name")], ids=["no_missing", "all_missing"])
@pytest.mark.parametrize(("outcomes", "classification"), list(MATRIX.items()), ids=lambda value: str(value))
def test_every_lookup_outcome_combination_classifies_as_the_table_says(outcomes, classification, missing):
    """An unresolved result that filled no expected field lists them all as missing whatever the
    reason; the lookups decide, in either order."""

    assert validator_failure_classification(_result(outcomes, missing=missing)) == classification
    assert validator_failure_classification(_result(tuple(reversed(outcomes)), missing=missing)) == classification


@pytest.mark.parametrize("outcomes", [key for key in MATRIX if {"error", "blocked"} & set(key)])
def test_a_lookup_that_could_not_run_never_makes_a_result_decisive(outcomes):
    """S1: an outage (error) or a blocked lookup never overrules a value."""

    classification = validator_failure_classification(_result(outcomes, missing=("curie", "name")))
    assert lookup_outcome_for_failure(classification) not in DECISIVE_OUTCOMES


@pytest.mark.parametrize("outcomes", [("success",), ("not_found",), ("ambiguous",), ("success", "not_found")])
def test_a_partly_filled_result_is_incomplete(outcomes):
    """Some expected fields filled, others missing: a genuinely incomplete result, non-decisive."""

    result = _result(outcomes, missing=("name",), resolved_values={"curie": "ONT:1"})
    assert validator_failure_classification(result) == "missing_expected_result_field"


def test_a_result_without_lookups_is_incomplete_or_unclassifiable():
    assert validator_failure_classification(_result((), missing=("curie",))) == "missing_expected_result_field"
    with pytest.raises(ValueError, match="Unable to classify"):
        validator_failure_classification(_result(()))


@pytest.mark.parametrize(("method", "classification"), [
    ("invalid_schema", "invalid_schema"), ("validator_agent_error", "transient"),
])
def test_a_validator_that_failed_to_produce_a_result_is_never_decisive(method, classification):
    for outcomes in MATRIX:
        result = _result(outcomes, missing=("curie",), methods={0: method})
        assert validator_failure_classification(result) == classification


@pytest.mark.parametrize("empty", [None, "", [], {}])
@pytest.mark.parametrize(("outcomes", "classification"), [
    (("not_found",), "not_found"), (("success",), "rejected_candidates"),
])
def test_an_empty_resolved_value_fills_nothing(empty, outcomes, classification):
    """V1: a result carrying an expected field with no value is not partly filled."""

    result = _result(outcomes, missing=("curie", "name"), resolved_values={"curie": empty})
    assert validator_failure_classification(result) == classification
