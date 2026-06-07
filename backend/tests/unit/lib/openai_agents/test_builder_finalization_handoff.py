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
    assert (
        event["internal"]["builder_finalization"]["builder_invocation_id"]
        == finalization.builder_invocation_id
    )
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


def _builder_finalizer_tool(
    name: str = "finalize_gene_expression_extraction",
) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _disable_package_tool_rebinding(monkeypatch) -> None:
    monkeypatch.setattr(
        streaming_tools,
        "_bind_run_state_into_tools",
        lambda runtime_agent, **_kwargs: runtime_agent,
    )


def _evidence_record_ids_from_payload(payload: dict) -> list[str]:
    evidence_record_ids: list[str] = []
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        records = metadata.get("evidence_records")
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict):
                    evidence_record_id = record.get("evidence_record_id")
                    if evidence_record_id:
                        evidence_record_ids.append(str(evidence_record_id))
    for key in ("evidence_record_ids", "evidence_records"):
        records = payload.get(key)
        if isinstance(records, list):
            for record in records:
                if isinstance(record, str):
                    evidence_record_ids.append(record)
                elif isinstance(record, dict) and record.get("evidence_record_id"):
                    evidence_record_ids.append(str(record["evidence_record_id"]))
    return list(dict.fromkeys(evidence_record_ids))


class _FakeRunResult:
    def __init__(self, *, final_output):
        self.final_output = final_output

    async def stream_events(self):
        if False:
            yield None

    def to_input_list(self):
        return [{"role": "user", "content": "extract"}]


class _PlainTextRunResult:
    final_output = "plain specialist answer"

    async def stream_events(self):
        if False:
            yield None

    def to_input_list(self):
        return [{"role": "user", "content": "plain"}]


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


class _BuilderFinalizingPayloadRunResult:
    def __init__(
        self,
        payload: dict,
        *,
        candidate_id: str = "builder-candidate-1",
        final_output: dict | None = None,
    ):
        self.payload = payload
        self.candidate_id = candidate_id
        self.final_output = json.dumps(
            final_output
            or {
                "status": "complete",
                "finalized_run_id": "model-authored-ack-only",
                "summary": "Finalizer acknowledged the extraction.",
            }
        )
        self.workspace: builder.ExtractionBuilderWorkspace | None = None

    async def stream_events(self):
        workspace = builder.get_active_extraction_builder_workspace()
        self.workspace = workspace
        workspace.upsert_candidate(
            candidate_id=self.candidate_id,
            staged_fields=self.payload,
            evidence_record_ids=_evidence_record_ids_from_payload(self.payload),
            status=builder.CANDIDATE_STATUS_VALID,
        )
        workspace.finalize(candidate_ids=[self.candidate_id])
        if False:
            yield None

    def to_input_list(self):
        return [{"role": "user", "content": "extract"}]


class _AckOnlyNoFinalizationRunResult:
    def __init__(self, final_output: dict):
        self.final_output = json.dumps(final_output)

    async def stream_events(self):
        if False:
            yield None

    def to_input_list(self):
        return [{"role": "user", "content": "extract"}]


def _record_evidence_arguments(entity: str) -> str:
    return json.dumps({"entity": entity, "span_ids": ["span-1"]})


def _record_evidence_output(record: dict) -> str:
    return json.dumps({
        "status": "verified",
        "entity": record["entity"],
        "chunk_id": record["chunk_id"],
        "verified_quote": record["verified_quote"],
        "page": record["page"],
        "section": record["section"],
        "evidence_record_id": record["evidence_record_id"],
    })


def _tool_call_stream_event(
    name: str,
    *,
    arguments: str,
    call_id: str,
):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="tool_call_item",
            name=name,
            raw_item=SimpleNamespace(arguments=arguments, call_id=call_id),
        ),
    )


def _tool_output_stream_event(output: str, *, call_id: str):
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(
            type="tool_call_output_item",
            output=output,
            raw_item=SimpleNamespace(call_id=call_id),
        ),
    )


class _BuilderFinalizingRunResultWithRecordedEvidence:
    final_output = json.dumps({"model_authored": "must not be staged"})

    def __init__(self, evidence_record: dict):
        self.evidence_record = evidence_record

    async def stream_events(self):
        call_id = "call-evidence-1"
        yield _tool_call_stream_event(
            "record_evidence",
            arguments=_record_evidence_arguments(self.evidence_record["entity"]),
            call_id=call_id,
        )
        yield _tool_output_stream_event(
            _record_evidence_output(self.evidence_record),
            call_id=call_id,
        )

        workspace = builder.get_active_extraction_builder_workspace()
        workspace.upsert_candidate(
            candidate_id="gex-candidate-1",
            staged_fields={
                "relation": {"name": "is_expressed_in"},
                "single_reference": {"reference_id": "PMID:39550471"},
                "expression_annotation_subject": {"gene_symbol": "pef-1"},
                "evidence_record_ids": [self.evidence_record["evidence_record_id"]],
            },
            pending_ref_ids=["gene-expression-annotation-pef-1"],
            evidence_record_ids=[self.evidence_record["evidence_record_id"]],
            resolver_selection_refs=["call_relation"],
            status=builder.CANDIDATE_STATUS_VALID,
        )
        workspace.finalize(candidate_ids=["gex-candidate-1"])

    def to_input_list(self):
        return [{"role": "user", "content": "extract"}]


class _FinalizedWorkspaceMissingFinalizationRunResult:
    final_output = json.dumps(
        {
            "status": "complete",
            "finalized_run_id": "trace-finalized-state",
            "summary": "Finalizer acknowledged the extraction.",
            "staged_count": 1,
            "finalized_count": 1,
        }
    )

    async def stream_events(self):
        call_id = "call-finalize-gene-1"
        yield _tool_call_stream_event(
            "finalize_gene_extraction",
            arguments=json.dumps({"candidate_ids": ["gene-candidate-1"]}),
            call_id=call_id,
        )
        workspace = builder.get_active_extraction_builder_workspace()
        workspace.upsert_candidate(
            candidate_id="gene-candidate-1",
            staged_fields={
                "curatable_objects": [
                    {
                        "object_type": "gene_mention_evidence",
                        "pending_ref_id": "gene-mention-evidence-crb-1",
                        "payload": {"mention": "crb/Crumbs"},
                        "evidence_record_ids": ["evidence-1"],
                    }
                ],
                "metadata": {
                    "evidence_records": [
                        {
                            "evidence_record_id": "evidence-1",
                            "entity": "crb/Crumbs",
                            "verified_quote": "Crb abundance was measured.",
                        }
                    ]
                },
                "run_summary": {"candidate_count": 1, "kept_count": 1},
            },
            evidence_record_ids=["evidence-1"],
            status=builder.CANDIDATE_STATUS_VALID,
        )
        workspace.finalize(candidate_ids=["gene-candidate-1"])
        workspace.finalization = None
        yield _tool_output_stream_event(
            json.dumps({
                "status": "complete",
                "finalized_run_id": workspace.run_id,
                "finalized_count": 1,
            }),
            call_id=call_id,
        )

    def to_input_list(self):
        return [{"role": "user", "content": "extract"}]


@pytest.mark.asyncio
async def test_builder_finalized_specialist_skips_model_authored_output_staging(
    monkeypatch,
):
    captured_events = []
    captured_trace_events = []
    dispatched = {}
    _disable_package_tool_rebinding(monkeypatch)

    async def _record_validator_dispatch(serialized_payload, *_args, **_kwargs):
        # A1: builder-finalized output DOES run domain validators inline (extraction ->
        # validation -> reply). Echo the payload back as the validated envelope; validation
        # makes no changes in this test.
        dispatched["called"] = True
        dispatched["is_builder_envelope"] = _kwargs.get("is_builder_envelope")
        return serialized_payload

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
        "_emit_specialist_evidence_summary_or_raise",
        lambda *args, **kwargs: pytest.fail(
            "builder-finalized output must not require model-authored evidence"
        ),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _record_validator_dispatch,
    )
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_trace_events.append(event) or event,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[_builder_finalizer_tool()],
        output_type=None,
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

    assert "Full canonical envelope is retained by the specialist runtime" in final_output
    with pytest.raises(json.JSONDecodeError):
        json.loads(final_output)
    assert "curatable_objects" not in final_output
    assert "model_authored" not in final_output

    internal_event = next(
        event
        for event in captured_events
        if event.get("type") == "INTERNAL_EXTRACTION_RESULT"
    )
    payload = internal_event["internal"]["canonical_payload"]
    assert payload["relation"]["name"] == "is_expressed_in"
    assert "model_authored" not in payload

    assert internal_event["internal"]["canonical_payload"] == payload
    assert internal_event["internal"]["builder_finalization"]["candidate_ids"] == [
        "gex-candidate-1"
    ]
    assert [
        event
        for event in captured_trace_events
        if event.get("event_type") == "extraction_builder.finalization_decision"
    ]
    finalization_state_event = next(
        event
        for event in captured_events
        if event.get("type") == "SPECIALIST_BUILDER_FINALIZATION_STATE"
    )
    assert finalization_state_event["details"]["finalizationPresent"] is True
    assert finalization_state_event["details"]["builderRunId"]
    # A1: validators run on the builder-finalized envelope in the chat turn.
    assert dispatched["called"] is True
    assert dispatched["is_builder_envelope"] is True


@pytest.mark.asyncio
async def test_builder_materializer_rejects_structured_output_schema(monkeypatch):
    _disable_package_tool_rebinding(monkeypatch)
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: pytest.fail(
            "builder-materializer output-schema guard should fail before the model runs"
        ),
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[_builder_finalizer_tool()],
        output_type=_DomainEnvelope,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(
        streaming_tools.SpecialistOutputError,
        match="also declares _DomainEnvelope structured output",
    ) as exc_info:
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract gene expression evidence",
            specialist_name="Gene Expression Extractor",
            max_turns=3,
            tool_name="ask_gene_expression_specialist",
        )

    assert exc_info.value.output_type_name == "_DomainEnvelope"
    assert exc_info.value.details == [
        {
            "reason": "builder_materializer_output_schema_forbidden",
            "output_type": "_DomainEnvelope",
        }
    ]


@pytest.mark.asyncio
async def test_builder_materializer_missing_finalization_fails_with_diagnostics(
    monkeypatch,
):
    captured_events = []
    _disable_package_tool_rebinding(monkeypatch)

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
        lambda *args, **kwargs: _FinalizedWorkspaceMissingFinalizationRunResult(),
    )

    agent = SimpleNamespace(
        name="Gene Extractor",
        tools=[_builder_finalizer_tool("finalize_gene_extraction")],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(
        streaming_tools.SpecialistOutputError,
        match="did not leave a finalized backend builder payload",
    ) as exc_info:
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract one gene",
            specialist_name="Gene Extractor",
            max_turns=3,
            tool_name="ask_gene_extractor_specialist",
        )

    assert exc_info.value.output_type_name == "builder_finalization"
    assert not [
        event
        for event in captured_events
        if event.get("type") == "INTERNAL_EXTRACTION_RESULT"
    ]
    state_event = next(
        event
        for event in captured_events
        if event.get("type") == "SPECIALIST_BUILDER_FINALIZATION_STATE"
    )
    assert state_event["details"]["workspaceState"] == "finalized"
    assert state_event["details"]["finalizationPresent"] is False
    assert state_event["details"]["candidateCount"] == 1
    assert state_event["details"]["builderRunId"]
    assert state_event["details"]["finalizerToolCalls"] == ["finalize_gene_extraction"]
    missing_event = next(
        event
        for event in captured_events
        if event.get("type") == "SPECIALIST_BUILDER_FINALIZATION_MISSING"
    )
    assert missing_event["details"]["reason"] == "builder_finalization_missing"
    assert missing_event["details"]["workspaceState"] == "finalized"


@pytest.mark.asyncio
async def test_builder_materializer_ack_json_without_finalize_fails(monkeypatch):
    captured_events = []
    _disable_package_tool_rebinding(monkeypatch)
    model_authored_payload = {
        "summary": "Expression extraction",
        "curatable_objects": [
            {
                "object_type": "gene_expression_annotation",
                "payload": {"gene_symbol": "wg"},
                "evidence_record_ids": ["evidence-record-1"],
            }
        ],
        "metadata": {
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-record-1",
                    "entity": "wg",
                    "verified_quote": "wg is expressed in embryonic stripes.",
                }
            ]
        },
    }

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
        lambda *args, **kwargs: _AckOnlyNoFinalizationRunResult(
            model_authored_payload
        ),
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[_builder_finalizer_tool()],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(
        streaming_tools.SpecialistOutputError,
        match="did not leave a finalized backend builder payload",
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
    missing_event = next(
        event
        for event in captured_events
        if event.get("type") == "SPECIALIST_BUILDER_FINALIZATION_MISSING"
    )
    assert missing_event["details"]["finalizationPresent"] is False
    assert missing_event["details"]["candidateCount"] == 0


@pytest.mark.asyncio
async def test_builder_materializer_requires_tool_name_for_internal_event(monkeypatch):
    captured_events = []
    _disable_package_tool_rebinding(monkeypatch)

    async def _echo_validator_dispatch(serialized_payload, *_args, **_kwargs):
        return serialized_payload

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
        "_dispatch_domain_envelope_validators_for_chat",
        _echo_validator_dispatch,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[_builder_finalizer_tool()],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    with pytest.raises(
        streaming_tools.SpecialistOutputError,
        match="without a supervisor tool name",
    ):
        await streaming_tools.run_specialist_with_events(
            agent=agent,
            input_text="extract gene expression evidence",
            specialist_name="Gene Expression Extractor",
            max_turns=3,
            tool_name=None,
        )

    assert not [
        event
        for event in captured_events
        if event.get("type") == "INTERNAL_EXTRACTION_RESULT"
    ]
    missing_tool_event = next(
        event
        for event in captured_events
        if event.get("type") == "SPECIALIST_BUILDER_TOOL_NAME_MISSING"
    )
    assert missing_tool_event["details"]["finalizationPresent"] is True
    assert missing_tool_event["details"]["reason"] == (
        "builder_materializer_tool_name_missing"
    )


@pytest.mark.asyncio
async def test_specialist_does_not_replay_prior_finalization_in_same_trace(
    monkeypatch,
):
    captured_events = []
    run_results = [_BuilderFinalizingRunResult(), _PlainTextRunResult()]
    _disable_package_tool_rebinding(monkeypatch)

    async def _echo_validator_dispatch(serialized_payload, *_args, **_kwargs):
        return serialized_payload

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(
        streaming_tools,
        "RunConfig",
        lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools,
        "get_current_extraction_trace_run",
        lambda: SimpleNamespace(trace_id="trace-shared"),
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: run_results.pop(0),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_dispatch_domain_envelope_validators_for_chat",
        _echo_validator_dispatch,
    )

    first_agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[_builder_finalizer_tool()],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )
    await streaming_tools.run_specialist_with_events(
        agent=first_agent,
        input_text="extract gene expression evidence",
        specialist_name="Gene Expression Extractor",
        max_turns=3,
        tool_name="ask_gene_expression_specialist",
    )
    internal_event_count_after_first = sum(
        1 for event in captured_events if event.get("type") == "INTERNAL_EXTRACTION_RESULT"
    )

    second_agent = SimpleNamespace(
        name="Plain Specialist",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )
    second_output = await streaming_tools.run_specialist_with_events(
        agent=second_agent,
        input_text="answer normally",
        specialist_name="Plain Specialist",
        max_turns=3,
        tool_name="ask_plain_specialist",
    )

    assert second_output == "plain specialist answer"
    assert (
        sum(
            1
            for event in captured_events
            if event.get("type") == "INTERNAL_EXTRACTION_RESULT"
        )
        == internal_event_count_after_first
    )


@pytest.mark.asyncio
async def test_builder_finalized_specialist_emits_evidence_summary_from_recorded_evidence(
    monkeypatch,
):
    evidence_record = {
        "evidence_record_id": "evidence-live-1",
        "entity": "pef-1",
        "verified_quote": "pef-1 is expressed in mechanosensory neurons.",
        "page": 9,
        "section": "Results",
        "chunk_id": "chunk-expression-1",
    }
    captured_events = []
    _disable_package_tool_rebinding(monkeypatch)

    async def _echo_validator_dispatch(serialized_payload, *_args, **_kwargs):
        return serialized_payload

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
        lambda *args, **kwargs: _BuilderFinalizingRunResultWithRecordedEvidence(
            evidence_record
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
        _echo_validator_dispatch,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[_builder_finalizer_tool()],
        output_type=None,
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

    evidence_events = [
        event for event in captured_events if event.get("type") == "evidence_summary"
    ]
    assert len(evidence_events) == 1
    assert evidence_events[0]["tool_name"] == "ask_gene_expression_specialist"
    assert evidence_events[0]["evidence_records"] == [evidence_record]


@pytest.mark.asyncio
async def test_specialist_validation_consumes_builder_finalized_payload(monkeypatch):
    payload = {
        "domain_pack_id": "agr.test",
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
    _disable_package_tool_rebinding(monkeypatch)
    run_result = _BuilderFinalizingPayloadRunResult(payload)

    async def _capture_validator_input(final_output, **_kwargs):
        captured["validator_payload"] = json.loads(final_output)
        return final_output

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
        _capture_validator_input,
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: run_result,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[_builder_finalizer_tool()],
        output_type=None,
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
    assert run_result.workspace is not None
    assert run_result.workspace.finalization is not None
    assert run_result.workspace.finalization.payload == (
        internal_event["internal"]["canonical_payload"]
    )


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
    _disable_package_tool_rebinding(monkeypatch)
    run_result = _BuilderFinalizingPayloadRunResult(payload)

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
        lambda *args, **kwargs: run_result,
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
        tools=[_builder_finalizer_tool()],
        output_type=None,
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
    assert run_result.workspace is not None
    assert run_result.workspace.finalization is not None
    assert run_result.workspace.finalization.payload == canonical_payload
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
    _disable_package_tool_rebinding(monkeypatch)

    async def _echo_validator_dispatch(final_output, **_kwargs):
        return final_output

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
        _echo_validator_dispatch,
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _BuilderFinalizingPayloadRunResult(payload),
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
        domain_pack_id="agr.test",
        agent_id="supervisor",
    )
    token = builder.set_active_extraction_builder_workspace(parent_workspace)
    try:
        agent = SimpleNamespace(
            name="Gene Expression Extractor",
            tools=[_builder_finalizer_tool()],
            output_type=None,
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
    _disable_package_tool_rebinding(monkeypatch)
    run_result = _BuilderFinalizingPayloadRunResult(payload)

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
        lambda *args, **kwargs: run_result,
    )
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: captured_trace_events.append(event) or event,
    )

    agent = SimpleNamespace(
        name="Gene Expression Extractor",
        tools=[_builder_finalizer_tool()],
        output_type=None,
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
    assert [
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
    assert run_result.workspace is not None
    candidate = run_result.workspace.get_candidate(run_result.candidate_id)
    assert candidate.status == builder.CANDIDATE_STATUS_FINALIZED
    assert candidate.validation_errors == [
        {
            "reason": "domain_validator_dispatch_failed",
            "message": "Validator agent execution failed: resolver unavailable",
            "request_id": "request-1",
            "validator_binding_id": "binding-1",
            "provider": "domain_validator_dispatch",
            "method": "validator_agent_error",
            "specialist_name": "Gene Expression Extractor",
            "tool_name": "ask_gene_expression_specialist",
        }
    ]


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
