"""Declared generic-profile paths and lossless projection values.

Schema paths use [] for array traversal; runtime rows retain a list at every
traversal, including None placeholders. No observed-value/key normalization or
flattening is involved, so paired paths always share the source indices.
"""
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.schemas.generic_extraction_profile import GenericProfileContract, ProfileField, ValueSchema


@dataclass(frozen=True)
class ProfileProjectionField:
    profile_path: str
    row_ref: str
    label: str
    schema_kind: str
    value_type: str
    array_depth: int
    required: bool
    nullable: bool
    enum_values: tuple[str, ...]
    tokens: tuple[str | None, ...]

    def value_from(self, attributes: dict[str, Any]) -> Any:
        def read(value: Any, tokens: tuple[str | None, ...]) -> Any:
            if not tokens:
                return deepcopy(value)
            key, *rest = tokens
            if key is None:
                if not isinstance(value, list):
                    return None
                return [read(item, tuple(rest)) for item in value]
            if not isinstance(value, dict) or key not in value:
                return None
            return read(value[key], tuple(rest))

        return read(attributes, self.tokens)


def profile_projection_fields(contract: GenericProfileContract) -> list[ProfileProjectionField]:
    """Discover fields from the immutable schema, even with zero result rows."""
    result: list[ProfileProjectionField] = []

    def visit(
        schema: ValueSchema, path: str, tokens: tuple[str | None, ...],
        *, label: str, required: bool, nullable: bool, array_depth: int,
    ) -> None:
        value_type = "list" if array_depth or schema.kind == "array" else (
            "string" if schema.kind == "enum" else schema.kind
        )
        result.append(ProfileProjectionField(
            profile_path=f"attributes.{path}", row_ref=f"object.attribute.{path}",
            label=label, schema_kind=schema.kind, value_type=value_type,
            array_depth=array_depth, required=required, nullable=nullable,
            enum_values=tuple(schema.values) if schema.kind == "enum" else (), tokens=tokens,
        ))
        if schema.kind == "object":
            for field in schema.fields:
                visit_field(field, f"{path}.{field.key}", (*tokens, field.key), array_depth)
        elif schema.kind == "array":
            if schema.items.kind == "object":
                for field in schema.items.fields:
                    visit_field(field, f"{path}[].{field.key}", (*tokens, None, field.key), array_depth + 1)
            elif schema.items.kind == "array":
                visit(schema.items, f"{path}[]", (*tokens, None), label=label,
                      required=required, nullable=False, array_depth=array_depth + 1)

    def visit_field(field: ProfileField, path: str, tokens: tuple[str | None, ...], depth: int) -> None:
        visit(field.value_schema, path, tokens, label=field.display_name or field.key.replace("_", " ").title(),
              required=field.required, nullable=field.nullable, array_depth=depth)

    for field in contract.fields:
        visit_field(field, field.key, (field.key,), 0)
    return result
