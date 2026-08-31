import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.lib.benchmarks.models import (
    BenchmarkCaseRun,
    BenchmarkRoute,
    BenchmarkScorerReference,
    BenchmarkScoringRecord,
    BenchmarkTarget,
)
from src.lib.benchmarks.scoring import aggregate_scores, score_case


def _scorer(*fields) -> BenchmarkScorerReference:
    return BenchmarkScorerReference(
        id="deterministic-v1",
        configuration={"scoring_version": 1, "fields": list(fields)},
    )


@pytest.mark.parametrize(
    ("rule", "expected", "actual"),
    [
        ("exact", {"a": 1}, {"a": 1}),
        ("normalized_string", "  Gene\n Name ", "gene name"),
        ("normalized_identifier", " REF : 123 ", "ref:123"),
        ("unordered_collection", ["B", "a"], ["a", "B"]),
        ("structured", {"a": 1, "b": 2}, {"b": 2, "a": 1}),
    ],
)
def test_supported_deterministic_semantics_pass(rule, expected, actual):
    item_comparison = "normalized_string" if rule == "unordered_collection" else "exact"
    result = score_case(
        scorer=_scorer(
            {
                "comparison": rule,
                "item_comparison": item_comparison,
                "case_sensitive": False,
            }
        ),
        expected=expected,
        actual=actual,
    )

    assert result.outcome == "pass"
    assert result.weighted_score == Decimal("1")
    assert result.fields[0].mismatch_class == "none"


def test_ordered_and_structured_mismatches_produce_stable_partial_scores():
    result = score_case(
        scorer=_scorer(
            {"path": "/items", "comparison": "ordered_collection", "weight": 2},
            {"path": "/record", "comparison": "structured", "weight": 1},
        ),
        expected={"items": [1, 2], "record": {"a": 1, "b": 2}},
        actual={"items": [1, 3], "record": {"a": 1, "b": 3}},
    )

    assert [field.score for field in result.fields] == [Decimal("0.5"), Decimal("0.5")]
    assert result.outcome == "partial"
    assert result.weighted_score == Decimal("0.5")
    assert result.fields[0].mismatch_class == "collection_mismatch"


def test_evidence_sensitive_output_classifies_evidence_mismatch():
    result = score_case(
        scorer=_scorer(
            {
                "comparison": "evidence",
                "evidence_paths": ["/evidence/quote", "/evidence/source"],
            }
        ),
        expected={"value": "x", "evidence": {"quote": "q", "source": "s"}},
        actual={"value": "x", "evidence": {"quote": "wrong", "source": "s"}},
    )

    assert result.outcome == "fail"
    assert result.fields[0].mismatch_class == "evidence_mismatch"


@pytest.mark.parametrize(
    ("expected", "actual", "mismatch"),
    [
        (
            {"value": "x", "evidence": {"quote": "q"}},
            {"value": "x", "evidence": {}},
            "missing_required",
        ),
        (
            {"value": "x", "evidence": {}},
            {"value": "x", "evidence": {"quote": "q"}},
            "malformed_output",
        ),
    ],
)
def test_missing_evidence_is_never_adjudication_eligible(expected, actual, mismatch):
    result = score_case(
        scorer=_scorer(
            {
                "comparison": "evidence",
                "evidence_paths": ["/evidence/quote"],
                "ambiguous": True,
            }
        ),
        expected=expected,
        actual=actual,
    )

    assert result.fields[0].mismatch_class == mismatch
    assert result.fields[0].adjudication_eligible is False


def test_present_semantic_evidence_mismatch_remains_adjudication_eligible():
    result = score_case(
        scorer=_scorer(
            {
                "comparison": "evidence",
                "evidence_paths": ["/evidence/quote"],
                "ambiguous": True,
            }
        ),
        expected={"evidence": {"quote": "expected interpretation"}},
        actual={"evidence": {"quote": "actual interpretation"}},
    )

    assert result.fields[0].mismatch_class == "ambiguous"
    assert result.fields[0].base_mismatch_class == "evidence_mismatch"
    assert result.fields[0].adjudication_eligible is True


@pytest.mark.parametrize(
    ("expected", "actual", "provider_failure", "mismatch"),
    [
        ({"required": 1}, {}, False, "missing_required"),
        ({"required": []}, {"required": "not-a-list"}, False, "malformed_output"),
        ({"required": 1}, None, True, "provider_failure"),
    ],
)
def test_hard_failures_have_machine_readable_classes(
    expected, actual, provider_failure, mismatch
):
    comparison = (
        "ordered_collection" if isinstance(expected["required"], list) else "exact"
    )
    result = score_case(
        scorer=_scorer({"path": "/required", "comparison": comparison}),
        expected=expected,
        actual=actual,
        provider_failure=provider_failure,
    )

    assert result.outcome == "fail"
    assert result.fields[0].mismatch_class == mismatch
    assert result.fields[0].adjudication_eligible is False


def test_only_profile_declared_ambiguity_opens_supplemental_gate():
    result = score_case(
        scorer=_scorer(
            {"path": "/name", "comparison": "normalized_string", "ambiguous": True}
        ),
        expected={"name": "alpha"},
        actual={"name": "beta"},
    )

    field = result.fields[0]
    assert result.outcome == "fail"
    assert field.mismatch_class == "ambiguous"
    assert field.base_mismatch_class == "value_mismatch"
    assert field.adjudication_eligible is True


def test_golden_score_and_aggregate_are_identical_across_reruns():
    scorer = _scorer(
        {"path": "/name", "comparison": "normalized_string", "weight": 2},
        {"path": "/ids", "comparison": "unordered_collection", "weight": 1},
    )
    first = score_case(
        scorer=scorer,
        expected={"name": "Gene A", "ids": ["A", "B"]},
        actual={"name": " gene  a ", "ids": ["A", "C"]},
    )
    second = score_case(
        scorer=scorer,
        expected={"name": "Gene A", "ids": ["A", "B"]},
        actual={"name": " gene  a ", "ids": ["A", "C"]},
    )
    assert first == second

    fixture_path = Path(__file__).parent / "fixtures" / "deterministic_score_v1.json"
    assert first.model_dump(mode="json") == json.loads(
        fixture_path.read_text(encoding="utf-8")
    )

    run = BenchmarkCaseRun.model_validate(
        {
            "run_id": "run-1",
            "profile_id": "profile-1",
            "case_id": "case-1",
            "target": BenchmarkTarget(kind="agent", id="gene"),
            "requested_route": BenchmarkRoute(provider="openai", model="gpt-5.6-sol"),
            "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "completed_at": datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
            "latency_ms": 1000,
            "status": "succeeded",
            "fixture_digest": "sha256:test",
            "scoring": [BenchmarkScoringRecord(deterministic=first)],
        }
    )
    aggregate = aggregate_scores([run])[0]
    assert aggregate.case_count == 1
    assert aggregate.profile_id == "profile-1"
    assert aggregate.requested_route == BenchmarkRoute(
        provider="openai", model="gpt-5.6-sol"
    )
    assert aggregate.partial_count == 1
    assert aggregate.weighted_score == first.weighted_score

    second_route_run = run.model_copy(
        update={
            "run_id": "run-2",
            "requested_route": BenchmarkRoute(
                provider="openrouter", model="openai/gpt-5.6-sol"
            ),
        }
    )
    route_aggregates = aggregate_scores([run, second_route_run])
    assert len(route_aggregates) == 2
    assert [item.case_count for item in route_aggregates] == [1, 1]
    assert [item.requested_route.provider for item in route_aggregates] == [
        "openai",
        "openrouter",
    ]
