"""Advisory capability facts; never an authorization or selection policy."""

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from src.schemas.domain_pack_metadata import (
    DomainPackMetadata,
    DomainPackObjectDefinition,
)

if TYPE_CHECKING:
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry


def registry_object_capabilities(
    registry: "DomainPackValidationRegistry",
    obj: DomainPackObjectDefinition,
    attachments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Count unique matching bindings, including pack-wide validators."""
    matches = [
        item
        for item in attachments
        if item.get("scope") == "pack" or item.get("object_type") == obj.object_type
    ]
    counts = {
        state: len(
            {
                item["validator_binding_id"]
                for item in matches
                if item.get("state") == state and item.get("validator_binding_id")
                and item.get("available") is not False
            }
        )
        for state in ("active", "under_development")
    }
    result = object_capabilities(
        registry.domain_pack.metadata,
        obj,
        active_validators=counts["active"],
        development_validators=counts["under_development"],
    )
    unavailable = len({item["validator_binding_id"] for item in matches
                       if item.get("available") is False and item.get("validator_binding_id")})
    if unavailable:
        result["validate"]["unavailable_bindings"] = unavailable
        if not counts["active"] and not counts["under_development"]:
            result["validate"]["state"] = "unavailable"
    return result


def object_capabilities(
    pack: DomainPackMetadata,
    obj: DomainPackObjectDefinition,
    *,
    active_validators: int,
    development_validators: int,
) -> dict[str, Any]:
    """Preserve independent source facts without inventing runtime readiness."""
    metadata = obj.metadata
    generic = metadata.get("generic_extraction", {})
    stageable = generic.get("stageable") if isinstance(generic, dict) else None
    model = next(
        (item for item in pack.model_definitions if item.model_id == obj.model_ref),
        None,
    )
    schema = obj.schema_ref or (model.schema_ref if model else None)
    result: dict[str, Any] = {
        "pack_state": pack.status.value,
        "definition_state": obj.definition_state.value,
        "schema_ref": schema.model_dump(mode="json", exclude_none=True)
        if schema
        else None,
        "extract": {
            "state": "available"
            if stageable is True
            else "unavailable"
            if stageable is False
            else "unspecified",
            "source": "object.metadata.generic_extraction.stageable",
            "reason": "Generic staging availability only; other extraction routes are not inferred.",
        },
        "schema": {
            "state": "referenced" if schema else "unspecified",
            "source": "object.schema_ref or model.schema_ref",
            "reason": "Schema reference is not a claim of full conformance or submission readiness."
            if schema
            else "No object/model schema reference declared.",
        },
        "validate": {
            "state": "active"
            if active_validators
            else "under_development"
            if development_validators
            else "none",
            "active_bindings": active_validators,
            "under_development_bindings": development_validators,
            "source": "matching validator bindings",
        },
        "review": {
            "state": "configured"
            if metadata.get("workspace_display")
            else "unspecified",
            "source": "object.metadata.workspace_display",
            "reason": "Display metadata does not guarantee adapter operations.",
        },
    }
    for operation in ("export", "write"):
        behavior = metadata.get(f"{operation}_behavior")
        result[operation] = {
            "state": behavior.get("status", "unspecified")
            if isinstance(behavior, dict)
            else "unspecified",
            "source": f"object.metadata.{operation}_behavior",
            "declared_behavior": deepcopy(behavior)
            if isinstance(behavior, dict)
            else None,
        }
    return result
