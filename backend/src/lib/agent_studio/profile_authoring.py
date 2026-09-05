"""Typed profile edits compiled inside the shared Workshop proposal pipeline.

No persistence, authorization, proposal state or output-mode selection lives here.
The caller owns those existing Workshop boundaries and canonical validation.
"""

from copy import deepcopy
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.generic_extraction_profile import ProfileField
from src.schemas.profile_validator_mapping import ProfileValidatorMapping


class ProfileInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["current", "list_saved", "saved_revision", "validator_options", "preview"]
    profile_id: str | None = None
    revision: int | None = Field(default=None, ge=1)
    after: str | None = None


def inspect_workshop_profile(db, *, workshop, user_id, active_group_ids, request: ProfileInspection):
    """Read only the current local draft or explicitly authorized saved resources."""
    from src.lib.agent_studio import generic_profile_service
    from src.lib.agent_studio.authoring_context import workshop_draft_fingerprint
    from src.lib.agent_studio.profile_mapping_options import profile_mapping_options
    from src.schemas.generic_extraction_profile import GenericProfileContract

    if user_id is None or workshop is None:
        raise ValueError("Open an authenticated Workshop draft first")
    if request.action == "list_saved":
        profiles, cursor = generic_profile_service.list_profiles(
            db, user_id, after_id=UUID(request.after) if request.after else None,
        )
        return {"profiles": [{"profile_id": str(profile.id), "name": profile.name,
                              "semantic_class": profile.semantic_class, "head_revision": profile.head_revision}
                             for profile in profiles], "next_cursor": str(cursor) if cursor else None}
    if request.action == "saved_revision":
        revision = generic_profile_service.get_profile_revision(
            db, UUID(_required(request.profile_id, "profile_id")), _required(request.revision, "revision"), user_id,
            include_archived=True,
        )
        return {"profile_id": str(revision.profile_id), "profile_revision_id": str(revision.id),
                "revision": revision.revision, "fingerprint": revision.fingerprint, "contract": revision.contract,
                "output_resource_id": f"profile:{revision.profile_id}:{revision.revision}",
                "saved": True, "selected_in_draft": False}
    output = deepcopy(workshop.draft_output)
    fingerprint = workshop_draft_fingerprint(workshop)
    if request.action == "current":
        return {"draft_fingerprint": fingerprint, "output": output, "saved": False}
    if not output or output.get("mode") != "profile_bound_generic" or not output.get("profileContract"):
        raise ValueError("Choose a custom Output Structure and define its basics first")
    raw = deepcopy(output["profileContract"])
    if request.action == "validator_options":
        # Stale mappings must not prevent discovering valid replacements.
        raw["validator_mappings"] = []
        profile = GenericProfileContract.model_validate(raw)
        return {"draft_fingerprint": fingerprint, **profile_mapping_options(
            profile, active_group_ids=active_group_ids, after=request.after,
        )}
    profile = GenericProfileContract.model_validate(raw)
    return {"draft_fingerprint": fingerprint, "placeholder_data": True, "paper_evidence": False,
            "example_attributes": _example_value({"kind": "object", "fields": profile.model_dump(mode="json")["fields"]}),
            "semantic_class": profile.semantic_class, "saved": False}


def _example_value(schema):
    kind = schema["kind"]
    if kind == "object":
        return {field["key"]: _example_value(field["value_schema"]) for field in schema["fields"]}
    if kind == "array":
        return [_example_value(schema["items"])]
    if kind == "enum":
        return schema["values"][0]
    return {"string": "Example text", "integer": 1, "number": 1.5, "boolean": False}[kind]


class ProfileBasics(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    description: str
    semantic_class: str


class ProfileEdit(BaseModel):
    """Paths are canonical field keys, never aliases or arbitrary JSON pointers."""

    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal[
        "set_basics", "add_field", "replace_field", "remove_field", "reorder_fields",
        "set_source_labels", "set_mapping", "remove_mapping",
    ]
    field_path: list[str] = Field(default_factory=list)
    basics: ProfileBasics | None = None
    field: ProfileField | None = None
    field_order: list[str] | None = None
    source_labels: list[str] | None = None
    mapping: ProfileValidatorMapping | None = None
    mapping_id: str | None = None


def _required(value, name):
    if value is None:
        raise ValueError(f"Profile operation requires {name}")
    return value


def _children(contract, path):
    fields = contract.get("fields")
    def require_fields(value):
        if not isinstance(value, list) or any(not isinstance(field, dict) or not isinstance(field.get("key"), str) for field in value):
            raise ValueError("The selected container has invalid fields; repair its field definition first")
        return value
    fields = require_fields(fields)
    for key in path:
        field = next((field for field in fields if field["key"] == key), None)
        if field is None:
            raise ValueError("Profile field path does not exist")
        schema = field.get("value_schema")
        while isinstance(schema, dict) and schema.get("kind") == "array":
            schema = schema.get("items")
        if not isinstance(schema, dict) or schema.get("kind") != "object":
            raise ValueError("Child fields require an object or repeating group")
        fields = require_fields(schema.get("fields"))
    return fields


def apply_profile_edit(output, edit: ProfileEdit):
    """Return a new output draft; preserve its exact source pin and unrelated data."""
    if not output or output.get("mode") != "profile_bound_generic":
        raise ValueError("Select profile-bound generic output before editing its structure")
    result = deepcopy(output)
    contract = result.get("profileContract")
    if contract is not None and not isinstance(contract, dict):
        raise ValueError("The current profile draft must be an object")
    if contract is None:
        if edit.action != "set_basics":
            raise ValueError("Set profile basics before adding fields or mappings")
        contract = {"name": "", "description": "", "semantic_class": "", "fields": [], "validator_mappings": []}
        result["profileContract"] = contract
    if edit.action == "set_basics":
        contract.update(_required(edit.basics, "basics").model_dump(mode="json"))
    elif edit.action in {"set_mapping", "remove_mapping"}:
        mappings = contract.setdefault("validator_mappings", [])
        if not isinstance(mappings, list) or any(not isinstance(item, dict) or not isinstance(item.get("mapping_id"), str) for item in mappings):
            raise ValueError("The current validator mapping list is malformed")
        if edit.action == "set_mapping":
            mapping = _required(edit.mapping, "mapping").model_dump(mode="json", exclude_unset=True)
            existing = next((i for i, item in enumerate(mappings) if item["mapping_id"] == mapping["mapping_id"]), None)
            if existing is None:
                mappings.append(mapping)
            else:
                mappings[existing] = mapping
        else:
            mapping_id = _required(edit.mapping_id, "mapping_id")
            if not any(item["mapping_id"] == mapping_id for item in mappings):
                raise ValueError("Profile mapping does not exist")
            contract["validator_mappings"] = [item for item in mappings if item["mapping_id"] != mapping_id]
    elif edit.action in {"add_field", "reorder_fields"}:
        fields = _children(contract, edit.field_path)
        if edit.action == "add_field":
            fields.append(_required(edit.field, "field").model_dump(mode="json"))
        else:
            order = _required(edit.field_order, "field_order")
            by_key = {field["key"]: field for field in fields}
            if len(order) != len(fields) or len(set(order)) != len(order) or set(order) != set(by_key):
                raise ValueError("Field order must name every sibling exactly once")
            fields[:] = [by_key[key] for key in order]
    else:
        if not edit.field_path:
            raise ValueError("Choose a canonical field path")
        fields = _children(contract, edit.field_path[:-1])
        index = next((i for i, field in enumerate(fields) if field["key"] == edit.field_path[-1]), None)
        if index is None:
            raise ValueError("Profile field path does not exist")
        if edit.action == "remove_field":
            fields.pop(index)
        elif edit.action == "replace_field":
            fields[index] = _required(edit.field, "field").model_dump(mode="json")
        else:
            fields[index]["source_labels"] = list(_required(edit.source_labels, "source_labels"))
    # Cross-field collisions and stale mappings remain visible to the canonical
    # validator; renaming/removing a field never silently retargets a mapping.
    return result
