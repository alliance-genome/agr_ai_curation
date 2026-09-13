"""Deeply immutable flow selection carried by execution plans.

This captures flow syntax, not authorization or a certification that all runtime
dependencies have been frozen. Admission owns that verification separately.
"""

from collections.abc import Mapping
import math
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator, model_serializer

from .stage_definitions import StageDefinition, freeze_stage_definitions


def freeze_json(value: Any) -> Any:
    """Detach JSON containers and reject non-JSON values rather than stringify."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Frozen JSON object keys must be strings")
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("Frozen execution data must contain only finite JSON values")


def thaw_json(value: Any) -> Any:
    """Return an independent JSON tree for serialization or normal runtime use."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


class FrozenBenchmarkFlow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_kind: Literal["saved_flow", "recipe"]
    source_id: str = Field(min_length=1)
    source_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    title: str = Field(min_length=1)
    description: str | None
    definition: Mapping[str, Any]
    output_contracts: Mapping[str, Any] = Field(default_factory=dict)
    stage_definitions: Mapping[str, StageDefinition] = Field(default_factory=dict)

    @field_validator("stage_definitions")
    @classmethod
    def freeze_stages(cls, value):
        return freeze_stage_definitions(value)

    @field_serializer("stage_definitions")
    def serialize_stages(self, value):
        return dict(value)

    @model_serializer(mode="wrap")
    def serialize_known_stages(self, handler):
        value = handler(self)
        if not self.stage_definitions:
            value.pop("stage_definitions", None)
        return value

    @field_validator("output_contracts")
    @classmethod
    def freeze_contracts(cls, value):
        return freeze_json(value)

    @field_serializer("output_contracts")
    def serialize_contracts(self, value):
        return thaw_json(value)

    @field_validator("definition")
    @classmethod
    def validate_and_freeze_definition(cls, value):
        from src.schemas.flows import FlowDefinition

        # Validate using the normal flow schema, including its configured limits.
        definition = FlowDefinition.model_validate(thaw_json(freeze_json(value)))
        return freeze_json(definition.model_dump(mode="json"))

    @model_validator(mode="after")
    def validate_source_identity(self):
        if self.source_kind == "saved_flow":
            UUID(self.source_id)
        return self

    @field_serializer("definition")
    def serialize_definition(self, value):
        return thaw_json(value)
