"""Real-pack workspace_display regression tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    DomainEnvelopeStatus,
)
from src.schemas.domain_pack_metadata import DomainPackMetadata, DomainPackObjectDefinition


pytestmark = pytest.mark.provider_agnostic_domain_pack

REPO_ROOT = Path(__file__).resolve().parents[5]
PACK_ROOT = REPO_ROOT / "packages" / "alliance" / "domain_packs"
PACK_PATHS = {
    "agr.alliance.allele": PACK_ROOT / "allele" / "domain_pack.yaml",
    "agr.alliance.disease": PACK_ROOT / "disease" / "domain_pack.yaml",
    "agr.alliance.gene_expression": PACK_ROOT / "gene_expression" / "domain_pack.yaml",
    "agr.alliance.phenotype": PACK_ROOT / "phenotype" / "domain_pack.yaml",
    "gene": PACK_ROOT / "gene" / "domain_pack.yaml",
}


def _pack(pack_id: str) -> DomainPackMetadata:
    return load_domain_pack_metadata(PACK_PATHS[pack_id])


def _draft_paths(
    pack_id: str,
    object_type: str,
    payload: dict[str, Any],
    *,
    object_role: str | None = None,
) -> list[str]:
    metadata = _pack(pack_id)
    object_metadata = {"object_role": object_role} if object_role is not None else {}
    envelope = DomainEnvelope(
        envelope_id="e",
        domain_pack_id=metadata.pack_id,
        domain_pack_version=metadata.version,
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[
            CuratableObjectEnvelope(
                object_type=object_type,
                object_id="o1",
                status=CuratableObjectStatus.PENDING,
                payload=payload,
                metadata=object_metadata,
            ),
        ],
    )
    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope,
        envelope_revision=1,
    )
    return [
        field["field_path"]
        for field in rows[0].metadata.get("workspace_fields", [])
    ]


def _workspace_fields(
    pack_id: str,
    object_type: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    metadata = _pack(pack_id)
    envelope = DomainEnvelope(
        envelope_id="e",
        domain_pack_id=metadata.pack_id,
        domain_pack_version=metadata.version,
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[
            CuratableObjectEnvelope(
                object_type=object_type,
                object_id="o1",
                status=CuratableObjectStatus.PENDING,
                payload=payload,
            ),
        ],
    )
    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope,
        envelope_revision=1,
    )
    return rows[0].metadata.get("workspace_fields", [])


def _configured_workspace_paths(object_definition: DomainPackObjectDefinition) -> list[str]:
    display = object_definition.metadata.get("workspace_display")
    if not isinstance(display, dict):
        return []

    paths = [
        path
        for path in display.get("summary_fields", [])
        if isinstance(path, str) and path.strip()
    ]
    groups = display.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            paths.extend(
                path
                for path in group.get("fields", [])
                if isinstance(path, str) and path.strip()
            )
    return paths


@pytest.mark.parametrize("pack_id", sorted(PACK_PATHS))
def test_workspace_display_paths_are_declared_fields(pack_id: str):
    metadata = _pack(pack_id)

    failures: list[str] = []
    for object_definition in metadata.object_definitions:
        declared = {field.field_path for field in object_definition.fields}
        for path in _configured_workspace_paths(object_definition):
            if path not in declared:
                failures.append(f"{object_definition.object_type}.{path}")

    assert failures == []


def test_gene_expression_hides_mirror_and_under_development_fields():
    payload = {
        "expression_annotation_subject": {
            "primary_external_id": "WB:WBGene00006789",
            "gene_symbol": "pef-1",
        },
        "expression_experiment": {
            "entity_assayed": {
                "primary_external_id": "WB:WBGene00006789",
                "gene_symbol": "pef-1",
            },
            "expression_assay_used": {
                "curie": "MMO:0000640",
                "name": "GFP reporter",
            },
            "single_reference": {"reference_id": 1},
            "detection_reagents": [],
            "specimen_alleles": [],
        },
        "single_reference": {"reference_id": 1},
        "where_expressed_statement": "ciliated sensory neuron",
        "when_expressed_stage_name": "L4",
        "relation": {"name": "expressed_in"},
        "data_provider": {"abbreviation": "WB"},
        "internal": False,
    }

    paths = _draft_paths(
        "agr.alliance.gene_expression",
        "GeneExpressionAnnotation",
        payload,
    )

    assert "expression_experiment.entity_assayed.primary_external_id" not in paths
    assert "expression_experiment.entity_assayed.gene_symbol" not in paths
    assert "expression_experiment.single_reference.reference_id" not in paths
    assert "expression_experiment.detection_reagents" not in paths
    assert "internal" not in paths
    assert "expression_annotation_subject.gene_symbol" in paths
    assert "single_reference.reference_id" in paths


def test_gene_expression_render_hints_target_curie_and_collapsed_fields():
    payload = {
        "expression_annotation_subject": {"gene_symbol": "pef-1"},
        "expression_experiment": {
            "expression_assay_used": {"curie": "MMO:0000640"},
        },
        "single_reference": {"reference_id": 1, "curie": "FB:FBrf0000001"},
        "where_expressed_statement": "ciliated sensory neuron",
        "when_expressed_stage_name": "L4",
        "expression_pattern": {
            "where_expressed": {
                "anatomical_structure": {"curie": "WBbt:0005800"},
                "anatomical_structure_uberon_terms": ["UBERON:0001000"],
                "cellular_component": {"curie": "GO:0005737"},
                "cellular_component_qualifiers": ["part_of"],
            },
            "when_expressed": {
                "developmental_stage_start": {"curie": "WBls:0000041"},
                "stage_uberon_slim_terms": ["UBERON:0000066"],
            },
        },
        "condition_relations": [{"condition_summary": "heat shock"}],
    }

    fields = _workspace_fields(
        "agr.alliance.gene_expression",
        "GeneExpressionAnnotation",
        payload,
    )
    by_path = {field["field_path"]: field for field in fields}

    assert by_path["expression_experiment.expression_assay_used.curie"][
        "metadata"
    ]["render_as"] == "curie-chip"
    assert by_path["expression_pattern.when_expressed.developmental_stage_start.curie"][
        "metadata"
    ]["render_as"] == "curie-chip"
    assert by_path["expression_pattern.where_expressed.anatomical_structure_uberon_terms"][
        "metadata"
    ]["hide_when_empty"] is True
    assert by_path["condition_relations"]["metadata"]["render_as"] == "sub-table"
    assert "single_reference.curie" not in by_path


def test_gene_groups_show_identity_and_hide_plumbing():
    payload = {
        "mention": "pef-1",
        "gene_symbol": "pef-1",
        "primary_external_id": "WB:WBGene00006789",
        "taxon": "NCBITaxon:6239",
        "species": "Caenorhabditis elegans",
        "proposed_gene_symbol": "pef-1",
        "data_provider_hint": "WB",
        "confidence": "high",
        "section": "Results",
        "page": 4,
        "taxon_hint": "NCBITaxon:6239",
        "chunk_id": "abc",
        "evidence_record_id": "e1",
    }

    paths = _draft_paths(
        "gene",
        "gene_mention_evidence",
        payload,
        object_role="validated_reference",
    )

    assert "gene_symbol" in paths
    assert "primary_external_id" in paths
    assert "taxon" in paths
    assert "species" in paths
    assert "taxon_hint" not in paths
    assert "chunk_id" not in paths
    assert "evidence_record_id" not in paths


def test_allele_association_promotes_identifier_hides_routing():
    payload = {
        "association_kind": "allele_paper_evidence",
        "allele_identifier": "WB:WBVar00012345",
        "evidence_record_ids": ["evidence-7cad0b8f"],
    }

    paths = _draft_paths(
        "agr.alliance.allele",
        "AllelePaperEvidenceAssociation",
        payload,
    )

    assert "allele_identifier" in paths
    assert "association_kind" not in paths
    assert "evidence_record_ids[0]" not in paths
    assert "evidence_record_ids" not in paths


def test_disease_groups_hide_plumbing_and_keep_curatable():
    payload = {
        "disease_annotation_object": {"curie": "DOID:0050200", "name": "x"},
        "disease_annotation_subject": {
            "subject_identifier": "WB:WBGene1",
            "subject_type": "gene",
            "subject_label": "pef-1",
        },
        "disease_relation_name": "is_implicated_in",
        "evidence_code_curies": ["ECO:0000033"],
        "data_provider": {"abbreviation": "WB"},
        "confidence": "high",
        "annotation_type_vocabulary": "v",
        "annotation_type_id": "i",
    }

    paths = _draft_paths(
        "agr.alliance.disease",
        "AGMDiseaseAnnotation",
        payload,
    )

    assert "disease_annotation_object.curie" in paths
    assert "disease_annotation_subject.subject_identifier" in paths
    assert "evidence_code_curies" in paths
    assert "confidence" not in paths
    assert "annotation_type_vocabulary" not in paths
    assert "annotation_type_id" not in paths


def test_disease_term_curie_workspace_field_stays_visible_when_empty():
    payload = {
        "disease_annotation_object": {"name": "x"},
        "disease_annotation_subject": {
            "subject_identifier": "WB:WBGene1",
            "subject_type": "gene",
        },
        "disease_relation_name": "is_implicated_in",
    }

    fields = _workspace_fields(
        "agr.alliance.disease",
        "AGMDiseaseAnnotation",
        payload,
    )
    by_path = {field["field_path"]: field for field in fields}

    curie_field = by_path["disease_annotation_object.curie"]
    assert curie_field["value"] is None
    assert curie_field["metadata"]["render_as"] == "curie-chip"


def test_phenotype_hides_lookup_hints_and_scaffolding():
    payload = {
        "phenotype_annotation_object": "dye-filling defect",
        "negated": False,
        "phenotype_terms": [
            {
                "curie": "WBPhenotype:0001191",
                "label": "dye-filling defect",
                "ontology_lookup_hint": {
                    "data_provider": "WB",
                    "taxon_id": "NCBITaxon:6239",
                },
                "export_state": "blocked",
                "write_blocked_reason": "x",
            }
        ],
        "annotation_kind": "phenotype_assertion",
        "evidence_record_ids": ["e1"],
    }

    paths = _draft_paths(
        "agr.alliance.phenotype",
        "PhenotypeAnnotation",
        payload,
    )

    assert "phenotype_annotation_object" in paths
    assert "phenotype_terms[0].curie" in paths
    assert not any("ontology_lookup_hint" in path for path in paths)
    assert not any(
        "export_state" in path or "write_blocked_reason" in path
        for path in paths
    )
    assert "annotation_kind" not in paths


def test_phenotype_term_curie_workspace_field_carries_term_chip_hint():
    payload = {
        "phenotype_annotation_object": "dye-filling defect",
        "phenotype_terms": [{"curie": "WBPhenotype:0001191"}],
    }

    fields = _workspace_fields(
        "agr.alliance.phenotype",
        "PhenotypeAnnotation",
        payload,
    )
    by_path = {field["field_path"]: field for field in fields}

    assert by_path["phenotype_terms[0].curie"]["metadata"]["render_as"] == "term-chip"
