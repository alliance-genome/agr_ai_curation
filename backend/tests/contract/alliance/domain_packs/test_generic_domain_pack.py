"""Contract tests for generic PDF extraction domain-pack generation."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
from src.lib.curation_workspace.extraction_results import ExtractionEnvelopeCandidate
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBinding,
)
from src.lib.flows.output_projection import (
    apply_projection_plan,
    build_flow_output_artifact_bundle,
    default_projection_plan,
)
from src.lib.openai_agents.extraction_builder_workspace import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderWorkspace,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackObjectDefinition,
)

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.generic import (  # noqa: E402
    GENERIC_DOMAIN_PACK_ID,
    GENERIC_MATERIALIZER_ID,
    GENERIC_OBJECT_TYPE,
    GenericBuilderExtractionOutput,
    GenericClassCatalog,
    get_generated_generic_domain_pack,
    load_generic_class_catalog,
    materialize_generic_builder_state,
    proxy_object_type,
)
from agr_ai_curation_alliance.domain_packs.generic.catalog import (  # noqa: E402
    _binding_applies_to_object,
    _proxy_field_definition,
)
from agr_ai_curation_alliance.tools.builder_finalization import (  # noqa: E402
    finalize_builder_extraction,
)

BINDINGS_PATH = REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"
PDF_AGENT_PATH = REPO_ROOT / "packages" / "alliance" / "agents" / "pdf" / "agent.yaml"


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-generic-1",
            "entity": "TRiP.HMS00001",
            "verified_quote": "The screen used the TRiP.HMS00001 RNAi reagent.",
            "page": 6,
            "section": "Methods",
            "subsection": "RNAi screen",
            "chunk_id": "chunk-generic-1",
        }
    ]


def _generic_workspace(staged_fields: Mapping[str, Any]) -> ExtractionBuilderWorkspace:
    workspace = ExtractionBuilderWorkspace(
        run_id="generic-builder-test-run",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        agent_id="pdf_extraction",
    )
    workspace.upsert_candidate(
        candidate_id="generic-candidate-1",
        staged_fields=dict(staged_fields),
        pending_ref_ids=["generic-object-1"],
        evidence_record_ids=["evidence-generic-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return workspace


def test_generic_catalog_derives_stageable_classes_from_domain_pack_metadata():
    catalog = load_generic_class_catalog()
    class_keys = {entry.class_key for entry in catalog.entries if entry.stageable}
    assert "generic:generic_object" in class_keys
    assert "generic:generic_reagent_candidate" in class_keys
    assert "gene:gene_mention_evidence" in class_keys

    gene_entry = catalog.entries_by_class_key["gene:gene_mention_evidence"]
    assert gene_entry.generic_object_type == proxy_object_type(
        "gene", "gene_mention_evidence"
    )
    assert gene_entry.source_is_generic_native is False
    assert gene_entry.validator_state == "active"
    assert [
        binding.binding_id for binding in gene_entry.active_validator_bindings
    ] == ["alliance_gene_reference_lookup"]
    assert "mention" in gene_entry.payload_fields
    assert "identity_resolution_notes" in gene_entry.required_payload_fields


def test_generated_generic_domain_pack_reuses_existing_validator_bindings():
    pack = get_generated_generic_domain_pack()
    proxy_type = proxy_object_type("gene", "gene_mention_evidence")
    object_definitions = {
        object_definition.object_type: object_definition
        for object_definition in pack.metadata.object_definitions
    }
    assert GENERIC_OBJECT_TYPE in object_definitions
    assert proxy_type in object_definitions

    proxy_definition = object_definitions[proxy_type]
    assert proxy_definition.model_ref is None
    confidence_field = next(
        field for field in proxy_definition.fields if field.field_path == "confidence"
    )
    assert confidence_field.enum_ref is None
    assert (
        confidence_field.metadata["generic_extraction_proxy_source_refs"]["enum_ref"]
        == "GeneMentionConfidence"
    )

    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    active_bindings = [
        binding
        for binding in registry.bindings
        if binding.state is ValidationBindingState.ACTIVE
    ]
    assert [binding.binding_id for binding in active_bindings] == [
        "proxy__gene__gene_mention_evidence__alliance_gene_reference_lookup"
    ]
    assert active_bindings[0].applies_to_domain_pack_id == GENERIC_DOMAIN_PACK_ID
    assert active_bindings[0].object_types == (proxy_type,)


def test_generated_generic_binding_applicability_reuses_role_and_field_targets():
    object_definition = DomainPackObjectDefinition(
        object_type="fixture_stageable",
        display_name="Fixture stageable",
        metadata={"object_role": "fixture_role"},
        fields=[
            DomainPackFieldDefinition(
                field_path="symbol",
                field_type=DomainPackFieldType.STRING,
                metadata={
                    "validator_bindings": {
                        "active": [
                            {
                                "binding_id": "source_field_binding",
                                "validator_agent": {
                                    "package_id": "fixture",
                                    "agent_id": "fixture_validator",
                                },
                                "applies_to": {
                                    "domain_pack_id": "fixture",
                                    "field_paths": ["symbol"],
                                },
                            }
                        ]
                    }
                },
            ),
            DomainPackFieldDefinition(
                field_path="confidence",
                field_type=DomainPackFieldType.STRING,
            ),
        ],
    )
    role_binding = ValidatorBinding(
        binding_id="role_binding",
        state=ValidationBindingState.ACTIVE,
        source_scope="object",
        applies_to_domain_pack_id="fixture",
        object_roles=("fixture_role",),
    )
    field_path_binding = ValidatorBinding(
        binding_id="field_path_binding",
        state=ValidationBindingState.ACTIVE,
        source_scope="field",
        applies_to_domain_pack_id="fixture",
        field_paths=("symbol",),
    )
    field_type_binding = ValidatorBinding(
        binding_id="field_type_binding",
        state=ValidationBindingState.ACTIVE,
        source_scope="field",
        applies_to_domain_pack_id="fixture",
        field_types=(DomainPackFieldType.STRING,),
    )
    wrong_role_binding = ValidatorBinding(
        binding_id="wrong_role_binding",
        state=ValidationBindingState.ACTIVE,
        source_scope="object",
        applies_to_domain_pack_id="fixture",
        object_roles=("other_role",),
    )

    assert _binding_applies_to_object(
        role_binding,
        source_pack_id="fixture",
        object_definition=object_definition,
    )
    assert _binding_applies_to_object(
        field_path_binding,
        source_pack_id="fixture",
        object_definition=object_definition,
    )
    assert _binding_applies_to_object(
        field_type_binding,
        source_pack_id="fixture",
        object_definition=object_definition,
    )
    assert not _binding_applies_to_object(
        wrong_role_binding,
        source_pack_id="fixture",
        object_definition=object_definition,
    )


def test_proxy_field_definition_strips_unproxied_field_validator_metadata():
    field_definition = DomainPackFieldDefinition(
        field_path="symbol",
        field_type=DomainPackFieldType.STRING,
        metadata={
            "validator_bindings": {
                "active": [
                    {
                        "binding_id": "source_field_binding",
                        "validator_agent": {
                            "package_id": "fixture",
                            "agent_id": "fixture_validator",
                        },
                        "applies_to": {
                            "domain_pack_id": "fixture",
                            "field_paths": ["symbol"],
                        },
                    }
                ]
            },
            "display": {"compact": True},
        },
    )

    proxy_field = _proxy_field_definition(field_definition)

    assert "validator_bindings" not in proxy_field.metadata
    assert proxy_field.metadata["display"] == {"compact": True}


def test_generated_generic_validator_dispatch_builds_source_validator_request():
    pack = get_generated_generic_domain_pack()
    proxy_type = proxy_object_type("gene", "gene_mention_evidence")
    envelope = DomainEnvelope(
        envelope_id="generic-gene-validator-fixture",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        objects=[
            CuratableObjectEnvelope(
                object_type=proxy_type,
                pending_ref_id="generic-gene-1",
                object_role="generic_proxy_object",
                payload={
                    "mention": "daf-16",
                    "identity_resolution_notes": [
                        "The paper reports daf-16 in C. elegans."
                    ],
                    "species": "Caenorhabditis elegans",
                    "taxon_hint": "NCBITaxon:6239",
                    "data_provider_hint": "WB",
                },
            )
        ],
    )
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    matches = registry.match_bindings(
        envelope,
        states=[ValidationBindingState.ACTIVE],
    )
    assert len(matches) == 1

    request = build_domain_validation_request(matches[0]).request
    assert request is not None
    assert request.target.domain_pack_id == GENERIC_DOMAIN_PACK_ID
    assert request.validator_binding_id == (
        "proxy__gene__gene_mention_evidence__alliance_gene_reference_lookup"
    )
    assert request.selected_inputs["mention"] == "daf-16"
    assert request.selected_inputs["species"] == "Caenorhabditis elegans"
    assert request.selected_inputs["identity_resolution_notes"] == [
        "The paper reports daf-16 in C. elegans."
    ]


def test_generic_builder_materializer_requires_explicit_class_key_and_label():
    missing_class_workspace = _generic_workspace(
        {
            "label": "TRiP.HMS00001",
            "classification_notes": ["The source table labels this as an RNAi reagent."],
        }
    )
    missing_class_result = materialize_generic_builder_state(
        workspace=missing_class_workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not missing_class_result.ok
    assert any(
        issue["reason"] == "missing_class_key"
        for issue in missing_class_result.issues
    )

    missing_label_workspace = _generic_workspace(
        {
            "class_key": "generic:generic_reagent_candidate",
            "classification_notes": ["The source table labels this as an RNAi reagent."],
        }
    )
    missing_label_result = materialize_generic_builder_state(
        workspace=missing_label_workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not missing_label_result.ok
    assert any(issue["reason"] == "missing_label" for issue in missing_label_result.issues)


def test_explicit_generic_object_class_materializes_without_fallback():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_object",
            "label": "TRiP.HMS00001",
            "source_label": "TRiP.HMS00001",
            "description": "RNAi reagent mentioned in the paper.",
            "classification_notes": [
                "The paper calls this a reagent but no more specific class is needed."
            ],
            "attributes": {"source_identifier": "TRiP.HMS00001"},
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert result.ok, result.summary()
    assert result.payload is not None

    output = GenericBuilderExtractionOutput.model_validate(result.payload)
    obj = output.curatable_objects[0]
    assert obj.object_type == GENERIC_OBJECT_TYPE
    assert obj.payload["class_key"] == "generic:generic_object"
    assert obj.payload["label"] == "TRiP.HMS00001"
    assert obj.payload["attributes"]["source_identifier"] == "TRiP.HMS00001"
    assert obj.evidence_record_ids == ["evidence-generic-1"]
    assert obj.metadata["generic_extraction"]["class_key"] == "generic:generic_object"
    assert result.payload["metadata"]["provenance"]["source"] == GENERIC_MATERIALIZER_ID
    assert "items" not in result.payload
    assert "raw_mentions" not in result.payload


def test_generic_materializer_enforces_required_class_payload_fields():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_claim",
            "label": "principal finding",
            "classification_notes": ["This is a paper-level result claim."],
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )

    assert not result.ok
    assert any(
        issue["reason"] == "missing_required_payload_field"
        and issue["field_path"] == "payload.claim_text"
        for issue in result.issues
    )


def test_generic_materializer_rejects_payload_keys_outside_selected_class():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_reagent_candidate",
            "label": "TRiP.HMS00001",
            "classification_notes": ["The source table labels this as an RNAi reagent."],
            "payload": {"source_identifer": "typo"},
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )

    assert not result.ok
    assert any(
        issue["reason"] == "unknown_payload_field"
        and issue["field_path"] == "payload.source_identifer"
        for issue in result.issues
    )


def test_generic_materializer_allows_empty_no_result_extraction():
    workspace = ExtractionBuilderWorkspace(
        run_id="generic-empty-builder-test-run",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        agent_id="pdf_extraction",
    )

    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=[],
        evidence_records=[],
        resolver_entry_lookup=None,
    )

    assert result.ok, result.summary()
    assert result.payload is not None
    output = GenericBuilderExtractionOutput.model_validate(result.payload)
    assert output.curatable_objects == []
    assert output.run_summary.candidate_count == 0
    assert output.run_summary.kept_count == 0


def test_generic_builder_finalization_projects_to_object_tsv_rows():
    workspace = ExtractionBuilderWorkspace(
        run_id="generic-builder-tsv-regression",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        agent_id="pdf_extraction",
    )
    evidence_records = [
        {
            "evidence_record_id": "evidence-generic-1",
            "verified_quote": "Ck:GFP was used as a genetic reagent.",
        },
        {
            "evidence_record_id": "evidence-generic-2",
            "verified_quote": "Actn RNAi was used as a genetic reagent.",
        },
    ]
    for index, (candidate_id, label, source, count, evidence_id) in enumerate(
        [
            ("generic-candidate-1", "Ck:GFP", "This study", 4, "evidence-generic-1"),
            (
                "generic-candidate-2",
                "Actn RNAi",
                "Source not found",
                2,
                "evidence-generic-2",
            ),
        ],
        start=1,
    ):
        workspace.upsert_candidate(
            candidate_id=candidate_id,
            staged_fields={
                "class_key": "generic:generic_reagent_candidate",
                "label": label,
                "classification_notes": ["The prompt asked for a reagent inventory."],
                "payload": {
                    "source": source,
                    "source_identifier": "New in paper" if index == 1 else "Not found",
                    "count": count,
                },
            },
            pending_ref_ids=[f"generic-object-{index}"],
            evidence_record_ids=[evidence_id],
            resolver_selection_refs=[],
            status=CANDIDATE_STATUS_VALID,
        )

    outcome = finalize_builder_extraction(
        workspace=workspace,
        candidate_ids=["generic-candidate-1", "generic-candidate-2"],
        materialize=materialize_generic_builder_state,
        evidence_records=evidence_records,
        resolver_entry_lookup=None,
        materialized_candidate_prefix="generic-envelope",
        require_evidence_record_ids=True,
        require_resolver_selections=False,
    )

    assert outcome.ok, outcome.issues
    assert outcome.finalization is not None
    completed_step = {
        "step": 1,
        "agent_id": "pdf_extraction",
        "agent_name": "General PDF Extraction Agent",
        "tool_name": "ask_pdf_specialist",
        "output_preview": "Builder finalized generic reagents.",
        "candidate": ExtractionEnvelopeCandidate(
            agent_key="pdf_extraction",
            adapter_key="generic",
            candidate_count=2,
            payload_json=outcome.finalization.payload,
            conversation_summary="Extracted two generic reagents.",
            metadata={
                "flow_id": "flow-gillian-regression",
                "step": 1,
                "tool_name": "ask_pdf_specialist",
            },
        ),
    }
    bundle = build_flow_output_artifact_bundle(
        completed_steps=[completed_step],
        flow_name="Gillian Regression Flow",
        output_format="tsv",
    )
    result = apply_projection_plan(
        bundle,
        default_projection_plan(bundle, output_format="tsv"),
    )

    assert result.row_source == "object"
    assert result.total_count == 2
    assert "artifact_preview" not in [column.key for column in result.columns]
    assert [row["object_payload_label"] for row in result.rows] == ["Ck:GFP", "Actn RNAi"]


def test_generic_proxy_materializer_hydrates_required_evidence_fields_and_schema_ref():
    proxy_type = proxy_object_type("gene", "gene_mention_evidence")
    workspace = _generic_workspace(
        {
            "class_key": "gene:gene_mention_evidence",
            "label": "daf-16",
            "confidence": "high",
            "classification_notes": ["The paper-backed mention is a gene symbol."],
            "payload": {
                "identity_resolution_notes": [
                    "The paper reports this symbol in C. elegans."
                ],
                "species": "Caenorhabditis elegans",
                "taxon_hint": "NCBITaxon:6239",
                "data_provider_hint": "WB",
            },
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=[
            {
                "evidence_record_id": "evidence-generic-1",
                "entity": "daf-16",
                "verified_quote": "DAF-16 translocated to nuclei after heat shock.",
                "page": 4,
                "section": "Results",
                "chunk_id": "chunk-daf16-1",
            }
        ],
        resolver_entry_lookup=None,
    )

    assert result.ok, result.summary()
    assert result.payload is not None
    obj = result.payload["curatable_objects"][0]
    assert obj["object_type"] == proxy_type
    assert obj["schema_ref"]["schema_id"] == "alliance.linkml.Gene"
    assert obj["payload"]["mention"] == "daf-16"
    assert obj["payload"]["evidence_record_id"] == "evidence-generic-1"
    assert obj["payload"]["verified_quote"] == (
        "DAF-16 translocated to nuclei after heat shock."
    )
    assert obj["payload"]["page"] == 4
    assert obj["payload"]["section"] == "Results"
    assert obj["payload"]["chunk_id"] == "chunk-daf16-1"
    payload_fields = {
        field_definition.field_path
        for object_definition in get_generated_generic_domain_pack().metadata.object_definitions
        if object_definition.object_type == proxy_type
        for field_definition in object_definition.fields
    }
    assert set(obj["payload"]).issubset(payload_fields)
    assert "label" not in obj["payload"]
    assert "class_key" not in obj["payload"]
    assert "semantic_class" not in obj["payload"]
    assert "classification_notes" not in obj["payload"]
    assert obj["metadata"]["generic_extraction"]["label"] == "daf-16"


def test_unknown_generic_class_key_is_rejected_not_silently_fallbacked():
    workspace = _generic_workspace(
        {
            "class_key": "unknown:thing",
            "label": "TRiP.HMS00001",
            "classification_notes": ["This deliberately uses an unknown class key."],
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(issue["reason"] == "invalid_class_key" for issue in result.issues)


def test_generic_adapter_resolves_generated_domain_pack_view():
    pack = resolve_curation_domain_pack_by_id(GENERIC_DOMAIN_PACK_ID)
    assert pack is not None
    proxy_type = proxy_object_type("gene", "gene_mention_evidence")
    assert any(
        object_definition.object_type == proxy_type
        for object_definition in pack.metadata.object_definitions
    )


def test_finalize_generic_extraction_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {
        entry["tool_id"]: entry
        for entry in bindings["tools"]
        if isinstance(entry, Mapping) and "tool_id" in entry
    }
    finalize = by_id["finalize_generic_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    assert finalize["callable"] == (
        "agr_ai_curation_alliance.tools.generic_builder_tools:"
        "finalize_generic_extraction"
    )
    assert "list_generic_object_classes" in by_id
    for tool_id in (
        "stage_generic_object",
        "patch_generic_object",
        "discard_generic_object",
        "list_staged_generic_objects",
        "find_staged_generic_objects",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_pdf_extraction_agent_is_converted_to_generic_builder_contract():
    agent = yaml.safe_load(PDF_AGENT_PATH.read_text(encoding="utf-8"))
    assert agent["agent_id"] == "pdf_extraction"
    assert agent["output_schema"] is None
    assert "structured_finalization" not in agent
    assert agent["curation"] == {
        "adapter_key": "generic",
        "domain_pack_id": "generic",
        "launchable": True,
    }
    tools = set(agent["tools"])
    assert "list_generic_object_classes" in tools
    assert "stage_generic_object" in tools
    assert "finalize_generic_extraction" in tools
    assert "finalize_pdf_extraction" not in tools
    assert "PdfExtractionResultEnvelope" not in str(agent)


def test_generic_materializer_rejects_unknown_evidence_record_id():
    workspace = _generic_workspace(
        {
            "class_key": "generic:generic_reagent_candidate",
            "label": "TRiP.HMS00001",
            "classification_notes": ["The source table labels this as an RNAi reagent."],
        }
    )
    result = materialize_generic_builder_state(
        workspace=workspace,
        candidate_ids=["generic-candidate-1"],
        evidence_records=[],
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "unknown_evidence_record_id" for issue in result.issues
    )


def test_non_stageable_catalog_entry_cannot_be_required():
    catalog = load_generic_class_catalog()
    entry = catalog.entries_by_class_key["generic:generic_object"]
    non_stageable_catalog = GenericClassCatalog(
        entries=(
            replace(
                entry,
                class_key="generic:temporarily_non_stageable",
                stageable=False,
            ),
        ),
        generated_domain_pack=get_generated_generic_domain_pack(),
    )
    with pytest.raises(ValueError):
        non_stageable_catalog.require_stageable("generic:temporarily_non_stageable")
