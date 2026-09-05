"""Unit tests for curator field-path domain-envelope patches."""

from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from uuid import uuid4
import pytest

from src.lib.domain_envelopes.patches import (
    EnvelopeFieldPatch,
    EnvelopeFieldPatchStatus,
    apply_curator_field_patch,
)
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    HistoryEventKind,
)
from src.schemas.domain_pack_metadata import DomainPackMetadata
from src.schemas.generic_extraction_profile import GenericProfileContract
from src.schemas.agent_execution_revision import GenericProfilePin
from src.lib.agent_studio.profile_conformance import ResolvedGenericProfile, ProfileConformanceError, ProfileIdentityError


@pytest.fixture
def profiled_patch_context(tmp_path):
    contract = GenericProfileContract.model_validate({"name": "Records", "semantic_class": "record", "fields": [
        {"key": "details", "value_schema": {"kind": "object", "fields": [
            {"key": "count", "required": True, "value_schema": {"kind": "integer"}},
        ]}},
        {"key": "sources", "value_schema": {"kind": "array", "items": {"kind": "object", "fields": [
            {"key": "name", "value_schema": {"kind": "string"}},
        ]}}},
        {"key": "state", "value_schema": {"kind": "enum", "values": ["new", "known"]}},
    ]})
    pin = GenericProfilePin(profile_id=uuid4(), profile_revision_id=uuid4(), revision=1, fingerprint=contract.fingerprint())
    profile = ResolvedGenericProfile(pin, contract)
    metadata = DomainPackMetadata.model_validate({
        "pack_id": "generic", "display_name": "Generic", "version": "0.1.0",
        "metadata_api_version": "1.0.0", "status": "active",
        "object_definitions": [{"object_type": "generic_object", "display_name": "Object", "fields": [
            {"field_path": "attributes", "field_type": "object", "metadata": {"editable": True}},
        ]}],
    })
    pack = LoadedDomainPack(pack_id="generic", display_name="Generic", version="0.1.0", pack_path=tmp_path,
                            metadata_path=tmp_path / "domain_pack.yaml", metadata=metadata)
    envelope = DomainEnvelope(envelope_id="env-1", domain_pack_id="generic", extracted_objects=[
        CuratableObjectEnvelope(object_type="generic_object", object_id="gene-1", payload={
            "semantic_class": "record", "attributes": {"details": {"count": 1}, "sources": [{"name": "A"}], "state": "new"},
        }, metadata={"generic_profile_ref": pin.model_dump(mode="json")}),
    ])
    return profile, pack, envelope


def test_saved_profile_allows_declared_nested_edit_without_global_pack_path(profiled_patch_context):
    profile, pack, envelope = profiled_patch_context
    before = deepcopy(envelope)
    result = apply_curator_field_patch(envelope, pack, _patch("attributes.sources[0].name", before="A", value="B"),
                                      current_revision=1, actor_id="curator-1", profile=profile)
    assert result.accepted
    assert result.envelope.extracted_objects[0].payload["attributes"]["sources"] == [{"name": "B"}]
    assert envelope == before
    assert result.history_event_ids


@pytest.mark.parametrize("path,before,value", [
    ("attributes.unknown", None, "extra"),
    ("attributes.details.unknown", None, "extra"),
    ("attributes.details.count", 1, "2"),
    ("attributes.details.count", 1, None),
    ("attributes.sources[0].name", "A", ["B"]),
    ("attributes.state", "new", "invalid"),
])
def test_profile_patch_rejects_invalid_nested_values_before_mutation(profiled_patch_context, path, before, value):
    profile, pack, envelope = profiled_patch_context
    original = deepcopy(envelope)
    with pytest.raises(ProfileConformanceError):
        apply_curator_field_patch(envelope, pack, _patch(path, before=before, value=value),
                                  current_revision=1, actor_id="curator-1", profile=profile)
    assert envelope == original
    assert not envelope.history


def test_profile_patch_rejects_mismatched_object_pin(profiled_patch_context):
    profile, pack, envelope = profiled_patch_context
    envelope.extracted_objects[0].metadata["generic_profile_ref"]["profile_revision_id"] = str(uuid4())
    with pytest.raises(ProfileIdentityError):
        apply_curator_field_patch(envelope, pack, _patch("attributes.details.count", before=1, value=2),
                                  current_revision=1, actor_id="curator-1", profile=profile)


def _pack_text() -> str:
    return """
pack_id: fixture.curator_patch
display_name: Fixture Curator Patch Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    fields:
      - field_path: gene.symbol
        field_type: string
        metadata:
          editable: true
      - field_path: gene.identifier
        field_type: string
        metadata:
          editable: true
      - field_path: protected_note
        field_type: string
        metadata:
          protected: true
      - field_path: stable_note
        field_type: string
""".strip()


def _loaded_pack(tmp_path: Path) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.curator_patch"
    pack_path.mkdir(exist_ok=True)
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(_pack_text(), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


def _envelope() -> DomainEnvelope:
    return DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.curator_patch",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_id="gene-1",
                payload={
                    "gene": {"symbol": "abc-1"},
                    "protected_note": "do not edit",
                    "stable_note": "fixed",
                },
            )
        ],
    )


def _patch(field_path: str, *, before: object, value: object) -> EnvelopeFieldPatch:
    return EnvelopeFieldPatch(
        patch_id="curator-field-patch:test",
        envelope_id="env-1",
        expected_revision=1,
        object_id="gene-1",
        field_path=field_path,
        before=before,
        value=value,
        reason="Curator corrected the field.",
    )


def test_apply_curator_field_patch_accepts_editable_field_and_records_history(tmp_path: Path):
    result = apply_curator_field_patch(
        _envelope(),
        _loaded_pack(tmp_path),
        _patch("gene.symbol", before="abc-1", value="abc-2"),
        current_revision=1,
        actor_id="curator-1",
    )

    assert result.accepted is True
    assert result.status is EnvelopeFieldPatchStatus.ACCEPTED
    assert result.before == "abc-1"
    assert result.after == "abc-2"
    assert result.envelope.extracted_objects[0].payload["gene"]["symbol"] == "abc-2"
    assert [event.event_type for event in result.envelope.history] == [
        HistoryEventKind.FIELD_UPDATED,
        HistoryEventKind.CURATOR_FIELD_PATCH_ACCEPTED,
    ]
    assert result.envelope.history[-1].actor_id == "curator-1"


def test_apply_curator_field_patch_allows_editable_field_fill(tmp_path: Path):
    result = apply_curator_field_patch(
        _envelope(),
        _loaded_pack(tmp_path),
        _patch("gene.identifier", before=None, value="AGR:0000001"),
        current_revision=1,
        actor_id="curator-1",
    )

    assert result.accepted is True
    assert result.envelope.extracted_objects[0].payload["gene"]["identifier"] == "AGR:0000001"


def test_apply_curator_field_patch_rejects_stale_revision_without_history(tmp_path: Path):
    result = apply_curator_field_patch(
        _envelope(),
        _loaded_pack(tmp_path),
        _patch("gene.symbol", before="abc-1", value="abc-2"),
        current_revision=2,
        actor_id="curator-1",
    )

    assert result.status is EnvelopeFieldPatchStatus.STALE_REVISION
    assert result.envelope.history == []
    assert "expected_revision 1 does not match current revision 2" in result.errors[0]


def test_apply_curator_field_patch_rejects_before_mismatch_and_records_rejection(
    tmp_path: Path,
):
    result = apply_curator_field_patch(
        _envelope(),
        _loaded_pack(tmp_path),
        _patch("gene.symbol", before="stale", value="abc-2"),
        current_revision=1,
        actor_id="curator-1",
    )

    assert result.status is EnvelopeFieldPatchStatus.REJECTED
    assert result.envelope.extracted_objects[0].payload["gene"]["symbol"] == "abc-1"
    assert result.envelope.history[-1].event_type is (
        HistoryEventKind.CURATOR_FIELD_PATCH_REJECTED
    )
    assert "before does not match current value" in result.errors[0]


def test_apply_curator_field_patch_rejects_protected_and_undeclared_paths(
    tmp_path: Path,
):
    protected = apply_curator_field_patch(
        _envelope(),
        _loaded_pack(tmp_path),
        _patch("protected_note", before="do not edit", value="new"),
        current_revision=1,
        actor_id="curator-1",
    )
    undeclared = apply_curator_field_patch(
        _envelope(),
        _loaded_pack(tmp_path),
        _patch("missing.path", before=None, value="new"),
        current_revision=1,
        actor_id="curator-1",
    )
    stable = apply_curator_field_patch(
        _envelope(),
        _loaded_pack(tmp_path),
        _patch("stable_note", before="fixed", value="new"),
        current_revision=1,
        actor_id="curator-1",
    )

    assert protected.status is EnvelopeFieldPatchStatus.REJECTED
    assert "protected" in protected.errors[0]
    assert undeclared.status is EnvelopeFieldPatchStatus.REJECTED
    assert "not declared" in undeclared.errors[0]
    assert stable.status is EnvelopeFieldPatchStatus.REJECTED
    assert "not declared editable" in stable.errors[0]
