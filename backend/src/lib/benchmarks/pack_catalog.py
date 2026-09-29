"""Benchmark-only catalog of one domain pack: record kinds, families and field facts.

The Studio export catalog (``export_fields.source_catalog``) is not changed; this
describes the same declarations for choosing benchmark fields. Every fact comes
from an explicit declaration (field type, display roles, ``free_text``,
``validator_binding_id``, ``record_kind_families``); nothing is inferred from a
name or description. Only curatable record kinds carry fields and defaults.
"""

from typing import Any

from src.lib.domain_packs.resolvable_values import CONTRACT_KEYS, MENTION_KEY
from src.lib.flows.export_fields import PackagedExportSource, _declared_display
from src.schemas.domain_envelope import parse_field_path
from src.schemas.domain_pack_metadata import DomainPackFieldType, DomainPackRecordKindFamily

VALIDATOR_WRITTEN_KEYS = frozenset(CONTRACT_KEYS) - {MENTION_KEY}
EVIDENCE_LINK_KEYS = frozenset({"evidence_record_id", "evidence_record_ids"})
TEXT_TYPES = frozenset({DomainPackFieldType.STRING, DomainPackFieldType.ENUM})
OFFERABLE_SHAPES = frozenset({"text", "text_list", "text_from_each_item"})


def _is_list(field: Any) -> bool:
    return field.multivalued or field.field_type is DomainPackFieldType.ARRAY


def _indexed(path: str) -> bool:
    return any(isinstance(part, int) for part in parse_field_path(path))


class _ObjectFacts:
    """One object type's declared fields and their benchmark facts, keyed by declared path."""

    def __init__(self, obj: Any, models: dict[str, Any], object_models: dict[str, Any]) -> None:
        self.obj = obj
        self.models = models
        self.object_models = object_models
        self.declared = {field.field_path: field for field in obj.fields}
        self.by_path = {path: field for path, field in self.declared.items() if not _indexed(path)}
        self.facts: dict[str, dict[str, Any]] = {}
        for path, field in self.by_path.items():
            if field.metadata.get("exported") is False:
                continue
            self.facts[path] = self._fact(field)

    def _has_children(self, path: str) -> bool:
        prefix = path + "."
        return any(other.startswith(prefix) for other in self.by_path)

    def _display(self, path: str) -> dict[str, Any] | None:
        if not path:
            model = self.models.get(self.obj.model_ref) if self.obj.model_ref else None
            display = model.metadata.get("display") if model is not None else None
            return display or None
        field = self.by_path.get(path)
        if field is None:
            return None
        return _declared_display(field, self.models, self.object_models)

    def _written_path(self, path: str) -> tuple[str, int]:
        """``path`` with ``[]`` after each declared list ancestor, and how many lists are above."""
        parts = path.split(".")
        written: list[str] = []
        lists = 0
        for size in range(1, len(parts)):
            segment = parts[size - 1]
            ancestor = self.by_path.get(".".join(parts[:size]))
            if ancestor is not None and _is_list(ancestor):
                segment += "[]"
                lists += 1
            written.append(segment)
        written.append(parts[-1])
        return ".".join(written), lists

    def _shape(self, field: Any) -> str:
        path = field.field_path
        if _is_list(field):
            if self._has_children(path):
                return "object_list"
            element = self.declared.get(f"{path}[0]")
            return "text_list" if element is None or element.field_type in TEXT_TYPES else "other"
        kind = field.field_type
        if kind in TEXT_TYPES:
            return "text"
        if kind in (DomainPackFieldType.OBJECT, DomainPackFieldType.OBJECT_REF):
            return "object" if self._has_children(path) else "other"
        if kind is DomainPackFieldType.BOOLEAN:
            return "yes_no"
        if kind in (DomainPackFieldType.INTEGER, DomainPackFieldType.NUMBER):
            return "number"
        return "other"

    def _binding(self, field: Any, parent_path: str, key: str, resolvable: bool) -> str | None:
        if resolvable and key == MENTION_KEY:
            return None  # The paper wording is the extractor's, never a validator's.
        own = field.metadata.get("validator_binding_id")
        if own:
            return str(own)
        if not resolvable or not parent_path:
            return None
        parent = self.by_path.get(parent_path)
        inherited = parent.metadata.get("validator_binding_id") if parent is not None else None
        return str(inherited) if inherited else None

    def _fact(self, field: Any) -> dict[str, Any]:
        path = field.field_path
        written, lists = self._written_path(path)
        shape = self._shape(field)
        inside_list = lists > 1 or (lists == 1 and _is_list(field))
        if lists == 1 and not inside_list and shape == "text":
            shape = "text_from_each_item"
        parent_path, _, key = path.rpartition(".")
        parent_display = self._display(parent_path)
        resolvable = bool(parent_display and parent_display.get("mention"))
        return {
            "object_type": self.obj.object_type,
            "path": written,
            "label": field.display_name or path.replace("_", " "),
            "shape": shape,
            "inside_list": inside_list,
            "is_identifier": bool(parent_display) and parent_display.get("id") == key,
            "validator_written": resolvable and key in VALIDATOR_WRITTEN_KEYS,
            "is_pointer": (
                field.field_type is DomainPackFieldType.OBJECT_REF and not self._has_children(path)
            ) or key in EVIDENCE_LINK_KEYS,
            "free_text": field.metadata.get("free_text") is True,
            "validator_binding_id": self._binding(field, parent_path, key, resolvable),
        }

    def _default_entry(self, layout_path: str) -> dict[str, Any] | None:
        fact = self.facts.get(layout_path)
        if fact is None:
            return None
        leaf_path = layout_path
        if fact["shape"] in ("object", "object_list"):
            display = self._display(layout_path) or {}
            role = next((display[name] for name in ("id", "label")
                         if isinstance(display.get(name), str)
                         and f"{layout_path}.{display[name]}" in self.facts), None)
            if role is None:
                return None
            leaf_path = f"{layout_path}.{role}"
        leaf = self.facts[leaf_path]
        if (leaf["shape"] not in OFFERABLE_SHAPES or leaf["inside_list"] or leaf["is_pointer"]
                or leaf["validator_written"] or leaf["free_text"]):
            return None
        mention = None
        value_path = leaf_path.rpartition(".")[0]
        if leaf["validator_binding_id"] and value_path:
            mention_key = (self._display(value_path) or {}).get("mention")
            mention_fact = (self.facts.get(f"{value_path}.{mention_key}")
                            if isinstance(mention_key, str) else None)
            mention = mention_fact["path"] if mention_fact is not None else None
        return {"path": leaf["path"], "if_not_validated": mention}

    def default_fields(self, layout_paths: list[str]) -> list[dict[str, Any]]:
        chosen: list[dict[str, Any]] = []
        for layout_path in layout_paths:
            entry = self._default_entry(layout_path)
            if entry is not None and all(entry["path"] != item["path"] for item in chosen):
                chosen.append(entry)
        return chosen


def benchmark_pack_catalog(domain_pack: Any) -> dict[str, Any]:
    """The pack's record kinds, families, curatable field facts and default fields.

    Defaults are the curatable kind's ``workspace_display`` layout, read live on
    every call, mapped to comparable leaves; no copy is kept anywhere.
    """
    metadata = domain_pack.metadata
    source = PackagedExportSource(domain_pack)
    models = {model.model_id: model for model in metadata.model_definitions}
    object_models = {obj.object_type: obj.model_ref for obj in metadata.object_definitions}
    curatable = set(source.curatable_unit_types)
    record_kinds: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    defaults: dict[str, list[dict[str, Any]]] = {}
    for obj in metadata.object_definitions:
        role = "curatable" if obj.object_type in curatable else "supporting"
        record_kinds.append({"object_type": obj.object_type, "label": obj.display_name,
                             "role": role})
        if role != "curatable":
            continue
        facts = _ObjectFacts(obj, models, object_models)
        fields.extend(facts.facts.values())
        prefix = f"object.pack.{obj.object_type}."
        layout = [ref.removeprefix(prefix) for ref in source.default_layout([obj.object_type])]
        defaults[obj.object_type] = facts.default_fields(layout)
    # Validated when the pack loaded; parsed here into the typed declaration.
    families = [
        DomainPackRecordKindFamily.model_validate(raw).model_dump()
        for raw in metadata.metadata.get("record_kind_families", [])
    ]
    return {
        "pack_id": metadata.pack_id, "pack_version": metadata.version,
        "pack_label": metadata.display_name, "record_kinds": record_kinds,
        "families": families, "fields": fields, "default_fields": defaults,
    }
