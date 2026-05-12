"""Contract tests for Alliance domain-pack validation metadata."""

from __future__ import annotations

import sys
from pathlib import Path

from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.lib.domain_packs import validation_supervisor
from src.lib.domain_packs.validation_supervisor import run_validation_supervisor
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ValidationFindingStatus,
)


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


def test_db_backed_validator_bindings_emit_lookup_attempts_and_provider_projections():
    alliance_registry = load_alliance_domain_pack_registry()
    disease_pack = alliance_registry.get_pack("agr.alliance.disease")
    registry = DomainPackValidationRegistry.from_domain_pack(disease_pack)

    binding = {
        item.binding_id: item
        for item in registry.bindings
    }["disease_ontology_term_lookup"]
    assert binding.provider_projection == {
        "provider": "alliance_curation_db",
        "projection_type": "curation_db_reference_lookup",
        "target": {
            "input_fields": {
                "curie": "disease_annotation_object.curie",
                "name": "disease_annotation_object.name",
            },
            "expected_result_fields": {
                "curie": "disease_annotation_object.curie",
                "name": "disease_annotation_object.name",
                "ontologytermtype": "DOTerm",
            },
        },
        "provider_fields": {
            "table": "public.ontologyterm",
        },
    }

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

    result = run_validation_supervisor(envelope, disease_pack, registry=registry)
    ontology_finding = next(
        finding
        for finding in result.envelope.validation_findings
        if finding.details.get("lookup_attempts")
        and finding.details["lookup_attempts"][0]["attempted_query"][
            "validator_binding_id"
        ] == "disease_ontology_term_lookup"
    )
    attempt = ontology_finding.details["lookup_attempts"][0]
    assert attempt["lookup_status"] == "under_development"
    assert attempt["provider"] == "alliance_curation_db"
    assert attempt["attempted_query"]["input_fields"]["curie"]["value"] == "DOID:0050434"
    assert ontology_finding.details["failure_classification"] == "under_development"
    assert ontology_finding.details["provider_projections"][0]["provider"] == "alliance_curation_db"

    history_event = next(
        event
        for event in result.envelope.history
        if event.details.get("finding_id") == ontology_finding.finding_id
    )
    assert history_event.details["lookup_attempts"] == ontology_finding.details["lookup_attempts"]
    assert history_event.details["provider_projections"] == ontology_finding.details["provider_projections"]


def test_first_pass_alliance_domain_packs_have_explicit_supervisor_behavior(monkeypatch):
    alliance_registry = load_alliance_domain_pack_registry()

    monkeypatch.setattr(
        validation_supervisor,
        "_agr_curation_query_callable",
        lambda method, **kwargs: {
            "status": "ok",
            "data": {
                "curie": kwargs["gene_id"],
                "symbol": "ninaE",
                "taxon": "NCBITaxon:7227",
            },
            "count": 1,
            "lookup_status": "success",
            "explanation": "Resolved ninaE.",
            "lookup_attempts": [
                {
                    "attempted_query": {"method": method, **kwargs},
                    "lookup_status": "success",
                    "candidate_count": 1,
                    "resolved_id": kwargs["gene_id"],
                    "resolved_label": "ninaE",
                }
            ],
            "result_projections": [
                {
                    "provider": "alliance_curation_db",
                    "resolved_id": kwargs["gene_id"],
                    "resolved_label": "ninaE",
                }
            ],
        },
    )

    gene_result = run_validation_supervisor(
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
                        "confidence": "high",
                        "evidence_record_id": "evidence-1",
                        "verified_quote": "ninaE",
                        "page": 1,
                        "section": "Results",
                    },
                )
            ],
        ),
        alliance_registry.get_pack("gene"),
    )
    gene_resolved_fields = {
        finding.field_ref.field_path
        for finding in gene_result.envelope.validation_findings
        if finding.code == "domain_pack.validator_lookup_resolved"
    }
    assert gene_resolved_fields == {"primary_external_id", "gene_symbol", "taxon"}
    assert any(
        finding.status is ValidationFindingStatus.RESOLVED
        for finding in gene_result.envelope.validation_findings
    )

    allele_result = run_validation_supervisor(
        DomainEnvelope(
            envelope_id="allele-env",
            domain_pack_id="agr.alliance.allele",
            objects=[
                CuratableObjectEnvelope(
                    object_type="AllelePaperEvidenceAssociation",
                    pending_ref_id="allele-association-1",
                    payload={},
                )
            ],
        ),
        alliance_registry.get_pack("agr.alliance.allele"),
    )
    assert any(
        finding.code == "alliance.allele.association_refs_missing"
        for finding in allele_result.envelope.validation_findings
    )

    chemical_result = run_validation_supervisor(
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
                        "source_chemical_mention": "bad chemical",
                        "evidence_record_ids": ["evidence-1"],
                        "confidence": "high",
                    },
                )
            ],
        ),
        alliance_registry.get_pack("agr.alliance.chemical_condition"),
    )
    assert any(
        finding.code == "domain_pack.curie_prefix_mismatch"
        for finding in chemical_result.envelope.validation_findings
    )

    disease_result = run_validation_supervisor(
        DomainEnvelope(
            envelope_id="disease-env",
            domain_pack_id="agr.alliance.disease",
            objects=[
                CuratableObjectEnvelope(
                    object_type="DiseaseAnnotation",
                    pending_ref_id="disease-1",
                    payload={
                        "disease_annotation_object": {
                            "curie": "DOID:0050434",
                            "name": "Andersen-Tawil syndrome",
                        },
                        "condition_relations": [
                            {
                                "condition_relation_type": {
                                    "name": "has_condition",
                                }
                            }
                        ],
                        "single_reference": {"curie": "PMID:1"},
                        "evidence_code_curies": ["ECO:0000315"],
                        "data_provider": {"abbreviation": "WB"},
                    },
                )
            ],
        ),
        alliance_registry.get_pack("agr.alliance.disease"),
    )
    assert any(
        finding.code == "domain_pack.validator_binding_planned"
        for finding in disease_result.envelope.validation_findings
    )
    assert any(
        finding.code == "domain_pack.validator_binding_blocked"
        for finding in disease_result.envelope.validation_findings
    )

    phenotype_result = run_validation_supervisor(
        DomainEnvelope(
            envelope_id="phenotype-env",
            domain_pack_id="agr.alliance.phenotype",
            objects=[
                CuratableObjectEnvelope(
                    object_type="PhenotypeSubject",
                    pending_ref_id="subject-1",
                    payload={
                        "subject_identifier": "WB:WBGene00000001",
                        "subject_type": "gene",
                        "taxon": "NCBITaxon:6239",
                    },
                )
            ],
        ),
        alliance_registry.get_pack("agr.alliance.phenotype"),
    )
    assert any(
        finding.code == "domain_pack.validator_dispatch_unavailable"
        for finding in phenotype_result.envelope.validation_findings
    )

    expression_result = run_validation_supervisor(
        DomainEnvelope(
            envelope_id="expression-env",
            domain_pack_id="agr.alliance.gene_expression",
            objects=[
                CuratableObjectEnvelope(
                    object_type="GeneExpressionAnnotation",
                    pending_ref_id="expression-1",
                    payload={},
                )
            ],
        ),
        alliance_registry.get_pack("agr.alliance.gene_expression"),
    )
    assert not any(
        finding.code == "domain_pack.validator_dispatch_unavailable"
        for finding in expression_result.envelope.validation_findings
    )
    assert any(
        finding.code == "domain_pack.validator_planned"
        for finding in expression_result.envelope.validation_findings
    )
    assert any(
        finding.code == "domain_pack.validator_blocked"
        for finding in expression_result.envelope.validation_findings
    )
