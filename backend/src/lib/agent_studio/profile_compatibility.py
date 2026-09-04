"""Deterministic revision differences for closed profile producer compatibility."""

from typing import Any

from src.schemas.generic_extraction_profile import (
    ArrayValueSchema,
    EnumValueSchema,
    GenericProfileContract,
    ObjectValueSchema,
    ProfileField,
    normalize_profile_contract,
)


def profile_compatibility(
    previous: GenericProfileContract | dict[str, Any],
    proposed: GenericProfileContract | dict[str, Any],
) -> list[dict[str, Any]]:
    """Describe whether existing records/producers fit the proposed contract.

    Findings do not retarget any consumer and are not permission to execute.
    Removing a field breaks older records because the new object is closed.
    """
    old = normalize_profile_contract(previous)
    new = normalize_profile_contract(proposed)
    findings: list[dict[str, Any]] = []

    def add(path: str, code: str, breaking: bool, before: Any, after: Any) -> None:
        findings.append(
            {
                "path": path,
                "code": code,
                "breaking": breaking,
                "before": before,
                "after": after,
            }
        )

    def fields_diff(
        before: list[ProfileField], after: list[ProfileField], path: str
    ) -> None:
        old_fields = {field.key: field for field in before}
        new_fields = {field.key: field for field in after}
        if (
            list(old_fields) != list(new_fields)
            and old_fields.keys() == new_fields.keys()
        ):
            add(path, "field_order_changed", False, list(old_fields), list(new_fields))
        for key in sorted(old_fields.keys() | new_fields.keys()):
            field_path = f"{path}.{key}"
            left, right = old_fields.get(key), new_fields.get(key)
            if left is None:
                assert right is not None  # key came from the union of both maps
                add(
                    field_path,
                    "field_added",
                    right.required,
                    None,
                    right.model_dump(mode="json"),
                )
                continue
            if right is None:
                add(
                    field_path,
                    "field_removed",
                    True,
                    left.model_dump(mode="json"),
                    None,
                )
                continue
            for attr in (
                "display_name",
                "description",
                "source_labels",
                "required",
                "nullable",
            ):
                a, b = getattr(left, attr), getattr(right, attr)
                if a != b:
                    breaking = (attr == "required" and b) or (
                        attr == "nullable" and not b
                    )
                    add(field_path, f"{attr}_changed", bool(breaking), a, b)
            schema_diff(left.value_schema, right.value_schema, field_path)

    def schema_diff(before, after, path: str) -> None:
        if before.kind != after.kind:
            add(path, "value_kind_changed", True, before.kind, after.kind)
        elif isinstance(before, EnumValueSchema):
            if before.values != after.values:
                add(
                    path,
                    "enum_choices_changed",
                    not set(before.values) <= set(after.values),
                    before.values,
                    after.values,
                )
        elif isinstance(before, ArrayValueSchema):
            schema_diff(before.items, after.items, path + "[]")
        elif isinstance(before, ObjectValueSchema):
            fields_diff(before.fields, after.fields, path)

    for attr in ("name", "description", "semantic_class", "validator_mappings"):
        before, after = getattr(old, attr), getattr(new, attr)
        if attr == "validator_mappings":
            before = [mapping.model_dump(mode="json") for mapping in before]
            after = [mapping.model_dump(mode="json") for mapping in after]
        if before != after:
            add(attr, f"{attr}_changed", attr == "semantic_class", before, after)
    fields_diff(old.fields, new.fields, "attributes")
    return sorted(findings, key=lambda item: (item["path"], item["code"]))
