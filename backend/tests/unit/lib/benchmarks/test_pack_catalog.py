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


def test_pack_level_binding_declarations_are_read():
    # GO and one gene-expression value declare their bindings only under the
    # pack's validator_bindings.active[].applies_to.
    go = catalog("agr.alliance.go")
    kind = "GOCuratableObject"
    for path, binding in [("gene_product.curie", "go_gene_product_validation"),
                          ("gene_product.label", "go_gene_product_validation"),
                          ("go_term.curie", "go_term_validation"),
                          ("reference_curie.curie", "go_reference_validation"),
                          ("with_from[].curie", "go_with_from_gene_validation")]:
        assert field(go, kind, path)["validator_binding_id"] == binding, path
    assert field(go, kind, "gene_product.mention")["validator_binding_id"] is None
    assert field(go, kind, "with_from[].mention")["validator_binding_id"] is None
    assert {"path": "gene_product.curie", "if_not_validated": "gene_product.mention"} in (
        go["default_fields"][kind])
    expression = catalog("agr.alliance.gene_expression")
    relation = field(expression, "GeneExpressionAnnotation", "relation.name")
    assert relation["validator_binding_id"] == "relation_vocabulary_validation"
    assert field(expression, "GeneExpressionAnnotation", "relation.mention")[
        "validator_binding_id"] is None


def test_relation_name_default_falls_back_to_its_mention():
    from src.lib.benchmarks.pack_catalog import _ObjectFacts

    pack = domain_pack_validation_registries()["agr.alliance.gene_expression"].domain_pack
    metadata = pack.metadata
    obj = next(o for o in metadata.object_definitions
               if o.object_type == "GeneExpressionAnnotation")
    facts = _ObjectFacts(obj, {m.model_id: m for m in metadata.model_definitions},
                         {o.object_type: o.model_ref for o in metadata.object_definitions},
                         metadata)
    assert facts.default_fields(["relation"]) == [
        {"path": "relation.name", "if_not_validated": "relation.mention"}]


def test_pack_and_field_binding_declarations_must_agree():
    from types import SimpleNamespace

    from src.schemas.domain_pack_metadata import DomainPackMetadata

    metadata = DomainPackMetadata.model_validate({
        "pack_id": "fixture.pack", "display_name": "Fixture", "version": "0.1.0",
        "metadata_api_version": "1.0.0",
        "object_definitions": [{
            "object_type": "Thing", "display_name": "Thing",
            "metadata": {"object_role": "curatable_unit"},
            "fields": [{"field_path": "term", "field_type": "string",
                        "metadata": {"validator_binding_id": "field_check"}}],
        }],
        "metadata": {"object_role_key": "object_role", "validator_bindings": {"active": [{
            "binding_id": "pack_check",
            "validator_agent": {"package_id": "fixture.pack", "agent_id": "checker"},
            "applies_to": {"object_types": ["Thing"], "field_paths": ["term"]},
        }]}},
    })
    with pytest.raises(ValueError, match="two different validator bindings"):
        benchmark_pack_catalog(SimpleNamespace(metadata=metadata))


def test_value_roles_come_from_the_declared_display():
    result = catalog("agr.alliance.gene_expression")
    kind = "GeneExpressionAnnotation"
    subject = "expression_annotation_subject"
    assert field(result, kind, subject)["has_value_roles"] is True
    assert field(result, kind, f"{subject}.primary_external_id")["value_role"] == "id"
    assert field(result, kind, f"{subject}.gene_symbol")["value_role"] == "label"
    assert field(result, kind, f"{subject}.mention")["value_role"] == "mention"
    for note in ("resolution_state", "lookup_outcome", "validator_explanation",
                 "validator_curator_message"):
        assert field(result, kind, f"{subject}.{note}")["value_role"] == "working_note"
    # The relation declares its roles on its model even though the object has no binding.
    assert field(result, kind, "relation")["has_value_roles"] is True
    assert field(result, kind, "relation.name")["value_role"] == "label"
    assert field(result, kind, "relation.vocabulary")["value_role"] is None
    # A grouping container composes its parts; it has no roles of its own.
    assert field(result, kind, "expression_experiment")["has_value_roles"] is False
    assert field(result, kind, "condition_relations")["has_value_roles"] is False
    assert field(result, kind, "rationale")["value_role"] is None


def test_lists_of_values_carry_roles_on_each_item():
    result = catalog("agr.alliance.phenotype")
    terms = field(result, "PhenotypeAnnotation", "phenotype_terms")
    assert terms["shape"] == "object_list" and terms["has_value_roles"] is True
    assert field(result, "PhenotypeAnnotation", "phenotype_terms[].curie")["value_role"] == "id"
    assert field(result, "PhenotypeAnnotation", "phenotype_terms[].label")["value_role"] == "label"


def test_a_reference_is_benchmarked_by_its_curie():
    # The display id (the database reference_id) stays the identifier everywhere
    # else; the declared benchmark_id makes the CURIE the value's id for benchmarks.
    result = catalog("agr.alliance.gene_expression")
    kind = "GeneExpressionAnnotation"
    for reference in ("single_reference", "expression_experiment.single_reference"):
        curie = field(result, kind, f"{reference}.curie")
        assert curie["value_role"] == "id" and curie["is_identifier"] is False, reference
        reference_id = field(result, kind, f"{reference}.reference_id")
        assert reference_id["value_role"] is None, reference
        assert reference_id["is_identifier"] is True, reference
        assert field(result, kind, f"{reference}.title")["value_role"] == "label", reference


# Leaves whose value declares a benchmark_id other than its display id.
BENCHMARK_ID_LEAVES = {
    ("agr.alliance.gene_expression", "single_reference.curie"),
    ("agr.alliance.gene_expression", "single_reference.reference_id"),
    ("agr.alliance.gene_expression", "expression_experiment.single_reference.curie"),
    ("agr.alliance.gene_expression", "expression_experiment.single_reference.reference_id"),
}


def test_value_role_id_matches_is_identifier_everywhere():
    for pack_id in ("agr.alliance.gene_expression", "agr.alliance.phenotype",
                    "agr.alliance.disease", "agr.alliance.go"):
        for item in catalog(pack_id)["fields"]:
            same = (item["value_role"] == "id") == item["is_identifier"]
            assert same != ((pack_id, item["path"]) in BENCHMARK_ID_LEAVES), (
                pack_id, item["path"])
            assert (item["value_role"] == "working_note") == item["validator_written"], (
                pack_id, item["path"])


def test_system_filled_fields_are_declared():
    result = catalog("agr.alliance.gene_expression")
    filled = {item["path"] for item in result["fields"] if item["system_filled"]}
    assert filled == {"unique_id", "date_created", "expression_experiment.unique_id"}
    for pack_id in ("agr.alliance.phenotype", "agr.alliance.disease", "agr.alliance.go"):
        assert not any(item["system_filled"] for item in catalog(pack_id)["fields"]), pack_id


@pytest.mark.parametrize("pack_id", ["agr.alliance.gene_expression", "agr.alliance.phenotype",
                                     "agr.alliance.disease", "agr.alliance.go",
                                     "agr.alliance.allele"])
def test_every_field_binding_names_a_declared_validation(pack_id):
    result = catalog(pack_id)
    declared = {item["binding_id"]: item["label"] for item in result["validations"]}
    assert len(declared) == len(result["validations"])
    assert all(label.strip() for label in declared.values())
    used = {item["validator_binding_id"] for item in result["fields"]} - {None}
    assert used <= set(declared), sorted(used - set(declared))


def test_validations_keep_the_pack_labels():
    declared = {item["binding_id"]: item["label"]
                for item in catalog("agr.alliance.gene_expression")["validations"]}
    assert declared["subject_gene_validation"] == "Subject gene validation"
    assert declared["relation_vocabulary_validation"] == "Relation vocabulary lookup"
    # Bindings still being built are declared too, so the portal can name them.
    phenotype = {item["binding_id"] for item in catalog("agr.alliance.phenotype")["validations"]}
    assert "phenotype_subject_entity_validator" in phenotype


def test_validations_carry_their_declared_state():
    under_development = {
        "agr.alliance.gene_expression": {"reagent_context_materialization"},
        "agr.alliance.phenotype": {"phenotype_subject_entity_validator",
                                   "phenotype_reference_validator"},
        "agr.alliance.disease": {"disease_reference_materialization"},
        "agr.alliance.allele": {"source_reference_validation"},
        "agr.alliance.go": set(),
    }
    for pack_id, expected in under_development.items():
        validations = catalog(pack_id)["validations"]
        assert {item["state"] for item in validations} <= {"active", "under_development"}
        found = {item["binding_id"] for item in validations
                 if item["state"] == "under_development"}
        assert found == expected, pack_id
