"""Direct builder runs use real package tools/state, without model or SQL calls."""

import asyncio
from contextvars import Context
import json
from types import SimpleNamespace

import pytest
from agents import Agent
from agents.tool_context import ToolContext

from src.lib.agent_studio import catalog_service
from src.lib.openai_agents import runner
from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents import resolver_call_ledger


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setattr(runner, "get_max_turns", lambda: 4)
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(runner, "write_stream_event", lambda *a, **k: None)
    monkeypatch.setattr(runner, "write_extraction_trace_event", lambda **k: k)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **k: k)
    monkeypatch.setattr(resolver_call_ledger, "write_extraction_trace_event", lambda **k: k)
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
@pytest.mark.parametrize("outcome", ["persisted", "persistence_failed", "missing_owner", "missing_session",
                                     "missing_finalization", "validator_failed"])
async def test_direct_chat_builder_requires_owned_durable_result(runtime, monkeypatch, outcome):
    from uuid import UUID
    from agr_ai_curation_alliance.tools.gene_builder_tools import finalize_gene_extraction
    from src.api import chat_common
    from src.schemas.execution_provenance import SourceDocumentProvenance
    from src.lib.openai_agents import extraction_trace_events

    document_id = "2a66f37b-1536-4eab-894e-d421046c838e"
    agent = Agent(name="Direct synthetic gene", model="gpt-5.6-sol", tools=[finalize_gene_extraction])
    agent.agent_key = "gene_extractor"
    agent.curation_metadata = {"launchable": True, "adapter_key": "gene"}
    payload = {"extracted_objects": [], "domain_pack_id": "gene"}
    validated = {**payload, "metadata": {"inline_validator_dispatch_complete": True}}
    calls = []
    trace_records = []
    monkeypatch.setattr(runner, "write_stream_event", extraction_trace_events.write_stream_event)
    monkeypatch.setattr(extraction_trace_events, "write_extraction_trace_event",
                        lambda **kwargs: trace_records.append(kwargs))

    def capture(doc, user):
        assert (doc, user) == (document_id, "synthetic-owner")
        return None if outcome == "missing_owner" else SourceDocumentProvenance(document_id=UUID(doc))

    def persist(**kwargs):
        calls.append(kwargs)
        if outcome == "persistence_failed":
            raise RuntimeError("synthetic persistence failure")
        return SimpleNamespace(extraction_result_id="571bb209-fd16-4516-8142-68f19ec06739",
                               result_ref="extraction-result:571bb209-fd16-4516-8142-68f19ec06739",
                               created_new=True, idempotency_key="synthetic-key", payload_hash="synthetic-hash")

    async def dispatch(value, **kwargs):
        assert json.loads(value) == payload
        if outcome == "validator_failed":
            raise streaming_tools.SpecialistOutputError(agent.name, "builder_finalization")
        return json.dumps(validated)

    class Result:
        final_output = "Completion manifest, not the canonical payload"

        async def stream_events(self):
            workspace = builder.get_active_extraction_builder_workspace()
            workspace.upsert_candidate(candidate_id="materialized", staged_fields=payload)
            if outcome != "missing_finalization":
                workspace.finalize(candidate_ids=["materialized"])
            if False:
                yield None

    monkeypatch.setattr(runner, "capture_source_document", capture)
    monkeypatch.setattr(runner, "persist_inline_validated_extraction_result", persist)
    monkeypatch.setattr(runner, "_dispatch_domain_envelope_validators_for_chat", dispatch)
    monkeypatch.setattr(runner.Runner, "run_streamed", lambda *a, **k: Result())
    events = []

    async def consume():
        async for event in runner._run_agent_with_owned_resources(
            owned_openai_resources=runtime, agent=agent, input_items=[], user_id="synthetic-owner",
            document_id=document_id, document_name="Synthetic paper", user_message="Extract this paper",
            trace_id="synthetic-trace", direct_chat_context=runner._DirectBuilderChatContext(
                session_id=None if outcome == "missing_session" else "synthetic-session", turn_id="synthetic-turn"),
        ):
            if event["type"] in {"INTERNAL_EXTRACTION_RESULT", "STRUCTURED_RESULT", "RUN_FINISHED"}:
                assert len(calls) == 1, "Success must follow durable persistence"
            if event["type"] == "INTERNAL_EXTRACTION_RESULT":
                recorded = [record for record in trace_records
                            if record["event_type"] == "extraction_builder.internal_result"]
                assert len(recorded) == 1, "Internal ref must be trace-recorded before yield"
                assert recorded[0]["trace_id"] == "synthetic-trace"
                assert recorded[0]["input_summary"]["extraction_result_id"] == event["details"]["extraction_result_id"]
                assert recorded[0]["input_summary"]["persistence"] == event["details"]["persistence"]
            events.append(event)

    if outcome == "missing_finalization":
        await consume()
        assert any(event["type"] == "RUN_ERROR" for event in events)
        assert not calls
        assert not any(event["type"] in {"INTERNAL_EXTRACTION_RESULT", "STRUCTURED_RESULT", "RUN_FINISHED"}
                       for event in events)
        return
    if outcome != "persisted":
        with pytest.raises(streaming_tools.SpecialistOutputError):
            await consume()
        assert not any(event["type"] in {"INTERNAL_EXTRACTION_RESULT", "STRUCTURED_RESULT", "RUN_FINISHED"}
                       for event in events)
        assert len(calls) == (1 if outcome == "persistence_failed" else 0)
        return

    await consume()
    assert len(calls) == 1
    stored = calls[0]
    assert stored["payload_json"] == validated
    assert (stored["document_id"], stored["user_id"], stored["origin_session_id"], stored["trace_id"]) == (
        document_id, "synthetic-owner", "synthetic-session", "synthetic-trace")
    assert stored["agent_key"] == stored["tool_name"] == "gene_extractor"
    assert stored["metadata"]["execution_context"]["executed_query"] == "Extract this paper"
    assert stored["metadata"]["chat_turn_id"] == "synthetic-turn"
    internal = [event for event in events if event["type"] == "INTERNAL_EXTRACTION_RESULT"]
    assert len(internal) == 1
    assert events.index(internal[0]) < next(i for i, event in enumerate(events) if event["type"] == "STRUCTURED_RESULT")
    ref = chat_common._build_persisted_extraction_result_ref_from_tool_event(internal[0], tool_agent_map={})
    assert str(ref.extraction_result_id) == internal[0]["details"]["extraction_result_id"]
    assert ref.agent_key == "gene_extractor"
    assert chat_common._build_extraction_candidate_from_tool_event(
        internal[0], tool_agent_map={}, conversation_summary=None) is None


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
@pytest.mark.parametrize("launchable", [True, False])
async def test_actual_builder_tools_reach_benchmark_adapter(runtime, monkeypatch, outcome, launchable):
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
    agent.curation_metadata = {"launchable": launchable, "adapter_key": "gene"}
    captured = {}
    trace_events = []
    monkeypatch.setattr(runner, "write_extraction_trace_event", lambda **k: trace_events.append(k))

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

    async def with_resources(**kwargs):
        async for event in runner._run_agent_with_owned_resources(
            owned_openai_resources=runtime, **kwargs,
        ):
            assert event["type"] != "INTERNAL_EXTRACTION_RESULT"
            yield event

    def reject_chat_io(*args, **kwargs):
        raise AssertionError("Benchmark entered CHAT persistence/ownership capture")

    monkeypatch.setattr(runner, "capture_source_document", reject_chat_io)
    monkeypatch.setattr(runner, "persist_inline_validated_extraction_result", reject_chat_io)
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)
    for name in ("commit_pending_prompts", "_log_used_prompts_to_db", "provider_context_preflight",
                 "start_extraction_trace_run", "clear_extraction_trace_run"):
        monkeypatch.setattr(runner, name, lambda *a, **k: None)
    doc_context = SimpleNamespace(hierarchy={}, abstract="", section_count=lambda: 0, to_agent_kwargs=lambda: {})
    monkeypatch.setattr(adapter.DocumentContext, "fetch", lambda *a, **k: doc_context)

    monkeypatch.setattr(runner.Runner, "run_streamed", lambda active, **k: Result(active))
    monkeypatch.setattr(runner, "_dispatch_domain_envelope_validators_for_chat", dispatch)
    # Keep the real adapter -> run_agent_streamed call, including route=agent.
    monkeypatch.setattr(runner, "_run_agent_with_tracing", with_resources)
    monkeypatch.setattr(adapter, "get_agent_by_id", lambda *a, **k: agent)
    cell = ResolvedBenchmarkCell.model_validate({
        "cell_id": "cell", "case_id": "case", "configuration_id": "config", "repetition": 1,
        "target": {"kind": "agent", "id": "gene_extractor"},
        "input": {"resolver": "fixture", "reference": "paper", "version": "1", "digest": "sha256:" + "0" * 64},
        "routes": {"agent:gene_extractor": {"provider": "openai", "model": "gpt-5.6-sol"}},
    })
    call = adapter.execute_resolved_agent_cell(cell, {
        "messages": [{"role": "user", "content": "Extract"}], "user_id": "synthetic-owner",
        "document_id": "2a66f37b-1536-4eab-894e-d421046c838e", "document_name": "Synthetic paper",
    }, "synthetic-cell")
    if outcome == "accepted":
        result = await call
        assert result.output == captured["canonical"] == captured["dispatch"]
        assert captured["workspace"].finalization.source_candidate_ids == ("gene-candidate-1",)
        assert not any(event["event_type"] == "extraction_builder.top_level_curation_shaped_structured_output"
                       for event in trace_events)
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
    parent_ledger = resolver_call_ledger.ResolverCallLedger(trace_id="parent")
    ledger_token = resolver_call_ledger.set_active_resolver_call_ledger(parent_ledger)

    def reject(*args, **kwargs):
        raise ValueError("synthetic binding failure")

    monkeypatch.setattr(runner, "_bind_run_state_into_tools", reject)
    try:
        agent = SimpleNamespace(name="Synthetic", model="gpt-5.6-sol", tools=[SimpleNamespace(name="unknown")])
        with pytest.raises(ValueError, match="synthetic binding failure"):
            await run_direct(runtime, agent, "rejected")
        assert builder.get_active_extraction_builder_workspace() is parent
        assert evidence_workspace.get_active_evidence_records_snapshot() == parent_evidence
        assert resolver_call_ledger.get_active_resolver_call_ledger() is parent_ledger
    finally:
        builder.reset_active_extraction_builder_workspace(builder_token)
        evidence_workspace.reset_active_evidence_records(evidence_token)
        resolver_call_ledger.reset_active_resolver_call_ledger(ledger_token)


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved", [True, False])
async def test_direct_controlled_selection_records_runtime_tool_output(runtime, monkeypatch, resolved):
    from agr_ai_curation_alliance.tools import agr_curation

    monkeypatch.setattr(agr_curation, "write_extraction_trace_event", lambda **k: k)
    stage = catalog_service._resolve_package_tool(
        "stage_gene_expression_observation", catalog_service.ToolExecutionContext(database_url="unused"),
    )
    agent = Agent(name="Synthetic controlled selection", model="gpt-5.6-sol", tools=[stage])
    output = {"status": "resolved", "data": {
        "domain_pack_id": agr_curation.GENE_EXPRESSION_DOMAIN_PACK_ID,
        "object_type": agr_curation.GENE_EXPRESSION_OBJECT_TYPE,
        "payload_field_instructions": {"set": [{"field_path": "relation.name", "value": "is_expressed_in"}]},
        "helper_selection": {
            "field_path": "relation.name", "selected_value": "is_expressed_in",
            "source_phrase": "expressed", "source_tool": "resolve_domain_field_term",
            "authority": "selector_evidence", "lookup_status": "success",
            "term_source": {"kind": "controlled_vocabulary", "vocabulary": "Expression Relation"},
        },
    }}
    observed = []

    completed = asyncio.Event()
    monkeypatch.setattr(runner, "write_stream_event", lambda event, **k:
                        completed.set() if event["type"] == "TOOL_COMPLETE" else None)

    class Result:
        final_output = "Controlled selection staging complete"

        def __init__(self, active):
            self.agent = active

        async def stream_events(self):
            completed.clear()
            ledger = resolver_call_ledger.get_active_resolver_call_ledger()
            assert ledger is not parent
            observed.append(ledger)
            assert ledger.snapshot()["entry_count"] == 0
            assert ledger.trace_id is not None
            call_id = "resolver-" + ledger.trace_id
            if resolved:
                # Synthetic resolver boundary; real runner correlates completion
                # and records its structured output, never a manually seeded ledger.
                yield SimpleNamespace(type="run_item_stream_event", item=SimpleNamespace(
                    type="tool_call_item", raw_item=SimpleNamespace(
                        name="resolve_domain_field_term", call_id=call_id, arguments="{}",
                    ),
                ))
                yield SimpleNamespace(type="run_item_stream_event", item=SimpleNamespace(
                    type="tool_call_output_item", call_id=call_id, output=json.dumps(output),
                ))
                # Model boundaries normally separate resolve from staging. Wait
                # for the runner to consume the synthetic completion first.
                await completed.wait()
            entry = ledger.find_validated_selection(field_path="relation.name", selected_value="is_expressed_in")
            assert (entry is not None) is resolved
            if entry is not None:
                assert entry.tool_call_id == call_id
            arguments = json.dumps({
                "pending_ref_id": "synthetic-expression", "evidence_record_ids": ["synthetic-evidence"],
                "where_expressed_statement": "Synthetic expression observation",
                "subject": {"source_phrase": "synthetic", "gene_symbol": "synthetic", "primary_external_id": None},
                "reference": {"source_phrase": "synthetic paper", "reference_id": "PMID:39550471"},
                "controlled_fields": [{"field_path": "relation.name", "selected_value": "is_expressed_in"}],
                "condition_relations": None,
            })
            tool = self.agent.tools[0]
            # Actual package stage tool in a fresh context proves the closure
            # receives this same populated ledger across execution boundaries.
            value = await asyncio.create_task(tool.on_invoke_tool(ToolContext(
                context=None, tool_name=tool.name, tool_call_id="stage", tool_arguments=arguments,
            ), arguments), context=Context())
            payload = value.model_dump(mode="json")
            workspace = builder.get_active_extraction_builder_workspace()
            if resolved:
                assert payload["status"] == "ok"
                candidate = next(iter(workspace.candidates.values()))
                assert candidate.resolver_selection_refs == [call_id]
                assert candidate.staged_fields["relation"]["name"] == "is_expressed_in"
            else:
                assert payload["status"] != "ok"
                assert not workspace.candidates

    monkeypatch.setattr(runner.Runner, "run_streamed", lambda active, **k: Result(active))
    parent = resolver_call_ledger.ResolverCallLedger(trace_id="parent")
    token = resolver_call_ledger.set_active_resolver_call_ledger(parent)
    try:
        for trace in ("first", "second"):
            await run_direct(runtime, agent, trace)
            assert resolver_call_ledger.get_active_resolver_call_ledger() is parent
        assert observed[0] is not observed[1]
        assert parent.snapshot()["entry_count"] == 0
    finally:
        resolver_call_ledger.reset_active_resolver_call_ledger(token)
