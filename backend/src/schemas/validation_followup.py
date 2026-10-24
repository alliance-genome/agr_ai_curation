"""Targeted extractor proposals, distinct from source replacement or validation."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.domain_envelope import validate_field_path_syntax


class FollowupContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProposedItemRelationship(FollowupContract):
    """The extractor supplies identity semantics; indices only specify output order."""

    item_key: str = Field(min_length=1)
    relationship: Literal["retain", "revise", "merge", "split", "add"]
    predecessor_ids: list[str]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_cardinality(self):
        if len(self.predecessor_ids) != len(set(self.predecessor_ids)):
            raise ValueError("An item cannot repeat a predecessor")
        size = len(self.predecessor_ids)
        if (self.relationship == "add" and size != 0
            or self.relationship in {"retain", "revise", "split"} and size != 1
            or self.relationship == "merge" and size < 2):
            raise ValueError("Item relationship has incompatible predecessor coverage")
        return self


class ProposedCollectionMapping(FollowupContract):
    collection_path: str = Field(min_length=1)
    source_collection_id: str | None
    items: list[ProposedItemRelationship]
    retired_item_ids: list[str]
    retirement_reason: str

    @model_validator(mode="after")
    def unambiguous_mapping(self):
        validate_field_path_syntax(self.collection_path)
        keys = [item.item_key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("Output item keys must be unique within a collection")
        if len(self.retired_item_ids) != len(set(self.retired_item_ids)):
            raise ValueError("Retired item IDs must be unique")
        if self.retired_item_ids and not self.retirement_reason.strip():
            raise ValueError("Explicit retirement requires a reason")
        used = {item for relation in self.items for item in relation.predecessor_ids}
        if used.intersection(self.retired_item_ids):
            raise ValueError("A predecessor cannot be both mapped and retired")
        if self.source_collection_id is None and (used or self.retired_item_ids):
            raise ValueError("A new collection cannot refer to old items")
        return self


class ProposedSourceValue(FollowupContract):
    field_path: str = Field(min_length=1)
    value: Any
    evidence_record_ids: list[str]
    collection_mappings: list[ProposedCollectionMapping] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_path(self):
        validate_field_path_syntax(self.field_path)
        if len(set(self.evidence_record_ids)) != len(self.evidence_record_ids):
            raise ValueError("Evidence references must be distinct")
        return self


class FollowupEntryResult(FollowupContract):
    entry_id: str = Field(min_length=1)
    outcome: Literal[
        "additional_information", "proposed_correction", "contradictory_findings",
        "no_new_information", "unresolved",
    ]
    assessment: str = Field(min_length=1)
    remaining_uncertainty: str
    proposed_values: list[ProposedSourceValue]
    evidence_record_ids: list[str]

    @model_validator(mode="after")
    def distinct_paths(self):
        paths = [item.field_path for item in self.proposed_values]
        if len(paths) != len(set(paths)):
            raise ValueError("Each proposed field must be named once")
        return self


class TargetedFollowupResult(FollowupContract):
    entries: list[FollowupEntryResult] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_entries(self):
        identifiers = [entry.entry_id for entry in self.entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Each authorized entry requires one outcome")
        return self
