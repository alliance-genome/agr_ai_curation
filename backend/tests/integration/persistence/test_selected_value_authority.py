"""Selected versions remain independent from a new review episode's readiness."""
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.lib.validation_history import selections as s, store as h
from tests.integration.persistence.test_validation_history import (
    scope as scope, migrated as migrated, artifact, attempt, decision,
)


def binding(scope, aid):
    return h.register_target(
        scope, aid, identity="binding", original={"binding_id": "fixture"}
    )


def episode(scope, aid, tid):
    return s.start_episode(scope, aid, episode={
        "episode_id": uuid4().hex, "reason": "new review",
        "obligations": [{"target_id": tid, "mode": "validation", "binding_id": "fixture"}],
    })


def answer(scope, aid, tid, *, state="completed", value="first"):
    att = attempt(scope, aid, tid, key=uuid4().hex)
    return h.complete_attempt(
        scope, aid, att, state=state,
        result={"status": "unresolved" if value is None else "resolved", "value": value},
        diagnostics={},
    )


def review(scope, aid, ep, tid, subject, *, action="accept"):
    with h.SessionLocal.begin() as db:
        state = h._current(db, aid, tid)
        return s.decide_episode_in_transaction(
            db, scope, aid, episode_id=ep, operation_id=uuid4().hex,
            actor={"type": "agent", "id": "supervisor"},
            entries=[{"target_id": tid, "expected_version": state["version"],
                      "action": action, **({"subject": subject} if action in {"accept", "reject"} else {}),
                      "reason": "LLM judgment"}],
        )


def test_previous_selection_does_not_complete_new_episode(scope):
    aid = artifact(scope)
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    first = answer(scope, aid, tid)
    review(scope, aid, ep, tid, first)
    assert s.read_manifest(scope, aid, episode_id=ep)["ready"]
    next_ep = episode(scope, aid, tid)
    manifest = s.read_manifest(scope, aid, episode_id=next_ep)
    assert manifest["units"][0]["selected"] == first
    assert manifest["units"][0]["eligible"]
    assert not manifest["ready"]
    second = answer(scope, aid, tid, value="second")
    review(scope, aid, next_ep, tid, second, action="reject")
    manifest = s.read_manifest(scope, aid, episode_id=next_ep)
    assert manifest["units"][0]["selected"] == first
    assert not manifest["ready"]
    # Explicitly retaining the previous answer completes this episode.
    review(scope, aid, next_ep, tid, first)
    assert s.read_manifest(scope, aid, episode_id=next_ep)["ready"]


def test_unresolved_is_selectable_without_inventing_identity(scope):
    aid = artifact(scope)
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    result = answer(scope, aid, tid, value=None)
    review(scope, aid, ep, tid, result)
    manifest = s.read_manifest(scope, aid, episode_id=ep)
    assert manifest["ready"]
    assert manifest["units"][0]["scientific_status"] == "unresolved"


def test_incomplete_opinion_cannot_select_or_complete(scope):
    aid = artifact(scope)
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    result = answer(scope, aid, tid, state="incomplete")
    opinion = s.record_opinion(
        scope, aid, operation_id=uuid4().hex,
        actor={"type": "agent", "id": "supervisor"}, completion_id=result,
        reason="I agree with the interpretation; required formatting is missing",
    )
    assert h.read_fact(scope, aid, opinion)["content"]["selects_value"] is False
    with pytest.raises(h.HistoryConflict, match="Incomplete"):
        review(scope, aid, ep, tid, result)
    assert not s.read_manifest(scope, aid, episode_id=ep)["ready"]


def test_missing_execution_still_has_review_obligation(scope):
    aid = artifact(scope)
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    manifest = s.read_manifest(scope, aid, episode_id=ep)
    assert not manifest["ready"]
    assert manifest["blockers"] == [{"target_id": tid, "code": "unreviewed"}]
    with pytest.raises(h.HistoryConflict, match="awaiting"):
        s.selected_prerequisites(manifest, [tid])


def test_required_validation_cannot_accept_baseline(scope):
    aid = artifact(scope)
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    with pytest.raises(h.HistoryConflict, match="extraction-only"):
        review(scope, aid, ep, tid, "baseline")


def test_rejected_replacement_does_not_break_accepted_prerequisite_pin(scope):
    aid = artifact(scope)
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    first = answer(scope, aid, tid)
    accepted = review(scope, aid, ep, tid, first)[0]
    second = answer(scope, aid, tid, value="second")
    review(scope, aid, ep, tid, second, action="reject")
    manifest = s.read_manifest(scope, aid, episode_id=ep)
    pins = s.selected_prerequisites(manifest, [tid])
    assert pins[0]["selection_id"] == accepted
    with h.SessionLocal.begin() as db:
        h._prerequisites_current(db, scope, pins)


def captured_source(scope):
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.lib.validation_history.runtime import capture_extraction
    env = DomainEnvelope(
        envelope_id="selected-" + uuid4().hex, domain_pack_id="fixture",
        extracted_objects=[CuratableObjectEnvelope(
            object_id="one", object_type="entity",
            payload={"name": "paper wording", "terms": [{"label": "a"}, {"label": "b"}]},
        )],
    )
    return capture_extraction(
        scope=scope, producer_id=uuid4().hex, typed=env.model_dump(mode="json"),
        envelope=env, provenance={"schema": "fixture/v1"},
    )


def test_source_rebuild_ignores_payload_values_and_preserves_stable_reorder(scope):
    from src.lib.validation_history.projection import source_envelope_in_transaction
    from src.lib.validation_history.runtime import reorder_element_projection
    env = captured_source(scope)
    aid = env.metadata["validation_history"]["artifact_id"]
    obj = env.extracted_objects[0]
    obj.payload["name"] = "unreviewed mutation"
    obj.payload["terms"][0]["label"] = "unreviewed first"
    # The structural order is changed by the existing stable-identity operation.
    ids = obj.metadata["validation_history"]["elements"]["terms"]
    env.extracted_objects[0] = reorder_element_projection(obj, "terms", list(reversed(ids)))
    with h.SessionLocal.begin() as db:
        rebuilt = source_envelope_in_transaction(db, scope, aid, env)
    assert rebuilt.extracted_objects[0].payload == {
        "name": "paper wording", "terms": [{"label": "b"}, {"label": "a"}],
    }


def test_exact_publication_survives_later_selection_and_failed_publication_rolls_back(scope):
    from src.lib.validation_history.projection import (
        pin_publication_in_transaction, read_published_projection,
        read_published_projection_in_transaction,
    )
    env = captured_source(scope)
    aid = env.metadata["validation_history"]["artifact_id"]
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    first = answer(scope, aid, tid)
    review(scope, aid, ep, tid, first)
    manifest = s.read_manifest(scope, aid, episode_id=ep)
    with h.SessionLocal.begin() as db:
        published = pin_publication_in_transaction(
            db, scope, aid, operation_id="first-publication", manifest=manifest, envelope=env,
        )
        # Autosave and readiness checks must see the publication in the caller's
        # transaction, without opening another session behind its artifact lock.
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        pending, pending_manifest = read_published_projection_in_transaction(
            db, scope, published.metadata["selected_projection"], finalized=True,
        )
        assert pending_manifest["ready"]
        assert pending.extracted_objects == published.extracted_objects
    reference = published.metadata["selected_projection"]
    old, old_manifest = read_published_projection(scope, reference, finalized=True)
    assert old_manifest["ready"]
    next_ep = episode(scope, aid, tid)
    current, readiness = read_published_projection(scope, reference, current=True)
    assert current == old
    assert not readiness["ready"]
    assert readiness["episode_id"] == next_ep
    with pytest.raises(h.HistoryConflict, match="current episode publication"):
        read_published_projection(scope, reference, finalized=True)
    # The new episode explicitly retains the prior value before considering V2.
    review(scope, aid, next_ep, tid, first)
    ep = next_ep
    second = answer(scope, aid, tid, value="second")
    review(scope, aid, ep, tid, second)
    with pytest.raises(h.HistoryConflict, match="current episode publication"):
        read_published_projection(scope, reference, finalized=True)
    exact, exact_manifest = read_published_projection(scope, reference)
    assert exact == old
    assert exact_manifest == old_manifest
    with pytest.raises(RuntimeError, match="checkpoint failed"):
        with h.SessionLocal.begin() as db:
            pending = pin_publication_in_transaction(
                db, scope, aid, operation_id="failed-publication",
                manifest=s.manifest_in_transaction(db, scope, aid, episode_id=ep),
                envelope=env,
            )
            raise RuntimeError("checkpoint failed")
    with pytest.raises(h.HistoryConflict):
        read_published_projection(scope, pending.metadata["selected_projection"])


def test_real_pack_projection_uses_only_selected_completion(scope, tmp_path, monkeypatch):
    from src.lib.validation_history import runtime
    from src.lib.validation_history.candidates import retain_candidates
    from src.lib.validation_history.projection import rebuild_selected_envelope
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack, _result_payload

    pack = _multivalued_dispatch_pack(tmp_path)
    env = DomainEnvelope(
        envelope_id="replay-" + uuid4().hex, domain_pack_id=pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(
            object_type="Annotation", pending_ref_id="one",
            payload={"evidence_code_curies": ["eco:1"]},
        )],
    )
    env = runtime.capture_extraction(
        scope=scope, producer_id=uuid4().hex, typed=env.model_dump(mode="json"),
        envelope=env, provenance={},
    )
    aid = env.metadata["validation_history"]["artifact_id"]
    captured = runtime.CapturedArtifact(scope, aid)
    match = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(env)[0]
    request = build_domain_validation_request(match).request
    job = ValidatorDispatchJob(match, request)
    tid = runtime.request_target(captured, job)
    ep = s.start_episode(scope, aid, episode={
        "episode_id": uuid4().hex, "reason": "review one field",
        "obligations": [{"target_id": tid, "mode": "validation", "binding_id": request.validator_binding_id}],
    })

    def propose(value):
        invocation = runtime.begin_jobs(captured, [job], operation_id=uuid4().hex, receipt={})
        data = _result_payload(request, resolved_values={"curie": value})
        data["resolved_objects"] = []
        result = DomainValidatorResultBase.model_validate(data)
        runtime.complete_jobs(captured, invocation, [result])
        item = ValidatorResultMaterializationInput(match, request, result)
        retained = retain_candidates(env, [item], scope=scope)
        assert retained.envelope.extracted_objects[0].payload == env.extracted_objects[0].payload
        return result._history_ref["fact_id"]

    rejected = propose("ECO:REJECTED")
    review(scope, aid, ep, tid, rejected, action="reject")
    accepted = propose("ECO:ACCEPTED")
    review(scope, aid, ep, tid, accepted)
    manifest = s.read_manifest(scope, aid, episode_id=ep)
    assert manifest["ready"]
    with h.SessionLocal.begin() as db:
        projected = rebuild_selected_envelope(
            db, scope, aid, envelope=env, manifest=manifest, domain_pack=pack,
        )
    assert projected.extracted_objects[0].payload["evidence_code_curies"] == ["ECO:ACCEPTED"]
    assert "ECO:REJECTED" not in str(projected.extracted_objects[0].payload)
    from src.lib.validation_history.projection import read_dispatch_source
    reconsideration = read_dispatch_source(captured, projected)
    repeated_match = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(reconsideration)[0]
    repeated_request = build_domain_validation_request(repeated_match).request
    assert repeated_request.selected_inputs == request.selected_inputs
    assert reconsideration.extracted_objects[0].payload["evidence_code_curies"] == ["eco:1"]
    assert projected.extracted_objects[0].payload["evidence_code_curies"] == ["ECO:ACCEPTED"]
    from src.lib.validation_history.projection import publish_decisions, read_published_projection
    from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, StaleDomainEnvelopeRevisionError
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    checkpoint_request = DomainEnvelopeCheckpointRequest(
        project_key="fixture", envelope=env, expected_revision=0,
        document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=uuid4(),
    )
    with h.SessionLocal() as db:
        version = h._current(db, aid, tid)["version"]
    entries = [{"target_id": tid, "expected_version": version, "action": "accept",
                "subject": accepted, "reason": "Publish reviewed answer"}]
    published, published_manifest, checkpoint = publish_decisions(
        scope, checkpoint_request=checkpoint_request, episode_id=ep,
        operation_id=uuid4().hex, actor={"type": "agent", "id": "supervisor"}, entries=entries,
    )
    assert checkpoint.revision == 1
    pinned, exact_manifest = read_published_projection(scope, published.metadata["selected_projection"], finalized=True)
    assert exact_manifest == published_manifest
    assert pinned.extracted_objects[0].payload == projected.extracted_objects[0].payload
    entries[0]["expected_version"] += 1
    # A failed checkpoint rolls the decision, projection fact and heads back too.
    with pytest.raises(StaleDomainEnvelopeRevisionError):
        publish_decisions(scope, checkpoint_request=checkpoint_request, episode_id=ep,
            operation_id=uuid4().hex, actor={"type": "agent", "id": "supervisor"}, entries=entries)
    assert read_published_projection(scope, published.metadata["selected_projection"], finalized=True)[1] == published_manifest
    assert h.read_baseline(scope, aid)["normalized"]["extracted_objects"][0]["payload"] == {
        "evidence_code_curies": ["eco:1"],
    }


def test_rejection_does_not_implicitly_revoke_current_value(scope):
    aid = artifact(scope)
    tid = binding(scope, aid)
    ep = episode(scope, aid, tid)
    accepted = answer(scope, aid, tid)
    review(scope, aid, ep, tid, accepted)
    review(scope, aid, ep, tid, accepted, action="reject")
    rejected = s.read_manifest(scope, aid, episode_id=ep)
    assert rejected["units"][0]["selected"] == accepted
    assert not rejected["ready"]
    review(scope, aid, ep, tid, accepted, action="revoke")
    assert s.read_manifest(scope, aid, episode_id=ep)["units"][0]["selected"] is None


@pytest.mark.parametrize("identifier, valid", [("EX:SELECTED", True), (42, False)])
def test_profile_selected_projection_enforces_whole_record_contract(scope, identifier, valid):
    from tests.unit.lib.domain_packs.test_profile_validation import example
    from tests.unit.lib.domain_packs.test_profile_materialization import prepared
    from src.lib.validation_history import runtime
    from src.lib.validation_history.candidates import retain_candidates
    from src.lib.validation_history.projection import rebuild_selected_envelope
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.schemas.domain_validator import DomainValidatorResultBase

    source, context = prepared(example.__wrapped__())
    source = runtime.capture_extraction(
        scope=scope, producer_id=uuid4().hex, typed=source.model_dump(mode="json"),
        envelope=source, provenance={},
    )
    aid = source.metadata["validation_history"]["artifact_id"]
    captured = runtime.CapturedArtifact(scope, aid)
    match, = context.registry.match_bindings(source)
    source = s.declare_envelope_episode(captured, source, [match])
    request = build_domain_validation_request(match).request
    job = ValidatorDispatchJob(match, request)
    invocation = runtime.begin_jobs(captured, [job], operation_id=uuid4().hex, receipt={})
    result = DomainValidatorResultBase(
        status="resolved", request_id=request.request_id,
        validator_binding_id=request.validator_binding_id, validator_agent=request.validator_agent,
        target=request.target, resolved_values={"identifier": identifier}, resolved_objects=[],
        missing_expected_fields=[], candidates=[], lookup_attempts=[],
        explanation="A retained model proposal", curator_message=None,
    )
    runtime.complete_jobs(captured, invocation, [result])
    staged = retain_candidates(source, [ValidatorResultMaterializationInput(match, request, result)], scope=scope)
    assert "resolved_id" not in staged.envelope.extracted_objects[0].payload["attributes"]
    tid = runtime.request_target(captured, job)
    ep = source.metadata["review_episode_id"]
    review(scope, aid, ep, tid, result._history_ref["fact_id"])
    manifest = s.read_manifest(scope, aid, episode_id=ep)
    assert manifest["ready"]
    with h.SessionLocal.begin() as db:
        if not valid:
            with pytest.raises(h.HistoryConflict, match="structurally incomplete"):
                rebuild_selected_envelope(db, scope, aid, envelope=source, manifest=manifest,
                    domain_pack=context.registry.domain_pack, profile_context=context)
        else:
            projected = rebuild_selected_envelope(db, scope, aid, envelope=source, manifest=manifest,
                domain_pack=context.registry.domain_pack, profile_context=context)
            assert projected.extracted_objects[0].payload["attributes"] == {
                "paper_name": "A", "resolved_id": identifier,
            }
    assert "resolved_id" not in h.read_baseline(scope, aid)["normalized"]["extracted_objects"][0]["payload"]["attributes"]


def test_projection_cannot_silently_drop_active_source_objects(scope):
    from src.lib.validation_history.projection import source_envelope_in_transaction
    source = captured_source(scope)
    aid = source.metadata["validation_history"]["artifact_id"]
    source.extracted_objects = []
    with h.SessionLocal.begin() as db, pytest.raises(h.HistoryConflict, match="omits an active"):
        source_envelope_in_transaction(db, scope, aid, source)


def test_unavailable_required_mapping_cannot_be_hidden_by_extraction_only_acceptance(scope):
    source = captured_source(scope)
    aid = source.metadata["validation_history"]["artifact_id"]
    tid = source.extracted_objects[0].metadata["validation_history"]["target_id"]
    ep = s.start_episode(scope, aid, episode={
        "episode_id": uuid4().hex, "reason": "Pinned capability unavailable",
        "obligations": [{"target_id": tid, "mode": "extraction_only"}],
        "unavailable_bindings": ["required-profile-lookup"],
    })
    review(scope, aid, ep, tid, "baseline")
    manifest = s.read_manifest(scope, aid, episode_id=ep)
    assert not manifest["ready"]
    assert manifest["blockers"] == [{"target_id": None, "code": "required_validator_unavailable",
                                      "binding_id": "required-profile-lookup"}]


def test_curator_withdraw_and_restore_do_not_revive_retired_source(scope):
    from src.lib.validation_history.curator_values import resolve_curator_identity
    from src.lib.validation_history.projection import pin_publication_in_transaction, read_published_projection
    source = captured_source(scope)
    aid = source.metadata["validation_history"]["artifact_id"]
    tid = source.extracted_objects[0].metadata["validation_history"]["target_id"]
    ep = s.start_episode(scope, aid, episode={"episode_id": uuid4().hex, "reason": "extraction only",
        "obligations": [{"target_id": tid, "mode": "extraction_only"}]})
    review(scope, aid, ep, tid, "baseline")
    with h.SessionLocal.begin() as db:
        published = pin_publication_in_transaction(db, scope, aid, operation_id=uuid4().hex,
            manifest=s.manifest_in_transaction(db, scope, aid, episode_id=ep), envelope=source)
    reference = published.metadata["selected_projection"]
    kwargs = dict(envelope=source, object_id="one", value_path="terms[0]", identity_keys=["label"])
    assert resolve_curator_identity(scope, **kwargs, action="withdraw_override", reference=None) == {"label": "a"}
    with pytest.raises(h.HistoryAccessError):
        resolve_curator_identity(h.HistoryScope("other-owner", scope.document_id, scope.session_id),
            **kwargs, action="restore_selected", reference=reference)
    pending_episode = s.start_episode(scope, aid, episode={
        "episode_id": uuid4().hex, "reason": "reconsider without replacing selected value",
        "obligations": [{"target_id": tid, "mode": "extraction_only"}],
    })
    assert not s.read_manifest(scope, aid, episode_id=pending_episode)["ready"]
    assert resolve_curator_identity(scope, **kwargs, action="withdraw_override", reference=None) == {"label": "a"}
    field = source.extracted_objects[0].metadata["validation_history"]["fields"]["terms[0].label"]
    decision(scope, aid, field, 0, "retire")
    with pytest.raises(h.HistoryConflict, match="no longer current"):
        read_published_projection(scope, reference, current=True)
    assert resolve_curator_identity(scope, **kwargs, action="withdraw_override", reference=None) == {"label": None}
    assert resolve_curator_identity(scope, **kwargs, action="restore_selected", reference=reference) == {"label": "a"}
    with h.SessionLocal() as db:
        assert h._current(db, aid, field)["active"] is False
    with pytest.raises(h.HistoryConflict, match="exact selection"):
        resolve_curator_identity(scope, **kwargs, action="restore_selected", reference={**reference, "artifact_id": "other"})
