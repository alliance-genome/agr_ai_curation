"""Chat adapter uses real admission/history/checkpoints with fake validator answers."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from agents import Agent

from src.lib.validation_history import runtime, store
from src.lib.validation_review import admission, decisions, chat_dispatch
from src.lib.validation_review.validator_adapter import (
    ValidatorAdapter,
    save_plan,
    MATCH,
    serialize_match,
)
from src.lib.domain_packs.validator_dispatch import (
    prepare_validator_dispatch,
    ValidatorRuntimeContext,
)
from src.models.sql.validation_history import ValidationFact
from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
from tests.integration.persistence.test_validation_history import (
    scope as scope,
    migrated as migrated,
)
from tests.unit.lib.domain_packs.test_validator_dispatch import (
    _multivalued_dispatch_pack,
    _result_payload,
)


@pytest.fixture
def planned(scope, tmp_path, monkeypatch, request):
    # These database tests use a new event loop per case. Cancellation behaviour
    # is tested separately; do not share Redis's loop-bound pool across cases.
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "src.lib.redis_client.check_cancel_signal", AsyncMock(return_value=False)
    )
    pack = _multivalued_dispatch_pack(tmp_path)
    if getattr(request, "param", None) == "dependent":
        from copy import deepcopy

        raw = deepcopy(pack.metadata.metadata)
        first = raw["validator_bindings"]["active"][0]
        raw["validator_bindings"]["active"].append(
            {
                **deepcopy(first),
                "binding_id": "fixture.dependent",
                "runs_after": [first["binding_id"]],
            }
        )
        pack = replace(
            pack, metadata=pack.metadata.model_copy(update={"metadata": raw})
        )
    if getattr(request, "param", None) == "nested":
        from src.lib.domain_packs.loader import load_domain_pack_metadata
        text = pack.metadata_path.read_text().replace("evidence_code_curies", "genes.aliases")
        text = text.replace("    fields:\n", "    fields:\n      - field_path: genes\n        field_type: string\n        metadata:\n          multivalued: true\n")
        pack.metadata_path.write_text(text)
        pack = replace(pack, metadata=load_domain_pack_metadata(pack.metadata_path))
    if getattr(request, "param", None) == "absent":
        from copy import deepcopy
        raw = deepcopy(pack.metadata.metadata)
        binding = raw["validator_bindings"]["active"][0]
        binding["applies_to"]["field_paths"] = ["optional.term"]
        binding["input_fields"] = {"query": {"source": "literal", "value": "fixture lookup"}}
        binding["expected_result_fields"] = {"curie": "optional.term"}
        field = pack.metadata.object_definitions[0].fields[0].model_copy(
            update={"field_path": "optional.term", "metadata": {}})
        definition = pack.metadata.object_definitions[0].model_copy(update={"fields": [field]})
        pack = replace(pack, metadata=pack.metadata.model_copy(update={
            "metadata": raw, "object_definitions": [definition]}))
    groups = ()
    if getattr(request, "param", None) == "authorized":
        from copy import deepcopy

        raw = deepcopy(pack.metadata.metadata)
        from src.lib.config.groups_loader import get_valid_group_ids
        group_id = get_valid_group_ids()[0]
        raw["validator_bindings"]["active"][0]["group_scope"] = {
            "required_any_active_group": [group_id]
        }
        pack = replace(
            pack, metadata=pack.metadata.model_copy(update={"metadata": raw})
        )
        groups = (group_id,)
    env = DomainEnvelope(
        envelope_id=uuid4().hex,
        domain_pack_id=pack.pack_id,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id="one",
                payload=({"genes": [{"aliases": ["eco:1", "eco:2"]}]}
                         if getattr(request, "param", None) == "nested"
                         else {"evidence_code_curies": ["eco:1", "eco:2"]}),
            )
        ],
    )
    if getattr(request, "param", None) == "absent":
        env.extracted_objects[0].payload = {"optional": {}}
    if getattr(request, "param", None) == "three":
        env.extracted_objects = [CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=name,
            payload={"evidence_code_curies": ["eco:" + name]}) for name in "ABC"]
    if getattr(request, "param", None) == "evidence":
        env.extracted_objects[0].evidence_record_ids = ["original-evidence"]
        env.metadata["evidence_records"] = [{"evidence_record_id": "original-evidence",
            "document_id": scope.document_id, "verified_quote": "Original quoted evidence"}]
    env = runtime.capture_extraction(
        scope=scope,
        producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"),
        envelope=env,
        provenance={},
    )
    context = ValidatorRuntimeContext(
        user_id=scope.owner_id,
        document_id=scope.document_id,
        authenticated_groups=groups,
    )
    from src.lib.config.agent_loader import AgentDefinition

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(
            folder_name=agent, agent_id=agent, name=agent, package_id=package
        ),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
        lambda _: pack,
    )
    plan = prepare_validator_dispatch(env, pack, runtime_context=context)
    plan_ref = save_plan(plan, context=context)
    aid = plan.history_binding.artifact_id
    ep = plan.envelope.metadata["review_episode_id"]
    sid = admission.create_scope(
        scope,
        operation_id=uuid4().hex,
        kind="discovered_document_run",
        targets=0,
        episodes=0,
    )
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    adapter = ValidatorAdapter(scope, aid, ep, plan_ref, authenticated_groups=groups)
    return SimpleNamespace(
        plan=plan, adapter=adapter, aid=aid, ep=ep, scope=scope, pack=pack
    )


@pytest.mark.asyncio
async def test_real_adapter_retains_candidates_and_replay_never_calls_provider_again(
    planned, monkeypatch
):
    item = planned
    calls = []

    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:correct"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"),
    )
    await item.adapter.initial()
    await item.adapter.initial()
    assert len(calls) == 2
    with store.SessionLocal() as db:
        completed = list(
            db.scalars(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == item.aid,
                    ValidationFact.kind == "completion",
                )
            )
        )
        candidates = list(
            db.scalars(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == item.aid,
                    ValidationFact.kind == "candidate",
                )
            )
        )
        assert len(completed) == len(candidates) == 2
        assert all(store._content(db, row)["state"] == "completed" for row in completed)
    assert not admission.status(item.scope, item.ep)["ready"]
    assert item.plan.envelope.extracted_objects[0].payload["evidence_code_curies"] == [
        "eco:1",
        "eco:2",
    ]


def test_saved_match_round_trip_and_agent_substitution_refused(planned):
    job = planned.plan.initial_jobs[0]
    assert MATCH.validate_python(serialize_match(job.match)) == job.match
    frozen = runtime.frozen_job_requests(planned.plan.history_binding, [job])[0]
    frozen["request"]["validator_binding_id"] = "unconfigured"
    with pytest.raises(store.HistoryAccessError):
        planned.adapter.job(frozen)


def test_dispatch_survives_resume_and_unknown_submission_is_not_replayed(scope):
    context = SimpleNamespace(
        owner_id=scope.owner_id,
        document_id=scope.document_id,
        session_id=scope.session_id,
        turn_id=uuid4().hex,
    )
    first = chat_dispatch.claim(context, "extract", "query")
    second = chat_dispatch.claim(context, "extract", "query")
    assert first["claimed"] and not second["claimed"] and second["state"] == "started"
    chat_dispatch.complete(context, first["id"], {"output": "result-ref"})
    third = chat_dispatch.claim(context, "extract", "query")
    assert not third["claimed"] and third["result"] == {"output": "result-ref"}


@pytest.mark.asyncio
@pytest.mark.parametrize("review_kind", ["extraction_concern", "operational_refusal"])
async def test_chat_source_checkpoint_and_prep_share_identity_before_and_after_review(
    scope, tmp_path, monkeypatch, review_kind
):
    from src.lib.validation_review.chat import ChatReview
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.curation_workspace import curation_prep_service as prep
    from src.lib.curation_workspace.models import DomainEnvelopeModel

    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "src.lib.redis_client.check_cancel_signal", AsyncMock(return_value=False)
    )

    pack = _multivalued_dispatch_pack(tmp_path)
    env = DomainEnvelope(
        envelope_id=uuid4().hex,
        domain_pack_id=pack.pack_id,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id="one",
                payload={"evidence_code_curies": ["eco:1"]},
            )
        ],
    )
    env = runtime.capture_extraction(
        scope=scope,
        producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"),
        envelope=env,
        provenance={},
    )
    from src.lib.config.agent_loader import AgentDefinition

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(
            folder_name=agent, agent_id=agent, name=agent, package_id=package
        ),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
        lambda _: pack,
    )

    def run(request, binding, **kwargs):
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:selected"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    supervisor = Agent(name="Supervisor", model="gpt-6-astra", instructions="Policy")
    attach_agent_cost_identity(
        supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"}
    )
    chat = ChatReview(
        scope.owner_id,
        scope.document_id,
        scope.session_id,
        uuid4().hex,
        supervisor,
        (),
        "Extract",
    )
    context = ValidatorRuntimeContext(
        user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=()
    )
    from datetime import datetime, timezone
    from src.schemas.execution_provenance import ExtractionExecutionContext

    provenance = ExtractionExecutionContext(
        captured_at=datetime.now(timezone.utc),
        source_kind="chat",
        agent_key="fixture",
        executed_query="Extract",
        document={"document_id": scope.document_id},
    )
    await chat.receive(
        env,
        pack,
        agent_key="fixture",
        adapter_key="fixture",
        tool_name="ask_fixture",
        execution_receipt=None,
        context=context,
        trace_id="trace-source",
        source_metadata={
            "execution_context": provenance.model_dump(mode="json"),
            "specialist_explanation": "Extracted from the supplied paper.",
        },
    )
    records = chat.records()
    assert len(records) == 1 and chat.status()["pending_count"] == 1
    assert records[0].trace_id == "trace-source"
    assert (
        records[0].metadata["specialist_explanation"]
        == "Extracted from the supplied paper."
    )
    assert chat.checkpoint(records[0]).execution_context == provenance
    from src.lib.validation_review.chat import refresh_saved_response
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.resolve_tools", lambda *args: []
    )
    monkeypatch.setattr("src.lib.prompts.assembly.get_all_active_prompts", lambda: {})
    reviewer_calls = []
    concern = "The extraction omitted an observation. Ask the curator before starting fresh extraction."

    async def fake_reviewer(agent, input, **kwargs):
        reviewer_calls.append(agent.name)
        if review_kind == "operational_refusal":
            raise admission.ReviewStopped("configured_validator_changed")
        return SimpleNamespace(final_output=concern)

    monkeypatch.setattr(
        "src.lib.openai_agents.runner.run_agent_with_owned_openai_resources",
        fake_reviewer,
    )
    await chat.resume()
    expected_note = (
        concern
        if review_kind == "extraction_concern"
        else "Review paused: configured validator changed"
    )
    rendered, delivery_status = chat.direct_delivery(current_turn_only=True)
    assert expected_note in rendered
    replay = refresh_saved_response(
        owner_id=scope.owner_id,
        session_id=chat.session_id,
        status=delivery_status,
        original="old extractor prose",
    )
    assert expected_note in replay and len(reviewer_calls) == 1
    assert chat.supervisor_handoff()["review_outcomes"][0]["outcome"][
        "output" if review_kind == "extraction_concern" else "reason"
    ] == (
        concern
        if review_kind == "extraction_concern"
        else "configured_validator_changed"
    )
    reconnected = ChatReview(
        scope.owner_id, scope.document_id, scope.session_id,
        chat.turn_id, supervisor, (), "Extract",
    )
    await reconnected.resume()
    assert len(reviewer_calls) == 1
    assert reconnected.review_notes() == chat.review_notes()
    with store.SessionLocal() as db:
        summaries = db.scalars(
            select(ValidationFact).where(
                ValidationFact.artifact_id == records[0].metadata["chat_review"]["artifact_id"],
                ValidationFact.kind == "review_supervisor_summary",
            )
        ).all()
        assert len(summaries) == 1
    later = ChatReview(
        scope.owner_id, scope.document_id, scope.session_id,
        uuid4().hex, supervisor, (), "Unrelated question",
    )
    assert later.review_notes() == chat.review_notes()
    assert later.review_notes(current_turn_only=True) == []
    assert later.render_review_notes(current_turn_only=True) == ""
    assert chat.review_notes(current_turn_only=True) == chat.review_notes()
    assert len(chat.records()) == 1
    record = records[0]
    assert record.payload_json["extracted_objects"][0]["payload"][
        "evidence_code_curies"
    ] == ["eco:1"]
    with pytest.raises(store.SelectionNotReady):
        prep.ensure_domain_envelope_materialization(record, persist=True)
    request = chat.checkpoint(record)
    ref = record.metadata["chat_review"]
    with store.SessionLocal() as db:
        attempt = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == ref["artifact_id"],
                ValidationFact.kind == "attempt",
            )
        )
        entry = {
            "target_id": attempt.target_id,
            "decision_unit_id": attempt.target_id,
            "expected_version": 0,
            "logical_attempt_id": attempt.id,
            "action": "accept",
            "reason": "Solid answer",
        }
    result = decisions.record_decisions(
        chat.context(record),
        checkpoint_request=request,
        operation_id="review",
        entries=[entry],
    )
    assert result["ready"] and chat.status()["pending_count"] == 0
    presentation = chat.selected_presentation()
    assert "ECO:selected" in presentation and '"eco:1"' not in presentation
    from src.lib.validation_review.chat import refresh_saved_status

    assert (
        refresh_saved_status(
            owner_id=scope.owner_id,
            session_id=chat.session_id,
            saved_status=chat.status(),
        )
        == chat.status()
    )
    updated = chat.checkpoint(record)
    assert (
        updated.expected_revision == 1
        and updated.source_payload_hash == request.source_payload_hash
    )
    assert updated.envelope.extracted_objects[0].payload["evidence_code_curies"] == [
        "ECO:selected"
    ]
    with store.SessionLocal() as db:
        row = db.get(DomainEnvelopeModel, env.envelope_id)
        # The real prep path checks the exact same hash and source/receipt scope.
        prep._validate_existing_materialization_scope(
            row,
            extraction_result=record,
            envelope=updated.envelope,
            project_key=request.project_key,
            adapter_key=record.adapter_key,
            source_payload_hash=request.source_payload_hash,
        )
    assert chat.records()[0].payload_json == record.payload_json


def checkpoint_provider(item):
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
    from src.lib.curation_workspace.models import DomainEnvelopeModel

    source_id = uuid4()

    def load():
        with store.SessionLocal() as db:
            row = db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id)
            return DomainEnvelopeCheckpointRequest(
                project_key="fixture",
                envelope=item.plan.envelope
                if row is None
                else DomainEnvelope.model_validate(row.envelope_json),
                expected_revision=0 if row is None else row.revision,
                document_id=item.scope.document_id,
                adapter_key="fixture",
                source_extraction_result_id=source_id,
            )

    return load


def entries_for(item, action, *, binding_id=None):
    with store.SessionLocal() as db:
        rows = db.scalars(
            select(ValidationFact)
            .where(
                ValidationFact.artifact_id == item.aid, ValidationFact.kind == "attempt"
            )
            .order_by(ValidationFact.sequence)
        ).all()
        return [
            {
                "target_id": row.target_id,
                "decision_unit_id": row.target_id,
                "expected_version": store._current(db, item.aid, row.target_id)[
                    "version"
                ],
                "logical_attempt_id": row.id,
                "action": action,
                "reason": "Reviewer assessment",
            }
            for row in rows
            if binding_id is None
            or store._content(db, row)["input"]["binding_id"] == binding_id
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["dependent"], indirect=True)
async def test_dependent_initial_work_reads_selected_prerequisite_only_after_review(
    planned, monkeypatch
):
    item = planned
    calls = []

    def run(request, binding, **kwargs):
        calls.append((binding.binding_id, dict(request.selected_inputs)))
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:SELECTED"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    checkpoint = checkpoint_provider(item)
    await item.adapter.initial()
    assert len(calls) == 2
    before = await item.adapter.advance(checkpoint)
    assert all(result["state"] == "awaiting_review" for result in before)
    assert len(calls) == 2
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    decision = decisions.record_decisions(
        context,
        checkpoint_request=checkpoint(),
        operation_id="first-review",
        entries=entries_for(item, "accept", binding_id="fixture.evidence_lookup"),
    )
    assert not decision["ready"]
    after = await item.adapter.advance(checkpoint)
    assert all(result["state"] == "completed" for result in after)
    assert [
        values["curie"] for name, values in calls if name == "fixture.dependent"
    ] == ["ECO:SELECTED", "ECO:SELECTED"]
    assert await item.adapter.advance(checkpoint) == []
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_reconsideration_uses_saved_binding_and_durable_queued_work(
    planned, monkeypatch
):
    from src.lib.validation_review import scheduling

    item = planned
    calls = []

    def run(request, binding, **kwargs):
        from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT

        calls.append((request.validator_binding_id, RECONSIDERATION_INPUT.get()))
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:VALUE"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    entries = entries_for(item, "defer")[:1]
    decision = decisions.record_decisions(
        context, checkpoint_request=checkpoint(), operation_id="defer", entries=entries
    )
    scheduling.reconsider(
        context,
        operation_id="retry",
        parent_decision_id=decision["decision_id"],
        target_ids=[entries[0]["target_id"]],
        instructions="Check the supplied identifier carefully",
        evidence_refs=[],
    )
    # Simulate a disconnect after queueing but before executing the retry.
    resumed = ValidatorAdapter(
        item.scope, item.aid, item.ep, item.adapter.plan_ref, authenticated_groups=()
    )
    await resumed.initial()
    await resumed.initial()
    assert len(calls) == 3
    assert calls[-1][0] == "fixture.evidence_lookup"
    assert (
        calls[-1][1]["context"]["instructions"]
        == "Check the supplied identifier carefully"
    )
    supplied = calls[-1][1]["context"]["specialist_context"]
    assert supplied["previous_validation"][0]["assessed_input"]["request"]
    assert supplied["original_entries"]
    assert supplied["current_selected_entries"]
    assert supplied["attempt"]["targets"][entries[0]["target_id"]] == {
        "ordinal": 2, "ceiling": 2, "remaining_after": 0}
    assert calls[-1][1]["history"]["reader"] == "read_supplied_history"
    assert (
        admission.status(item.scope, item.ep)["budgets"][0]["remaining"][
            "extra_attempts"
        ]
        == 15
    )
    assert not admission.status(item.scope, item.ep)["ready"]


@pytest.mark.asyncio
async def test_changed_executable_validator_prompt_is_refused_before_provider(
    planned, monkeypatch
):
    from src.lib.validation_review import scheduling
    from src.lib.validation_review.validator_adapter import pin_validator_agent

    item = planned
    validator = Agent(
        name="Configured validator", model="gpt-test", instructions="Saved policy"
    )
    calls = []

    def run(request, binding, **kwargs):
        pin_validator_agent(validator, request.validator_binding_id)
        calls.append(request.request_id)
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:VALUE"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    entries = entries_for(item, "defer")[:1]
    decision = decisions.record_decisions(
        context,
        checkpoint_request=checkpoint(),
        operation_id="defer-config",
        entries=entries,
    )
    wid = scheduling.reconsider(
        context,
        operation_id="retry-config",
        parent_decision_id=decision["decision_id"],
        target_ids=[entries[0]["target_id"]],
        instructions="Check again",
        evidence_refs=[],
    )
    validator.instructions = "A different unapproved policy"
    with pytest.raises(admission.ReviewStopped, match="configured_validator_changed"):
        await item.adapter.execute(wid)
    assert len(calls) == 2
    assert not admission.status(item.scope, item.ep)["ready"]


@pytest.mark.asyncio
async def test_stop_keeps_actual_late_answer_without_repeating_provider(
    planned, monkeypatch
):
    import asyncio
    import threading

    item = planned
    started, finish = threading.Event(), threading.Event()
    calls = []

    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        started.set()
        assert finish.wait(10)  # Test synchronization only, not an operational limit.
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:LATE"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
        lambda: lambda *a, **kw: pytest.fail("Unexpected batch"),
    )
    task = asyncio.create_task(item.adapter.initial())
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    finish.set()
    # Actual synchronous provider threads finish after cancellation. Wait only
    # for their durable completions, not for any new dispatch or retry.
    async with asyncio.timeout(10):
        while True:
            with store.SessionLocal() as db:
                count = len(
                    list(
                        db.scalars(
                            select(ValidationFact.id).where(
                                ValidationFact.artifact_id == item.aid,
                                ValidationFact.kind == "completion",
                            )
                        )
                    )
                )
            if count == len(calls):
                break
            await asyncio.sleep(0.01)
    previous = len(calls)
    await item.adapter.initial()
    assert len(calls) == previous
    assert admission.status(item.scope, item.ep)["state"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("chosen_action", ["continue_review", "new_extraction"])
async def test_direct_intent_is_supervisor_decision_and_durable_replay(
    planned, monkeypatch, chosen_action
):
    from src.lib.validation_review.chat import ChatReview, DirectChatAction
    from src.lib.observability.cost_context import attach_agent_cost_identity
    from src.lib.openai_agents import runner

    calls = []
    supervisor = Agent(
        name="Resolved supervisor", model="gpt-test", instructions="policy"
    )
    attach_agent_cost_identity(
        supervisor, {"agent_id": "supervisor", "agent_role": "supervisor"}
    )
    extractor = Agent(name="Only chosen extractor")
    attach_agent_cost_identity(extractor, {"agent_id": "chosen-extractor"})
    context = ChatReview(
        planned.scope.owner_id,
        planned.scope.document_id,
        planned.scope.session_id,
        "followup-turn",
        supervisor,
        (),
        "continue",
    )
    monkeypatch.setattr(context, "records", lambda: [object()])
    monkeypatch.setattr(context, "status", lambda: {"pending_count": 1, "results": []})

    async def run(agent, input, **kwargs):
        import json

        calls.append(json.loads(input))
        assert agent.tools == [] and agent.handoffs == []
        assert (
            agent.model == supervisor.model
            and agent.model_settings == supervisor.model_settings
        )
        return SimpleNamespace(
            final_output=DirectChatAction(
                action=chosen_action, explanation="Supervisor intent decision."
            )
        )

    monkeypatch.setattr(runner, "run_agent_with_owned_openai_resources", run)
    first = await context.direct_action(extractor, [])
    second = await context.direct_action(extractor, [])
    assert first == second and first.action == chosen_action and len(calls) == 1
    assert calls[0]["chosen_extractor"] == "chosen-extractor"


@pytest.mark.asyncio
async def test_multitarget_retry_retains_first_answer_when_second_is_not_admitted(
    planned, monkeypatch
):
    from src.lib.validation_review import scheduling
    from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT

    item = planned
    retry_calls = []

    def run(request, binding, **kwargs):
        if RECONSIDERATION_INPUT.get():
            from src.lib.validation_review.provider import ACTIVE_BUDGET

            ACTIVE_BUDGET.get().execution.provider_admitted = True
            retry_calls.append(request.request_id)
            if len(retry_calls) == 2:
                raise admission.ReviewStopped(
                    "budget_exhausted", resource="reconsideration_tokens"
                )
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:ANSWER"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    await item.adapter.initial()
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    entries = entries_for(item, "defer")
    decision = decisions.record_decisions(
        context,
        checkpoint_request=checkpoint_provider(item)(),
        operation_id="defer-both",
        entries=entries,
    )
    wid = scheduling.reconsider(
        context,
        operation_id="retry-both",
        parent_decision_id=decision["decision_id"],
        target_ids=[entry["target_id"] for entry in entries],
        instructions="Check both",
        evidence_refs=[],
    )
    with pytest.raises(admission.ReviewStopped):
        await item.adapter.execute(wid)
    assert len(retry_calls) == 2
    with store.SessionLocal() as db:
        completed = [
            store._content(db, row)
            for row in db.scalars(
                select(ValidationFact).where(
                    ValidationFact.artifact_id == item.aid,
                    ValidationFact.kind == "completion",
                )
            )
        ]
    assert sum(body["state"] == "completed" for body in completed) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["authorized"], indirect=True)
async def test_changed_groups_refuse_queued_work_without_provider_or_uncertain_state(
    planned, monkeypatch
):
    from src.lib.validation_review import scheduling
    from src.models.sql.validation_review import ValidationReviewWork

    item = planned
    calls = []

    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        return {
            **_result_payload(request, resolved_values={"curie": "ECO:VALUE"}),
            "resolved_objects": [],
        }

    monkeypatch.setattr(
        "src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
        lambda: run,
    )
    await item.adapter.initial()
    assert len(calls) == 2
    entries = entries_for(item, "defer")[:1]
    context = decisions.ReviewContext(item.scope, item.ep, "supervisor")
    decision = decisions.record_decisions(
        context,
        checkpoint_request=checkpoint_provider(item)(),
        operation_id="defer-auth",
        entries=entries,
    )
    wid = scheduling.reconsider(
        context,
        operation_id="retry-auth",
        parent_decision_id=decision["decision_id"],
        target_ids=[entries[0]["target_id"]],
        instructions="Check",
        evidence_refs=[],
    )
    resumed = ValidatorAdapter(
        item.scope, item.aid, item.ep, item.adapter.plan_ref, authenticated_groups=()
    )
    with pytest.raises(admission.ReviewStopped, match="validator_not_authorized"):
        await resumed.initial()
    assert len(calls) == 2
    with store.SessionLocal() as db:
        row = db.get(ValidationReviewWork, wid)
        assert row.state == "completed" and row.outcome["state"] == "not_admitted"
    await (
        resumed.initial()
    )  # Completed work is not rebuilt/revalidated after access changes.
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", ["three"], indirect=True)
async def test_workspace_b_round_calls_only_b_and_retains_a_c(planned, monkeypatch):
    from datetime import datetime, timezone
    from src.lib.curation_workspace import review_operations as ops
    from src.lib.curation_workspace.models import CurationCandidate, CurationReviewSession, DomainEnvelopeModel
    from src.models.sql.validation_review import WorkspaceReviewOperation
    from src.schemas.curation_workspace import CurationCandidateSource
    from src.lib.validation_history import selections
    from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT
    item = planned
    calls = []
    contexts = []
    def run(request, binding, **kwargs):
        calls.append(request.target.model_dump(mode="json"))
        contexts.append(RECONSIDERATION_INPUT.get())
        return {**_result_payload(request, resolved_values={"curie": "ECO:correct"}), "resolved_objects": []}
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, "supervisor"),
        checkpoint_request=checkpoint(), operation_id="accept-original", entries=entries_for(item, "accept"))
    before = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert before['ready'] and len(calls) == 3
    with store.SessionLocal.begin() as db:
        session = CurationReviewSession(adapter_key='fixture', document_id=item.scope.document_id,
            created_by_id=item.scope.owner_id, prepared_at=datetime.now(timezone.utc))
        db.add(session)
        db.flush()
        db.get(DomainEnvelopeModel, item.plan.envelope.envelope_id).session_id = session.id
        candidate = CurationCandidate(session=session, adapter_key='fixture', source=CurationCandidateSource.MANUAL,
            envelope_id=item.plan.envelope.envelope_id, object_id='B', envelope_revision=1)
        db.add(candidate)
        db.flush()
        saved = ops.submit(db, owner_id=item.scope.owner_id, session_id=str(session.id), request_id=str(uuid4()),
                           candidate_ids=[str(candidate.id)], guidance='Please reconsider B', groups=[])
        row = db.get(WorkspaceReviewOperation, saved['operation_id'])
        row.state, row.lease_token = 'preparing', 'test-lease'
    from auth_runtime.base import AuthPrincipal
    monkeypatch.setattr("src.lib.curation_workspace.review_authorization.get_current_subject_resolver",
        lambda: lambda subject: AuthPrincipal(subject=subject, provider="fixture", groups=[]))
    owner, groups, artifacts = ops.prepare(saved['operation_id'], 'test-lease')
    current = artifacts[0]
    adapter = ValidatorAdapter(item.scope, item.aid, current['episode_id'], current['plan_ref'], authenticated_groups=groups)
    assert len(adapter.plan['initial_requests']) == 1
    await adapter.initial()
    assert len(calls) == 4
    assert calls[-1] == adapter.plan['initial_requests'][0]['request']['target']
    assert contexts[-1] is not None
    assert 'Please reconsider B' in str(contexts[-1])
    # Exact history reaches the specialist, but untouched records are not dispatched.
    await adapter.initial()
    assert len(calls) == 4
    with store.SessionLocal() as db:
        attempts = list(db.scalars(select(ValidationFact).where(ValidationFact.artifact_id == item.aid, ValidationFact.kind == 'attempt')))
        new_attempt = next(f for f in attempts if store._content(db, f)['input']['review_episode_id'] == current['episode_id'])
        version = store._current(db, item.aid, new_attempt.target_id)['version']
    decisions.record_decisions(decisions.ReviewContext(item.scope, current['episode_id'], 'supervisor'),
        checkpoint_request=ops.checkpoint(owner, current, groups), operation_id='accept-B', entries=[{
            'target_id': new_attempt.target_id, 'decision_unit_id': new_attempt.target_id, 'expected_version': version,
            'logical_attempt_id': new_attempt.id, 'action': 'accept', 'reason': 'Reviewed B again'}])
    after = selections.read_manifest(item.scope, item.aid, episode_id=current['episode_id'])
    assert after['review_ready']
    for old in before['units']:
        if old['target_id'] == new_attempt.target_id:
            continue
        retained = next(unit for unit in after['units'] if unit['target_id'] == old['target_id'])
        for key in ('selected', 'selection_id', 'version'):
            assert retained[key] == old[key]


@pytest.mark.asyncio
async def test_curator_round_reuses_accepted_dependency_until_supervisor_explicitly_reconsiders(planned, monkeypatch):
    from src.lib.validation_history import selections
    from src.lib.validation_review import scheduling
    item = planned
    calls = []
    def run(request, binding, **kwargs):
        calls.append(request.request_id)
        return {**_result_payload(request, resolved_values={'curie': 'ECO:correct'}), 'resolved_objects': []}
    monkeypatch.setattr('src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner', lambda: run)
    monkeypatch.setattr('src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner', lambda: lambda *a, **k: pytest.fail('Unexpected batch'))
    await item.adapter.initial()
    checkpoint = checkpoint_provider(item)
    old_entries = entries_for(item, 'accept')
    decisions.record_decisions(decisions.ReviewContext(item.scope, item.ep, 'supervisor'),
        checkpoint_request=checkpoint(), operation_id='accept-old', entries=old_entries)
    dependency, requested = [entry['target_id'] for entry in old_entries]
    with store.SessionLocal() as db:
        original = store._content(db, store._fact(db, item.aid, item.ep))
    episode = selections.start_episode(item.scope, item.aid, episode={
        'episode_id': str(uuid4()), 'reason': 'Record plus previously reviewed dependency',
        'obligations': original['obligations'], 'review_target_ids': [dependency, requested],
        'retained_episode_ids': {dependency: item.ep}})
    root = admission.create_scope(item.scope, operation_id=str(uuid4()), kind='workspace_operation', targets=2, episodes=1)
    child = admission.create_scope(item.scope, operation_id=str(uuid4()), kind='document_run', targets=2, episodes=1, parent_id=root)
    admission.attach_episode(item.scope, artifact_id=item.aid, episode_id=episode, scope_id=child,
        predecessor_id=item.ep, renewal_actor_id=item.scope.owner_id, renewal_reason='Explicit curator round', curator_round=True)
    plan = replace(item.plan, envelope=item.plan.envelope.model_copy(deep=True),
                   initial_jobs=tuple(replace(job, history_episode_id=episode) for job in item.plan.initial_jobs))
    plan.envelope.metadata['review_episode_id'] = episode
    plan_ref = save_plan(plan, context=item.adapter.context)
    adapter = ValidatorAdapter(item.scope, item.aid, episode, plan_ref, authenticated_groups=())
    await adapter.initial()
    assert len(calls) == 3  # Two original calls, only the requested target in the fresh round.
    ctx = decisions.ReviewContext(item.scope, episode, 'supervisor')
    entry = next(entry for entry in entries_for(item, 'defer') if entry['target_id'] == dependency)
    parent = decisions.record_decisions(ctx, checkpoint_request=checkpoint(), operation_id='question-dependency', entries=[entry])
    adapter.prepare_inputs(checkpoint)
    wid = scheduling.reconsider(ctx, operation_id='reconsider-dependency', parent_decision_id=parent['decision_id'],
        target_ids=[dependency], instructions='Reconsider the dependency needed by this record', evidence_refs=[],
        refresh_inputs=adapter.refresh_inputs, rebind_receipt=adapter.rebind_receipt)
    await adapter.execute(wid)
    assert len(calls) == 4
