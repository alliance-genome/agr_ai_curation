"""Exact specialist input/history with real persisted sources and fake answers."""
import json
from uuid import uuid4

import pytest
from agents.tool_context import ToolContext

from src.lib.validation_history import store, runtime
from src.lib.validation_review import admission, specialist_context
from tests.integration.persistence.test_chat_review_adapter import planned as planned, scope as scope, migrated as migrated
from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload


@pytest.mark.asyncio
async def test_followup_context_retains_actual_previous_input_and_scopes_reads(planned, monkeypatch):
    item = planned
    explanation = "The organism in the Discussion mention is ambiguous; examine Methods."

    def run(request, binding, **kwargs):
        value = _result_payload(request, resolved_values={"curie": "ECO:original-answer"})
        value["explanation"] = explanation
        return {**value, "resolved_objects": []}

    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner", lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    await item.adapter.initial()
    entry = item.plan.envelope.extracted_objects[0].metadata["validation_history"]["target_id"]
    target = runtime.binding_target(item.plan.history_binding, item.plan.matches[0])
    with store.SessionLocal.begin() as db:
        parent = store._append(db, item.aid, operation_id="context-parent:" + uuid4().hex,
            kind="decision", body={"actor": {"type": "agent", "id": "supervisor"},
                "entries": [{"target_id": target, "action": "defer", "reason": "Need organism context"}]})
        unrelated = store._append(db, item.aid, operation_id=uuid4().hex,
            kind="specialist_context", body={"private_unrelated_entry": "MUST_NOT_BE_VISIBLE"})
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        packet = specialist_context.assemble_in_transaction(db, item.scope, ep,
            operation_id="followup-input", entry_ids=[entry], parent_decision_id=parent.id,
            instructions="Investigate Materials and Methods for organism context.",
            current_entries={entry: item.plan.envelope.extracted_objects[0].model_dump(mode="json")},
            attempt={"ordinal": 2, "ceiling": 2, "remaining_after": 0}, purpose="targeted_extraction")
        unrelated_reference = specialist_context.reference(unrelated)
    context = packet["context"]
    assert context["original_entries"][0]["entry_id"] == entry
    assert context["previous_validation"]
    assert all(record["assessed_input"]["request"] for record in context["previous_validation"])
    assert explanation in json.dumps(context)
    assert context["supervisor_request"]["parent_decision"]["entries"][0]["reason"] == "Need organism context"
    assert "MUST_NOT_BE_VISIBLE" not in json.dumps(packet)
    reader = specialist_context.create_history_reader(item.scope, item.aid,
        records=packet["history"]["records"], packet_bytes=2048)
    ref = packet["history"]["records"][0]
    from sqlalchemy import select
    from src.models.sql.validation_history import ValidationFact
    with store.SessionLocal() as db:
        before_facts = set(db.scalars(select(ValidationFact.id).where(ValidationFact.artifact_id == item.aid)))
    denied_references = [
        unrelated_reference,
        {"fact_id": "baseline", "sha256": context["baseline_sha"]},
        context["previous_validation"][0]["answer_reference"],
        {**ref, "sha256": "0" * 64},
    ]
    for denied in denied_references:
        # Model-correctable requests do not grant access or kill the invocation.
        result = await reader.on_invoke_tool(
            ToolContext(context=None, tool_name="read_supplied_history", tool_call_id="denied", tool_arguments="{}"),
            json.dumps({**denied, "offset": 0}),
        )
        refusal = result if isinstance(result, dict) else json.loads(result)
        assert refusal["status"] == "not_supplied"
        assert set(refusal) == {"status", "message"}
        assert "MUST_NOT_BE_VISIBLE" not in json.dumps(refusal)
        assert explanation not in json.dumps(refusal)
    offset = 0
    chunks = []
    while offset is not None:
        result = await reader.on_invoke_tool(ToolContext(context=None, tool_name="read_supplied_history", tool_call_id="test", tool_arguments="{}"), json.dumps({**ref, "offset": offset}))
        page = result if isinstance(result, dict) else json.loads(result)
        chunks.append(page["content"])
        offset = page["next_offset"]
    assert json.loads("".join(chunks)) == context
    with store.SessionLocal() as db:
        assert set(db.scalars(select(ValidationFact.id).where(ValidationFact.artifact_id == item.aid))) == before_facts
    # Supplying a valid capability cannot bypass actual owner authorization.
    denied_scope = store.HistoryScope("not-the-owner", item.scope.document_id, item.scope.session_id)
    denied_reader = specialist_context.create_history_reader(denied_scope, item.aid,
        records=packet["history"]["records"], packet_bytes=2048)
    with pytest.raises(store.HistoryAccessError):
        await denied_reader.on_invoke_tool(ToolContext(context=None, tool_name="read_supplied_history", tool_call_id="test", tool_arguments="{}"), json.dumps({**ref, "offset": 0}))
    # Integrity errors after authorization are still fatal, not argument refusals.
    original_fact = store._fact
    def changed_fact(*args, **kwargs):
        from types import SimpleNamespace
        fact = original_fact(*args, **kwargs)
        return SimpleNamespace(body_sha="changed") if fact.id == ref["fact_id"] else fact
    monkeypatch.setattr(store, "_fact", changed_fact)
    with pytest.raises(store.HistoryConflict, match="identity changed"):
        await reader.on_invoke_tool(ToolContext(context=None, tool_name="read_supplied_history", tool_call_id="test", tool_arguments="{}"), json.dumps({**ref, "offset": 0}))


@pytest.mark.parametrize("planned", ["three"], indirect=True)
def test_workspace_envelope_context_keeps_all_entries_beyond_decision_command_limit(planned):
    from types import SimpleNamespace
    item = planned
    envelope = item.plan.envelope
    roots = [obj.metadata["validation_history"]["target_id"] for obj in envelope.extracted_objects]
    target = store.register_target(item.scope, item.aid, identity="envelope-context-test",
        original={"binding_id": "envelope-context-test"},
        parent_target_id=envelope.metadata["validation_history"]["target_id"])
    with store.SessionLocal.begin() as db:
        request = store._append(db, item.aid, operation_id=uuid4().hex,
            kind="workspace_revalidation_request", parent_id=item.ep,
            body={"actor": {"type": "curator", "id": item.scope.owner_id},
                  "entries": [{"target_id": roots[0], "action": "request_revalidation", "reason": "Check all supplied context"}]})
        request_id = request.id
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        context_episode = SimpleNamespace(id=ep.id, artifact_id=ep.artifact_id, policy={**ep.policy, "decision_entries": 1})
        packet = specialist_context.assemble_in_transaction(db, item.scope, context_episode,
            operation_id="full-batch-context", entry_ids=roots, parent_decision_id=request_id,
            instructions="Use the supplied records and history", current_entries={
                obj.metadata["validation_history"]["target_id"]: obj.model_dump(mode="json") for obj in envelope.extracted_objects},
            attempt={}, purpose="validation_reconsideration", additional_target_ids=[target])
        assert len(packet["context"]["original_entries"]) == 3
    packet = item.adapter.specialist_packet({
        "requests": [{"target_id": target}],
        "invocation": {"execution_id": uuid4().hex, "attempt_ids": []},
        "followup": {"parent_decision_id": request_id, "instructions": "Check the envelope"},
    })
    assert len(packet["context"]["original_entries"]) == 3
