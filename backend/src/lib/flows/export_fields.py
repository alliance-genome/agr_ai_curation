"""Declared export fields shared by Studio authoring and runtime serialization."""
from copy import deepcopy
import hashlib
import json
import re
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
    return _pack_export_fields(domain_pack) if domain_pack is not None else []


def _declared_display(field: Any, models: dict[str, Any], object_models: dict[str, Any]) -> dict[str, Any] | None:
    """Field-level display wins, then its model's (or referenced object's model's)."""

    display = field.metadata.get("display")
    if display:
        return deepcopy(display)
    model_ref = field.model_ref
    if model_ref is None and getattr(field, "object_type_ref", None):
        model_ref = object_models.get(field.object_type_ref)
    model = models.get(model_ref) if model_ref else None
    display = model.metadata.get("display") if model is not None else None
    return deepcopy(display) if display else None


def _field_label(field: Any) -> str:
    return field.display_name or field.field_path.replace("_", " ")


def _resolvable_leaf_key(
    field: Any, obj: Any, by_path: dict[str, Any], models: dict, object_models: dict,
) -> tuple[str, str, str] | None:
    """(parent label, leaf key, mention key) when ``field`` is a resolvable value's own leaf.

    The parent is the declared field above it, or the object root (a top-level
    leaf) when the object's model declares the root resolvable.
    """

    parent_path, _, key = field.field_path.rpartition(".")
    if parent_path:
        parent = by_path.get(parent_path)
        if parent is None:
            return None
        parent_display, parent_label = _declared_display(parent, models, object_models), _field_label(parent)
    else:
        model = models.get(obj.model_ref) if obj.model_ref else None
        parent_display = model.metadata.get("display") if model is not None else None
        parent_label = obj.display_name
    if not parent_display or not parent_display.get("mention"):
        return None
    return parent_label, key, str(parent_display["mention"])


def _pack_export_fields(domain_pack: Any) -> list[dict[str, Any]]:
    from src.lib.domain_packs.resolvable_values import resolvable_leaf_header

    metadata = domain_pack.metadata
    models = {model.model_id: model for model in metadata.model_definitions}
    object_models = {obj.object_type: obj.model_ref for obj in metadata.object_definitions}
    enums = {enum.enum_id: [value.value for value in enum.values] for enum in metadata.enum_definitions}
    result = []
    for obj in metadata.object_definitions:
        summary = obj.metadata.get("export_validation_summary")
        if summary:
            from src.lib.flows.validation_summary_export import summary_fields
            result.extend(summary_fields(obj.object_type, summary))
        by_path = {field.field_path: field for field in obj.fields}
        for field in obj.fields:
            label = _field_label(field)
            # A resolvable value's paper wording, status, lookup result and
            # validator explanation are their own columns (ALL-1283).
            leaf = _resolvable_leaf_key(field, obj, by_path, models, object_models)
            if leaf is not None:
                parent_label, key, mention_key = leaf
                label = resolvable_leaf_header(parent_label, key, mention_key=mention_key) or label
            entry = {
                "ref": f"object.pack.{obj.object_type}.{field.field_path}",
                "label": label,
                "group": obj.display_name, "object_type": obj.object_type,
                "payload_path": field.field_path, "pack_version": domain_pack.metadata.version,
                "value_type": "list" if field.multivalued else field.field_type.value,
                "schema_kind": field.field_type.value,
                "required": field.required, "nullable": not field.required,
                "array_depth": int(field.multivalued),
                "description": field.description,
            }
            if field.enum_ref is not None:
                # Controlled vocabularies carry their allowed values.
                entry["enum_values"] = list(enums[field.enum_ref])
            result.append(entry)
    return result


def _legacy_display_mapper(domain_pack_id: str) -> Any | None:
    from src.lib.curation_workspace.adapter_registry import (
        resolve_curation_legacy_display_mapper_by_id,
    )

    return resolve_curation_legacy_display_mapper_by_id(domain_pack_id)


def source_catalog(fields: list[dict], receipt: Any = None) -> dict:
    declared = [*fields, *deepcopy(COMMON_FIELDS)] if fields else []
    identity = {"fields": declared, "execution_receipt": receipt}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return {**identity, "schema_fingerprint": "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()}


_LIST_INDEX = re.compile(r"\[\d+\]")


class PackagedExportSource:
    """One packaged source's export catalog, resolved once per output bundle.

    Display declarations were validated when the pack loaded
    (``src.schemas.domain_pack_metadata``); this only resolves them.
    """

    def __init__(self, domain_pack: Any) -> None:
        from src.lib.domain_packs.materialization import _definition_object_role, _object_role_key

        metadata = domain_pack.metadata
        self.domain_pack = domain_pack
        self.fields = _pack_export_fields(domain_pack)
        self._models = {model.model_id: model for model in metadata.model_definitions}
        self._object_models = {obj.object_type: obj.model_ref for obj in metadata.object_definitions}
        role_key = _object_role_key(metadata)
        self._roles = {
            obj.object_type: _definition_object_role(obj, object_role_key=role_key)
            for obj in metadata.object_definitions
        }
        # The curatable units are the rows curators review; their supporting
        # objects (validated references, evidence quotes) are not laid out.
        self.curatable_unit_types = [
            object_type for object_type, role in self._roles.items() if role == "curatable_unit"
        ]
        self.display_specs = self._display_specs()
        self.resolvable_fields = self._resolvable_fields()
        self.object_ref_fields = {
            obj.object_type: fields
            for obj in metadata.object_definitions
            if (fields := {
                field.field_path: field.object_type_ref
                for field in obj.fields
                if getattr(field, "object_type_ref", None)
            })
        }
        self.object_label_paths = self._object_label_paths()
        self.legacy_display_mapper = _legacy_display_mapper(metadata.pack_id)

    def _field_display(self, field: Any) -> dict[str, Any] | None:
        return _declared_display(field, self._models, self._object_models)

    def _resolvable_fields(self) -> dict[str, dict[str, Any]]:  # {object_type: {path: ResolvableSpec}}
        """Declared resolvable values per object type ("" is the object root)."""

        from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

        metadata = self.domain_pack.metadata
        return {
            obj.object_type: specs
            for obj in metadata.object_definitions
            if (specs := declared_resolvable_fields(metadata, obj.object_type))
        }

    def effective_item(self, item: dict) -> dict:
        """An object row's item with the read-time resolution state of its declared values.

        A record stored in a previous pack format is first read in the current
        value shape by the pack's registered legacy display mapper; values
        stored before ALL-1283 then read through the legacy rule
        (``resolvable_values.effective_payload``); overruled identities are
        left out. Nothing is written back.
        """

        from src.lib.domain_packs.resolvable_values import effective_payload, without_overruled

        object_type = str(item.get("object_type") or "")
        specs = self.resolvable_fields.get(object_type)
        payload = item.get("payload")
        if not isinstance(payload, dict):
            return item
        # An identity a validator overruled is never exported.
        payload = without_overruled(payload)
        if not specs:
            return {**item, "payload": payload}
        if self.legacy_display_mapper is not None:
            payload = self.legacy_display_mapper(object_type, payload)
        metadata = item.get("metadata")
        return {
            **item,
            "payload": effective_payload(
                payload, specs, object_metadata=metadata if isinstance(metadata, dict) else None,
            ),
        }

    def _resolved(self, display: dict[str, Any], field_path: str, by_path: dict[str, Any]) -> dict[str, Any]:
        """Composite parts carry their own resolved specs, recursively."""

        if not display.get("compose"):
            return display
        entries = []
        for entry in display["compose"]:
            if isinstance(entry, dict):
                path, child = entry.get("path"), entry.get("display")
            else:
                path, child = str(entry), None
            if path and child is None and f"{field_path}.{path}" in by_path:
                child = self._field_display(by_path[f"{field_path}.{path}"])
            if child is not None:
                child = self._resolved(child, f"{field_path}.{path}" if path else field_path, by_path)
            entries.append({"path": path, "display": child} if path else {"display": child})
        return {**display, "compose": entries}

    def _display_specs(self) -> dict[str, dict[str, Any]]:
        """Resolved display specs keyed by packaged export field ref.

        Kept apart from the export field catalog so saved field selections and
        their schema fingerprints do not change.
        """

        from src.lib.domain_packs.resolvable_values import LEAF_VALUE_LABELS

        specs: dict[str, dict[str, Any]] = {}
        for obj in self.domain_pack.metadata.object_definitions:
            by_path = {field.field_path: field for field in obj.fields}
            for field in obj.fields:
                ref = f"object.pack.{obj.object_type}.{field.field_path}"
                display = self._field_display(field)
                if display is not None:
                    specs[ref] = self._resolved(display, field.field_path, by_path)
                    continue
                leaf = _resolvable_leaf_key(field, obj, by_path, self._models, self._object_models)
                if leaf is not None and leaf[1] in LEAF_VALUE_LABELS:
                    # A resolvable value's status and lookup result read in plain words.
                    specs[ref] = {"value_labels": dict(LEAF_VALUE_LABELS[leaf[1]])}
        return specs

    def _object_label_paths(self) -> dict[str, str]:
        """Declared object label path per object type (Chris, Sep 22).

        The object-root model's display label, else workspace_display
        primary_label_field; object types without either have no declared label.
        """

        paths: dict[str, str] = {}
        for obj in self.domain_pack.metadata.object_definitions:
            model = self._models.get(obj.model_ref) if obj.model_ref else None
            display = model.metadata.get("display") if model is not None else None
            if display and display.get("label"):
                paths[obj.object_type] = str(display["label"])
                continue
            primary = (obj.metadata.get("workspace_display") or {}).get("primary_label_field")
            if isinstance(primary, str) and primary.strip():
                paths[obj.object_type] = primary.strip()
        return paths

    def default_layout(self, object_types: list[str]) -> list[str]:
        """Default export refs from each curatable unit's workspace_display, in order.

        Leaf paths collapse into their nearest declared parent that has a display
        spec, so a term reads as one "label (id)" column instead of its leaves. A
        path that reads one list element (``terms[0].label``) collapses into the
        declared list field itself, so the default export carries every element.
        A pack without curatable units lays out the objects it declares.
        """

        has_units = bool(self.curatable_unit_types)
        refs: list[str] = []
        for obj in self.domain_pack.metadata.object_definitions:
            if obj.object_type not in object_types:
                continue
            if has_units and self._roles[obj.object_type] != "curatable_unit":
                continue
            layout = obj.metadata.get("workspace_display") or {}
            paths = list(layout.get("summary_fields") or [])
            for group in layout.get("groups") or []:
                paths.extend(group.get("fields") or [])
            declared = {field.field_path for field in obj.fields}
            lists = {field.field_path for field in obj.fields if field.multivalued}
            chosen_paths: list[str] = []
            for path in paths:
                chosen = path
                parts = path.split(".")
                list_field = next(
                    (
                        path[: match.start()]
                        for match in _LIST_INDEX.finditer(path)
                        if path[: match.start()] in lists
                    ),
                    None,
                )
                if list_field is not None:
                    chosen = list_field
                else:
                    # The path itself when it has a display, else its nearest parent that does.
                    for size in range(len(parts), 0, -1):
                        candidate = ".".join(parts[:size])
                        if f"object.pack.{obj.object_type}.{candidate}" in self.display_specs:
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


def packaged_export_source(
    agent_id: str, entry: dict | None, *, cache: dict[tuple[str, str], PackagedExportSource | None],
) -> PackagedExportSource | None:
    """The packaged export source for a step, resolved once per ``cache`` (one bundle)."""

    pack_id = (entry.get("curation") or {}).get("domain_pack_id") if entry is not None else None
    key = ("pack", str(pack_id)) if entry is not None else ("agent", agent_id)
    if key not in cache:
        domain_pack = _packaged_domain_pack(agent_id, entry)
        cache[key] = PackagedExportSource(domain_pack) if domain_pack is not None else None
    return cache[key]


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


# The builder stores a curator-facing rationale beside every profile-bound
# record's attributes; it is selectable like any declared profile field.
PROFILE_RATIONALE_EXPORT_FIELD = {
    "ref": "object.payload.rationale", "label": "Rationale", "group": "",
    "value_type": "string", "schema_kind": "string", "array_depth": 0,
    "required": False, "nullable": True, "enum_values": [],
}


def profile_export_fields(fields: list) -> list[dict]:
    labels = {field.row_ref: field.label for field in fields}
    return [{
        "ref": field.row_ref, "profile_path": field.profile_path, "label": field.label,
        "group": labels.get(field.row_ref.rsplit(".", 1)[0].replace("[]", ""), ""),
        "value_type": field.value_type, "schema_kind": field.schema_kind,
        "array_depth": field.array_depth, "required": field.required,
        "nullable": field.nullable, "enum_values": list(field.enum_values),
    } for field in fields] + [deepcopy(PROFILE_RATIONALE_EXPORT_FIELD)]
