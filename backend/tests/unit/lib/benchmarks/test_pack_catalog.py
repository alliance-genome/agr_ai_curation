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


def test_allele_root_values_read_their_declared_result_binding():
    # The allele pack declares its bindings as validation_result_binding_id on
    # root-level leaves of a resolvable object root.
    result = catalog("agr.alliance.allele")
    kind = "AllelePaperEvidenceAssociation"
    for path in ["allele_label", "allele_identifier", "allele_taxon"]:
        assert field(result, kind, path)["validator_binding_id"] == (
            "allele_mention_reference_validation"), path
    assert field(result, kind, "allele_identifier")["is_identifier"] is True
    mention = field(result, kind, "mention")
    assert mention["validator_binding_id"] is None and mention["validator_written"] is False
    defaults = result["default_fields"][kind]
    assert defaults[0] == {"path": "allele_label", "if_not_validated": "mention"}
    assert {"path": "allele_identifier", "if_not_validated": "mention"} in defaults
    paths = [entry["path"] for entry in defaults]
    assert not {"resolution_state", "lookup_outcome", "validator_explanation",
                "validator_curator_message", "rationale"} & set(paths)


@pytest.mark.parametrize("pack_id,object_type,prose", [
    ("agr.alliance.allele", "AllelePaperEvidenceAssociation", ["rationale"]),
    ("agr.alliance.go", "GOCuratableObject", ["rationale", "blocking_reasons"]),
])
def test_model_written_prose_is_declared_free_text(pack_id, object_type, prose):
    result = catalog(pack_id)
    defaults = [entry["path"] for entry in result["default_fields"][object_type]]
    for path in prose:
        assert field(result, object_type, path)["free_text"] is True, path
        assert path not in defaults, path
    if pack_id == "agr.alliance.go":
        # Annotation extensions are structured relation(term) values, not prose.
        assert field(result, object_type, "annotation_extensions")["free_text"] is False


@pytest.mark.parametrize("pack_id,object_type,path", [
    ("agr.alliance.disease", "AGMDiseaseAnnotation", "evidence_records"),
    ("agr.alliance.gene_expression", "GeneExpressionAnnotation",
     "expression_experiment.detection_reagents"),
    ("agr.alliance.gene_expression", "GeneExpressionAnnotation",
     "expression_experiment.specimen_alleles"),
])
def test_lists_of_undeclared_objects_are_not_comparable(pack_id, object_type, path):
    assert field(catalog(pack_id), object_type, path)["shape"] == "other"


def _fixture_pack(fields):
    from types import SimpleNamespace

    from src.schemas.domain_pack_metadata import DomainPackMetadata

    metadata = DomainPackMetadata.model_validate({
        "pack_id": "fixture.pack", "display_name": "Fixture", "version": "0.1.0",
        "metadata_api_version": "1.0.0",
        "object_definitions": [{
            "object_type": "Thing", "display_name": "Thing",
            "metadata": {"object_role": "curatable_unit"}, "fields": fields,
        }],
        "metadata": {"object_role_key": "object_role"},
    })
    return SimpleNamespace(metadata=metadata)


def test_unexported_containers_hide_their_children():
    result = benchmark_pack_catalog(_fixture_pack([
        {"field_path": "hidden", "field_type": "object", "metadata": {"exported": False}},
        {"field_path": "hidden.note", "field_type": "string"},
        {"field_path": "visible", "field_type": "object"},
        {"field_path": "visible.proposal", "field_type": "string",
         "metadata": {"exported": False}},
        {"field_path": "title", "field_type": "string"},
    ]))
    paths = {item["path"]: item for item in result["fields"]}
    assert set(paths) == {"visible", "title"}
    # Its only child is unexported, so the container has nothing to compare.
    assert paths["visible"]["shape"] == "other"
