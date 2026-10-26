"""Shared review judgments through real runtime producers and current CSV output."""

from unittest.mock import AsyncMock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agents import Agent, ModelSettings, RunConfig
from agents.tracing import get_trace_provider
from sqlalchemy import select

from src.lib.validation_history import store
from src.lib.validation_review import reviewer
from src.lib.validation_review.decisions import ReviewContext
from src.lib.observability.cost_context import attach_agent_cost_identity
from src.models.sql.validation_history import ValidationFact
from src.lib.curation_workspace.models import DomainEnvelopeModel
from tests.integration.persistence.test_chat_review_adapter import (
    planned as planned, scope as scope, migrated as migrated, checkpoint_provider,
)
from tests.validation_review_runtime_support import CanonicalReviewer, CanonicalValidator


@pytest.fixture
def canonical_models(monkeypatch, tmp_path):
    from src.lib.agent_studio.catalog_service import get_agent_by_id as real_get_agent_by_id
    provider = get_trace_provider()
    monkeypatch.setattr(provider, "_manual_disabled", True)
    monkeypatch.setattr(provider, "_disabled", True)
    monkeypatch.setenv("AGENT_MODEL_CAPACITY_OVERRIDES_JSON",
        '{"gpt-test":{"context_window_tokens":1050000,"max_input_tokens":922000,"max_output_tokens":128000}}')
    monkeypatch.setattr("src.lib.openai_agents.context_capacity.NativeCapacityRequest.count",
                        AsyncMock(return_value=1000))
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    from src.lib.openai_agents.config import build_default_model_retry
    monkeypatch.setenv("OPENAI_MODEL_MAX_RETRIES", "3")
    validators = []
    flags = SimpleNamespace(unresolved_b=False)

    def configured_validator(*args, **kwargs):
        model = CanonicalValidator()
        model.unresolved_b = flags.unresolved_b
        validators.append(model)
        agent = Agent(name="Fixture validator", model=model, instructions="Resolve only supplied values.",
                      model_settings=ModelSettings(retry=build_default_model_retry()))
        attach_agent_cost_identity(agent, {"agent_id": "fixture_validator", "agent_role": "validator"})
        return agent

    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_by_id", configured_validator)
    model = CanonicalReviewer()
    supervisor = Agent(name="Supervisor", model=model, instructions="Review saved answers.")
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    from src.lib.openai_agents import extraction_trace_events as trace_events
    trace_id = "canonical-" + uuid4().hex
    trace_dir = tmp_path / "trace-events"
    monkeypatch.setenv("EXTRACTION_TRACE_EVENT_DIR", str(trace_dir))
    monkeypatch.setattr(trace_events, "_mirror_to_langfuse", lambda event: None)
    trace_events.start_extraction_trace_run(trace_id=trace_id)
    try:
        yield SimpleNamespace(model=model, supervisor=supervisor, validators=validators,
                              real_get_agent_by_id=real_get_agent_by_id,
                              trace_id=trace_id, trace_dir=trace_dir, flags=flags)
    finally:
        trace_events.clear_extraction_trace_run()


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
@pytest.mark.parametrize("runtime_kind", ["phase", "chat_adapter"])
async def test_canonical_grouped_review_and_reconsideration_use_real_sdk_tools(
    planned, monkeypatch, runtime_kind, canonical_models
):
    validators = canonical_models.validators
    model = canonical_models.model
    supervisor = canonical_models.supervisor
    item = planned
    if runtime_kind == "phase":
        checkpoint = checkpoint_provider(item)
        await item.adapter.initial()
        assert len(validators) == 3
        context = ReviewContext(item.scope, item.ep, "supervisor.validation_review")
        agent = reviewer.create_supervisor_phase(context, supervisor=supervisor,
            checkpoint_provider=checkpoint, authenticated_groups=[],
            execute_work=item.adapter.execute,
            advance_work=lambda: item.adapter.advance(checkpoint),
            prepare_inputs=lambda: item.adapter.prepare_inputs(checkpoint),
            refresh_inputs=item.adapter.refresh_inputs)
        result = await reviewer.run(context, agent, input="Review the three saved records",
                                    run_config=RunConfig(tracing_disabled=True))
    else:
        from src.lib.validation_history import runtime
        from src.lib.validation_review.chat import ChatReview
        from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
        from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext
        envelope = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=item.pack.pack_id,
            extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=label,
                payload={"evidence_code_curies": [f"eco:{label}"]}) for label in "ABC"])
        envelope = runtime.capture_extraction(scope=item.scope, producer_id=uuid4().hex,
            typed=envelope.model_dump(mode="json"), envelope=envelope, provenance={})
        chat = ChatReview(item.scope.owner_id, item.scope.document_id, item.scope.session_id,
                          uuid4().hex, supervisor, (), "Review the three saved records")
        await chat.receive(envelope, item.pack, agent_key="fixture", adapter_key="fixture",
            tool_name="ask_fixture", execution_receipt=None,
            context=ValidatorRuntimeContext(user_id=item.scope.owner_id,
                document_id=item.scope.document_id, authenticated_groups=()))
        records = chat.records()
        assert len(records) == 1
        original_saved_payload = records[0].payload_json
        from src.lib.curation_workspace.adapter_registry import CurationAdapterRegistry
        registry = CurationAdapterRegistry()
        registry.register_adapter(adapter_key="fixture", candidate_normalizer=object(), domain_pack=item.pack)
        monkeypatch.setattr(
            "src.lib.curation_workspace.domain_envelope_normalization.load_curation_adapter_registry",
            lambda: registry)
        from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
        monkeypatch.setattr("src.lib.flows.validation_attachments.domain_pack_validation_registries",
            lambda: {item.pack.pack_id: DomainPackValidationRegistry.from_domain_pack(item.pack)})
        from src.lib.flows.output_projection import (
            build_extraction_result_artifact_bundle, finalize_output_projection,
            FlowOutputProjectionPlan, FlowOutputColumnSpec,
        )
        export_plan = FlowOutputProjectionPlan(format="csv", row_source="object", columns=[
            FlowOutputColumnSpec(key="validated", field_ref="object.pack.Annotation.evidence_code_curies")])
        pending_bundle = build_extraction_result_artifact_bundle(
            user_id=item.scope.owner_id, extraction_results=records,
            document_id=item.scope.document_id, output_format="csv")
        assert len(pending_bundle.artifacts) == 1
        assert pending_bundle.artifacts[0].is_canonical_curation_data
        assert pending_bundle.rows_for_source("object") == []
        with pytest.raises(store.HistoryConflict, match="awaits explicit review"):
            finalize_output_projection(pending_bundle, export_plan)
        outcomes = await chat.resume()
        assert len(outcomes) == 1
        result = outcomes[0]
        assert chat.status()["pending_count"] == 0
        assert chat.records()[0].payload_json == original_saved_payload
        # Re-read through the real publication reader using the same original
        # saved source, rather than replacing it with the validated payload.
        accepted_bundle = build_extraction_result_artifact_bundle(
            user_id=item.scope.owner_id, extraction_results=records,
            document_id=item.scope.document_id, output_format="csv")
        accepted_projection = finalize_output_projection(accepted_bundle, export_plan)
        assert accepted_projection.rows == [
            {"validated": value} for value in ("FIRST_A", "FIRST_B", "SECOND_C")]
        assert accepted_projection.selection_manifests
        assert await chat.resume() == []
        ref = chat.reference(records[0])
        item = SimpleNamespace(scope=item.scope, aid=ref["artifact_id"], ep=ref["episode_id"],
                               plan=SimpleNamespace(envelope=envelope))
    assert result["review"]["ready"], result
    assert len(validators) == 4
    assert sum(hasattr(validator, "retry_history") for validator in validators) == 1
    with store.SessionLocal() as db:
        attempts = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid, ValidationFact.kind == "attempt")))
        assert len(attempts) == 4
        row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
        assert [obj["payload"]["evidence_code_curies"] for obj in row.envelope_json["extracted_objects"]] == [
            ["FIRST_A"], ["FIRST_B"], ["SECOND_C"]]
        completions = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid, ValidationFact.kind == "completion")))
        assert len(completions) == 4
        assert any("FIRST_C" in str(store._content(db, fact)) for fact in completions)
    if runtime_kind == "phase":
        # Parse the actual committed-fact emitter output with the shipped
        # TraceReview analyzer, not hand-authored timeline events.
        from pathlib import Path
        monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[4] / "trace_review/backend/src"))
        from analyzers.extraction_timeline import ExtractionTimelineAnalyzer
        parsed = ExtractionTimelineAnalyzer.analyze(trace_id=canonical_models.trace_id,
            raw_trace={}, observations=[], include_raw_outputs=True)
        events = parsed["validation_review"]["events"]
        assert events
        with store.SessionLocal() as db:
            for event in events:
                emitted = event["output"]["preview"]
                fact = db.scalar(select(ValidationFact).where(ValidationFact.id == emitted["fact_id"]))
                assert fact is not None
                assert emitted["artifact_id"] == fact.artifact_id
                assert emitted["sha256"] == fact.body_sha
                assert emitted["parent_id"] == fact.parent_id
            assert any(event["event_type"] == "validation_review.review_response" for event in events)
            assert any("Ambiguous C requires reconsideration" in str(event) for event in events)
            contents = [(event["event_type"], event["output"]["preview"]["content"]) for event in events]
            packets = [body for kind, body in contents if kind == "validation_review.review_packet"]
            assert len(packets) == 2
            latest = next(packet for packet in packets if len([
                fact for fact in packet["facts"] if fact["kind"] == "attempt"]) == 4)
            attempts_by_id = {fact.id: fact for fact in attempts}
            completions_by_id = {fact.id: fact for fact in completions}
            traced_attempts = {fact["id"]: fact for fact in latest["facts"] if fact["kind"] == "attempt"}
            traced_completions = {fact["id"]: fact for fact in latest["facts"] if fact["kind"] == "completion"}
            assert set(traced_attempts) == set(attempts_by_id)
            assert set(traced_completions) == set(completions_by_id)
            for fact_id, fact in traced_completions.items():
                assert fact["parent_id"] == completions_by_id[fact_id].parent_id
                assert fact["parent_id"] in traced_attempts
                assert fact["sha256"] == completions_by_id[fact_id].body_sha
            works = [body["payload"] for kind, body in contents if kind == "validation_review.review_work"]
            retry_work = [work for work in works if work["followup"] is not None]
            assert len(retry_work) == 1
            assert retry_work[0]["followup"]["parent_decision_id"] == model.parent_decision_id
            assert retry_work[0]["followup"]["instructions"] == model.retry_guidance
            assert retry_work[0]["invocation"]["attempt_ids"] == [model.final_attempt_id]
            assert attempts_by_id[model.final_attempt_id].target_id == attempts_by_id[model.initial_answers["C"]].target_id
            responses = [body for kind, body in contents if kind == "validation_review.review_response"]
            assert len(responses) == 2
            rejected = next(body for body in responses if body["response"]["decision_id"] == model.parent_decision_id)
            identity = rejected["identity"]
            if isinstance(identity, str):
                import json
                identity = json.loads(identity)
            assert {(entry["logical_attempt_id"], entry["action"]) for entry in identity["entries"]} == {
                (model.initial_answers["A"], "accept"), (model.initial_answers["B"], "accept"),
                (model.initial_answers["C"], "reject")}
            final = next(body for body in responses if body["response"]["ready"])
            publication = final["response"]["publication"]
            saved = db.scalar(select(ValidationFact).where(ValidationFact.id == publication["fact_id"]))
            assert saved.kind == "selected_projection" and saved.body_sha == publication["body_sha256"]
            manifest = store._content(db, saved)["manifest"]
            assert manifest["sha256"] == publication["manifest_sha256"]
            selected_completions = {unit["selected"] for unit in manifest["units"]}
            assert {completions_by_id[fact_id].parent_id for fact_id in selected_completions} == {
                model.initial_answers["A"], model.initial_answers["B"], model.final_attempt_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
@pytest.mark.parametrize("producer", ["chat", "direct", "flow"])
async def test_canonical_producer_runs_extractor_validator_reviewer_and_current_export(
    planned, canonical_models, monkeypatch, tmp_path, producer
):
    await _canonical_producer_scenario(planned, canonical_models, monkeypatch, tmp_path, producer)


async def _canonical_producer_scenario(
    planned, canonical_models, monkeypatch, tmp_path, producer, *, prepare_only=False, unresolved_b=False
):
    from src.lib.openai_agents import runner
    from src.lib.openai_agents.agents.supervisor_agent import _create_streaming_tool
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from tests.unit.lib.openai_agents.test_model_request_measurement import FakeResponsesWSModel, _tool_call, _final
    from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult
    from src.lib.validation_review.chat import ChatReview
    import json

    canonical_models.flags.unresolved_b = unresolved_b
    selected_values = ["FIRST_A", "eco:B (unresolved)" if unresolved_b else "FIRST_B", "SECOND_C"]

    class ChatModel(CanonicalReviewer):
        def __init__(self):
            super().__init__()
            self.outer_calls = 0

        def _next(self, kwargs):
            if any(tool.name == "record_validation_review" for tool in kwargs["tools"]):
                return super()._next(kwargs)
            self.outer_calls += 1
            if self.outer_calls == 1:
                name = "ask_fixture_specialist" if producer == "chat" else "ask_gene_specialist"
                assert name in {tool.name for tool in kwargs["tools"]}
                return _tool_call(name, "extract", '{"query":"Extract the three fixture records"}')
            if producer == "flow" and self.outer_calls == 2:
                assert "ask_csv_formatter_specialist" in {tool.name for tool in kwargs["tools"]}
                return _tool_call("ask_csv_formatter_specialist", "export", '{"query":"Export the selected fields"}')
            assert self.outer_calls == (3 if producer == "flow" else 2)
            return _final("The extraction is saved for review.")

    item = planned
    source = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=item.pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=label,
            payload={"evidence_code_curies": [f"eco:{label}"]}) for label in "ABC"])
    from agr_ai_curation_alliance.tools import weaviate_search
    from src.lib.openai_agents import document_references
    from src.lib.openai_agents.tools import record_evidence
    from src.models.sql.pdf_document import PDFDocument
    from uuid import UUID
    from datetime import datetime, timezone
    chunk = {"id": str(uuid4()), "chunk_index": 0,
        "text": "The fixture records eco:A, eco:B, and eco:C were observed.",
        "document_id": item.scope.document_id, "page_number": 1,
        "section_title": "Results", "parent_section": "Results",
        "metadata": {"page_number": 1, "section_title": "Results"}}
    with store.SessionLocal.begin() as db:
        document = db.get(PDFDocument, UUID(item.scope.document_id))
        document.status = "completed"
        document.processing_completed_at = datetime.now(timezone.utc)
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", AsyncMock(return_value=chunk))
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids",
                        AsyncMock(return_value={"previous_chunk_id": None, "next_chunk_id": None}))
    monkeypatch.setattr(document_references, "get_chunk_by_index", AsyncMock(return_value=chunk))
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", AsyncMock(return_value=chunk))
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))

    class ExtractorModel(FakeResponsesWSModel):
        def _next(self, kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _tool_call("read_chunk", "read-source", '{"chunk_index":0}')
            outputs = [entry for entry in kwargs["input"] if entry.get("type") == "function_call_output"]
            assert outputs
            if len(self.calls) == 2:
                assert "eco:A" in str(outputs[-1]["output"])
                return _tool_call("record_evidence", "record-source", json.dumps({
                    "entity": "Fixture records", "selections": [{"chunk_index": 0, "span_index": 0}]}))
            if len(self.calls) == 4:
                assert producer != "chat"
                assert "finalized" in str(outputs[-1]["output"])
                return _final("Saved three fixture records with their source evidence.")
            assert len(self.calls) == 3
            import ast
            raw = outputs[-1]["output"]
            try:
                evidence = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                evidence = ast.literal_eval(raw)
            assert evidence["status"] == "verified", evidence
            for obj in source.extracted_objects:
                obj.evidence_record_ids = [evidence["evidence_record_id"]]
            if producer != "chat":
                return _tool_call("finalize_fixture_extraction", "finalize-source",
                                  json.dumps({"payload": source.model_dump(mode="json")}))
            payload = DomainEnvelopeExtractionResult(curatable_objects=source.extracted_objects).model_dump(mode="json")
            return _final(json.dumps(payload))

    from src.lib.curation_workspace.adapter_registry import CurationAdapterRegistry
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    registry = CurationAdapterRegistry()
    registry.register_adapter(adapter_key="fixture", candidate_normalizer=object(), domain_pack=item.pack)
    monkeypatch.setattr("src.lib.curation_workspace.domain_envelope_normalization.load_curation_adapter_registry", lambda: registry)
    monkeypatch.setattr("src.lib.flows.validation_attachments.domain_pack_validation_registries",
                        lambda: {item.pack.pack_id: DomainPackValidationRegistry.from_domain_pack(item.pack)})
    extractor_model = ExtractorModel()
    from src.lib.agent_studio.catalog_service import ToolExecutionContext, _resolve_package_tool
    document_context = ToolExecutionContext(document_id=item.scope.document_id, user_id=item.scope.owner_id)
    extractor = Agent(name="Fixture extractor", model=extractor_model,
        instructions="Read the supplied fixture source and record evidence before extracting.",
        tools=[_resolve_package_tool(name, document_context) for name in ("read_chunk", "record_evidence")],
        output_type=DomainEnvelopeExtractionResult)
    if producer != "chat":
        # A fixture domain adapter uses the real builder workspace and finalization
        # boundary. Domain-specific scientific materialization is tested separately.
        from agents import function_tool
        from typing import Any
        from src.lib.packages import tool_roles
        from src.lib.openai_agents import extraction_builder_workspace as builder
        from src.lib.openai_agents.tools.evidence_workspace import get_active_evidence_records_snapshot
        @function_tool(strict_mode=False)
        def finalize_fixture_extraction(payload: dict[str, Any]) -> dict:
            envelope = DomainEnvelope.model_validate(payload)
            workspace = builder.get_active_extraction_builder_workspace()
            builder.stage_extraction_payload(envelope.model_dump(mode="json"), workspace=workspace,
                candidate_id="fixture-materialized", evidence_records=get_active_evidence_records_snapshot())
            return workspace.finalize(candidate_ids=["fixture-materialized"]).summary()
        configured_finalizers = tool_roles.builder_finalization_tool_names()
        monkeypatch.setattr(tool_roles, "builder_finalization_tool_names",
                            lambda: configured_finalizers | {"finalize_fixture_extraction"})
        extractor.tools.append(finalize_fixture_extraction)
        extractor.output_type = None
    extractor.agent_key = "fixture_extractor"
    extractor.curation_metadata = {"launchable": True, "adapter_key": "fixture"}
    attach_agent_cost_identity(extractor, {"agent_id": "fixture_extractor", "agent_role": "extractor"})
    tool = _create_streaming_tool(extractor, "ask_fixture_specialist", "Extract fixture records",
                                 "Fixture extractor", propagate_errors=True)
    model = ChatModel()
    model.unresolved_b = unresolved_b
    supervisor = Agent(name="Supervisor", model=model, instructions="Extract then review.", tools=[tool])
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("src.lib.file_outputs.storage.get_file_output_storage_path", lambda: tmp_path)
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)
    monkeypatch.setattr(reviewer, "create_direct_supervisor", lambda **kwargs: supervisor)
    from src.lib.context import agent_request_scope
    run_id = uuid4().hex
    with agent_request_scope(user_id=item.scope.owner_id,
                             session_id=item.scope.session_id, trace_id=uuid4().hex):
        if producer != "flow":
            events = [event async for event in runner.run_agent_streamed(
                context_messages=[{"role": "user", "content": "Extract the three fixture records"}],
                user_id=item.scope.owner_id, session_id=item.scope.session_id, turn_id=run_id,
                document_id=item.scope.document_id, active_groups=[],
                agent=supervisor if producer == "chat" else extractor,
                chat_route_mode="automatic" if producer == "chat" else "agent", inline_chat_persistence=True,
                propagate_runtime_exceptions=True,
            )]
        else:
            from src.lib.flows import executor
            from tests.unit.lib.flows.test_executor import _make_output_attachment_flow, _agent_node
            from tests.unit.lib.prompts.test_assembly import _prompt
            monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {
                "supervisor:system:base": _prompt("supervisor", "system", "Review the saved fixture answers.")})
            original_metadata = executor.get_agent_metadata
            monkeypatch.setattr(executor, "get_agent_metadata", lambda agent_id, **kwargs: {
                "agent_id": "gene", "display_name": "Fixture extractor", "description": "Extract fixture records",
                "category": "Extraction", "requires_document": True, "required_params": [],
                "output_schema_key": "DomainEnvelopeExtractionResult", "is_active": True,
                "curation": {"adapter_key": "fixture", "domain_pack_id": item.pack.pack_id, "launchable": True}}
                if agent_id == "gene" else original_metadata(agent_id, **kwargs))
            node = _agent_node("extract", "gene", validation_groups=[{
                "state": "automatic", "binding_id": item.plan.matches[0].binding.binding_id}])
            from src.lib.flows.export_fields import PackagedExportSource, source_catalog
            from src.lib.openai_agents.tools.output_formatter_tools import build_output_formatter_tools
            from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output
            catalog = source_catalog(PackagedExportSource(item.pack).fields)
            monkeypatch.setenv("FLOW_SELECTED_FIELDS_DIRECT_EXPORT", "true")
            formatter_node = _agent_node("export", "csv_formatter")
            formatter_node["data"].update(export_execution_mode="direct", projection_plan={
                "format": "csv", "row_source": "object", "row_strategy": "wide_union",
                "selection_mode": "selected_fields",
                "selected_sources": [{"node_id": "extract", "schema_fingerprint": catalog["schema_fingerprint"]}],
                "columns": [{"key": "validated", "field_ref": "object.pack.Annotation.evidence_code_curies",
                             "source_node_id": "extract"}],
            })
            flow = _make_output_attachment_flow([node, formatter_node],
                source_node_id="extract", output_node_id="export")
            def configured_agent(agent_id, **kwargs):
                if agent_id == "gene":
                    return extractor
                assert agent_id == "csv_formatter"
                return Agent(name="Configured CSV formatter", model="gpt-test", tools=build_output_formatter_tools(
                    bundle=kwargs["formatter_bundle"], output_format="csv", formatter_agent_id=agent_id,
                    configured_plan=kwargs["formatter_projection_plan"],
                    save_projected_output=save_projected_file_output))
            monkeypatch.setattr(executor, "get_agent_by_id", configured_agent)
            create = executor.create_flow_supervisor
            def configured_flow_supervisor(*args, **kwargs):
                configured = create(*args, **kwargs)
                configured.model = model
                return configured
            monkeypatch.setattr(executor, "create_flow_supervisor", configured_flow_supervisor)
            if prepare_only:
                # Batch owns execution and its real admission/serial worker loop.
                # Return fresh provider state; never synthesize terminal events.
                return SimpleNamespace(flow=flow, configured_agent=configured_agent,
                    model=model, extractor_model=extractor_model, chunk=chunk, scope=item.scope)
            events = [event async for event in executor.execute_flow(
                flow, user_id=item.scope.owner_id, session_id=item.scope.session_id,
                document_id=item.scope.document_id, user_query="Extract the three fixture records",
                active_groups=[], flow_run_id=run_id)]
            assert len([event for event in events if event["type"] == "FLOW_FINISHED"]) == 1, events
            assert not [event for event in events if event["type"] in {"FLOW_ERROR", "RUN_ERROR"}], events
    terminals = [event for event in events if event["type"] == "RUN_FINISHED"]
    # Explicit output attachments deliver typed file events, suppressing the
    # runner's generic response (which is reserved for attachment-free flows).
    assert len(terminals) == (0 if producer == "flow" else 1), events
    if producer == "direct":
        response = terminals[0]["data"]["response"]
        assert "SECOND_C" in response
        assert "FIRST_C" not in response
    assert len(extractor_model.calls) == (3 if producer == "chat" else 4)
    assert len(canonical_models.validators) == 4
    if producer != "flow":
        context = ChatReview(item.scope.owner_id, item.scope.document_id, item.scope.session_id,
                            uuid4().hex, supervisor, (), "Inspect")
        records = context.records()
        assert context.status()["pending_count"] == 0
        assert "FIRST_C" not in context.selected_presentation()
        assert "SECOND_C" in context.selected_presentation()
    else:
        from src.lib.curation_workspace.models import CurationExtractionResultRecord
        from src.lib.curation_workspace.extraction_results import _record_to_schema
        with store.SessionLocal() as db:
            records = [_record_to_schema(row) for row in db.scalars(select(CurationExtractionResultRecord).where(
                CurationExtractionResultRecord.flow_run_id == run_id))]
            from src.lib.flows.evidence_export import (
                FlowEvidenceExportFormat, FlowRunEvidenceExportPermissionError,
                build_flow_evidence_export_artifact, resolve_authorized_flow_run_extraction_results,
            )
            authorized = resolve_authorized_flow_run_extraction_results(
                db=db, flow_run_id=run_id, user_id=item.scope.owner_id)
            with pytest.raises(FlowRunEvidenceExportPermissionError):
                resolve_authorized_flow_run_extraction_results(
                    db=db, flow_run_id=run_id, user_id="another-owner")
        import csv
        import io
        for export_format in FlowEvidenceExportFormat:
            artifact = build_flow_evidence_export_artifact(
                flow_run_id=run_id, extraction_results=authorized, export_format=export_format)
            assert artifact.record_count > 0
            if export_format is FlowEvidenceExportFormat.JSON:
                exported = json.loads(artifact.payload_text)
                assert exported["flow_name"] == flow.name
                assert exported["total_evidence_records"] == artifact.record_count
                assert len(exported["steps"]) == 1
                step = exported["steps"][0]
                assert step["step"] == 1
                assert step["agent_id"] == records[0].agent_key
                assert step["agent_name"] == "Fixture extractor"
                assert step["tool_name"] == "ask_gene_specialist"
                rows = step["evidence_records"]
            else:
                rows = list(csv.DictReader(io.StringIO(artifact.payload_text), delimiter=export_format.delimiter))
                assert rows
                assert {row["step_number"] for row in rows} == {"1"}
                assert {row["agent_id"] for row in rows} == {records[0].agent_key}
            assert all(row["verified_quote"] == chunk["text"] for row in rows)
        assert records[0].metadata["persistence_phase"] == "inline_validated_extraction"
    assert len(records) == 1
    assert all(f"eco:{label}" in json.dumps(records[0].payload_json) for label in "ABC")
    reference = records[0].metadata["flow_review" if producer == "flow" else "chat_review"]
    with store.SessionLocal() as db:
        facts = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == reference["artifact_id"])))
        attempts = [fact for fact in facts if fact.kind == "attempt"]
        completions = [store._content(db, fact) for fact in facts if fact.kind == "completion"]
        assert len(attempts) == len(completions) == 4
        from collections import Counter
        assert sorted(Counter(fact.target_id for fact in attempts).values()) == [1, 1, 2]
        rejected = [body for body in completions if "FIRST_C" in json.dumps(body)]
        assert len(rejected) == 1
        assert "Supplied information has an ambiguity" in json.dumps(rejected[0])
        decision = next(fact for fact in facts if str(fact.id) == model.parent_decision_id)
        assert "Ambiguous C requires reconsideration" in json.dumps(store._content(db, decision))
        assert any(str(fact.id) == model.final_attempt_id for fact in attempts)
    from src.lib.flows.output_projection import (
        build_extraction_result_artifact_bundle, finalize_output_projection,
        FlowOutputProjectionPlan, FlowOutputColumnSpec,
    )
    from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output
    from src.models.sql.file_output import FileOutput
    from pathlib import Path
    import csv
    import io

    if producer == "flow":
        emitted = [event for event in events if event["type"] == "FILE_READY"]
        assert len(emitted) == 1, events
        with store.SessionLocal() as db:
            flow_file = db.get(FileOutput, UUID(emitted[0]["details"]["file_id"]))
            assert flow_file.file_metadata["selection_manifests"]
            flow_content = Path(flow_file.file_path).read_text()
        assert [row["validated"] for row in csv.DictReader(io.StringIO(flow_content))] == selected_values
        assert "FIRST_C" not in flow_content

    bundle = build_extraction_result_artifact_bundle(
        user_id=item.scope.owner_id, extraction_results=records,
        document_id=item.scope.document_id, output_format="csv")
    assert bundle.rows_for_source("object"), bundle.model_dump(mode="json")
    projection = finalize_output_projection(bundle, FlowOutputProjectionPlan(
        format="csv", row_source="object", columns=[FlowOutputColumnSpec(
            key="validated", field_ref="object.pack.Annotation.evidence_code_curies")]))
    assert projection.selection_manifests
    assert projection.rows == [{"validated": value} for value in selected_values]
    monkeypatch.setattr("src.lib.file_outputs.storage.get_file_output_storage_path", lambda: tmp_path)
    with agent_request_scope(user_id=item.scope.owner_id,
                             session_id=item.scope.session_id, trace_id=uuid4().hex):
        saved = await save_projected_file_output("csv", projection, "canonical_review", "fixture_formatter")
    with store.SessionLocal() as db:
        output = db.get(FileOutput, UUID(saved["file_id"]))
        assert output.file_metadata["selection_manifests"] == projection.selection_manifests
        content = Path(output.file_path).read_text()
    rows = list(csv.DictReader(io.StringIO(content)))
    assert len(rows) == 3
    assert [row["validated"] for row in rows] == selected_values
    assert "FIRST_C" not in content

    if unresolved_b:
        assert "FIRST_B" not in content
        from src.lib.validation_history.selections import read_manifest
        history_scope = store.HistoryScope(item.scope.owner_id, item.scope.document_id, reference["history_scope_id"])
        manifest = read_manifest(history_scope, reference["artifact_id"], episode_id=reference["episode_id"])
        unresolved = [unit for unit in manifest["units"] if unit["scientific_status"] == "unresolved"]
        assert len(unresolved) == 1 and unresolved[0]["eligible"]
        assert unresolved[0]["review"] == "accept"
        with store.SessionLocal() as db:
            completion = db.scalar(select(ValidationFact).where(ValidationFact.id == unresolved[0]["selected"]))
            result = store._content(db, completion)["result"]
            assert result["status"] == "unresolved" and result["resolved_values"] == {}


@pytest.fixture
def batch_catalog_agents(migrated):
    """Persist the catalog identities checked by the real batch access gate."""
    from src.models.sql.agent import Agent as SavedAgent
    from src.models.sql.batch import Batch
    from src.models.sql.curation_flow import CurationFlow
    created = []
    groups = set()
    resources = SimpleNamespace(groups=[], flow_id=None, batch_id=None)
    with store.SessionLocal.begin() as db:
        for key in ("gene", "csv_formatter"):
            agent = db.scalar(select(SavedAgent).where(SavedAgent.agent_key == key))
            if agent is None:
                agent = SavedAgent(agent_key=key, name=key, instructions="Fixture provider",
                    model_id="gpt-test", visibility="system", is_active=True, allowed_group_ids=[])
                db.add(agent)
                db.flush()
                created.append(agent.id)
            assert agent.visibility == "system" and agent.is_active
            groups.update(agent.allowed_group_ids)
    try:
        resources.groups = sorted(groups)
        yield resources
    finally:
        # Remove the fixture's operational rows before its temporary catalog
        # entries. Otherwise a later whole-database migration sees a saved flow
        # referring to the now-unavailable gene agent. Immutable scientific
        # history and all pre-existing rows remain untouched.
        with store.SessionLocal.begin() as db:
            for model, identity in ((Batch, resources.batch_id), (CurationFlow, resources.flow_id)):
                if identity is not None:
                    row = db.get(model, identity)
                    if row is not None:
                        db.delete(row)
            db.flush()
            for identity in created:
                db.delete(db.get(SavedAgent, identity))


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
async def test_canonical_batch_processes_two_documents_serially_without_replay(
    planned, canonical_models, monkeypatch, tmp_path, batch_catalog_agents
):
    """Real batch lease, worker, flows, review history, and persisted CSV files."""
    import asyncio
    import csv
    import io
    import json
    from pathlib import Path
    from uuid import UUID
    from collections import Counter
    from src.lib.batch import processor
    from src.lib.flows import executor
    from src.models.sql.batch import Batch, BatchDocument, BatchDocumentStatus, BatchStatus
    from src.models.sql.curation_flow import CurationFlow
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.file_output import FileOutput
    from src.lib.curation_workspace.models import CurationExtractionResultRecord
    from agr_ai_curation_alliance.tools import weaviate_search
    from src.lib.openai_agents import document_references
    from src.lib.openai_agents.tools import record_evidence

    original_create = executor.create_flow_supervisor
    document_ids = [planned.scope.document_id, str(uuid4())]
    with store.SessionLocal.begin() as db:
        first = db.get(PDFDocument, UUID(document_ids[0]))
        owner_id = first.user_id
        second_id = UUID(document_ids[1])
        db.add(PDFDocument(id=second_id, user_id=owner_id, filename="batch-second.pdf",
            file_path=f"{second_id}/batch-second.pdf", file_hash=second_id.hex * 2,
            file_size=1024, page_count=1, status="completed"))

    scenarios = {}
    for document_id in document_ids:
        item = SimpleNamespace(pack=planned.pack, plan=planned.plan,
            scope=store.HistoryScope(planned.scope.owner_id, document_id, uuid4().hex))
        scenarios[document_id] = await _canonical_producer_scenario(
            item, canonical_models, monkeypatch, tmp_path, "flow", prepare_only=True)

    async def chunk_by_index(*, chunk_index, user_id, document_id):
        assert chunk_index == 0 and user_id == planned.scope.owner_id
        return scenarios[document_id].chunk

    async def chunk_by_id(chunk_id, user_id, *, document_id=None):
        assert user_id == planned.scope.owner_id
        chunk = next(state.chunk for state in scenarios.values() if state.chunk["id"] == chunk_id)
        assert document_id in (None, chunk["document_id"])
        return chunk

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", chunk_by_index)
    monkeypatch.setattr(document_references, "get_chunk_by_index", chunk_by_index)
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", chunk_by_id)
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", chunk_by_id)

    def configured_agent(agent_id, **kwargs):
        return scenarios[kwargs["document_id"]].configured_agent(agent_id, **kwargs)

    def configured_supervisor(*args, **kwargs):
        agent = original_create(*args, **kwargs)
        agent.model = scenarios[kwargs["document_id"]].model
        return agent

    monkeypatch.setattr(executor, "get_agent_by_id", configured_agent)
    monkeypatch.setattr(executor, "create_flow_supervisor", configured_supervisor)
    events = []
    monkeypatch.setattr(processor, "get_batch_broadcaster", lambda: SimpleNamespace(
        publish_sync=lambda batch_id, event: events.append(event)))
    with store.SessionLocal.begin() as db:
        flow = CurationFlow(user_id=owner_id, name="Canonical two-document review",
            flow_definition=scenarios[document_ids[0]].flow.flow_definition)
        db.add(flow)
        db.flush()
        batch_catalog_agents.flow_id = flow.id
        batch = Batch(user_id=owner_id, flow_id=flow.id, total_documents=2,
            status=BatchStatus.PENDING, active_group_ids=batch_catalog_agents.groups)
        db.add(batch)
        db.flush()
        batch_id = batch.id
        batch_catalog_agents.batch_id = batch_id
        for position, document_id in enumerate(document_ids):
            db.add(BatchDocument(batch_id=batch_id, document_id=UUID(document_id),
                position=position, status=BatchDocumentStatus.PENDING))

    await asyncio.to_thread(processor.process_batch_task, batch_id)
    with store.SessionLocal() as db:
        batch = db.get(Batch, batch_id)
        assert batch.status == BatchStatus.COMPLETED
        assert (batch.completed_documents, batch.failed_documents) == (2, 0), [
            (str(doc.document_id), doc.status, doc.error_message) for doc in batch.documents]
        assert (batch.review_pending_documents, batch.review_exhausted_documents,
                batch.interrupted_documents) == (0, 0, 0)
        assert [str(doc.document_id) for doc in batch.documents] == document_ids
        file_ids = []
        for doc in batch.documents:
            assert doc.status == BatchDocumentStatus.COMPLETED
            assert doc.output_status == "complete"
            assert len(doc.result_files) == 1
            file_ids.append(doc.result_files[0]["file_id"])
            output = db.get(FileOutput, UUID(file_ids[-1]))
            assert output.file_metadata["selection_manifests"]
            content = Path(output.file_path).read_text()
            assert [row["validated"] for row in csv.DictReader(io.StringIO(content))] == [
                "FIRST_A", "FIRST_B", "SECOND_C"]
            assert "FIRST_C" not in content
        assert len(set(file_ids)) == 2
        records = list(db.scalars(select(CurationExtractionResultRecord).where(
            CurationExtractionResultRecord.flow_run_id == str(batch_id))))
        assert len(records) == 2
        from src.lib.flows.evidence_export import (
            FlowEvidenceExportFormat, build_flow_evidence_export_artifact,
            resolve_authorized_flow_run_extraction_results,
        )
        authorized = resolve_authorized_flow_run_extraction_results(
            db=db, flow_run_id=str(batch_id), user_id=planned.scope.owner_id)
        assert {str(record.document_id) for record in authorized} == set(document_ids)
        exported = json.loads(build_flow_evidence_export_artifact(
            flow_run_id=str(batch_id), extraction_results=authorized,
            export_format=FlowEvidenceExportFormat.JSON).payload_text)
        assert exported['flow_name'] == 'Canonical two-document review'
        assert len(exported['steps']) == 2
        assert all(step['step'] == 1 and step['evidence_count'] > 0 for step in exported['steps'])
        for record in records:
            assert all(f"eco:{label}" in json.dumps(record.payload_json) for label in "ABC")
            reference = record.extraction_metadata["flow_review"]
            facts = list(db.scalars(select(ValidationFact).where(
                ValidationFact.artifact_id == reference["artifact_id"])))
            attempts = [fact for fact in facts if fact.kind == "attempt"]
            completions = [store._content(db, fact) for fact in facts if fact.kind == "completion"]
            assert len(attempts) == len(completions) == 4
            assert sorted(Counter(fact.target_id for fact in attempts).values()) == [1, 1, 2]
            assert sum("FIRST_C" in json.dumps(body) for body in completions) == 1
            model = scenarios[str(record.document_id)].model
            rejected = next(fact for fact in facts if str(fact.id) == model.parent_decision_id)
            assert "Ambiguous C requires reconsideration" in json.dumps(store._content(db, rejected))
            assert any(str(fact.id) == model.final_attempt_id for fact in attempts)

    assert [(event["type"], event["document_id"]) for event in events
            if event["type"] in {"FLOW_STARTED", "FLOW_FINISHED"}] == [
        (kind, doc) for doc in document_ids for kind in ("FLOW_STARTED", "FLOW_FINISHED")]
    assert len(canonical_models.validators) == 8
    assert [len(state.extractor_model.calls) for state in scenarios.values()] == [4, 4]
    before_events = len(events)
    await asyncio.to_thread(processor.process_batch_task, batch_id)
    assert len(events) == before_events
    assert len(canonical_models.validators) == 8
    assert [len(state.extractor_model.calls) for state in scenarios.values()] == [4, 4]


@pytest.mark.asyncio
async def test_canonical_saved_profile_uses_pinned_agent_and_real_generic_builder(
    scope, migrated, canonical_models, monkeypatch, tmp_path
):
    import ast
    import csv
    import io
    import json
    from pathlib import Path
    from uuid import UUID
    from datetime import datetime, timezone
    from src.lib.agent_studio import generic_profile_service
    from src.lib.agent_studio.execution_revision_service import append_execution_revision, current_execution_receipt
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.curation_workspace.execution_contracts import resolve_receipt_profile
    from src.models.sql.agent import Agent as SavedAgent
    from src.models.sql.pdf_document import PDFDocument
    from src.schemas.agent_execution_revision import AgentOutputContract
    from tests.unit.lib.domain_packs.test_profile_validation import example
    from tests.unit.lib.openai_agents.test_model_request_measurement import FakeResponsesWSModel, _tool_call, _final
    from src.lib.config.agent_loader import AgentDefinition
    from src.lib.openai_agents import runner, document_references
    from src.lib.openai_agents.tools import record_evidence
    from agr_ai_curation_alliance.tools import weaviate_search
    from src.lib.context import agent_request_scope
    from src.lib.validation_review.chat import ChatReview

    raw, capability, _ = example.__wrapped__()
    monkeypatch.setattr("src.lib.agent_studio.profile_mapping_service.capability_catalog", lambda **kw: [capability])
    monkeypatch.setattr("src.lib.domain_packs.profile_validation.capability_catalog", lambda **kw: [capability])
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(folder_name=agent, agent_id=agent, name=agent, package_id=package))
    with store.SessionLocal.begin() as db:
        document = db.get(PDFDocument, UUID(scope.document_id))
        document.status = "completed"
        document.processing_completed_at = datetime.now(timezone.utc)
        owner_id = document.user_id
        _, revision = generic_profile_service.create_profile(db, owner_id, raw)
        saved = SavedAgent(agent_key="ca_" + uuid4().hex, user_id=owner_id, name="Canonical saved extractor",
            instructions="Read the paper and capture the three records in the declared profile.",
            model_id="gpt-6.1-sol", visibility="private", tool_ids=[
                "read_chunk", "record_evidence", "stage_generic_object", "finalize_generic_extraction"])
        db.add(saved)
        db.flush()
        contract = AgentOutputContract.model_validate({"output_state": "structured_extraction",
            "output_mode": "profile_bound_generic", "generic_profile_ref": {
                "profile_id": revision.profile_id, "profile_revision_id": revision.id,
                "revision": revision.revision, "fingerprint": revision.fingerprint}})
        append_execution_revision(db, saved, capture_execution_snapshot(db, saved, contract),
            user_id=owner_id, expected_revision_id=None)
        receipt = current_execution_receipt(db, saved.agent_key, owner_id, active_group_ids=[])

    chunk = {"id": str(uuid4()), "chunk_index": 0, "document_id": scope.document_id,
        "text": "The fixture records eco:A, eco:B, and eco:C were observed.", "page_number": 1,
        "section_title": "Results", "parent_section": "Results",
        "metadata": {"page_number": 1, "section_title": "Results"}}
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", AsyncMock(return_value=chunk))
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", AsyncMock(return_value={
        "previous_chunk_id": None, "next_chunk_id": None}))
    monkeypatch.setattr(document_references, "get_chunk_by_index", AsyncMock(return_value=chunk))
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", AsyncMock(return_value=chunk))
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))

    class ProfileExtractor(FakeResponsesWSModel):
        def __init__(self):
            super().__init__()
            self.candidates = []

        def _next(self, kwargs):
            self.calls.append(kwargs)
            call = len(self.calls)
            if call == 1:
                return _tool_call("read_chunk", "read", '{"chunk_index":0}')
            outputs = [entry for entry in kwargs["input"] if entry.get("type") == "function_call_output"]
            raw_output = outputs[-1]["output"]
            if isinstance(raw_output, str) and raw_output.startswith("status='ok' data="):
                # The SDK renders the native AgrQueryResult as text for these
                # bound builder tools. Read its actual controlled fixture data.
                output = {"status": "ok", "data": ast.literal_eval(
                    raw_output.split(" data=", 1)[1].split(" count=", 1)[0])}
            elif isinstance(raw_output, str):
                try:
                    output = json.loads(raw_output)
                except json.JSONDecodeError:
                    output = ast.literal_eval(raw_output)
            else:
                output = raw_output
            if call == 2:
                assert "eco:A" in str(output)
                return _tool_call("record_evidence", "evidence", json.dumps({
                    "entity": "Fixture records", "selections": [{"chunk_index": 0, "span_index": 0}]}))
            if call == 3:
                assert output["status"] == "verified"
                self.evidence_id = output["evidence_record_id"]
            if 4 <= call <= 6:
                assert output["status"] == "ok", output
                self.candidates.append(output["data"]["candidate_id"])
            if 3 <= call <= 5:
                label = "ABC"[call - 3]
                return _tool_call("stage_generic_object", f"stage-{label}", json.dumps({
                    "label": f"eco:{label}", "attributes": {"paper_name": f"eco:{label}"},
                    "evidence_record_ids": [self.evidence_id],
                    "classification_notes": ["The supplied source names this record."],
                    "rationale": "The supplied source names this record."}))
            if call == 6:
                return _tool_call("finalize_generic_extraction", "finalize", json.dumps({
                    "candidate_ids": self.candidates}))
            assert call == 7 and "finalized" in str(output), output
            return _final("Saved the three extracted records.")

    class ProfileSupervisor(CanonicalReviewer):
        def __init__(self):
            super().__init__()
            self.outer_calls = 0

        def _next(self, kwargs):
            if any(tool.name == "record_validation_review" for tool in kwargs["tools"]):
                return super()._next(kwargs)
            self.outer_calls += 1
            if self.outer_calls == 1:
                tool_name = f"ask_{receipt.agent_key}_specialist"
                assert tool_name in {tool.name for tool in kwargs["tools"]}
                return _tool_call(tool_name, "extract", '{"query":"Extract the three records"}')
            assert self.outer_calls == 2
            return _final("The saved records have been reviewed.")

    extractor_model = ProfileExtractor()
    monkeypatch.setattr("src.lib.openai_agents.config.get_model_for_agent", lambda *args, **kwargs: extractor_model)
    extractor = canonical_models.real_get_agent_by_id(receipt.agent_key,
        user_id=scope.owner_id, db_user_id=owner_id, document_id=scope.document_id,
        authenticated_groups=[], active_groups=[], execution_receipt=receipt.model_dump(mode="json"),
        execution_revision_id=str(receipt.agent_revision_id))
    assert extractor.execution_receipt == receipt.model_dump(mode="json")
    assert extractor.generic_profile is not None
    model = ProfileSupervisor()
    supervisor = Agent(name="Supervisor", model=model, instructions="Review the saved answers.")
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    monkeypatch.setattr(reviewer, "create_direct_supervisor", lambda **kwargs: supervisor)
    monkeypatch.setattr(runner, "get_langfuse", lambda: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with agent_request_scope(user_id=scope.owner_id, session_id=scope.session_id, trace_id=uuid4().hex):
        events = [event async for event in runner.run_agent_streamed(
            context_messages=[{"role": "user", "content": "Extract the three records"}],
            user_id=scope.owner_id, session_id=scope.session_id, turn_id=uuid4().hex,
            document_id=scope.document_id, active_groups=[], agent=extractor,
            chat_route_mode="agent", inline_chat_persistence=True, propagate_runtime_exceptions=True)]
    terminal, = [event for event in events if event["type"] == "RUN_FINISHED"]
    assert "SECOND_C" in terminal["data"]["response"] and "FIRST_C" not in terminal["data"]["response"]
    assert len(extractor_model.calls) == 7
    assert len(canonical_models.validators) == 4
    chat = ChatReview(scope.owner_id, scope.document_id, scope.session_id, uuid4().hex, supervisor, (), "Inspect")
    record, = chat.records()
    assert record.execution_receipt == receipt
    original = record.payload_json["extracted_objects"]
    assert [obj["payload"]["attributes"] for obj in original] == [
        {"paper_name": f"eco:{label}"} for label in "ABC"]
    reference = chat.reference(record)
    with store.SessionLocal() as db:
        facts = list(db.scalars(select(ValidationFact).where(ValidationFact.artifact_id == reference["artifact_id"])))
        attempts = [fact for fact in facts if fact.kind == "attempt"]
        completions = [store._content(db, fact) for fact in facts if fact.kind == "completion"]
        assert len(attempts) == len(completions) == 4
        assert sum("FIRST_C" in json.dumps(body) for body in completions) == 1
        decision = next(fact for fact in facts if str(fact.id) == model.parent_decision_id)
        assert "Ambiguous C requires reconsideration" in json.dumps(store._content(db, decision))
    from src.lib.flows.output_projection import (
        build_extraction_result_artifact_bundle, finalize_output_projection, FlowOutputProjectionPlan, FlowOutputColumnSpec)
    from src.lib.openai_agents.tools.file_output_tools import save_projected_file_output
    from src.models.sql.file_output import FileOutput
    def profile_resolver(requested_receipt):
        with store.SessionLocal() as db:
            return resolve_receipt_profile(db, requested_receipt)
    bundle = build_extraction_result_artifact_bundle(user_id=scope.owner_id, extraction_results=[record],
        document_id=scope.document_id, output_format="csv", profile_resolver=profile_resolver)
    projection = finalize_output_projection(bundle, FlowOutputProjectionPlan(format="csv", row_source="object", columns=[
        FlowOutputColumnSpec(key="validated", field_ref="object.attribute.resolved_id")]))
    monkeypatch.setattr("src.lib.file_outputs.storage.get_file_output_storage_path", lambda: tmp_path)
    with agent_request_scope(user_id=scope.owner_id, session_id=scope.session_id, trace_id=uuid4().hex):
        exported = await save_projected_file_output("csv", projection, "saved_profile", receipt.agent_key)
    with store.SessionLocal() as db:
        output = db.get(FileOutput, UUID(exported["file_id"]))
        assert output.file_metadata["selection_manifests"] == projection.selection_manifests
        content = Path(output.file_path).read_text()
    assert [row["validated"] for row in csv.DictReader(io.StringIO(content))] == ["FIRST_A", "FIRST_B", "SECOND_C"]


@pytest.mark.asyncio
@pytest.mark.parametrize("origin,planned", [
    ("bootstrap", {"shape": "three", "admitted": False}),
    ("curator", "three"),
], indirect=["planned"])
async def test_canonical_workspace_command_reviews_fresh_records_and_retains_history(
    planned, canonical_models, monkeypatch, origin
):
    """Fresh source -> curator command -> real SDK review -> current workspace."""
    from datetime import datetime, timezone
    from uuid import UUID
    from auth_runtime.base import AuthPrincipal
    from src.lib.curation_workspace import review_operations as operations
    from src.lib.curation_workspace import review_history_service as history
    from src.lib.curation_workspace.models import CurationCandidate, CurationReviewSession, CurationDraft
    from src.lib.domain_envelopes.persistence import (
        DomainEnvelopeCheckpointRequest, write_domain_envelope_checkpoint,
    )
    from src.models.sql.validation_review import WorkspaceReviewOperation
    from src.schemas.curation_workspace import CurationCandidateSource
    import json

    item = planned
    monkeypatch.setattr(
        "src.lib.curation_workspace.review_authorization.get_current_subject_resolver",
        lambda: lambda subject: AuthPrincipal(subject=subject, provider="fixture", groups=[]),
    )
    monkeypatch.setattr(reviewer, "create_direct_supervisor", lambda **kwargs: canonical_models.supervisor)
    monkeypatch.setattr("src.lib.redis_client.check_cancel_signal", AsyncMock(return_value=False))
    def unexpected(error):
        raise error
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", unexpected)
    with store.SessionLocal.begin() as db:
        assert not list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid, ValidationFact.kind == "completion")))
        session = CurationReviewSession(adapter_key="fixture", document_id=UUID(item.scope.document_id),
            created_by_id=item.scope.owner_id, prepared_at=datetime.now(timezone.utc))
        db.add(session)
        db.flush()
        sid = str(session.id)
        written = write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(
            project_key="fixture", envelope=item.source_envelope if origin == "bootstrap" else item.plan.envelope,
            expected_revision=0, document_id=item.scope.document_id, session_id=sid, adapter_key="fixture"))
        candidates = []
        for obj in item.plan.envelope.extracted_objects:
            candidate = CurationCandidate(session=session, adapter_key="fixture",
                source=CurationCandidateSource.MANUAL, envelope_id=item.plan.envelope.envelope_id,
                object_id=obj.object_id or obj.pending_ref_id, envelope_revision=written.revision)
            db.add(candidate)
            db.flush()
            db.add(CurationDraft(candidate_id=candidate.id, adapter_key="fixture", version=1,
                                fields=[], draft_metadata={}))
            candidates.append(str(candidate.id))
        saved = operations.submit(db, owner_id=item.scope.owner_id, session_id=sid,
            request_id=str(uuid4()), candidate_ids=candidates,
            guidance="Review all three original records; reconsider C if the supplied answer is ambiguous.",
            groups=[], origin=origin)
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        row.state, row.lease_token = "preparing", "canonical-workspace-lease"
    await operations._execute(saved["operation_id"], "canonical-workspace-lease", "canonical-workspace-trace")
    assert len(canonical_models.validators) == 4
    assert sum(hasattr(model, "curator_initial_context") for model in canonical_models.validators) == (
        3 if origin == "curator" else 0)
    with store.SessionLocal() as db:
        row = db.get(WorkspaceReviewOperation, saved["operation_id"])
        assert row.state == "complete", row.outcome
        assert row.outcome["results"][0]["review"]["ready"]
        assert row.outcome["trace_id"] == "canonical-workspace-trace"
        envelope = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
        assert [obj["payload"]["evidence_code_curies"] for obj in envelope.envelope_json["extracted_objects"]] == [
            ["FIRST_A"], ["FIRST_B"], ["SECOND_C"]]
        facts = list(db.scalars(select(ValidationFact).where(ValidationFact.artifact_id == item.aid)))
        assert len([fact for fact in facts if fact.kind == "attempt"]) == 4
        completions = [store._content(db, fact) for fact in facts if fact.kind == "completion"]
        assert len(completions) == 4
        assert any("FIRST_C" in json.dumps(body) for body in completions)
        decision = next(fact for fact in facts if str(fact.id) == canonical_models.model.parent_decision_id)
        assert "Ambiguous C requires reconsideration" in json.dumps(store._content(db, decision))
        # The same curator-facing history reader sees original and current values.
        for candidate_id in candidates:
            page = history.history_page(db, candidate_id, owner_id=item.scope.owner_id)
            baseline = history.history_detail(db, candidate_id, owner_id=item.scope.owner_id,
                fact_id="baseline", sha256=page["baseline"]["sha256"])
            assert all(f"eco:{label}" in baseline["content"] for label in "ABC")
    calls = sum(len(model.calls) for model in canonical_models.validators)
    with store.SessionLocal() as db:
        assert operations.get(db, saved["operation_id"], owner_id=item.scope.owner_id).state == "complete"
        for candidate_id in candidates:
            assert operations.latest_for_candidate(db, candidate_id, owner_id=item.scope.owner_id)
    assert sum(len(model.calls) for model in canonical_models.validators) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
async def test_canonical_flow_exports_accepted_unresolved_without_inventing_identifier(
    planned, canonical_models, monkeypatch, tmp_path
):
    await _canonical_producer_scenario(planned, canonical_models, monkeypatch, tmp_path,
                                       "flow", unresolved_b=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('planned', ['three'], indirect=True)
async def test_scoped_reviewer_records_each_answer_without_full_baseline(planned, canonical_models):
    import json
    from tests.validation_review_runtime_support import HistoryReadingReviewer, _tool_call
    from tests.unit.lib.openai_agents.test_model_request_measurement import _final
    from src.lib.validation_history import selections

    class ScopedReviewer(HistoryReadingReviewer):
        def review(self):
            reference = yield _tool_call('get_validation_review_packet', 'packet',
                                         '{"operation_id":"scoped-initial"}')
            packet = yield from self.read(reference)
            for index, unit in enumerate(packet['manifest']['units']):
                # Non-composite bindings legitimately have no separate field group.
                assert 'decision_unit' in unit
                source_ref = yield _tool_call('get_validation_record_source', f'source-{index}',
                                             json.dumps({'target_id': unit['target_id']}))
                source = yield from self.read(source_ref)
                assert source['record']['payload']['evidence_code_curies']
                pending = [b for b in packet['manifest']['blockers']
                           if b['target_id'] == unit['target_id'] and b['code'] == 'answer_review_pending']
                assert len(pending) == 1
                link = pending[0]
                request = yield from self.read(link['attempt_ref'])
                answer = yield from self.read(link['completion_ref'])
                assert unit['target_id'] in request['coverage']
                assert answer['state'] == 'completed'
                entry = CanonicalReviewer.entry(unit, {'id': link['attempt_ref']['fact_id']}, 'accept')
                yield _tool_call('record_validation_review', f'decide-{index}',
                    json.dumps({'operation_id': f'scoped-decision-{index}', 'entries': [entry]}))
            yield _final('Requested answers reviewed.')

    item = planned
    checkpoint = checkpoint_provider(item)
    await item.adapter.initial()
    model = ScopedReviewer()
    context = ReviewContext(item.scope, item.ep, 'supervisor.validation_review')
    supervisor = canonical_models.supervisor.clone(model=model)
    agent = reviewer.create_supervisor_phase(context, supervisor=supervisor,
        checkpoint_provider=checkpoint, authenticated_groups=[], execute_work=item.adapter.execute,
        advance_work=lambda: item.adapter.advance(checkpoint),
        prepare_inputs=lambda: item.adapter.prepare_inputs(checkpoint), refresh_inputs=item.adapter.refresh_inputs)
    before = store.read_baseline(item.scope, item.aid)
    result = await reviewer.run(context, agent, input='Assess the requested saved answers incrementally.',
                                run_config=RunConfig(tracing_disabled=True))
    assert result['review']['ready']
    assert selections.read_manifest(item.scope, item.aid, episode_id=item.ep)['ready']
    assert len(canonical_models.validators) == 3
    assert store.read_baseline(item.scope, item.aid) == before
    assert len([b for b in model.read_bodies if b.get('representation') == 'normalized_source_record']) == 3
    assert not any('normalized' in b and 'typed' in b for b in model.read_bodies)
