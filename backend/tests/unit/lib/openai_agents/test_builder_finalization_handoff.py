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


def test_finalize_extraction_payload_backfills_scope_metadata_from_payload(monkeypatch):
    captured_events = []
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_events.append(event) or event,
    )
    workspace = builder.ExtractionBuilderWorkspace(
        run_id="trace-domain",
        agent_id="agent",
    )

    finalization = builder.finalize_extraction_payload(
        {
            "domain_pack_id": "agr.test",
            "objects": [
                {
                    "object_type": "gene_expression_annotation",
                    "pending_ref_id": "annotation-1",
                    "payload": {"gene_symbol": "wg"},
                    "evidence_record_ids": ["evidence-record-1"],
                }
            ],
            "run_summary": {"candidate_count": 1, "kept_count": 1},
        },
        workspace=workspace,
        candidate_id="candidate-1",
        evidence_records=[
            {
                "evidence_record_id": "evidence-record-1",
                "document_id": "doc-1",
                "entity": "wg",
                "verified_quote": "wg is expressed in embryonic stripes.",
            }
        ],
    )

    assert finalization.status == "finalized"
    assert workspace.document_id == "doc-1"
    assert workspace.domain_pack_id == "agr.test"
    finalization_event = captured_events[-1]
    assert finalization_event["event_type"] == "extraction_builder.finalization_decision"
    assert finalization_event["domain_pack_id"] == "agr.test"
    assert finalization_event["metadata"]["document_id"] == "doc-1"


def test_finalize_extraction_payload_duplicate_candidate_is_idempotent(monkeypatch):
    captured_events = []
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_events.append(event) or event,
    )
    workspace = _workspace()

    first = builder.finalize_extraction_payload(
        {"items": [{"label": "crumb"}], "run_summary": {"candidate_count": 1}},
        workspace=workspace,
        candidate_id="candidate-1",
    )
    duplicate = builder.finalize_extraction_payload(
        {"items": [{"label": "crumb"}], "run_summary": {"candidate_count": 1}},
        workspace=workspace,
        candidate_id="candidate-1",
    )

    assert duplicate is first
    assert duplicate.payload == first.payload
    assert duplicate.payload["items"][0]["label"] == "crumb"
    decisions = [
        event["output_summary"]["decision"]
        for event in captured_events
        if event.get("event_type") == "extraction_builder.finalization_decision"
    ]
    assert decisions == ["finalized", "duplicate_idempotent"]


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


class _BuilderFinalizingRunResult:
    final_output = json.dumps({"model_authored": "must not be staged"})

    async def stream_events(self):
        workspace = builder.get_active_extraction_builder_workspace()
        workspace.upsert_candidate(
            candidate_id="gex-candidate-1",
            staged_fields={
                "relation": {"name": "is_expressed_in"},
                "single_reference": {"reference_id": "PMID:39550471"},
                "expression_annotation_subject": {"gene_symbol": "pef-1"},
            },
            pending_ref_ids=["gene-expression-annotation-pef-1"],
            evidence_record_ids=["evidence-67598e5688f123c8"],
            resolver_selection_refs=["call_relation"],
            status=builder.CANDIDATE_STATUS_VALID,
        )
        workspace.finalize(candidate_ids=["gex-candidate-1"])
        if False:
            yield None

    def to_input_list(self):
        return [{"role": "user", "content": "extract"}]


@pytest.mark.asyncio
async def test_builder_finalized_specialist_skips_model_authored_output_staging(
    monkeypatch,
):
    captured_events = []
    captured_trace_events = []

    async def _unexpected_validator_dispatch(*_args, **_kwargs):
        pytest.fail("builder-finalized output must not run domain validators")

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(
        streaming_tools,
        "RunConfig",
        lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _BuilderFinalizingRunResult(),
    )
    monkeypatch.setattr(
        streaming_tools,
        "stage_extraction_payload",
        lambda *args, **kwargs: pytest.fail(
            "builder-finalized output must not stage model-authored JSON"
        ),
    )
    monkeypatch.setattr(
        streaming_tools,
        "finalize_extraction_payload",
        lambda *args, **kwargs: pytest.fail(
            "builder-finalized output must not finalize model-authored JSON"
        ),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_emit_specialist_evidence_summary_or_raise",
        lambda *args, **kwargs: pytest.fail(
            "builder-finalized output must not require model-authored evidence"
        ),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _unexpected_validator_dispatch,
    )
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_trace_events.append(event) or event,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[],
        output_type=_DomainEnvelope,
        instructions="",
        model="gpt-4o",
    )

    final_output = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract gene expression evidence",
        specialist_name="Gene Expression Extractor",
        max_turns=3,
        tool_name="ask_gene_expression_specialist",
    )

    payload = json.loads(final_output)
    assert payload["relation"]["name"] == "is_expressed_in"
    assert "model_authored" not in payload

    internal_event = next(
        event
        for event in captured_events
        if event.get("type") == "INTERNAL_EXTRACTION_RESULT"
    )
    assert internal_event["internal"]["canonical_payload"] == payload
    assert internal_event["internal"]["builder_finalization"]["candidate_ids"] == [
        "gex-candidate-1"
    ]
    assert [
        event
        for event in captured_trace_events
        if event.get("event_type") == "extraction_builder.finalization_decision"
    ]


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


@pytest.mark.asyncio
async def test_specialist_internal_event_uses_post_validator_builder_payload(monkeypatch):
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
    captured_events = []

    async def _append_validator_materialization(final_output, **_kwargs):
        dispatched_payload = json.loads(final_output)
        dispatched_payload["curatable_objects"][0]["payload"]["validator_marker"] = (
            "post-dispatch"
        )
        dispatched_payload["metadata"]["validator_appended_findings"] = [
            {"code": "domain_pack.validator_resolved", "message": "Resolved wg"}
        ]
        return json.dumps(dispatched_payload)

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(
        streaming_tools,
        "RunConfig",
        lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _append_validator_materialization,
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(final_output=json.dumps(payload)),
    )
    monkeypatch.setattr(
        chat_common,
        "build_extraction_envelope_candidate",
        lambda raw_output, **kwargs: {
            "raw_output": raw_output,
            "agent_key": kwargs["agent_key"],
            "metadata": kwargs["metadata"],
        },
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
    canonical_payload = internal_event["internal"]["canonical_payload"]
    assert canonical_payload["curatable_objects"][0]["payload"][
        "validator_marker"
    ] == "post-dispatch"
    assert canonical_payload["metadata"]["validator_appended_findings"] == [
        {"code": "domain_pack.validator_resolved", "message": "Resolved wg"}
    ]

    candidate = chat_common._build_extraction_candidate_from_tool_event(
        internal_event,
        tool_agent_map={"ask_gene_expression_specialist": "gene-expression"},
        conversation_summary="extract",
    )

    assert isinstance(candidate, dict)
    assert candidate["raw_output"]["curatable_objects"][0]["payload"][
        "validator_marker"
    ] == "post-dispatch"
    assert candidate["raw_output"]["metadata"]["validator_appended_findings"] == [
        {"code": "domain_pack.validator_resolved", "message": "Resolved wg"}
    ]


@pytest.mark.asyncio
async def test_specialist_builder_events_track_document_and_domain(monkeypatch):
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
                    "document_id": "doc-1",
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
    captured_trace_events = []

    async def _materialize_domain_envelope(final_output, **_kwargs):
        dispatched_payload = json.loads(final_output)
        dispatched_payload["domain_pack_id"] = "agr.test"
        return json.dumps(dispatched_payload)

    monkeypatch.setattr(streaming_tools, "add_specialist_event", lambda _event: None)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(
        streaming_tools,
        "RunConfig",
        lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _materialize_domain_envelope,
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(final_output=json.dumps(payload)),
    )
    monkeypatch.setattr(
        streaming_tools,
        "get_current_extraction_trace_run",
        lambda: SimpleNamespace(trace_id="trace-domain"),
    )
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_trace_events.append(event) or event,
    )

    parent_workspace = builder.ExtractionBuilderWorkspace(
        run_id="trace-domain",
        document_id="doc-1",
        agent_id="supervisor",
    )
    token = builder.set_active_extraction_builder_workspace(parent_workspace)
    try:
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
    finally:
        builder.reset_active_extraction_builder_workspace(token)

    finalization_event = next(
        event
        for event in captured_trace_events
        if event.get("event_type") == "extraction_builder.finalization_decision"
        and event.get("output_summary", {}).get("decision") == "finalized"
    )
    assert finalization_event["domain_pack_id"] == "agr.test"
    assert finalization_event["metadata"]["document_id"] == "doc-1"


@pytest.mark.asyncio
async def test_specialist_validator_dispatch_failure_records_builder_validation_failure(
    monkeypatch,
):
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
    captured_events = []
    captured_trace_events = []

    async def _fail_validator_dispatch(_final_output, **_kwargs):
        raise streaming_tools.SpecialistOutputError(
            specialist_name="Gene Expression Extractor",
            output_type_name="DomainEnvelopeExtractionResult",
            message="Validator agent execution failed: resolver unavailable",
            details=[
                {
                    "reason": "domain_validator_dispatch_failed",
                    "message": "Validator agent execution failed: resolver unavailable",
                    "request_id": "request-1",
                    "validator_binding_id": "binding-1",
                    "provider": "domain_validator_dispatch",
                    "method": "validator_agent_error",
                }
            ],
        )

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(
        streaming_tools,
        "RunConfig",
        lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _fail_validator_dispatch,
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(final_output=json.dumps(payload)),
    )
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_trace_events.append(event) or event,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[],
        output_type=_DomainEnvelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(
        streaming_tools.SpecialistOutputError,
        match="resolver unavailable",
    ):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract gene expression evidence",
            specialist_name="Gene Expression Extractor",
            max_turns=3,
            tool_name="ask_gene_expression_specialist",
        )

    assert not [
        event
        for event in captured_events
        if event.get("type") == "INTERNAL_EXTRACTION_RESULT"
    ]
    assert not [
        event
        for event in captured_trace_events
        if event.get("event_type") == "extraction_builder.finalization_decision"
    ]
    validation_event = next(
        event
        for event in captured_trace_events
        if event.get("event_type") == "extraction_builder.validation_failure"
    )
    errors = validation_event["validation"]["errors"]
    assert errors[0]["reason"] == "domain_validator_dispatch_failed"
    assert errors[0]["request_id"] == "request-1"
    assert errors[0]["validator_binding_id"] == "binding-1"


@pytest.mark.asyncio
async def test_specialist_retry_failure_records_builder_validation_failure(
    monkeypatch,
):
    captured_trace_events = []
    run_results = [
        _FakeRunResult(final_output=None),
        _FakeRunResult(final_output=None),
    ]

    monkeypatch.setattr(streaming_tools, "add_specialist_event", lambda _event: None)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(
        streaming_tools,
        "RunConfig",
        lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools,
        "Agent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: run_results.pop(0),
    )
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_trace_events.append(event) or event,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[],
        output_type=_DomainEnvelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(
        streaming_tools.SpecialistOutputError,
        match="failed to produce .* output after retry",
    ):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract gene expression evidence",
            specialist_name="Gene Expression Extractor",
            max_turns=3,
            tool_name="ask_gene_expression_specialist",
        )

    validation_event = next(
        event
        for event in captured_trace_events
        if event.get("event_type") == "extraction_builder.validation_failure"
    )
    errors = validation_event["validation"]["errors"]
    assert errors[0]["reason"] == "missing_structured_output_after_retry"
    assert errors[0]["retry_events"] == 0
    assert errors[0]["specialist_name"] == "Gene Expression Extractor"
