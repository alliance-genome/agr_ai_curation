"""Unit tests for Agent Studio domain-envelope authoring metadata projections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import src.lib.agent_studio.domain_envelope_metadata as metadata_module
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry


FIELD_GROUP_PACK_TEXT = """
pack_id: fixture.groups
display_name: Fixture Field Group Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: AnnotationPayload
    display_name: Annotation payload
  - model_id: NotePayload
    display_name: Note payload
object_definitions:
  - object_type: Annotation
    display_name: Annotation
    model_ref: AnnotationPayload
    metadata:
      object_role: curatable_unit
      workspace_display:
        primary_label_field: subject.label
        groups:
          - id: provenance
            label: Provenance
            fields:
              - data_provider.abbreviation
          - id: subject
            label: Subject
            fields:
              - subject.identifier
              - subject.label
          - id: evidence
            label: Evidence & codes
            fields:
              - evidence_code_curies
              - not_a_declared_field
    fields:
      - field_path: subject.identifier
        field_type: string
        required: true
      - field_path: subject.label
        field_type: string
      - field_path: evidence_code_curies
        field_type: array
      - field_path: data_provider.abbreviation
        field_type: string
  - object_type: Note
    display_name: Note
    model_ref: NotePayload
    fields:
      - field_path: text
        field_type: string
metadata:
  validator_bindings:
    active: []
""".strip()


def _catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    pack_path = tmp_path / "fixture.groups"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(FIELD_GROUP_PACK_TEXT, encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    loaded_pack = LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
        package_id="org.owner",
    )
    registry = DomainPackValidationRegistry.from_domain_pack(loaded_pack)
    monkeypatch.setattr(
        metadata_module,
        "domain_pack_validation_registries",
        lambda: {"fixture.groups": registry},
    )

    catalog = metadata_module.domain_envelope_metadata_catalog_by_agent(
        {"fixture_extractor": {"curation": {"domain_pack_id": "fixture.groups"}}}
    )
    return catalog["fixture_extractor"]


def _object_definition(payload: dict[str, Any], object_type: str) -> dict[str, Any]:
    matches = [
        definition
        for definition in payload["object_definitions"]
        if definition["object_type"] == object_type
    ]
    assert len(matches) == 1
    return matches[0]


def test_object_with_workspace_groups_exposes_field_groups(monkeypatch, tmp_path):
    payload = _catalog(monkeypatch, tmp_path)
    annotation = _object_definition(payload, "Annotation")

    assert annotation["field_groups"] == [
        {
            "id": "provenance",
            "label": "Provenance",
            "field_paths": ["data_provider.abbreviation"],
        },
        {
            "id": "subject",
            "label": "Subject",
            "field_paths": ["subject.identifier", "subject.label"],
        },
        {
            "id": "evidence",
            "label": "Evidence & codes",
            "field_paths": ["evidence_code_curies", "not_a_declared_field"],
        },
    ]


def test_object_without_workspace_groups_exposes_empty_field_groups(
    monkeypatch, tmp_path
):
    payload = _catalog(monkeypatch, tmp_path)
    note = _object_definition(payload, "Note")

    assert note["field_groups"] == []


def test_field_groups_preserve_domain_pack_order(monkeypatch, tmp_path):
    payload = _catalog(monkeypatch, tmp_path)
    annotation = _object_definition(payload, "Annotation")

    group_ids = [group["id"] for group in annotation["field_groups"]]
    assert group_ids == ["provenance", "subject", "evidence"]

    declared_paths = [field["field_path"] for field in annotation["fields"]]
    assert declared_paths[0] == "subject.identifier"
    assert group_ids[0] == "provenance"


def test_field_group_with_undeclared_field_path_passes_through(monkeypatch, tmp_path):
    payload = _catalog(monkeypatch, tmp_path)
    annotation = _object_definition(payload, "Annotation")

    declared_paths = {field["field_path"] for field in annotation["fields"]}
    assert "not_a_declared_field" not in declared_paths

    evidence_group = annotation["field_groups"][2]
    assert evidence_group["field_paths"] == [
        "evidence_code_curies",
        "not_a_declared_field",
    ]


def test_field_groups_do_not_change_other_object_keys(monkeypatch, tmp_path):
    payload = _catalog(monkeypatch, tmp_path)
    annotation = _object_definition(payload, "Annotation")

    assert set(annotation) == {
        "object_type",
        "display_name",
        "description",
        "object_role",
        "model_ref",
        "schema_ref",
        "definition_state",
        "definition_notes",
        "provider_refs",
        "validation_attachments",
        "fields",
        "field_groups",
    }
    assert annotation["object_role"] == "curatable_unit"
    assert [field["field_path"] for field in annotation["fields"]] == [
        "subject.identifier",
        "subject.label",
        "evidence_code_curies",
        "data_provider.abbreviation",
    ]
