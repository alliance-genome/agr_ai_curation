"""Versioned schemas shared by benchmark loading, API, CLI, and reporting."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BenchmarkTarget(StrictModel):
    kind: Literal["agent", "flow"]
    id: str = Field(min_length=1, max_length=255)


class BenchmarkRoute(StrictModel):
    provider: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN)
    model: str = Field(min_length=1, max_length=255, pattern=_IDENTIFIER_PATTERN)


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


class BenchmarkExecutionResponse(StrictModel):
    schema_version: Literal[1] = 1
    runs: list[BenchmarkCaseRun]


class ExecutionResult(StrictModel):
    output: Any
    provider_usage: ProviderUsage | None = None
