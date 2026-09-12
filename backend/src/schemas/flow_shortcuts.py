"""The absence of a saved selection preserves the existing all-flows list."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FlowShortcutResponse(BaseModel):
    flow_ids: list[UUID] | None = None
    revision: int = 0


class FlowShortcutUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flow_ids: list[UUID]
    revision: int = Field(ge=0)

    @field_validator("flow_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Each flow can appear only once")
        return values
