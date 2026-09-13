"""Frozen semantic stage identity, independent of model-route coupling."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .stage_measurements import StageIdentity

StageRole = Literal["extraction", "validation", "output", "supervisor", "other"]
_CATEGORY_ROLES: dict[str, StageRole] = {
    "Extraction": "extraction", "Validation": "validation", "Output": "output",
}


def role_from_category(category: str | None) -> StageRole:
    return _CATEGORY_ROLES.get(category or "", "other")


class StageDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    stage_id: str = Field(min_length=1)
    role: StageRole
    node_id: str | None = None
    source_node_id: str | None = None
    binding_id: str | None = None
    agent_id: str | None = None

    def identity(self) -> StageIdentity:
        return StageIdentity(**self.model_dump())


def definitions_from_flow_stages(stages) -> dict[str, StageDefinition]:
    """Consume the authorized graph/metadata projection, never route prefixes."""
    return {stage.stage_id: StageDefinition(
        stage_id=stage.stage_id, role=stage.role, node_id=stage.node_id,
        source_node_id=stage.source_node_id, binding_id=stage.binding_id,
        agent_id=stage.agent_id,
    ) for stage in stages}


def freeze_stage_definitions(value: Mapping[str, StageDefinition]) -> Mapping[str, StageDefinition]:
    if any(key != definition.stage_id for key, definition in value.items()):
        raise ValueError("Stage definition key differs from its identity")
    return MappingProxyType(dict(value))
