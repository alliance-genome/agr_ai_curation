"""Versioned schemas shared by benchmark loading, API, CLI, and reporting."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FrozenStrictModel(BaseModel):
    """Immutable strict input and planning contracts for benchmark suite v2."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _reject_network_reference(value: str) -> str:
    if "://" in value:
        raise ValueError("benchmark input references must not be network URLs")
    return value


BenchmarkOpaqueReference = Annotated[
    str, Field(min_length=1, max_length=1024), AfterValidator(_reject_network_reference),
]


class BenchmarkInputReference(FrozenStrictModel):
    """Typed immutable provenance; resolver behavior is owned by ALL-979."""

    resolver: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    reference: BenchmarkOpaqueReference
    version: str = Field(min_length=1, max_length=255)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

class BenchmarkExecutionTarget(FrozenStrictModel):
    """Immutable agent or flow target used by suite v2 and resolved plans."""

    kind: Literal["agent", "flow"]
    id: str = Field(min_length=1, max_length=255)


class BenchmarkSuiteCase(FrozenStrictModel):
    case_id: str = Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
    target: BenchmarkExecutionTarget
    input: BenchmarkInputReference
    user_query: str | None = Field(default=None, min_length=1)


class BenchmarkSuiteRoute(FrozenStrictModel):
    """Immutable per-slot route used only by suite v2 catalogs and plans."""

    provider: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER_PATTERN)
    model: str = Field(min_length=1, max_length=255, pattern=_IDENTIFIER_PATTERN)
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None


class BenchmarkConfiguration(FrozenStrictModel):
    configuration_id: str = Field(
        min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN
    )
    routes: Mapping[str, BenchmarkSuiteRoute] = Field(default_factory=dict)

    @field_validator("routes")
    @classmethod
    def require_route_slot_names(
        cls, value: Mapping[str, BenchmarkSuiteRoute]
    ) -> Mapping[str, BenchmarkSuiteRoute]:
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
        return MappingProxyType(dict(value))

    @field_serializer("routes")
    def serialize_routes(
        self, value: Mapping[str, BenchmarkSuiteRoute]
    ) -> dict[str, BenchmarkSuiteRoute]:
        return dict(value)


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
    default_route: BenchmarkSuiteRoute

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
    target: BenchmarkExecutionTarget
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
    target: BenchmarkExecutionTarget
    input: BenchmarkInputReference
    user_query: str | None = Field(default=None, min_length=1)


class ResolvedBenchmarkCell(FrozenStrictModel):
    cell_id: str
    case_id: str
    configuration_id: str
    repetition: int = Field(ge=1)
    target: BenchmarkExecutionTarget
    input: BenchmarkInputReference
    user_query: str | None = Field(default=None, min_length=1)
    routes: Mapping[str, BenchmarkSuiteRoute]

    @field_validator("routes")
    @classmethod
    def freeze_routes(
        cls, value: Mapping[str, BenchmarkSuiteRoute]
    ) -> Mapping[str, BenchmarkSuiteRoute]:
        return MappingProxyType(dict(value))

    @field_serializer("routes")
    def serialize_routes(
        self, value: Mapping[str, BenchmarkSuiteRoute]
    ) -> dict[str, BenchmarkSuiteRoute]:
        return dict(value)


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


class BilledCost(StrictModel):
    amount: Decimal = Field(ge=0, strict=False)
    unit: str
    source: str


class ProviderUsage(StrictModel):
    route_slot: str | None = None
    requested_provider: str
    requested_model: str
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    actual_provider: str | None = None
    actual_model: str | None = None
    routing_attempt: int | None = Field(default=None, ge=0)
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    billed_cost: BilledCost | None = None
    sequence: int | None = Field(default=None, ge=1)
    status: Literal["completed", "failed"] = "completed"
    failure_detail: str | None = None


class BenchmarkCellExecutionResult(StrictModel):
    """Ephemeral worker handoff for one resolved suite-v2 cell."""

    output: Any
    invocations: list[ProviderUsage]
