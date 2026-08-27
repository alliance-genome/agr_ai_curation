"""Authenticated chat route preference API contracts."""

from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRouteMode(str, Enum):
    AUTOMATIC = "automatic"
    AGENT = "agent"
    FLOW = "flow"


class ChatRoutePreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ChatRouteMode
    agent_id: str | None = Field(default=None, min_length=1, max_length=100)
    flow_id: UUID | None = None

    @model_validator(mode="after")
    def validate_mode_target(self) -> "ChatRoutePreferenceUpdate":
        valid = (
            self.mode == ChatRouteMode.AUTOMATIC
            and self.agent_id is None
            and self.flow_id is None
        ) or (
            self.mode == ChatRouteMode.AGENT
            and self.agent_id is not None
            and self.flow_id is None
        ) or (
            self.mode == ChatRouteMode.FLOW
            and self.agent_id is None
            and self.flow_id is not None
        )
        if not valid:
            raise ValueError("mode must identify exactly one matching chat route target")
        return self


class ChatRoutePickerTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["agent", "flow"]
    display_name: str
    description: str | None = None
    category: str | None = None
    available: bool


class ChatRoutePreferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ChatRouteMode
    agent_id: str | None = None
    flow_id: UUID | None = None
    status: Literal["available", "unavailable"]
    target: ChatRoutePickerTarget | None = None


class ChatRoutePickerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[ChatRoutePickerTarget]
