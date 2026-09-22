"""Alliance's schema-exported compact-runtime factory and lookup capture."""

from copy import deepcopy
from dataclasses import replace
from urllib.parse import urlsplit
from uuid import uuid4
import re
import json

from src.lib.domain_packs.compact_runtime import CapturedValidatorLookup, CompactValidatorRuntime
from src.schemas.domain_validator import DomainValidationRequest, ValidatorCandidate, ValidatorLookupAttempt
from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord

from .compact_validation import canonical_record, source_record_entries


LOOKUP_TOOLS = frozenset({"agr_curation_query", "agr_literature_reference_lookup", "go_api_call",
                          "quickgo_api_call", "chebi_api_call", "alliance_api_call"})


def build_compact_validator_runtime(requests, *, result_schema, profile_request_ids=(), input_text=None, evidence=()):
    """Exported by each Alliance validator schema; backend stays package-neutral."""
    from .compact_conditions import condition_decision_contract
    from .compact_contracts import simple_decision_contract
    from .compact_policy import policy_decision_contract

    standalone = requests is None
    if standalone:
        owners = {
            "GeneResultEnvelope": "gene_validation", "AlleleResultEnvelope": "allele_validation",
            "AgmValidationResult": "agm_validation", "SubjectEntityValidationResult": "subject_entity_validation",
            "OntologyTermValidationResult": "ontology_term_validation", "ControlledVocabularyValidationResult": "controlled_vocabulary_validation",
            "DataProviderValidationResult": "data_provider_validation", "GOTermResultEnvelope": "gene_ontology_lookup",
            "GOAnnotationsResult": "go_annotations_lookup", "ReferenceValidationResult": "reference_validation",
            "OrthologsResult": "orthologs_lookup", "ChemicalValidationResult": "chemical_validation",
            "DiseaseValidationResult": "disease_validation", "ExperimentalConditionValidationResult": "experimental_condition_validation",
            "RGDGOEvidencePolicyValidationResult": "rgd_go_evidence_policy_validation",
        }
        selected_inputs = {"query": input_text}
        if isinstance(input_text, str):
            try:
                structured = json.loads(input_text)
            except (ValueError, TypeError):
                structured = None
            if isinstance(structured, dict):
                selected_inputs = structured.get("selected_inputs", structured)
                if not isinstance(selected_inputs, dict):
                    raise ValueError("Standalone validator selected_inputs must be an object")
        requests = [DomainValidationRequest(
            request_id="standalone-" + uuid4().hex, validator_binding_id="standalone",
            validator_agent={"package_id": "agr.alliance", "agent_id": owners[result_schema.__name__]},
            target={"domain_pack_id": "agr.alliance"}, selected_inputs=selected_inputs, evidence=list(evidence),
        )]
    contracts = []
    for request in requests:
        if result_schema.__name__ == "RGDGOEvidencePolicyValidationResult":
            contract = policy_decision_contract(request, result_schema)
        elif result_schema.__name__ == "ExperimentalConditionValidationResult":
            contract = condition_decision_contract(request, result_schema,
                profile_mapped=request.request_id in profile_request_ids)
        else:
            contract = simple_decision_contract(request, result_schema,
                profile_mapped=request.request_id in profile_request_ids)
        contracts.append(contract)
    runtime = CompactValidatorRuntime(contracts, capture_lookup)
    runtime.lookup_tool_names = LOOKUP_TOOLS
    if standalone and isinstance(evidence, list):
        runtime.standalone_evidence_records = evidence
    if result_schema.__name__ == "RGDGOEvidencePolicyValidationResult":
        import sys
        policy = sys.modules[result_schema.__module__].EVIDENCE_POLICY
        for contract in contracts:
            records = [CanonicalValidatorRecord(
                candidate=ValidatorCandidate(value=code, label=basis,
                    details={"source_kind": "scientific_policy_option", "evidence_basis": basis, "eco_curie": eco}),
                values={},
            ) for basis, (code, eco) in policy.items()]
            seen = set()
            for text in _strings(contract.request.selected_inputs):
                for identifier in re.findall(r"\bRGD:[A-Za-z0-9][A-Za-z0-9_.-]*", text):
                    if identifier not in seen:
                        seen.add(identifier)
                        records.append(CanonicalValidatorRecord(
                            candidate=ValidatorCandidate(value=identifier, details={"source_kind": "supplied_unvalidated_identifier"}),
                            values={},
                        ))
            refs = runtime.workspace.record_sources(contract.request.request_id, source_id="supplied-policy-context", records=records)
            runtime.source_catalog.extend({"request_id": contract.request.request_id, "record_ref": reference,
                                           "value": record.candidate.value, "label": record.candidate.label,
                                           "source_kind": record.candidate.details["source_kind"]}
                                          for reference, record in zip(refs, records))
    return runtime


def capture_lookup(contract, tool_name, arguments, payload):
    status = payload.get("lookup_status") or payload.get("status")
    outcomes = {"ok": "success", "success": "success", "not_found": "not_found",
                "ambiguous": "ambiguous", "conflict": "conflict", "blocked": "blocked",
                "error": "error", "transient": "error", "upstream_error": "error",
                "invalid_input": "blocked", "unsupported_identifier": "blocked"}
    if status not in outcomes:
        raise ValueError(f"Unsupported lookup outcome from {tool_name}: {status}")
    outcome = outcomes[status]
    failed = outcome in {"error", "blocked"}
    entries = [] if failed else source_record_entries(tool_name, payload)
    count = payload.get("returned_count", len(entries))
    if type(count) is not int or count < len(entries):
        raise ValueError("Lookup returned_count must be a nonnegative count of returned records")
    if count == 0 and outcome == "success":
        outcome = "not_found"
    method = arguments.get("method") or payload.get("method") or ("GET" if arguments.get("url") else tool_name)
    coverage = deepcopy(payload.get("coverage"))
    if tool_name == "go_api_call":
        coverage = {key: deepcopy(payload[key]) for key in (
            "source_cursor", "source_limit", "returned_count", "next_source_cursor",
            "source_complete", "source_limit_capped", "source_response_truncated") if key in payload}
    attempt = ValidatorLookupAttempt(provider=tool_name, method=method, query=deepcopy(arguments),
                                     result_count=count, outcome=outcome, coverage=coverage,
                                     message=payload.get("message"))
    # Bulk groups carry their original input. Partition those groups before
    # assigning record references, while preserving the concrete call's count.
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        inputs = set(_strings(contract.request.selected_inputs))
        matching = [index for index, item in enumerate(data["items"]) if item.get("input") in inputs]
        if not matching:
            raise ValueError("Bulk response has no input group belonging to this validator request")
        prefixes = tuple(f"/data/items/{index}/results/" for index in matching)
        entries = [(path, record) for path, record in entries if path.startswith(prefixes)]
    schema = contract.result_schema
    component = schema.__name__ == "ExperimentalConditionValidationResult"
    if component:
        from src.lib.config.schema_discovery import discover_agent_schemas
        owner_schema = ("ControlledVocabularyValidationResult" if method in {"get_vocabulary_term", "search_vocabulary_terms"}
                        else "DataProviderValidationResult" if method in {"get_data_provider", "get_data_providers"}
                        else "OntologyTermValidationResult")
        schema = discover_agent_schemas()[owner_schema]
    role = None
    if schema.__name__ == "OrthologsResult":
        path = urlsplit(str(arguments.get("url", ""))).path.rstrip("/")
        if path.startswith("/api/gene/") and not path.endswith("/orthologs"):
            role = "query_gene"
    records = []
    for path, record in entries:
        canonical = canonical_record(record, schema, request=contract.request, record_role=role)
        canonical = replace(canonical, source_path=path)
        if component:
            canonical = replace(canonical, result_rows={})
        records.append(canonical)
    return CapturedValidatorLookup(attempt=attempt, records=records)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
