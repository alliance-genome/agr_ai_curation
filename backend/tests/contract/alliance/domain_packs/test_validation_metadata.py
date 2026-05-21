"""Contract tests for Alliance domain-pack validation metadata."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
)


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)


def _unresolved_result_payload(request):
    return {
        "status": "unresolved",
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": {},
        "resolved_objects": [],
        "missing_expected_fields": list(request.target.expected_fields),
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "contract_fixture",
                "method": "package_scoped_validator",
                "query": dict(request.selected_inputs),
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
        "curator_message": None,
        "explanation": "Contract fixture unresolved validator result.",
    }


def test_alliance_domain_pack_validation_metadata_states_are_discoverable():
    alliance_registry = load_alliance_domain_pack_registry()
    expected_pack_ids = {
        "agr.alliance.gene_expression",
        "gene",
        "agr.alliance.allele",
        "agr.alliance.disease",
        "agr.alliance.chemical_condition",
        "agr.alliance.phenotype",
    }

    validation_registries = {
        pack_id: DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for pack_id in expected_pack_ids
    }

    assert {
        entry.state
        for entry in validation_registries[
            "agr.alliance.gene_expression"
        ].validator_metadata
    } == {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.UNDER_DEVELOPMENT,
    }
    assert {
        binding.binding_id
        for binding in validation_registries["gene"].bindings
    } == {"alliance_gene_reference_lookup"}
    assert {
        binding.state
        for binding in validation_registries["agr.alliance.disease"].bindings
    } >= {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.UNDER_DEVELOPMENT,
    }
    assert {
        binding.state
        for binding in validation_registries[
            "agr.alliance.chemical_condition"
        ].bindings
    } == {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.UNDER_DEVELOPMENT,
    }
    assert {
        binding.binding_id
        for binding in validation_registries["agr.alliance.allele"].bindings
    } == {
        "allele_pending_envelope_validator",
        "allele_mention_reference_validation",
        "source_reference_validation",
    }
    assert {
        entry.state
        for entry in validation_registries["agr.alliance.allele"].validator_metadata
    } == {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.UNDER_DEVELOPMENT,
    }
    assert {
        entry.state
        for entry in validation_registries[
            "agr.alliance.phenotype"
        ].validator_metadata
    } == {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.UNDER_DEVELOPMENT,
    }


def test_alliance_active_validator_bindings_have_dispatch_contracts():
    alliance_registry = load_alliance_domain_pack_registry()
    pack_ids = {
        "agr.alliance.gene_expression",
        "gene",
        "agr.alliance.allele",
        "agr.alliance.disease",
        "agr.alliance.chemical_condition",
        "agr.alliance.phenotype",
    }

    empty_active_bindings: list[str] = []
    for pack_id in sorted(pack_ids):
        registry = DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for binding in registry.bindings:
            if binding.state is not ValidationBindingState.ACTIVE:
                continue
            if binding.input_fields or binding.expected_result_fields:
                continue
            empty_active_bindings.append(f"{pack_id}:{binding.binding_id}")

    assert empty_active_bindings == []


def test_disease_pack_declares_builder_tool_contract():
    alliance_registry = load_alliance_domain_pack_registry()
    metadata = alliance_registry.get_pack("agr.alliance.disease").metadata
    builder = metadata.metadata["extraction_builder"]

    assert builder["enabled"] is True
    assert builder["stage_tool"] == "stage_disease_assertion_evidence"
    assert builder["finalize_tool"] == "finalize_disease_extraction"
    assert builder["model_final_ack_schema"] == "ExtractionToolFinalizationAck"
    assert builder["curation_output_schema"] == "DiseaseExtractionResultEnvelope"
    assert builder["fields"]["mention"]["required"] is True
    assert builder["fields"]["disease_name"]["required"] is True
    assert builder["fields"]["disease_relation_name"]["required"] is True
    assert builder["fields"]["data_provider_abbreviation"]["required"] is True
    assert builder["fields"]["evidence_record_ids"]["min_items"] == 1
    assert {
        target["binding_id"]
        for target in builder["object_graph"]["validator_targets"]
    } >= {
        "disease_ontology_term_lookup",
        "disease_relation_cv_lookup",
        "disease_data_provider_lookup",
        "disease_condition_relation_lookup",
    }
    assert builder["object_graph"]["required_objects"] == ["DiseaseAnnotation"]

    bindings = yaml.safe_load(
        (REPO_ROOT / "packages/alliance/tools/bindings.yaml").read_text(
            encoding="utf-8"
        )
    )
    tool_ids = {tool["tool_id"] for tool in bindings["tools"]}
    assert builder["stage_tool"] in tool_ids
    assert builder["finalize_tool"] in tool_ids

    agent = yaml.safe_load(
        (
            REPO_ROOT / "packages/alliance/agents/disease_extractor/agent.yaml"
        ).read_text(encoding="utf-8")
    )
    assert builder["stage_tool"] in agent["tools"]
    assert builder["finalize_tool"] in agent["tools"]


def test_chemical_pack_declares_builder_tool_contract():
    alliance_registry = load_alliance_domain_pack_registry()
    metadata = alliance_registry.get_pack("agr.alliance.chemical_condition").metadata
    builder = metadata.metadata["extraction_builder"]

    assert builder["enabled"] is True
    assert builder["stage_tool"] == "stage_chemical_condition_evidence"
    assert builder["finalize_tool"] == "finalize_chemical_extraction"
    assert builder["model_final_ack_schema"] == "ExtractionToolFinalizationAck"
    assert builder["curation_output_schema"] == "ChemicalExtractionResultEnvelope"
    assert builder["fields"]["source_chemical_mention"]["required"] is True
    assert builder["fields"]["condition_chemical_name"]["required"] is True
    assert builder["fields"]["evidence_record_ids"]["min_items"] == 1
    assert builder["fields"]["condition_relation_type_name"]["default"] == (
        "has_condition"
    )
    assert builder["fields"]["condition_class_name"]["default"] == (
        "chemical treatment"
    )
    assert {
        target["binding_id"]
        for target in builder["object_graph"]["validator_targets"]
    } >= {
        "chemical_condition.chebi_api_lookup",
        "chemical_condition.term_chebi_api_lookup",
        "chemical_condition.condition_ontology_lookup",
        "chemical_condition.condition_relation_type_lookup",
    }
    assert builder["object_graph"]["required_objects"] == [
        "ChemicalCondition",
        "ChemicalTerm",
        "Reference",
        "EvidenceQuote",
    ]

    bindings = yaml.safe_load(
        (REPO_ROOT / "packages/alliance/tools/bindings.yaml").read_text(
            encoding="utf-8"
        )
    )
    tool_ids = {tool["tool_id"] for tool in bindings["tools"]}
    assert builder["stage_tool"] in tool_ids
    assert builder["finalize_tool"] in tool_ids

    agent = yaml.safe_load(
        (
            REPO_ROOT / "packages/alliance/agents/chemical_extractor/agent.yaml"
        ).read_text(encoding="utf-8")
    )
    assert builder["stage_tool"] in agent["tools"]
    assert builder["finalize_tool"] in agent["tools"]


def test_active_bindings_have_active_capability_metadata():
    alliance_registry = load_alliance_domain_pack_registry()
    pack_ids = {
        "agr.alliance.allele",
        "agr.alliance.chemical_condition",
        "agr.alliance.disease",
        "agr.alliance.gene_expression",
        "agr.alliance.phenotype",
    }

    missing_active_metadata: list[str] = []
    for pack_id in sorted(pack_ids):
        registry = DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        active_validator_ids = {
            item.validator_id
            for item in registry.validator_metadata
            if item.state is ValidationBindingState.ACTIVE
        }
        active_binding_ids = {
            item.binding_id
            for item in registry.bindings
            if item.state is ValidationBindingState.ACTIVE
        }
        for binding_id in sorted(active_binding_ids):
            if binding_id not in active_validator_ids:
                missing_active_metadata.append(f"{pack_id}:{binding_id}")

    assert missing_active_metadata == []


def test_alliance_evidence_record_selectors_use_verified_quote_path():
    alliance_registry = load_alliance_domain_pack_registry()
    pack_ids = {
        "agr.alliance.gene_expression",
        "gene",
        "agr.alliance.allele",
        "agr.alliance.disease",
        "agr.alliance.chemical_condition",
        "agr.alliance.phenotype",
    }

    legacy_quote_selectors: list[str] = []
    for pack_id in sorted(pack_ids):
        registry = DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for binding in registry.bindings:
            for input_name, selector in binding.input_fields.items():
                if selector.source != "evidence_record":
                    continue
                if selector.path == "quote":
                    legacy_quote_selectors.append(
                        f"{pack_id}:{binding.binding_id}:{input_name}"
                    )

    assert legacy_quote_selectors == []


def test_alliance_validator_metadata_has_curator_facing_display_names():
    alliance_registry = load_alliance_domain_pack_registry()
    pack_ids = {
        "agr.alliance.gene_expression",
        "gene",
        "agr.alliance.allele",
        "agr.alliance.disease",
        "agr.alliance.chemical_condition",
        "agr.alliance.phenotype",
    }

    missing_display_names: list[str] = []
    technical_labels: list[str] = []
    duplicate_labels: list[str] = []
    for pack_id in sorted(pack_ids):
        registry = DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for entry in registry.validator_metadata:
            if not entry.display_name:
                missing_display_names.append(f"{pack_id}:validator:{entry.validator_id}")
        for binding in registry.bindings:
            if not binding.display_name:
                missing_display_names.append(f"{pack_id}:binding:{binding.binding_id}")

        seen_labels: set[tuple[str, str, str, str, str]] = set()
        for option in registry.validation_attachment_options():
            if option.label and (
                "." in option.label
                or "_" in option.label
                or "(" in option.label
                or ")" in option.label
                or "validated reference" in option.label.lower()
                or "envelope validation" in option.label.lower()
                or "export projection" in option.label.lower()
            ):
                technical_labels.append(f"{pack_id}:{option.attachment_id}:{option.label}")
            label_key = (
                option.state.value,
                option.label,
                option.scope,
                option.object_type or "",
                option.field_path or "",
            )
            if label_key in seen_labels:
                duplicate_labels.append(f"{pack_id}:{option.label}")
            seen_labels.add(label_key)

    assert missing_display_names == []
    assert technical_labels == []
    assert duplicate_labels == []


def test_alliance_validator_binding_capability_groups_have_explicit_policies():
    alliance_registry = load_alliance_domain_pack_registry()
    top_level_pack_ids = {
        "agr.alliance.gene_expression",
        "agr.alliance.allele",
        "agr.alliance.disease",
        "agr.alliance.chemical_condition",
        "agr.alliance.phenotype",
    }

    for pack_id in sorted(top_level_pack_ids):
        raw_bindings = alliance_registry.get_pack(pack_id).metadata.metadata[
            "validator_bindings"
        ]
        assert isinstance(raw_bindings["active"], list)
        assert isinstance(raw_bindings["under_development"], list)

        for binding in raw_bindings["active"]:
            assert binding["validator_agent"]["package_id"] == "agr.alliance"
            assert "applies_to" in binding
            assert "input_fields" in binding
            assert "expected_result_fields" in binding
            assert binding["required"] is True
            if binding["binding_id"] == "allele_mention_reference_validation":
                assert binding["blocking"] is True
                assert binding["allow_opt_out"] is False
            else:
                assert binding["blocking"] is False
                assert binding["allow_opt_out"] is True
            assert binding["curator_override"] == {"allowed": False}

        for binding in raw_bindings["under_development"]:
            assert binding["state_explanation"]
            assert "required" not in binding
            assert "blocking" not in binding
            assert "allow_opt_out" not in binding
            assert "curator_override" not in binding


def test_alliance_active_and_under_development_capabilities_have_distinct_visibility():
    alliance_registry = load_alliance_domain_pack_registry()
    top_level_pack_ids = {
        "agr.alliance.gene_expression",
        "agr.alliance.allele",
        "agr.alliance.disease",
        "agr.alliance.chemical_condition",
        "agr.alliance.phenotype",
    }

    active_options = []
    under_development_options = []
    for pack_id in sorted(top_level_pack_ids):
        registry = DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for option in registry.validation_attachment_options():
            if option.validator_binding_id is None:
                continue
            if option.state is ValidationBindingState.ACTIVE:
                active_options.append(option)
            elif option.state is ValidationBindingState.UNDER_DEVELOPMENT:
                under_development_options.append(option)

    assert active_options
    assert under_development_options
    for option in active_options:
        assert option.default_enabled is True
        assert option.validator_package_id == "agr.alliance"
        assert option.validator_agent_id

    for option in under_development_options:
        assert option.default_enabled is False
        assert option.required is False
        assert option.export_blocking is False
        assert option.allow_opt_out is False
        assert option.state_explanation
        if option.validator_package_id is not None or option.validator_agent_id is not None:
            assert option.validator_package_id == "agr.alliance"
            assert option.validator_agent_id


def test_alliance_relative_validator_metadata_targets_fields_and_policies():
    alliance_registry = load_alliance_domain_pack_registry()
    registries = {
        pack_id: DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for pack_id in (
            "agr.alliance.gene_expression",
            "gene",
            "agr.alliance.disease",
            "agr.alliance.chemical_condition",
            "agr.alliance.phenotype",
        )
    }

    gene_binding = {
        binding.binding_id: binding for binding in registries["gene"].bindings
    }["alliance_gene_reference_lookup"]
    assert gene_binding.object_types == ("gene_mention_evidence",)
    assert gene_binding.field_paths == ()
    assert gene_binding.expected_result_fields == {
        "curie": "primary_external_id",
        "symbol": "gene_symbol",
        "taxon": "taxon",
    }
    assert set(gene_binding.input_fields) == {
        "mention",
        "proposed_gene_id",
        "proposed_symbol",
        "proposed_taxon",
        "taxon_hint",
        "data_provider_hint",
        "species",
        "evidence_quote",
        "identity_resolution_notes",
    }
    assert (
        "alliance_gene_reference_lookup"
        not in registries["gene"]
        .policy_for("gene_mention_evidence", "primary_external_id")
        .validator_binding_ids
    )

    disease_bindings = {
        binding.binding_id: binding
        for binding in registries["agr.alliance.disease"].bindings
    }
    assert disease_bindings["disease_ontology_term_lookup"].object_types == (
        "DiseaseAnnotation",
    )
    assert disease_bindings["disease_ontology_term_lookup"].field_paths == (
        "disease_annotation_object.curie",
        "disease_annotation_object.name",
    )
    assert disease_bindings["disease_condition_relation_lookup"].object_types == (
        "DiseaseAnnotation",
    )
    assert disease_bindings["disease_condition_relation_lookup"].field_paths == (
        "condition_relations[0].condition_relation_type.name",
    )
    assert (
        disease_bindings["disease_reference_materialization"].state
        is ValidationBindingState.UNDER_DEVELOPMENT
    )
    assert disease_bindings["disease_reference_materialization"].field_paths == (
        "single_reference.reference_id",
        "single_reference.curie",
        "single_reference.title",
    )
    assert "disease_ontology_term_lookup" in registries[
        "agr.alliance.disease"
    ].policy_for(
        "DiseaseAnnotation",
        "disease_annotation_object.curie",
    ).validator_binding_ids
    assert "disease_condition_relation_lookup" in registries[
        "agr.alliance.disease"
    ].policy_for(
        "DiseaseAnnotation",
        "condition_relations[0].condition_relation_type.name",
    ).validator_binding_ids
    assert "disease_reference_materialization" in registries[
        "agr.alliance.disease"
    ].policy_for(
        "DiseaseAnnotation",
        "single_reference.curie",
    ).validator_binding_ids

    disease_matches = registries["agr.alliance.disease"].match_bindings(
        DomainEnvelope(
            envelope_id="disease-env",
            domain_pack_id="agr.alliance.disease",
            objects=[
                CuratableObjectEnvelope(
                    object_type="DiseaseAnnotation",
                    pending_ref_id="disease-annotation-1",
                    payload={},
                )
            ],
        ),
        states=[ValidationBindingState.UNDER_DEVELOPMENT],
    )
    disease_match_targets = {
        (match.binding.binding_id, match.object_type, match.field_path)
        for match in disease_matches
    }
    assert (
        "disease_reference_materialization",
        "DiseaseAnnotation",
        "single_reference.curie",
    ) in disease_match_targets

    chemical_condition_bindings = {
        binding.binding_id: binding
        for binding in registries["agr.alliance.chemical_condition"].bindings
    }
    gene_expression_bindings = {
        binding.binding_id: binding
        for binding in registries["agr.alliance.gene_expression"].bindings
    }
    relation_binding = gene_expression_bindings["relation_vocabulary_validation"]
    assert relation_binding.validator_agent is not None
    assert relation_binding.validator_agent.agent_id == "controlled_vocabulary_validation"
    assert relation_binding.state is ValidationBindingState.ACTIVE
    assert relation_binding.object_types == ("GeneExpressionAnnotation",)
    assert relation_binding.field_paths == ("relation.name",)
    assert relation_binding.input_fields["vocabulary"].value == "Expression Relation"
    assert "relation_vocabulary_validation" in registries[
        "agr.alliance.gene_expression"
    ].policy_for(
        "GeneExpressionAnnotation",
        "relation.name",
    ).validator_binding_ids

    expression_provider_binding = gene_expression_bindings["data_provider_validation"]
    assert expression_provider_binding.validator_agent is not None
    assert (
        expression_provider_binding.validator_agent.agent_id
        == "data_provider_validation"
    )
    assert expression_provider_binding.state is ValidationBindingState.ACTIVE
    assert expression_provider_binding.object_types == ("GeneExpressionAnnotation",)
    assert expression_provider_binding.field_paths == ("data_provider.abbreviation",)
    assert set(expression_provider_binding.input_fields) == {"abbreviation"}
    assert "data_provider_validation" in registries[
        "agr.alliance.gene_expression"
    ].policy_for(
        "GeneExpressionAnnotation",
        "data_provider.abbreviation",
    ).validator_binding_ids

    disease_relation_binding = disease_bindings["disease_relation_cv_lookup"]
    assert disease_relation_binding.validator_agent is not None
    assert (
        disease_relation_binding.validator_agent.agent_id
        == "controlled_vocabulary_validation"
    )
    assert disease_relation_binding.state is ValidationBindingState.ACTIVE
    assert disease_relation_binding.input_fields["vocabulary"].value == (
        "Disease Relation"
    )
    assert disease_relation_binding.input_fields["term_name"].path == (
        "disease_relation_name"
    )
    assert (
        disease_relation_binding.expected_result_fields["internal_id"]
        == "disease_relation_id"
    )

    disease_condition_binding = disease_bindings["disease_condition_relation_lookup"]
    assert disease_condition_binding.validator_agent is not None
    assert (
        disease_condition_binding.validator_agent.agent_id
        == "controlled_vocabulary_validation"
    )
    assert disease_condition_binding.state is ValidationBindingState.ACTIVE
    assert disease_condition_binding.input_fields["vocabulary"].value == (
        "Condition Relation Type"
    )
    assert (
        disease_condition_binding.input_fields["term_name"].required is False
    )

    disease_provider_binding = disease_bindings["disease_data_provider_lookup"]
    assert disease_provider_binding.validator_agent is not None
    assert (
        disease_provider_binding.validator_agent.agent_id
        == "data_provider_validation"
    )
    assert disease_provider_binding.state is ValidationBindingState.ACTIVE
    assert disease_provider_binding.field_paths == ("data_provider.abbreviation",)
    assert set(disease_provider_binding.input_fields) == {"abbreviation"}
    assert disease_provider_binding.expected_result_fields == {
        "abbreviation": "data_provider.abbreviation",
    }

    disease_subject_binding = disease_bindings["disease_subject_materialization"]
    assert disease_subject_binding.validator_agent is not None
    assert (
        disease_subject_binding.validator_agent.agent_id
        == "subject_entity_validation"
    )
    assert disease_subject_binding.state is ValidationBindingState.UNDER_DEVELOPMENT
    assert disease_subject_binding.field_paths == (
        "disease_annotation_subject.subject_identifier",
        "disease_annotation_subject.subject_type",
    )
    assert (
        disease_subject_binding.input_fields["subject_label"].required is False
    )
    assert disease_subject_binding.input_fields["taxon"].required is False
    assert disease_subject_binding.expected_result_fields == {
        "subject_identifier": "disease_annotation_subject.subject_identifier",
        "subject_type": "disease_annotation_subject.subject_type",
        "subject_label": "disease_annotation_subject.subject_label",
        "taxon": "disease_annotation_subject.taxon",
    }

    phenotype_bindings = {
        binding.binding_id: binding
        for binding in registries["agr.alliance.phenotype"].bindings
    }
    phenotype_subject_binding = phenotype_bindings[
        "phenotype_subject_entity_validator"
    ]
    assert phenotype_subject_binding.validator_agent is not None
    assert (
        phenotype_subject_binding.validator_agent.agent_id
        == "subject_entity_validation"
    )
    assert phenotype_subject_binding.state is ValidationBindingState.UNDER_DEVELOPMENT
    assert phenotype_subject_binding.field_paths == (
        "subject_identifier",
        "subject_type",
    )
    assert (
        phenotype_subject_binding.input_fields["subject_label"].required is False
    )
    assert phenotype_subject_binding.input_fields["taxon"].required is False
    assert phenotype_subject_binding.expected_result_fields == {
        "subject_identifier": "subject_identifier",
        "subject_type": "subject_type",
        "subject_label": "subject_label",
        "taxon": "taxon",
    }

    phenotype_term_binding = phenotype_bindings["phenotype_term_ontology_validator"]
    assert phenotype_term_binding.validator_agent is not None
    assert phenotype_term_binding.validator_agent.agent_id == "ontology_term_validation"
    assert phenotype_term_binding.state is ValidationBindingState.ACTIVE
    assert phenotype_term_binding.object_types == ("PhenotypeTerm",)
    assert phenotype_term_binding.field_paths == ()
    assert phenotype_term_binding.input_fields["curie"].required is False
    assert phenotype_term_binding.input_fields["label"].required is False
    assert phenotype_term_binding.input_fields["data_provider"].path == (
        "ontology_lookup_hint.data_provider"
    )
    assert phenotype_term_binding.input_fields["taxon_id"].path == (
        "ontology_lookup_hint.taxon_id"
    )
    assert (
        phenotype_term_binding.input_fields["provider_taxon_ontology_mappings"]
        .value[0]["ontology_term_type"]
        == "WBPhenotypeTerm"
    )

    assert chemical_condition_bindings[
        "chemical_condition.chebi_api_lookup"
    ].object_types == ("ChemicalCondition",)
    assert chemical_condition_bindings[
        "chemical_condition.chebi_api_lookup"
    ].field_paths == (
        "condition_chemical.curie",
        "condition_chemical.name",
    )
    chemical_condition_chebi_binding = chemical_condition_bindings[
        "chemical_condition.chebi_api_lookup"
    ]
    assert chemical_condition_chebi_binding.input_fields["curie"].required is False
    assert chemical_condition_chebi_binding.input_fields["name"].required is False
    assert chemical_condition_bindings[
        "chemical_condition.condition_ontology_lookup"
    ].object_types == ("ChemicalCondition",)
    assert (
        chemical_condition_bindings["chemical_condition.condition_ontology_lookup"].state
        is ValidationBindingState.ACTIVE
    )
    assert chemical_condition_bindings[
        "chemical_condition.condition_ontology_lookup"
    ].field_paths == (
        "condition_class.curie",
    )
    chemical_condition_ontology_binding = chemical_condition_bindings[
        "chemical_condition.condition_ontology_lookup"
    ]
    assert chemical_condition_ontology_binding.validator_agent is not None
    assert (
        chemical_condition_ontology_binding.validator_agent.agent_id
        == "ontology_term_validation"
    )
    assert chemical_condition_ontology_binding.input_fields["curie"].path == (
        "condition_class.curie"
    )
    assert chemical_condition_ontology_binding.input_fields["curie"].required is False
    assert chemical_condition_ontology_binding.input_fields["label"].path == (
        "condition_class.name"
    )
    assert chemical_condition_ontology_binding.input_fields["label"].required is False
    assert (
        chemical_condition_ontology_binding.input_fields["ontology_term_type"].value
        == "ZECOTerm"
    )
    assert chemical_condition_ontology_binding.input_fields[
        "accepted_prefixes"
    ].value == ["ZECO"]
    assert chemical_condition_ontology_binding.input_fields["exact_match"].value is True
    assert chemical_condition_ontology_binding.expected_result_fields == {
        "condition_class_curie": "condition_class.curie",
    }
    assert "chemical_condition.chebi_api_lookup" in registries[
        "agr.alliance.chemical_condition"
    ].policy_for(
        "ChemicalCondition",
        "condition_chemical.name",
    ).validator_binding_ids
    assert "chemical_condition.condition_ontology_lookup" in registries[
        "agr.alliance.chemical_condition"
    ].policy_for(
        "ChemicalCondition",
        "condition_class.curie",
    ).validator_binding_ids
    chemical_relation_binding = chemical_condition_bindings[
        "chemical_condition.condition_relation_type_lookup"
    ]
    assert chemical_relation_binding.validator_agent is not None
    assert (
        chemical_relation_binding.validator_agent.agent_id
        == "controlled_vocabulary_validation"
    )
    assert chemical_relation_binding.state is ValidationBindingState.ACTIVE
    assert chemical_relation_binding.object_types == ("ChemicalCondition",)
    assert chemical_relation_binding.field_paths == ("condition_relation_type.name",)
    assert chemical_relation_binding.input_fields["vocabulary"].value == (
        "Condition Relation Type"
    )
    assert "chemical_condition.condition_relation_type_lookup" in registries[
        "agr.alliance.chemical_condition"
    ].policy_for(
        "ChemicalCondition",
        "condition_relation_type.name",
    ).validator_binding_ids


def test_subject_entity_selectors_require_type_and_omit_absent_optional_context():
    alliance_registry = load_alliance_domain_pack_registry()
    phenotype_pack = alliance_registry.get_pack("agr.alliance.phenotype")
    registry = DomainPackValidationRegistry.from_domain_pack(phenotype_pack)
    subject_binding = {
        binding.binding_id: binding for binding in registry.bindings
    }["phenotype_subject_entity_validator"]

    envelope = DomainEnvelope(
        envelope_id="phenotype-env",
        domain_pack_id="agr.alliance.phenotype",
        objects=[
            CuratableObjectEnvelope(
                object_type="PhenotypeSubject",
                pending_ref_id="subject-1",
                payload={
                    "subject_identifier": "WB:WBGene00000001",
                    "subject_type": "gene",
                },
            )
        ],
    )
    match = next(
        match
        for match in registry.match_bindings(
            envelope,
            states=[ValidationBindingState.UNDER_DEVELOPMENT],
        )
        if match.binding.binding_id == subject_binding.binding_id
        and match.field_path == "subject_identifier"
    )

    result = build_domain_validation_request(match)

    assert result.request is not None
    assert result.findings == ()
    assert result.selected_inputs == {
        "subject_type": "gene",
        "subject_identifier": "WB:WBGene00000001",
    }
    assert result.request.target.input_values == result.selected_inputs

    missing_type_envelope = DomainEnvelope(
        envelope_id="phenotype-env-missing-type",
        domain_pack_id="agr.alliance.phenotype",
        objects=[
            CuratableObjectEnvelope(
                object_type="PhenotypeSubject",
                pending_ref_id="subject-1",
                payload={"subject_identifier": "WB:WBGene00000001"},
            )
        ],
    )
    missing_type_match = next(
        match
        for match in registry.match_bindings(
            missing_type_envelope,
            states=[ValidationBindingState.UNDER_DEVELOPMENT],
        )
        if match.binding.binding_id == subject_binding.binding_id
        and match.field_path == "subject_identifier"
    )

    missing_type_result = build_domain_validation_request(missing_type_match)

    assert missing_type_result.request is None
    assert [finding.code for finding in missing_type_result.findings] == [
        "selector_missing_field"
    ]
    assert (
        missing_type_result.findings[0].details["selector_problem"]["input_name"]
        == "subject_type"
    )


def test_subject_entity_selectors_reject_ambiguous_optional_taxon_context():
    alliance_registry = load_alliance_domain_pack_registry()
    phenotype_pack = alliance_registry.get_pack("agr.alliance.phenotype")
    registry = DomainPackValidationRegistry.from_domain_pack(phenotype_pack)

    envelope = DomainEnvelope(
        envelope_id="phenotype-env-ambiguous-taxon",
        domain_pack_id="agr.alliance.phenotype",
        objects=[
            CuratableObjectEnvelope(
                object_type="PhenotypeSubject",
                pending_ref_id="subject-1",
                payload={
                    "subject_identifier": "WB:WBGene00000001",
                    "subject_type": "gene",
                    "taxon": ["NCBITaxon:6239", "NCBITaxon:10090"],
                },
            )
        ],
    )
    match = next(
        match
        for match in registry.match_bindings(
            envelope,
            states=[ValidationBindingState.UNDER_DEVELOPMENT],
        )
        if match.binding.binding_id == "phenotype_subject_entity_validator"
        and match.field_path == "subject_identifier"
    )

    result = build_domain_validation_request(match)

    assert result.request is None
    assert [finding.code for finding in result.findings] == ["selector_ambiguous"]
    assert result.findings[0].details["selector_problem"]["input_name"] == "taxon"


def test_under_development_validator_bindings_remain_metadata_only():
    alliance_registry = load_alliance_domain_pack_registry()
    disease_pack = alliance_registry.get_pack("agr.alliance.disease")
    registry = DomainPackValidationRegistry.from_domain_pack(disease_pack)

    binding = {
        item.binding_id: item
        for item in registry.bindings
    }["disease_reference_materialization"]
    assert binding.state is ValidationBindingState.UNDER_DEVELOPMENT
    assert binding.required is False
    assert binding.blocking is False
    assert binding.allow_opt_out is False

    envelope = DomainEnvelope(
        envelope_id="disease-env",
        domain_pack_id="agr.alliance.disease",
        objects=[
            CuratableObjectEnvelope(
                object_type="DiseaseAnnotation",
                pending_ref_id="disease-annotation-1",
                payload={
                    "mention": "Andersen-Tawil syndrome",
                    "disease_annotation_object": {
                        "curie": "DOID:0050434",
                        "name": "Andersen-Tawil syndrome",
                    },
                    "role": "primary",
                    "confidence": "high",
                    "evidence_record_ids": ["evidence-1"],
                    "evidence_records": [
                        {
                            "evidence_record_id": "evidence-1",
                            "verified_quote": "Andersen-Tawil syndrome",
                            "page": 1,
                            "section": "Results",
                            "chunk_id": "chunk-1",
                        }
                    ],
                },
            )
        ],
    )

    assert envelope.validation_findings == []
    assert envelope.history == []
    assert all(
        match.binding.state is ValidationBindingState.ACTIVE
        for match in registry.match_bindings(
            envelope,
            states=[ValidationBindingState.ACTIVE],
        )
    )
    assert "disease_reference_materialization" in {
        match.binding.binding_id
        for match in registry.match_bindings(
            envelope,
            states=[ValidationBindingState.UNDER_DEVELOPMENT],
        )
        if match.binding.state is ValidationBindingState.UNDER_DEVELOPMENT
    }


def test_representative_ontology_term_bindings_target_generic_validator():
    alliance_registry = load_alliance_domain_pack_registry()
    cases = {
        "agr.alliance.disease": {
            "disease_ontology_term_lookup": {
                "state": ValidationBindingState.ACTIVE,
                "ontology_family": "disease",
                "accepted_prefixes": ["DOID"],
                "optional_inputs": ["curie", "label"],
                "expected_result_fields": {
                    "curie": "disease_annotation_object.curie",
                    "label": "disease_annotation_object.name",
                },
            },
            "disease_evidence_code_lookup": {
                "state": ValidationBindingState.UNDER_DEVELOPMENT,
                "ontology_family": "evidence",
                "accepted_prefixes": ["ECO"],
                "optional_inputs": ["curie"],
                "expected_result_fields": {
                    "curie": "evidence_code_curies[0]",
                },
            },
        },
        "agr.alliance.phenotype": {
            "phenotype_term_ontology_validator": {
                "state": ValidationBindingState.ACTIVE,
                "ontology_family": "phenotype",
                "accepted_prefixes": ["MP", "WBPhenotype"],
                "optional_inputs": [
                    "curie",
                    "label",
                    "data_provider",
                    "taxon_id",
                    "evidence_record_id",
                    "evidence_quote",
                    "source_chunk_id",
                    "source_section",
                ],
                "expected_result_fields": {
                    "curie": "curie",
                    "label": "label",
                },
            }
        },
        "agr.alliance.chemical_condition": {
            "chemical_condition.condition_ontology_lookup": {
                "state": ValidationBindingState.ACTIVE,
                "ontology_family": "condition",
                "ontology_term_type": "ZECOTerm",
                "accepted_prefixes": ["ZECO"],
                "optional_inputs": ["curie", "label"],
                "expected_result_fields": {
                    "condition_class_curie": "condition_class.curie",
                },
            }
        },
    }

    for pack_id, expected_bindings in cases.items():
        registry = DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        bindings = {binding.binding_id: binding for binding in registry.bindings}

        for binding_id, expected in expected_bindings.items():
            binding = bindings[binding_id]

            assert binding.state is expected["state"]
            assert binding.validator_agent is not None
            assert binding.validator_agent.package_id == "agr.alliance"
            assert binding.validator_agent.agent_id == "ontology_term_validation"
            assert binding.expected_result_fields == expected["expected_result_fields"]
            assert (
                binding.input_fields["ontology_family"].source == "literal"
            )
            assert (
                binding.input_fields["ontology_family"].value
                == expected["ontology_family"]
            )
            assert all(
                isinstance(field_path, str) and field_path.strip()
                for field_path in binding.expected_result_fields.values()
            )
            assert set(binding.expected_result_fields.values()).isdisjoint(
                {"DOTerm", "ECOTerm", "ZECOTerm", "DOID", "ECO", "ZECO"}
            )
            if binding.state is ValidationBindingState.ACTIVE:
                assert "state_explanation" not in binding.raw
            else:
                assert "state_explanation" in binding.raw
            if "ontology_term_type" in expected:
                assert (
                    binding.input_fields["ontology_term_type"].value
                    == expected["ontology_term_type"]
                )
            if "accepted_prefixes" in expected:
                assert (
                    binding.input_fields["accepted_prefixes"].value
                    == expected["accepted_prefixes"]
                )
            for input_name in expected.get("optional_inputs", []):
                selector = binding.input_fields.get(input_name)
                assert selector is not None
                assert selector.required is False


def test_representative_alliance_active_validators_dispatch_unresolved_results():
    alliance_registry = load_alliance_domain_pack_registry()

    cases = [
        (
            "gene",
            DomainEnvelope(
                envelope_id="gene-env",
                domain_pack_id="gene",
                objects=[
                    CuratableObjectEnvelope(
                        object_type="gene_mention_evidence",
                        pending_ref_id="gene-1",
                        payload={
                            "mention": "ninaE",
                            "primary_external_id": "FB:FBgn0002940",
                            "gene_symbol": "ninaE",
                            "taxon": "NCBITaxon:7227",
                            "identity_resolution_notes": [
                                "Fixture paper context identifies ninaE as a Drosophila gene mention."
                            ],
                            "confidence": "high",
                            "evidence_record_id": "evidence-1",
                            "verified_quote": "ninaE",
                            "page": 1,
                            "section": "Results",
                        },
                    )
                ],
            ),
        ),
        (
            "agr.alliance.chemical_condition",
            DomainEnvelope(
                envelope_id="chemical-env",
                domain_pack_id="agr.alliance.chemical_condition",
                objects=[
                    CuratableObjectEnvelope(
                        object_type="ChemicalCondition",
                        pending_ref_id="condition-1",
                        payload={
                            "condition_chemical": {
                                "curie": "BAD:1",
                                "name": "bad chemical",
                            },
                            "condition_class": {
                                "curie": "ZECO:0000101",
                                "name": "chemical treatment",
                            },
                            "condition_relation_type": {
                                "name": "has_condition",
                            },
                            "source_chemical_mention": "bad chemical",
                            "evidence_record_ids": ["evidence-1"],
                            "confidence": "high",
                        },
                    )
                ],
            ),
        ),
        (
            "agr.alliance.phenotype",
            DomainEnvelope(
                envelope_id="phenotype-env",
                domain_pack_id="agr.alliance.phenotype",
                objects=[
                    CuratableObjectEnvelope(
                        object_type="PhenotypeTerm",
                        object_role="validated_reference",
                        pending_ref_id="phenotype-term-1",
                        payload={
                            "curie": "WBPhenotype:0000001",
                            "label": "fixture phenotype",
                        },
                    )
                ],
            ),
        ),
    ]

    for pack_id, envelope in cases:
        result = dispatch_active_validator_bindings(
            envelope,
            alliance_registry.get_pack(pack_id),
            runner=lambda request, *, binding: _unresolved_result_payload(request),
        )

        assert result.validator_results
        assert {item.status for item in result.validator_results} == {"unresolved"}
        assert {
            finding.code for finding in result.envelope.validation_findings
        }.issubset({"domain_pack.validator_unresolved"})
        assert all(
            finding.details.get("failure_classification") != "under_development"
            for finding in result.envelope.validation_findings
        )
        assert all(
            finding.code != "domain_pack.validator_binding_under_development"
            for finding in result.envelope.validation_findings
        )
