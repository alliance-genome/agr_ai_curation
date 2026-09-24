"""Package-owned projections keep factual fields and reject unrecognized shapes."""

from pathlib import Path

import pytest

from src.lib.config import schema_discovery
from tests.unit.lib.packages import find_repo_root


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
    ("ChemicalValidationResult", {"chebi_accession": "CHEBI:1", "name": "compound"}, None, "CHEBI:1"),
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


def _condition_context_runtime(schemas):
    from agr_ai_curation_alliance.compact_conditions import condition_decision_contract
    from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord
    from src.lib.domain_packs.compact_runtime import CompactValidatorRuntime
    from src.schemas.domain_validator import DomainValidationRequest, ValidatorCandidate, ValidatorLookupAttempt

    request = DomainValidationRequest(
        request_id="condition-context", validator_binding_id="experimental_condition_validation",
        validator_agent={"package_id": "agr.alliance", "agent_id": "experimental_condition_validation"},
        target={"domain_pack_id": "fixture"},
        selected_inputs={"condition_class_curie": "ZECO:0000111", "condition_relation_type": "induced_by",
                         "evidence_quotes": [{"evidence_record_id": "paper-1", "verified_quote": "Treatment disrupted segmentation."}]},
        expected_result_fields={"condition_class_curie": "condition_class.curie"},
    )
    contract = condition_decision_contract(request, schemas["ExperimentalConditionValidationResult"])
    runtime = CompactValidatorRuntime([contract], adapter=None)
    ref = runtime.workspace.record_lookup(request.request_id, call_id="ontology-call", attempt=ValidatorLookupAttempt(
        provider="agr_curation_query", method="get_ontology_terms", query={"terms": ["ZECO:0000111"]},
        result_count=1, outcome="success"), records=[CanonicalValidatorRecord(
            candidate=ValidatorCandidate(value="ZECO:0000111", label="chemical treatment"),
            values={"curie": "ZECO:0000111", "name": "chemical treatment"},
            resolved_object={"curie": "ZECO:0000111", "name": "chemical treatment"})])[0]
    selection = {"kind": "record", "record_ref": ref, "field": "curie"}
    assessment = _assessment(ref)
    decision = {"request_id": request.request_id, "status": "resolved", "explanation": "Class matches.",
        "candidates": [assessment], "slots": {"condition_class_curie": selection}, "components": [
            {"component_type": "condition_class", "status": "resolved", "candidates": [assessment],
             "slots": {"curie": selection}, "lookup_refs": ["ontology-call"], "explanation": "Class matches."},
            {"component_type": "relation", "status": "not_checked", "explanation": "Coherent relation context."},
            {"component_type": "evidence_quotes", "status": "not_checked", "explanation": "Paper context retained."},
        ]}
    return runtime, decision


def test_condition_context_contract_is_exposed_and_assembles_without_copied_facts(schemas):
    import json
    from src.lib.domain_packs.compact_runtime import compact_finalization_instruction
    runtime, decision = _condition_context_runtime(schemas)
    instruction = compact_finalization_instruction(runtime, tool_name="finalize_validator_result")
    contracts = json.loads(instruction.split("Slot contracts: ", 1)[1].split(" Supplied-context", 1)[0])
    guidance = contracts[0]["domain_contract"]
    assert [item["component_type"] for item in guidance["components"]] == ["condition_class", "relation", "evidence_quotes"]
    assert guidance["components"][0]["allowed_statuses"] == ["resolved", "unresolved"]
    assert all(item["allowed_statuses"] == ["not_checked"] for item in guidance["components"][1:])
    namesake = guidance["component_slots"]["namesake_fields"]
    assert "validator_record_available_fields" in namesake and "available_fields" in namesake.replace(
        "validator_record_available_fields", "")
    assert guidance["component_slots"]["root_slots_are_component_slots"] is False
    result = runtime.assemble(decision)
    assert result.status == "resolved"
    assert result.resolved_values["condition_class_curie"] == "ZECO:0000111"
    assert result.normalized_components[0].resolved_values == {"curie": "ZECO:0000111"}
    assert [row.component_type for row in result.component_validations] == ["condition_class", "relation", "evidence_quotes"]
    assert result.component_validations[-1].selected_inputs["evidence_quotes"][0]["evidence_record_id"] == "paper-1"
    assert len(result.lookup_attempts) == 1


@pytest.mark.parametrize("problem,diagnostic", [("missing", "missing=['relation']"),
    ("duplicate", "duplicates=['condition_class']"), ("unexpected", "unexpected=['unit']")])
def test_condition_component_rejection_identifies_exact_repair(schemas, problem, diagnostic):
    runtime, decision = _condition_context_runtime(schemas)
    if problem == "missing":
        decision["components"].pop(1)
    elif problem == "duplicate":
        decision["components"].append(decision["components"][0])
    else:
        decision["components"].append({"component_type": "unit", "status": "not_checked", "explanation": "Absent."})
    with pytest.raises(ValueError) as error:
        runtime.assemble(decision)
    assert diagnostic in str(error.value)
    assert "expected=['condition_class', 'relation', 'evidence_quotes']" in str(error.value)


def test_condition_component_slot_diagnostic_distinguishes_root_slots(schemas):
    runtime, decision = _condition_context_runtime(schemas)
    decision["components"][0]["slots"] = decision["slots"]
    with pytest.raises(ValueError) as error:
        runtime.assemble(decision)
    assert "condition_class_curie" in str(error.value)
    assert "component slot 'curie'" in str(error.value)
    assert "Root" in str(error.value)


def test_condition_guidance_is_request_specific_in_batch(schemas):
    import json
    from agr_ai_curation_alliance.compact_conditions import condition_decision_contract
    from src.lib.domain_packs.compact_runtime import CompactValidatorRuntime, compact_finalization_instruction
    runtime, _ = _condition_context_runtime(schemas)
    first = runtime.contracts["condition-context"]
    second_request = first.request.model_copy(update={"request_id": "quantity-only", "expected_result_fields": {},
                                                    "selected_inputs": {"condition_quantity": "high"}})
    second = condition_decision_contract(second_request, schemas["ExperimentalConditionValidationResult"])
    batch = CompactValidatorRuntime([first, second], adapter=None)
    instruction = compact_finalization_instruction(batch, tool_name="finalize_validator_result", batch=True)
    contracts = json.loads(instruction.split("Slot contracts: ", 1)[1].split(" Supplied-context", 1)[0])
    by_request = {item["request_id"]: item["domain_contract"]["components"] for item in contracts}
    assert [item["component_type"] for item in by_request["condition-context"]] == ["condition_class", "relation", "evidence_quotes"]
    assert [item["component_type"] for item in by_request["quantity-only"]] == ["quantity"]
    assert by_request["quantity-only"][0]["lookup_methods"] == ["get_vocabulary_term", "search_vocabulary_terms"]


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


@pytest.mark.parametrize("source_checkout", [False, True])
def test_all_alliance_validator_schemas_load_the_package_runtime(schemas, monkeypatch, tmp_path, source_checkout):
    from src.lib.domain_packs.compact_runtime import runtime_for_schema
    if source_checkout:
        monkeypatch.delenv("AGR_RUNTIME_PACKAGES_DIR")
        monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
        schema_discovery.reset_cache()
        schemas = schema_discovery.discover_agent_schemas(force_reload=True)
    names = ["GeneResultEnvelope", "AlleleResultEnvelope", "AgmValidationResult",
        "OntologyTermValidationResult", "ControlledVocabularyValidationResult",
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


@pytest.mark.parametrize("schema, record", [
    # ALL-1283: an internal database id is never a record's identity.
    ("GeneResultEnvelope", {"id": 42, "symbol": "Abc"}),
    ("OntologyTermValidationResult", {"internal_id": 7, "name": "tissue"}),
    ("ChemicalValidationResult", {"id": 17234, "name": "glucose"}),
])
def test_records_without_their_identity_never_fall_through_to_internal_ids(schemas, schema, record):
    from agr_ai_curation_alliance.compact_validation import canonical_record
    with pytest.raises(ValueError, match="no authoritative identity"):
        canonical_record(record, schemas[schema])


_CONDITION_EXPECTED = {
    f"{component}_{key}": f"conditions[0].{component}.{key}"
    for component in ("condition_class", "condition_id", "condition_chemical", "condition_taxon")
    for key in ("curie", "name")
}


def _stored_component(mention, proposed_curie):
    from src.lib.domain_packs.resolvable_values import unresolved_value

    return unresolved_value(mention, identity_keys=("curie", "name"), proposed_curie=proposed_curie)


def _stored_condition_workspace(schemas, *, with_chemical=True):
    from agr_ai_curation_alliance.compact_conditions import condition_decision_contract
    from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord, ValidatorDecisionWorkspace
    from src.schemas.domain_validator import DomainValidationRequest, ValidatorCandidate, ValidatorLookupAttempt

    bundle = {"condition_class": _stored_component("chemical treatment", "ZECO:0000111"),
              "condition_free_text": "3 pM"}
    inputs = {"condition_class_curie": "ZECO:0000111", "condition_class_name": "chemical treatment",
              "condition_free_text": "3 pM"}
    if with_chemical:
        bundle["condition_chemical"] = _stored_component("rapamycin", "CHEBI:9168")
        inputs.update(condition_chemical_curie="CHEBI:9168", condition_chemical_name="rapamycin")
    request = DomainValidationRequest(
        request_id="stored-condition", validator_binding_id="experimental_condition_validation",
        validator_agent={"package_id": "agr.alliance", "agent_id": "experimental_condition_validation"},
        target={"domain_pack_id": "fixture"}, expected_result_fields=_CONDITION_EXPECTED,
        selected_inputs={"condition_components": bundle, **inputs},
    )
    contract = condition_decision_contract(request, schemas["ExperimentalConditionValidationResult"])
    workspace = ValidatorDecisionWorkspace([contract])
    class_ref = workspace.record_lookup("stored-condition", call_id="class-lookup", attempt=ValidatorLookupAttempt(
        provider="agr_curation_query", method="get_ontology_terms", query={"terms": ["ZECO:0000111"]},
        result_count=1, outcome="success",
    ), records=[CanonicalValidatorRecord(candidate=ValidatorCandidate(value="ZECO:0000111", label="chemical treatment"),
                                          values={"curie": "ZECO:0000111", "name": "chemical treatment"},
                                          resolved_object={"curie": "ZECO:0000111", "name": "chemical treatment"})])[0]
    selection = {"kind": "record", "record_ref": class_ref, "field": "curie"}
    components = [
        {"component_type": "condition_class", "status": "resolved", "candidates": [_assessment(class_ref)],
         "slots": {"curie": selection}, "lookup_refs": ["class-lookup"], "explanation": "Class matches."},
        {"component_type": "free_text", "status": "not_checked", "explanation": "Dose context."},
    ]
    if with_chemical:
        workspace.record_lookup("stored-condition", call_id="chemical-lookup", attempt=ValidatorLookupAttempt(
            provider="agr_curation_query", method="get_ontology_terms", query={"terms": ["CHEBI:9168"]},
            result_count=0, outcome="not_found",
        ), records=[])
        components.insert(1, {
            "component_type": "condition_chemical", "status": "unresolved", "lookup_refs": ["chemical-lookup"],
            "explanation": "No ChEBI term for this CURIE.", "curator_message": "Check the chemical.",
        })
    decision = {
        "request_id": "stored-condition", "status": "unresolved" if with_chemical else "resolved",
        "explanation": "Condition decision.", "candidates": [_assessment(class_ref)],
        "slots": {"condition_class_curie": selection}, "components": components,
    }
    return contract, workspace, decision


def test_stored_condition_components_each_carry_their_own_decision(schemas):
    """ALL-1283 Q3: every stored component is decided on its own; absent ones are not required."""

    contract, workspace, decision = _stored_condition_workspace(schemas)

    result = workspace.assemble(contract.decision_schema.model_validate(decision))

    resolutions = {key: value.model_dump() for key, value in result.field_resolutions.items()}
    assert resolutions == {
        "condition_class_curie": {
            "status": "resolved", "lookup_outcome": "matched",
            "resolved_values": {"condition_class_curie": "ZECO:0000111", "condition_class_name": "chemical treatment"},
            "explanation": "Class matches.", "curator_message": None,
        },
        "condition_chemical_curie": {
            "status": "unresolved", "lookup_outcome": "not_found", "resolved_values": {},
            "explanation": "No ChEBI term for this CURIE.", "curator_message": "Check the chemical.",
        },
    }
    # condition_id and condition_taxon are absent: never required, never decided.
    assert result.missing_expected_fields == []
    assert result.unresolved_components == ["condition_chemical"]


def test_stored_condition_resolves_without_its_absent_components(schemas):
    contract, workspace, decision = _stored_condition_workspace(schemas, with_chemical=False)

    result = workspace.assemble(contract.decision_schema.model_validate(decision))

    assert result.status == "resolved"
    assert set(result.field_resolutions) == {"condition_class_curie"}
    assert result.missing_expected_fields == []


def test_stored_component_judged_without_its_own_lookup_is_not_validated(schemas):
    contract, workspace, decision = _stored_condition_workspace(schemas)
    decision["components"][1] = {
        "component_type": "condition_chemical", "status": "unresolved",
        "explanation": "Judged without its own lookup.",
    }

    result = workspace.assemble(contract.decision_schema.model_validate(decision))

    assert result.field_resolutions["condition_chemical_curie"].lookup_outcome == "not_validated"


@pytest.mark.parametrize("record_values", [
    {"curie": "ZECO:0000111", "name": None},
    {"curie": "ZECO:0000111", "name": ""},
    {"curie": "ZECO:0000111", "term_name": "chemical treatment"},
    {"curie": "ZECO:0000111", "label": "chemical treatment"},
])
def test_resolved_component_without_a_record_name_stays_unresolved_alone(schemas, record_values):
    """Review #3: an empty or differently keyed record name never raises for the whole call;
    that component alone is recorded as missing_expected_result_field."""

    from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord
    from src.schemas.domain_validator import ValidatorCandidate, ValidatorLookupAttempt

    contract, workspace, decision = _stored_condition_workspace(schemas, with_chemical=False)
    ref = workspace.record_lookup("stored-condition", call_id="bare-lookup", attempt=ValidatorLookupAttempt(
        provider="agr_curation_query", method="get_ontology_terms", query={"terms": ["ZECO:0000111"]},
        result_count=1, outcome="success",
    ), records=[CanonicalValidatorRecord(candidate=ValidatorCandidate(value="ZECO:0000111"),
                                          values=record_values)])[0]
    selection = {"kind": "record", "record_ref": ref, "field": "curie"}
    decision["candidates"] = [_assessment(ref)]
    decision["slots"] = {"condition_class_curie": selection}
    decision["components"][0].update(candidates=[_assessment(ref)], slots={"curie": selection},
                                     lookup_refs=["bare-lookup"])

    result = workspace.assemble(contract.decision_schema.model_validate(decision))

    decided = result.field_resolutions["condition_class_curie"]
    assert (decided.status, decided.lookup_outcome, decided.resolved_values) == (
        "unresolved", "missing_expected_result_field", {},
    )
    assert result.missing_expected_fields == []


@pytest.mark.parametrize(("outcomes", "expected"), [
    (["not_found"], "not_found"), (["success"], "rejected_candidates"),
    (["not_found", "error"], "transient"), ([], "not_validated"),
])
def test_a_condition_component_outcome_follows_the_shared_classification(outcomes, expected):
    """V3: a component's outcome comes from its own lookups by the shared rule, with nothing filled."""

    from types import SimpleNamespace

    from agr_ai_curation_alliance.compact_conditions import _component_outcome

    attempts = [SimpleNamespace(method="lookup", outcome=outcome) for outcome in outcomes]
    assert _component_outcome(SimpleNamespace(request_id="request-1"), attempts) == expected
