"""Lint: every structured value in an Alliance domain pack declares a display spec (ALL-1282).

Non-JSON flow outputs (CSV, TSV, chat tables) render structured payload values from
pack-declared display roles instead of JSON text. The declaration contract is:

* ``model_definitions[].metadata.display``: ``{label, id, state, resolved_states}``
  where each value except ``resolved_states`` is a payload path relative to the value.
* ``fields[].metadata.display`` overrides the model spec for that field, either with
  the same role keys or with ``{compose: [child paths], separator}``.
* A ``compose`` entry is a child path, or ``{path, display}``; an entry without a path
  reads the value itself with its own ``display`` roles (ALL-1290), and a child path may
  name the parent of declared leaves (e.g. ``condition_chemical`` over its ``.curie``).

These tests keep every structured field (object, object_ref, arrays of objects) and every
model a field can reach covered, and keep the referenced leaves in line with the pack's
declared children and the production value shapes in
``tests/fixtures/flows/display_value_shapes.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.schemas.domain_envelope import parse_field_path
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


pytestmark = pytest.mark.provider_agnostic_domain_pack

REPO_ROOT = Path(__file__).resolve().parents[5]
PACK_ROOT = REPO_ROOT / "packages" / "alliance" / "domain_packs"
PACK_PATHS = {
    "agr.alliance.base": PACK_ROOT / "agr.alliance.base" / "domain_pack.yaml",
    "agr.alliance.allele": PACK_ROOT / "allele" / "domain_pack.yaml",
    "agr.alliance.disease": PACK_ROOT / "disease" / "domain_pack.yaml",
    "agr.alliance.gene_expression": PACK_ROOT / "gene_expression" / "domain_pack.yaml",
    "agr.alliance.go": PACK_ROOT / "go" / "domain_pack.yaml",
    "agr.alliance.phenotype": PACK_ROOT / "phenotype" / "domain_pack.yaml",
    "gene": PACK_ROOT / "gene" / "domain_pack.yaml",
}
SHAPES_PATH = REPO_ROOT / "backend" / "tests" / "fixtures" / "flows" / "display_value_shapes.json"

ROLE_KEYS = {"label", "id", "state"}
SPEC_KEYS = ROLE_KEYS | {"resolved_states", "compose", "separator"}

# Models no display can be declared for yet, each with the reason. Keep this list short:
# a new structured model must declare display instead of being added here.
EXEMPT_MODELS = {
    ("agr.alliance.base", "GeneExpressionAnnotationPayload"): (
        "scaffold pack bound to no agent; the payload shell declares no leaves"
    ),
    ("agr.alliance.base", "ExpressionPatternContextPayload"): (
        "scaffold pack bound to no agent; composes only leafless context models"
    ),
    ("agr.alliance.base", "TemporalContextPayload"): "scaffold model without declared leaves",
    ("agr.alliance.base", "AnatomicalSitePayload"): "scaffold model without declared leaves",
    ("agr.alliance.gene_expression", "ReagentSnapshotPayload"): (
        "in-development placeholder; only a boolean placeholder leaf is declared"
    ),
    ("agr.alliance.gene_expression", "AffectedGenomicModelSnapshotPayload"): (
        "in-development placeholder without declared leaves"
    ),
    ("agr.alliance.gene_expression", "AlleleSnapshotPayload"): (
        "in-development placeholder without declared leaves"
    ),
    ("agr.alliance.phenotype", "PhenotypeAnnotationPayload"): (
        "curatable-unit row whose only own label leaf is the free-text statement; the "
        "resolved phenotype terms render through the phenotype_terms[0] field"
    ),
    ("agr.alliance.disease", "VocabularyTermSnapshotPayload"): (
        "declared but referenced by no field (condition_relation_type leaves are declared directly)"
    ),
}
EXEMPT_FIELDS = {
    ("agr.alliance.go", "GOCuratableObject", "provider_context"): (
        "read-only provider diagnostics without declared leaves; generic rendering applies"
    ),
}
# Production shapes that carry no displayable leaf for the declared roles.
SHAPES_WITHOUT_DISPLAY_LEAF = {
    ("disease_unresolved_object_blocked_subject", "single_reference"): (
        "pending reference carries only resolution_state and resolution_note"
    ),
    ("disease_unresolved_object_blocked_subject", "disease_annotation_subject"): (
        "blocked subject carries only resolution_state and resolution_note"
    ),
    ("phenotype_pending_subject_and_term", "evidence_quote"): (
        "embedded evidence quote carries only evidence_record_id; the quote is a separate object"
    ),
    ("ge_empty_where_expressed", "expression_pattern"): (
        "where_expressed is an empty object and no when_expressed is present"
    ),
    ("ge_empty_where_expressed", "expression_pattern.where_expressed"): (
        "empty object; nothing to display"
    ),
}


def _key(field_path: str) -> str:
    """Declared field path without list indexes (``a[0].b`` -> ``a.b``)."""

    return ".".join(part for part in parse_field_path(field_path) if isinstance(part, str))


def _packs() -> dict[str, DomainPackMetadata]:
    return {pack_id: load_domain_pack_metadata(path) for pack_id, path in PACK_PATHS.items()}


def _objects(pack: DomainPackMetadata) -> dict[str, DomainPackObjectDefinition]:
    return {obj.object_type: obj for obj in pack.object_definitions}


def _models(pack: DomainPackMetadata) -> dict[str, Any]:
    return {model.model_id: model for model in pack.model_definitions}


def _children(
    pack: DomainPackMetadata,
    obj: DomainPackObjectDefinition,
    field: DomainPackFieldDefinition | None,
) -> dict[str, DomainPackFieldDefinition]:
    """Declared descendants of ``field`` (or of the object root) keyed by relative path."""

    if field is not None and field.field_type is DomainPackFieldType.OBJECT_REF:
        return _children(pack, _objects(pack)[field.object_type_ref], None)
    prefix = "" if field is None else _key(field.field_path) + "."
    return {
        _key(child.field_path)[len(prefix):]: child
        for child in obj.fields
        if _key(child.field_path).startswith(prefix) and _key(child.field_path) != prefix[:-1]
    }


def _is_structured(
    pack: DomainPackMetadata,
    obj: DomainPackObjectDefinition,
    field: DomainPackFieldDefinition,
) -> bool:
    if field.field_type in {DomainPackFieldType.OBJECT, DomainPackFieldType.OBJECT_REF}:
        return True
    if field.field_type is DomainPackFieldType.ARRAY:
        return field.model_ref is not None or bool(_children(pack, obj, field))
    return False


def _field_model_id(
    pack: DomainPackMetadata,
    field: DomainPackFieldDefinition,
) -> str | None:
    if field.field_type is DomainPackFieldType.OBJECT_REF:
        return _objects(pack)[field.object_type_ref].model_ref
    return field.model_ref


def _field_spec(
    pack: DomainPackMetadata,
    field: DomainPackFieldDefinition,
) -> dict[str, Any] | None:
    if "display" in field.metadata:
        return field.metadata["display"]
    model_id = _field_model_id(pack, field)
    if model_id is None:
        return None
    return _models(pack)[model_id].metadata.get("display")


def _structured_fields(
    pack: DomainPackMetadata,
) -> Iterator[tuple[DomainPackObjectDefinition, DomainPackFieldDefinition]]:
    for obj in pack.object_definitions:
        for field in obj.fields:
            if _is_structured(pack, obj, field):
                yield obj, field


def _compose_path(entry: Any) -> str | None:
    """A compose entry's child path; None for an entry that reads the value itself."""

    return entry.get("path") if isinstance(entry, dict) else entry


def _display_leaves(spec: dict[str, Any]) -> list[str]:
    """Paths a spec reads: its label/id leaves, or its compose parts' paths."""

    if "compose" not in spec:
        return [spec[role] for role in ("label", "id") if role in spec]
    return [
        leaf
        for entry in spec["compose"]
        for leaf in (
            [_compose_path(entry)] if _compose_path(entry) is not None else _display_leaves(entry["display"])
        )
    ]


def _is_child_path(path: str, children: dict[str, DomainPackFieldDefinition]) -> bool:
    return path in children or any(child.startswith(path + ".") for child in children)


def _spec_errors(
    pack: DomainPackMetadata,
    spec: Any,
    children: dict[str, DomainPackFieldDefinition],
    where: str,
) -> list[str]:
    if not isinstance(spec, dict) or not spec:
        return [f"{where}: display must be a non-empty mapping"]
    errors = [f"{where}: unknown display key {key!r}" for key in sorted(set(spec) - SPEC_KEYS)]
    if "compose" in spec:
        compose = spec["compose"]
        if set(spec) & ROLE_KEYS or "resolved_states" in spec:
            errors.append(f"{where}: compose cannot be combined with role keys")
        if not isinstance(compose, list) or not compose:
            return [*errors, f"{where}: compose must be a non-empty list"]
        if not isinstance(spec.get("separator"), str) or not spec["separator"]:
            errors.append(f"{where}: compose needs a non-empty separator")
        for entry in compose:
            path = _compose_path(entry)
            if path is not None and not _is_child_path(path, children):
                errors.append(f"{where}: compose child {path!r} is not a declared child path")
            if isinstance(entry, dict) and path is None:
                errors.extend(_spec_errors(pack, entry.get("display"), children, f"{where}.compose[self]"))
        return errors
    if "separator" in spec:
        errors.append(f"{where}: separator is only valid with compose")
    if not {"label", "id"} & set(spec):
        errors.append(f"{where}: display needs a label or id role")
    for role in sorted(ROLE_KEYS & set(spec)):
        if spec[role] not in children:
            errors.append(f"{where}: {role} leaf {spec[role]!r} is not a declared child path")
        elif children[spec[role]].field_type in {
            DomainPackFieldType.OBJECT,
            DomainPackFieldType.OBJECT_REF,
            DomainPackFieldType.ARRAY,
        }:
            errors.append(f"{where}: {role} {spec[role]!r} must name a scalar leaf")
    if ("state" in spec) != ("resolved_states" in spec):
        errors.append(f"{where}: state and resolved_states must be declared together")
    resolved = spec.get("resolved_states")
    if resolved is not None:
        if not isinstance(resolved, list) or not resolved:
            errors.append(f"{where}: resolved_states must be a non-empty list")
        state_field = children.get(spec.get("state"))
        if state_field is not None and state_field.enum_ref is not None:
            enum = next(e for e in pack.enum_definitions if e.enum_id == state_field.enum_ref)
            allowed = {value.value for value in enum.values}
            errors.extend(
                f"{where}: resolved state {value!r} is not in enum {enum.enum_id}"
                for value in resolved or []
                if value not in allowed
            )
    return errors


@pytest.mark.parametrize("pack_id", sorted(PACK_PATHS))
def test_every_structured_field_resolves_to_a_valid_display_spec(pack_id: str) -> None:
    pack = _packs()[pack_id]
    errors: list[str] = []
    for obj, field in _structured_fields(pack):
        where = f"{pack_id}:{obj.object_type}.{field.field_path}"
        model_id = _field_model_id(pack, field)
        if (pack_id, obj.object_type, field.field_path) in EXEMPT_FIELDS:
            continue
        if "display" not in field.metadata and (pack_id, model_id) in EXEMPT_MODELS:
            continue
        spec = _field_spec(pack, field)
        if spec is None:
            errors.append(f"{where}: no field or model display spec")
            continue
        children = _children(pack, obj, field)
        if "display" in field.metadata:
            # Model specs are checked against every usage of the model below.
            errors.extend(_spec_errors(pack, spec, children, where))
        for entry in spec.get("compose", []):
            child = _compose_path(entry) or ""
            child_field = children.get(child)
            if (
                child_field is not None
                and _is_structured(pack, obj, child_field)
                and _field_spec(pack, child_field) is None
                and (pack_id, _field_model_id(pack, child_field)) not in EXEMPT_MODELS
            ):
                errors.append(f"{where}: compose child {child!r} has no display spec")
    assert errors == []


@pytest.mark.parametrize("pack_id", sorted(PACK_PATHS))
def test_every_model_declares_display_or_is_covered_by_its_fields(pack_id: str) -> None:
    pack = _packs()[pack_id]
    usages: dict[str, list[tuple[DomainPackObjectDefinition, DomainPackFieldDefinition | None]]] = {}
    for obj in pack.object_definitions:
        if obj.model_ref is not None:
            usages.setdefault(obj.model_ref, []).append((obj, None))
    for obj, field in _structured_fields(pack):
        model_id = _field_model_id(pack, field)
        if model_id is not None:
            usages.setdefault(model_id, []).append((obj, field))

    errors: list[str] = []
    for model in pack.model_definitions:
        where = f"{pack_id}:{model.model_id}"
        exempt = (pack_id, model.model_id) in EXEMPT_MODELS
        spec = model.metadata.get("display")
        if spec is None:
            field_usages = [field for _, field in usages.get(model.model_id, []) if field is not None]
            covered = bool(field_usages) and all(
                "display" in field.metadata for field in field_usages
            )
            if not covered and not exempt:
                errors.append(f"{where}: no model display and not covered by field displays")
            continue
        if exempt:
            errors.append(f"{where}: declares display but is still listed as exempt")
        children: dict[str, DomainPackFieldDefinition] = {}
        for obj, field in usages.get(model.model_id, []):
            children.update(_children(pack, obj, field))
        if not usages.get(model.model_id):
            errors.append(f"{where}: declares display but no object or field uses the model")
            continue
        errors.extend(_spec_errors(pack, spec, children, where))
    assert errors == []


def test_exemptions_still_name_real_models_and_fields() -> None:
    packs = _packs()
    for pack_id, model_id in EXEMPT_MODELS:
        assert model_id in _models(packs[pack_id]), (pack_id, model_id)
        assert "display" not in _models(packs[pack_id])[model_id].metadata
    for pack_id, object_type, field_path in EXEMPT_FIELDS:
        fields = {field.field_path: field for field in _objects(packs[pack_id])[object_type].fields}
        assert field_path in fields and "display" not in fields[field_path].metadata


def _values_at(value: Any, parts: list[str]) -> Iterator[Any]:
    """Yield every value at ``parts``, fanning out through lists."""

    if isinstance(value, list):
        for item in value:
            yield from _values_at(item, parts)
        return
    if not parts:
        yield value
        return
    if isinstance(value, dict) and parts[0] in value:
        yield from _values_at(value[parts[0]], parts[1:])


def _leaf_present(value: dict[str, Any], path: str) -> bool:
    return any(
        leaf not in (None, "", [], {})
        for leaf in _values_at(value, path.split("."))
    )


def _shapes() -> dict[str, Any]:
    return json.loads(SHAPES_PATH.read_text())


def test_production_shape_fixture_is_synthetic_and_names_declared_objects() -> None:
    shapes = _shapes()
    packs = _packs()
    assert shapes["packaged"] and shapes["custom_profiles"]
    for shape in shapes["packaged"]:
        assert shape["object_type"] in _objects(packs[shape["pack_id"]]), shape["shape_id"]
    shape_ids = [shape["shape_id"] for shape in [*shapes["packaged"], *shapes["custom_profiles"]]]
    assert len(shape_ids) == len(set(shape_ids))
    assert {shape["pack_id"] for shape in shapes["packaged"]} == set(PACK_PATHS) - {"agr.alliance.base"}


@pytest.mark.parametrize(
    "shape",
    _shapes()["packaged"],
    ids=lambda shape: shape["shape_id"],
)
def test_production_shapes_expose_a_declared_display_leaf(shape: dict[str, Any]) -> None:
    pack = _packs()[shape["pack_id"]]
    obj = _objects(pack)[shape["object_type"]]
    errors: list[str] = []
    for field in obj.fields:
        if not _is_structured(pack, obj, field):
            continue
        if (shape["pack_id"], obj.object_type, field.field_path) in EXEMPT_FIELDS:
            continue
        for value in _values_at(shape["payload"], _key(field.field_path).split(".")):
            if not isinstance(value, dict) or not value:
                continue
            if (shape["shape_id"], field.field_path) in SHAPES_WITHOUT_DISPLAY_LEAF:
                continue
            spec = _field_spec(pack, field)
            where = f"{shape['shape_id']}:{field.field_path}"
            if spec is None:
                errors.append(f"{where}: structured value without a display spec")
                continue
            paths = _display_leaves(spec)
            if not any(_leaf_present(value, path) for path in paths):
                errors.append(f"{where}: none of {paths} is present in {sorted(value)}")
    assert errors == []
