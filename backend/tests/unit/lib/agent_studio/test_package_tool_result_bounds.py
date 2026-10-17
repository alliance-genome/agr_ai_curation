"""ALL-1278: subprocess package lookups reach the model bounded.

Covers the backend package-tool adapter (stateless re-query paging with hash
checks and exact detail chunks), the validator compact-runtime capture
(stored-lookup paging with per-page record refs), and a mocked provider loop
showing that the complete result stays application-side.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from agents import Agent, RunConfig, Runner, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from src.lib.agent_studio import catalog_service
from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord, DecisionContract
from src.lib.domain_packs.compact_runtime import CapturedValidatorLookup, CompactValidatorRuntime
from src.lib.openai_agents.tool_result_bounds import canonical_json
from src.schemas.domain_validator import (
    DomainValidationRequest,
    ValidatorCandidate,
    ValidatorLookupAttempt,
)

BUDGET = 8192
NOTE = "Synonym β-catenin/armadillo 表达 Flügel 😀; "


@dataclass
class _FakeFunctionTool:
    name: str
    on_invoke_tool: object
    params_json_schema: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"method": {"type": "string"}, "gene_symbol": {"type": "string"}},
            "required": ["method"],
        }
    )
    strict_json_schema: bool = False


def _gene(index: int, *, wide: int = 6) -> dict:
    return {
        "curie": f"EX:{index:07d}",
        "symbol": f"gene{index}",
        "name": f"gene {index} " + NOTE * wide,
        "synonyms": [f"syn-{index}-{part} {NOTE}" for part in range(wide)],
        "cross_references": [{"curie": f"XR:{index}-{part}", "page_area": "gene"} for part in range(wide)],
    }


def _query_result(count: int, *, huge_index: int | None = None) -> dict:
    data = [_gene(index) for index in range(count)]
    if huge_index is not None:
        data[huge_index] = _gene(huge_index, wide=400)
    return {
        "status": "ok",
        "data": data,
        "count": count,
        "lookup_status": "success",
        "warnings": ["default_limit_applied:100"],
        "lookup_attempts": [{"method": "search_genes", "query": {"gene_symbol": "arm"}}],
    }






@pytest.fixture
def resolved_query_tool(monkeypatch):
    """Resolve lookup_records through the real adapter with a fake runner."""

    state = {"result": _query_result(3), "calls": []}

    def _execute(tool_id, **kwargs):
        state["calls"].append(kwargs["kwargs"])
        return SimpleNamespace(ok=True, result=json.loads(json.dumps(state["result"])), error=None)

    binding = SimpleNamespace(tool_id="lookup_records", required_context=())
    monkeypatch.setattr(catalog_service, "_get_package_tool_binding", lambda _tool_id: binding)

    @function_tool(strict_mode=False)
    def lookup_records(method: str, gene_symbol: str | None = None) -> dict:
        """Query the curation database."""
        raise AssertionError("package tools run in the package runner")

    monkeypatch.setattr(
        catalog_service,
        "_instantiate_package_tool",
        lambda _binding, execution_context=None: lookup_records,
    )
    monkeypatch.setattr(
        catalog_service,
        "_get_package_tool_runner",
        lambda: SimpleNamespace(execute_tool=_execute),
    )
    tool = catalog_service._resolve_package_tool(
        "lookup_records", catalog_service.ToolExecutionContext()
    )
    return tool, state


async def _call(tool, **arguments):
    return await tool.on_invoke_tool(None, json.dumps({"method": "search_genes", **arguments}))


@pytest.mark.asyncio
async def test_compact_query_results_are_returned_unchanged(resolved_query_tool):
    tool, state = resolved_query_tool

    result = await _call(tool)

    assert result == state["result"]
    assert "result_offset" in tool.params_json_schema["properties"]
    assert "detail_path" in tool.params_json_schema["properties"]


@pytest.mark.asyncio
async def test_large_queries_return_complete_data_without_context_quota(resolved_query_tool):
    tool, state = resolved_query_tool
    state["result"] = _query_result(120)
    assert await _call(tool) == state["result"]
    assert state["calls"] == [{"method": "search_genes"}]


@pytest.mark.asyncio
async def test_large_record_and_explicit_detail_are_both_exact(resolved_query_tool):
    tool, state = resolved_query_tool
    state["result"] = _query_result(4, huge_index=1)
    assert await _call(tool) == state["result"]
    detail = await _call(tool, detail_path="data.1", detail_cursor=0)
    assert detail["detail"]["content"] == canonical_json(state["result"]["data"][1])
    assert detail["detail"]["complete"]


@pytest.mark.asyncio
async def test_changed_result_is_reported_stale_not_mixed(resolved_query_tool):
    tool, state = resolved_query_tool
    first = await _call(tool, result_offset=0)
    state["result"] = _query_result(61)
    stale = await _call(tool, result_offset=1, result_sha256=first["result_page"]["result_sha256"])
    assert stale["error_code"] == "stale_result_cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-1, 500])
async def test_invalid_query_offset_is_explicit(resolved_query_tool, offset):
    tool, state = resolved_query_tool
    state["result"] = _query_result(60)

    result = await _call(tool, result_offset=offset)

    assert result["status"] == "invalid_request"
    assert result["error_code"] == "invalid_result_cursor"




def test_reserved_view_argument_collision_is_rejected():
    tool = _FakeFunctionTool(
        name="collides",
        on_invoke_tool=None,
        params_json_schema={"type": "object", "properties": {"result_offset": {"type": "integer"}}},
    )
    with pytest.raises(ValueError, match="reserved result view"):
        catalog_service._with_result_view_arguments(tool)


# --- Validator capture: complete result stays application-side --------------------------


def _contract(identifier: str) -> DecisionContract:
    return DecisionContract(
        DomainValidationRequest(
            request_id=identifier,
            validator_binding_id="identity",
            validator_agent={"package_id": "fixture", "agent_id": "identity"},
            target={"domain_pack_id": "fixture", "object_id": identifier},
            expected_result_fields={"identifier": "identity.id"},
        ),
        profile_mapped=True,
    )


def _capture(contract, name, arguments, payload):
    return CapturedValidatorLookup(
        attempt=ValidatorLookupAttempt(
            provider=name, method=arguments["method"], query=arguments,
            result_count=len(payload["data"]), outcome="success",
        ),
        records=[
            CanonicalValidatorRecord(
                candidate=ValidatorCandidate(value=row["curie"]),
                values={"identifier": row["curie"]},
                source_path=f"/data/{index}",
            )
            for index, row in enumerate(payload["data"])
        ],
    )


@pytest.mark.asyncio
async def test_validator_capture_keeps_complete_lookup_and_pages_refs_with_rows(
    resolved_query_tool,
):
    tool, state = resolved_query_tool
    state["result"] = _query_result(80)
    runtime = CompactValidatorRuntime([_contract("a")], _capture)
    wrapped = runtime.wrap_lookup_tool(tool)
    assert "lookup_ref" in wrapped.params_json_schema["properties"]
    assert "result_sha256" not in wrapped.params_json_schema["properties"]

    first = json.loads(await wrapped.on_invoke_tool(
        SimpleNamespace(tool_call_id="call-1"), json.dumps({"method": "search_genes"})
    ))
    pages = [first]
    assert len(state["calls"]) == 1  # continuation pages the stored lookup, no re-run
    rows = [row for page in pages for row in page["data"]]
    refs = [ref for page in pages for ref in page["validator_record_refs"]]
    assert rows == state["result"]["data"]
    assert [ref["value"] for ref in refs] == [row["curie"] for row in rows]
    for page in pages:
        assert [ref["value"] for ref in page["validator_record_refs"]] == [
            row["curie"] for row in page["data"]
        ]
    assert runtime.workspace.source_payloads("a")["call-1"] == state["result"]
    with pytest.raises(ValueError, match="Unknown lookup_ref"):
        await wrapped.on_invoke_tool(
            None, json.dumps({"method": "search_genes", "lookup_ref": "call-foreign"})
        )
    with pytest.raises(ValueError, match="lookup_ref"):
        await wrapped.on_invoke_tool(
            None, json.dumps({"method": "search_genes", "result_offset": 3})
        )


class _LookupThenAnswer(Model):
    """Calls the lookup once, then records the history it is given."""

    def __init__(self):
        self.inputs = []

    async def get_response(self, system_instructions, input, *args, **kwargs):
        self.inputs.append(input)
        if len(self.inputs) == 1:
            output = [ResponseFunctionToolCall(
                type="function_call", name="lookup_records", call_id="lookup-1",
                arguments=json.dumps({"method": "search_genes"}),
            )]
        else:
            output = [ResponseOutputMessage(
                id="msg-1", type="message", role="assistant", status="completed",
                content=[ResponseOutputText(type="output_text", text="done", annotations=[])],
            )]
        return ModelResponse(output=output, usage=Usage(requests=1), response_id=f"r{len(self.inputs)}")

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("Non-streaming run expected")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_provider_loop_history_holds_only_the_bounded_page(resolved_query_tool):
    tool, state = resolved_query_tool
    state["result"] = _query_result(150)
    model = _LookupThenAnswer()
    agent = Agent(name="curation", model=model, tools=[tool])

    await Runner.run(agent, "find armadillo", run_config=RunConfig(tracing_disabled=True))

    history = model.inputs[1]
    outputs = [item for item in history if isinstance(item, dict) and item.get("type") == "function_call_output"]
    assert len(outputs) == 1
    output_text = outputs[0]["output"]
    import ast
    assert ast.literal_eval(output_text) == state["result"]
    assert state["result"]["data"][-1]["curie"] in output_text


# --- Self-bounded tools: escapes are not silently passed ------------------------






def _bound_tool(name, raw):
    from src.lib.openai_agents import extraction_builder_workspace as builder
    from src.lib.openai_agents.streaming_tools import _build_run_state_bound_tool

    raw.__name__ = f"_{name}_impl"
    existing = SimpleNamespace(name=name, description="", strict_json_schema=False)
    return _build_run_state_bound_tool(
        raw,
        existing,
        builder_workspace=builder.ExtractionBuilderWorkspace(run_id="r", document_id="doc-1"),
        evidence_records=[],
    )


@pytest.mark.asyncio
async def test_oversized_builder_result_becomes_reported_compact_failure():
    def stage(candidate_id: str) -> dict:
        return {"status": "ok", "lookup_status": "success", "data": {"echo": "y" * (3 * BUDGET)}}

    tool = _bound_tool("stage_generic_object", stage)
    result = await tool.on_invoke_tool(
        SimpleNamespace(tool_name="stage_generic_object", tool_call_id="c1"),
        json.dumps({"candidate_id": "cand-1"}),
    )

    assert result == stage("cand-1")


@pytest.mark.asyncio
async def test_an_oversized_resolver_result_is_replaced_like_any_run_state_result():
    """No resolver ledger reads a run-state tool's full output any more, so none is exempt."""

    def resolve(field_path: str) -> dict:
        return {"status": "resolved", "data": {"options": ["z" * 100] * (BUDGET // 50)}}

    tool = _bound_tool("resolve_domain_field_term", resolve)
    result = await tool.on_invoke_tool(
        SimpleNamespace(tool_name="resolve_domain_field_term", tool_call_id="c2"),
        json.dumps({"field_path": "anatomy"}),
    )

    assert result == resolve("anatomy")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "post", "patch"])
@pytest.mark.parametrize("view_arguments", [
    {"result_offset": 3},
    {"result_offset": 0},
    {"result_sha256": "unexpected-hash"},
    {"result_sha256": ""},
    {"detail_path": "data"},
    {"detail_path": ""},
    {"detail_cursor": 0},
    {"result_ref": "unknown"},
    {"result_ref": ""},
])
async def test_write_paging_is_rejected_before_execution(
    resolved_query_tool, method, view_arguments,
):
    tool, state = resolved_query_tool

    result = await _call(tool, method=method, **view_arguments)

    assert state["calls"] == []
    assert result["error_code"] == "invalid_result_cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("view_arguments", [{}, {
    "result_offset": None,
    "result_sha256": None,
    "detail_path": None,
    "detail_cursor": None,
    "result_ref": None,
}])
async def test_ordinary_write_executes_once_with_omitted_or_null_view_arguments(
    resolved_query_tool, method, view_arguments,
):
    tool, state = resolved_query_tool

    result = await _call(tool, method=method, **view_arguments)

    assert result["data"] == state["result"]
    assert result["result_ref"]
    assert state["calls"] == [{"method": method}]


@pytest.mark.asyncio
async def test_get_explicit_selection_does_not_forward_view_arguments(resolved_query_tool):
    tool, state = resolved_query_tool
    state["result"] = _query_result(40)
    page = await _call(tool, method="GET", result_offset=10)
    assert page["data"] == state["result"]["data"][10:]
    assert state["calls"] == [{"method": "GET"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_write_response_is_captured_and_read_without_repetition(resolved_query_tool, monkeypatch, method):
    tool, state = resolved_query_tool
    state["result"] = {"status": "ok", "data": NOTE * 500}
    original = {"method": method}
    first = await tool.on_invoke_tool(None, json.dumps(original))
    ref = first["result_ref"]
    # Read the complete captured response exactly, even after upstream changes.
    expected = canonical_json(state["result"])
    state["result"] = {"status": "changed"}
    cursor = 0
    chunks = []
    while True:
        result = await tool.on_invoke_tool(None, json.dumps({**original, "result_ref": ref, "detail_path": "data", "detail_cursor": cursor}))
        detail = result["detail"]
        chunks.append(detail["content"])
        if detail["complete"]:
            break
        cursor = detail["next_cursor"]
    assert "".join(chunks) == expected
    for bad in ({"result_offset": 1}, {"result_ref": "unknown"}, {"result_ref": ref, "gene_symbol": "changed"}):
        result = await tool.on_invoke_tool(None, json.dumps({**original, **bad}))
        assert result["error_code"] == "invalid_result_cursor"
    for scope_key in ("session_id", "user_id", "trace_id"):
        monkeypatch.setattr(catalog_service, "_current_package_tool_request_context", lambda: {scope_key: "another-scope"})
        denied = await tool.on_invoke_tool(None, json.dumps({**original, "result_ref": ref}))
        assert denied["error_code"] == "invalid_result_cursor"
    other_tool = catalog_service._resolve_package_tool(
        "lookup_records", catalog_service.ToolExecutionContext(user_id="other-user", document_id="other-document")
    )
    denied = await other_tool.on_invoke_tool(None, json.dumps({**original, "result_ref": ref}))
    assert denied["error_code"] == "invalid_result_cursor"
    assert len(state["calls"]) == 1


@pytest.mark.asyncio
async def test_captured_write_returns_all_rows_and_recall_never_reexecutes(resolved_query_tool):
    tool, state = resolved_query_tool
    state["result"] = _query_result(40)
    first = await _call(tool, method="POST")
    assert first["data"] == state["result"]
    recalled = await _call(tool, method="POST", result_ref=first["result_ref"], result_offset=10)
    assert recalled["data"]["data"] == state["result"]["data"][10:]
    assert len(state["calls"]) == 1
