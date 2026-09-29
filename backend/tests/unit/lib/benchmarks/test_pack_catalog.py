"""The benchmark pack catalog: explicit field facts from pack declarations, never names."""

import json

import pytest

from src.lib.benchmarks.pack_catalog import benchmark_pack_catalog
from src.lib.flows.validation_attachments import domain_pack_validation_registries


def catalog(pack_id):
    return benchmark_pack_catalog(domain_pack_validation_registries()[pack_id].domain_pack)


def field(result, object_type, path):
    matches = [item for item in result["fields"]
               if item["object_type"] == object_type and item["path"] == path]
    assert len(matches) == 1, (object_type, path, len(matches))
    return matches[0]


def test_disease_family_roles_and_identity():
    result = catalog("agr.alliance.disease")
    assert (result["pack_id"], result["pack_version"]) == ("agr.alliance.disease", "0.1.0")
    assert result["pack_label"] == "Alliance Disease Domain Pack"
    roles = {kind["object_type"]: kind["role"] for kind in result["record_kinds"]}
    assert roles["AGMDiseaseAnnotation"] == "curatable" and roles["DOTerm"] == "supporting"
    assert result["families"] == [{
        "id": "disease_annotations", "label": "Disease annotations",
        "fallback_object_type": "DiseaseAnnotation",
        "object_types": ["DiseaseAnnotation", "GeneDiseaseAnnotation",
                         "AlleleDiseaseAnnotation", "AGMDiseaseAnnotation"]}]
    assert {item["object_type"] for item in result["fields"]} == {
        "DiseaseAnnotation", "GeneDiseaseAnnotation", "AlleleDiseaseAnnotation",
        "AGMDiseaseAnnotation"}


def test_resolvable_value_facts():
    result = catalog("agr.alliance.disease")
    curie = field(result, "AGMDiseaseAnnotation", "disease_annotation_object.curie")
    assert curie["shape"] == "text" and curie["is_identifier"] is True
    assert curie["validator_binding_id"] == "disease_ontology_term_lookup"
    assert curie["validator_written"] is False
    state = field(result, "AGMDiseaseAnnotation", "disease_annotation_object.resolution_state")
    assert state["validator_written"] is True
    container = field(result, "AGMDiseaseAnnotation", "disease_annotation_object")
    assert container["shape"] == "object" and container["is_pointer"] is False


def test_paper_wording_never_carries_a_validator_binding():
    # Review Focus 2: the mention leaf is written by the extractor, not a validator.
    result = catalog("agr.alliance.disease")
    mention = field(result, "AGMDiseaseAnnotation", "disease_annotation_object.mention")
    assert mention["validator_binding_id"] is None and mention["validator_written"] is False
    pheno = catalog("agr.alliance.phenotype")
    assert field(pheno, "PhenotypeAnnotation", "phenotype_terms[].mention")[
        "validator_binding_id"] is None


def test_phenotype_terms_are_compared_from_each_item():
    result = catalog("agr.alliance.phenotype")
    terms = field(result, "PhenotypeAnnotation", "phenotype_terms")
    assert terms["shape"] == "object_list" and terms["is_pointer"] is False
    curie = field(result, "PhenotypeAnnotation", "phenotype_terms[].curie")
    assert curie["shape"] == "text_from_each_item" and curie["inside_list"] is False
    assert curie["validator_binding_id"] == "phenotype_term_ontology_validator"
    assert curie["is_identifier"] is True
    subject = field(result, "PhenotypeAnnotation", "phenotype_annotation_subject")
    assert subject["shape"] == "object" and subject["is_pointer"] is False
    mentions = field(result, "PhenotypeAnnotation", "phenotype_terms[].source_mentions")
    assert mentions["inside_list"] is True
    assert not any("[0]" in item["path"] for item in result["fields"])


def test_lists_inside_lists_and_links_are_marked():
    result = catalog("agr.alliance.disease")
    summary = field(result, "AGMDiseaseAnnotation",
                    "condition_relations[].conditions[].condition_summary")
    assert summary["inside_list"] is True and summary["free_text"] is True
    links = field(result, "AGMDiseaseAnnotation", "evidence_record_ids")
    assert links["is_pointer"] is True


def test_prose_is_free_text_and_never_a_default():
    for pack_id, object_type, prose in [
        ("agr.alliance.disease", "DiseaseAnnotation", ["rationale"]),
        ("agr.alliance.gene_expression", "GeneExpressionAnnotation",
         ["rationale", "where_expressed_statement"]),
    ]:
        result = catalog(pack_id)
        defaults = [entry["path"] for entry in result["default_fields"][object_type]]
        for path in prose:
            assert field(result, object_type, path)["free_text"] is True
            assert path not in defaults


def test_defaults_follow_the_workspace_layout_with_mention_fallbacks():
    disease = catalog("agr.alliance.disease")["default_fields"]["DiseaseAnnotation"]
    assert disease[0] == {"path": "disease_annotation_object.curie",
                          "if_not_validated": "disease_annotation_object.mention"}
    paths = [entry["path"] for entry in disease]
    assert len(paths) == len(set(paths))
    assert not any(path.endswith(("resolution_state", "lookup_outcome")) for path in paths)
    # condition_relations has neither an id nor a label display leaf, so it is dropped.
    assert not any(path.startswith("condition_relations") for path in paths)
    expression = catalog("agr.alliance.gene_expression")["default_fields"][
        "GeneExpressionAnnotation"]
    assert expression[0] == {"path": "expression_annotation_subject.primary_external_id",
                             "if_not_validated": "expression_annotation_subject.mention"}
    phenotype = catalog("agr.alliance.phenotype")["default_fields"]["PhenotypeAnnotation"]
    assert {"path": "phenotype_terms[].curie",
            "if_not_validated": "phenotype_terms[].mention"} in phenotype


def test_catalog_omits_descriptions_and_enum_values():
    encoded = json.dumps(catalog("agr.alliance.disease"))
    assert "description" not in encoded and "enum_values" not in encoded


@pytest.mark.parametrize("pack_id", ["gene", "agr.alliance.allele"])
def test_builtin_packs_still_describe_their_kinds(pack_id):
    result = catalog(pack_id)
    assert result["pack_id"] == pack_id and result["record_kinds"]
