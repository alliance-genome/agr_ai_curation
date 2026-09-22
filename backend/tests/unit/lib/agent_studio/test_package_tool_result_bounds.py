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

import src.lib.openai_agents.tool_result_bounds as tool_result_bounds
from src.lib.agent_studio import catalog_service
from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord, DecisionContract
from src.lib.domain_packs.compact_runtime import CapturedValidatorLookup, CompactValidatorRuntime
from src.lib.openai_agents.tool_result_bounds import canonical_json, serialized_size
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


@pytest.fixture(autouse=True)
def small_budget(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", str(BUDGET))


@pytest.fixture
def reported(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_result_bounds,
        "report_payload_contract_violation",
        lambda violation, **kwargs: calls.append((violation, kwargs)) or True,
    )
    return calls


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
async def test_large_query_pages_completely_by_size_with_hash_checked_continuation(
    resolved_query_tool, reported
):
    tool, state = resolved_query_tool
    state["result"] = _query_result(120)
    assert serialized_size(state["result"]) > 10 * BUDGET

    pages = [await _call(tool)]
    while pages[-1]["result_page"]["next_call"] is not None:
        pages.append(await _call(tool, **pages[-1]["result_page"]["next_call"]))

    rows = [row for page in pages for row in page["data"]]
    assert rows == state["result"]["data"]
    assert all(serialized_size(page) <= BUDGET for page in pages)
    assert all(page["lookup_status"] == "success" and page["count"] == 120 for page in pages)
    assert pages[0]["warnings"] == ["default_limit_applied:100"]
    assert "warnings" in pages[1]["result_page"]["omitted_fields"]
    # Page arguments never reach the package; the lookup inputs are unchanged.
    assert all(call == {"method": "search_genes"} for call in state["calls"])
    assert reported == []


@pytest.mark.asyncio
async def test_oversized_single_record_is_read_exactly_through_detail_chunks(resolved_query_tool):
    tool, state = resolved_query_tool
    state["result"] = _query_result(4, huge_index=1)

    first = await _call(tool)
    pages = [first]
    while pages[-1]["result_page"]["next_call"] is not None:
        pages.append(await _call(tool, **pages[-1]["result_page"]["next_call"]))
    rows = [row for page in pages for row in page["data"]]
    descriptor = rows[1]
    assert descriptor["withheld"] is True and descriptor["detail_path"] == "data.1"

    chunks, cursor = [], 0
    while cursor is not None:
        chunk = await _call(
            tool,
            detail_path="data.1",
            detail_cursor=cursor,
            result_sha256=first["result_page"]["result_sha256"],
        )
        assert serialized_size(chunk) <= BUDGET
        chunks.append(chunk["detail"]["content"])
        cursor = chunk["detail"]["next_cursor"]
    assert json.loads("".join(chunks)) == state["result"]["data"][1]
    assert "".join(chunks) == canonical_json(state["result"]["data"][1])


@pytest.mark.asyncio
async def test_changed_result_is_reported_stale_not_mixed(resolved_query_tool):
    tool, state = resolved_query_tool
    state["result"] = _query_result(60)
    first = await _call(tool)
    state["result"] = _query_result(61)

    stale = await _call(tool, **first["result_page"]["next_call"])

    assert stale["error_code"] == "stale_result_cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-1, 500])
async def test_invalid_query_offset_is_explicit(resolved_query_tool, offset):
    tool, state = resolved_query_tool
    state["result"] = _query_result(60)

    result = await _call(tool, result_offset=offset)

    assert result["status"] == "invalid_request"
    assert result["error_code"] == "invalid_result_cursor"


@pytest.mark.asyncio
async def test_unmeetable_budget_reports_one_escape(resolved_query_tool, reported, monkeypatch):
    tool, state = resolved_query_tool
    state["result"] = _query_result(60)
    monkeypatch.setattr(catalog_service, "tool_result_budget", lambda: 100)

    result = await _call(tool)

    assert result["error_code"] == "tool_result_budget_unmet"
    assert len(reported) == 1
    assert reported[0][0].category == "tool_result_budget_escape"
    assert reported[0][1]["tool_name"] == "lookup_records"


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
    while pages[-1]["result_page"]["next_call"] is not None:
        pages.append(json.loads(await wrapped.on_invoke_tool(
            SimpleNamespace(tool_call_id="call-2"),
            json.dumps({"method": "search_genes", **pages[-1]["result_page"]["next_call"]}),
        )))

    assert len(state["calls"]) == 1  # continuation pages the stored lookup, no re-run
    assert all(serialized_size(page) <= BUDGET for page in pages)
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
    assert len(output_text.encode("utf-8")) <= BUDGET
    assert "result_page" in output_text
    # Rows past the first page stay application-side until the model asks for them.
    assert state["result"]["data"][-1]["curie"] not in json.dumps(history, ensure_ascii=False)


# --- Self-bounded tools: escapes are reported, not silently passed ------------------------


def test_inline_tool_escape_is_reported_but_sibling_contract_tool_is_not(reported):
    oversized = {"status": "ok", "record": {"note": "x" * (2 * BUDGET)}}

    catalog_service._report_inline_package_result("record_evidence", oversized)
    catalog_service._report_inline_package_result("get_agent_contract", oversized)
    catalog_service._report_inline_package_result("record_evidence", {"status": "ok"})

    assert len(reported) == 1
    violation, kwargs = reported[0]
    assert kwargs["tool_name"] == "record_evidence"
    assert kwargs["correlation"]["enforced"] is False


def test_self_reported_failure_is_not_captured_twice(reported):
    failure = tool_result_bounds.budget_failure(tool_name="list_recorded_evidence", measured=10, limit=5)
    tool_result_bounds.report_budget_failure_result(
        failure, tool_name="list_recorded_evidence", component="evidence_workspace"
    )
    catalog_service._report_inline_package_result("list_recorded_evidence", failure)

    assert len(reported) == 1


def _bound_tool(name, raw):
    from src.lib.openai_agents import extraction_builder_workspace as builder
    from src.lib.openai_agents import resolver_call_ledger
    from src.lib.openai_agents.streaming_tools import _build_run_state_bound_tool

    raw.__name__ = f"_{name}_impl"
    existing = SimpleNamespace(name=name, description="", strict_json_schema=False)
    return _build_run_state_bound_tool(
        raw,
        existing,
        builder_workspace=builder.ExtractionBuilderWorkspace(run_id="r", document_id="doc-1"),
        resolver_ledger=resolver_call_ledger.ResolverCallLedger(trace_id="r"),
        evidence_records=[],
    )


@pytest.mark.asyncio
async def test_oversized_builder_result_becomes_reported_compact_failure(reported):
    def stage(candidate_id: str) -> dict:
        return {"status": "ok", "lookup_status": "success", "data": {"echo": "y" * (3 * BUDGET)}}

    tool = _bound_tool("stage_generic_object", stage)
    result = await tool.on_invoke_tool(
        SimpleNamespace(tool_name="stage_generic_object", tool_call_id="c1"),
        json.dumps({"candidate_id": "cand-1"}),
    )

    assert "tool_result_budget_unmet" in str(result)
    assert "operation_status" in str(result)
    assert len(str(result).encode("utf-8")) <= BUDGET
    assert len(reported) == 1 and reported[0][1]["correlation"]["enforced"] is True


@pytest.mark.asyncio
async def test_resolver_ledger_tool_is_observed_not_replaced(reported):
    def resolve(field_path: str) -> dict:
        return {"status": "resolved", "data": {"options": ["z" * 100] * (BUDGET // 50)}}

    tool = _bound_tool("resolve_domain_field_term", resolve)
    result = await tool.on_invoke_tool(
        SimpleNamespace(tool_name="resolve_domain_field_term", tool_call_id="c2"),
        json.dumps({"field_path": "anatomy"}),
    )

    assert "tool_result_budget_unmet" not in str(result)
    assert len(reported) == 1 and reported[0][1]["correlation"]["enforced"] is False


def test_write_requests_are_never_paged_by_repetition(reported):
    large = {"status": "ok", "data": [{"row": "w" * 500} for _ in range(100)]}
    small = {"status": "ok", "data": [{"row": "w"}]}

    oversized = catalog_service._bounded_package_result(
        "rest_lookup", large, {}, repeatable=False
    )
    fits = catalog_service._bounded_package_result(
        "rest_lookup", small, {}, repeatable=False
    )
    continued = catalog_service._bounded_package_result(
        "rest_lookup", large, {"result_offset": 3}, repeatable=False
    )

    assert oversized["error_code"] == "tool_result_budget_unmet"
    assert "result_page" not in oversized
    assert fits == small
    assert continued["error_code"] == "invalid_result_cursor"
    assert len(reported) == 1
    assert catalog_service._is_non_idempotent_http_call({"method": "post"})
    assert not catalog_service._is_non_idempotent_http_call({"method": "GET"})
    assert not catalog_service._is_non_idempotent_http_call({"method": "search_genes"})
