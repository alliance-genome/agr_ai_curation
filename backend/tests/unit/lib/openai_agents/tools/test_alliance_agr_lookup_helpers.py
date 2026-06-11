"""Alliance-owned lookup projection helper tests."""

from __future__ import annotations

from agr_ai_curation_alliance.tools.agr_lookup import (
    ALLIANCE_CURATION_DB_PROVIDER,
    alliance_projection_type,
    bulk_item_status_from_lookup_status,
    candidate_from_result,
    projection_from_entity_match,
    projection_from_result,
)
from agr_ai_curation_runtime.agr_lookup import LOOKUP_STATUS_TRANSIENT


def test_alliance_gene_projection_metadata():
    projection = projection_from_result(
        "get_gene_by_id",
        {
            "curie": "WB:WBGene00000298",
            "symbol": "cat-4",
            "taxon": "NCBITaxon:6239",
            "gene_type": "protein_coding_gene",
        },
    )

    assert projection["provider"] == ALLIANCE_CURATION_DB_PROVIDER
    assert projection["projection_type"] == "gene_reference"
    assert projection["object_type"] == "Gene"
    assert projection["resolved_id"] == "WB:WBGene00000298"
    assert projection["resolved_label"] == "cat-4"
    assert projection["source"] == {
        "tool_name": "agr_curation_query",
        "method": "get_gene_by_id",
    }


def test_alliance_allele_projection_metadata():
    projection = projection_from_result(
        "get_allele_by_id",
        {
            "curie": "WB:WBVar00000001",
            "symbol": "e1370",
            "taxon": "NCBITaxon:6239",
        },
    )

    assert projection["projection_type"] == "allele_reference"
    assert projection["object_type"] == "Allele"
    assert projection["resolved_id"] == "WB:WBVar00000001"
    assert projection["resolved_label"] == "e1370"


def test_alliance_ontology_projection_metadata():
    projection = projection_from_result(
        "search_go_terms",
        {
            "curie": "GO:0003674",
            "name": "molecular function",
            "namespace": "molecular_function",
        },
    )

    assert projection["projection_type"] == "ontology_term_reference"
    assert projection["object_type"] == "OntologyTerm"
    assert projection["resolved_id"] == "GO:0003674"
    assert projection["resolved_label"] == "molecular function"


def test_alliance_vocabulary_projection_metadata():
    projection = projection_from_result(
        "map_curies_to_names",
        {
            "curie": "AGR:0000001",
            "name": "is_implicated_in",
        },
    )

    assert alliance_projection_type("map_curies_to_names") == "vocabulary_term_reference"
    assert projection["projection_type"] == "vocabulary_term_reference"
    assert projection["object_type"] == "VocabularyTerm"
    assert projection["resolved_id"] == "AGR:0000001"


def test_alliance_controlled_vocabulary_projection_metadata():
    projection = projection_from_result(
        "get_vocabulary_term",
        {
            "id": 101,
            "internal_id": 101,
            "vocabulary": "Disease Relation",
            "term_name": "is_implicated_in",
            "name": "is_implicated_in",
            "abbreviation": "implicated",
            "obsolete": False,
        },
    )

    assert alliance_projection_type("get_vocabulary_term") == "vocabulary_term_reference"
    assert alliance_projection_type("search_vocabulary_terms") == "vocabulary_term_reference"
    assert projection["projection_type"] == "vocabulary_term_reference"
    assert projection["object_type"] == "VocabularyTerm"
    assert projection["resolved_id"] == 101
    assert projection["resolved_label"] == "is_implicated_in"
    assert projection["provider_data"]["vocabulary"] == "Disease Relation"
    assert projection["provider_data"]["abbreviation"] == "implicated"


def test_alliance_controlled_vocabulary_projection_preserves_zero_id():
    projection = projection_from_result(
        "get_vocabulary_term",
        {
            "id": 0,
            "internal_id": 0,
            "vocabulary": "Disease Relation",
            "term_name": "is_implicated_in",
            "name": "is_implicated_in",
            "obsolete": False,
        },
    )

    assert projection["resolved_id"] == 0
    assert projection["projection_key"] == "0"


def test_alliance_entity_projection_and_candidate_metadata():
    projection = projection_from_entity_match(
        "map_entity_names_to_curies",
        {
            "entity_curie": "WB:WBGene00000298",
            "entity": "cat-4",
            "match_type": "exact",
        },
        taxon_id="NCBITaxon:6239",
    )
    candidate = candidate_from_result(
        "map_entity_names_to_curies",
        {
            "curie": "WB:WBGene00000298",
            "symbol": "cat-4",
            "match_type": "exact",
        },
    )

    assert projection["projection_type"] == "entity_reference"
    assert projection["object_type"] == "Entity"
    assert projection["provider_data"]["taxon"] == "NCBITaxon:6239"
    assert candidate["provider"] == ALLIANCE_CURATION_DB_PROVIDER
    # The candidate is a lightweight pointer; the full projection is carried
    # once under result_projections and is not re-embedded in the candidate.
    # It keeps a scalar object_type so it stays self-describing on its own.
    assert "projection" not in candidate
    assert candidate["object_type"] == "Entity"
    assert candidate["candidate_id"] == "WB:WBGene00000298"
    assert candidate["candidate_label"] == "cat-4"
    assert candidate["match_type"] == "exact"


def test_alliance_bulk_status_owns_detail_lookup_stages():
    assert (
        bulk_item_status_from_lookup_status(
            LOOKUP_STATUS_TRANSIENT,
            count=0,
            attempts=[
                {
                    "attempted_query": {
                        "lookup_stage": "batch_fetch_gene_details",
                    },
                },
            ],
        )
        == "detail_failure"
    )
