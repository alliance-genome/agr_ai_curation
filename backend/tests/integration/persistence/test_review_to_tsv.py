"""Real SDK loop, durable review ledger and TSV finalization with a fake provider."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from agents import Agent, ModelResponse, RunConfig
from openai.types.responses import ResponseFunctionToolCall

from src.lib.validation_history import store, runtime, selections
from src.lib.validation_review import admission, reviewer
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.tools import create_tools
from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, load_domain_envelope
from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
from src.schemas.domain_pack_metadata import DomainPackFieldDefinition, DomainPackFieldType
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated
from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack
from tests.unit.lib.openai_agents.test_model_request_measurement import FakeResponsesHTTPModel, _usage, _final


@pytest.mark.asyncio
@pytest.mark.parametrize("record_count", [2, 70])
async def test_review_crosses_old_call_and_tool_ceilings_and_exports_uncertain_tsv(scope, tmp_path, monkeypatch, record_count):
    for key in ("REVIEWER_TURNS", "REVIEWER_TOOLS", "DEADLINE_SECONDS"):
        monkeypatch.delenv("VALIDATION_REVIEW_" + key, raising=False)
    # Replace only the provider/capacity endpoint. SDK tools, runtime policy,
    # durable decisions, publication reads and finalization remain real.
    monkeypatch.setenv("AGENT_MODEL_CAPACITY_OVERRIDES_JSON", '{"gpt-test":{"context_window_tokens":1050000,"max_input_tokens":922000,"max_output_tokens":128000}}')
    monkeypatch.setattr("src.lib.openai_agents.context_capacity.NativeCapacityRequest.count", AsyncMock(return_value=1000))
    pack = _multivalued_dispatch_pack(tmp_path)
    definition = pack.metadata.object_definitions[0].model_copy(update={"fields": [*pack.metadata.object_definitions[0].fields,
        DomainPackFieldDefinition(field_path="statement", field_type=DomainPackFieldType.STRING, metadata={"editable": True})]})
    pack = replace(pack, metadata=pack.metadata.model_copy(update={"object_definitions": [definition]}))
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    monkeypatch.setattr("src.lib.flows.export_fields.packaged_domain_pack", lambda *args, **kwargs: pack)
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=f"row-{i}",
            payload={"statement": None if i == 0 else f"Original {i}"}) for i in range(record_count)])
    env = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, envelope=env,
        typed=env.model_dump(mode="json"), provenance={})
    aid = env.metadata["validation_history"]["artifact_id"]
    env = selections.declare_envelope_episode(runtime.CapturedArtifact(scope, aid), env, [])
    ep = env.metadata["review_episode_id"]
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=record_count, episodes=1)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    with store.SessionLocal() as db:
        baseline = store._artifact(db, aid, scope).baseline_sha
    request = DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=env, expected_revision=0,
        document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=uuid4())

    def checkpoint():
        with store.SessionLocal() as db:
            row = db.get(DomainEnvelopeModel, env.envelope_id)
            return request if row is None else replace(request,
                envelope=load_domain_envelope(db, env.envelope_id), expected_revision=row.revision)

    context = ReviewContext(scope, ep, "fixture-reviewer")
    responses = []
    for i, obj in enumerate(env.extracted_objects):
        tid = obj.metadata["validation_history"]["target_id"]
        entry = {"target_id": tid, "decision_unit_id": tid, "expected_version": 0,
            "action": "accept", "reason": "Assessment complete", "extraction_ref": baseline}
        if i == 0:
            entry["unresolved_fields"] = [{"field_path": "statement", "explanation": "Use not established"}]
        calls = [("record_validation_review", {"operation_id": f"accept-{i}", "entries": [entry]})]
        if i < record_count - 1:
            calls += [("get_validation_review_packet", {"operation_id": f"packet-{i}-{j}"}) for j in range(3)]
        responses.append(ModelResponse(output=[ResponseFunctionToolCall(
            id=f"fc-{i}-{j}", call_id=f"call-{i}-{j}", name=name,
            arguments=json.dumps(args), type="function_call") for j, (name, args) in enumerate(calls)],
            usage=_usage(), response_id=f"response-{i}"))
    responses.append(_final("Review decisions recorded."))
    model = FakeResponsesHTTPModel(responses)
    model._client = model._client.copy(max_retries=0)
    agent = Agent(name="Fixture Reviewer", model=model, instructions="Review saved records.",
                  tools=create_tools(context, checkpoint_provider=checkpoint))
    from src.lib.observability.cost_context import attach_agent_cost_identity
    attach_agent_cost_identity(agent, {"agent_id": "fixture-reviewer", "agent_role": "supervisor"})
    result = await reviewer.run(context, agent, input="Review every saved record and retain uncertainty.",
                                run_config=RunConfig(tracing_disabled=True))
    if not result["review"]["ready"]:
        for item in model.calls[-1]["input"]:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                print(item["output"])
    assert result["review"]["ready"], result
    assert record_count <= len(model.calls) <= record_count + 1
    from src.models.sql.validation_review import ValidationReviewEpisode
    with store.SessionLocal() as db:
        charged = db.get(ValidationReviewEpisode, ep).charged
        assert charged["reviewer_turns"] >= record_count
        assert charged["reviewer_tools"] >= (record_count - 1) * 4 + 1
        assert charged.get("extra_attempts", 0) == 0
    current = checkpoint().envelope
    assert [obj.payload for obj in current.extracted_objects] == [obj.payload for obj in env.extracted_objects]

    from src.lib.flows.output_projection import build_flow_output_artifact_bundle, FlowOutputProjectionPlan, finalize_output_projection
    from src.lib.openai_agents.tools.file_output_tools import _projection_content_for_file_type
    bundle = build_flow_output_artifact_bundle(user_id=scope.owner_id, document_id=scope.document_id,
        completed_steps=[{"step": 1, "node_id": "source", "agent_id": "fixture", "agent_name": "Fixture",
            "candidate": SimpleNamespace(payload_json=current.model_dump(mode="json"), agent_key="fixture",
                adapter_key="fixture", candidate_count=record_count, conversation_summary="Complete assessment")}],
        flow_name="Large review", output_format="tsv")
    plan = FlowOutputProjectionPlan.model_validate({"format": "tsv", "row_source": "object",
        "columns": [{"key": "statement", "field_ref": "object.pack.Annotation.statement"}]})
    projected = finalize_output_projection(bundle, plan)
    assert len(projected.rows) == record_count
    # The doubtful empty value exports as stored: blank, never a placeholder.
    assert projected.rows[0]["statement"] == ""
    assert projected.rows[-1]["statement"] == f"Original {record_count - 1}"
    content = _projection_content_for_file_type(projection=projected, output_format="tsv")
    assert "UNRESOLVED" not in content and "unresolved" not in content
    assert f"Original {record_count - 1}" in content
    # The reviewer's doubt and reason are their own, optional fields.
    review = finalize_output_projection(bundle, FlowOutputProjectionPlan.model_validate({
        "format": "tsv", "row_source": "object", "columns": [
            {"key": "statement", "field_ref": "object.pack.Annotation.statement"},
            {"key": "doubt", "field_ref": "object.review_uncertainty"},
            {"key": "reason", "field_ref": "object.review_reason"}]}))
    assert review.rows[0] == {"statement": "", "doubt": "statement: Use not established",
                              "reason": "Assessment complete"}
    assert review.rows[-1] == {"statement": f"Original {record_count - 1}", "doubt": "",
                               "reason": "Assessment complete"}


def _captured(scope, tmp_path, monkeypatch, payloads):
    """Extraction-only records captured for review, with the checkpoint the reviewer saves to."""
    pack = _multivalued_dispatch_pack(tmp_path)
    definition = pack.metadata.object_definitions[0].model_copy(update={"fields": [*pack.metadata.object_definitions[0].fields,
        DomainPackFieldDefinition(field_path="statement", field_type=DomainPackFieldType.STRING, metadata={"editable": True})]})
    pack = replace(pack, metadata=pack.metadata.model_copy(update={"object_definitions": [definition]}))
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    monkeypatch.setattr("src.lib.flows.export_fields.packaged_domain_pack", lambda *args, **kwargs: pack)
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=f"row-{i}", payload=payload)
                           for i, payload in enumerate(payloads)])
    env = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, envelope=env,
        typed=env.model_dump(mode="json"), provenance={})
    aid = env.metadata["validation_history"]["artifact_id"]
    env = selections.declare_envelope_episode(runtime.CapturedArtifact(scope, aid), env, [])
    ep = env.metadata["review_episode_id"]
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=len(payloads), episodes=1)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    with store.SessionLocal() as db:
        baseline = store._artifact(db, aid, scope).baseline_sha
    request = DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=env, expected_revision=0,
        document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=uuid4())

    def checkpoint():
        with store.SessionLocal() as db:
            row = db.get(DomainEnvelopeModel, env.envelope_id)
            return request if row is None else replace(request,
                envelope=load_domain_envelope(db, env.envelope_id), expected_revision=row.revision)

    targets = [obj.metadata["validation_history"]["target_id"] for obj in env.extracted_objects]
    return SimpleNamespace(context=ReviewContext(scope, ep, "fixture-reviewer"), checkpoint=checkpoint,
                           baseline=baseline, targets=targets)


def _review_export(scope, envelope, columns, *, finalized=True):
    from src.lib.flows.output_projection import (
        build_flow_output_artifact_bundle, FlowOutputProjectionPlan, apply_projection_plan, finalize_output_projection,
    )
    bundle = build_flow_output_artifact_bundle(user_id=scope.owner_id, document_id=scope.document_id,
        completed_steps=[{"step": 1, "node_id": "source", "agent_id": "fixture", "agent_name": "Fixture",
            "candidate": SimpleNamespace(payload_json=envelope.model_dump(mode="json"), agent_key="fixture",
                adapter_key="fixture", candidate_count=len(envelope.extracted_objects), conversation_summary="x")}],
        flow_name="Review reasons", output_format="tsv")
    project = finalize_output_projection if finalized else apply_projection_plan
    return bundle, project(bundle, FlowOutputProjectionPlan.model_validate({
        "format": "tsv", "row_source": "object",
        "columns": [{"key": key, "field_ref": ref} for key, ref in columns]}))


def test_review_reason_is_the_decision_that_stands_never_a_replaced_one(scope, tmp_path, monkeypatch):
    from src.lib.validation_review.decisions import record_decisions

    item = _captured(scope, tmp_path, monkeypatch, [{"statement": "First"}, {"statement": "Second"}])

    def accept(index, version, reason, operation_id, **extra):
        return record_decisions(item.context, checkpoint_request=item.checkpoint(), operation_id=operation_id,
            entries=[{"target_id": item.targets[index], "decision_unit_id": item.targets[index],
                      "expected_version": version, "action": "accept", "reason": reason,
                      "extraction_ref": item.baseline, **extra}])

    accept(0, 0, "The first reading of the table.", "first",
           unresolved_fields=[{"field_path": "statement", "explanation": "The column is unclear."}])
    # A later acceptance of the same record replaces the first decision and its doubt.
    accept(0, 1, "The methods name this cohort.", "second")
    assert accept(1, 0, "Results report it directly.", "third")["ready"] is True

    columns = [("statement", "object.pack.Annotation.statement"),
               ("doubt", "object.review_uncertainty"), ("reason", "object.review_reason")]
    bundle, projected = _review_export(scope, item.checkpoint().envelope, columns)
    assert projected.rows == [
        {"statement": "First", "doubt": "", "reason": "The methods name this cohort."},
        {"statement": "Second", "doubt": "", "reason": "Results report it directly."},
    ]
    # The review fields are opt-in: the declared default layout never lists them.
    refs = {ref for artifact in bundle.artifacts for ref in artifact.default_object_refs}
    assert not refs & {"object.review_uncertainty", "object.review_reason"}


def test_review_reason_after_reject_defer_revoke_and_a_curator_accept(scope, tmp_path, monkeypatch):
    """A curator's own accept is the decision that stands; a revoke leaves none.

    ALL-1561: a record still pending under the reviewer's reject or defer is set
    aside, and Review reason gives the reason that set it aside. Pending records
    are in an output only when it selects Review status."""
    from dataclasses import replace as replaced
    from src.lib.validation_review.decisions import record_decisions

    item = _captured(scope, tmp_path, monkeypatch, [{"statement": f"S{i}"} for i in range(4)])

    def decide(index, version, action, reason, operation_id, context=None, **refs):
        return record_decisions(context or item.context, checkpoint_request=item.checkpoint(),
            operation_id=operation_id, entries=[{"target_id": item.targets[index], "decision_unit_id": item.targets[index],
                "expected_version": version, "action": action, "reason": reason, **refs}])

    baseline = {"extraction_ref": item.baseline}
    decide(0, 0, "accept", "Table 1 lists it for this cohort.", "accept-0", **baseline)
    decide(0, 1, "reject", "A second reading did not hold up.", "reject-0", **baseline)
    decide(1, 0, "defer", "Needs a look at the supplement.", "defer-1", **baseline)
    decide(2, 0, "accept", "The methods name it.", "accept-2", **baseline)
    decide(2, 1, "revoke", "Accepted too early.", "revoke-2")
    curator = replaced(item.context, actor_id=scope.owner_id, actor_type="curator")
    decide(3, 0, "accept", "Confirmed against Figure 2.", "curator-3", context=curator, **baseline)

    _, projected = _review_export(scope, item.checkpoint().envelope, [
        ("statement", "object.pack.Annotation.statement"), ("reason", "object.review_reason"),
        ("status", "object.review_status")], finalized=False)
    assert projected.rows == [
        {"statement": "S0", "reason": "A second reading did not hold up.", "status": "set aside"},
        {"statement": "S1", "reason": "Needs a look at the supplement.", "status": "set aside"},
        {"statement": "S2", "reason": "", "status": "set aside"},
        {"statement": "S3", "reason": "Confirmed against Figure 2.", "status": "accepted"},
    ]
    _, without_status = _review_export(scope, item.checkpoint().envelope, [
        ("statement", "object.pack.Annotation.statement")], finalized=False)
    assert without_status.rows == [{"statement": "S3"}] and without_status.set_aside_left_out == 3
