"""ALL-1285: validators see each lookup candidate once; the program keeps all of it.

Real ``agr_curation_query`` responses restate every returned row as
``candidate_matches``, ``result_projections`` and, for one match, an attempt's
``target_projection``, and repeat long guidance as message, explanation and
each attempt's explanation. The compact validator runtime must give the model
the rows plus their ``validator_record_refs`` only, while the workspace keeps
the complete response and assembly stays byte-for-byte the same.
"""

from copy import deepcopy
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from src.lib.config import schema_discovery
from src.lib.openai_agents.tool_result_bounds import serialized_size
from src.schemas.domain_validator import DomainValidationRequest
from tests.unit.lib.packages import find_repo_root

DERIVED_KEYS = ("candidate_matches", "result_projections")


@pytest.fixture(autouse=True)
def schemas(monkeypatch):
    packages = find_repo_root(Path(__file__)) / "packages"
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(packages))
    monkeypatch.syspath_prepend(str(packages / "alliance" / "python" / "src"))
    schema_discovery.reset_cache()
    yield schema_discovery.discover_agent_schemas(force_reload=True)
    schema_discovery.reset_cache()


def _allele(symbol, index):
    return {
        "curie": f"MGI:{7000 + index}",
        "symbol": f"{symbol}<em{index}Cya>",
        "name": f"{symbol} endonuclease-mediated mutation {index}, Cyagen Biosciences",
        "taxon": "NCBITaxon:10090",
        "synonyms": [f"{symbol} em{index}flox", f"{symbol}-cKO line {index}"],
        "genes": [{"curie": "MGI:103070", "symbol": symbol, "relation": "is_allele_of"}],
        "functional_impacts": ["conditional_ready"],
        "mutation_types": [{"curie": "SO:0000667", "name": "insertion"}],
        "attribution": "Cyagen Biosciences",
        "annotations_capped": [],
        "match_reasons": ["full_name_attribution_text", "structured_functional_impact"],
        "match_type": "starts_with",
    }


@pytest.fixture
def query(monkeypatch):
    """The real agr_curation_query tool over an in-memory allele source."""
    from agr_ai_curation_alliance.tools import agr_curation as tool

    class DB:
        def search_allele_candidates(self, symbol, **kwargs):
            rows = [_allele(symbol, index) for index in range(8)]
            return {"candidates": rows, "coverage": {
                "discovered_count": 8, "returned_count": 8, "discovery_limit": 200,
                "display_limit": 20, "discovery_capped": False, "display_capped": False,
                "database_total": None, "detail_missing_count": 0,
            }}

        def get_gene(self, identifier):
            return SimpleNamespace(primaryExternalId=identifier, taxon="NCBITaxon:10090",
                                   obsolete=False, internal=False)

    db = DB()
    monkeypatch.setattr(tool, "get_curation_resolver", lambda: SimpleNamespace(get_db_client=lambda: db))
    monkeypatch.setattr(tool, "PROVIDER_TO_TAXON", {"MGI": "NCBITaxon:10090"})
    monkeypatch.setattr(tool, "TAXON_TO_PROVIDER", {"NCBITaxon:10090": "MGI"})
    monkeypatch.setattr(tool, "_GROUP_MAPPING_LOAD_ERROR", None)
    monkeypatch.setattr(tool, "is_valid_curie", lambda _: True)
    run = tool._unwrap_function_tool_callable(tool.agr_curation_query, "agr_curation_query")
    return lambda **arguments: run(**arguments).model_dump(mode="json")


def _request(request_id, symbol):
    return DomainValidationRequest(
        request_id=request_id, validator_binding_id="allele_validation",
        validator_agent={"package_id": "agr.alliance", "agent_id": "allele_validation"},
        target={"domain_pack_id": "agr.alliance", "object_id": request_id},
        selected_inputs={"allele_symbol": symbol},
        expected_result_fields={"allele_id": "allele.curie", "allele_symbol": "allele.symbol"},
    )


def _runtime(schemas, requests):
    from agr_ai_curation_alliance.compact_adapter import build_compact_validator_runtime
    return build_compact_validator_runtime(requests, result_schema=schemas["AlleleResultEnvelope"])


def _lookup_tool(payload):
    async def invoke(context, arguments):
        return json.dumps(payload)

    return SimpleNamespace(name="agr_curation_query", on_invoke_tool=invoke, params_json_schema={
        "type": "object", "properties": {"method": {"type": "string"}}, "required": ["method"]})


async def _call(runtime, payload, arguments, call_id="call-1"):
    wrapped = runtime.wrap_lookup_tool(_lookup_tool(payload))
    raw = await wrapped.on_invoke_tool(SimpleNamespace(tool_call_id=call_id), json.dumps(arguments))
    return json.loads(raw), wrapped


def _pointers(value, target, path=""):
    """JSON pointers of every string equal to ``target``."""
    if isinstance(value, dict):
        return [hit for key, child in value.items() for hit in _pointers(child, target, f"{path}/{key}")]
    if isinstance(value, list):
        return [hit for index, child in enumerate(value) for hit in _pointers(child, target, f"{path}/{index}")]
    return [path] if value == target else []


def _base_view(runtime, payload, response):
    """What the unchanged runtime showed: the complete response plus refs."""
    return {**payload, "validator_record_refs": response["validator_record_refs"],
            "validator_lookup_refs": response["validator_lookup_refs"]}


def _decision(request_id, refs, selected_index):
    selected = refs[selected_index]
    return {
        "request_id": request_id, "status": "resolved", "explanation": "Attribution and design match the paper.",
        "candidates": [
            {"record_ref": ref, "disposition": "selected" if index == selected_index else "plausible",
             "explanation": "Compared symbol, attribution and functional impact."}
            for index, ref in enumerate(refs)
        ],
        "slots": {"allele_id": {"kind": "record", "record_ref": selected, "field": "curie"},
                  "allele_symbol": {"kind": "record", "record_ref": selected, "field": "symbol"}},
    }


def _without_refs(result, refs):
    text = json.dumps(result.model_dump(mode="json"), sort_keys=True)
    for index, ref in enumerate(refs):
        text = text.replace(ref, f"<ref {index}>")
    return json.loads(re.sub(r"vr:[0-9a-f]{32}:", "vr:<scope>:", text))


@pytest.mark.asyncio
async def test_single_lookup_shows_each_candidate_once_and_keeps_the_complete_response(schemas, query):
    payload = query(method="search_alleles", allele_symbol="H2-Ab1", data_provider="MGI", limit=20)
    assert payload["candidate_matches"] and payload["result_projections"]
    runtime = _runtime(schemas, [_request("allele-1", "H2-Ab1")])

    response, _ = await _call(runtime, payload, {"method": "search_alleles", "allele_symbol": "H2-Ab1"})

    assert not set(DERIVED_KEYS) & set(response)
    rows = response["data"]
    assert rows == payload["data"]
    refs = response["validator_record_refs"]
    assert [(ref["value"], ref["source_path"]) for ref in refs] == [
        (row["curie"], f"/data/{index}") for index, row in enumerate(rows)
    ]
    for index, row in enumerate(rows):
        # One row plus its record ref, nothing else restating the candidate.
        assert _pointers(response, row["curie"]) == [f"/data/{index}/curie", f"/validator_record_refs/{index}/value"]
        assert _pointers(response, row["symbol"]) == [f"/data/{index}/symbol", f"/validator_record_refs/{index}/label"]
    # Long guidance once; counts, status, coverage and the query stay.
    assert len(_pointers(response, payload["message"])) == 1
    assert "explanation" not in response
    assert response["lookup_status"] == payload["lookup_status"] == "ambiguous"
    assert response["count"] == 8 and response["coverage"] == payload["coverage"]
    attempt = response["lookup_attempts"][0]
    assert attempt["attempted_query"] == payload["lookup_attempts"][0]["attempted_query"]
    assert attempt["lookup_status"] == "ambiguous" and attempt["candidate_count"] == 8
    assert not {"target_projection", "resolved_id", "resolved_label", "explanation", "coverage"} & set(attempt)

    # The workspace keeps the complete response; the record refs are unchanged.
    stored = runtime.workspace.source_payloads("allele-1")
    assert stored == {"call-1": payload}
    base = _base_view(runtime, payload, response)
    before, after = serialized_size(base), serialized_size(response)
    # Measured on this 8-candidate search_alleles lookup: 17,504 -> 10,431 bytes (-40%).
    # What remains is the rows, one guidance message and one record ref each.
    assert after <= before * 0.62, (before, after)


@pytest.mark.asyncio
async def test_single_match_attempt_projection_is_dropped_but_unreturned_records_are_kept(schemas):
    from agr_ai_curation_runtime.agr_lookup import lookup_model_view, lookup_response_payload

    row = {"curie": "MGI:1", "symbol": "Abc<tm1>", "taxon": "NCBITaxon:10090"}
    single = lookup_response_payload(method="get_allele_by_id", data=row, count=1, exact_lookup=True,
                                     projection_metadata={"provider": "db", "provider_data_keys": ("curie", "symbol")})
    assert single["lookup_attempts"][0]["target_projection"]["resolved_id"] == "MGI:1"
    lean = lookup_model_view(single)
    assert _pointers(lean, "MGI:1") == ["/data/curie"]
    assert lean["lookup_attempts"][0]["lookup_status"] == "success"

    # A failed detail fetch names a record that is not among the returned rows:
    # its projection is the only place that record appears, so it stays.
    failure = {"attempted_query": {"method": "search_genes", "gene_id": "MGI:9"}, "lookup_status": "transient",
               "target_projection": {"resolved_id": "MGI:9", "resolved_label": "Xyz"},
               "resolved_id": "MGI:9", "resolved_label": "Xyz", "explanation": "Details for MGI:9 failed."}
    partial = lookup_response_payload(method="search_genes", data=[row], count=1, attempts=[failure])
    assert lookup_model_view(partial)["lookup_attempts"] == [failure]
    assert partial["lookup_attempts"] == [failure]  # the complete response is never edited


@pytest.mark.asyncio
async def test_bulk_lookup_groups_are_lean_and_top_level_attempts_are_not_repeated(schemas, query):
    payload = query(method="search_alleles_bulk", allele_symbols=["H2-Ab1", "Cd4"], data_provider="MGI", limit=20)
    items = payload["data"]["items"]
    assert all(item["candidate_matches"] and item["result_projections"] for item in items)
    assert len(payload["lookup_attempts"]) == 2
    runtime = _runtime(schemas, [_request("a", "H2-Ab1"), _request("b", "Cd4")])

    response, _ = await _call(runtime, payload, {"method": "search_alleles_bulk", "validator_request_ids": ["a", "b"]})

    assert "lookup_attempts" not in response  # both repeat a group's own attempt
    for lean, item in zip(response["data"]["items"], items):
        assert not set(DERIVED_KEYS) & set(lean)
        assert lean["results"] == item["results"] and lean["input"] == item["input"]
        assert lean["coverage"] == item["coverage"] and lean["status"] == item["status"]
        assert lean["lookup_attempts"][0]["attempted_query"] == item["lookup_attempts"][0]["attempted_query"]
        for row in item["results"]:
            assert len(_pointers(lean, row["curie"])) == 1
    assert {ref["request_id"] for ref in response["validator_record_refs"]} == {"a", "b"}
    for ref in response["validator_record_refs"]:
        # Source pointers still resolve in the model view.
        _, _, group, _, index = ref["source_path"].strip("/").split("/")
        assert response["data"]["items"][int(group)]["results"][int(index)]["curie"] == ref["value"]
    assert runtime.workspace.source_payloads("a")["call-1"] == payload
    base = _base_view(runtime, payload, response)
    before, after = serialized_size(base), serialized_size(response)
    # Measured on this 2-input x 8-candidate search_alleles_bulk lookup:
    # 39,662 -> 21,801 bytes (-45%). The complete view needed paging under the
    # default 32 KiB budget; the lean view is served in one response.
    assert after <= before * 0.6, (before, after)
    assert before > 32768 >= after and "result_page" not in response


@pytest.mark.asyncio
async def test_assembled_results_are_identical_to_assembly_from_the_captured_records(schemas, query):
    from agr_ai_curation_alliance.compact_adapter import capture_lookup

    payload = query(method="search_alleles", allele_symbol="H2-Ab1", data_provider="MGI", limit=20)
    arguments = {"method": "search_alleles", "allele_symbol": "H2-Ab1"}
    viewed = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    response, _ = await _call(viewed, payload, arguments)
    view_refs = [ref["record_ref"] for ref in response["validator_record_refs"]]

    # Reference assembly: the same captured records registered directly,
    # independent of any model-facing view.
    direct = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    captured = capture_lookup(direct.contracts["allele-1"], "agr_curation_query", deepcopy(arguments), deepcopy(payload))
    direct_refs = direct.workspace.record_lookup("allele-1", call_id="call-1", attempt=captured.attempt,
                                                 records=captured.records, source_payload=payload)
    assert [(ref["value"], ref["label"], ref["source_path"], _fields_of(response, ref))
            for ref in response["validator_record_refs"]] == [
        (record.candidate.value, record.candidate.label, record.source_path, list(record.values))
        for record in captured.records
    ]

    via_view = viewed.assemble(_decision("allele-1", view_refs, 3))
    reference = direct.assemble(_decision("allele-1", direct_refs, 3))
    assert via_view.resolved_values == {"allele_id": "MGI:7003", "allele_symbol": "H2-Ab1<em3Cya>"}
    assert _without_refs(via_view, view_refs) == _without_refs(reference, direct_refs)
    assert len(via_view.candidates) == 8  # curator-visible candidates keep every record


@pytest.mark.asyncio
async def test_paged_lean_view_keeps_refs_with_rows_and_exact_detail_reads(schemas, query, monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "4096")
    payload = query(method="search_alleles", allele_symbol="H2-Ab1", data_provider="MGI", limit=20)
    runtime = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    first, wrapped = await _call(runtime, payload, {"method": "search_alleles", "allele_symbol": "H2-Ab1"})
    pages = [first]
    while pages[-1]["result_page"]["next_call"] is not None:
        pages.append(json.loads(await wrapped.on_invoke_tool(SimpleNamespace(tool_call_id="page"), json.dumps(
            {"method": "search_alleles", **pages[-1]["result_page"]["next_call"]}))))

    assert len(pages) > 1
    assert all(serialized_size(page) <= 4096 for page in pages)
    assert [row for page in pages for row in page["data"]] == payload["data"]
    for page in pages:
        assert not set(DERIVED_KEYS) & set(page)
        assert [ref["value"] for ref in page["validator_record_refs"]] == [row["curie"] for row in page["data"]]
    detail = json.loads(await wrapped.on_invoke_tool(SimpleNamespace(tool_call_id="detail"), json.dumps(
        {"method": "search_alleles", **first["result_page"]["detail_call"], "detail_path": "data.3"})))
    assert json.loads(detail["detail"]["content"]) == payload["data"][3]


# ALL-1291: every candidate of a lookup usually offers the same field names, so
# the model view lists them once; a ref lists its own only when they differ.
SHARED_FIELDS = "validator_record_available_fields"


def _fields_of(response, ref):
    """The field names the model may copy from one record ref."""
    return ref.get("available_fields", response.get(SHARED_FIELDS))


def _captured(runtime, request_id, payload, arguments):
    from agr_ai_curation_alliance.compact_adapter import capture_lookup
    return capture_lookup(runtime.contracts[request_id], "agr_curation_query", deepcopy(arguments), deepcopy(payload))


def _per_ref_view(response, records):
    """The ALL-1285 view: the same response with the field list on every ref."""
    refs = [{**{key: value for key, value in ref.items() if key != "available_fields"},
             "available_fields": list(record.values)} for ref, record in zip(response["validator_record_refs"], records)]
    return {**{key: value for key, value in response.items() if key != SHARED_FIELDS}, "validator_record_refs": refs}


@pytest.mark.asyncio
async def test_available_fields_are_listed_once_per_lookup_result(schemas, query):
    payload = query(method="search_alleles", allele_symbol="H2-Ab1", data_provider="MGI", limit=20)
    arguments = {"method": "search_alleles", "allele_symbol": "H2-Ab1"}
    runtime = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    response, _ = await _call(runtime, payload, arguments)
    records = _captured(runtime, "allele-1", payload, arguments).records

    refs = response["validator_record_refs"]
    assert len(refs) == len(records) == 8
    assert response[SHARED_FIELDS] == list(records[0].values)
    assert all("available_fields" not in ref for ref in refs)
    for ref, record in zip(refs, records):
        assert set(_fields_of(response, ref)) == set(record.values)
    assert json.dumps(response).count('"fullname_attribution"') == 1
    # The stored ledger and detail reads are untouched.
    assert runtime.workspace.source_payloads("allele-1") == {"call-1": payload}

    before, after = serialized_size(_per_ref_view(response, records)), serialized_size(response)
    # Measured on this 8-candidate search_alleles lookup: 10,431 -> 8,215 bytes
    # (-21%); the 20-name list (297 bytes) travelled with each of 8 refs.
    assert before - after >= 7 * serialized_size(list(records[0].values)), (before, after)
    assert after <= before * 0.82, (before, after)


@pytest.mark.asyncio
async def test_a_ref_lists_its_own_available_fields_only_where_they_differ(schemas, query):
    payload = query(method="search_alleles", allele_symbol="H2-Ab1", data_provider="MGI", limit=20)
    del payload["data"][2]["mutation_types"]
    arguments = {"method": "search_alleles", "allele_symbol": "H2-Ab1"}
    runtime = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    response, _ = await _call(runtime, payload, arguments)
    records = _captured(runtime, "allele-1", payload, arguments).records

    refs = response["validator_record_refs"]
    assert "mutation_types" not in records[2].values
    assert response[SHARED_FIELDS] == list(records[0].values)
    assert [index for index, ref in enumerate(refs) if "available_fields" in ref] == [2]
    assert refs[2]["available_fields"] == list(records[2].values)
    for ref, record in zip(refs, records):
        assert set(_fields_of(response, ref)) == set(record.values)


@pytest.mark.asyncio
async def test_bulk_lookup_lists_available_fields_once_across_requests(schemas, query):
    payload = query(method="search_alleles_bulk", allele_symbols=["H2-Ab1", "Cd4"], data_provider="MGI", limit=20)
    runtime = _runtime(schemas, [_request("a", "H2-Ab1"), _request("b", "Cd4")])
    response, _ = await _call(runtime, payload, {"method": "search_alleles_bulk", "validator_request_ids": ["a", "b"]})

    refs = response["validator_record_refs"]
    assert {ref["request_id"] for ref in refs} == {"a", "b"} and len(refs) == 16
    assert all("available_fields" not in ref for ref in refs)
    assert json.dumps(response).count('"fullname_attribution"') == 1
    records = []
    for request_id in ("a", "b"):
        own_records = _captured(runtime, request_id, payload, {"method": "search_alleles_bulk"}).records
        own = [ref for ref in refs if ref["request_id"] == request_id]
        assert [set(_fields_of(response, ref)) for ref in own] == [set(record.values) for record in own_records]
        records.extend(own_records)
    before, after = serialized_size(_per_ref_view(response, records)), serialized_size(response)
    # Measured on this 2-input x 8-candidate search_alleles_bulk lookup:
    # 21,801 -> 17,033 bytes (-22%).
    assert after <= before * 0.8, (before, after)


@pytest.mark.asyncio
async def test_every_paged_lookup_page_carries_the_shared_field_list(schemas, query, monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "4096")
    payload = query(method="search_alleles", allele_symbol="H2-Ab1", data_provider="MGI", limit=20)
    del payload["data"][5]["mutation_types"]
    arguments = {"method": "search_alleles", "allele_symbol": "H2-Ab1"}
    runtime = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    first, wrapped = await _call(runtime, payload, arguments)
    records = _captured(runtime, "allele-1", payload, arguments).records
    pages = [first]
    while pages[-1]["result_page"]["next_call"] is not None:
        pages.append(json.loads(await wrapped.on_invoke_tool(SimpleNamespace(tool_call_id="page"), json.dumps(
            {"method": "search_alleles", **pages[-1]["result_page"]["next_call"]}))))

    assert len(pages) > 1 and all(serialized_size(page) <= 4096 for page in pages)
    resolved = []
    for page in pages:
        # Each page is self-describing: its refs resolve without an earlier page.
        assert page["validator_record_refs"]
        assert page[SHARED_FIELDS] == list(records[0].values)
        resolved.extend(set(_fields_of(page, ref)) for ref in page["validator_record_refs"])
    assert resolved == [set(record.values) for record in records]
    assert [ref.get("available_fields") is not None for page in pages for ref in page["validator_record_refs"]] == [
        index == 5 for index in range(8)]


def test_finalization_guidance_names_the_shared_field_list(schemas):
    from src.lib.domain_packs.compact_runtime import compact_finalization_instruction
    runtime = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    instruction = compact_finalization_instruction(runtime, tool_name="finalize_validator_result")
    assert SHARED_FIELDS in instruction
    assert "available_fields" in instruction
    # Fields resolve within one response; a list never carries across pages.
    assert "same tool response (page)" in instruction


def test_request_scope_argument_is_named_only_when_lookups_accept_it(schemas):
    """A single-request lookup rejects validator_request_ids, so its guidance never names it."""
    from src.lib.domain_packs.compact_runtime import compact_finalization_instruction
    single = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    many = _runtime(schemas, [_request("allele-1", "H2-Ab1"), _request("allele-2", "Cre")])

    assert "validator_request_ids" not in single.wrap_lookup_tool(_lookup_tool({})).params_json_schema["properties"]
    for batch in (False, True):
        assert "validator_request_ids" not in compact_finalization_instruction(
            single, tool_name="finalize_validator_result", batch=batch)
    assert "validator_request_ids" in many.wrap_lookup_tool(_lookup_tool({})).params_json_schema["properties"]
    assert "validator_request_ids" in compact_finalization_instruction(
        many, tool_name="finalize_validator_batch_results", batch=True)


@pytest.mark.asyncio
async def test_provider_payload_cannot_supply_the_shared_field_list(schemas):
    runtime = _runtime(schemas, [_request("allele-1", "H2-Ab1")])
    forged = {"lookup_status": "success", "data": [], SHARED_FIELDS: ["curie"]}
    with pytest.raises(ValueError, match="reserved"):
        await _call(runtime, forged, {"method": "search_alleles"})
