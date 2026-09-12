"""Declared export fields shared by Studio authoring and runtime serialization."""
from copy import deepcopy
import hashlib
import json
from typing import Any

from src.schemas.domain_envelope import parse_field_path


COMMON_FIELDS = [
    {"ref": "object.label", "label": "Item label", "value_type": "string"},
    {"ref": "object.object_type", "label": "Item type", "value_type": "string"},
    {"ref": "object.evidence_record_ids", "label": "Supporting evidence IDs", "value_type": "list"},
    {"ref": "object.validation_status", "label": "Validation status", "value_type": "string"},
    {"ref": "artifact.agent_name", "label": "Source agent", "value_type": "string"},
]


def packaged_export_fields(agent_id: str, entry: dict | None = None) -> list[dict[str, Any]]:
    from src.lib.config.agent_loader import canonical_system_agent_key, list_agents
    from src.lib.flows.validation_attachments import domain_pack_validation_registries

    if entry is not None:
        pack_id = (entry.get("curation") or {}).get("domain_pack_id")
    else:
        # Flows store the public system key, which need not equal agent.yaml's
        # definition ID (e.g. gene_expression vs gene_expression_extraction).
        matches = [
            agent for agent in list_agents()
            if agent_id in {canonical_system_agent_key(agent), agent.agent_id}
        ]
        if len(matches) > 1:
            raise ValueError(f"Ambiguous packaged export source: {agent_id}")
        definition = matches[0] if matches else None
        pack_id = definition.curation.domain_pack_id if definition else None
    if not pack_id or pack_id == "generic":
        return []
    registry = domain_pack_validation_registries().get(pack_id)
    if registry is None:
        return []
    result = []
    for obj in registry.domain_pack.metadata.object_definitions:
        for field in obj.fields:
            result.append({
                "ref": f"object.pack.{obj.object_type}.{field.field_path}",
                "label": field.display_name or field.field_path.replace("_", " "),
                "group": obj.display_name, "object_type": obj.object_type,
                "payload_path": field.field_path, "pack_version": registry.domain_pack.metadata.version,
                "value_type": "list" if field.multivalued else field.field_type.value,
                "schema_kind": field.field_type.value,
                "required": field.required, "nullable": not field.required,
                "array_depth": int(field.multivalued),
                "description": field.description,
            })
    return result


def source_catalog(fields: list[dict], receipt: Any = None) -> dict:
    declared = [*fields, *deepcopy(COMMON_FIELDS)] if fields else []
    identity = {"fields": declared, "execution_receipt": receipt}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return {**identity, "schema_fingerprint": "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()}


def packaged_field_value(item: dict, field: dict) -> Any:
    if item.get("object_type") != field["object_type"]:
        return None
    value = item.get("payload", {})
    for token in parse_field_path(field["payload_path"]):
        if isinstance(token, int):
            if not isinstance(value, list) or token >= len(value):
                return None
            value = value[token]
        else:
            if not isinstance(value, dict):
                return None
            value = value.get(token)
    return deepcopy(value)


def profile_export_fields(fields: list) -> list[dict]:
    labels = {field.row_ref: field.label for field in fields}
    return [{
        "ref": field.row_ref, "profile_path": field.profile_path, "label": field.label,
        "group": labels.get(field.row_ref.rsplit(".", 1)[0].replace("[]", ""), ""),
        "value_type": field.value_type, "schema_kind": field.schema_kind,
        "array_depth": field.array_depth, "required": field.required,
        "nullable": field.nullable, "enum_values": list(field.enum_values),
    } for field in fields]
