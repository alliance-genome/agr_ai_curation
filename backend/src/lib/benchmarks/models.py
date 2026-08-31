"""Versioned schemas shared by benchmark loading, API, CLI, and reporting."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FrozenStrictModel(BaseModel):
    """Immutable strict input and planning contracts for benchmark suite v2."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BenchmarkInputReference(FrozenStrictModel):
    """Typed immutable provenance; resolver behavior is owned by ALL-979."""

    resolver: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    reference: str = Field(min_length=1, max_length=1024)
    version: str = Field(min_length=1, max_length=255)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("reference")
    @classmethod
    def reject_network_destination(cls, value: str) -> str:
        if "://" in value:
            raise ValueError("benchmark input references must not be network URLs")
        return value


class BenchmarkSuiteCase(FrozenStrictModel):
    case_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    target: "BenchmarkTarget"
    input: BenchmarkInputReference


class BenchmarkRoute(FrozenStrictModel):
    provider: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN)
    model: str = Field(min_length=1, max_length=255, pattern=_IDENTIFIER_PATTERN)
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None


class BenchmarkConfiguration(FrozenStrictModel):
    configuration_id: str = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    routes: dict[str, BenchmarkRoute] = Field(default_factory=dict)

    @field_validator("routes")
    @classmethod
    def require_route_slot_names(
        cls, value: dict[str, BenchmarkRoute]
    ) -> dict[str, BenchmarkRoute]:
        for slot in value:
            if slot != "supervisor" and not (
                slot.startswith("agent:") or slot.startswith("validator:")
            ):
                raise ValueError(
                    "route slots must be supervisor, agent:<id>, or validator:<id>"
                )
            suffix = slot.partition(":")[2]
            if slot != "supervisor" and (
                not suffix or not re.fullmatch(_IDENTIFIER_PATTERN, suffix)
            ):
                raise ValueError(f"invalid route slot: {slot}")
        return value


class BenchmarkSuite(FrozenStrictModel):
    schema_version: Literal[2]
    suite_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    cases: tuple[BenchmarkSuiteCase, ...] = Field(min_length=1, strict=False)
    configurations: tuple[BenchmarkConfiguration, ...] = Field(
        min_length=1, strict=False
    )
    repetitions: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def require_unique_suite_entries(self) -> "BenchmarkSuite":
        for label, values in (
            ("case IDs", [case.case_id for case in self.cases]),
            (
                "configuration IDs",
                [
                    configuration.configuration_id
                    for configuration in self.configurations
                ],
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"suite {label} must not contain duplicates")
        return self


class BenchmarkModelCatalogEntry(FrozenStrictModel):
    provider: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN)
    model: str = Field(min_length=1, max_length=255, pattern=_IDENTIFIER_PATTERN)
    reasoning_efforts: tuple[
        Literal["minimal", "low", "medium", "high", "xhigh"], ...
    ] = ()


class BenchmarkRouteSlot(FrozenStrictModel):
    slot: str
    kind: Literal["supervisor", "agent", "validator"]
    default_route: BenchmarkRoute

    @model_validator(mode="after")
    def require_canonical_slot(self) -> "BenchmarkRouteSlot":
        expected_prefix = {
            "supervisor": "supervisor",
            "agent": "agent:",
            "validator": "validator:",
        }[self.kind]
        if self.kind == "supervisor":
            valid = self.slot == expected_prefix
        else:
            suffix = self.slot.removeprefix(expected_prefix)
            valid = self.slot.startswith(expected_prefix) and bool(
                re.fullmatch(_IDENTIFIER_PATTERN, suffix)
            )
        if not valid:
            raise ValueError(f"slot '{self.slot}' does not match kind '{self.kind}'")
        return self


class BenchmarkTargetCatalogEntry(FrozenStrictModel):
    target: "BenchmarkTarget"
    route_slots: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_slots(self) -> "BenchmarkTargetCatalogEntry":
        if len(self.route_slots) != len(set(self.route_slots)):
            raise ValueError("target route slots must not contain duplicates")
        return self


class BenchmarkRouteCatalog(FrozenStrictModel):
    schema_version: Literal[1] = 1
    models: tuple[BenchmarkModelCatalogEntry, ...] = Field(min_length=1)
    route_slots: tuple[BenchmarkRouteSlot, ...] = Field(min_length=1)
    targets: tuple[BenchmarkTargetCatalogEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_consistent_catalog(self) -> "BenchmarkRouteCatalog":
        model_keys = [(item.provider, item.model) for item in self.models]
        slots = [item.slot for item in self.route_slots]
        targets = [(item.target.kind, item.target.id) for item in self.targets]
        for label, values in (
            ("models", model_keys),
            ("route slots", slots),
            ("targets", targets),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"catalog {label} must not contain duplicates")
        known_models = {(item.provider, item.model): item for item in self.models}
        for slot in self.route_slots:
            route_key = (slot.default_route.provider, slot.default_route.model)
            if route_key not in known_models:
                raise ValueError(
                    f"default route for '{slot.slot}' is not in the model catalog"
                )
            effort = slot.default_route.reasoning_effort
            if (
                effort is not None
                and effort not in known_models[route_key].reasoning_efforts
            ):
                raise ValueError(
                    f"default route for '{slot.slot}' uses unsupported reasoning "
                    f"effort '{effort}'"
                )
        known_slots = set(slots)
        for target in self.targets:
            unknown = set(target.route_slots) - known_slots
            if unknown:
                raise ValueError(
                    f"target '{target.target.id}' has unknown route slots: "
                    + ", ".join(sorted(unknown))
                )
        return self


class ResolvedBenchmarkCase(FrozenStrictModel):
    case_id: str
    target: "BenchmarkTarget"
    input: BenchmarkInputReference


class ResolvedBenchmarkCell(FrozenStrictModel):
    cell_id: str
    case_id: str
    configuration_id: str
    repetition: int = Field(ge=1)
    target: "BenchmarkTarget"
    input: BenchmarkInputReference
    routes: dict[str, BenchmarkRoute]


class ResolvedBenchmarkPlan(FrozenStrictModel):
    schema_version: Literal[2] = 2
    suite_id: str
    suite_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    catalog_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repetitions: int = Field(ge=1)
    cases: tuple[ResolvedBenchmarkCase, ...]
    configurations: tuple[BenchmarkConfiguration, ...]
    cells: tuple[ResolvedBenchmarkCell, ...]
    plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class BenchmarkTarget(StrictModel):
    kind: Literal["agent", "flow"]
    id: str = Field(min_length=1, max_length=255)


class BenchmarkCaseReference(StrictModel):
    case_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    fixture: str = Field(min_length=1, max_length=512)
    expected: str = Field(min_length=1, max_length=512)


class BenchmarkScorerReference(StrictModel):
    id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    configuration: dict[str, Any] = Field(default_factory=dict)


class BenchmarkProfile(StrictModel):
    schema_version: Literal[1]
    profile_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    target: BenchmarkTarget
    routes: list[BenchmarkRoute] = Field(min_length=1)
    cases: list[BenchmarkCaseReference] = Field(min_length=1)
    scorers: list[BenchmarkScorerReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_entries(self) -> "BenchmarkProfile":
        for label, values in (
            ("routes", [(route.provider, route.model) for route in self.routes]),
            ("case IDs", [case.case_id for case in self.cases]),
            ("scorer IDs", [scorer.id for scorer in self.scorers]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"profile {label} must not contain duplicates")
        return self


class LoadedBenchmarkCase(StrictModel):
    case_id: str
    fixture_path: str
    expected_path: str
    fixture_digest: str
    input: dict[str, Any]
    expected: Any


class BenchmarkSelection(StrictModel):
    profile_ids: list[str] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    route: BenchmarkRoute | None = None

    @field_validator("profile_ids", "case_ids")
    @classmethod
    def require_unique_selection(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("selection IDs must not contain duplicates")
        return value


class PlannedCaseRun(StrictModel):
    run_id: str
    profile_id: str
    case_id: str
    target: BenchmarkTarget
    requested_route: BenchmarkRoute
    fixture_digest: str


class DryRunPlan(StrictModel):
    schema_version: Literal[1] = 1
    runs: list[PlannedCaseRun]


class BilledCost(StrictModel):
    amount: Decimal = Field(ge=0, strict=False)
    unit: str
    source: str


class ProviderUsage(StrictModel):
    requested_provider: str
    requested_model: str
    actual_provider: str | None = None
    actual_model: str | None = None
    routing_attempt: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    billed_cost: BilledCost | None = None


class BenchmarkFailure(StrictModel):
    category: Literal[
        "timeout", "configuration_error", "runtime_error", "internal_error"
    ]
    message: str = Field(max_length=512)


class BenchmarkOutput(StrictModel):
    kind: Literal["json", "text", "preview"]
    value: Any
    truncated: bool = False
    size_bytes: int = Field(ge=0)


class BenchmarkFieldScore(StrictModel):
    """One deterministic, machine-readable comparison outcome."""

    path: str
    rule: Literal[
        "exact",
        "normalized_string",
        "normalized_identifier",
        "ordered_collection",
        "unordered_collection",
        "structured",
        "evidence",
    ]
    weight: Decimal = Field(gt=0, strict=False)
    score: Decimal = Field(ge=0, le=1, strict=False)
    outcome: Literal["pass", "partial", "fail"]
    mismatch_class: Literal[
        "none",
        "value_mismatch",
        "collection_mismatch",
        "evidence_mismatch",
        "missing_required",
        "malformed_output",
        "provider_failure",
        "ambiguous",
    ]
    base_mismatch_class: (
        Literal["value_mismatch", "collection_mismatch", "evidence_mismatch"] | None
    ) = None
    diagnostic: str
    adjudication_eligible: bool = False

    @model_validator(mode="after")
    def require_consistent_ambiguity(self) -> "BenchmarkFieldScore":
        is_ambiguous = self.mismatch_class == "ambiguous"
        if is_ambiguous != self.adjudication_eligible:
            raise ValueError("only ambiguous mismatches may be adjudication eligible")
        if is_ambiguous != (self.base_mismatch_class is not None):
            raise ValueError(
                "ambiguous mismatches require their deterministic base class"
            )
        return self


class BenchmarkDeterministicScore(StrictModel):
    """Versioned deterministic result; supplemental judging never mutates it."""

    scoring_version: Literal[1] = 1
    scorer_id: str
    outcome: Literal["pass", "partial", "fail"]
    weighted_score: Decimal = Field(ge=0, le=1, strict=False)
    earned_weight: Decimal = Field(ge=0, strict=False)
    total_weight: Decimal = Field(gt=0, strict=False)
    fields: list[BenchmarkFieldScore] = Field(min_length=1)

    @model_validator(mode="after")
    def require_consistent_metrics(self) -> "BenchmarkDeterministicScore":
        total = sum((field.weight for field in self.fields), Decimal("0"))
        earned = sum(
            (field.weight * field.score for field in self.fields), Decimal("0")
        )
        expected_outcome = (
            "pass" if earned == total else "fail" if earned == 0 else "partial"
        )
        if (
            self.total_weight != total
            or self.earned_weight != earned
            or self.weighted_score != earned / total
            or self.outcome != expected_outcome
        ):
            raise ValueError("deterministic score metrics are inconsistent")
        return self


class BenchmarkAdjudicationFailure(StrictModel):
    category: Literal[
        "disabled",
        "ineligible",
        "case_limit",
        "timeout",
        "refusal",
        "invalid_result",
        "provider_error",
        "non_reproducible",
    ]
    message: str = Field(max_length=512)
    attempts: int = Field(ge=0)


class BenchmarkAdjudicationAttempt(StrictModel):
    """Bounded, content-safe provenance for one adjudicator invocation."""

    turn: int = Field(ge=1)
    attempt: int = Field(ge=1)
    retry: int = Field(ge=0)
    status: Literal[
        "completed", "timeout", "refusal", "invalid_result", "provider_error"
    ]
    latency_ms: int = Field(ge=0)
    outcome: Literal["supports_expected", "supports_actual", "uncertain"] | None = None
    reason: str | None = Field(default=None, max_length=2000)
    confidence: Decimal | None = Field(default=None, ge=0, le=1, strict=False)
    uncertainty: str | None = Field(default=None, max_length=1000)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    billed_cost: BilledCost | None = None

    @model_validator(mode="after")
    def require_completed_decision(self) -> "BenchmarkAdjudicationAttempt":
        decision_values = (
            self.outcome,
            self.reason,
            self.confidence,
            self.uncertainty,
        )
        if self.status == "completed" and any(
            value is None for value in decision_values
        ):
            raise ValueError("completed adjudication attempt requires a decision")
        if self.status != "completed" and any(
            value is not None for value in decision_values
        ):
            raise ValueError("failed adjudication attempt cannot contain a decision")
        return self


class BenchmarkAdjudicationResult(StrictModel):
    """Supplemental semantic evidence kept separate from deterministic truth."""

    rubric_version: Literal[1] = 1
    status: Literal["not_requested", "completed", "failed"]
    outcome: Literal["supports_expected", "supports_actual", "uncertain"] | None = None
    reason: str | None = Field(default=None, max_length=2000)
    confidence: Decimal | None = Field(default=None, ge=0, le=1, strict=False)
    uncertainty: str | None = Field(default=None, max_length=1000)
    prompt_id: str
    model: str
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    billed_cost: BilledCost | None = None
    attempts: list[BenchmarkAdjudicationAttempt] = Field(default_factory=list)
    failure: BenchmarkAdjudicationFailure | None = None

    @model_validator(mode="after")
    def require_consistent_status(self) -> "BenchmarkAdjudicationResult":
        completed_values = (
            self.outcome,
            self.reason,
            self.confidence,
            self.uncertainty,
        )
        if self.status == "completed":
            if (
                any(value is None for value in completed_values)
                or self.failure is not None
                or not self.attempts
            ):
                raise ValueError(
                    "completed adjudication requires attempts, a decision, and no failure"
                )
        elif (
            any(value is not None for value in completed_values) or self.failure is None
        ):
            raise ValueError(
                "non-completed adjudication requires a failure and no final decision"
            )
        if self.failure is not None and self.failure.attempts != len(self.attempts):
            raise ValueError("failure attempt count must match attempt provenance")
        return self


class BenchmarkScoringRecord(StrictModel):
    deterministic: BenchmarkDeterministicScore
    adjudication: BenchmarkAdjudicationResult | None = None


class BenchmarkAggregateScore(StrictModel):
    scoring_version: Literal[1] = 1
    profile_id: str
    requested_route: BenchmarkRoute
    scorer_id: str
    case_count: int = Field(ge=1)
    pass_count: int = Field(ge=0)
    partial_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    weighted_score: Decimal = Field(ge=0, le=1, strict=False)
    earned_weight: Decimal = Field(ge=0, strict=False)
    total_weight: Decimal = Field(gt=0, strict=False)

    @model_validator(mode="after")
    def require_consistent_counts(self) -> "BenchmarkAggregateScore":
        if self.pass_count + self.partial_count + self.fail_count != self.case_count:
            raise ValueError("aggregate outcome counts must equal case_count")
        if self.weighted_score != self.earned_weight / self.total_weight:
            raise ValueError("aggregate weighted score is inconsistent")
        return self


class BenchmarkCaseRun(StrictModel):
    schema_version: Literal[1] = 1
    run_id: str
    profile_id: str
    case_id: str
    target: BenchmarkTarget
    requested_route: BenchmarkRoute
    provider_usage: ProviderUsage | None = None
    started_at: datetime
    completed_at: datetime
    latency_ms: int = Field(ge=0)
    status: Literal["succeeded", "failed"]
    failure: BenchmarkFailure | None = None
    fixture_digest: str
    output: BenchmarkOutput | None = None
    scoring: list[BenchmarkScoringRecord] = Field(default_factory=list)


class BenchmarkExecutionResponse(StrictModel):
    schema_version: Literal[1] = 1
    runs: list[BenchmarkCaseRun]
    aggregates: list[BenchmarkAggregateScore] = Field(default_factory=list)


class ExecutionResult(StrictModel):
    output: Any
