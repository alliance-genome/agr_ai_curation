"""Unit tests for builder-finalized payload handoff surfaces."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.api import chat_common
from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents import streaming_tools
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult


def _workspace() -> builder.ExtractionBuilderWorkspace:
    return builder.ExtractionBuilderWorkspace(
        run_id="trace-handoff",
        document_id="doc-1",
        domain_pack_id="pack-1",
        agent_id="gene-expression",
    )


def test_finalize_extraction_payload_returns_canonical_payload_with_evidence(monkeypatch):
    captured_events = []
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_events.append(event) or event,
    )
    evidence_record = {
        "evidence_record_id": "evidence-live",
        "entity": "crumb",
        "verified_quote": "crumb is expressed in the embryo.",
        "chunk_id": "chunk-1",
        "page": 3,
        "section": "Results",
    }

    finalization = builder.finalize_extraction_payload(
        {
            "items": [
                {
                    "label": "crumb",
                    "entity_type": "gene",
                    "source_mentions": ["crumb"],
                    "evidence_record_ids": ["evidence-live"],
                }
            ],
            "evidence_records": [],
            "run_summary": {"candidate_count": 1, "kept_count": 1},
        },
        workspace=_workspace(),
        candidate_id="candidate-1",
        evidence_records=[evidence_record],
        resolver_selection_refs=["resolver:gene:crumb"],
    )

    assert finalization.payload["evidence_records"] == [evidence_record]
    assert finalization.summary()["finalized_candidate_count"] == 1
    assert finalization.summary()["evidence_record_ids"] == ["evidence-live"]
    assert finalization.summary()["resolver_selection_count"] == 1
    assert captured_events[-1]["event_type"] == "extraction_builder.finalization_decision"


def test_internal_extraction_result_event_carries_canonical_builder_payload(monkeypatch):
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: event)
    finalization = builder.finalize_extraction_payload(
        {
            "actor": "gene_expression_specialist",
            "destination": "gene_expression",
            "items": [{"label": "notch"}],
            "run_summary": {"candidate_count": 1},
        },
        workspace=_workspace(),
        candidate_id="candidate-1",
    )

    event = builder.build_internal_extraction_result_event(
        tool_name="ask_gene_expression_specialist",
        specialist_name="Gene Expression Specialist",
        finalization=finalization,
        timestamp="2026-05-29T00:00:00+00:00",
    )

    assert event["type"] == "INTERNAL_EXTRACTION_RESULT"
    assert event["internal"]["canonical_payload"] == finalization.payload
    assert event["internal"]["builder_finalization"] == finalization.summary()
    assert event["internal"]["tool_output"].startswith("{")


def test_chat_candidate_collection_prefers_builder_canonical_payload(monkeypatch):
    monkeypatch.setattr(
        chat_common,
        "build_extraction_envelope_candidate",
        lambda raw_output, **kwargs: {
            "raw_output": raw_output,
            "agent_key": kwargs["agent_key"],
            "metadata": kwargs["metadata"],
        },
    )
    event = {
        "type": "INTERNAL_EXTRACTION_RESULT",
        "details": {"toolName": "ask_gene_expression_specialist"},
        "internal": {
            "tool_output": '{"items":[{"label":"old"}],"run_summary":{"candidate_count":1}}',
            "canonical_payload": {
                "items": [{"label": "canonical"}],
                "run_summary": {"candidate_count": 1},
            },
            "builder_finalization": {
                "builder_run_id": "trace-handoff",
                "candidate_ids": ["candidate-1"],
            },
        },
    }

    candidate = chat_common._build_extraction_candidate_from_tool_event(
        event,
        tool_agent_map={"ask_gene_expression_specialist": "gene-expression"},
        conversation_summary="extract",
    )

    assert isinstance(candidate, dict)
    assert candidate["raw_output"]["items"][0]["label"] == "canonical"
    assert candidate["agent_key"] == "gene-expression"
    assert candidate["metadata"]["builder_run_id"] == "trace-handoff"
    assert candidate["metadata"]["builder_candidate_ids"] == ["candidate-1"]


class _DomainEnvelope(DomainEnvelopeExtractionResult):
    pass


class _FakeRunResult:
    def __init__(self, *, final_output):
        self.final_output = final_output

    async def stream_events(self):
        if False:
            yield None

    def to_input_list(self):
        return [{"role": "user", "content": "extract"}]


@pytest.mark.asyncio
async def test_specialist_validation_consumes_builder_finalized_payload(monkeypatch):
    payload = {
        "summary": "Expression extraction",
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
    captured = {}
    captured_events = []

    async def _capture_validator_input(final_output, **_kwargs):
        captured["validator_payload"] = json.loads(final_output)
        return final_output

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _capture_validator_input,
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(final_output=json.dumps(payload)),
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[],
        output_type=_DomainEnvelope,
        instructions="",
        model="gpt-4o",
    )

    await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract gene expression evidence",
        specialist_name="Gene Expression Extractor",
        max_turns=3,
        tool_name="ask_gene_expression_specialist",
    )

    internal_event = next(
        event
        for event in captured_events
        if event.get("type") == "INTERNAL_EXTRACTION_RESULT"
    )
    assert captured["validator_payload"] == internal_event["internal"]["canonical_payload"]
    assert internal_event["internal"]["builder_finalization"]["status"] == "finalized"
