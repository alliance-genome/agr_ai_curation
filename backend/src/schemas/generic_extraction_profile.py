"""Closed, provider-neutral custom generic output contracts.

One normalizer is used by API parsing and persistence. This defines the
contract, not extraction-time record conformance (owned by the runtime).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.lib.openai_agents.config import (
    get_generic_profile_max_contract_bytes,
    get_generic_profile_max_depth,
    get_generic_profile_max_fields,
)


RESERVED_PROFILE_KEYS = frozenset(
    {
        "label",
        "class_key",
        "semantic_class",
        "object_type",
        "object_id",
        "pending_ref_id",
        "evidence",
        "evidence_ids",
        "evidence_refs",
        "provenance",
        "provenance_ids",
        "envelope_id",
        "metadata",
        "payload",
        "attributes",
    }
)


def canonical_key(value: str) -> str:
    """Normalize user-entered keys without accepting paths or executable syntax."""
    result = re.sub(
        r"[\s-]+", "_", unicodedata.normalize("NFKC", value).strip().lower()
    )
    if not re.fullmatch(r"[a-z][a-z0-9_]*", result):
        raise ValueError(
            "Use a key starting with a letter, then letters, digits or underscores"
        )
    return result


def _label_token(value: str) -> str:
    return re.sub(
        r"[\s_-]+", "_", unicodedata.normalize("NFKC", value).strip().casefold()
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScalarValueSchema(ClosedModel):
    kind: Literal["string", "integer", "number", "boolean"]


class EnumValueSchema(ClosedModel):
    kind: Literal["enum"]
    values: list[str]

    @field_validator("values")
    @classmethod
    def valid_values(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if not normalized or any(not value for value in normalized):
            raise ValueError("Provide at least one non-empty choice")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Choices must be unique")
        return normalized


class ObjectValueSchema(ClosedModel):
    kind: Literal["object"]
    fields: list[ProfileField]


class ArrayValueSchema(ClosedModel):
    kind: Literal["array"]
    items: ValueSchema


ValueSchema = Annotated[
    Union[ScalarValueSchema, EnumValueSchema, ObjectValueSchema, ArrayValueSchema],
    Field(discriminator="kind"),
]


class ProfileField(ClosedModel):
    key: str
    display_name: str = ""
    description: str = ""
    required: bool = False
    nullable: bool = False
    source_labels: list[str] = Field(default_factory=list)
    value_schema: ValueSchema

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        key = canonical_key(value)
        if key in RESERVED_PROFILE_KEYS:
            raise ValueError(
                "This key is owned by the platform, not a custom attribute"
            )
        return key

    @field_validator("display_name", "description")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_labels")
    @classmethod
    def valid_source_labels(cls, values: list[str]) -> list[str]:
        labels = [
            " ".join(unicodedata.normalize("NFKC", value).strip().split())
            for value in values
        ]
        tokens = [_label_token(value) for value in labels]
        if any(not value for value in tokens):
            raise ValueError("Source labels must not be empty")
        if len(set(tokens)) != len(tokens):
            raise ValueError("Source labels must be unique after normalization")
        if any(value in RESERVED_PROFILE_KEYS for value in tokens):
            raise ValueError("Source labels cannot name platform-owned fields")
        return labels


class GenericProfileContract(ClosedModel):
    """Immutable revision payload; display metadata is revisioned with semantics."""

    contract_version: Literal[1] = 1
    name: str
    description: str = ""
    semantic_class: str
    fields: list[ProfileField]
    validator_mappings: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("name", "semantic_class")
    @classmethod
    def non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Must not be empty")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="before")
    @classmethod
    def bounded_input(cls, value: Any) -> Any:
        if isinstance(value, cls):
            value = value.model_dump(mode="json")
        if not isinstance(value, dict):
            return value
        try:
            serialized = canonical_json(value)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError("Profile must be a finite JSON contract") from exc
        if len(serialized.encode("utf-8")) > get_generic_profile_max_contract_bytes():
            raise ValueError("Profile exceeds GENERIC_PROFILE_MAX_CONTRACT_BYTES")
        pending = [(value.get("fields"), "fields", 1)]
        field_count = 0
        max_depth = get_generic_profile_max_depth()
        while pending:
            node, path, depth = pending.pop()
            if depth > max_depth:
                raise ValueError(f"{path}: exceeds GENERIC_PROFILE_MAX_DEPTH")
            if isinstance(node, list):
                field_count += len(node)
                if field_count > get_generic_profile_max_fields():
                    raise ValueError(f"{path}: exceeds GENERIC_PROFILE_MAX_FIELDS")
                for index, field in enumerate(node):
                    if isinstance(field, dict):
                        pending.append(
                            (
                                field.get("value_schema"),
                                f"{path}[{index}].value_schema",
                                depth,
                            )
                        )
            elif isinstance(node, dict):
                if node.get("kind") == "object":
                    pending.append((node.get("fields"), f"{path}.fields", depth + 1))
                elif node.get("kind") == "array":
                    pending.append((node.get("items"), f"{path}.items", depth + 1))
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> GenericProfileContract:
        # ALL-1037 supplies typed mappings; accepting partial JSON now would
        # create unvalidated immutable semantics that later code must guess at.
        if self.validator_mappings:
            raise ValueError("validator_mappings: semantic mappings are not enabled")
        for path, fields in _object_fields(self.fields):
            names = {field.key for field in fields}
            if len(names) != len(fields):
                raise ValueError(f"{path}: canonical field keys must be unique")
            aliases: dict[str, str] = {}
            for index, field in enumerate(fields):
                for label in field.source_labels:
                    token = _label_token(label)
                    other = aliases.get(token)
                    if (token in names and token != field.key) or (
                        other is not None and other != field.key
                    ):
                        raise ValueError(
                            f"{path}[{index}].source_labels: label identifies another canonical field"
                        )
                    aliases[token] = field.key
        if (
            len(canonical_json(self.model_dump(mode="json")).encode("utf-8"))
            > get_generic_profile_max_contract_bytes()
        ):
            raise ValueError(
                "Profile exceeds GENERIC_PROFILE_MAX_CONTRACT_BYTES after normalization"
            )
        return self

    def fingerprint(self) -> str:
        return (
            "sha256:"
            + hashlib.sha256(
                canonical_json(self.model_dump(mode="json")).encode("utf-8")
            ).hexdigest()
        )


def _object_fields(fields: list[ProfileField], path: str = "fields"):
    """Visit object-local field namespaces, including objects inside arrays."""
    yield path, fields
    for index, field in enumerate(fields):
        schema = field.value_schema
        child_path = f"{path}[{index}].value_schema"
        while isinstance(schema, ArrayValueSchema):
            schema = schema.items
            child_path += ".items"
        if isinstance(schema, ObjectValueSchema):
            yield from _object_fields(schema.fields, child_path + ".fields")


ProfileField.model_rebuild()
ObjectValueSchema.model_rebuild()
ArrayValueSchema.model_rebuild()
GenericProfileContract.model_rebuild()


def normalize_profile_contract(value: Any) -> GenericProfileContract:
    """Validate even an existing model anew; never trust caller mutation."""
    if isinstance(value, GenericProfileContract):
        value = value.model_dump(mode="json")
    return GenericProfileContract.model_validate(value)
