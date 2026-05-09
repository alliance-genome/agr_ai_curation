"""Contract tests for Alliance domain-pack validation metadata."""

from __future__ import annotations

import sys
from pathlib import Path

from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)


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
    } >= {ValidationBindingState.PLANNED, ValidationBindingState.BLOCKED}
    assert {
        binding.binding_id
        for binding in validation_registries["gene"].bindings
    } == {"alliance_gene_reference_lookup"}
    assert {
        binding.state
        for binding in validation_registries["agr.alliance.disease"].bindings
    } >= {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.PLANNED,
        ValidationBindingState.BLOCKED,
    }
    assert {
        binding.state
        for binding in validation_registries[
            "agr.alliance.chemical_condition"
        ].bindings
    } == {
        ValidationBindingState.ACTIVE,
        ValidationBindingState.PLANNED,
        ValidationBindingState.BLOCKED,
    }
    assert {
        binding.binding_id
        for binding in validation_registries["agr.alliance.allele"].bindings
    } == {"allele_pending_envelope_validator"}
    assert {
        entry.state
        for entry in validation_registries[
            "agr.alliance.phenotype"
        ].validator_metadata
    } >= {ValidationBindingState.PLANNED, ValidationBindingState.BLOCKED}


def test_alliance_relative_validator_metadata_targets_fields_and_policies():
    alliance_registry = load_alliance_domain_pack_registry()
    registries = {
        pack_id: DomainPackValidationRegistry.from_domain_pack(
            alliance_registry.get_pack(pack_id)
        )
        for pack_id in (
            "gene",
            "agr.alliance.disease",
            "agr.alliance.chemical_condition",
        )
    }

    gene_binding = {
        binding.binding_id: binding for binding in registries["gene"].bindings
    }["alliance_gene_reference_lookup"]
    assert gene_binding.object_types == ("gene_mention_evidence",)
    assert gene_binding.field_paths == (
        "primary_external_id",
        "gene_symbol",
        "taxon",
    )
    assert "alliance_gene_reference_lookup" in registries["gene"].policy_for(
        "gene_mention_evidence",
        "primary_external_id",
    ).validator_binding_ids

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
        is ValidationBindingState.BLOCKED
    )
    assert disease_bindings["disease_reference_materialization"].field_paths == (
        "single_reference.curie",
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
        states=[ValidationBindingState.PLANNED, ValidationBindingState.BLOCKED],
    )
    disease_match_targets = {
        (match.binding.binding_id, match.object_type, match.field_path)
        for match in disease_matches
    }
    assert (
        "disease_ontology_term_lookup",
        "DiseaseAnnotation",
        "disease_annotation_object.curie",
    ) in disease_match_targets
    assert (
        "disease_reference_materialization",
        "DiseaseAnnotation",
        "single_reference.curie",
    ) in disease_match_targets

    chemical_condition_bindings = {
        binding.binding_id: binding
        for binding in registries["agr.alliance.chemical_condition"].bindings
    }
    assert chemical_condition_bindings[
        "chemical_condition.chebi_api_lookup"
    ].object_types == ("ChemicalCondition",)
    assert chemical_condition_bindings[
        "chemical_condition.chebi_api_lookup"
    ].field_paths == (
        "condition_chemical.curie",
        "condition_chemical.name",
    )
    assert chemical_condition_bindings[
        "chemical_condition.condition_ontology_lookup"
    ].object_types == ("ChemicalCondition",)
    assert chemical_condition_bindings[
        "chemical_condition.condition_ontology_lookup"
    ].field_paths == (
        "condition_class.curie",
    )
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
