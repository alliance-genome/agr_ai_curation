"""Structural history contracts; these do not judge scientific answers."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class HistoryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HistoryActor(HistoryContract):
    type: Literal["curator", "agent", "system"]
    id: StrictStr = Field(min_length=1)


class HistoryDecision(HistoryContract):
    target_id: StrictStr = Field(min_length=1)
    expected_version: StrictInt = Field(ge=0)
    action: Literal[
        "accept", "reject", "defer", "retire", "restore", "revoke", "replace"
    ]
    reason: StrictStr
    subject: StrictStr | None = None
    replacement_original: dict[str, Any] | None = None

    @model_validator(mode="after")
    def explicit_subject(self):
        if self.action in {"accept", "reject"}:
            if not self.subject:
                raise ValueError("Review decisions must identify an exact subject")
        elif self.subject is not None:
            raise ValueError("This lifecycle action does not select a review subject")
        if (self.action == "replace") != (self.replacement_original is not None):
            raise ValueError("Only replacement decisions supply new original content")
        return self


class FrozenValidationInput(HistoryContract):
    target_id: StrictStr = Field(min_length=1)
    binding_id: StrictStr = Field(min_length=1)
    request: dict[str, Any]
