"""Coverage tests for streaming_tools retry and text-fallback paths."""

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import BaseModel

from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents.tools import evidence_workspace
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult


class _Envelope(BaseModel):
    value: str


class _DomainEnvelope(DomainEnvelopeExtractionResult):
    pass


class _FakeRunResult:
    def __init__(self, events=None, final_output=None, new_items=None):
        self._events = events or []
        self.final_output = final_output
        self.new_items = new_items or []

    async def stream_events(self):
        for event in self._events:
            yield event

    def to_input_list(self):
        return [{"role": "user", "content": "prior query"}]


class _FailingStreamRunResult(_FakeRunResult):
    def __init__(self, *, error, **kwargs):
        super().__init__(**kwargs)
        self._error = error

    async def stream_events(self):
        for event in self._events:
            yield event
        raise self._error


@pytest.fixture(autouse=True)
def _reset_streaming_state():
    evidence_workspace.set_active_evidence_records(None)
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)
    yield
    evidence_workspace.set_active_evidence_records(None)
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)


@pytest.mark.asyncio
async def test_run_specialist_uses_streaming_text_fallback_when_final_output_missing(monkeypatch):
    class ResponseTextDeltaEvent:
        def __init__(self, delta):
            self.delta = delta

    raw_event = SimpleNamespace(
        type="raw_response_event",
        data=ResponseTextDeltaEvent("fallback text"),
    )

    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(events=[raw_event], final_output=None, new_items=[]),
    )

    agent = SimpleNamespace(
        name="Plain Text Specialist",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="summarize findings",
        specialist_name="Plain Text Specialist",
        max_turns=3,
        tool_name=None,
    )

    assert result == "fallback text"
    assert any(
        e.get("type") == "SPECIALIST_TEXT_FALLBACK_SUCCESS"
        and e.get("details", {}).get("extraction_method") == "streaming_text_fallback"
        for e in captured_events
    )
    summary = next(
        e.get("details") or {}
        for e in captured_events
        if e.get("type") == "SPECIALIST_SUMMARY"
    )
    assert summary["totalDurationMs"] >= summary["streamDurationMs"]
    assert "stream_consume_ms" in summary["phaseTimingsMs"]
    assert "post_stream_output_ms" in summary["phaseTimingsMs"]
    assert "domain_validator_dispatch_ms" in summary["phaseTimingsMs"]


@pytest.mark.asyncio
async def test_run_specialist_rejects_domain_envelope_text_after_stream_validation_error(monkeypatch):
    class ResponseTextDoneEvent:
        def __init__(self, text):
            self.text = text

    generated_payload = {
        "summary": "Recovered extraction",
        "curatable_objects": [
            {
                "object_type": "gene_expression_annotation",
                "payload": {"gene_symbol": "wg", "assay": "in situ"},
                "evidence_record_ids": ["evidence-record-1"],
            }
        ],
        "metadata": {
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-record-1",
                    "entity": "wg",
                    "verified_quote": "wg is expressed in embryonic stripes.",
                    "page": 3,
                    "section": "Results",
                    "chunk_id": "chunk-1",
                }
            ]
        },
        "run_summary": {"candidate_count": 1, "kept_count": 1},
    }
    raw_event = SimpleNamespace(
        type="raw_response_event",
        data=ResponseTextDoneEvent(json.dumps(generated_payload)),
    )
    stream_error = RuntimeError("Invalid JSON when parsing text for TypeAdapter")

    captured_events = []
    captured_builder_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_builder_events.append(event) or event,
    )
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FailingStreamRunResult(
            events=[raw_event],
            final_output=None,
            new_items=[],
            error=stream_error,
        ),
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[],
        output_type=_DomainEnvelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(RuntimeError, match="Invalid JSON"):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract gene expression evidence",
            specialist_name="Gene Expression Extractor",
            max_turns=3,
            tool_name="ask_gene_expression_specialist",
        )

    assert not any(
        e.get("type") == "SPECIALIST_TEXT_FALLBACK_SUCCESS"
        and e.get("details", {}).get("extraction_method") == "stream_validation_recovery"
        for e in captured_events
    )
    assert any(
        event["event_type"] == "extraction_builder.aborted"
        and event["output_summary"]["reason"] == (
            "RuntimeError: Invalid JSON when parsing text for TypeAdapter"
        )
        for event in captured_builder_events
    )


@pytest.mark.asyncio
async def test_run_specialist_does_not_recover_non_domain_stream_errors(monkeypatch):
    stream_error = RuntimeError("Invalid JSON when parsing text for TypeAdapter")
    captured_builder_events = []

    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_builder_events.append(event) or event,
    )
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FailingStreamRunResult(
            events=[],
            final_output=None,
            new_items=[],
            error=stream_error,
        ),
    )

    agent = SimpleNamespace(
        name="Structured Specialist",
        tools=[],
        output_type=_Envelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(RuntimeError, match="Invalid JSON"):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract structured output",
            specialist_name="Structured Specialist",
            max_turns=3,
            tool_name=None,
        )

    assert any(
        event["event_type"] == "extraction_builder.aborted"
        for event in captured_builder_events
    )


@pytest.mark.asyncio
async def test_run_specialist_rejects_structured_json_from_new_items_without_final_output(
    monkeypatch,
):
    generated_payload = {
        "summary": "Model-authored envelope text",
        "curatable_objects": [
            {
                "object_type": "gene_expression_annotation",
                "pending_ref_id": "annotation-1",
                "payload": {"gene_symbol": "wg", "assay": "in situ"},
                "evidence_record_ids": ["evidence-record-1"],
            }
        ],
        "metadata": {
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-record-1",
                    "entity": "wg",
                    "verified_quote": "wg is expressed in embryonic stripes.",
                    "page": 3,
                    "section": "Results",
                    "chunk_id": "chunk-1",
                }
            ]
        },
        "run_summary": {"candidate_count": 1, "kept_count": 1},
    }
    message_item = SimpleNamespace(type="message_output_item")
    fake_items_module = ModuleType("agents.items")
    setattr(
        fake_items_module,
        "ItemHelpers",
        SimpleNamespace(text_message_output=lambda _item: json.dumps(generated_payload)),
    )

    calls = {"count": 0}
    captured_events = []
    captured_builder_events = []

    def _run_streamed(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _FakeRunResult(
                events=[],
                final_output=None,
                new_items=[message_item],
            )
        return _FakeRunResult(events=[], final_output=None, new_items=[])

    async def _pass_through_validator(final_output, **_kwargs):
        return final_output

    monkeypatch.setitem(sys.modules, "agents.items", fake_items_module)
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_builder_events.append(event) or event,
    )
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)
    monkeypatch.setattr(
        streaming_tools,
        "_emit_specialist_evidence_summary_or_raise",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _pass_through_validator,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[],
        output_type=_DomainEnvelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(streaming_tools.SpecialistOutputError):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract gene expression evidence",
            specialist_name="Gene Expression Extractor",
            max_turns=3,
            tool_name="ask_gene_expression_specialist",
        )

    assert calls["count"] == 2
    assert not any(
        e.get("type") == "SPECIALIST_TEXT_FALLBACK_SUCCESS"
        and e.get("details", {}).get("extraction_method") == "text_fallback_new_items"
        for e in captured_events
    )
    assert not any(
        e.get("type") == streaming_tools.INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
        for e in captured_events
    )
    assert not any(
        event["event_type"] == "extraction_builder.finalization_decision"
        for event in captured_builder_events
    )


@pytest.mark.asyncio
async def test_run_specialist_marks_builder_cancelled_on_stream_cancellation(monkeypatch):
    captured_builder_events = []

    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_builder_events.append(event) or event,
    )
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FailingStreamRunResult(
            events=[],
            final_output=None,
            new_items=[],
            error=asyncio.CancelledError(),
        ),
    )

    agent = SimpleNamespace(
        name="Structured Specialist",
        tools=[],
        output_type=_Envelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(asyncio.CancelledError):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract structured output",
            specialist_name="Structured Specialist",
            max_turns=3,
            tool_name=None,
        )

    assert any(
        event["event_type"] == "extraction_builder.cancelled"
        for event in captured_builder_events
    )


@pytest.mark.asyncio
async def test_run_specialist_resets_evidence_workspace_after_stream_error(monkeypatch):
    class _WorkspaceInspectingRunResult(_FakeRunResult):
        async def stream_events(self):
            assert evidence_workspace._workspace_records() == []
            yield SimpleNamespace(type="noop")
            raise RuntimeError("stream exploded")

    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _WorkspaceInspectingRunResult(),
    )

    agent = SimpleNamespace(
        name="Structured Specialist",
        tools=[],
        output_type=_Envelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(RuntimeError, match="stream exploded"):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract structured output",
            specialist_name="Structured Specialist",
            max_turns=3,
            tool_name=None,
        )

    with pytest.raises(RuntimeError, match="No active evidence workspace"):
        evidence_workspace._workspace_records()


@pytest.mark.asyncio
async def test_run_specialist_resets_run_state_when_the_tool_surface_fails(monkeypatch):
    from src.lib.openai_agents import resolver_call_ledger
    from src.lib.openai_agents.tool_surface import ToolSurfaceError

    def _fail(*_args, **_kwargs):
        raise ToolSurfaceError("namespace over the cap")

    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "apply_tool_surface", _fail)
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: pytest.fail("the run must not start"),
    )

    with pytest.raises(ToolSurfaceError, match="namespace over the cap"):
        await streaming_tools.run_specialist_with_events(
            agent=SimpleNamespace(
                name="Structured Specialist", tools=[], output_type=_Envelope,
                instructions="", model="gpt-4o",
            ),
            input_text="extract structured output",
            specialist_name="Structured Specialist",
            max_turns=3,
            tool_name=None,
        )

    with pytest.raises(RuntimeError, match="No active evidence workspace"):
        evidence_workspace._workspace_records()
    with pytest.raises(RuntimeError, match="No active extraction builder workspace"):
        builder.get_active_extraction_builder_workspace()
    with pytest.raises(RuntimeError, match="No active resolver call ledger"):
        resolver_call_ledger.get_active_resolver_call_ledger()


@pytest.mark.asyncio
async def test_custom_agent_over_the_tool_group_cap_reports_a_curator_message(monkeypatch):
    from src.lib.openai_agents.tool_surface import ToolGroupCapError

    cap_message = (
        "This agent has 11 tools from the 'staged object corrections' group, and custom "
        "agents can use at most 10 tools from one group. Please contact the AI Curation "
        "developers for help setting up this agent."
    )

    def _over_cap(*_args, **_kwargs):
        raise ToolGroupCapError(
            cap_message,
            runtime="extractor",
            agent_key="ca_custom",
            oversized={"staged_object_corrections": ["patch_a"] * 11},
        )

    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "apply_tool_surface", _over_cap)

    with pytest.raises(ToolGroupCapError):
        await streaming_tools.run_specialist_with_events(
            agent=SimpleNamespace(
                name="Custom Extractor", tools=[], output_type=_Envelope,
                instructions="", model="gpt-4o",
            ),
            input_text="extract structured output",
            specialist_name="Custom Extractor",
            max_turns=3,
            tool_name=None,
        )

    [error_event] = [e for e in captured_events if e["type"] == "SPECIALIST_ERROR"]
    # The audit line and a flow's failure message read details.message/error.
    assert error_event["details"] == {
        "specialist": "Custom Extractor",
        "error": cap_message,
        "message": cap_message,
        "reason": "tool_group_too_large",
        "severity": "error",
    }


@pytest.mark.asyncio
async def test_run_specialist_retry_succeeds_when_initial_output_missing(monkeypatch):
    first = _FakeRunResult(events=[], final_output=None, new_items=[])
    second = _FakeRunResult(events=[], final_output=_Envelope(value="ok"), new_items=[])
    calls = {"count": 0}

    def _run_streamed(*args, **kwargs):
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)

    agent = SimpleNamespace(
        name="Structured Specialist",
        tools=[],
        output_type=_Envelope,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract structured output",
        specialist_name="Structured Specialist",
        max_turns=3,
        tool_name=None,
    )

    assert calls["count"] == 2
    assert "structured result accepted" in result
    assert "Full validated payload is retained by the specialist runtime" in result
    assert "value=ok" in result
    assert any(e.get("type") == "SPECIALIST_RETRY" for e in captured_events)
    assert any(e.get("type") == "SPECIALIST_RETRY_SUCCESS" for e in captured_events)


@pytest.mark.asyncio
async def test_run_specialist_retry_raises_when_retry_also_missing_output(monkeypatch):
    first = _FakeRunResult(events=[], final_output=None, new_items=[])
    second = _FakeRunResult(events=[], final_output=None, new_items=[])
    calls = {"count": 0}

    def _run_streamed(*args, **kwargs):
        calls["count"] += 1
        return first if calls["count"] == 1 else second

    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)

    agent = SimpleNamespace(
        name="Structured Specialist",
        tools=[],
        output_type=_Envelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(streaming_tools.SpecialistOutputError):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract structured output",
            specialist_name="Structured Specialist",
            max_turns=3,
            tool_name=None,
        )

    assert calls["count"] == 2
    assert any(e.get("type") == "SPECIALIST_RETRY" for e in captured_events)
    assert any(e.get("type") == "SPECIALIST_ERROR" for e in captured_events)


_DEFERRED_RUN_HISTORY = [
    {"role": "user", "content": "extract structured output"},
    {"type": "reasoning", "id": "rs_search", "summary": []},
    {"type": "tool_search_call", "id": "ts_1", "call_id": None, "execution": "server",
     "arguments": {"paths": ["evidence_maintenance"]}, "status": "completed"},
    {"type": "tool_search_output", "id": "tso_1", "call_id": None, "execution": "server",
     "status": "completed", "tools": [{"type": "namespace", "name": "evidence_maintenance",
                                      "description": "Record and maintain evidence.", "tools": []}]},
    {"type": "reasoning", "id": "rs_call", "summary": []},
    {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "record_evidence",
     "namespace": "evidence_maintenance", "arguments": "{\"span_id\": \"s1\"}", "status": "completed"},
    {"type": "function_call_output", "call_id": "call_1", "output": "evidence e1 recorded"},
]


@pytest.mark.asyncio
async def test_structured_retry_request_replays_deferred_history_without_tool_search(monkeypatch):
    """ALL-1280: the tool-less retry request carries every call/result, no search items."""
    from agents import AgentOutputSchema, ModelSettings
    from agents.models.openai_responses import OpenAIResponsesModel
    from openai import AsyncOpenAI

    class _DeferredHistoryRunResult(_FakeRunResult):
        def to_input_list(self):
            return [dict(item) for item in _DEFERRED_RUN_HISTORY]

    first = _DeferredHistoryRunResult(events=[], final_output=None, new_items=[])
    second = _FakeRunResult(events=[], final_output=_Envelope(value="ok"), new_items=[])
    calls = []

    def _run_streamed(agent, **kwargs):
        calls.append((agent, kwargs))
        return first if len(calls) == 1 else second

    monkeypatch.setattr(streaming_tools, "add_specialist_event", lambda _event: None)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)

    await streaming_tools.run_specialist_with_events(
        agent=SimpleNamespace(
            name="Structured Specialist", tools=[], output_type=_Envelope,
            instructions="", model="gpt-4o",
        ),
        input_text="extract structured output",
        specialist_name="Structured Specialist",
        max_turns=3,
        tool_name=None,
    )

    retry_agent, retry_kwargs = calls[1]
    assert retry_agent.tools == []
    request = OpenAIResponsesModel(
        model="gpt-4o", openai_client=AsyncOpenAI(api_key="test-key")
    )._build_response_create_kwargs(
        system_instructions=retry_agent.instructions,
        input=retry_kwargs["input"],
        model_settings=ModelSettings(),
        tools=retry_agent.tools,
        output_schema=AgentOutputSchema(_Envelope),
        handoffs=[],
    )

    item_types = [item.get("type") for item in request["input"]]
    assert "tool_search_call" not in item_types
    assert "tool_search_output" not in item_types
    # Reasoning tied to the removed search is removed (the API rejects a
    # reasoning item without its following item); reasoning before the kept
    # function call stays in front of it.
    assert [item.get("id") for item in request["input"] if item.get("type") == "reasoning"] == [
        "rs_call"
    ]
    assert item_types[item_types.index("reasoning") + 1] == "function_call"
    assert request["tools"] == []
    [call] = [item for item in request["input"] if item.get("type") == "function_call"]
    assert "namespace" not in call
    assert (call["call_id"], call["name"], call["arguments"]) == (
        "call_1", "record_evidence", '{"span_id": "s1"}',
    )
    [output] = [item for item in request["input"] if item.get("type") == "function_call_output"]
    assert output == {"type": "function_call_output", "call_id": "call_1", "output": "evidence e1 recorded"}
    assert request["input"][-1]["role"] == "user"


def test_validator_lookup_audit_events_dedupe_identical_batch_attempts(monkeypatch):
    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)

    query = {
        "data_provider": "FB",
        "gene_symbols": ["actin", "crumbs", "opsin"],
        "include_synonyms": True,
        "limit": 10,
    }

    def result(request_id, status):
        return SimpleNamespace(
            request_id=request_id,
            validator_binding_id="alliance_gene_reference_lookup",
            status=status,
            lookup_attempts=[
                SimpleNamespace(
                    provider="alliance_curation_db",
                    method="search_genes_bulk",
                    query=query,
                    result_count=3,
                    outcome="success",
                    message=None,
                )
            ],
        )

    dispatch_result = SimpleNamespace(
        validator_results=[
            result("request-crumbs", "resolved"),
            result("request-actin", "unresolved"),
            result("request-opsin", "unresolved"),
        ]
    )

    streaming_tools._emit_validator_lookup_audit_events(
        specialist_name="Gene Extraction",
        dispatch_result=dispatch_result,
    )

    start_events = [event for event in captured_events if event["type"] == "TOOL_START"]
    complete_events = [
        event for event in captured_events if event["type"] == "TOOL_COMPLETE"
    ]

    assert len(start_events) == 1
    assert len(complete_events) == 1
    start_details = start_events[0]["details"]
    complete_details = complete_events[0]["details"]
    assert start_details["validatorResultStatus"] == "mixed"
    assert start_details["validatorResultStatuses"] == {
        "resolved": 1,
        "unresolved": 2,
    }
    assert start_details["validatorLookupDuplicateCount"] == 3
    assert start_details["validatorLookupRequestIds"] == [
        "request-crumbs",
        "request-actin",
        "request-opsin",
    ]
    assert complete_details["friendlyName"] == (
        "Gene Extraction: Validator Lookup success "
        "(3 targets, mixed validation)"
    )


def test_validator_lookup_audit_events_keep_distinct_queries(monkeypatch):
    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)

    dispatch_result = SimpleNamespace(
        validator_results=[
            SimpleNamespace(
                request_id="request-crumbs",
                validator_binding_id="alliance_gene_reference_lookup",
                status="resolved",
                lookup_attempts=[
                    SimpleNamespace(
                        provider="alliance_curation_db",
                        method="search_genes",
                        query={"data_provider": "FB", "gene_symbol": "crumbs"},
                        result_count=1,
                        outcome="success",
                        message=None,
                    )
                ],
            ),
            SimpleNamespace(
                request_id="request-actin",
                validator_binding_id="alliance_gene_reference_lookup",
                status="unresolved",
                lookup_attempts=[
                    SimpleNamespace(
                        provider="alliance_curation_db",
                        method="search_genes",
                        query={"data_provider": "FB", "gene_symbol": "actin"},
                        result_count=10,
                        outcome="ambiguous",
                        message=None,
                    )
                ],
            ),
        ]
    )

    streaming_tools._emit_validator_lookup_audit_events(
        specialist_name="Gene Extraction",
        dispatch_result=dispatch_result,
    )

    assert (
        len([event for event in captured_events if event["type"] == "TOOL_START"])
        == 2
    )
    assert (
        len([event for event in captured_events if event["type"] == "TOOL_COMPLETE"])
        == 2
    )
