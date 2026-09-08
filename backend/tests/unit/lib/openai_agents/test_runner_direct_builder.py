"""Direct builder runs use real package tools/state, without model or SQL calls."""

import json
from types import SimpleNamespace

import pytest
from agents import Agent
from agents.tool_context import ToolContext

from src.lib.agent_studio import catalog_service
from src.lib.openai_agents import runner
from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents import streaming_tools


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(runner, "get_max_turns", lambda: 4)
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(runner, "write_stream_event", lambda *a, **k: None)
    monkeypatch.setattr(runner, "write_extraction_trace_event", lambda **k: k)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **k: k)
    monkeypatch.setattr(runner, "_build_agents_run_config", lambda **k: SimpleNamespace())
    return SimpleNamespace(client=None, provider=None)


async def run_direct(resources, agent, trace):
    return [event async for event in runner._run_agent_with_owned_resources(
        owned_openai_resources=resources, agent=agent,
        input_items=[{"role": "user", "content": "Extract the synthetic paper"}],
        user_id="synthetic-owner", document_id="synthetic-document",
        document_name="Synthetic paper", user_message="Extract", trace_id=trace,
    )]


@pytest.mark.asyncio
async def test_direct_builder_binds_actual_package_stage_tool(runtime, monkeypatch):
    context = catalog_service.ToolExecutionContext(database_url="unused")
    stage = catalog_service._resolve_package_tool("stage_gene_mention_evidence", context)
    # The old direct path delegates this stateful tool to an isolated process.
    # Reject that boundary rather than launching any subprocess/provider/database.
    def no_subprocess():
        raise AssertionError("stateful builder tool escaped the active run")
    monkeypatch.setattr(catalog_service, "_get_package_tool_runner", no_subprocess)
    agent = Agent(name="Synthetic direct gene", model="gpt-5.6-sol", tools=[stage])
    observed = []

    class Result:
        final_output = "Finished"

        def __init__(self, active_agent):
            self.agent = active_agent

        async def stream_events(self):
            tool = self.agent.tools[0]
            value = await tool.on_invoke_tool(ToolContext(
                context=None, tool_name=tool.name, tool_call_id="synthetic-call", tool_arguments="{}",
            ), json.dumps({
                "pending_ref_id": "pending:gene:1", "mention": "synthetic gene",
                "evidence_record_ids": ["evidence-1"],
                "identity_resolution_notes": ["Synthetic unresolved identity"], "confidence": "high",
            }))
            assert "An error occurred" not in str(value)
            workspace = builder.get_active_extraction_builder_workspace()
            assert len(workspace.candidates) == 1
            observed.append(workspace)
            if False:
                yield None

    monkeypatch.setattr(runner.Runner, "run_streamed", lambda active, **k: Result(active))
    for trace in ("trace-first", "trace-second"):
        await run_direct(runtime, agent, trace)
    assert observed[0] is not observed[1]
    assert [item.run_id for item in observed] == ["trace-first", "trace-second"]
    assert agent.tools == [stage]


@pytest.mark.asyncio
@pytest.mark.parametrize("finalized", [True, False])
async def test_direct_builder_requires_and_harvests_canonical_finalization(runtime, monkeypatch, finalized):
    # Recognition stays registry-derived: this is the actual declared finalize tool.
    from agr_ai_curation_alliance.tools.gene_builder_tools import finalize_gene_extraction
    agent = Agent(name="Synthetic builder", model="gpt-5.6-sol", tools=[finalize_gene_extraction])
    assert streaming_tools.is_builder_materializer_agent(agent)
    expected = {"records": [{"id": "synthetic", "value": -0.0}]}
    agent.agent_key = "gene_extractor"
    agent.authenticated_groups = ("synthetic-curator",)
    agent.curation_metadata = {"launchable": True, "adapter_key": "gene"}
    workspaces = []
    dispatches = []

    async def dispatch(payload, **kwargs):
        dispatches.append(kwargs)
        assert json.loads(payload) == expected
        return payload

    monkeypatch.setattr(runner, "_dispatch_domain_envelope_validators_for_chat", dispatch)

    class Result:
        final_output = "Ordinary completion text, never the extraction payload"

        async def stream_events(self):
            workspace = builder.get_active_extraction_builder_workspace()
            workspaces.append(workspace)
            if finalized:
                workspace.upsert_candidate(candidate_id="materialized", staged_fields=expected)
                workspace.finalize(candidate_ids=["materialized"])
            if False:
                yield None

    monkeypatch.setattr(runner.Runner, "run_streamed", lambda *a, **k: Result())
    events = await run_direct(runtime, agent, "trace-finalization")
    if finalized:
        structured = [event for event in events if event["type"] == "STRUCTURED_RESULT"]
        assert len(structured) == 1
        assert structured[0]["data"]["result"] == expected
        assert workspaces[0].finalized_candidate_ids == ("materialized",)
        assert list(workspaces[0].candidates) == ["materialized"]
        assert events[-1]["type"] == "RUN_FINISHED"
        assert len(dispatches) == 1
        assert dispatches[0]["source_agent_key"] == "gene_extractor"
        assert dispatches[0]["adapter_key"] == "gene"
        assert dispatches[0]["tool_name"] is None
        assert dispatches[0]["is_builder_envelope"] is True
        assert dispatches[0]["runtime_context"].authenticated_groups == ("synthetic-curator",)
    else:
        assert any(event["type"] == "RUN_ERROR" for event in events)
        assert not any(event["type"] in {"STRUCTURED_RESULT", "RUN_FINISHED"} for event in events)
        assert not dispatches


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["accepted", "missing", "validator_failed"])
async def test_actual_builder_tools_reach_benchmark_adapter(runtime, monkeypatch, outcome):
    from src.lib.benchmarks import runtime as adapter
    from src.lib.benchmarks.models import ResolvedBenchmarkCell
    from src.lib.openai_agents.tools import evidence_workspace
    from src.lib.openai_agents.benchmark_routing import active_benchmark_route

    context = catalog_service.ToolExecutionContext(database_url="unused")
    names = ["stage_gene_mention_evidence", "finalize_gene_extraction"]
    agent = Agent(name="Synthetic gene extractor", model="gpt-5.6-sol", tools=[
        catalog_service._resolve_package_tool(name, context) for name in names
    ])
    agent.agent_key = "gene_extractor"
    agent.authenticated_groups = ("synthetic-curator",)
    captured = {}

    class Result:
        final_output = "Extraction finished."

        def __init__(self, active):
            self.agent = active

        async def stream_events(self):
            evidence_workspace._workspace_records().append({
                "evidence_record_id": "evidence-1", "entity": "synthetic gene",
                "verified_quote": "The synthetic gene was measured.",
                "chunk_id": "synthetic-chunk", "page": 1, "section": "Results",
            })
            args = [
                {"pending_ref_id": "pending:gene:1", "mention": "synthetic gene",
                 "evidence_record_ids": ["evidence-1"], "confidence": "high",
                 "identity_resolution_notes": ["Synthetic unresolved identity"]},
                {"candidate_ids": ["gene-candidate-1"]},
            ]
            for tool, arguments in zip(self.agent.tools, args):
                if outcome == "missing" and tool.name == "finalize_gene_extraction":
                    break
                result = await tool.on_invoke_tool(ToolContext(
                    context=None, tool_name=tool.name, tool_call_id="synthetic-call",
                    tool_arguments=json.dumps(arguments),
                ), json.dumps(arguments))
                assert "An error occurred" not in str(result)
            workspace = builder.get_active_extraction_builder_workspace()
            captured["workspace"] = workspace
            if outcome != "missing":
                assert workspace.finalization is not None
                captured["canonical"] = workspace.finalization.payload
            if False:
                yield None

    async def dispatch(payload, **kwargs):
        assert kwargs["source_agent_key"] == "gene_extractor"
        assert kwargs["tool_name"] is None
        # The adapter's frozen plan survives into post-stream validation.
        route = active_benchmark_route("agent:gene_extractor")
        assert route is not None and route.model == "gpt-5.6-sol"
        captured["dispatch"] = json.loads(payload)
        if outcome == "validator_failed":
            raise streaming_tools.SpecialistOutputError("Synthetic gene extractor", "builder_finalization")
        return payload

    async def actual_runner(**kwargs):
        async for event in runner._run_agent_with_owned_resources(
            owned_openai_resources=runtime, agent=kwargs["agent"],
            input_items=kwargs["context_messages"], user_id=kwargs["user_id"],
            document_id="synthetic-document", document_name="Synthetic paper",
            user_message="Extract", trace_id=kwargs["session_id"],
        ):
            yield event

    monkeypatch.setattr(runner.Runner, "run_streamed", lambda active, **k: Result(active))
    monkeypatch.setattr(runner, "_dispatch_domain_envelope_validators_for_chat", dispatch)
    monkeypatch.setattr(adapter, "run_agent_streamed", actual_runner)
    monkeypatch.setattr(adapter, "get_agent_by_id", lambda *a, **k: agent)
    cell = ResolvedBenchmarkCell.model_validate({
        "cell_id": "cell", "case_id": "case", "configuration_id": "config", "repetition": 1,
        "target": {"kind": "agent", "id": "gene_extractor"},
        "input": {"resolver": "fixture", "reference": "paper", "version": "1", "digest": "sha256:" + "0" * 64},
        "routes": {"agent:gene_extractor": {"provider": "openai", "model": "gpt-5.6-sol"}},
    })
    call = adapter.execute_resolved_agent_cell(cell, {
        "messages": [{"role": "user", "content": "Extract"}], "user_id": "synthetic-owner",
    }, "synthetic-cell")
    if outcome == "accepted":
        result = await call
        assert result.output == captured["canonical"] == captured["dispatch"]
        assert captured["workspace"].finalization.source_candidate_ids == ("gene-candidate-1",)
    else:
        with pytest.raises((RuntimeError, streaming_tools.SpecialistOutputError)):
            await call


@pytest.mark.asyncio
async def test_binding_failure_restores_parent_state(runtime, monkeypatch):
    from src.lib.openai_agents.tools import evidence_workspace
    parent = builder.ExtractionBuilderWorkspace(run_id="parent")
    parent_evidence = [{"evidence_record_id": "parent-evidence"}]
    builder_token = builder.set_active_extraction_builder_workspace(parent)
    evidence_token = evidence_workspace.set_active_evidence_records(parent_evidence)

    def reject(*args, **kwargs):
        raise ValueError("synthetic binding failure")

    monkeypatch.setattr(runner, "_bind_run_state_into_tools", reject)
    try:
        agent = SimpleNamespace(name="Synthetic", model="gpt-5.6-sol", tools=[SimpleNamespace(name="unknown")])
        with pytest.raises(ValueError, match="synthetic binding failure"):
            await run_direct(runtime, agent, "rejected")
        assert builder.get_active_extraction_builder_workspace() is parent
        assert evidence_workspace.get_active_evidence_records_snapshot() == parent_evidence
    finally:
        builder.reset_active_extraction_builder_workspace(builder_token)
        evidence_workspace.reset_active_evidence_records(evidence_token)
