"""Package-owned projections keep factual fields and reject unrecognized shapes."""

from pathlib import Path

import pytest

from src.lib.config import schema_discovery
from ..packages import find_repo_root


@pytest.fixture(autouse=True)
def schemas(monkeypatch):
    packages = find_repo_root(Path(__file__)) / "packages"
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages))
    monkeypatch.syspath_prepend(str(packages / "alliance" / "python" / "src"))
    schema_discovery.reset_cache()
    yield schema_discovery.discover_agent_schemas(force_reload=True)
    schema_discovery.reset_cache()


@pytest.mark.parametrize("schema, record, field, identity", [
    ("GeneResultEnvelope", {"curie": "RGD:1", "symbol": "Abc", "taxon": "NCBITaxon:10116"}, "gene_candidates", "RGD:1"),
    ("AlleleResultEnvelope", {"curie": "MGI:1", "symbol": "Abc<flox>", "data_provider": None}, "allele_candidates", "MGI:1"),
    ("AgmValidationResult", {"curie": "ZFIN:1", "name": "mutant"}, "agm_candidates", "ZFIN:1"),
    ("OntologyTermValidationResult", {"curie": "UBERON:1", "name": "tissue"}, "ontology_term_candidates", "UBERON:1"),
    ("ControlledVocabularyValidationResult", {"id": 12, "vocabulary": "units", "name": "day", "obsolete": False}, "controlled_vocabulary_candidates", "12"),
    ("DataProviderValidationResult", {"abbreviation": "RGD", "taxon_id": "NCBITaxon:10116", "taxon_matches": False}, "data_provider_candidates", "RGD"),
    ("GOTermResultEnvelope", {"id": "GO:1", "name": "binding", "aspect": "molecular_function", "definition": {"text": "A definition"}, "synonyms": [{"name": "an alias"}]}, "results", "GO:1"),
    ("ReferenceValidationResult", {"curie": "PMID:1", "title": "A title", "cross_references": ["DOI:example"]}, "candidate_references", "PMID:1"),
    ("OrthologsResult", {"geneToGeneOrthologyGenerated": {"subjectGene": {"primaryExternalId": "RGD:1"}, "objectGene": {"primaryExternalId": "MGI:2", "symbol": "Abc"}, "confidence": {"name": "high"}, "isBestScore": {"name": "Yes"}, "predictionMethodsMatched": [{"name": "method"}]}}, "orthologs", "MGI:2"),
    ("ChemicalValidationResult", {"id": "CHEBI:1", "name": "compound"}, None, "CHEBI:1"),
    ("DiseaseValidationResult", {"curie": "DOID:1", "name": "disease"}, None, "DOID:1"),
])
def test_provider_record_projection(schemas, schema, record, field, identity):
    from agr_ai_curation_alliance.compact_validation import canonical_record
    result = canonical_record(record, schemas[schema])
    assert result.candidate.value == identity
    assert result.candidate.details["source_record"] == record
    assert (field in result.result_rows) if field else not result.result_rows
    if schema == "AlleleResultEnvelope":
        assert result.result_rows[field]["data_provider"] is None
    if schema == "GOTermResultEnvelope":
        assert result.result_rows[field]["definition"] == "A definition"
        assert result.result_rows[field]["synonyms"] == ["an alias"]
    if schema == "OrthologsResult":
        assert result.result_rows[field]["confidence"] == "high"
        assert result.result_rows[field]["methods_matched"] == [{"name": "method"}]


def test_annotation_identity_is_source_record_not_shared_go_term(schemas):
    from agr_ai_curation_alliance.compact_validation import canonical_record
    record = {
        "gene_product_id": "RGD:1", "go_id": "GO:1", "go_name": None,
        "aspect": None, "evidence_code": None, "eco_id": None, "evidence_label": None,
        "references": [], "relation": None, "with_from": [], "qualifiers": [],
        "negated": False, "providers": [], "product_type": None,
        "provenance": {"source": "GO", "source_url": "https://example.org", "source_record_id": "annotation-1"},
    }
    result = canonical_record(record, schemas["GOAnnotationsResult"])
    assert result.candidate.value == "annotation-1"
    assert result.result_rows["annotations"] == record
    record["provenance"]["source_record_id"] = "annotation-2"
    other = canonical_record(record, schemas["GOAnnotationsResult"])
    assert other.candidate.value != result.candidate.value


def test_collections_include_provider_conflicts_and_reject_silent_data_loss():
    from agr_ai_curation_alliance.compact_validation import source_records
    record = {"abbreviation": "RGD", "taxon_id": "NCBITaxon:10116"}
    assert source_records("agr_curation_query", {"data": {"candidates": [record]}}) == [record]
    assert source_records("agr_curation_query", {"data": {"items": [{"results": [record]}, {"results": []}]}}) == [record]
    for malformed in ({"data": [record, "malformed"]}, {"data": {"items": [{}]}}, {"data": {"unrecognized": [record]}}):
        with pytest.raises(ValueError):
            source_records("agr_curation_query", malformed)


def test_simple_contract_requires_lookup_and_preserves_typed_judgment(schemas):
    from agr_ai_curation_alliance.compact_contracts import simple_decision_contract
    from agr_ai_curation_alliance.compact_validation import canonical_record
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace
    from src.schemas.domain_validator import DomainValidationRequest, ValidatorLookupAttempt

    request = DomainValidationRequest(
        request_id="agm", validator_binding_id="agm",
        validator_agent={"package_id": "agr.alliance", "agent_id": "agm_validation"},
        target={"domain_pack_id": "fixture"},
    )
    contract = simple_decision_contract(request, schemas["AgmValidationResult"])
    workspace = ValidatorDecisionWorkspace([contract])
    decision = contract.decision_schema(request_id="agm", status="resolved", explanation="Resolved.",
                                         scientific={"unresolved_explanations": []})
    with pytest.raises(ValueError, match="actual lookup"):
        workspace.assemble(decision)
    refs = workspace.record_lookup("agm", call_id="call-1", attempt=ValidatorLookupAttempt(
        provider="agr_curation_query", method="map_entity_curies_to_info", query={"curies": ["ZFIN:1"]},
        result_count=1, outcome="success",
    ), records=[canonical_record({"curie": "ZFIN:1", "name": "strain"}, schemas["AgmValidationResult"])])
    unresolved = contract.decision_schema(
        request_id="agm", status="unresolved", explanation="The strain identity is uncertain.",
        scientific={"unresolved_explanations": ["Supplier differs from the paper."]},
        candidates=[{"record_ref": refs[0], "disposition": "plausible", "explanation": "Name matches; supplier unclear."}],
    )
    result = workspace.assemble(unresolved)
    assert result.unresolved_explanations == ["Supplier differs from the paper."]
    assert result.agm_candidates[0].agm_id == "ZFIN:1"
    assert result.resolved_objects == []


def test_subject_route_copies_facts_but_keeps_scientific_route_reason(schemas):
    from agr_ai_curation_alliance.compact_contracts import simple_decision_contract
    from agr_ai_curation_alliance.compact_validation import canonical_record
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace
    from src.schemas.domain_validator import DomainValidationRequest, ValidatorLookupAttempt

    schema = schemas["SubjectEntityValidationResult"]
    request = DomainValidationRequest(
        request_id="subject", validator_binding_id="subject",
        validator_agent={"package_id": "agr.alliance", "agent_id": "subject_entity_validation"},
        target={"domain_pack_id": "fixture"}, selected_inputs={"subject_type": "Gene", "subject_identifier": "RGD:1"},
    )
    contract = simple_decision_contract(request, schema)
    workspace = ValidatorDecisionWorkspace([contract])
    refs = workspace.record_lookup("subject", call_id="call-1", attempt=ValidatorLookupAttempt(
        provider="agr_curation_query", method="get_gene_by_id", query={"gene_id": "RGD:1"}, result_count=1, outcome="success",
    ), records=[canonical_record({"curie": "RGD:1", "symbol": "Abc", "taxon": "NCBITaxon:10116"}, schema, request=request)])
    decision = contract.decision_schema(
        request_id="subject", status="resolved", explanation="Subject matches.",
        scientific={"route_reason": "The supplied subject type explicitly selects gene validation.", "unresolved_explanations": []},
        candidates=[{"record_ref": refs[0], "disposition": "selected", "explanation": "Identifier and taxon match."}],
    )
    result = workspace.assemble(decision)
    assert result.normalized_subject_identifier == "RGD:1"
    assert result.normalized_subject_type == "gene"
    assert result.selected_validator.validator_agent.agent_id == "gene_validation"
    assert result.selected_validator.tool_methods == ["get_gene_by_id"]
    assert result.subject_candidates[0].selected_validator == result.selected_validator
    unsupported = request.model_copy(update={"selected_inputs": {"subject_type": "unknown"}})
    with pytest.raises(ValueError, match="explicit supported"):
        canonical_record({"curie": "RGD:1", "symbol": "Abc"}, schema, request=unsupported)


def test_composite_assembles_component_facts_audits_and_partial_results(schemas):
    from agr_ai_curation_alliance.compact_conditions import condition_decision_contract
    from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord, ValidatorDecisionWorkspace
    from src.schemas.domain_validator import DomainValidationRequest, ValidatorCandidate, ValidatorLookupAttempt

    request = DomainValidationRequest(
        request_id="condition", validator_binding_id="condition",
        validator_agent={"package_id": "agr.alliance", "agent_id": "experimental_condition_validation"},
        target={"domain_pack_id": "fixture"}, expected_result_fields={"condition_class_curie": "condition_class.curie"},
        selected_inputs={"condition_class_curie": "ZECO:1", "condition_chemical_name": "compound"},
        input_selectors={"condition_class_curie": {"path": "conditions[0].condition_class.curie"}},
    )
    contract = condition_decision_contract(request, schemas["ExperimentalConditionValidationResult"])
    workspace = ValidatorDecisionWorkspace([contract])
    ref = workspace.record_lookup("condition", call_id="grouped-lookup", attempt=ValidatorLookupAttempt(
        provider="agr_curation_query", method="get_ontology_terms", query={"terms": ["ZECO:1"]},
        result_count=1, outcome="success",
    ), records=[CanonicalValidatorRecord(candidate=ValidatorCandidate(value="ZECO:1", label="condition"),
                                          values={"curie": "ZECO:1", "name": "condition"},
                                          resolved_object={"curie": "ZECO:1", "name": "condition"})])[0]
    assessment = {"record_ref": ref, "disposition": "selected", "explanation": "Class matches."}
    payload = {
        "request_id": "condition", "status": "unresolved", "explanation": "Chemical remains unresolved.",
        "candidates": [assessment],
        "slots": {"condition_class_curie": {"kind": "record", "record_ref": ref, "field": "curie"}},
        "components": [
            {"component_type": "condition_class", "status": "resolved", "candidates": [assessment],
             "slots": {"curie": {"kind": "record", "record_ref": ref, "field": "curie"}},
             "lookup_refs": ["grouped-lookup"], "explanation": "Class matches."},
            {"component_type": "condition_chemical", "status": "unresolved", "explanation": "Chemical identity is ambiguous."},
        ],
    }
    result = workspace.assemble(contract.decision_schema.model_validate(payload))
    assert result.unresolved_components == ["condition_chemical"]
    assert result.normalized_components[0].resolved_values == {"curie": "ZECO:1"}
    component = result.component_validations[0]
    assert component.selected_inputs == {"condition_class_curie": "ZECO:1"}
    assert component.field_path == "conditions[0].condition_class.curie"
    assert component.lookup_attempts[0] == result.lookup_attempts[0]
    assert component.validator_agent.agent_id == "ontology_term_validation"
    other = workspace.record_lookup("condition", call_id="other-lookup", attempt=ValidatorLookupAttempt(
        provider="agr_curation_query", method="get_ontology_terms", query={"terms": ["ZECO:2"]},
        result_count=1, outcome="success",
    ), records=[CanonicalValidatorRecord(candidate=ValidatorCandidate(value="ZECO:2"),
        values={"curie": "ZECO:2"})])[0]
    payload["candidates"].append({"record_ref": other, "disposition": "selected", "explanation": "Other class."})
    payload["slots"]["condition_class_curie"]["record_ref"] = other
    with pytest.raises(ValueError, match="contradicts"):
        workspace.assemble(contract.decision_schema.model_validate(payload))
    payload["candidates"].pop()
    payload["slots"]["condition_class_curie"]["record_ref"] = ref
    payload["status"] = "resolved"
    with pytest.raises(ValueError, match="required component"):
        workspace.assemble(contract.decision_schema.model_validate(payload))


def _simple_workspace(schemas, name, **request_fields):
    from agr_ai_curation_alliance.compact_contracts import simple_decision_contract
    from src.lib.domain_packs.compact_decisions import ValidatorDecisionWorkspace
    from src.schemas.domain_validator import DomainValidationRequest
    request = DomainValidationRequest(
        request_id="test", validator_binding_id="test",
        validator_agent={"package_id": "agr.alliance", "agent_id": "test"},
        target={"domain_pack_id": "test"}, **request_fields,
    )
    contract = simple_decision_contract(request, schemas[name])
    return contract, ValidatorDecisionWorkspace([contract])


def _capture(workspace, schema, records, *, call_id="call", query=None, role=None):
    from agr_ai_curation_alliance.compact_validation import canonical_record
    from src.schemas.domain_validator import ValidatorLookupAttempt
    return workspace.record_lookup("test", call_id=call_id, attempt=ValidatorLookupAttempt(
        provider="lookup", method="GET", query=query or {}, result_count=len(records),
        outcome="success" if records else "not_found",
    ), records=[canonical_record(record, schema, record_role=role) for record in records])


def _assessment(ref, disposition="selected"):
    return {"record_ref": ref, "disposition": disposition, "explanation": "Scientific assessment."}


def test_go_selected_rows_not_found_and_hierarchy_are_assembled(schemas):
    contract, workspace = _simple_workspace(schemas, "GOTermResultEnvelope", selected_inputs={"terms": ["absent"]})
    schema = schemas["GOTermResultEnvelope"]
    refs = _capture(workspace, schema, [
        {"id": "GO:1", "name": "binding", "aspect": "molecular_function", "ancestors": ["GO:2"]},
        {"id": "GO:3", "name": "excluded", "aspect": "molecular_function"},
    ])
    _capture(workspace, schema, [{"id": "GO:2", "name": "parent", "aspect": "molecular_function"}], call_id="details")
    result = workspace.assemble(contract.decision_schema.model_validate({
        "request_id": "test", "status": "unresolved", "explanation": "One term was absent.",
        "scientific": {"query_summary": "Two requests checked.", "not_found_inputs": ["/terms/0"]},
        "candidates": [_assessment(refs[0]), _assessment(refs[1], "excluded")],
    }))
    assert [row.go_id for row in result.results] == ["GO:1"]
    assert result.results[0].ancestors[0].name == "parent"
    assert result.not_found == ["absent"]
    assert len(result.candidates) == 2
    assert len(result.lookup_attempts) == 2


def test_orthology_query_metadata_is_not_an_ortholog_and_empty_set_keeps_query(schemas):
    schema = schemas["OrthologsResult"]
    contract, workspace = _simple_workspace(schemas, "OrthologsResult")
    _capture(workspace, schema, [{"primaryExternalId": "RGD:1", "symbol": "query"}], role="query_gene")
    _capture(workspace, schema, [], call_id="relationships", query={"url": "https://www.alliancegenome.org/api/gene/RGD:1/orthologs"})
    result = workspace.assemble(contract.decision_schema.model_validate({
        "request_id": "test", "status": "resolved", "explanation": "Confirmed empty orthology set.",
    }))
    assert result.orthologs == []
    assert result.query_gene["gene_id"] == "RGD:1"
    assert result.query_gene["symbol"] == "query"
    assert result.high_confidence_count == 0
    contract, workspace = _simple_workspace(schemas, "OrthologsResult")
    _capture(workspace, schema, [], query={"url": "https://www.alliancegenome.org/api/gene/RGD:1/orthologs"})
    result = workspace.assemble(contract.decision_schema.model_validate({
        "request_id": "test", "status": "resolved", "explanation": "Confirmed empty set.",
    }))
    assert result.query_gene == {"gene_id": "RGD:1"}


def test_ontology_array_slot_is_copied_from_selected_records(schemas):
    contract, workspace = _simple_workspace(schemas, "OntologyTermValidationResult",
        expected_result_fields={"terms": "qualifiers"})
    refs = _capture(workspace, schemas["OntologyTermValidationResult"], [
        {"curie": "GO:1", "name": "selected"}, {"curie": "GO:2", "name": "excluded"},
    ])
    result = workspace.assemble(contract.decision_schema.model_validate({
        "request_id": "test", "status": "resolved", "explanation": "One qualifier applies.",
        "candidates": [_assessment(refs[0]), _assessment(refs[1], "excluded")],
    }))
    assert result.resolved_values == {"terms": [{"curie": "GO:1", "name": "selected"}]}


def test_condition_bundle_preserves_quantity_unit_and_supplemental_context(schemas):
    from agr_ai_curation_alliance.compact_conditions import condition_components
    from src.schemas.domain_validator import DomainValidationRequest
    request = DomainValidationRequest(request_id="test", validator_binding_id="test",
        validator_agent={"package_id": "agr.alliance", "agent_id": "experimental_condition_validation"},
        target={"domain_pack_id": "test"}, selected_inputs={"condition_components": {
            "condition_quantity": "high", "condition_unit": "day", "condition_free_text": "description",
        }})
    components = condition_components(request)
    assert set(components) == {"quantity", "unit", "free_text"}
    assert components["quantity"].owner == "controlled_vocabulary_validation"
    assert components["quantity"].required
    assert components["unit"].owner == "controlled_vocabulary_validation"
    assert components["free_text"].source_inputs == {"condition_free_text": "description"}
    assert components["free_text"].owner is None


def test_chebi_search_and_compound_use_accession_not_internal_elasticsearch_id(schemas):
    from agr_ai_curation_alliance.compact_validation import canonical_record
    search = {"_id": "17234", "_score": 45.4,
              "_source": {"chebi_accession": "CHEBI:17234", "name": "glucose"}}
    compound = {"id": 17234, "chebi_accession": "CHEBI:17234", "name": "glucose",
                "chemical_data": {"formula": "C6H12O6"}}
    for raw in (search, compound):
        record = canonical_record(raw, schemas["ChemicalValidationResult"])
        assert record.candidate.value == "CHEBI:17234"
        assert record.values["chebi_id"] == "CHEBI:17234"
        assert record.candidate.details["source_record"] == raw
    assert canonical_record(search, schemas["ChemicalValidationResult"]).values["raw_score"] == 45.4


def test_all_alliance_validator_schemas_load_the_package_runtime(schemas):
    from src.lib.domain_packs.compact_runtime import runtime_for_schema
    names = ["GeneResultEnvelope", "AlleleResultEnvelope", "AgmValidationResult",
        "SubjectEntityValidationResult", "OntologyTermValidationResult", "ControlledVocabularyValidationResult",
        "DataProviderValidationResult", "GOTermResultEnvelope", "GOAnnotationsResult", "ReferenceValidationResult",
        "OrthologsResult", "ChemicalValidationResult", "DiseaseValidationResult",
        "ExperimentalConditionValidationResult", "RGDGOEvidencePolicyValidationResult"]
    for name in names:
        runtime = runtime_for_schema(None, result_schema=schemas[name], input_text="{}")
        assert runtime is not None
        contract = next(iter(runtime.contracts.values()))
        assert contract.result_schema is schemas[name]
        assert contract.request.validator_agent.package_id == "agr.alliance"


@pytest.mark.parametrize("name,raw,slots,expected", [
    ("DiseaseValidationResult", {"curie": "DOID:1", "name": "disease", "ontology_type": "DOTerm"},
     {"label": "name", "ontology_term_type": "ontology_type"}, {"label": "disease", "ontology_term_type": "DOTerm"}),
    ("ChemicalValidationResult", {"id": 17234, "chebi_accession": "CHEBI:17234", "name": "glucose",
        "chemical_data": {"formula": "C6H12O6", "charge": 0, "mass": "180.156", "monoisotopic_mass": "180.06339"},
        "default_structure": {"smiles": "provider-smiles", "standard_inchi": "provider-inchi", "standard_inchi_key": "provider-key"}},
     {"formula": "formula", "charge": "charge", "mass": "mass", "inchi": "inchi", "smiles": "smiles", "inchikey": "inchikey"},
     {"formula": "C6H12O6", "charge": 0, "mass": "180.156", "inchi": "provider-inchi", "smiles": "provider-smiles", "inchikey": "provider-key"}),
])
def test_supported_disease_and_chemical_slots_copy_actual_provider_fields(schemas, name, raw, slots, expected):
    contract, workspace = _simple_workspace(schemas, name, expected_result_fields={key: key for key in slots})
    refs = _capture(workspace, schemas[name], [raw])
    result = workspace.assemble(contract.decision_schema.model_validate({
        "request_id": "test", "status": "resolved", "explanation": "Selected database record.",
        "candidates": [_assessment(refs[0])], "slots": {key: {"kind": "record", "record_ref": refs[0], "field": field}
                                                       for key, field in slots.items()},
    }))
    assert result.resolved_values == expected
    assert result.candidates[0].details["source_record"] == raw


def test_standalone_preserves_structured_inputs_and_new_runtime_evidence(schemas):
    import json
    from src.lib.domain_packs.compact_runtime import runtime_for_schema
    evidence = []
    runtime = runtime_for_schema(None, result_schema=schemas["GeneResultEnvelope"],
        input_text=json.dumps({"selected_inputs": {"symbol": "Abc"}}), evidence=evidence)
    contract = next(iter(runtime.contracts.values()))
    assert contract.request.selected_inputs == {"symbol": "Abc"}
    # The evidence registry is populated by document tools after construction.
    evidence.append({"evidence_record_id": "live-1", "quote": "Abc was observed."})
    result = runtime.assemble({"request_id": contract.request.request_id,
        "status": "unresolved", "explanation": "No database match yet."})
    assert result.status == "unresolved"
    assert contract.request.evidence == evidence
