"""Targeted extractor proposals, distinct from source replacement or validation."""

import json
import math
from typing import Any, Literal, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.schemas.domain_envelope import validate_field_path_syntax

class FollowupContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class ProposedItemRelationshipShape(FollowupContract):
    """The extractor supplies identity semantics; indices only specify output order."""

    item_key: str = Field(min_length=1)
    relationship: Literal["retain", "revise", "merge", "split", "add"]
    predecessor_ids: list[str]
    reason: str = Field(min_length=1)


class ProposedItemRelationship(ProposedItemRelationshipShape):
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

RelationshipT = TypeVar("RelationshipT", bound=ProposedItemRelationshipShape)


class ProposedCollectionMappingShape(FollowupContract, Generic[RelationshipT]):
    collection_path: str = Field(min_length=1, description="Path to a list inside this record's payload, never the inventory of records.")
    source_collection_id: str | None = Field(description="Exact collection anchor from current_selected_entries metadata, or null for a new list.")
    items: list[RelationshipT]
    retired_item_ids: list[str] = Field(description="Existing nested item IDs in this collection; never a whole record ID.")
    retirement_reason: str


class ProposedCollectionMapping(ProposedCollectionMappingShape[ProposedItemRelationship]):

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

CollectionT = TypeVar("CollectionT", bound=ProposedCollectionMappingShape)


class ProposedSourceValueShape(FollowupContract, Generic[CollectionT]):
    field_path: str = Field(min_length=1)
    value: Any
    evidence_record_ids: list[str]
    collection_mappings: list[CollectionT] = Field(default_factory=list)


class ProposedSourceValue(ProposedSourceValueShape[ProposedCollectionMapping]):

    @model_validator(mode="after")
    def valid_path(self):
        validate_field_path_syntax(self.field_path)
        if len(set(self.evidence_record_ids)) != len(self.evidence_record_ids):
            raise ValueError("Evidence references must be distinct")
        return self

SourceValueT = TypeVar("SourceValueT", bound=ProposedSourceValueShape)


class FollowupEntryShape(FollowupContract, Generic[SourceValueT]):
    entry_id: str = Field(min_length=1)
    outcome: Literal[
        "additional_information", "proposed_correction", "contradictory_findings",
        "no_new_information", "unresolved", "propose_retirement",
    ]
    assessment: str = Field(min_length=1)
    remaining_uncertainty: str
    proposed_values: list[SourceValueT]
    evidence_record_ids: list[str]


class FollowupEntryResult(FollowupEntryShape[ProposedSourceValue]):

    @model_validator(mode="after")
    def distinct_paths(self):
        if self.outcome == "propose_retirement":
            if not self.assessment.strip() or self.proposed_values:
                raise ValueError("Whole-record retirement requires a reason and no field replacements")
        if len(self.evidence_record_ids) != len(set(self.evidence_record_ids)):
            raise ValueError("Evidence references must be distinct")
        paths = [item.field_path for item in self.proposed_values]
        if len(paths) != len(set(paths)):
            raise ValueError("Each proposed field must be named once")
        return self

EntryT = TypeVar("EntryT", bound=FollowupEntryShape)


class TargetedFollowupSubmission(FollowupContract, Generic[EntryT]):
    """Shape-valid response; per-entry structural checks belong to retention."""

    entries: list[EntryT] = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_entries(self):
        identifiers = [entry.entry_id for entry in self.entries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Each authorized entry requires one outcome")
        return self

class ProposedSourceValueTransport(FollowupContract):
    """Provider wire format; arbitrary domain values are decoded only at ingress."""

    field_path: str = Field(min_length=1)
    value_json: str = Field(description=(
        'One complete JSON value, encoded as text. For a string the text must include '
        'JSON quotes, e.g. "gene A"; for an object use {"name":"gene A"}; '
        'for a list use ["gene A",null,2]; booleans use true or false. '
        'Preserve the complete proposed value and its native types. '
        'No prose, Markdown fences, duplicate object keys, NaN or Infinity.'
    ))
    evidence_record_ids: list[str]
    collection_mappings: list[ProposedCollectionMappingShape]

class FollowupEntryTransport(FollowupContract):
    entry_id: str = Field(min_length=1)
    outcome: Literal[
        "additional_information", "proposed_correction", "contradictory_findings",
        "no_new_information", "unresolved", "propose_retirement",
    ]
    assessment: str = Field(min_length=1)
    remaining_uncertainty: str
    proposed_values: list[ProposedSourceValueTransport]
    evidence_record_ids: list[str]


def _decode_proposed_value(encoded: str):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object key")
            result[key] = value
        return result

    def finite_float(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Nonfinite JSON number")
        return parsed

    def reject_constant(_value):
        raise ValueError("Nonfinite JSON constant")

    try:
        return json.loads(encoded, object_pairs_hook=unique_object,
                          parse_float=finite_float, parse_constant=reject_constant)
    except ValueError:
        # Do not include curator text, object keys or JSONDecodeError.doc in reports.
        raise ValueError("Targeted follow-up value_json must contain one valid finite JSON value with unique object keys") from None

class TargetedFollowupTransport(FollowupContract):
    entries: list[FollowupEntryTransport] = Field(min_length=1)

    def decode(self) -> TargetedFollowupSubmission:
        """Convert provider encoding once; stored scientific values stay native."""
        result = self.model_dump(mode="json")
        for entry in result["entries"]:
            for proposed in entry["proposed_values"]:
                proposed["value"] = _decode_proposed_value(proposed.pop("value_json"))
        return TargetedFollowupSubmission.model_validate(result)
