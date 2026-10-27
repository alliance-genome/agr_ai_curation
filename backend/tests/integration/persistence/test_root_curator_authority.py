"""Root compound identities preserve curator authority and immutable history."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from uuid import uuid4

from src.lib.domain_envelopes.patches import EnvelopeFieldPatch, EnvelopeFieldPatchOperation, apply_curator_field_patch
from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest, write_domain_envelope_checkpoint, load_domain_envelope
from src.lib.validation_history import curator_authority as authority, runtime, selections, store
from src.lib.validation_history.curator_values import resolve_curator_identity
from src.lib.validation_history.projection import publish_decisions, read_published_projection
from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated
from tests.unit.lib.domain_packs.test_curator_override import _root_pack, _pack


@pytest.fixture
def selected_root(scope, monkeypatch, request):
    nested = getattr(request, "param", None) in {"nested", "two_nested"}
    pack = _pack() if nested else _root_pack()
    if getattr(request, "param", None) == "two_nested":
        definition = pack.metadata.object_definitions[0]
        fields = list(definition.fields) + [field.model_copy(update={"field_path": field.field_path.replace("site", "other", 1)}) for field in definition.fields]
        pack = replace(pack, metadata=pack.metadata.model_copy(update={"object_definitions": [definition.model_copy(update={"fields": fields})]}))
    label = "name" if nested else "symbol"
    prefix = "site." if nested else ""
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    original = {"curie": "G:1", label: "original", "mention": "paper wording",
                "entity_type": "gene", "resolution_state": "resolved", "lookup_outcome": "matched"}
    if nested:
        original = {"site": original}
        if getattr(request, "param", None) == "two_nested":
            original["other"] = {**original["site"], "curie": "G:3"}
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_id="one", object_type="Observation" if nested else "Mention", payload=original)])
    env = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"), envelope=env, provenance={})
    aid = env.metadata["validation_history"]["artifact_id"]
    root = env.extracted_objects[0].metadata["validation_history"]["target_id"]
    ep = selections.start_episode(scope, aid, episode={"episode_id": uuid4().hex,
        "reason": "explicit source review", "obligations": [{"target_id": root, "mode": "extraction_only"}]})
    checkpoint = DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=env,
        expected_revision=0, document_id=scope.document_id, adapter_key="fixture",
        source_extraction_result_id=uuid4())
    env, _, saved = publish_decisions(scope, checkpoint_request=checkpoint, episode_id=ep,
        operation_id=uuid4().hex, actor={"type": "agent", "id": "supervisor"},
        entries=[{"target_id": root, "expected_version": 0, "action": "accept",
                  "subject": "baseline", "reason": "Explicit extraction review"}])
    reference = env.metadata["selected_projection"]
    baseline = store.read_baseline(scope, aid)
    before_facts = [(fact["id"], fact["sha256"]) for fact in store.list_history(scope, aid)]
    revision = saved.revision
    state = SimpleNamespace(env=env, revision=revision, aid=aid, root=root, pack=pack,
        reference=reference, baseline=baseline, before_facts=before_facts, original=original, checkpoint=checkpoint)

    def edit(identity, *, action=None, selected_state=None, patch_id=None):
        nonlocal env, revision
        payload = env.extracted_objects[0].payload
        identity_value = payload["site"] if nested else payload
        patch = EnvelopeFieldPatch(envelope_id=env.envelope_id, expected_revision=revision,
            object_id="one", field_path=prefix + label, operation=EnvelopeFieldPatchOperation.REPLACE_IDENTITY,
            before={key: identity_value[key] for key in ("curie", label)}, value=identity,
            selected_value_action=action, selected_state=selected_state,
            **({"patch_id": patch_id} if patch_id is not None else {}))
        result = apply_curator_field_patch(env, pack, patch, current_revision=revision,
                                          actor_id=scope.owner_id, actor_display_name="Curator")
        assert result.accepted, result.errors
        with store.SessionLocal.begin() as db:
            persisted = write_domain_envelope_checkpoint(db, replace(checkpoint,
                envelope=result.envelope, expected_revision=revision))
            env = load_domain_envelope(db, env.envelope_id)
            revision = persisted.revision
        state.env, state.revision = env, revision

    state.edit = edit
    return state


def test_root_override_checkpoint_withdraw_and_restore_preserve_original(scope, selected_root):
    x = selected_root
    # The ID did not change: it must still belong to the atomic identity group.
    x.edit({"curie": "G:1", "symbol": "curator"})
    assert x.env.extracted_objects[0].payload["symbol"] == "curator"
    with store.SessionLocal() as db:
        overlay, body = authority.active_overlays(db, x.aid)[0]
        assert body["anchor"] == {"target_id": x.root, "suffix": []}
        assert not any(change["path"] == "curie" for change in body["changes"])
        members = authority._atomic_identity_anchors(db, x.aid, body, {})
        refs = x.env.extracted_objects[0].metadata["validation_history"]
        assert authority.anchor_for_path(refs, "curie") in members
        assert authority.anchor_for_path(refs, "symbol") in members
        assert authority.anchor_for_path(refs, "entity_type") not in members
        overlay_id = overlay.id
    for path, expected in [("entity_type", []), ("curie", [overlay_id])]:
        review_episode = selections.start_episode(scope, x.aid, episode={"episode_id": uuid4().hex,
            "reason": "explicit scoped revalidation", "obligations": [{"target_id": x.root, "mode": "extraction_only"}]})
        intent = authority.record_revalidation_intent(runtime.CapturedArtifact(scope, x.aid), envelope=x.env,
            episode_id=review_episode, object_id="one", field_paths=[path], actor_id=scope.owner_id)
        assert store.read_fact(scope, x.aid, intent)["content"]["overlay_ids"] == expected
    selected = resolve_curator_identity(scope, envelope=x.env, object_id="one", value_path="",
        identity_keys=["curie", "symbol"], action="withdraw_override", reference=None, include_state=True)
    assert selected["curie"] == "G:1" and selected["symbol"] == "original"
    x.edit({key: selected[key] for key in ("curie", "symbol")},
         action="withdraw_override", selected_state=selected)
    assert "curator_override" not in x.env.extracted_objects[0].payload
    restored = resolve_curator_identity(scope, envelope=x.env, object_id="one", value_path="",
        identity_keys=["curie", "symbol"], action="restore_selected", reference=x.reference)
    assert restored == {"curie": "G:1", "symbol": "original"}
    assert store.read_baseline(scope, x.aid) == x.baseline
    assert [(fact["id"], fact["sha256"]) for fact in store.list_history(scope, x.aid)][:len(x.before_facts)] == x.before_facts
    assert read_published_projection(scope, x.reference)[0].extracted_objects[0].payload == x.original


def test_root_override_real_api_save_is_atomic_and_stale_edit_writes_nothing(scope, selected_root, monkeypatch):
    from datetime import datetime, timezone
    from uuid import UUID
    from unittest.mock import AsyncMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import curation_workspace as api
    from src.api.auth import _get_user_from_cookie_impl
    from src.models.sql.database import get_db
    from src.lib.curation_workspace.models import CurationCandidate, CurationDraft, CurationReviewSession, DomainEnvelopeModel
    from src.schemas.curation_workspace import CurationCandidateSource

    x = selected_root
    with store.SessionLocal.begin() as db:
        session = CurationReviewSession(adapter_key="fixture", document_id=UUID(scope.document_id),
            created_by_id=scope.owner_id, prepared_at=datetime.now(timezone.utc))
        db.add(session)
        db.flush()
        sid = str(session.id)
        db.get(DomainEnvelopeModel, x.env.envelope_id).session_id = session.id
        candidate = CurationCandidate(session=session, adapter_key="fixture", source=CurationCandidateSource.MANUAL,
            envelope_id=x.env.envelope_id, object_id="one", envelope_revision=x.revision)
        db.add(candidate)
        db.flush()
        db.add(CurationDraft(candidate_id=candidate.id, adapter_key="fixture", version=1,
                            fields=[], draft_metadata={}))
    monkeypatch.setattr("src.lib.curation_workspace.session_mutation_service.resolve_curation_domain_pack_by_id", lambda _: x.pack)
    # Only the authentication boundary is substituted; route/service/SQL are real.
    monkeypatch.setattr(api, "provision_user_async", AsyncMock())
    provider = AsyncMock(side_effect=AssertionError("A curator edit must not invoke a model"))
    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", provider)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[_get_user_from_cookie_impl] = lambda: {
        "sub": scope.owner_id, "name": "Curator", "groups": []}
    def database():
        with store.SessionLocal() as db:
            yield db
    app.dependency_overrides[get_db] = database
    payload = {"session_id": sid, "envelope_id": x.env.envelope_id, "expected_revision": x.revision,
        "object_id": "one", "field_path": "symbol", "operation": "replace_identity",
        "before": {"curie": "G:1", "symbol": "original"},
        "value": {"curie": "G:1", "symbol": "curator"}}
    url = f"/api/curation-workspace/sessions/{sid}/envelopes/{x.env.envelope_id}/field"
    with TestClient(app) as client:
        result = client.patch(url, json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["envelope_revision"] == x.revision + 1
        before_stale = store.list_history(scope, x.aid)
        stale = client.patch(url, json=payload)
        assert stale.status_code == 409, stale.text
        invalid = client.patch(url, json={**payload, "field_path": ""})
        assert invalid.status_code == 422, invalid.text
    provider.assert_not_called()
    assert store.list_history(scope, x.aid) == before_stale
    assert store.read_baseline(scope, x.aid) == x.baseline
    with store.SessionLocal() as db:
        row = db.get(DomainEnvelopeModel, x.env.envelope_id)
        assert row.revision == x.revision + 1
        current = load_domain_envelope(db, x.env.envelope_id)
        assert current.extracted_objects[0].payload["symbol"] == "curator"
        assert current.extracted_objects[0].payload["lookup_outcome"] == "curator_override"
        _, body = authority.active_overlays(db, x.aid)[0]
        assert body["anchor"] == {"target_id": x.root, "suffix": []}


@pytest.mark.parametrize("destination,intent_path,action,later_edit,expected", [
    ("curie", "curie", "accept", False, "validated"),
    ("curie", "entity_type", "accept", False, "curator"),
    ("entity_type", "curie", "accept", False, "curator"),
    ("curie", "curie", "reject", False, "curator"),
    ("curie", "curie", "accept", True, "later curator"),
    ("curie", None, "accept", False, "curator"),
    ("curie", "curie", "incomplete", False, "curator"),
])
def test_root_revalidation_releases_only_authorized_identity(scope, selected_root, monkeypatch,
        destination, intent_path, action, later_edit, expected):
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history.candidates import retain_candidates
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload

    x = selected_root
    x.edit({"curie": "G:1", "symbol": "curator"})
    # An independent scalar override must survive identity revalidation.
    with store.SessionLocal.begin() as db:
        authority.record_patch(db, scope, x.aid, event_id=uuid4().hex, actor_id=scope.owner_id,
            body=authority.bind_patch_anchors(x.env, {"target_id": x.root, "object_id": "one",
                "value_path": "entity_type", "action": "override", "changes": [{"target_id": x.root,
                    "path": "entity_type", "present": True, "value": "curator classification"}]}))
    outputs = {"curie": "curie", "symbol": "symbol"} if destination == "curie" else {"curie": "entity_type"}
    binding = {"binding_id": "fixture.identity", "display_name": "Identity",
        "validator_agent": {"package_id": "fixture.validators", "agent_id": "identity_validator"},
        "applies_to": {"domain_pack_id": x.pack.pack_id, "object_types": ["Mention"], "field_paths": [destination]},
        "input_fields": {"curie": {"source": "payload", "path": destination}},
        "expected_result_fields": outputs, "required": True}
    pack = replace(x.pack, metadata=x.pack.metadata.model_copy(update={
        "metadata": {"validator_bindings": {"active": [binding]}}}))
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    bound = runtime.CapturedArtifact(scope, x.aid)
    matches = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(x.env)
    env = selections.declare_envelope_episode(bound, x.env, matches)
    episode = env.metadata["review_episode_id"]
    if intent_path is not None:
        authority.record_revalidation_intent(bound, envelope=env, episode_id=episode,
            object_id="one", field_paths=[intent_path], actor_id=scope.owner_id)
    if later_edit:
        x.edit({"curie": "G:1", "symbol": "later curator"})
        env = x.env
    match, = matches
    request = build_domain_validation_request(match).request
    job = ValidatorDispatchJob(match, request, history_episode_id=episode)
    invocation = runtime.begin_jobs(bound, [job], operation_id=uuid4().hex, receipt={})
    values = {"curie": "G:2", "symbol": "validated"} if destination == "curie" else {"curie": "new classification"}
    result = DomainValidatorResultBase.model_validate({**_result_payload(request, resolved_values=values), "resolved_objects": []})
    if action == "incomplete":
        completion = store.complete_attempt(scope, x.aid, invocation["attempt_ids"][0],
            state="incomplete", result={"status": "unresolved"}, diagnostics={})
    else:
        runtime.complete_jobs(bound, invocation, [result])
        completion = result._history_ref["fact_id"]
        env = retain_candidates(env, [ValidatorResultMaterializationInput(match, request, result)],
                                scope=scope, source_envelope_revision=x.revision).envelope
    target = runtime.request_target(bound, job)
    with store.SessionLocal() as db:
        version = store._current(db, x.aid, target)["version"]
    def publish():
        return publish_decisions(scope, checkpoint_request=replace(x.checkpoint,
            envelope=env, expected_revision=x.revision), episode_id=episode, operation_id=uuid4().hex,
            actor={"type": "agent", "id": "supervisor"}, entries=[{"target_id": target,
                "expected_version": version, "action": "accept" if action == "incomplete" else action,
                "subject": completion, "reason": "Explicit supervisor assessment"}])
    if action == "incomplete":
        with pytest.raises(store.HistoryConflict, match="Incomplete"):
            publish()
        with store.SessionLocal() as db:
            projected = authority.apply_overlays(db, x.aid, env)
    else:
        projected, _, _ = publish()
    assert projected.extracted_objects[0].payload["symbol"] == expected
    assert projected.extracted_objects[0].payload["entity_type"] == "curator classification"
    assert store.read_baseline(scope, x.aid) == x.baseline


def test_reused_patch_id_resolves_only_the_new_audit_in_each_revision(scope, selected_root):
    x = selected_root
    patch_id = uuid4().hex
    x.edit({"curie": "G:1", "symbol": "first"}, patch_id=patch_id)
    x.edit({"curie": "G:1", "symbol": "second"}, patch_id=patch_id)
    with store.SessionLocal() as db:
        _, body = authority.active_overlays(db, x.aid)[0]
        assert len(authority._atomic_identity_anchors(db, x.aid, body, {})) == 2
        refs = x.env.extracted_objects[0].metadata["validation_history"]
        assert authority.anchor_for_path(refs, "curie") in authority._atomic_identity_anchors(db, x.aid, body, {})
    assert store.read_baseline(scope, x.aid) == x.baseline


@pytest.mark.parametrize("missing", ["event", "checkpoint", "audit", "ambiguous_audit"])
def test_atomic_membership_fails_on_missing_or_ambiguous_exact_evidence(scope, selected_root, monkeypatch, missing):
    from copy import deepcopy
    from src.lib.domain_envelopes import persistence
    from src.lib.domain_packs.resolvable_values import CURATOR_OVERRIDE_METADATA_KEY

    x = selected_root
    x.edit({"curie": "G:1", "symbol": "curator"})
    facts = store.list_history(scope, x.aid)
    original_load = persistence.load_domain_envelope
    def damaged_checkpoint(db, envelope_id, *, revision=None):
        if revision == x.revision and missing == "checkpoint":
            raise persistence.DomainEnvelopePersistenceError("Missing checkpoint fixture")
        env = original_load(db, envelope_id, revision=revision)
        if revision == x.revision and missing in {"audit", "ambiguous_audit"}:
            env = env.model_copy(deep=True)
            audits = env.extracted_objects[0].metadata[CURATOR_OVERRIDE_METADATA_KEY]
            env.extracted_objects[0].metadata[CURATOR_OVERRIDE_METADATA_KEY] = (
                [] if missing == "audit" else [*audits, deepcopy(audits[-1])])
        return env
    monkeypatch.setattr(persistence, "load_domain_envelope", damaged_checkpoint)
    with store.SessionLocal() as db:
        _, body = authority.active_overlays(db, x.aid)[0]
        if missing == "event":
            body = {**body, "source_event_id": uuid4().hex}
        with pytest.raises(store.HistoryConflict, match="Atomic curator identity"):
            authority._atomic_identity_anchors(db, x.aid, body, {})
    assert store.list_history(scope, x.aid) == facts


@pytest.mark.parametrize("selected_root", ["nested"], indirect=True)
def test_existing_nested_audit_authorizes_full_identity_but_not_other_children(scope, selected_root):
    x = selected_root
    x.edit({"curie": "G:1", "name": "curator"})
    with store.SessionLocal() as db:
        fact, body = authority.active_overlays(db, x.aid)[0]
        assert "identity_members" not in body  # No new stored membership contract or migration.
        assert not any(change["path"] == "site.curie" for change in body["changes"])
        members = authority._atomic_identity_anchors(db, x.aid, body, {})
        refs = x.env.extracted_objects[0].metadata["validation_history"]
        assert authority.anchor_for_path(refs, "site.curie") in members
        identity_id = fact.id
    for path, expected in [("site.mention", []), ("site.curie", [identity_id])]:
        episode = selections.start_episode(scope, x.aid, episode={"episode_id": uuid4().hex,
            "reason": "explicit scoped review", "obligations": [{"target_id": x.root, "mode": "extraction_only"}]})
        intent = authority.record_revalidation_intent(runtime.CapturedArtifact(scope, x.aid),
            envelope=x.env, episode_id=episode, object_id="one", field_paths=[path], actor_id=scope.owner_id)
        assert store.read_fact(scope, x.aid, intent)["content"]["overlay_ids"] == expected
    assert store.read_baseline(scope, x.aid) == x.baseline


@pytest.mark.parametrize("selected_root", ["two_nested"], indirect=True)
def test_draft_save_two_identities_in_one_checkpoint_preserves_separate_authority(scope, selected_root, monkeypatch):
    from datetime import datetime, timezone
    from uuid import UUID
    from src.lib.curation_workspace import session_mutation_service as service
    from src.lib.curation_workspace.models import CurationCandidate, CurationDraft, CurationReviewSession, DomainEnvelopeModel
    from src.schemas.curation_workspace import CurationCandidateSource, CurationDraftField

    x = selected_root
    monkeypatch.setattr(service, "resolve_curation_domain_pack_by_id", lambda _: x.pack)
    fields = [CurationDraftField(field_key=path, label=path, value=value)
              for path, value in [("site.name", "first curator"), ("other.name", "second curator")]]
    with store.SessionLocal.begin() as db:
        session = CurationReviewSession(adapter_key="fixture", document_id=UUID(scope.document_id),
            created_by_id=scope.owner_id, prepared_at=datetime.now(timezone.utc))
        db.add(session)
        db.flush()
        session_id = session.id
        db.get(DomainEnvelopeModel, x.env.envelope_id).session_id = session.id
        candidate = CurationCandidate(session=session, adapter_key="fixture", source=CurationCandidateSource.MANUAL,
            envelope_id=x.env.envelope_id, object_id="one", envelope_revision=x.revision)
        db.add(candidate)
        db.flush()
        db.add(CurationDraft(candidate_id=candidate.id, adapter_key="fixture", version=1, fields=[], draft_metadata={}))
        db.flush()
        service._materialize_candidate_draft_changes_into_envelope(db, candidate,
            draft_fields=fields, changed_field_keys=[field.field_key for field in fields], confirmed_field_keys=set(),
            actor_claims={"sub": scope.owner_id, "name": "Curator", "groups": []},
            updated_at=datetime.now(timezone.utc))
        current = load_domain_envelope(db, x.env.envelope_id)
        assert db.get(DomainEnvelopeModel, x.env.envelope_id).revision == x.revision + 1
        assert current.extracted_objects[0].payload["site"]["name"] == "first curator"
        assert current.extracted_objects[0].payload["other"]["name"] == "second curator"
        overlays = authority.active_overlays(db, x.aid)
        assert len(overlays) == 2
        refs = current.extracted_objects[0].metadata["validation_history"]
        ids = {}
        for fact, body in overlays:
            prefix = body["value_path"]
            ids[prefix] = fact.id
            members = authority._atomic_identity_anchors(db, x.aid, body, {})
            assert len(members) == 2
            assert all(authority.anchor_for_path(refs, prefix + "." + key) in members for key in ("curie", "name"))
    for path, expected in [("site.curie", [ids["site"]]), ("other.curie", [ids["other"]]), ("site.mention", [])]:
        episode = selections.start_episode(scope, x.aid, episode={"episode_id": uuid4().hex,
            "reason": "explicit scoped review", "obligations": [{"target_id": x.root, "mode": "extraction_only"}]})
        intent = authority.record_revalidation_intent(runtime.CapturedArtifact(scope, x.aid),
            envelope=current, episode_id=episode, object_id="one", field_paths=[path], actor_id=scope.owner_id)
        assert store.read_fact(scope, x.aid, intent)["content"]["overlay_ids"] == expected
    assert store.read_baseline(scope, x.aid) == x.baseline

    # A real fresh validator answer and supervisor publication releases only site.
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history.candidates import retain_candidates
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    bound = runtime.CapturedArtifact(scope, x.aid)
    matches = DomainPackValidationRegistry.from_domain_pack(x.pack).match_bindings(current)
    current = selections.declare_envelope_episode(bound, current, matches)
    episode = current.metadata["review_episode_id"]
    authority.record_revalidation_intent(bound, envelope=current, episode_id=episode,
        object_id="one", field_paths=["site.curie"], actor_id=scope.owner_id)
    match, = matches
    request = build_domain_validation_request(match).request
    job = ValidatorDispatchJob(match, request, history_episode_id=episode)
    invocation = runtime.begin_jobs(bound, [job], operation_id=uuid4().hex, receipt={})
    result = DomainValidatorResultBase.model_validate({**_result_payload(request,
        resolved_values={"curie": "G:2", "name": "validated site"}), "resolved_objects": []})
    runtime.complete_jobs(bound, invocation, [result])
    current = retain_candidates(current, [ValidatorResultMaterializationInput(match, request, result)],
        scope=scope, source_envelope_revision=x.revision + 1).envelope
    target = runtime.request_target(bound, job)
    with store.SessionLocal() as db:
        version = store._current(db, x.aid, target)["version"]
    projected, _, _ = publish_decisions(scope, checkpoint_request=replace(x.checkpoint,
        envelope=current, expected_revision=x.revision + 1, session_id=session_id), episode_id=episode,
        operation_id=uuid4().hex, actor={"type": "agent", "id": "supervisor"},
        entries=[{"target_id": target, "expected_version": version, "action": "accept",
            "subject": result._history_ref["fact_id"], "reason": "Explicit assessment"}])
    assert projected.extracted_objects[0].payload["site"]["name"] == "validated site"
    assert projected.extracted_objects[0].payload["other"]["name"] == "second curator"
    with store.SessionLocal() as db:
        assert [body["value_path"] for _, body in authority.active_overlays(db, x.aid)] == ["other"]
    assert store.read_baseline(scope, x.aid) == x.baseline
