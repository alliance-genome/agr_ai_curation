"""Raw lookup capture, explicit batch scope, and all-or-nothing assembly."""

import json
from types import SimpleNamespace

import pytest

from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord, DecisionContract
from src.lib.domain_packs.compact_runtime import CapturedValidatorLookup, CompactValidatorRuntime
from src.schemas.domain_validator import DomainValidationRequest, ValidatorCandidate, ValidatorLookupAttempt


def contract(identifier):
    return DecisionContract(DomainValidationRequest(
        request_id=identifier, validator_binding_id="identity",
        validator_agent={"package_id": "fixture", "agent_id": "identity"},
        target={"domain_pack_id": "fixture", "object_id": identifier},
        expected_result_fields={"identifier": "identity.id"},
    ), profile_mapped=True)


def adapter(contract, name, arguments, payload):
    return CapturedValidatorLookup(
        attempt=ValidatorLookupAttempt(provider=name, method=arguments["method"], query=arguments,
                                       result_count=payload["returned_count"], outcome="success"),
        records=[CanonicalValidatorRecord(candidate=ValidatorCandidate(value=row["id"]),
                                          values={"identifier": row["id"]})
                 for row in payload["data"] if row["request"] == contract.request.request_id],
    )


def decision(identifier, reference):
    return {
        "request_id": identifier, "status": "resolved", "explanation": "Identity matches.",
        "candidates": [{"record_ref": reference, "disposition": "selected", "explanation": "Selected from lookup."}],
        "slots": {"identifier": {"kind": "record", "record_ref": reference, "field": "identifier"}},
    }


@pytest.mark.asyncio
async def test_batch_lookup_preserves_raw_count_and_projects_records_by_request():
    calls = []

    async def invoke(context, arguments):
        calls.append(json.loads(arguments))
        return json.dumps({"returned_count": 26, "data": [
            {"request": "a", "id": "EX:1"}, {"request": "b", "id": "EX:2"},
        ]})

    tool = SimpleNamespace(name="lookup", params_json_schema={"type": "object", "properties": {
        "method": {"type": "string"}}, "required": ["method"]}, on_invoke_tool=invoke)
    runtime = CompactValidatorRuntime([contract("a"), contract("b")], adapter)
    wrapped = runtime.wrap_lookup_tool(tool)
    assert "validator_request_ids" not in tool.params_json_schema["properties"]
    response = json.loads(await wrapped.on_invoke_tool(SimpleNamespace(tool_call_id="call-1"), json.dumps({
        "method": "search", "validator_request_ids": ["a", "b"],
    })))
    assert calls == [{"method": "search"}]
    assert response["returned_count"] == 26
    refs = {row["request_id"]: row["record_ref"] for row in response["validator_record_refs"]}
    results = runtime.assemble_batch([decision("b", refs["b"]), decision("a", refs["a"])])
    assert [result.request_id for result in results] == ["a", "b"]
    assert [result.resolved_values["identifier"] for result in results] == ["EX:1", "EX:2"]
    assert all(result.lookup_attempts[0].result_count == 26 for result in results)
    assert all(result.lookup_attempts[0].query == {"method": "search"} for result in results)
    with pytest.raises(ValueError, match="foreign"):
        runtime.assemble(decision("b", refs["a"]))
    with pytest.raises(ValueError, match="exactly one"):
        runtime.assemble_batch([decision("a", refs["a"]), decision("a", refs["a"])])


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [None, [], ["a", "a"], ["unknown"], [{"id": "a"}]])
async def test_invalid_batch_scope_rejected_before_provider_call(ids):
    async def forbidden(context, arguments):
        pytest.fail("Provider must not run for an invalid request scope")

    runtime = CompactValidatorRuntime([contract("a"), contract("b")], adapter)
    tool = SimpleNamespace(name="lookup", params_json_schema={"type": "object"}, on_invoke_tool=forbidden)
    with pytest.raises(ValueError, match="unique known"):
        await runtime.wrap_lookup_tool(tool).on_invoke_tool(None, json.dumps({"validator_request_ids": ids}))


@pytest.mark.asyncio
async def test_single_request_scope_owned_by_runtime_and_capture_is_immutable():
    source = {"returned_count": 1, "data": [{"request": "a", "id": "EX:1"}]}

    async def invoke(context, arguments):
        return source

    runtime = CompactValidatorRuntime([contract("a")], adapter)
    tool = SimpleNamespace(name="lookup", params_json_schema={"type": "object"}, on_invoke_tool=invoke)
    wrapped = runtime.wrap_lookup_tool(tool)
    assert "validator_request_ids" not in wrapped.params_json_schema.get("properties", {})
    response = json.loads(await wrapped.on_invoke_tool(None, '{"method":"search"}'))
    source["data"][0]["id"] = "EX:forged"
    result = runtime.assemble(decision("a", response["validator_record_refs"][0]["record_ref"]))
    assert result.resolved_values == {"identifier": "EX:1"}
    with pytest.raises(ValueError, match="runtime"):
        await wrapped.on_invoke_tool(None, '{"method":"search","validator_request_ids":["a"]}')
