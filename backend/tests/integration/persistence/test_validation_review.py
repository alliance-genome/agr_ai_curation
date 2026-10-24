"""Durable quota and delivery tests use real commits and concurrent sessions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.lib.validation_history import store as h
from src.lib.validation_review import admission as a, work as w
from src.models.sql.validation_review import ValidationReviewWork as Work
from tests.integration.persistence.test_validation_history import (
    scope,
    migrated,
    artifact,
)
from tests.integration.persistence.test_selected_value_authority import binding, episode


def admitted(scope, *, parent=None, operation=None):
    aid = artifact(scope)
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    sid = a.create_scope(
        scope,
        operation_id=operation or uuid4().hex,
        kind="document_run",
        targets=1,
        episodes=1,
        parent_id=parent,
    )
    a.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    return aid, ep, sid


def queue(scope, ep):
    with h.SessionLocal.begin() as db:
        return w.enqueue_in_transaction(
            db,
            scope,
            ep,
            operation_id=uuid4().hex,
            payload={"request": "frozen", "invocation": {"attempt_ids": []}},
            charges={"extra_attempts": 1},
        )


def expire(work_id):
    with h.SessionLocal.begin() as db:
        row = db.get(Work, work_id)
        row.lease_until = a.now() - timedelta(seconds=1)


def test_concurrent_reservation_cannot_overspend(scope):
    _, ep, _ = admitted(scope)

    def reserve(_):
        try:
            return a.reserve(
                scope,
                ep,
                operation_id=uuid4().hex,
                charges={"extra_attempts": 1},
                request={},
            )
        except a.ReviewStopped as exc:
            assert exc.resource == "extra_attempts"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(value is not None for value in results) == 1
    assert a.status(scope, ep)["budgets"][-1]["remaining"]["extra_attempts"] == 0


def test_scope_retry_freezes_policy_across_config_change(scope, monkeypatch):
    operation = uuid4().hex
    _, _, sid = admitted(scope, operation=operation)
    monkeypatch.setenv("VALIDATION_REVIEW_EXTRA_PER_RUN", "200")
    assert (
        a.create_scope(
            scope, operation_id=operation, kind="document_run", targets=1, episodes=1
        )
        == sid
    )


def test_unknown_usage_retains_charge_then_reconciles_once(scope):
    _, ep, _ = admitted(scope)
    rid = a.reserve(
        scope, ep, operation_id="call", charges={"reviewer_tokens": 1000}, request={}
    )
    a.reconcile(scope, ep, rid, actual=None)
    assert a.status(scope, ep)["budgets"][-1]["remaining"]["reviewer_tokens"] == 999000
    a.reconcile(scope, ep, rid, actual={"reviewer_tokens": 200})
    a.reconcile(scope, ep, rid, actual={"reviewer_tokens": 200})
    assert a.status(scope, ep)["budgets"][-1]["remaining"]["reviewer_tokens"] == 999800
    with pytest.raises(h.HistoryConflict):
        a.reconcile(scope, ep, rid, actual={"reviewer_tokens": 100})


def test_overrun_is_retained_and_stops_new_work(scope):
    _, ep, _ = admitted(scope)
    rid = a.reserve(
        scope, ep, operation_id="call", charges={"reviewer_tokens": 10}, request={}
    )
    a.reconcile(scope, ep, rid, actual={"reviewer_tokens": 11})
    assert a.status(scope, ep)["state"] == "provider_budget_overrun"
    with pytest.raises(a.ReviewStopped):
        queue(scope, ep)


def test_claim_recovery_fences_old_worker_and_submission_is_not_repeated(scope):
    _, ep, _ = admitted(scope)
    wid = queue(scope, ep)
    first = w.claim(scope, wid)
    assert w.claim(scope, wid) is None
    expire(wid)
    assert w.recover(scope, wid) == "queued"
    second = w.claim(scope, wid)
    assert second["lease_token"] != first["lease_token"]
    with pytest.raises(h.HistoryConflict):
        w.mark_submitted(scope, wid, first["lease_token"])
    w.mark_submitted(scope, wid, second["lease_token"])
    expire(wid)
    assert w.recover(scope, wid) == "uncertain"
    assert w.claim(scope, wid) is None
    w.finish(scope, wid, second["lease_token"], outcome={"answer": "late"})
    w.finish(scope, wid, second["lease_token"], outcome={"answer": "late"})
    with pytest.raises(h.HistoryConflict):
        w.finish(scope, wid, second["lease_token"], outcome={"answer": "changed"})
    assert not a.status(scope, ep)["ready"]


def test_initial_attempt_and_outbox_are_atomic_and_idempotent(scope):
    from src.lib.validation_review import scheduling
    from src.models.sql.validation_history import ValidationFact

    aid, ep, _ = admitted(scope)
    with h.SessionLocal() as db:
        target = db.scalar(
            select(ValidationFact).where(
                ValidationFact.artifact_id == aid, ValidationFact.kind == "target"
            )
        )
        tid = target.id
    request = {
        "target_id": tid,
        "binding_id": "fixture",
        "review_episode_id": ep,
        "request": {"evidence": "original"},
    }
    first = scheduling.initial(
        scope,
        ep,
        operation_id="initial",
        requests=[request],
        receipt={"agent_revision": "saved-custom-7"},
    )
    assert (
        scheduling.initial(
            scope,
            ep,
            operation_id="initial",
            requests=[request],
            receipt={"agent_revision": "saved-custom-7"},
        )
        == first
    )
    with pytest.raises(h.HistoryConflict, match="initial attempt"):
        scheduling.initial(
            scope,
            ep,
            operation_id="another",
            requests=[request],
            receipt={"agent_revision": "saved-custom-7"},
        )
    with h.SessionLocal() as db:
        attempts = db.scalars(
            select(ValidationFact).where(
                ValidationFact.artifact_id == aid, ValidationFact.kind == "attempt"
            )
        ).all()
        assert len(attempts) == 1


def test_exact_history_is_readable_in_many_bounded_pages(scope, monkeypatch):
    from src.lib.validation_review import reads

    monkeypatch.setenv("VALIDATION_REVIEW_PACKET_BYTES", "512")
    aid, ep, _ = admitted(scope)
    with h.SessionLocal.begin() as db:
        fact = h._append(
            db, aid, operation_id="large", kind="test", body={"text": 'α"\\' * 2000}
        )
        fid, sha = fact.id, fact.body_sha
    offset, chunks = 0, []
    while offset is not None:
        page = reads.read(scope, ep, fact_id=fid, sha256=sha, offset=offset)
        assert reads._wire_size(page) <= 512
        chunks.append(page["content"])
        offset = page["next_offset"]
    import json

    assert json.loads("".join(chunks)) == {"text": 'α"\\' * 2000}
    assert len(chunks) > 10


def test_reconsideration_retains_saved_custom_receipt_and_caps_attempts(scope):
    from src.lib.validation_review import scheduling
    from src.lib.validation_review.decisions import ReviewContext
    from src.lib.validation_history import selections
    from src.models.sql.validation_history import ValidationFact

    aid, ep, _ = admitted(scope)
    with h.SessionLocal() as db:
        tid = db.scalar(
            select(ValidationFact.id).where(
                ValidationFact.artifact_id == aid, ValidationFact.kind == "target"
            )
        )
    request = {
        "target_id": tid,
        "binding_id": "fixture",
        "review_episode_id": ep,
        "request": {"evidence": "original"},
    }
    wid = scheduling.initial(
        scope,
        ep,
        operation_id="initial",
        requests=[request],
        receipt={"agent_revision": "custom-7", "fingerprint": "exact"},
    )
    with h.SessionLocal() as db:
        att = db.get(Work, wid).payload["invocation"]["attempt_ids"][0]
    completion = h.complete_attempt(
        scope,
        aid,
        att,
        state="completed",
        result={"value": "proposed", "caveat": "uncertainty"},
        diagnostics={},
    )
    with h.SessionLocal.begin() as db:
        selections.decide_episode_in_transaction(
            db,
            scope,
            aid,
            episode_id=ep,
            operation_id="reject",
            actor={"type": "agent", "id": "reviewer"},
            entries=[
                {
                    "target_id": tid,
                    "expected_version": 0,
                    "action": "reject",
                    "subject": completion,
                    "reason": "Please reconsider",
                }
            ],
        )
        parent = db.scalar(
            select(ValidationFact.id).where(
                ValidationFact.artifact_id == aid,
                ValidationFact.operation_id == "decision:reject",
            )
        )
    context = ReviewContext(scope, ep, "reviewer")
    args = dict(
        operation_id="retry",
        parent_decision_id=parent,
        target_ids=[tid],
        instructions="Check the supplied distinction; disagree if warranted",
        evidence_refs=[],
    )
    retry = scheduling.reconsider(context, **args)
    assert scheduling.reconsider(context, **args) == retry
    with h.SessionLocal() as db:
        work = db.get(Work, retry)
        assert work.payload["receipt"]["agent_revision"] == "custom-7"
        assert work.payload["requests"][0]["request"] == request["request"]
        assert work.payload["identity"]["parent_decision_id"] == parent
    with pytest.raises(a.ReviewStopped, match="scientific_attempts"):
        scheduling.reconsider(context, **{**args, "operation_id": "third"})
    assert not a.status(scope, ep)["ready"]


from tests.integration.persistence.test_curator_selected_authority import scenario


def test_public_decisions_publish_atomically_and_replay_without_new_revision(
    scope, scenario
):
    from src.lib.validation_review.decisions import ReviewContext, record_decisions
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
    from src.lib.validation_history.projection import read_published_projection

    x = scenario
    x.episode_fn()
    sid = a.create_scope(
        scope, operation_id=uuid4().hex, kind="document_run", targets=1, episodes=1
    )
    a.attach_episode(scope, artifact_id=x.aid, episode_id=x.episode, scope_id=sid)
    completed = x.propose("ECO:NEW")
    answer = h.read_fact(scope, x.aid, completed)
    with h.SessionLocal() as db:
        version = h._current(db, x.aid, x.target)["version"]
    request = DomainEnvelopeCheckpointRequest(
        project_key="fixture",
        envelope=x.env,
        expected_revision=x.revision,
        document_id=scope.document_id,
        adapter_key="fixture",
        source_extraction_result_id=x.source,
    )
    entry = {
        "target_id": x.target,
        "decision_unit_id": x.target,
        "logical_attempt_id": answer["parent_id"],
        "expected_version": version,
        "action": "accept",
        "reason": "Solid answer with a caveat",
    }
    context = ReviewContext(scope, x.episode, "supervisor")
    result = record_decisions(
        context, checkpoint_request=request, operation_id="review", entries=[entry]
    )
    assert result["ready"]
    assert (
        record_decisions(
            context, checkpoint_request=request, operation_id="review", entries=[entry]
        )
        == result
    )
    projected, _ = read_published_projection(
        scope, result["publication"], finalized=True
    )
    assert projected.extracted_objects[0].payload["evidence_code_curies"] == ["ECO:NEW"]
    with pytest.raises(h.HistoryConflict, match="identity"):
        record_decisions(
            context,
            checkpoint_request=request,
            operation_id="review",
            entries=[{**entry, "action": "reject"}],
        )


def test_deadline_expires_without_publishing_or_resetting_on_resume(scope, monkeypatch):
    from src.models.sql.validation_review import ValidationReviewEpisode

    _, ep, _ = admitted(scope)
    wid = queue(scope, ep)
    claimed = w.claim(scope, wid)
    w.mark_submitted(scope, wid, claimed["lease_token"])
    with h.SessionLocal() as db:
        row = db.get(ValidationReviewEpisode, ep)
        future = row.started_at + timedelta(seconds=row.policy["deadline_seconds"] + 1)
    monkeypatch.setattr(a, "now", lambda: future)
    assert a.status(scope, ep)["state"] == "expired"
    assert w.recover(scope, wid) == "uncertain"
    w.finish(scope, wid, claimed["lease_token"], outcome={"late": "preserved"})
    assert not a.status(scope, ep)["ready"]
    with pytest.raises(a.ReviewStopped, match="expired"):
        queue(scope, ep)


def test_parent_quota_is_shared_by_document_artifacts_and_cannot_be_inflated(
    scope, monkeypatch
):
    from dataclasses import replace

    monkeypatch.setenv("VALIDATION_REVIEW_EXTRA_PER_RUN", "1")
    parent = a.create_scope(
        scope,
        operation_id=uuid4().hex,
        kind="workspace_operation",
        targets=2,
        episodes=2,
    )
    other_scope = replace(scope, session_id=uuid4().hex)
    _, ep1, _ = admitted(scope, parent=parent)
    _, ep2, _ = admitted(other_scope, parent=parent)
    queue(scope, ep1)
    with pytest.raises(a.ReviewStopped, match="budget_exhausted"):
        queue(other_scope, ep2)
    with pytest.raises(h.HistoryConflict, match="parent coverage"):
        a.create_scope(
            scope,
            operation_id=uuid4().hex,
            kind="document_run",
            targets=1,
            episodes=1,
            parent_id=parent,
        )


def test_dependent_wave_waits_for_explicit_review_then_pins_selected_answer(scope):
    from src.lib.validation_review import scheduling, waves
    from src.lib.validation_history import selections

    aid = artifact(scope)
    first = binding(scope, aid)
    second = h.register_target(
        scope, aid, identity="dependent", original={"binding_id": "dependent"}
    )
    ep = selections.start_episode(
        scope,
        aid,
        episode={
            "episode_id": uuid4().hex,
            "reason": "two waves",
            "obligations": [
                {"target_id": first, "mode": "validation", "binding_id": "fixture"},
                {"target_id": second, "mode": "validation", "binding_id": "dependent"},
            ],
        },
    )
    sid = a.create_scope(
        scope, operation_id=uuid4().hex, kind="document_run", targets=2, episodes=1
    )
    a.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    wid = scheduling.initial(
        scope,
        ep,
        operation_id="initial",
        receipt={"revision": "fixed"},
        requests=[
            {
                "target_id": first,
                "binding_id": "fixture",
                "review_episode_id": ep,
                "request": {"mention": "original"},
            }
        ],
    )
    with h.SessionLocal() as db:
        att = db.get(Work, wid).payload["invocation"]["attempt_ids"][0]
    completion = h.complete_attempt(
        scope,
        aid,
        att,
        state="completed",
        result={"status": "resolved", "value": "selected"},
        diagnostics={},
    )
    built = []

    def build(sources):
        built.append(sources)
        return [
            {
                "target_id": second,
                "binding_id": "dependent",
                "request": {"input": sources[first]["result"]["value"]},
            }
        ]

    args = dict(
        operation_id="wave2",
        prerequisite_targets=[first],
        receipt={"revision": "dependent"},
        build_requests=build,
    )
    assert waves.advance(scope, ep, **args)["state"] == "awaiting_review"
    assert not built
    with h.SessionLocal.begin() as db:
        selections.decide_episode_in_transaction(
            db,
            scope,
            aid,
            episode_id=ep,
            operation_id="accept",
            actor={"type": "agent", "id": "reviewer"},
            entries=[
                {
                    "target_id": first,
                    "expected_version": 0,
                    "action": "accept",
                    "subject": completion,
                    "reason": "Solid",
                }
            ],
        )
    result = waves.advance(scope, ep, **args)
    assert (
        result["state"] == "queued" and result["prerequisites"][0]["target_id"] == first
    )
    with h.SessionLocal() as db:
        payload = db.get(Work, result["work_id"]).payload
        assert payload["requests"][0]["request"] == {"input": "selected"}
        assert payload["requests"][0]["review_episode_id"] == ep
    assert not a.status(scope, ep)["ready"]


@pytest.mark.asyncio
async def test_fake_executor_uses_frozen_requests_and_leaves_answer_unreviewed(scope):
    from src.lib.validation_review import scheduling, execution
    from src.models.sql.validation_history import ValidationFact

    aid, ep, _ = admitted(scope)
    with h.SessionLocal() as db:
        tid = db.scalar(
            select(ValidationFact.id).where(
                ValidationFact.artifact_id == aid, ValidationFact.kind == "target"
            )
        )
    wid = scheduling.initial(
        scope,
        ep,
        operation_id="initial",
        receipt={"revision": "custom-7"},
        requests=[
            {
                "target_id": tid,
                "binding_id": "fixture",
                "review_episode_id": ep,
                "request": {"mention": "original"},
            }
        ],
    )
    calls = []

    async def fake(payload):
        calls.append(payload)
        assert payload["receipt"] == {"revision": "custom-7"}
        return [
            execution.ValidatorOutcome(
                attempt,
                "completed",
                {"status": "unresolved", "caveat": "ambiguous"},
                {},
            )
            for attempt in payload["invocation"]["attempt_ids"]
        ]

    assert (await execution.execute(scope, wid, call=fake))["state"] == "completed"
    assert (await execution.execute(scope, wid, call=fake))["state"] == "not_claimed"
    assert len(calls) == 1
    assert not a.status(scope, ep)["ready"]


def test_grouped_public_review_accepts_caveat_and_unresolved_but_omissions_stay_pending(
    scope, tmp_path, monkeypatch
):
    from dataclasses import replace
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history import runtime, selections
    from src.lib.validation_history.candidates import retain_candidates
    from src.lib.validation_review.decisions import ReviewContext, record_decisions
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import (
        _multivalued_dispatch_pack,
        _result_payload,
    )

    pack = _multivalued_dispatch_pack(tmp_path)
    monkeypatch.setattr(
        "src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
        lambda _: pack,
    )
    env = DomainEnvelope(
        envelope_id=uuid4().hex,
        domain_pack_id=pack.pack_id,
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type="Annotation",
                pending_ref_id=f"obj-{i}",
                payload={"evidence_code_curies": [f"eco:{i}"]},
            )
            for i in range(5)
        ],
    )
    env = runtime.capture_extraction(
        scope=scope,
        producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"),
        envelope=env,
        provenance={},
    )
    aid = env.metadata["validation_history"]["artifact_id"]
    bound = runtime.CapturedArtifact(scope, aid)
    matches = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(env)
    env = selections.declare_envelope_episode(bound, env, matches)
    ep = env.metadata["review_episode_id"]
    sid = a.create_scope(
        scope, operation_id=uuid4().hex, kind="document_run", targets=5, episodes=1
    )
    a.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    jobs = [
        ValidatorDispatchJob(
            match, build_domain_validation_request(match).request, history_episode_id=ep
        )
        for match in matches
    ]
    invocation = runtime.begin_jobs(
        bound, jobs, operation_id="group", receipt={"revision": "fixture"}
    )
    results = []
    for i, job in enumerate(jobs):
        data = _result_payload(
            job.request,
            status="unresolved" if i == 2 else "resolved",
            resolved_values={} if i == 2 else {"curie": f"ECO:{i}"},
        )
        data["resolved_objects"] = []
        data["unresolved_questions"] = ["A caveat remains"] if i == 1 else []
        results.append(DomainValidatorResultBase.model_validate(data))
    runtime.complete_jobs(bound, invocation, results)
    env = retain_candidates(
        env,
        [
            ValidatorResultMaterializationInput(j.match, j.request, r)
            for j, r in zip(jobs, results)
        ],
        scope=scope,
        source_envelope_revision=1,
    ).envelope
    entries = []
    for i, (job, attempt_id) in enumerate(zip(jobs, invocation["attempt_ids"])):
        if i == 4:
            continue
        tid = runtime.request_target(bound, job)
        entries.append(
            {
                "target_id": tid,
                "decision_unit_id": tid,
                "logical_attempt_id": attempt_id,
                "expected_version": 0,
                "action": "reject" if i == 3 else "accept",
                "reason": "Explicit model judgment",
            }
        )
    output = record_decisions(
        ReviewContext(scope, ep, "reviewer"),
        operation_id="group-review",
        entries=entries,
        checkpoint_request=DomainEnvelopeCheckpointRequest(
            project_key="fixture",
            envelope=env,
            expected_revision=0,
            document_id=scope.document_id,
            adapter_key="fixture",
            source_extraction_result_id=uuid4(),
        ),
    )
    assert not output["ready"] and len(output["applied"]) == 4
    manifest = selections.read_manifest(scope, aid, episode_id=ep)
    assert sum(unit["review"] == "accept" for unit in manifest["units"]) == 3
    assert any(
        unit["scientific_status"] == "unresolved" and unit["eligible"]
        for unit in manifest["units"]
    )
    assert len({item["target_id"] for item in manifest["blockers"]}) == 2


def test_uncertain_attempt_can_be_acknowledged_without_fabricating_a_completion(scope):
    from src.lib.validation_review import scheduling
    from src.lib.validation_history import selections
    from src.models.sql.validation_history import ValidationFact

    aid, ep, _ = admitted(scope)
    with h.SessionLocal() as db:
        tid = db.scalar(
            select(ValidationFact.id).where(
                ValidationFact.artifact_id == aid, ValidationFact.kind == "target"
            )
        )
    wid = scheduling.initial(
        scope,
        ep,
        operation_id="initial",
        receipt={"revision": "fixed"},
        requests=[
            {
                "target_id": tid,
                "binding_id": "fixture",
                "review_episode_id": ep,
                "request": {"mention": "original"},
            }
        ],
    )
    claim = w.claim(scope, wid)
    w.mark_submitted(scope, wid, claim["lease_token"])
    w.mark_uncertain(scope, wid, claim["lease_token"], reason="worker_disconnected")
    att = claim["payload"]["invocation"]["attempt_ids"][0]
    with h.SessionLocal.begin() as db:
        selections.decide_episode_in_transaction(
            db,
            scope,
            aid,
            episode_id=ep,
            operation_id="ack",
            actor={"type": "agent", "id": "reviewer"},
            entries=[
                {
                    "target_id": tid,
                    "expected_version": 0,
                    "action": "acknowledge_failure",
                    "subject": att,
                    "reason": "Outcome is unknown",
                }
            ],
        )
        assert not db.scalar(
            select(ValidationFact.id).where(
                ValidationFact.artifact_id == aid, ValidationFact.kind == "completion"
            )
        )
    late = h.complete_attempt(
        scope,
        aid,
        att,
        state="completed",
        result={"status": "resolved", "value": "late"},
        diagnostics={},
    )
    manifest = selections.read_manifest(scope, aid, episode_id=ep)
    assert not manifest["ready"]
    assert any(item["code"] == "answer_review_pending" for item in manifest["blockers"])
    assert h.read_fact(scope, aid, late)["content"]["result"]["value"] == "late"
