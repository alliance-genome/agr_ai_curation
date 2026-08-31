"""Pure, versioned deterministic scoring for canonical benchmark records."""

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from .models import (
    BenchmarkAggregateScore,
    BenchmarkCaseRun,
    BenchmarkDeterministicScore,
    BenchmarkFieldScore,
    BenchmarkScorerReference,
    StrictModel,
)

_MISSING = object()
_AMBIGUOUS_BASE_CLASSES = {
    "value_mismatch",
    "collection_mismatch",
    "evidence_mismatch",
}


class ScorerConfigurationError(ValueError):
    """A profile-declared deterministic scorer is invalid."""


class FieldScoringRule(StrictModel):
    path: str = ""
    comparison: Literal[
        "exact",
        "normalized_string",
        "normalized_identifier",
        "ordered_collection",
        "unordered_collection",
        "structured",
        "evidence",
    ] = "exact"
    weight: Decimal = Field(default=Decimal("1"), gt=0, strict=False)
    required: bool = True
    ambiguous: bool = False
    case_sensitive: bool = False
    item_comparison: Literal[
        "exact", "normalized_string", "normalized_identifier", "structured"
    ] = "exact"
    evidence_paths: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_semantics(self) -> "FieldScoringRule":
        if self.path and not self.path.startswith("/"):
            raise ValueError(
                "field path must be an RFC 6901 JSON pointer or empty root"
            )
        if self.comparison == "evidence" and not self.evidence_paths:
            raise ValueError("evidence comparison requires evidence_paths")
        if self.comparison != "evidence" and self.evidence_paths:
            raise ValueError("evidence_paths are only valid for evidence comparison")
        if any(path and not path.startswith("/") for path in self.evidence_paths):
            raise ValueError("evidence paths must be RFC 6901 JSON pointers")
        return self


class DeterministicScorerConfiguration(StrictModel):
    scoring_version: Literal[1] = 1
    fields: list[FieldScoringRule] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_paths(self) -> "DeterministicScorerConfiguration":
        paths = [rule.path for rule in self.fields]
        if len(paths) != len(set(paths)):
            raise ValueError("scorer field paths must be unique")
        return self


def parse_scorer_configuration(
    reference: BenchmarkScorerReference,
) -> DeterministicScorerConfiguration:
    """Resolve the canonical exact scorer or a configured deterministic-v1 scorer."""

    if reference.id == "exact-json":
        if reference.configuration:
            raise ScorerConfigurationError("exact-json does not accept configuration")
        return DeterministicScorerConfiguration(fields=[FieldScoringRule()])
    if reference.id != "deterministic-v1":
        raise ScorerConfigurationError(f"Unknown benchmark scorer: {reference.id}")
    try:
        return DeterministicScorerConfiguration.model_validate(reference.configuration)
    except ValidationError as exc:
        raise ScorerConfigurationError(
            f"Invalid configuration for scorer {reference.id}: {exc}"
        ) from exc


def validate_scorer_reference(reference: BenchmarkScorerReference) -> None:
    parse_scorer_configuration(reference)


def _pointer(value: Any, path: str) -> Any:
    if not path:
        return value
    current = value
    for raw_part in path.removeprefix("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _strict_equal(expected: Any, actual: Any) -> bool:
    return type(expected) is type(actual) and _canonical(expected) == _canonical(actual)


def _normalize_string(value: str, *, case_sensitive: bool) -> str:
    normalized = " ".join(value.split())
    return normalized if case_sensitive else normalized.casefold()


def _normalize_identifier(value: str, *, case_sensitive: bool) -> str:
    normalized = re.sub(r"\s+", "", value.strip())
    return normalized if case_sensitive else normalized.casefold()


def _item_key(value: Any, comparison: str, case_sensitive: bool) -> str:
    if comparison == "normalized_string" and isinstance(value, str):
        return _normalize_string(value, case_sensitive=case_sensitive)
    if comparison == "normalized_identifier" and isinstance(value, str):
        return _normalize_identifier(value, case_sensitive=case_sensitive)
    return f"{type(value).__name__}:{_canonical(value)}"


def _collection_score(
    expected: list[Any],
    actual: list[Any],
    *,
    ordered: bool,
    item_comparison: str,
    case_sensitive: bool,
) -> Decimal:
    denominator = max(len(expected), len(actual))
    if denominator == 0:
        return Decimal("1")
    expected_keys = [
        _item_key(item, item_comparison, case_sensitive) for item in expected
    ]
    actual_keys = [_item_key(item, item_comparison, case_sensitive) for item in actual]
    if ordered:
        matches = sum(left == right for left, right in zip(expected_keys, actual_keys))
    else:
        matches = sum((Counter(expected_keys) & Counter(actual_keys)).values())
    return Decimal(matches) / Decimal(denominator)


def _structured_score(expected: dict[str, Any], actual: dict[str, Any]) -> Decimal:
    keys = sorted(set(expected) | set(actual))
    if not keys:
        return Decimal("1")
    matches = sum(
        key in expected and key in actual and _strict_equal(expected[key], actual[key])
        for key in keys
    )
    return Decimal(matches) / Decimal(len(keys))


def _compare(
    expected: Any, actual: Any, rule: FieldScoringRule
) -> tuple[Decimal, str, str]:
    if actual is _MISSING:
        if not rule.required:
            return Decimal("1"), "none", "optional field is absent"
        return Decimal("0"), "missing_required", "required field is absent"
    if expected is _MISSING:
        return Decimal("0"), "malformed_output", "gold field is absent"

    comparison = rule.comparison
    if comparison == "exact":
        score = Decimal(_strict_equal(expected, actual))
        mismatch = "none" if score == 1 else "value_mismatch"
    elif comparison in {"normalized_string", "normalized_identifier"}:
        if not isinstance(expected, str) or not isinstance(actual, str):
            return Decimal("0"), "malformed_output", "comparison requires strings"
        normalize = (
            _normalize_string
            if comparison == "normalized_string"
            else _normalize_identifier
        )
        score = Decimal(
            normalize(expected, case_sensitive=rule.case_sensitive)
            == normalize(actual, case_sensitive=rule.case_sensitive)
        )
        mismatch = "none" if score == 1 else "value_mismatch"
    elif comparison in {"ordered_collection", "unordered_collection"}:
        if not isinstance(expected, list) or not isinstance(actual, list):
            return Decimal("0"), "malformed_output", "comparison requires collections"
        score = _collection_score(
            expected,
            actual,
            ordered=comparison == "ordered_collection",
            item_comparison=rule.item_comparison,
            case_sensitive=rule.case_sensitive,
        )
        mismatch = "none" if score == 1 else "collection_mismatch"
    elif comparison == "structured":
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            return Decimal("0"), "malformed_output", "comparison requires objects"
        score = _structured_score(expected, actual)
        mismatch = "none" if score == 1 else "value_mismatch"
    else:
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            return Decimal("0"), "malformed_output", "comparison requires objects"
        evidence_scores = []
        for path in rule.evidence_paths:
            expected_evidence = _pointer(expected, path)
            actual_evidence = _pointer(actual, path)
            if expected_evidence is _MISSING:
                return (
                    Decimal("0"),
                    "malformed_output",
                    f"gold evidence path is absent: {path}",
                )
            if actual_evidence is _MISSING:
                return (
                    Decimal("0"),
                    "missing_required",
                    f"required evidence path is absent: {path}",
                )
            evidence_scores.append(
                _strict_equal(expected_evidence, actual_evidence)
            )
        if not all(evidence_scores):
            score, mismatch = Decimal("0"), "evidence_mismatch"
        else:
            score = Decimal(int(_strict_equal(expected, actual)))
            mismatch = "none" if score == 1 else "value_mismatch"

    diagnostic = "values match" if score == 1 else "values differ under configured rule"
    return score, mismatch, diagnostic


def score_case(
    *,
    scorer: BenchmarkScorerReference,
    expected: Any,
    actual: Any = None,
    provider_failure: bool = False,
) -> BenchmarkDeterministicScore:
    """Score one canonical case without time, randomness, network, or provider state."""

    configuration = parse_scorer_configuration(scorer)
    fields: list[BenchmarkFieldScore] = []
    for rule in configuration.fields:
        if provider_failure:
            score = Decimal("0")
            mismatch = "provider_failure"
            diagnostic = "benchmark target did not produce an output"
        else:
            try:
                score, mismatch, diagnostic = _compare(
                    _pointer(expected, rule.path), _pointer(actual, rule.path), rule
                )
            except (TypeError, ValueError):
                score = Decimal("0")
                mismatch = "malformed_output"
                diagnostic = "output is not JSON-compatible with the configured rule"
        base_mismatch = None
        eligible = False
        if rule.ambiguous and mismatch in _AMBIGUOUS_BASE_CLASSES:
            base_mismatch = mismatch
            mismatch = "ambiguous"
            eligible = True
        outcome = "pass" if score == 1 else "fail" if score == 0 else "partial"
        fields.append(
            BenchmarkFieldScore(
                path=rule.path,
                rule=rule.comparison,
                weight=rule.weight,
                score=score,
                outcome=outcome,
                mismatch_class=mismatch,
                base_mismatch_class=base_mismatch,
                diagnostic=diagnostic,
                adjudication_eligible=eligible,
            )
        )
    total_weight = sum((field.weight for field in fields), Decimal("0"))
    earned_weight = sum((field.weight * field.score for field in fields), Decimal("0"))
    weighted_score = earned_weight / total_weight
    outcome = (
        "pass" if weighted_score == 1 else "fail" if weighted_score == 0 else "partial"
    )
    return BenchmarkDeterministicScore(
        scorer_id=scorer.id,
        outcome=outcome,
        weighted_score=weighted_score,
        earned_weight=earned_weight,
        total_weight=total_weight,
        fields=fields,
    )


def aggregate_scores(runs: list[BenchmarkCaseRun]) -> list[BenchmarkAggregateScore]:
    grouped: dict[tuple[str, str, str, str], list[BenchmarkDeterministicScore]] = {}
    for run in runs:
        for record in run.scoring:
            grouped.setdefault(
                (
                    run.profile_id,
                    run.requested_route.provider,
                    run.requested_route.model,
                    record.deterministic.scorer_id,
                ),
                [],
            ).append(record.deterministic)
    aggregates = []
    for profile_id, provider, model, scorer_id in sorted(grouped):
        scores = grouped[(profile_id, provider, model, scorer_id)]
        total_weight = sum((score.total_weight for score in scores), Decimal("0"))
        earned_weight = sum((score.earned_weight for score in scores), Decimal("0"))
        aggregates.append(
            BenchmarkAggregateScore(
                profile_id=profile_id,
                requested_route={"provider": provider, "model": model},
                scorer_id=scorer_id,
                case_count=len(scores),
                pass_count=sum(score.outcome == "pass" for score in scores),
                partial_count=sum(score.outcome == "partial" for score in scores),
                fail_count=sum(score.outcome == "fail" for score in scores),
                weighted_score=earned_weight / total_weight,
                earned_weight=earned_weight,
                total_weight=total_weight,
            )
        )
    return aggregates
