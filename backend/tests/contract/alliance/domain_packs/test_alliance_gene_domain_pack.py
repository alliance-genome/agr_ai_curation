"""Contract tests for the Alliance gene validated-reference domain pack."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from src.lib.domain_packs.loader import (
    load_domain_fixture_pack,
    load_domain_pack_metadata,
)
from src.schemas.curation_workspace import SubmissionMode
from src.schemas.domain_pack_metadata import DomainPackFieldType

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    OBJECT_ROLE_METADATA_KEY,
    PROVIDER_REFS_METADATA_KEY,
    get_alliance_domain_packs_dir,
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.gene import (  # noqa: E402
    GENE_DOMAIN_PACK_ID,
    GENE_DOMAIN_PACK_VERSION,
    GENE_LINKML_SCHEMA_ID,
    GENE_MENTION_EVIDENCE_MODEL_ID,
    GENE_MENTION_EVIDENCE_OBJECT_TYPE,
    GENE_REFERENCE_VALIDATOR_BINDING_ID,
    GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY,
    GeneMentionEvidenceExportAdapter,
    build_gene_mention_evidence_export,
    build_gene_mention_evidence_submission_plan,
    tool_verified_gene_output_to_pending_envelope,
)

GENE_PACK_DIR = get_alliance_domain_packs_dir() / GENE_DOMAIN_PACK_ID
GENE_PACK_METADATA_PATH = GENE_PACK_DIR / "domain_pack.yaml"
GENE_VALIDATOR_PROMPT_PATH = (
    REPO_ROOT / "packages" / "alliance" / "agents" / "gene" / "prompt.yaml"
)
GENE_RAW_FIXTURE_PATH = (
    REPO_ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "domain_packs"
    / "gene"
    / "tool_verified_gene_output.yaml"
)
LEGACY_SEMANTIC_KEYS = {
    "items",
    "annotations",
    "genes",
    "alleles",
    "diseases",
    "chemicals",
    "phenotypes",
    "normalized_payload",
    "annotation_drafts",
}


def _provider_ref(metadata: dict[str, Any]) -> dict[str, Any]:
    return metadata[PROVIDER_REFS_METADATA_KEY][ALLIANCE_LINKML_PROVIDER_KEY]


def _gene_object_definition():
    metadata = load_domain_pack_metadata(GENE_PACK_METADATA_PATH)
    return next(
        item
        for item in metadata.object_definitions
        if item.object_type == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    )


def _load_raw_gene_fixture() -> dict[str, Any]:
    return yaml.safe_load(GENE_RAW_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_gene_domain_pack_loads_from_alliance_registry():
    registry = load_alliance_domain_pack_registry()
    loaded_pack = registry.get_pack(GENE_DOMAIN_PACK_ID)

    assert registry.failed_packs == ()
    assert loaded_pack is not None
    assert loaded_pack.metadata_path == GENE_PACK_METADATA_PATH
    assert loaded_pack.metadata.version == GENE_DOMAIN_PACK_VERSION

    fixture_ref = registry.get_fixture_pack_ref(GENE_DOMAIN_PACK_ID, "tool_verified")
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/tool_verified.yaml"


def test_gene_mention_evidence_is_exporting_validated_reference():
    object_definition = _gene_object_definition()
    object_metadata = object_definition.metadata

    assert object_definition.model_ref == GENE_MENTION_EVIDENCE_MODEL_ID
    assert object_definition.definition_state.value == "stable"
    assert object_metadata[OBJECT_ROLE_METADATA_KEY] == "validated_reference"
    assert object_metadata["evidence_role"] == GENE_MENTION_EVIDENCE_OBJECT_TYPE
    assert object_metadata["blocking_validation"] is False

    export_behavior = object_metadata["export_behavior"]
    assert export_behavior["status"] == "ready"
    assert export_behavior["mode"] == "validated_reference_evidence"
    assert export_behavior["target_key"] == GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY
    assert export_behavior["exportable"] is True
    assert export_behavior["mutates_base_gene"] is False
    assert export_behavior["creates_paper_gene_association"] is False

    write_behavior = object_metadata["write_behavior"]
    assert write_behavior["mode"] == "none"
    assert write_behavior["write_target"] == "none"
    assert write_behavior["mutate_canonical_gene"] is False
    assert write_behavior["creates_paper_gene_association"] is False

    workspace_display = object_metadata["workspace_display"]
    assert workspace_display["primary_label_field"] == "mention"
    assert workspace_display["secondary_label_field"] == "gene_symbol"
    assert workspace_display["evidence_quote_field"] == "verified_quote"


def test_gene_validator_prompt_matches_scalar_materialization_contract():
    metadata = load_domain_pack_metadata(GENE_PACK_METADATA_PATH)
    prompt = yaml.safe_load(GENE_VALIDATOR_PROMPT_PATH.read_text(encoding="utf-8"))
    prompt_content = prompt["content"]
    object_definition = _gene_object_definition()
    binding = object_definition.metadata["validator_bindings"]["active"][0]

    assert binding["binding_id"] == GENE_REFERENCE_VALIDATOR_BINDING_ID
    assert binding["expected_result_fields"] == {
        "curie": "primary_external_id",
        "symbol": "gene_symbol",
        "taxon": "taxon",
    }
    assert "resolved_values" in prompt_content
    assert "optional diagnostic lookup context only" in prompt_content
    assert "does not create a separate Gene object" in prompt_content
    assert not any(
        item.object_type == "Gene"
        for item in metadata.object_definitions
    )


def test_gene_pack_declares_validatable_linkml_grounded_gene_fields():
    object_definition = _gene_object_definition()
    fields_by_path = {field.field_path: field for field in object_definition.fields}

    assert {
        "mention",
        "primary_external_id",
        "gene_symbol",
        "taxon",
        "taxon_hint",
        "data_provider_hint",
        "proposed_primary_external_id",
        "proposed_gene_symbol",
        "proposed_taxon",
        "identity_resolution_notes",
        "confidence",
        "evidence_record_id",
        "verified_quote",
        "page",
        "section",
        "chunk_id",
    }.issubset(fields_by_path)
    assert fields_by_path["confidence"].field_type is DomainPackFieldType.ENUM
    assert fields_by_path["confidence"].enum_ref == "GeneMentionConfidence"

    expected_linkml_slots = {
        "primary_external_id": (
            "model/schema/core.yaml",
            "primary_external_id",
            "string",
        ),
        "gene_symbol": (
            "model/schema/gene.yaml",
            "gene_symbol",
            "GeneSymbolSlotAnnotation",
        ),
        "taxon": ("model/schema/core.yaml", "taxon", "NCBITaxonTerm"),
    }
    validatable_fields = set()
    for field_path, (source_file, slot, range_name) in expected_linkml_slots.items():
        field = fields_by_path[field_path]
        validatable_fields.add(field.field_path)
        assert field.required is False
        assert field.metadata["validatable"] is True
        assert (
            field.metadata["validator_binding_id"]
            == GENE_REFERENCE_VALIDATOR_BINDING_ID
        )

        provider_ref = _provider_ref(field.metadata)
        assert provider_ref["commit"] == ALLIANCE_LINKML_COMMIT
        assert provider_ref["source_file"] == source_file
        assert provider_ref["class"] == "Gene"
        assert provider_ref["slot"] == slot
        assert provider_ref["range"] == range_name

    assert validatable_fields == {"primary_external_id", "gene_symbol", "taxon"}


def test_gene_pack_declares_reference_validator_binding():
    object_definition = _gene_object_definition()
    bindings = object_definition.metadata["validator_bindings"]

    assert bindings["under_development"] == []
    binding = bindings["active"][0]
    assert binding["binding_id"] == GENE_REFERENCE_VALIDATOR_BINDING_ID
    assert binding["display_name"] == "Alliance gene lookup"
    assert binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "gene_validation",
    }
    assert binding["applies_to"] == {
        "domain_pack_id": "gene",
        "object_types": ["gene_mention_evidence"],
        "object_roles": [],
        "field_paths": [],
        "field_types": [],
    }
    assert binding["input_fields"] == {
        "mention": {
            "source": "payload",
            "path": "mention",
            "required": True,
        },
        "proposed_gene_id": {
            "source": "payload",
            "path": "proposed_primary_external_id",
            "required": False,
        },
        "proposed_symbol": {
            "source": "payload",
            "path": "proposed_gene_symbol",
            "required": False,
        },
        "proposed_taxon": {
            "source": "payload",
            "path": "proposed_taxon",
            "required": False,
        },
        "taxon_hint": {
            "source": "payload",
            "path": "taxon_hint",
            "required": False,
        },
        "data_provider_hint": {
            "source": "payload",
            "path": "data_provider_hint",
            "required": False,
        },
        "species": {
            "source": "payload",
            "path": "species",
            "required": False,
        },
        "evidence_quote": {
            "source": "payload",
            "path": "verified_quote",
            "required": False,
        },
        "identity_resolution_notes": {
            "source": "payload",
            "path": "identity_resolution_notes",
            "required": False,
            "allow_multiple": True,
        },
    }
    assert binding["expected_result_fields"] == {
        "curie": "primary_external_id",
        "symbol": "gene_symbol",
        "taxon": "taxon",
    }
    assert binding["required"] is True
    assert binding["blocking"] is False
    assert binding["allow_opt_out"] is True
    assert binding["batch"] == {
        "enabled": False,
        "family": GENE_REFERENCE_VALIDATOR_BINDING_ID,
    }
    assert binding["curator_override"] == {"allowed": False}


def test_tool_verified_gene_fixture_converts_to_pending_envelope():
    raw_fixture = _load_raw_gene_fixture()
    converted_envelope = tool_verified_gene_output_to_pending_envelope(raw_fixture)

    fixture_ref = load_alliance_domain_pack_registry().get_fixture_pack_ref(
        GENE_DOMAIN_PACK_ID,
        "tool_verified",
    )
    assert fixture_ref is not None
    fixture_pack = load_domain_fixture_pack(GENE_PACK_DIR / fixture_ref.path)
    expected_envelope = fixture_pack.fixtures[0].envelope

    assert converted_envelope.model_dump(mode="json", exclude_none=True) == (
        expected_envelope.model_dump(mode="json", exclude_none=True)
    )
    assert converted_envelope.domain_pack_id == GENE_DOMAIN_PACK_ID
    assert converted_envelope.schema_ref.schema_id == GENE_LINKML_SCHEMA_ID
    assert converted_envelope.objects[0].pending_ref_id == "gene-mention-evidence-1"
    assert converted_envelope.objects[0].definition_state.value == "stable"
    assert converted_envelope.objects[0].metadata[OBJECT_ROLE_METADATA_KEY] == (
        "validated_reference"
    )
    assert converted_envelope.validation_findings[0].severity.value == "info"
    assert converted_envelope.validation_findings[0].details["blocking"] is False


def test_converted_gene_envelope_omits_legacy_semantic_stores():
    raw_fixture = _load_raw_gene_fixture()
    converted_envelope = tool_verified_gene_output_to_pending_envelope(raw_fixture)

    assert LEGACY_SEMANTIC_KEYS.isdisjoint(converted_envelope.metadata)
    for obj in converted_envelope.objects:
        assert LEGACY_SEMANTIC_KEYS.isdisjoint(obj.payload)
        assert LEGACY_SEMANTIC_KEYS.isdisjoint(obj.metadata)


def test_tool_verified_gene_fixture_requires_extractor_confidence():
    raw_fixture = _load_raw_gene_fixture()
    del raw_fixture["gene_mentions"][0]["confidence"]

    with pytest.raises(ValidationError, match="confidence"):
        tool_verified_gene_output_to_pending_envelope(raw_fixture)


def test_tool_verified_gene_fixture_rejects_blank_normalization_notes():
    raw_fixture = _load_raw_gene_fixture()
    raw_fixture["normalization_notes"] = [
        "Resolved against current Alliance Gene row.",
        "  ",
    ]

    with pytest.raises(ValidationError, match="normalization_notes"):
        tool_verified_gene_output_to_pending_envelope(raw_fixture)


def test_gene_mention_evidence_exports_validated_reference_evidence_payload():
    raw_fixture = _load_raw_gene_fixture()
    envelope = tool_verified_gene_output_to_pending_envelope(raw_fixture)

    payload = build_gene_mention_evidence_export(envelope)

    assert payload["export_type"] == "alliance_gene_validated_reference_evidence"
    assert payload["write_behavior"] == {
        "mode": "non_mutating_validated_reference_evidence",
        "mutates_base_gene": False,
        "creates_paper_gene_association": False,
        "write_targets": [],
    }
    assert len(payload["records"]) == 1
    record = payload["records"][0]
    assert record["validated_reference"] == {
        "mention": "daf-16",
        "primary_external_id": "WB:WBGene00000912",
        "gene_symbol": "daf-16",
        "taxon": "NCBITaxon:6239",
        "confidence": "high",
        "species": "Caenorhabditis elegans",
    }
    assert record["evidence"]["evidence_record_id"] == "ev-daf16-1"
    assert record["write_behavior"] == {
        "mutates_base_gene": False,
        "creates_paper_gene_association": False,
        "write_target": None,
    }


def test_gene_export_adapter_rehydrates_selected_domain_envelope_snapshot():
    raw_fixture = _load_raw_gene_fixture()
    envelope = tool_verified_gene_output_to_pending_envelope(raw_fixture)
    selected_object_id = envelope.objects[0].pending_ref_id
    assert selected_object_id is not None
    snapshot = envelope.model_dump(mode="json")
    snapshot["selected_object_ids"] = [selected_object_id]
    adapter = GeneMentionEvidenceExportAdapter()

    payload = adapter.build_submission_payload(
        mode=SubmissionMode.EXPORT,
        target_key=GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY,
        payload_context={
            "session_id": "session-1",
            "candidate_ids": ["candidate-1"],
            "candidate_count": 1,
            "domain_envelope_candidates": [{"candidate_id": "candidate-1"}],
            "domain_envelopes": [snapshot],
        },
    )

    assert payload.payload_json is not None
    assert payload.payload_json["record_count"] == 1
    assert payload.payload_json["records"][0]["object_id"] == selected_object_id


def test_gene_submission_plan_is_non_mutating_and_has_no_paper_gene_target():
    raw_fixture = _load_raw_gene_fixture()
    envelope = tool_verified_gene_output_to_pending_envelope(raw_fixture)

    plan = build_gene_mention_evidence_submission_plan(envelope)

    assert plan["status"] == "ready"
    assert plan["submission_kind"] == "validated_reference_evidence"
    assert plan["record_count"] == 1
    assert plan["write_targets"] == []
    assert plan["blocked_targets"] == []
    assert plan["mutations"] == {
        "public.gene": False,
        "paper_gene_association": False,
    }
