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


def _packaged_domain_pack(agent_id: str, entry: dict | None = None) -> Any:
    """The domain pack behind a packaged export source, or None (generic/unknown)."""

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
        return None
    registry = domain_pack_validation_registries().get(pack_id)
    return registry.domain_pack if registry is not None else None


def packaged_export_fields(agent_id: str, entry: dict | None = None) -> list[dict[str, Any]]:
    domain_pack = _packaged_domain_pack(agent_id, entry)
    if domain_pack is None:
        return []
    result = []
    for obj in domain_pack.metadata.object_definitions:
        summary = obj.metadata.get("export_validation_summary")
        if summary:
            from src.lib.flows.validation_summary_export import summary_fields
            result.extend(summary_fields(obj.object_type, summary))
        for field in obj.fields:
            result.append({
                "ref": f"object.pack.{obj.object_type}.{field.field_path}",
                "label": field.display_name or field.field_path.replace("_", " "),
                "group": obj.display_name, "object_type": obj.object_type,
                "payload_path": field.field_path, "pack_version": domain_pack.metadata.version,
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


_DISPLAY_ROLES = ("label", "id", "state")


def _checked_display(display: dict[str, Any], where: str) -> dict[str, Any]:
    """A display role is one leaf path; lists of fallback leaves are not allowed."""

    for role in _DISPLAY_ROLES:
        if role in display and not (isinstance(display[role], str) and display[role].strip()):
            raise ValueError(
                f"{where} metadata.display.{role} must be a single leaf path string; "
                "fallback lists are not supported."
            )
    return display


def _field_display(
    field: Any,
    models: dict[str, Any],
    object_models: dict[str, str | None] | None = None,
) -> dict[str, Any] | None:
    """Field-level display wins, then its model's (or referenced object's model's)."""

    display = field.metadata.get("display")
    if isinstance(display, dict) and display:
        return _checked_display(dict(display), f"Field '{field.field_path}'")
    model_ref = field.model_ref
    if model_ref is None and getattr(field, "object_type_ref", None):
        model_ref = (object_models or {}).get(field.object_type_ref)
    model = models.get(model_ref) if model_ref else None
    display = model.metadata.get("display") if model is not None else None
    if isinstance(display, dict) and display:
        return _checked_display(dict(display), f"Model '{model.model_id}'")
    return None


def packaged_display_specs(agent_id: str, entry: dict | None = None) -> dict[str, dict[str, Any]]:
    """Resolved display specs keyed by packaged export field ref.

    Kept apart from the export field catalog so saved field selections and their
    schema fingerprints do not change. Composite ``compose`` children carry their
    own resolved specs.
    """

    domain_pack = _packaged_domain_pack(agent_id, entry)
    if domain_pack is None:
        return {}
    models = {model.model_id: model for model in domain_pack.metadata.model_definitions}
    object_models = {
        obj.object_type: obj.model_ref for obj in domain_pack.metadata.object_definitions
    }
    specs: dict[str, dict[str, Any]] = {}
    for obj in domain_pack.metadata.object_definitions:
        by_path = {field.field_path: field for field in obj.fields}
        for field in obj.fields:
            display = _field_display(field, models, object_models)
            if display is None:
                continue
            if display.get("compose"):
                display["compose"] = [
                    {
                        "path": str(child),
                        "display": (
                            _field_display(by_path[f"{field.field_path}.{child}"], models, object_models)
                            if f"{field.field_path}.{child}" in by_path
                            else None
                        ),
                    }
                    for child in display["compose"]
                ]
            specs[f"object.pack.{obj.object_type}.{field.field_path}"] = display
    return specs


def packaged_default_layout(agent_id: str, entry: dict | None, object_types: list[str]) -> list[str]:
    """Default export refs from each object's workspace_display, in order.

    Leaf paths collapse into their nearest declared parent that has a display
    spec, so a term reads as one "label (id)" column instead of its leaves.
    """

    domain_pack = _packaged_domain_pack(agent_id, entry)
    if domain_pack is None:
        return []
    specs = packaged_display_specs(agent_id, entry)
    refs: list[str] = []
    for obj in domain_pack.metadata.object_definitions:
        if obj.object_type not in object_types:
            continue
        layout = obj.metadata.get("workspace_display") or {}
        paths = list(layout.get("summary_fields") or [])
        for group in layout.get("groups") or []:
            paths.extend(group.get("fields") or [])
        declared = {field.field_path for field in obj.fields}
        chosen_paths: list[str] = []
        for path in paths:
            chosen = path
            parts = path.split(".")
            # The path itself when it has a display, else its nearest parent that does.
            for size in range(len(parts), 0, -1):
                candidate = ".".join(parts[:size])
                if f"object.pack.{obj.object_type}.{candidate}" in specs:
                    chosen = candidate
                    break
            if chosen not in declared:
                continue
            # Skip a column already covered by a chosen ancestor or descendant.
            if any(
                existing == chosen
                or existing.startswith(chosen + ".")
                or chosen.startswith(existing + ".")
                for existing in chosen_paths
            ):
                continue
            chosen_paths.append(chosen)
            refs.append(f"object.pack.{obj.object_type}.{chosen}")
    return refs


def _walk_payload(value: Any, tokens: list) -> Any:
    if not tokens:
        return deepcopy(value)
    token, rest = tokens[0], tokens[1:]
    if isinstance(token, int):
        if not isinstance(value, list) or token >= len(value):
            return None
        return _walk_payload(value[token], rest)
    if isinstance(value, list):
        # Fan out through arrays; positions are kept so paired paths align.
        return [_walk_payload(item, tokens) for item in value]
    if not isinstance(value, dict):
        return None
    return _walk_payload(value.get(token), rest)


def packaged_field_value(item: dict, field: dict) -> Any:
    if item.get("object_type") != field["object_type"]:
        return None
    return _walk_payload(item.get("payload", {}), list(parse_field_path(field["payload_path"])))


def profile_export_fields(fields: list) -> list[dict]:
    labels = {field.row_ref: field.label for field in fields}
    return [{
        "ref": field.row_ref, "profile_path": field.profile_path, "label": field.label,
        "group": labels.get(field.row_ref.rsplit(".", 1)[0].replace("[]", ""), ""),
        "value_type": field.value_type, "schema_kind": field.schema_kind,
        "array_depth": field.array_depth, "required": field.required,
        "nullable": field.nullable, "enum_values": list(field.enum_values),
    } for field in fields]
