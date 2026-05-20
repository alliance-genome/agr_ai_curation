"""Coverage tests for streaming_tools retry and text-fallback paths."""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.lib.openai_agents import streaming_tools
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
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)
    yield
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
async def test_run_specialist_recovers_domain_envelope_text_after_stream_validation_error(monkeypatch):
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

    async def _passthrough_dispatch(final_output, **_kwargs):
        return final_output

    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _passthrough_dispatch,
    )
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

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract gene expression evidence",
        specialist_name="Gene Expression Extractor",
        max_turns=3,
        tool_name="ask_gene_expression_specialist",
    )

    parsed_result = json.loads(result)
    assert parsed_result["curatable_objects"][0]["pending_ref_id"] == (
        "salvaged_gene_expression_annotation_1"
    )
    assert parsed_result["curatable_objects"][0]["evidence_record_ids"] == [
        "evidence-record-1"
    ]
    assert parsed_result["metadata"]["evidence_records"][0]["evidence_record_id"] == (
        "evidence-record-1"
    )
    assert any(
        e.get("type") == "SPECIALIST_TEXT_FALLBACK_SUCCESS"
        and e.get("details", {}).get("extraction_method") == "stream_validation_recovery"
        for e in captured_events
    )


@pytest.mark.asyncio
async def test_run_specialist_does_not_recover_non_domain_stream_errors(monkeypatch):
    stream_error = RuntimeError("Invalid JSON when parsing text for TypeAdapter")

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
    assert result == '{"value": "ok"}'
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
