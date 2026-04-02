"""Contract tests for TOOL_START/TOOL_COMPLETE friendlyName emission."""

from types import SimpleNamespace
import json

import pytest

from src.lib.openai_agents import runner, streaming_tools
from src.lib.openai_agents.evidence_summary import build_evidence_record_id
from src.lib.openai_agents.models import AlleleExtractionResultEnvelope


class _FakeRunResult:
    def __init__(self, events, final_output="ok"):
        self._events = events
        self.final_output = final_output

    async def stream_events(self):
        for event in self._events:
            yield event


class _FakeStructuredOutput:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


class _FakeRunResultWithLiveEvidence(_FakeRunResult):
    def __init__(
        self,
        events,
        live_event_list_ref,
        *,
        tool_name: str | None = "ask_gene_specialist",
        final_output="ok",
    ):
        super().__init__(events, final_output=final_output)
        self._live_event_list_ref = live_event_list_ref
        self._tool_name = tool_name

    async def stream_events(self):
        live_event_list = self._live_event_list_ref.get("value")
        if live_event_list is not None:
            payload = {
                "type": "evidence_summary",
                "evidence_records": [
                    {
                        "entity": "crumb",
                        "verified_quote": "Crumb is essential for maintaining epithelial polarity.",
                        "page": 4,
                        "section": "Results",
                        "chunk_id": "chunk-live-1",
                    }
                ],
            }
            if self._tool_name:
                payload["tool_name"] = self._tool_name
            live_event_list.append(payload)
        async for event in super().stream_events():
            yield event


def _build_expected_evidence_record(
    *,
    entity: str,
    chunk_id: str,
    verified_quote: str,
    page: int,
    section: str,
    subsection: str | None = None,
    figure_reference: str | None = None,
):
    record = {
        "entity": entity,
        "verified_quote": verified_quote,
        "page": page,
        "section": section,
        "chunk_id": chunk_id,
    }
    if subsection:
        record["subsection"] = subsection
    if figure_reference:
        record["figure_reference"] = figure_reference
    record["evidence_record_id"] = build_evidence_record_id(evidence_record=record)
    return record


def _tool_call_stream_event(
    name: str,
    arguments: str = '{"query":"test"}',
    *,
    call_id: str | None = None,
):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="tool_call_item",
            name=name,
            raw_item=SimpleNamespace(arguments=arguments, call_id=call_id),
        ),
    )


def _tool_output_stream_event(output: str = '{"summary":"ok"}', *, call_id: str | None = None):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="tool_call_output_item",
            output=output,
            raw_item=SimpleNamespace(call_id=call_id),
        ),
    )


def _raw_response_stream_event(data):
    return SimpleNamespace(type="raw_response_event", data=data)


def _handoff_call_stream_event(target_name: str):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="handoff_call_item",
            target_agent=SimpleNamespace(name=target_name),
        ),
    )


def _handoff_output_stream_event(source_name: str):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="handoff_output_item",
            source_agent=SimpleNamespace(name=source_name),
        ),
    )


def _agent_updated_stream_event(agent_name: str):
    return SimpleNamespace(
        type="agent_updated_stream_event",
        new_agent=SimpleNamespace(name=agent_name),
    )


@pytest.mark.asyncio
async def test_runner_tool_events_emit_canonical_friendly_names(monkeypatch):
    fake_events = [
        _tool_call_stream_event("ask_gene_specialist"),
        _tool_output_stream_event(),
    ]

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(fake_events, final_output="done"),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[
            SimpleNamespace(
                name="ask_gene_specialist",
                description="Ask the Gene Validation Agent",
            )
        ],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "validate genes"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="validate genes",
            trace_id="trace-1",
        )
    ]

    tool_events = [
        event for event in emitted_events if event.get("type") in {"TOOL_START", "TOOL_COMPLETE"}
    ]
    assert tool_events, "Expected TOOL_START/TOOL_COMPLETE events from runner stream"

    assert tool_events[0]["details"]["friendlyName"] == "Calling Gene Validation Agent..."
    assert tool_events[1]["details"]["friendlyName"] == "Gene Validation Agent complete"
    assert tool_events[1]["internal"]["tool_input"] == {"query": "test"}


@pytest.mark.asyncio
async def test_runner_emits_evidence_summary_for_record_evidence_tool_calls(monkeypatch):
    expected_record = _build_expected_evidence_record(
        entity="crumb",
        chunk_id="chunk-1",
        verified_quote="Crumb is essential for maintaining epithelial polarity.",
        page=4,
        section="Results",
        subsection="Gene Expression Analysis",
        figure_reference="Figure 2A",
    )
    fake_events = [
        _tool_call_stream_event(
            "record_evidence",
            arguments='{"entity":"crumb","chunk_id":"chunk-1","claimed_quote":"Crumb is essential"}',
        ),
        _tool_output_stream_event(
            json.dumps(
                {
                    "status": "verified",
                    "verified_quote": "Crumb is essential for maintaining epithelial polarity.",
                    "page": 4,
                    "section": "Results",
                    "subsection": "Gene Expression Analysis",
                    "figure_reference": "Figure 2A",
                }
            )
        ),
    ]

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(fake_events, final_output="done"),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[
            SimpleNamespace(
                name="record_evidence",
                description="Ask the Evidence Recorder",
            )
        ],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "review evidence evidence"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="review evidence",
            trace_id="trace-evidence",
        )
    ]

    event_types = [event.get("type") for event in emitted_events]

    assert event_types[-2:] == ["evidence_summary", "RUN_FINISHED"]
    assert "SUPERVISOR_COMPLETE" in event_types
    assert emitted_events[event_types.index("evidence_summary")]["evidence_records"] == [expected_record]


@pytest.mark.asyncio
async def test_runner_matches_concurrent_record_evidence_outputs_by_call_id(monkeypatch):
    crumbs_record = _build_expected_evidence_record(
        entity="crumbs",
        chunk_id="chunk-crumbs-1",
        verified_quote="Changes in molecular organization following abnormal PRC development in crumbs mutants.",
        page=1,
        section="Results and Discussion",
        subsection="Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants",
        figure_reference="Figure 5E",
    )
    ninae_record = _build_expected_evidence_record(
        entity="ninaE",
        chunk_id="chunk-ninae-1",
        verified_quote="Decreased levels of Rh1 induced by mutating the ninaE gene resulted in substantially smaller rhabdomeres.",
        page=3,
        section="Results and Discussion",
        subsection="The Molar Abundance of Actins, Opsin, and Crumbs in Fly Eyes",
    )
    fake_events = [
        _tool_call_stream_event(
            "record_evidence",
            arguments='{"entity":"crumbs","chunk_id":"chunk-crumbs-1","claimed_quote":"Changes in molecular organization following abnormal PRC development in crumbs mutants."}',
            call_id="call-crumbs",
        ),
        _tool_call_stream_event(
            "record_evidence",
            arguments='{"entity":"ninaE","chunk_id":"chunk-ninae-1","claimed_quote":"Decreased levels of Rh1 induced by mutating the ninaE gene resulted in substantially smaller rhabdomeres."}',
            call_id="call-ninae",
        ),
        _tool_output_stream_event(
            json.dumps(
                {
                    "status": "verified",
                    "verified_quote": crumbs_record["verified_quote"],
                    "page": crumbs_record["page"],
                    "section": crumbs_record["section"],
                    "subsection": crumbs_record["subsection"],
                    "figure_reference": crumbs_record["figure_reference"],
                }
            ),
            call_id="call-crumbs",
        ),
        _tool_output_stream_event(
            json.dumps(
                {
                    "status": "verified",
                    "verified_quote": ninae_record["verified_quote"],
                    "page": ninae_record["page"],
                    "section": ninae_record["section"],
                    "subsection": ninae_record["subsection"],
                }
            ),
            call_id="call-ninae",
        ),
    ]

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(fake_events, final_output="done"),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[SimpleNamespace(name="record_evidence", description="Ask the Evidence Recorder")],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "review evidence"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="review evidence",
            trace_id="trace-evidence-concurrent",
        )
    ]

    evidence_event = next(event for event in emitted_events if event.get("type") == "evidence_summary")
    assert evidence_event["evidence_records"] == [crumbs_record, ninae_record]


@pytest.mark.asyncio
async def test_runner_emits_evidence_summary_from_structured_extraction_result(monkeypatch):
    crumbs_record = _build_expected_evidence_record(
        entity="crumbs",
        chunk_id="chunk-crumbs-1",
        verified_quote="Changes in molecular organization following abnormal PRC development in crumbs mutants.",
        page=1,
        section="Results and Discussion",
        subsection="Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants",
        figure_reference="Figure 5E",
    )
    crb_record = _build_expected_evidence_record(
        entity="crb",
        chunk_id="chunk-crb-1",
        verified_quote="all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates.",
        page=1,
        section="Results and Discussion",
        subsection="Quantitative Changes of Proteins in crb Mutant Alleles",
    )
    fake_events = [
        _tool_call_stream_event(
            "record_evidence",
            arguments='{"entity":"crumbs","chunk_id":"chunk-crumbs-1","claimed_quote":"Changes in molecular organization following abnormal PRC development in crumbs mutants."}',
        ),
        _tool_output_stream_event(
            json.dumps(
                {
                    "status": "verified",
                    "verified_quote": crumbs_record["verified_quote"],
                    "page": crumbs_record["page"],
                    "section": crumbs_record["section"],
                    "subsection": crumbs_record["subsection"],
                    "figure_reference": crumbs_record["figure_reference"],
                }
            )
        ),
        _tool_call_stream_event(
            "record_evidence",
            arguments='{"entity":"crb","chunk_id":"chunk-crb-1","claimed_quote":"all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates."}',
        ),
        _tool_output_stream_event(
            json.dumps(
                {
                    "status": "verified",
                    "verified_quote": crb_record["verified_quote"],
                    "page": crb_record["page"],
                    "section": crb_record["section"],
                    "subsection": crb_record["subsection"],
                }
            )
        ),
    ]

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            fake_events,
            final_output={
                "summary": "Extracted focal genes with duplicate retained aliases.",
                "genes": [
                    {
                        "mention": "crumbs",
                        "normalized_symbol": "crb",
                        "normalized_id": "FB:FBgn0000368",
                        "species": "Drosophila melanogaster",
                        "confidence": "high",
                        "evidence_record_ids": [crumbs_record["evidence_record_id"]],
                    },
                    {
                        "mention": "crb",
                        "normalized_symbol": "crb",
                        "normalized_id": "FB:FBgn0000368",
                        "species": "Drosophila melanogaster",
                        "confidence": "high",
                        "evidence_record_ids": [crb_record["evidence_record_id"]],
                    },
                ],
                "items": [
                    {
                        "label": "crumbs",
                        "entity_type": "gene",
                        "normalized_id": "FB:FBgn0000368",
                        "source_mentions": ["crumbs"],
                        "evidence_record_ids": [
                            crumbs_record["evidence_record_id"],
                            crb_record["evidence_record_id"],
                        ],
                    },
                    {
                        "label": "crb",
                        "entity_type": "gene",
                        "normalized_id": "FB:FBgn0000368",
                        "source_mentions": ["crb"],
                        "evidence_record_ids": [crb_record["evidence_record_id"]],
                    },
                ],
                "evidence_records": [],
                "run_summary": {"kept_count": 1},
            },
        ),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "review evidence evidence"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="review evidence",
            trace_id="trace-evidence",
        )
    ]

    event_types = [event.get("type") for event in emitted_events]

    assert event_types[-2:] == ["evidence_summary", "RUN_FINISHED"]
    assert "STRUCTURED_RESULT" in event_types
    assert "SUPERVISOR_COMPLETE" in event_types
    assert emitted_events[event_types.index("STRUCTURED_RESULT")]["data"]["result"]["items"] == [
        {
            "label": "crb",
            "entity_type": "gene",
            "normalized_id": "FB:FBgn0000368",
            "source_mentions": ["crumbs", "crb"],
            "evidence_record_ids": [
                crumbs_record["evidence_record_id"],
                crb_record["evidence_record_id"],
            ],
        }
    ]
    assert emitted_events[event_types.index("evidence_summary")]["evidence_records"] == [
        crumbs_record,
        crb_record,
    ]


@pytest.mark.asyncio
async def test_runner_fails_fast_when_structured_extraction_result_is_missing_evidence(monkeypatch):
    fake_events = []

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            fake_events,
            final_output={
                "summary": "Extracted focal genes but evidence was lost.",
                "items": [
                    {
                        "label": "crumb",
                        "entity_type": "gene",
                        "normalized_id": "FB:FBgn0000001",
                        "source_mentions": ["crumb"],
                    }
                ],
                "evidence_records": [],
                "run_summary": {"kept_count": 1},
            },
        ),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "review evidence"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="review evidence",
            trace_id="trace-missing-evidence",
        )
    ]

    assert emitted_events == [
        {
            "type": "RUN_ERROR",
            "data": {
                "message": (
                    "Extraction completed without the required verified evidence records. "
                    "Please report this run so we can investigate."
                ),
                "error_type": "MissingEvidenceRecords",
                "trace_id": "trace-missing-evidence",
            },
        }
    ]


@pytest.mark.asyncio
async def test_runner_buffers_live_specialist_evidence_until_completion(monkeypatch):
    fake_events = []
    live_event_list_ref = {"value": None}

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda events: live_event_list_ref.__setitem__("value", events))
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResultWithLiveEvidence(
            fake_events,
            live_event_list_ref,
            final_output="done",
        ),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "review evidence"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="review evidence",
            trace_id="trace-live-evidence",
        )
    ]

    event_types = [event.get("type") for event in emitted_events]
    assert event_types.count("evidence_summary") == 1
    assert event_types[-2:] == ["evidence_summary", "RUN_FINISHED"]
    evidence_event = next(event for event in emitted_events if event.get("type") == "evidence_summary")
    assert evidence_event["tool_name"] == "ask_gene_specialist"
    assert evidence_event["tool_names"] == ["ask_gene_specialist"]


@pytest.mark.asyncio
async def test_runner_fails_fast_without_structured_evidence_even_when_live_mention_evidence_exists(monkeypatch):
    fake_events = []
    live_event_list_ref = {"value": None}

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda events: live_event_list_ref.__setitem__("value", events))
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResultWithLiveEvidence(
            fake_events,
            live_event_list_ref,
            final_output={
                "summary": "Extractor kept a gene but lost canonical evidence.",
                "items": [
                    {
                        "label": "crb",
                        "entity_type": "gene",
                        "normalized_id": "FB:FBgn0000368",
                        "source_mentions": ["crumbs", "crb"],
                        "evidence": [],
                    }
                ],
                "evidence_records": [],
                "run_summary": {"kept_count": 1},
            },
        ),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "review evidence"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="review evidence",
            trace_id="trace-no-fallback",
        )
    ]

    assert emitted_events == [
        {
            "type": "RUN_ERROR",
            "data": {
                "message": (
                    "Extraction completed without the required verified evidence records. "
                    "Please report this run so we can investigate."
                ),
                "error_type": "MissingEvidenceRecords",
                "trace_id": "trace-no-fallback",
            },
        }
    ]


@pytest.mark.asyncio
async def test_runner_fails_fast_when_kept_count_is_positive_but_items_are_missing(monkeypatch):
    fake_events = [
        _tool_call_stream_event(
            "record_evidence",
            arguments='{"entity":"crumb","chunk_id":"chunk-1","claimed_quote":"Crumb is essential for maintaining epithelial polarity."}',
        ),
        _tool_output_stream_event(
            json.dumps(
                {
                    "status": "verified",
                    "verified_quote": "Crumb is essential for maintaining epithelial polarity.",
                    "page": 4,
                    "section": "Results",
                }
            )
        ),
    ]

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            fake_events,
            final_output={
                "summary": "Extractor claimed one retained gene but emitted no item payload.",
                "evidence_records": [],
                "run_summary": {"kept_count": 1},
            },
        ),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "review evidence"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="review evidence",
            trace_id="trace-missing-items",
        )
    ]

    assert [event.get("type") for event in emitted_events[:-1]] == [
        "TOOL_START",
        "TOOL_COMPLETE",
    ]
    assert emitted_events[-1] == {
        "type": "RUN_ERROR",
        "data": {
            "message": (
                "Extraction completed without the required verified evidence records. "
                "Please report this run so we can investigate."
            ),
            "error_type": "MissingEvidenceRecords",
            "trace_id": "trace-missing-items",
        },
    }


@pytest.mark.asyncio
async def test_runner_accepts_schema_defined_retained_collection_without_items(monkeypatch):
    verified_quote = "Actin 5C was the focal allele examined in the study."
    expected_record = _build_expected_evidence_record(
        entity="Actin 5C",
        chunk_id="chunk-1",
        verified_quote=verified_quote,
        page=4,
        section="Results",
    )
    fake_events = [
        _tool_call_stream_event(
            "record_evidence",
            arguments=json.dumps(
                {
                    "entity": "Actin 5C",
                    "chunk_id": "chunk-1",
                    "claimed_quote": verified_quote,
                }
            ),
        ),
        _tool_output_stream_event(
            json.dumps(
                {
                    "status": "verified",
                    "verified_quote": verified_quote,
                    "page": 4,
                    "section": "Results",
                }
            )
        ),
    ]

    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            fake_events,
            final_output={
                "summary": "Retained one focal allele with verified evidence.",
                "alleles": [
                    {
                        "mention": "Actin 5C",
                        "normalized_symbol": "Act5C",
                        "normalized_id": "FB:FBal0000001",
                        "associated_gene": "Act5C",
                        "confidence": "high",
                        "evidence_record_ids": [expected_record["evidence_record_id"]],
                    }
                ],
                "items": [],
                "evidence_records": [],
                "run_summary": {"kept_count": 1},
            },
        ),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[],
        model="gpt-4o",
        output_type=AlleleExtractionResultEnvelope,
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "extract alleles"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="extract alleles",
            trace_id="trace-allele-collection",
        )
    ]

    assert not any(event.get("type") == "RUN_ERROR" for event in emitted_events)
    assert any(event.get("type") == "evidence_summary" for event in emitted_events)
    assert emitted_events[-1]["type"] == "RUN_FINISHED"


@pytest.mark.asyncio
async def test_runner_emits_reasoning_file_ready_chat_output_and_handoff_events(monkeypatch):
    class _FakeTextDelta:
        def __init__(self, delta):
            self.delta = delta

    class _FakeArgsDelta:
        def __init__(self, delta):
            self.delta = delta

    class _FakeReasoningDelta:
        def __init__(self, delta):
            self.delta = delta

    file_output = (
        '{"file_id":"f-1","download_url":"/api/files/f-1/download","filename":"results.csv",'
        '"format":"csv","size_bytes":12,"mime_type":"text/csv"}'
    )
    fake_events = [
        _raw_response_stream_event(_FakeTextDelta("Hello ")),
        _raw_response_stream_event(_FakeArgsDelta('{"query":"x"}')),
        _raw_response_stream_event(_FakeReasoningDelta("thinking...")),
        _tool_call_stream_event("ask_chat_output_specialist"),
        _tool_output_stream_event(file_output),
        _handoff_call_stream_event("Gene Agent"),
        _handoff_output_stream_event("Query Supervisor"),
        _agent_updated_stream_event("Gene Agent"),
    ]

    monkeypatch.setattr(runner, "ResponseTextDeltaEvent", _FakeTextDelta)
    monkeypatch.setattr(runner, "ResponseFunctionCallArgumentsDeltaEvent", _FakeArgsDelta)
    monkeypatch.setattr(runner, "ResponseReasoningSummaryTextDeltaEvent", _FakeReasoningDelta)
    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            fake_events,
            final_output={"answer": "All good", "citations": [], "sources": []},
        ),
    )

    agent = SimpleNamespace(
        name="Query Supervisor",
        tools=[SimpleNamespace(name="ask_chat_output_specialist", description="Ask the Chat Output Agent")],
        model="gpt-4o",
    )

    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "export results"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="export results",
            trace_id="trace-2",
        )
    ]
    event_types = [e.get("type") for e in emitted_events]

    assert "AGENT_GENERATING" in event_types
    assert "TEXT_MESSAGE_CONTENT" in event_types
    assert "TOOL_CALL_ARGS" in event_types
    assert "AGENT_THINKING" in event_types
    assert "CHAT_OUTPUT_READY" in event_types
    assert "FILE_READY" in event_types
    assert "HANDOFF_START" in event_types
    assert "CREW_START" in event_types
    assert "STRUCTURED_RESULT" in event_types
    assert "SUPERVISOR_COMPLETE" in event_types
    assert "RUN_FINISHED" in event_types


@pytest.mark.asyncio
async def test_runner_guardrail_yields_run_error_and_skips_completion(monkeypatch):
    monkeypatch.setattr(runner, "SafeLangfuseAsyncOpenAI", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "OpenAIProvider", lambda *args, **kwargs: object())
    monkeypatch.setattr(runner, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(runner, "get_collected_events", lambda: [])
    monkeypatch.setattr(runner, "clear_collected_events", lambda: None)
    monkeypatch.setattr(runner, "set_live_event_list", lambda _events: None)
    monkeypatch.setattr(
        runner.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [],
            final_output={"answer": "No results found", "citations": [], "sources": []},
        ),
    )
    monkeypatch.setattr(
        runner.Answer,
        "model_validate",
        lambda data: SimpleNamespace(answer=data["answer"], citations=[], sources=[]),
    )
    monkeypatch.setattr(runner, "enforce_uncited_negative_guardrail", lambda _ans, _tools: "must search first")

    agent = SimpleNamespace(name="Query Supervisor", tools=[], model="gpt-4o")
    emitted_events = [
        event
        async for event in runner._run_agent_with_tracing(
            agent=agent,
            input_items=[{"role": "user", "content": "not found?"}],
            user_id="user-1",
            document_id=None,
            document_name=None,
            user_message="not found?",
            trace_id="trace-3",
        )
    ]
    event_types = [e.get("type") for e in emitted_events]

    assert "RUN_ERROR" in event_types
    assert "SUPERVISOR_COMPLETE" not in event_types
    assert "RUN_FINISHED" not in event_types


@pytest.mark.asyncio
async def test_specialist_tool_events_emit_humanized_internal_labels(monkeypatch):
    fake_events = [
        _tool_call_stream_event("search_document"),
        _tool_output_stream_event(),
    ]
    captured_events = []

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(fake_events, final_output="done"),
    )

    agent = SimpleNamespace(
        name="Gene Validation Agent",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Gene Validation Agent",
        max_turns=3,
        tool_name="ask_gene_specialist",
    )
    assert result == "done"

    tool_events = [
        event for event in captured_events if event.get("type") in {"TOOL_START", "TOOL_COMPLETE"}
    ]
    assert tool_events, "Expected TOOL_START/TOOL_COMPLETE events from specialist stream"

    assert tool_events[0]["details"]["friendlyName"] == "Gene Validation Agent: Search Document"
    assert tool_events[1]["details"]["friendlyName"] == "Gene Validation Agent: Search Document complete"


@pytest.mark.asyncio
async def test_specialist_required_tool_enforcement_raises_when_search_not_called(monkeypatch):
    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult([], final_output="done"),
    )

    agent = SimpleNamespace(
        name="PDF Specialist",
        tools=[SimpleNamespace(name="search_document")],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(streaming_tools.SpecialistOutputError):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract findings",
            specialist_name="PDF Specialist",
            max_turns=3,
            tool_name=None,
        )

    assert any(e.get("type") == "SPECIALIST_ERROR" for e in captured_events)


@pytest.mark.asyncio
async def test_specialist_emits_evidence_summary_for_structured_extraction_output(monkeypatch):
    captured_events = []
    crumbs_record = _build_expected_evidence_record(
        entity="crumbs",
        chunk_id="chunk-crumbs-1",
        verified_quote="Changes in molecular organization following abnormal PRC development in crumbs mutants.",
        page=1,
        section="Results and Discussion",
        figure_reference="Figure 5E",
    )
    crb_record = _build_expected_evidence_record(
        entity="crb",
        chunk_id="chunk-crb-1",
        verified_quote="all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates.",
        page=1,
        section="Results and Discussion",
    )

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [
                _tool_call_stream_event(
                    "record_evidence",
                    arguments='{"entity":"crumbs","chunk_id":"chunk-crumbs-1","claimed_quote":"Changes in molecular organization following abnormal PRC development in crumbs mutants."}',
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "verified_quote": crumbs_record["verified_quote"],
                            "page": crumbs_record["page"],
                            "section": crumbs_record["section"],
                            "figure_reference": crumbs_record["figure_reference"],
                        }
                    )
                ),
                _tool_call_stream_event(
                    "record_evidence",
                    arguments='{"entity":"crb","chunk_id":"chunk-crb-1","claimed_quote":"all proteins changed in the allele lacking the crb_C isoform constitute interesting candidates."}',
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "verified_quote": crb_record["verified_quote"],
                            "page": crb_record["page"],
                            "section": crb_record["section"],
                        }
                    )
                ),
            ],
            final_output=_FakeStructuredOutput(
                {
                    "summary": "Extracted focal genes with duplicate retained aliases.",
                    "genes": [
                        {
                            "mention": "crumbs",
                            "normalized_symbol": "crb",
                            "normalized_id": "FB:FBgn0000368",
                            "species": "Drosophila melanogaster",
                            "confidence": "high",
                            "evidence_record_ids": [crumbs_record["evidence_record_id"]],
                        },
                        {
                            "mention": "crb",
                            "normalized_symbol": "crb",
                            "normalized_id": "FB:FBgn0000368",
                            "species": "Drosophila melanogaster",
                            "confidence": "high",
                            "evidence_record_ids": [crb_record["evidence_record_id"]],
                        },
                    ],
                    "items": [
                        {
                            "label": "crumbs",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0000368",
                            "source_mentions": ["crumbs"],
                            "evidence_record_ids": [crumbs_record["evidence_record_id"]],
                        },
                        {
                            "label": "crb",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0000368",
                            "source_mentions": ["crb"],
                            "evidence_record_ids": [crb_record["evidence_record_id"]],
                        }
                    ],
                    "evidence_records": [],
                    "run_summary": {"kept_count": 1},
                }
            ),
        ),
    )

    agent = SimpleNamespace(
        name="Gene Validation Agent",
        tools=[],
        output_type=SimpleNamespace(__name__="GeneExtractionResultEnvelope"),
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Gene Validation Agent",
        max_turns=3,
        tool_name="ask_gene_specialist",
    )

    assert json.loads(result)["run_summary"]["kept_count"] == 1
    assert json.loads(result)["items"] == [
        {
            "label": "crb",
            "entity_type": "gene",
            "normalized_id": "FB:FBgn0000368",
            "source_mentions": ["crumbs", "crb"],
            "evidence_record_ids": [
                crumbs_record["evidence_record_id"],
                crb_record["evidence_record_id"],
            ],
        }
    ]
    assert len(json.loads(result)["genes"]) == 1
    evidence_events = [event for event in captured_events if event.get("type") == "evidence_summary"]
    assert len(evidence_events) == 1
    assert evidence_events[0]["tool_name"] == "ask_gene_specialist"
    assert evidence_events[0]["evidence_records"] == [crumbs_record, crb_record]


@pytest.mark.asyncio
async def test_specialist_matches_concurrent_record_evidence_outputs_by_call_id(monkeypatch):
    captured_events = []
    crumbs_record = _build_expected_evidence_record(
        entity="crumbs",
        chunk_id="chunk-crumbs-1",
        verified_quote="Changes in molecular organization following abnormal PRC development in crumbs mutants.",
        page=1,
        section="Results and Discussion",
        subsection="Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants",
        figure_reference="Figure 5E",
    )
    ninae_record = _build_expected_evidence_record(
        entity="ninaE",
        chunk_id="chunk-ninae-1",
        verified_quote="Decreased levels of Rh1 induced by mutating the ninaE gene resulted in substantially smaller rhabdomeres.",
        page=3,
        section="Results and Discussion",
        subsection="The Molar Abundance of Actins, Opsin, and Crumbs in Fly Eyes",
    )

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [
                _tool_call_stream_event(
                    "record_evidence",
                    arguments='{"entity":"crumbs","chunk_id":"chunk-crumbs-1","claimed_quote":"Changes in molecular organization following abnormal PRC development in crumbs mutants."}',
                    call_id="call-crumbs",
                ),
                _tool_call_stream_event(
                    "record_evidence",
                    arguments='{"entity":"ninaE","chunk_id":"chunk-ninae-1","claimed_quote":"Decreased levels of Rh1 induced by mutating the ninaE gene resulted in substantially smaller rhabdomeres."}',
                    call_id="call-ninae",
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "verified_quote": crumbs_record["verified_quote"],
                            "page": crumbs_record["page"],
                            "section": crumbs_record["section"],
                            "subsection": crumbs_record["subsection"],
                            "figure_reference": crumbs_record["figure_reference"],
                        }
                    ),
                    call_id="call-crumbs",
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "verified_quote": ninae_record["verified_quote"],
                            "page": ninae_record["page"],
                            "section": ninae_record["section"],
                            "subsection": ninae_record["subsection"],
                        }
                    ),
                    call_id="call-ninae",
                ),
            ],
            final_output=_FakeStructuredOutput(
                {
                    "summary": "Extracted focal genes with verified evidence.",
                    "genes": [
                        {
                            "mention": "crumbs",
                            "normalized_symbol": "crb",
                            "normalized_id": "FB:FBgn0000368",
                            "species": "Drosophila melanogaster",
                            "confidence": "high",
                            "evidence_record_ids": [crumbs_record["evidence_record_id"]],
                        },
                        {
                            "mention": "ninaE",
                            "normalized_symbol": "ninaE",
                            "normalized_id": "FB:FBgn0002940",
                            "species": "Drosophila melanogaster",
                            "confidence": "high",
                            "evidence_record_ids": [ninae_record["evidence_record_id"]],
                        },
                    ],
                    "items": [
                        {
                            "label": "crb",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0000368",
                            "source_mentions": ["crumbs"],
                            "evidence_record_ids": [crumbs_record["evidence_record_id"]],
                        },
                        {
                            "label": "ninaE",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0002940",
                            "source_mentions": ["ninaE"],
                            "evidence_record_ids": [ninae_record["evidence_record_id"]],
                        },
                    ],
                    "evidence_records": [],
                }
            ),
        ),
    )

    agent = SimpleNamespace(
        name="Gene Extraction Agent",
        tools=[],
        output_type=SimpleNamespace(__name__="GeneExtractionResultEnvelope"),
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Gene Extraction Agent",
        max_turns=3,
        tool_name="ask_gene_extractor_specialist",
    )

    assert json.loads(result)["summary"] == "Extracted focal genes with verified evidence."
    evidence_events = [event for event in captured_events if event.get("type") == "evidence_summary"]
    assert len(evidence_events) == 1
    assert evidence_events[0]["evidence_records"] == [crumbs_record, ninae_record]


@pytest.mark.asyncio
async def test_pdf_specialist_returns_plain_answer_from_structured_output_and_emits_evidence(monkeypatch):
    captured_events = []
    oregon_record = _build_expected_evidence_record(
        entity="Oregon R",
        chunk_id="chunk-strain-1",
        verified_quote="Oregon R flies were used as the wild-type strain.",
        page=3,
        section="Methods",
    )
    mutant_record = _build_expected_evidence_record(
        entity="crb mutant alleles",
        chunk_id="chunk-strain-2",
        verified_quote="The strains used were crb11A22, crb8F105, and crbp13A.",
        page=3,
        section="Methods",
    )

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [
                _tool_call_stream_event(
                    "record_evidence",
                    arguments='{"entity":"Oregon R","chunk_id":"chunk-strain-1","claimed_quote":"Oregon R flies were used as the wild-type strain."}',
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "verified_quote": oregon_record["verified_quote"],
                            "page": oregon_record["page"],
                            "section": oregon_record["section"],
                        }
                    )
                ),
                _tool_call_stream_event(
                    "record_evidence",
                    arguments='{"entity":"crb mutant alleles","chunk_id":"chunk-strain-2","claimed_quote":"The strains used were crb11A22, crb8F105, and crbp13A."}',
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "verified_quote": mutant_record["verified_quote"],
                            "page": mutant_record["page"],
                            "section": mutant_record["section"],
                        }
                    )
                ),
            ],
            final_output=_FakeStructuredOutput(
                {
                    "answer": (
                        "The transgenic fly strains used in the study include Oregon R, "
                        "white-eyed controls, and the crb mutant alleles crb11A22, crb8F105, "
                        "and crbp13A."
                    ),
                    "summary": "Retained 3 strain-related findings with verified evidence.",
                    "items": [
                        {
                            "label": "Oregon R",
                            "entity_type": "strain",
                            "source_mentions": ["Oregon R"],
                            "evidence_record_ids": [oregon_record["evidence_record_id"]],
                        },
                        {
                            "label": "crb mutant alleles",
                            "entity_type": "strain",
                            "source_mentions": ["crb11A22", "crb8F105", "crbp13A"],
                            "evidence_record_ids": [mutant_record["evidence_record_id"]],
                        },
                    ],
                    "raw_mentions": [
                        {"mention": "Oregon R", "entity_type": "strain", "evidence_record_ids": []},
                        {"mention": "crb11A22", "entity_type": "strain", "evidence_record_ids": []},
                    ],
                    "evidence_records": [],
                    "normalization_notes": [],
                    "exclusions": [],
                    "ambiguities": [],
                    "run_summary": {"candidate_count": 2, "kept_count": 2},
                }
            ),
        ),
    )

    agent = SimpleNamespace(
        name="General PDF Extraction Agent",
        tools=[],
        output_type=SimpleNamespace(__name__="PdfExtractionResultEnvelope"),
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="identify the transgenic fly strains",
        specialist_name="General PDF Extraction Agent",
        max_turns=3,
        tool_name="ask_pdf_extraction_specialist",
    )

    assert result == (
        "The transgenic fly strains used in the study include Oregon R, white-eyed controls, "
        "and the crb mutant alleles crb11A22, crb8F105, and crbp13A."
    )
    evidence_events = [event for event in captured_events if event.get("type") == "evidence_summary"]
    assert len(evidence_events) == 1
    assert evidence_events[0]["evidence_records"] == [oregon_record, mutant_record]


@pytest.mark.asyncio
async def test_specialist_fails_fast_when_structured_extraction_output_is_missing_evidence(monkeypatch):
    captured_events = []

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [],
            final_output=_FakeStructuredOutput(
                {
                    "summary": "Extracted focal genes but evidence was lost.",
                    "items": [
                        {
                            "label": "crumb",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0000001",
                            "source_mentions": ["crumb"],
                        }
                    ],
                    "evidence_records": [],
                    "run_summary": {"kept_count": 1},
                }
            ),
        ),
    )

    agent = SimpleNamespace(
        name="Gene Validation Agent",
        tools=[],
        output_type=SimpleNamespace(__name__="GeneExtractionResultEnvelope"),
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(streaming_tools.SpecialistOutputError):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract findings",
            specialist_name="Gene Validation Agent",
            max_turns=3,
            tool_name="ask_gene_specialist",
        )

    specialist_errors = [event for event in captured_events if event.get("type") == "SPECIALIST_ERROR"]
    assert len(specialist_errors) == 1
    assert specialist_errors[0]["details"]["reason"] == "missing_evidence_records"


@pytest.mark.asyncio
async def test_specialist_fails_fast_when_live_evidence_exists_but_item_refs_are_missing(monkeypatch):
    captured_events = []

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [
                _tool_call_stream_event(
                    "record_evidence",
                    arguments='{"entity":"crumb","chunk_id":"chunk-1","claimed_quote":"Crumb is essential for maintaining epithelial polarity."}',
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "verified_quote": "Crumb is essential for maintaining epithelial polarity.",
                            "page": 4,
                            "section": "Results",
                        }
                    )
                ),
            ],
            final_output=_FakeStructuredOutput(
                {
                    "summary": "Extractor retained a gene but lost its evidence references.",
                    "items": [
                        {
                            "label": "crumb",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0000001",
                            "source_mentions": ["crumb"],
                        }
                    ],
                    "evidence_records": [],
                    "run_summary": {"kept_count": 1},
                }
            ),
        ),
    )

    agent = SimpleNamespace(
        name="Gene Validation Agent",
        tools=[],
        output_type=SimpleNamespace(__name__="GeneExtractionResultEnvelope"),
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(streaming_tools.SpecialistOutputError):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract findings",
            specialist_name="Gene Validation Agent",
            max_turns=3,
            tool_name="ask_gene_specialist",
        )

    specialist_errors = [event for event in captured_events if event.get("type") == "SPECIALIST_ERROR"]
    assert len(specialist_errors) == 1
    assert specialist_errors[0]["details"]["reason"] == "missing_evidence_records"


@pytest.mark.asyncio
async def test_specialist_accepts_schema_defined_retained_collection_without_items(monkeypatch):
    verified_quote = "Actin 5C was the focal allele examined in the study."
    expected_record = _build_expected_evidence_record(
        entity="Actin 5C",
        chunk_id="chunk-1",
        verified_quote=verified_quote,
        page=4,
        section="Results",
    )
    captured_events = []

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [
                _tool_call_stream_event(
                    "record_evidence",
                    arguments=json.dumps(
                        {
                            "entity": "Actin 5C",
                            "chunk_id": "chunk-1",
                            "claimed_quote": verified_quote,
                        }
                    ),
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "verified_quote": verified_quote,
                            "page": 4,
                            "section": "Results",
                        }
                    )
                ),
            ],
            final_output=_FakeStructuredOutput(
                {
                    "summary": "Retained one focal allele with verified evidence.",
                    "alleles": [
                        {
                            "mention": "Actin 5C",
                            "normalized_symbol": "Act5C",
                            "normalized_id": "FB:FBal0000001",
                            "associated_gene": "Act5C",
                            "confidence": "high",
                            "evidence_record_ids": [expected_record["evidence_record_id"]],
                        }
                    ],
                    "items": [],
                    "evidence_records": [],
                    "run_summary": {"kept_count": 1},
                }
            ),
        ),
    )

    agent = SimpleNamespace(
        name="Allele/Variant Extraction Agent",
        tools=[],
        output_type=AlleleExtractionResultEnvelope,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Allele/Variant Extraction Agent",
        max_turns=3,
        tool_name="ask_allele_extractor_specialist",
    )

    assert not any(event.get("type") == "SPECIALIST_ERROR" for event in captured_events)
    assert any(event.get("type") == "evidence_summary" for event in captured_events)
    assert json.loads(result)["alleles"][0]["evidence_record_ids"] == [expected_record["evidence_record_id"]]


@pytest.mark.asyncio
async def test_specialist_matches_concurrent_record_evidence_outputs_by_identity_without_call_ids(monkeypatch):
    crumbs_quote = "Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants"
    ninae_quote = (
        "Decreased levels of Rh1 induced by mutating the ninaE gene, [20] or by removal "
        "of Vitamin A precursors [21] from the diet resulted in substantially smaller rhabdomeres"
    )
    crumbs_record = _build_expected_evidence_record(
        entity="crumbs",
        chunk_id="chunk-crumbs",
        verified_quote=crumbs_quote,
        page=1,
        section="Results",
        subsection="Changes in Molecular Organization Following Abnormal PRC Development in crumbs Mutants",
    )
    ninae_record = _build_expected_evidence_record(
        entity="ninaE",
        chunk_id="chunk-ninae",
        verified_quote=ninae_quote,
        page=3,
        section="Results",
        subsection="The Molar Abundance of Actins, Opsin, and Crumbs in Fly Eyes",
    )
    captured_events = []

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            [
                _tool_call_stream_event(
                    "record_evidence",
                    arguments=json.dumps(
                        {
                            "entity": "crumbs",
                            "chunk_id": "chunk-crumbs",
                            "claimed_quote": crumbs_quote,
                        }
                    ),
                ),
                _tool_call_stream_event(
                    "record_evidence",
                    arguments=json.dumps(
                        {
                            "entity": "ninaE",
                            "chunk_id": "chunk-ninae",
                            "claimed_quote": ninae_quote,
                        }
                    ),
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "entity": "ninaE",
                            "chunk_id": "chunk-ninae",
                            "claimed_quote": ninae_quote,
                            "verified_quote": ninae_record["verified_quote"],
                            "page": ninae_record["page"],
                            "section": ninae_record["section"],
                            "subsection": ninae_record["subsection"],
                        }
                    )
                ),
                _tool_output_stream_event(
                    json.dumps(
                        {
                            "status": "verified",
                            "entity": "crumbs",
                            "chunk_id": "chunk-crumbs",
                            "claimed_quote": crumbs_quote,
                            "verified_quote": crumbs_record["verified_quote"],
                            "page": crumbs_record["page"],
                            "section": crumbs_record["section"],
                            "subsection": crumbs_record["subsection"],
                        }
                    )
                ),
            ],
            final_output=_FakeStructuredOutput(
                {
                    "summary": "Extracted focal genes with verified evidence.",
                    "genes": [
                        {
                            "mention": "crumbs",
                            "normalized_symbol": "crb",
                            "normalized_id": "FB:FBgn0000368",
                            "species": "Drosophila melanogaster",
                            "confidence": "high",
                            "evidence_record_ids": [crumbs_record["evidence_record_id"]],
                        },
                        {
                            "mention": "ninaE",
                            "normalized_symbol": "ninaE",
                            "normalized_id": "FB:FBgn0002940",
                            "species": "Drosophila melanogaster",
                            "confidence": "high",
                            "evidence_record_ids": [ninae_record["evidence_record_id"]],
                        },
                    ],
                    "items": [
                        {
                            "label": "crb",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0000368",
                            "source_mentions": ["crumbs"],
                            "evidence_record_ids": [crumbs_record["evidence_record_id"]],
                        },
                        {
                            "label": "ninaE",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0002940",
                            "source_mentions": ["ninaE"],
                            "evidence_record_ids": [ninae_record["evidence_record_id"]],
                        },
                    ],
                    "evidence_records": [],
                    "run_summary": {"candidate_count": 2, "kept_count": 2},
                }
            ),
        ),
    )

    agent = SimpleNamespace(
        name="Gene Extraction Agent",
        tools=[],
        output_type=SimpleNamespace(__name__="GeneExtractionResultEnvelope"),
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract focal genes",
        specialist_name="Gene Extraction Agent",
        max_turns=3,
        tool_name="ask_gene_extractor_specialist",
    )

    parsed = json.loads(result)
    assert {record["evidence_record_id"] for record in parsed["evidence_records"]} == {
        crumbs_record["evidence_record_id"],
        ninae_record["evidence_record_id"],
    }


@pytest.mark.asyncio
async def test_specialist_emits_file_ready_for_fileinfo_output(monkeypatch):
    fake_events = [
        _tool_call_stream_event("save_csv_file"),
        _tool_output_stream_event(
            '{"file_id":"f1","download_url":"/api/files/f1/download","filename":"out.csv","format":"csv"}'
        ),
    ]
    captured_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(fake_events, final_output="done"),
    )

    agent = SimpleNamespace(
        name="Formatter Agent",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="format this table",
        specialist_name="Formatter Agent",
        max_turns=3,
        tool_name=None,
    )
    assert result == "done"

    file_ready = [e for e in captured_events if e.get("type") == "FILE_READY"]
    assert len(file_ready) == 1
    assert file_ready[0]["details"]["filename"] == "out.csv"


@pytest.mark.asyncio
async def test_specialist_appends_batching_nudge_at_threshold(monkeypatch):
    monkeypatch.setattr(streaming_tools, "add_specialist_event", lambda _event: None)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult([], final_output="done"),
    )
    monkeypatch.setattr(streaming_tools, "_track_specialist_call", lambda _tool_name: 3)
    monkeypatch.setattr(streaming_tools, "_generate_batching_nudge", lambda _tool_name, _count: "\nNUDGE")

    agent = SimpleNamespace(
        name="Gene Validation Agent",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Gene Validation Agent",
        max_turns=3,
        tool_name="ask_gene_specialist",
    )
    assert result == "done\nNUDGE"
