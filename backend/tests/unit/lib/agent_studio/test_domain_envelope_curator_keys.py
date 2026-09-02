"""Agent Studio envelope metadata passes binding curator keys through (ALL-1026)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import src.lib.agent_studio.domain_envelope_metadata as metadata_module
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry


CURATOR_LABEL = "Confirm the gene identifier in the Alliance records"
WHEN_OFF = (
    "The gene identifier stays as the extractor wrote it, "
    "and review rows show it unconfirmed."
)

PACK_TEXT = f"""
pack_id: fixture.curator_keys
display_name: Fixture Curator Keys Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: GeneAssertionPayload
    display_name: Gene assertion payload
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    model_ref: GeneAssertionPayload
    metadata:
      object_role: curatable_unit
      supervisor_manifest:
        primary_label_field: gene.symbol
        secondary_label_field: gene.identifier
    fields:
      - field_path: gene.identifier
        field_type: string
        required: true
      - field_path: gene.symbol
        field_type: string
metadata:
  validators:
    active:
      - validator_id: fixture.shape
        display_name: Fixture data structure
        curator_label: Confirm each record has the expected structure
        description: Checks that each record has the expected structure.
  validator_bindings:
    active:
      - binding_id: fixture.identifier_lookup
        display_name: Identifier lookup
        curator_label: {CURATOR_LABEL}
        description: Checks the gene identifier against the Alliance records.
        when_off: {WHEN_OFF}
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.curator_keys
          object_types:
            - GeneAssertion
          field_paths:
            - gene.identifier
        input_fields:
          gene_id:
            source: payload
            path: gene.identifier
        expected_result_fields:
          curie: gene.identifier
        required: true
        blocking: false
        allow_opt_out: true
      - binding_id: fixture.symbol_lookup
        display_name: Symbol lookup
        curator_label: Confirm the gene symbol in the Alliance records
        description: Checks the gene symbol against the Alliance records.
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.curator_keys
          object_types:
            - GeneAssertion
        input_fields:
          symbol:
            source: payload
            path: gene.symbol
        expected_result_fields:
          symbol: gene.symbol
        required: true
        blocking: true
        allow_opt_out: false
    under_development: []
""".strip()


def _catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    pack_path = tmp_path / "fixture.curator_keys"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(PACK_TEXT, encoding="utf-8")
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
        lambda: {"fixture.curator_keys": registry},
    )
    catalog = metadata_module.domain_envelope_metadata_catalog_by_agent(
        {"fixture_extractor": {"curation": {"domain_pack_id": "fixture.curator_keys"}}}
    )
    return catalog["fixture_extractor"]


def _by_binding(attachments: list[dict[str, Any]], binding_id: str) -> list[dict[str, Any]]:
    return [a for a in attachments if a.get("validator_binding_id") == binding_id]


def test_envelope_metadata_passes_curator_keys_through(monkeypatch, tmp_path):
    payload = _catalog(monkeypatch, tmp_path)

    top_level = payload["validation_attachments"]
    opt_out = _by_binding(top_level, "fixture.identifier_lookup")
    assert opt_out
    for attachment in opt_out:
        assert attachment["allow_opt_out"] is True
        assert attachment["curator_label"] == CURATOR_LABEL
        assert attachment["when_off"] == WHEN_OFF
        assert attachment["description"] == (
            "Checks the gene identifier against the Alliance records."
        )

    locked = _by_binding(top_level, "fixture.symbol_lookup")
    assert locked
    for attachment in locked:
        assert attachment["allow_opt_out"] is False
        assert attachment["curator_label"] == (
            "Confirm the gene symbol in the Alliance records"
        )
        assert "when_off" not in attachment

    shape = [a for a in top_level if a.get("validator_id") == "fixture.shape"]
    assert len(shape) == 1
    assert shape[0]["curator_label"] == "Confirm each record has the expected structure"
    assert "when_off" not in shape[0]


def test_field_and_object_projections_carry_the_same_keys(monkeypatch, tmp_path):
    payload = _catalog(monkeypatch, tmp_path)
    (annotation,) = [
        d for d in payload["object_definitions"] if d["object_type"] == "GeneAssertion"
    ]

    object_attachments = _by_binding(
        annotation["validation_attachments"], "fixture.symbol_lookup"
    )
    assert object_attachments
    assert object_attachments[0]["curator_label"] == (
        "Confirm the gene symbol in the Alliance records"
    )
    assert "when_off" not in object_attachments[0]

    (identifier_field,) = [
        f for f in annotation["fields"] if f["field_path"] == "gene.identifier"
    ]
    field_attachments = _by_binding(
        identifier_field["validation_attachments"], "fixture.identifier_lookup"
    )
    assert field_attachments
    assert field_attachments[0]["curator_label"] == CURATOR_LABEL
    assert field_attachments[0]["when_off"] == WHEN_OFF
