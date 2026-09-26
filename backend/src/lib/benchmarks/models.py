"""Versioned schemas shared by benchmark loading, API, CLI, and reporting."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
    model_serializer,
)

from src.schemas.agent_execution_revision import AgentExecutionReceipt
from .frozen_flow import FrozenBenchmarkFlow
from .system_snapshot import FrozenSystemAgent
from .supervisor_snapshot import FrozenFlowSupervisor
from .dependencies import BenchmarkDependencies
from .stage_definitions import StageDefinition, freeze_stage_definitions

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
    source_kind: Literal["saved_flow"] | None = None
    source_revision: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_saved_selection(self):
        if self.source_kind == "saved_flow":
            if self.kind != "flow" or self.source_revision is None:
                raise ValueError("Saved flow selection requires a flow and its discovered revision")
            UUID(self.id)
        elif self.source_revision is not None:
            raise ValueError("Source revision requires an explicit saved-flow selection")
        return self

    @model_serializer(mode="wrap")
    def serialize_selection(self, handler):
        result = handler(self)
        if self.source_kind is None:
            result.pop("source_kind", None)
            result.pop("source_revision", None)
        return result


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


class BenchmarkSourceRevisions(FrozenStrictModel):
    """Saved executable origins, separate from the experiment's model routes."""

    source_execution_receipts: Mapping[str, AgentExecutionReceipt] = Field(default_factory=dict)
    flow_snapshot: FrozenBenchmarkFlow | None = None
    supervisor_snapshot: FrozenFlowSupervisor | None = None
    system_agent_snapshots: Mapping[str, FrozenSystemAgent] = Field(default_factory=dict)
    dependencies: "BenchmarkDependencies | None" = None
    direct_stage_definitions: Mapping[str, StageDefinition] = Field(default_factory=dict)

    @field_validator("direct_stage_definitions")
    @classmethod
    def freeze_direct_stages(cls, value):
        return freeze_stage_definitions(value)

    @field_serializer("direct_stage_definitions")
    def serialize_direct_stages(self, value):
        return {key: stage.model_dump(mode="json") for key, stage in value.items()}

    @field_validator("system_agent_snapshots")
    @classmethod
    def freeze_system_sources(cls, value):
        if any(key != snapshot.agent_key for key, snapshot in value.items()):
            raise ValueError("System source key does not match its snapshot")
        return MappingProxyType(dict(value))

    @field_serializer("system_agent_snapshots")
    def serialize_system_sources(self, value):
        return {key: snapshot.model_dump(mode="json") for key, snapshot in value.items()}

    @field_validator("source_execution_receipts")
    @classmethod
    def freeze_sources(cls, value: Mapping[str, AgentExecutionReceipt]) -> Mapping[str, AgentExecutionReceipt]:
        for slot, receipt in value.items():
            if not receipt.agent_key.startswith("ca_") or not slot.startswith(("agent:", "validator:")):
                raise ValueError("Benchmark source revisions require a custom agent and model slot")
            if slot.startswith("agent:") and slot != f"agent:{receipt.agent_key}":
                raise ValueError("Benchmark source revision does not match its agent slot")
        return MappingProxyType(dict(value))

    @field_serializer("source_execution_receipts")
    def serialize_sources(self, value: Mapping[str, AgentExecutionReceipt]) -> dict:
        return dict(value)

    @model_serializer(mode="wrap")
    def serialize_with_sources(self, handler):
        result = handler(self)
        # System plans never had custom origins: preserve their durable bytes
        # and digests rather than inventing empty provenance for historical work.
        if not self.source_execution_receipts:
            result.pop("source_execution_receipts", None)
        if self.flow_snapshot is None:
            result.pop("flow_snapshot", None)
        if self.supervisor_snapshot is None:
            result.pop("supervisor_snapshot", None)
        if not self.system_agent_snapshots:
            result.pop("system_agent_snapshots", None)
        if self.dependencies is None:
            result.pop("dependencies", None)
        if not self.direct_stage_definitions:
            result.pop("direct_stage_definitions", None)
        return result


def _validate_flow_source(
    target: BenchmarkExecutionTarget, snapshot: FrozenBenchmarkFlow | None,
) -> None:
    if snapshot is None:
        if target.source_kind == "saved_flow":
            raise ValueError("Selected saved flow requires its frozen source")
        return
    if target.kind != "flow" or target.id != snapshot.source_id:
        raise ValueError("Frozen flow source does not match its target")
    if snapshot.source_kind == "saved_flow":
        if target.source_kind != "saved_flow" or target.source_revision != snapshot.source_revision:
            raise ValueError("Frozen saved flow revision does not match its selection")
    elif target.source_kind is not None:
        raise ValueError("Recipe snapshot cannot satisfy a saved flow selection")


class BenchmarkTargetCatalogEntry(BenchmarkSourceRevisions):
    target: BenchmarkExecutionTarget
    route_slots: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_slots(self) -> "BenchmarkTargetCatalogEntry":
        _validate_flow_source(self.target, self.flow_snapshot)
        if len(self.route_slots) != len(set(self.route_slots)):
            raise ValueError("target route slots must not contain duplicates")
        if set(self.source_execution_receipts) - set(self.route_slots):
            raise ValueError("Source revision references an unused target slot")
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


class ResolvedBenchmarkCell(BenchmarkSourceRevisions):
    cell_id: str
    case_id: str
    configuration_id: str
    repetition: int = Field(ge=1)
    target: BenchmarkExecutionTarget
    input: BenchmarkInputReference
    user_query: str | None = Field(default=None, min_length=1)
    routes: Mapping[str, BenchmarkSuiteRoute]

    @model_validator(mode="after")
    def require_source_slots(self) -> "ResolvedBenchmarkCell":
        _validate_flow_source(self.target, self.flow_snapshot)
        if set(self.source_execution_receipts) - set(self.routes):
            raise ValueError("Source revision references an unused cell slot")
        return self

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
    stage_execution_id: UUID | None = Field(default=None, strict=False)
    parent_invocation_sequence: int | None = Field(default=None, ge=1)
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

    @model_serializer(mode="wrap")
    def serialize_attribution(self, handler):
        value = handler(self)
        # Historical unknown attribution is not a fabricated measurement.
        for key in ("stage_execution_id", "parent_invocation_sequence"):
            if value.get(key) is None:
                value.pop(key, None)
        return value


class BenchmarkCellExecutionResult(StrictModel):
    """Transient worker outcome; persistence replaces usage with ledger references."""

    output: Any
    invocations: list[ProviderUsage]
