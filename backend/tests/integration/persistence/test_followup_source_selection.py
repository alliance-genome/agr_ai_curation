"""Atomic selected-source changes retain baseline and stale only affected inputs."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.lib.validation_history import store, selections
from src.lib.validation_review import admission, extraction_scheduling, extraction_results, source_selection, work
from src.lib.validation_review.decisions import ReviewContext
from src.models.sql.validation_history import ValidationFact
from tests.integration.persistence.test_chat_review_adapter import planned as planned, scope as scope, migrated as migrated
from tests.integration.persistence.test_extraction_followup_admission import request_args


def retained_proposal(item, *, merge=False):
    args = request_args(item)
    entry = item.plan.envelope.extracted_objects[0]
    refs = entry.metadata["validation_history"]
    ids = refs["elements"]["evidence_code_curies"]
    values = ["eco:new"] if merge else ["eco:new", "eco:2"]
    relationships = ([{"item_key": "merged", "predecessor_ids": ids, "relationship": "merge", "reason": "Same reported assertion"}]
        if merge else [{"item_key": "revised", "predecessor_ids": ids[:1], "relationship": "revise", "reason": "New evidence"},
            {"item_key": "unchanged", "predecessor_ids": ids[1:], "relationship": "retain", "reason": "Still supported"}])
    context = ReviewContext(item.scope, item.ep, "supervisor")
    wid = extraction_scheduling.request(context, **args)
    claim = work.claim(item.scope, wid)
    assert claim is not None
    work.mark_submitted(item.scope, wid, claim["lease_token"])
    result = {"entries": [{"entry_id": refs["target_id"], "outcome": "proposed_correction",
        "assessment": "The Methods clarify the entry", "remaining_uncertainty": "", "evidence_record_ids": [],
        "proposed_values": [{"field_path": "evidence_code_curies", "value": values, "evidence_record_ids": [],
            "collection_mappings": [{"collection_path": "evidence_code_curies",
                "source_collection_id": refs["fields"]["evidence_code_curies"], "items": relationships,
                "retired_item_ids": [], "retirement_reason": ""}]}]}]}
    result = extraction_results.retain(item.scope, wid, claim["lease_token"], result, evidence_records=[])
    work.finish(item.scope, wid, claim["lease_token"], outcome=result)
    return context, result["contributions"][0]


def test_explicit_selection_changes_only_affected_input_and_rejects_stale_dispatch(planned):
    item = planned
    original = store.read_baseline(item.scope, item.aid)
    requests = deepcopy(item.adapter.plan["initial_requests"])
    invocation = store.begin_execution(item.scope, item.aid, operation_id=uuid4().hex,
        receipt={"fixture": True}, requests=requests)
    context, ref = retained_proposal(item)

    def inputs(db, envelope):
        result = {}
        for request, value in zip(requests, envelope.extracted_objects[0].payload["evidence_code_curies"], strict=True):
            frozen = deepcopy(request)
            frozen["request"]["selected_inputs"]["curie"] = value
            result[frozen["target_id"]] = frozen
        return result

    published = []
    with store.SessionLocal.begin() as db:
        digest = selections.source_state_digest(db, item.aid)
        result = source_selection.select_in_transaction(db, context, operation_id="choose-source",
            contribution_ref=ref, field_paths=["evidence_code_curies"], evidence_ids=[], expected_source_sha=digest,
            reason="Approve the proposed correction", source_envelope=item.plan.envelope,
            rebuild_inputs=inputs, publish=lambda db, env, reqs, changed, selection_id: published.append(env) or {})
        assert [change["target_id"] for change in result["affected_bindings"]] == [requests[0]["target_id"]]
        assert selections.source_state_digest(db, item.aid) != digest
    assert published[0].extracted_objects[0].payload["evidence_code_curies"] == ["eco:new", "eco:2"]
    assert store.read_baseline(item.scope, item.aid) == original
    from src.lib.validation_history.projection import read_dispatch_source
    refreshed = read_dispatch_source(item.plan.history_binding, item.plan.envelope)
    assert refreshed.extracted_objects[0].payload["evidence_code_curies"] == ["eco:new", "eco:2"]
    with store.SessionLocal() as db:
        attempts = [store._content(db, store._fact(db, item.aid, aid)) for aid in invocation["attempt_ids"]]
        assert not store._attempt_is_current(db, item.scope, item.aid, attempts[0])
        assert store._attempt_is_current(db, item.scope, item.aid, attempts[1])
    with pytest.raises(store.HistoryConflict, match="source input version is stale"):
        store.begin_execution(item.scope, item.aid, operation_id=uuid4().hex, receipt={}, requests=requests[:1])


def test_merge_inherits_attempts_without_deleting_predecessors(planned):
    from src.lib.validation_review.attempt_lineage import counts
    from src.lib.validation_review.source_projection import plan
    from src.schemas.validation_followup import ProposedSourceValue

    item = planned
    requests = deepcopy(item.adapter.plan["initial_requests"])
    store.begin_execution(item.scope, item.aid, operation_id=uuid4().hex, receipt={}, requests=requests)
    context, ref = retained_proposal(item, merge=True)
    with store.SessionLocal.begin() as db:
        contribution = store._content(db, store._fact(db, item.aid, ref["fact_id"]))
        planned_source = plan(item.plan.envelope.extracted_objects[0].model_dump(mode="json"),
            [ProposedSourceValue.model_validate(contribution["proposal"]["proposed_values"][0])], contribution_id=ref["fact_id"])
        for addition in planned_source["additions"]:
            store._append(db, item.aid, operation_id="fixture:" + addition["id"], kind="target",
                fact_id=addition["id"], parent_id=addition["parent_id"], body=addition["body"])
        new_id = planned_source["entry"]["metadata"]["validation_history"]["elements"]["evidence_code_curies"][0]
        binding = store.register_target_in_transaction(db, item.aid, identity="fixture-merged-binding",
            original={"binding_id": requests[0]["binding_id"]}, parent_target_id=new_id)
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        assert counts(db, ep, [binding])[binding] == 1
    merged_request = {**requests[0], "target_id": binding}
    store.begin_execution(item.scope, item.aid, operation_id=uuid4().hex, receipt={}, requests=[merged_request])
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        assert counts(db, ep, [binding])[binding] == 2
        assert all(store._fact(db, item.aid, old, kind="target") for old in planned_source["retired_ids"])


@pytest.mark.asyncio
async def test_adapter_selects_source_and_revalidates_only_changed_binding(planned, monkeypatch):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    from src.lib.validation_review.validator_adapter import RECONSIDERATION_INPUT

    item = planned
    original = store.read_baseline(item.scope, item.aid)
    calls = []

    def run(request, binding, **kwargs):
        calls.append((deepcopy(request.selected_inputs), RECONSIDERATION_INPUT.get()))
        return {**_result_payload(request, resolved_values={"curie": "ECO:resolved"}), "resolved_objects": []}

    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    await item.adapter.initial()
    assert len(calls) == 2
    context, contribution = retained_proposal(item)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    arguments = dict(operation_id="adapter-select-source", contribution_ref=contribution,
        field_paths=["evidence_code_curies"], evidence_ids=[], expected_source_sha=digest,
        reason="The extractor clarified the first item from Methods")
    result = await item.adapter.select_source(context, checkpoint, **arguments)
    assert len(result["affected_bindings"]) == 1
    assert checkpoint().envelope.extracted_objects[0].payload["evidence_code_curies"] == ["eco:new", "eco:2"]
    assert await item.adapter.select_source(context, checkpoint, **arguments) == result
    target = result["affected_bindings"][0]["target_id"]
    assessment = dict(operation_id="assess-selected-source", selection_id=result["selection_id"],
        target_ids=[target], instructions="Assess the newly supplied clarification independently")
    outcome = await item.adapter.assess_source(context, **assessment)
    assert outcome["state"] == "completed"
    assert len(calls) == 3
    assert calls[-1][0]["curie"] == "eco:new"
    assert calls[-1][1] is not None
    supplied = calls[-1][1]["context"]["specialist_context"]
    assert supplied["original_entries"][0]["value"]["payload"]["evidence_code_curies"] == ["eco:1", "eco:2"]
    assert len(supplied["previous_validation"]) == 2
    assert supplied["contributions"][0]["reference"] == contribution
    assert supplied["supervisor_request"]["instructions"] == assessment["instructions"]
    replayed = await item.adapter.assess_source(context, **assessment)
    assert replayed["work_id"] == outcome["work_id"]
    assert replayed["state"] == "not_claimed"
    assert len(calls) == 3
    assert store.read_baseline(item.scope, item.aid) == original


@pytest.mark.asyncio
async def test_adapter_merge_preserves_history_and_inherits_consumed_attempts(planned, monkeypatch):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    from src.lib.validation_review.attempt_lineage import counts

    item = planned
    calls = []
    def run(request, binding, **kwargs):
        calls.append(request.selected_inputs)
        return {**_result_payload(request, resolved_values={"curie": "ECO:resolved"}), "resolved_objects": []}
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner", lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    await item.adapter.initial()
    context, contribution = retained_proposal(item, merge=True)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    checkpoint = checkpoint_provider(item)
    result = await item.adapter.select_source(context, checkpoint, operation_id="merge-items",
        contribution_ref=contribution, field_paths=["evidence_code_curies"], evidence_ids=[],
        expected_source_sha=digest, reason="Approve the extractor's explicit merge")
    assert checkpoint().envelope.extracted_objects[0].payload["evidence_code_curies"] == ["eco:new"]
    assert len(result["affected_bindings"]) == 1
    target = result["affected_bindings"][0]["target_id"]
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        assert counts(db, ep, [target])[target] == 1
    outcome = await item.adapter.assess_source(context, operation_id="assess-merge",
        selection_id=result["selection_id"], target_ids=[target], instructions="Assess the merged assertion")
    assert outcome["state"] == "completed"
    assert len(calls) == 3
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        assert counts(db, ep, [target])[target] == 2
        assert len(list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid, ValidationFact.kind == "attempt")))) == 3
    with pytest.raises(admission.ReviewStopped, match="scientific_attempts_exhausted"):
        await item.adapter.assess_source(context, operation_id="forbidden-third-assessment",
            selection_id=result["selection_id"], target_ids=[target], instructions="Try once more")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_queued_old_input_is_stopped_before_provider(planned, monkeypatch):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from src.lib.validation_review import scheduling
    from src.models.sql.validation_review import ValidationReviewWork

    item = planned
    requests = item.adapter.plan["initial_requests"][:1]
    jobs = [item.adapter.job(request) for request in requests]
    wid = scheduling.initial(item.scope, item.ep, operation_id="queued-before-correction",
        requests=requests, receipt=item.adapter.receipt(jobs, batch_key=None))
    context, contribution = retained_proposal(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    await item.adapter.select_source(context, checkpoint_provider(item), operation_id="correct-before-dispatch",
        contribution_ref=contribution, field_paths=["evidence_code_curies"], evidence_ids=[],
        expected_source_sha=digest, reason="Clarified before the queued validator ran")
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
                        lambda: pytest.fail("Stale work must not reach the provider"))
    with pytest.raises(admission.ReviewStopped, match="source_input_changed"):
        await item.adapter.execute(wid)
    with store.SessionLocal() as db:
        row = db.get(ValidationReviewWork, wid)
        assert row.state == "completed"
        assert row.outcome["state"] == "not_admitted"
        assert row.outcome["reason"] == "source_input_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize('planned', ['nested'], indirect=True)
async def test_split_reparents_retained_and_revised_children_with_attempt_history(planned, monkeypatch):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    from src.lib.validation_review.attempt_lineage import counts
    from src.lib.validation_history.projection import read_dispatch_source
    from tests.unit.lib.validation_review.test_item_relationships import relation

    item = planned
    baseline = store.read_baseline(item.scope, item.aid)
    calls = []
    def run(request, binding, **kwargs):
        calls.append(request.selected_inputs)
        return {**_result_payload(request, resolved_values={'curie': 'ECO:resolved'}), 'resolved_objects': []}
    monkeypatch.setattr('src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner', lambda: run)
    monkeypatch.setattr('src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner',
                        lambda: lambda *a, **kw: pytest.fail('Unexpected batch'))
    await item.adapter.initial()
    assert len(calls) == 2
    args = request_args(item)
    context = ReviewContext(item.scope, item.ep, 'supervisor')
    wid = extraction_scheduling.request(context, **args)
    claim = work.claim(item.scope, wid)
    work.mark_submitted(item.scope, wid, claim['lease_token'])
    refs = item.plan.envelope.extracted_objects[0].metadata['validation_history']
    gene, = refs['elements']['genes']
    a, b = refs['elements']['genes[0].aliases']
    expected = [{'aliases': ['eco:1']}, {'aliases': ['eco:new']}]
    mappings = [
        {'collection_path': 'genes', 'source_collection_id': refs['fields']['genes'],
         'items': [relation('first', [gene], 'split'), relation('second', [gene], 'split')]},
        {'collection_path': 'genes[0].aliases', 'source_collection_id': refs['fields']['genes[0].aliases'],
         'items': [relation('a', [a], 'retain')]},
        {'collection_path': 'genes[1].aliases', 'source_collection_id': refs['fields']['genes[0].aliases'],
         'items': [relation('b', [b], 'revise')]},
    ]
    for mapping in mappings:
        mapping.update(retired_item_ids=[], retirement_reason='')
    result = extraction_results.retain(item.scope, wid, claim['lease_token'], {'entries': [{
        'entry_id': refs['target_id'], 'outcome': 'proposed_correction', 'assessment': 'Two separate entities',
        'remaining_uncertainty': '', 'evidence_record_ids': [], 'proposed_values': [{
            'field_path': 'genes', 'value': expected, 'evidence_record_ids': [], 'collection_mappings': mappings}]}]}, evidence_records=[])
    work.finish(item.scope, wid, claim['lease_token'], outcome=result)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    checkpoint = checkpoint_provider(item)
    selected = await item.adapter.select_source(context, checkpoint, operation_id='split-with-intact-children',
        contribution_ref=result['contributions'][0], field_paths=['genes'], evidence_ids=[],
        expected_source_sha=digest, reason='Approve the explicit relationships')
    source = read_dispatch_source(item.plan.history_binding, checkpoint().envelope)
    assert source.extracted_objects[0].payload['genes'] == expected
    new_refs = source.extracted_objects[0].metadata['validation_history']
    children = [new_refs['elements'][f'genes[{index}].aliases'][0] for index in range(2)]
    assert not set(children) & {a, b}
    targets = [entry['target_id'] for entry in selected['affected_bindings']]
    assert len(targets) == 2
    with store.SessionLocal.begin() as db:
        for child, predecessor in zip(children, [a, b], strict=True):
            fact = store._fact(db, item.aid, child, kind='target')
            assert store._content(db, fact)['origin']['predecessor_ids'] == [predecessor]
            store._require_active_target(db, item.aid, child)
            assert store._fact(db, item.aid, predecessor, kind='target')
            with pytest.raises(store.HistoryConflict):
                store._require_active_target(db, item.aid, predecessor)
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        assert set(counts(db, ep, targets).values()) == {1}
    outcome = await item.adapter.assess_source(context, operation_id='assess-split', selection_id=selected['selection_id'],
        target_ids=targets, instructions='Assess the two clarified entities')
    assert outcome['state'] == 'completed'
    assert len(calls) == 4
    with store.SessionLocal.begin() as db:
        ep, _ = admission.lock_episode(db, item.scope, item.ep)
        assert set(counts(db, ep, targets).values()) == {2}
    assert store.read_baseline(item.scope, item.aid) == baseline


@pytest.mark.parametrize("planned", ["absent"], indirect=True)
@pytest.mark.asyncio
async def test_source_publication_retires_absence_coverage_and_rejects_late_answer(planned):
    from tests.integration.persistence.test_chat_review_adapter import checkpoint_provider
    from src.lib.validation_history.projection import read_published_projection

    item = planned
    original = store.read_baseline(item.scope, item.aid)
    frozen = item.adapter.plan["initial_requests"]
    assert len(frozen) == 1
    old_target = frozen[0]["target_id"]
    execution = store.begin_execution(item.scope, item.aid, operation_id=uuid4().hex,
                                     receipt={"fixture": True}, requests=frozen)
    args = request_args(item)
    root = args["entry_ids"][0]
    context = ReviewContext(item.scope, item.ep, "supervisor")
    wid = extraction_scheduling.request(context, **args)
    claim = work.claim(item.scope, wid)
    work.mark_submitted(item.scope, wid, claim["lease_token"])
    proposal = {"entries": [{"entry_id": root, "outcome": "proposed_correction",
        "assessment": "The Methods clarify the omitted value", "remaining_uncertainty": "",
        "evidence_record_ids": [], "proposed_values": [{"field_path": "optional",
            "value": {"term": "new extracted wording"}, "evidence_record_ids": [],
            "collection_mappings": []}]}]}
    retained = extraction_results.retain(item.scope, wid, claim["lease_token"], proposal, evidence_records=[])
    work.finish(item.scope, wid, claim["lease_token"], outcome=retained)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    result = await item.adapter.select_source(context, checkpoint,
        operation_id="populate-absent-source", contribution_ref=retained["contributions"][0],
        field_paths=["optional"], evidence_ids=[], expected_source_sha=digest,
        reason="Explicitly use the extractor clarification")
    assert not result["publication"]["ready"]
    manifest = selections.read_manifest(item.scope, item.aid, episode_id=item.ep)
    assert len(manifest["units"]) == 1
    assert manifest["units"][0]["target_id"] != old_target
    current = checkpoint().envelope
    projection, pending = read_published_projection(item.scope, current.metadata["selected_projection"])
    assert projection.extracted_objects[0].payload == {"optional": {"term": "new extracted wording"}}
    assert not pending["ready"]
    late = store.complete_attempt(item.scope, item.aid, execution["attempt_ids"][0],
        state="completed", result={"status": "resolved", "resolved_values": {"curie": "obsolete answer"}},
        diagnostics={})
    assert late is not None  # Retain the late answer as history, never as current.
    with store.SessionLocal() as db:
        assert not store._current(db, item.aid, old_target)["active"]
        attempt = store._content(db, store._fact(db, item.aid, execution["attempt_ids"][0]))
        assert not store._attempt_is_current(db, item.scope, item.aid, attempt)
    assert store.read_baseline(item.scope, item.aid) == original
