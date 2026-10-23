"""Real selected publication honors scoped, versioned curator revalidation intent."""
from uuid import uuid4
from types import SimpleNamespace

import pytest

from src.lib.validation_history import curator_authority as c, selections as s, store as h, runtime
from src.lib.validation_history.projection import publish_decisions, read_published_projection
from src.lib.domain_envelopes.persistence import DomainEnvelopeCheckpointRequest
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated


@pytest.fixture
def scenario(scope, tmp_path, monkeypatch, request):
    include_support = getattr(request, "param", False)
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history.candidates import retain_candidates
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack, _result_payload
    pack = _multivalued_dispatch_pack(tmp_path)
    if include_support:
        from dataclasses import replace
        from src.schemas.domain_pack_metadata import DomainPackObjectDefinition
        supporting = DomainPackObjectDefinition.model_validate({
            "object_type": "SupportingTerm", "display_name": "Supporting term",
            "metadata": {"object_role": "validated_reference"},
            "fields": [{"field_path": "primary_external_id", "field_type": "string"},
                       {"field_path": "name", "field_type": "string", "metadata": {"editable": True}}],
        })
        pack = replace(pack, metadata=pack.metadata.model_copy(update={"object_definitions": [*pack.metadata.object_definitions, supporting]}))
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id="one",
            payload={"evidence_code_curies": ["eco:1"]})])
    env = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"), envelope=env, provenance={})
    aid = env.metadata["validation_history"]["artifact_id"]
    bound = runtime.CapturedArtifact(scope, aid)
    match = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(env)[0]
    request = build_domain_validation_request(match).request
    job = ValidatorDispatchJob(match, request)
    target = runtime.request_target(bound, job)
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    state = SimpleNamespace(env=env, aid=aid, bound=bound, target=target, revision=0, source=uuid4())

    def episode():
        state.episode = s.start_episode(scope, aid, episode={"episode_id": uuid4().hex, "reason": "review",
            "obligations": [{"target_id": target, "mode": "validation", "binding_id": request.validator_binding_id}]})
        return state.episode

    def propose(value, *, status="resolved"):
        from dataclasses import replace
        invocation = runtime.begin_jobs(bound, [replace(job, history_episode_id=state.episode)], operation_id=uuid4().hex, receipt={})
        data = _result_payload(request, status=status, resolved_values={"curie": value} if value is not None else {})
        data["resolved_objects"] = ([{"object_type": "SupportingTerm", "canonical_id": value,
            "payload": {"primary_external_id": value, "name": value}}] if include_support and status == "resolved" else [])
        result = DomainValidatorResultBase.model_validate(data)
        runtime.complete_jobs(bound, invocation, [result])
        retain_candidates(env, [ValidatorResultMaterializationInput(match, request, result)], scope=scope)
        return result._history_ref["fact_id"]

    def publish(completion, action="accept"):
        with h.SessionLocal() as db:
            version = h._current(db, aid, target)["version"]
        state.env, manifest, checkpoint = publish_decisions(scope,
            checkpoint_request=DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=state.env,
                expected_revision=state.revision, document_id=scope.document_id,
                adapter_key="fixture", source_extraction_result_id=state.source),
            episode_id=state.episode, operation_id=uuid4().hex,
            actor={"type": "agent", "id": "supervisor"},
            entries=[{"target_id": target, "expected_version": version, "action": action,
                      "subject": completion, "reason": "explicit model review"}])
        state.revision = checkpoint.revision
        return manifest

    def edit(value):
        obj = state.env.extracted_objects[0]
        root = obj.metadata["validation_history"]["target_id"]
        with h.SessionLocal.begin() as db:
            return c.record_patch(db, scope, aid, event_id=uuid4().hex, actor_id=scope.owner_id,
                body=c.bind_patch_anchors(state.env, {"target_id": root, "object_id": "one", "value_path": "evidence_code_curies[0]",
                      "action": "override", "changes": [{"target_id": root,
                        "path": "evidence_code_curies[0]", "present": True, "value": value}]}))

    state.episode_fn, state.propose, state.publish, state.edit = episode, propose, publish, edit
    episode()
    state.first = propose("ECO:VALIDATED")
    publish(state.first)
    return state


@pytest.mark.parametrize("authorized,action,new_edit,expected", [
    (True, "accept", False, "ECO:NEW"),
    (False, "accept", False, "ECO:CURATOR"),
    (True, "reject", False, "ECO:CURATOR"),
    (True, "accept", True, "ECO:LATER_CURATOR"),
])
def test_only_authorized_accepted_revalidation_replaces_exact_curator_version(scope, scenario, authorized, action, new_edit, expected):
    x = scenario
    edit_id = x.edit("ECO:CURATOR")
    x.publish(x.first)
    old_reference = x.env.metadata["selected_projection"]
    assert x.env.extracted_objects[0].payload["evidence_code_curies"] == ["ECO:CURATOR"]
    x.episode_fn()
    if authorized:
        c.record_revalidation_intent(x.bound, envelope=x.env, episode_id=x.episode, object_id="one",
            field_paths=["evidence_code_curies[0]"], actor_id=scope.owner_id)
    # An episode beginning and an answer completing do not replace current value.
    assert read_published_projection(scope, old_reference, current=True)[0].extracted_objects[0].payload["evidence_code_curies"] == ["ECO:CURATOR"]
    if new_edit:
        x.edit("ECO:LATER_CURATOR")
    completion = x.propose("ECO:NEW")
    x.publish(completion, action)
    assert x.env.extracted_objects[0].payload["evidence_code_curies"] == [expected]
    assert h.read_fact(scope, x.aid, edit_id)["content"]["changes"][0]["value"] == "ECO:CURATOR"
    assert read_published_projection(scope, old_reference)[0].extracted_objects[0].payload["evidence_code_curies"] == ["ECO:CURATOR"]
    assert h.read_baseline(scope, x.aid)["normalized"]["extracted_objects"][0]["payload"] == {"evidence_code_curies": ["eco:1"]}


def test_revalidation_intent_cannot_impersonate_another_curator(scope, scenario):
    x = scenario
    x.edit("ECO:CURATOR")
    with pytest.raises(h.HistoryAccessError):
        c.record_revalidation_intent(x.bound, envelope=x.env, episode_id=x.episode, object_id="one",
            field_paths=[], actor_id="some-other-user")


def test_real_curator_patch_republishes_atomically_and_withdraws_without_losing_source(scope, tmp_path, monkeypatch):
    from dataclasses import replace
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.lib.domain_packs.registry import LoadedDomainPack
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatch, EnvelopeFieldPatchOperation, apply_curator_field_patch
    from src.lib.domain_envelopes.persistence import write_domain_envelope_checkpoint
    from src.lib.validation_history.curator_values import resolve_curator_identity
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    from tests.unit.lib.domain_packs.test_resolvable_values import _expression_metadata
    metadata = _expression_metadata()
    definition = metadata.object_definitions[0]
    definition = definition.model_copy(update={"fields": [field.model_copy(update={"metadata": {**field.metadata, "editable": True}}) for field in definition.fields]})
    metadata = metadata.model_copy(update={"metadata": {}, "object_definitions": [definition]})
    pack = LoadedDomainPack(pack_id=metadata.pack_id, display_name=metadata.display_name, version=metadata.version,
        pack_path=tmp_path, metadata_path=tmp_path / "pack.yaml", metadata=metadata)
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    gene = {"mention": "paper gene", "primary_external_id": "G:1", "gene_symbol": "original",
            "resolution_state": "resolved", "lookup_outcome": "matched"}
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=metadata.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_id="one", object_type="Expression",
            payload={"subject": dict(gene), "experiment": {"entity_assayed": dict(gene)}, "slim_terms": []})])
    env = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, typed=env.model_dump(mode="json"), envelope=env, provenance={})
    aid = env.metadata["validation_history"]["artifact_id"]
    root = env.extracted_objects[0].metadata["validation_history"]["target_id"]
    ep = s.start_episode(scope, aid, episode={"episode_id": uuid4().hex, "reason": "extraction choice",
        "obligations": [{"target_id": root, "mode": "extraction_only"}]})
    checkpoint = DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=env, expected_revision=0,
        document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=uuid4())
    env, _, _ = publish_decisions(scope, checkpoint_request=checkpoint, episode_id=ep,
        operation_id=uuid4().hex, actor={"type": "agent", "id": "supervisor"},
        entries=[{"target_id": root, "expected_version": 0, "action": "accept", "subject": "baseline", "reason": "explicit extraction choice"}])
    baseline_ref = env.metadata["selected_projection"]
    patch = EnvelopeFieldPatch(envelope_id=env.envelope_id, expected_revision=1, object_id="one",
        field_path="subject.primary_external_id", operation=EnvelopeFieldPatchOperation.REPLACE_IDENTITY,
        before={"primary_external_id": "G:1", "gene_symbol": "original"},
        value={"primary_external_id": "G:2", "gene_symbol": "curator"})
    edited = apply_curator_field_patch(env, pack, patch, current_revision=1,
        actor_id=scope.owner_id, actor_display_name="Curator")
    assert edited.accepted, edited.errors
    assert edited.envelope.extracted_objects[0].metadata["validation_history"] == env.extracted_objects[0].metadata["validation_history"]
    with h.SessionLocal.begin() as db:
        write_domain_envelope_checkpoint(db, replace(checkpoint, envelope=edited.envelope, expected_revision=1))
        current = DomainEnvelope.model_validate(db.get(DomainEnvelopeModel, env.envelope_id).envelope_json)
    selected, manifest = read_published_projection(scope, current.metadata["selected_projection"], finalized=True)
    assert manifest["ready"]
    assert selected.extracted_objects[0].payload["subject"]["gene_symbol"] == "curator"
    assert selected.extracted_objects[0].payload["experiment"]["entity_assayed"]["gene_symbol"] == "curator"
    state = resolve_curator_identity(scope, envelope=current, object_id="one", value_path="subject",
        identity_keys=["primary_external_id", "gene_symbol"], action="withdraw_override", reference=None, include_state=True)
    assert state["primary_external_id"] == "G:1"
    withdrawn = apply_curator_field_patch(current, pack, replace(patch, patch_id=uuid4().hex, expected_revision=2,
        before=patch.value, value={key: state[key] for key in patch.value},
        selected_value_action="withdraw_override", selected_state=state), current_revision=2,
        actor_id=scope.owner_id, actor_display_name="Curator")
    assert withdrawn.accepted, withdrawn.errors
    with h.SessionLocal.begin() as db:
        write_domain_envelope_checkpoint(db, replace(checkpoint, envelope=withdrawn.envelope, expected_revision=2))
        current = DomainEnvelope.model_validate(db.get(DomainEnvelopeModel, env.envelope_id).envelope_json)
    final, _ = read_published_projection(scope, current.metadata["selected_projection"], finalized=True)
    value = final.extracted_objects[0].payload["subject"]
    assert value["primary_external_id"] == "G:1"
    assert value["lookup_outcome"] == "matched"
    assert value["mention"] == "paper gene"
    assert "curator_override" not in value
    assert read_published_projection(scope, baseline_ref)[0].extracted_objects[0].payload["subject"] == gene


@pytest.mark.parametrize("mode,expected", [("allowed_skip", "configured_opt_out"), ("other_group", "group_not_applicable"), ("required_skip", None)])
def test_coverage_exempts_only_declared_policy(scope, tmp_path, mode, expected):
    from dataclasses import replace
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack
    pack = _multivalued_dispatch_pack(tmp_path)
    env = DomainEnvelope(envelope_id=uuid4().hex, domain_pack_id=pack.pack_id,
        extracted_objects=[CuratableObjectEnvelope(object_type="Annotation", pending_ref_id="one", payload={"evidence_code_curies": ["eco:1"]})])
    env = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"), envelope=env, provenance={})
    bound = runtime.CapturedArtifact(scope, env.metadata["validation_history"]["artifact_id"])
    match = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(env)[0]
    configured = replace(match.binding, allow_opt_out=mode == "allowed_skip",
        required_any_active_group=("WB",) if mode == "other_group" else ())
    env = s.declare_envelope_episode(bound, env, [replace(match, binding=configured)],
        authenticated_groups=("ZFIN",), flow_selections={configured.binding_id: {"state": "skipped"}})
    manifest = s.read_manifest(scope, bound.artifact_id, episode_id=env.metadata["review_episode_id"])
    assert not manifest["ready"]  # An exemption still needs explicit extraction review.
    assert [item["reason"] for item in manifest["excluded_bindings"]] == ([expected] if expected else [])


def test_accepted_unresolved_revalidation_withdraws_override_without_claiming_resolution(scope, scenario):
    x = scenario
    x.edit("ECO:CURATOR")
    x.publish(x.first)
    x.episode_fn()
    c.record_revalidation_intent(x.bound, envelope=x.env, episode_id=x.episode, object_id="one", field_paths=[], actor_id=scope.owner_id)
    answer = x.propose(None, status="unresolved")
    manifest = x.publish(answer)
    assert manifest["units"][0]["scientific_status"] == "unresolved"
    assert x.env.extracted_objects[0].payload["evidence_code_curies"] != ["ECO:CURATOR"]


@pytest.mark.parametrize("scenario", [True], indirect=True)
@pytest.mark.parametrize("authorized", [False, True])
def test_edited_support_record_is_preserved_until_authorized_replacement(scope, scenario, authorized):
    x = scenario
    support = next(obj for obj in x.env.extracted_objects if obj.object_type == "SupportingTerm")
    root = support.metadata["validation_history"]["target_id"]
    with h.SessionLocal.begin() as db:
        edit = c.record_patch(db, scope, x.aid, event_id=uuid4().hex, actor_id=scope.owner_id,
            body=c.bind_patch_anchors(x.env, {"target_id": root, "object_id": support.object_id, "value_path": "name", "action": "override",
                  "contributor_completion_id": x.first,
                  "changes": [{"target_id": root, "path": "name", "present": True, "value": "Curator term label"}]}))
    x.publish(x.first)
    old_reference = x.env.metadata["selected_projection"]
    x.episode_fn()
    if authorized:
        c.record_revalidation_intent(x.bound, envelope=x.env, episode_id=x.episode, object_id="one", field_paths=[], actor_id=scope.owner_id)
    answer = x.propose("ECO:NEW")
    if not authorized:
        with pytest.raises(h.HistoryConflict, match="explicit curator revalidation"):
            x.publish(answer)
    else:
        x.publish(answer)
        new_support = next(obj for obj in x.env.extracted_objects if obj.object_type == "SupportingTerm")
        assert new_support.payload["name"] == "ECO:NEW"
        assert new_support.metadata["validation_history"]["target_id"] != root
    old, _ = read_published_projection(scope, old_reference)
    assert next(obj for obj in old.extracted_objects if obj.object_type == "SupportingTerm").payload["name"] == "Curator term label"
    assert h.read_fact(scope, x.aid, edit)["content"]["changes"][0]["value"] == "Curator term label"


def test_curator_overlay_follows_stable_element_through_reorder_and_retirement(scope):
    from tests.integration.persistence.test_selected_value_authority import captured_source
    from tests.integration.persistence.test_validation_history import decision
    env = captured_source(scope)
    aid = env.metadata["validation_history"]["artifact_id"]
    root = env.extracted_objects[0].metadata["validation_history"]["target_id"]
    body = c.bind_patch_anchors(env, {"target_id": root, "object_id": "one", "value_path": "terms[0]",
        "action": "override", "changes": [{"target_id": root, "path": "terms[0].label", "present": True, "value": "Curator A"}]})
    with h.SessionLocal.begin() as db:
        c.record_patch(db, scope, aid, event_id=uuid4().hex, actor_id=scope.owner_id, body=body)
    ids = env.extracted_objects[0].metadata["validation_history"]["elements"]["terms"]
    env.extracted_objects[0] = runtime.reorder_element_projection(env.extracted_objects[0], "terms", list(reversed(ids)))
    with h.SessionLocal.begin() as db:
        current = c.apply_overlays(db, aid, env)
    assert [term["label"] for term in current.extracted_objects[0].payload["terms"]] == ["b", "Curator A"]
    decision(scope, aid, ids[0], 0, "retire")
    with h.SessionLocal.begin() as db:
        assert c.active_overlays(db, aid) == []
