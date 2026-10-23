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
        "accept",
        "reject",
        "defer",
        "retire",
        "restore",
        "revoke",
        "replace",
        "invalidate",
    ]
    reason: StrictStr = Field(min_length=1)
    subject: StrictStr | None = None
    replacement_original: dict[str, Any] | None = None
    replacement_target_id: StrictStr | None = None

    @model_validator(mode="after")
    def explicit_subject(self):
        if self.action in {"accept", "reject"}:
            if not self.subject:
                raise ValueError("Review decisions must identify an exact subject")
        elif self.subject is not None:
            raise ValueError("This lifecycle action does not select a review subject")
        if (self.action == "replace") != (self.replacement_original is not None):
            raise ValueError("Only replacement decisions supply new original content")
        if self.replacement_target_id is not None and self.action != "replace":
            raise ValueError("Only replacement can assign a replacement identity")
        return self


class DecisionUnit(HistoryContract):
    """One indivisible, explicitly declared group of result fields."""

    key: StrictStr = Field(min_length=1)
    fields: list[StrictStr] = Field(min_length=1)


class PrerequisiteSelection(HistoryContract):
    artifact_id: StrictStr = Field(min_length=1)
    target_id: StrictStr = Field(min_length=1)
    selection_id: StrictStr = Field(min_length=1)
    selection_generation: StrictInt = Field(ge=1)


class FrozenValidationInput(HistoryContract):
    target_id: StrictStr = Field(min_length=1)
    binding_id: StrictStr = Field(min_length=1)
    request: dict[str, Any]
    result_fields: list[StrictStr] = Field(default_factory=list)
    decision_units: list[DecisionUnit] = Field(default_factory=list)
    prerequisites: list[PrerequisiteSelection] = Field(default_factory=list)
    dispatch_wave: StrictInt | None = Field(default=None, ge=0)
    available_prior_completions: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete_partition(self):
        if len(set(self.result_fields)) != len(self.result_fields):
            raise ValueError("Result fields must be unique")
        if self.decision_units:
            keys = [unit.key for unit in self.decision_units]
            fields = [field for unit in self.decision_units for field in unit.fields]
            if len(set(keys)) != len(keys) or len(set(fields)) != len(fields):
                raise ValueError("Decision units must be disjoint and uniquely named")
            if set(fields) != set(self.result_fields):
                raise ValueError(
                    "Decision units must partition the complete result coverage"
                )
        return self


class ReviewObligation(HistoryContract):
    """A declared unit for this episode, including work that could not execute."""

    target_id: StrictStr = Field(min_length=1)
    mode: Literal["validation", "extraction_only", "curator"]
    binding_id: StrictStr | None = None

    @model_validator(mode="after")
    def binding_required(self):
        if (self.mode == "validation") != (self.binding_id is not None):
            raise ValueError("Only validation obligations name a binding")
        return self


class ReviewEpisode(HistoryContract):
    """Runtime declares coverage; the reader never infers it from successful calls."""

    episode_id: StrictStr = Field(min_length=1)
    obligations: list[ReviewObligation]
    unavailable_bindings: list[StrictStr] = Field(default_factory=list)
    reason: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def unique_targets(self):
        ids = [item.target_id for item in self.obligations]
        if len(ids) != len(set(ids)):
            raise ValueError("Review coverage must identify distinct decision units")
        return self
