"""A NUL-bearing extraction behaves like a clean one through flow review (KANBAN-2072).

PostgreSQL JSONB cannot hold U+0000, so history stores the stripped text. This
runs the production sequence after capture (capture_extraction ->
FlowReview.receive -> initial_validation -> finish_review) twice, once for a
clean quote and once for the same quote with a NUL, and requires the two runs
to be indistinguishable apart from the stripped character.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.lib.validation_history import store
from src.lib.validation_review import admission, flow_steps
from tests.integration.persistence.test_chat_review_adapter import (
    planned as planned,
    scope as scope,
    migrated as migrated,
)


CLEAN = "Synthetic marker +/ sentence for a null-character regression."
WITH_NUL = "Synthetic marker +/\x00 sentence for a null-character regression."


def _source_input_count(db, artifact_id):
    from src.models.sql.validation_history import ValidationFact

    return db.scalar(select(func.count()).select_from(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.kind == "validation_source_input",
    ))


async def _run_flow(item, monkeypatch, quote):
    from agents import Agent
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob, ValidatorRuntimeContext
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.openai_agents.evidence_summary import build_evidence_record_id
    from src.lib.validation_history import runtime
    from src.lib.validation_review import decisions, source_inputs
    from src.lib.validation_review.flow import FlowReview
    from src.models.sql.validation_history import ValidationFact
    from src.models.sql.validation_review import FlowReviewStep
    from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
    from tests.integration.persistence.test_chat_review_adapter import entries_for
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload

    run_id = uuid4().hex
    supervisor = Agent(name="Configured Flow Supervisor", model="test", instructions="Policy")
    attach_agent_cost_identity(supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"})
    step = flow_steps.claim(item.scope, run_id=run_id, node_id="extract", contract={"extractor": "saved"})
    parent_scope = admission.create_scope(item.scope, operation_id="batch:" + run_id,
        kind="discovered_batch", targets=0, episodes=0)
    flow = FlowReview(owner_id=item.scope.owner_id, document_id=item.scope.document_id,
        session_id=item.scope.session_id, turn_id=run_id, supervisor=supervisor,
        authenticated_groups=(), user_request="Extract evidence", flow_run_id=run_id,
        step_id=step["step_id"], node_id="extract",
        validation_groups=[{"state": "automatic", "binding_id": match.binding.binding_id}
                           for match in item.plan.matches[:1]],
        parent_scope_id=parent_scope, configured_nodes={},
        step_provenance={"flow_id": "fixture", "flow_name": "Fixture flow", "step": 1,
                         "agent_name": "Fixture extractor", "tool_name": "extract"},
        execution_context={"captured_at": datetime.now(timezone.utc).isoformat(), "source_kind": "flow",
            "flow_id": "fixture", "step_id": "extract", "agent_key": "fixture",
            "executed_query": "Extract evidence", "document": None})

    # The evidence id is minted over the raw quote, as record_evidence does.
    record = {"entity": "eco:1", "verified_quote": quote, "page": 1, "section": "Results",
              "chunk_id": "chunk-1"}
    record["evidence_record_id"] = build_evidence_record_id(record)
    source_scope = store.HistoryScope(item.scope.owner_id, item.scope.document_id, uuid4().hex)
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=item.pack.pack_id,
        metadata={"evidence_records": [record]},
        extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id="one",
            payload={"evidence_code_curies": ["eco:1", "eco:2"]},
            evidence_record_ids=[record["evidence_record_id"]])])
    env = runtime.capture_extraction(scope=source_scope, producer_id=uuid4().hex,
        envelope=env, typed=env.model_dump(mode="json"), provenance={})

    calls = []

    def run(request, binding, **kwargs):
        calls.append([item["verified_quote"] for item in request.evidence])
        return {**_result_payload(request, resolved_values={"curie": "ECO:chosen"}), "resolved_objects": []}

    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
                        lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    context = ValidatorRuntimeContext(user_id=item.scope.owner_id, document_id=item.scope.document_id,
                                      authenticated_groups=())
    returned = await flow.receive(env, item.pack, agent_key="fixture", adapter_key="fixture",
        tool_name="specialist-label", source_metadata={}, execution_receipt=None, context=context)
    ref = flow.reference(flow.record)
    aid, episode_id = ref["artifact_id"], ref["episode_id"]

    def attempt_inputs():
        with store.SessionLocal() as db:
            rows = db.scalars(select(ValidationFact).where(
                ValidationFact.artifact_id == aid, ValidationFact.kind == "attempt")).all()
            inputs = [store._content(db, row)["input"] for row in rows]
            return inputs, [source_inputs.current(db, aid, frozen) for frozen in inputs]

    def recheck_from_memory():
        """Rebuild the frozen inputs from the in-memory envelope capture returned."""
        binding = runtime.CapturedArtifact(source_scope, aid)
        matches = DomainPackValidationRegistry.from_domain_pack(item.pack).match_bindings(env)
        jobs = [ValidatorDispatchJob(match, build_domain_validation_request(match).request)
                for match in matches]
        frozen = runtime.frozen_job_requests(binding, jobs)
        changed = []
        for _ in range(2):
            with store.SessionLocal.begin() as db:
                changed.append(len(source_inputs.update(db, aid, operation_id="recheck:" + uuid4().hex,
                    before={}, after={request["target_id"]: request for request in frozen})))
        return changed

    inputs_before, current_before = attempt_inputs()
    with store.SessionLocal() as db:
        facts_after_initial = _source_input_count(db, aid)
    status_before = admission.status(source_scope, episode_id)

    fixture = SimpleNamespace(scope=source_scope, aid=aid, ep=episode_id)

    async def review(saved):
        decisions.record_decisions(flow.context(saved), checkpoint_request=flow.checkpoint(saved),
            operation_id="accept-flow", entries=entries_for(fixture, "accept"))
        return {"state": "completed", "output": "Reviewed saved answers"}

    monkeypatch.setattr(flow, "review_result", review)
    finished = await flow.finish_review()
    status_after = admission.status(source_scope, episode_id)
    _, current_after = attempt_inputs()
    rechecked = recheck_from_memory()
    with store.SessionLocal() as db:
        facts_after_recheck = _source_input_count(db, aid)
        stored_record = store._content(db, store._fact(db, aid, flow.record.metadata["flow_review"]["plan_ref"]))[
            "envelope"]["metadata"]["evidence_records"][0]
        step_state = db.get(FlowReviewStep, step["step_id"]).state

    return {
        "validator_calls": len(calls),
        "validator_quotes": calls,
        "returned_has_nul": "\\u0000" in returned,
        "stored_quote": stored_record["verified_quote"],
        "record_quote": flow.record.payload_json["metadata"]["evidence_records"][0]["verified_quote"],
        "attempt_quotes": [[e["verified_quote"] for e in i["request"]["evidence"]] for i in inputs_before],
        "source_input_facts_after_initial": facts_after_initial,
        "current_before": current_before,
        "status_before": (status_before["state"], status_before["ready"], len(status_before["uncovered"])),
        "status_after": (status_after["state"], status_after["ready"], len(status_after["uncovered"])),
        "current_after": current_after,
        "recheck_changed": rechecked,
        "source_input_facts_after_recheck": facts_after_recheck,
        "finished_payload": finished.extracted_objects[0].payload,
        "step_state": step_state,
    }


@pytest.mark.asyncio
async def test_null_character_envelope_behaves_like_a_clean_one_through_flow_review(planned, monkeypatch):
    clean = await _run_flow(planned, monkeypatch, CLEAN)
    with_nul = await _run_flow(planned, monkeypatch, WITH_NUL)
    assert clean["validator_calls"] == 2
    assert clean["step_state"] == "completed"
    assert clean["stored_quote"] == CLEAN
    assert with_nul == clean


def _captured(scope, quote):
    from src.lib.openai_agents.evidence_summary import build_evidence_record_id
    from src.lib.validation_history import runtime
    from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope

    record = {"entity": "eco:1", "verified_quote": quote, "page": 1, "section": "Results",
              "chunk_id": "chunk-1"}
    record["evidence_record_id"] = build_evidence_record_id(record)
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id="fixture",
        metadata={"evidence_records": [record], "note": quote},
        extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id="one",
            payload={"mention": quote, "z_last": 1, "a_first": [quote]},
            evidence_record_ids=[record["evidence_record_id"]])])
    before = env.model_dump(mode="json")
    captured = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        envelope=env, typed=before, provenance={})
    return record, before, env, captured


def _without_history_refs(dump):
    dump["metadata"].pop("validation_history")
    for obj in dump["extracted_objects"]:
        obj["metadata"].pop("validation_history")
    return dump


def test_clean_capture_keeps_the_v0118_copy_and_never_rebuilds(scope, monkeypatch):
    import json
    from src.schemas.domain_envelope import DomainEnvelope

    def no_rebuild(*args, **kwargs):
        raise AssertionError("a clean envelope must not be rebuilt")

    monkeypatch.setattr(DomainEnvelope, "model_validate", classmethod(no_rebuild))
    _, before, original, captured = _captured(scope, CLEAN)
    monkeypatch.undo()
    assert type(captured) is type(original) and captured is not original
    after = _without_history_refs(captured.model_dump(mode="json"))
    # Byte for byte, in key order: only the history references were added.
    assert json.dumps(after) == json.dumps(before)
    assert original.model_dump(mode="json") == before


def test_nul_capture_continues_with_the_stored_form(scope):
    import json
    from src.lib.persistence_sanitization import sanitize_persisted_json_value

    record, before, original, captured = _captured(scope, WITH_NUL)
    assert type(captured) is type(original)
    after = _without_history_refs(captured.model_dump(mode="json"))
    # Identical to the original except for the removed characters, key order included.
    assert json.dumps(after) == json.dumps(sanitize_persisted_json_value(before))
    assert "\\u0000" not in json.dumps(after)
    assert after["extracted_objects"][0]["payload"]["mention"] == CLEAN
    assert after["extracted_objects"][0]["evidence_record_ids"] == [record["evidence_record_id"]]
    assert original.model_dump(mode="json") == before  # the caller's object is not mutated
    stored = store.read_baseline(
        scope, captured.metadata["validation_history"]["artifact_id"])["normalized"]
    assert stored == sanitize_persisted_json_value(before)
