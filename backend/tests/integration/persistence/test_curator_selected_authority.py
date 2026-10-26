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
    fixture_mode = getattr(request, "param", False)
    include_support = fixture_mode is True
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history.candidates import retain_candidates
    from src.schemas.domain_envelope import DomainEnvelope, CuratableObjectEnvelope
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack, _result_payload
    pack = _multivalued_dispatch_pack(tmp_path)
    if fixture_mode == "absent":
        from dataclasses import replace
        from copy import deepcopy

        raw = deepcopy(pack.metadata.metadata)
        raw["validator_bindings"]["active"][0]["input_fields"] = {
            "query": {"source": "literal", "value": "Scripted test request"}}
        fields = [field.model_copy(update={"metadata": {}})
                  for field in pack.metadata.object_definitions[0].fields]
        definition = pack.metadata.object_definitions[0].model_copy(update={"fields": fields})
        pack = replace(pack, metadata=pack.metadata.model_copy(update={
            "metadata": raw, "object_definitions": [definition]}))
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
            payload={} if fixture_mode == "absent" else {"evidence_code_curies": ["eco:1", "eco:2"] if fixture_mode == "two" else ["eco:1"]})])
    env = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        typed=env.model_dump(mode="json"), envelope=env, provenance={})
    aid = env.metadata["validation_history"]["artifact_id"]
    bound = runtime.CapturedArtifact(scope, aid)
    matches = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(env)
    match = matches[0]
    request = build_domain_validation_request(match).request
    job = ValidatorDispatchJob(match, request)
    target = runtime.request_target(bound, job)
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    state = SimpleNamespace(env=env, aid=aid, bound=bound, target=target, revision=0, source=uuid4(), pack=pack)

    def episode():
        state.env = s.declare_envelope_episode(bound, state.env, matches)
        state.episode = state.env.metadata["review_episode_id"]
        return state.episode

    def propose(value, *, status="resolved"):
        from dataclasses import replace
        invocation = runtime.begin_jobs(bound, [replace(job, history_episode_id=state.episode)], operation_id=uuid4().hex, receipt={})
        data = _result_payload(request, status=status, resolved_values={"curie": value} if value is not None else {})
        data["resolved_objects"] = ([{"object_type": "SupportingTerm", "canonical_id": value,
            "payload": {"primary_external_id": value, "name": value}}] if include_support and status == "resolved" else [])
        result = DomainValidatorResultBase.model_validate(data)
        runtime.complete_jobs(bound, invocation, [result])
        state.env = retain_candidates(state.env, [ValidatorResultMaterializationInput(match, request, result)],
            scope=scope, source_envelope_revision=max(state.revision, 1)).envelope
        return result._history_ref["fact_id"]

    def publish(completion, action="accept"):
        with h.SessionLocal() as db:
            version = h._current(db, aid, target)["version"]
        state.env, manifest, checkpoint = publish_decisions(scope,
            checkpoint_request=DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=state.env,
                expected_revision=state.revision, document_id=scope.document_id,
                session_id=getattr(state, "workspace_session_id", None), adapter_key="fixture", source_extraction_result_id=state.source),
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
    current_unit = s._selected_unit

    def pre_navigation_unit(*args, **kwargs):
        # Simulate a publication written before pending-answer navigation metadata.
        # Baseline and curator selections never carried validator unit metadata.
        unit = current_unit(*args, **kwargs)
        if unit["selected"] in ("baseline", "curator"):
            unit.pop("decision_unit", None)
        return unit

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
    with monkeypatch.context() as legacy_writer:
        legacy_writer.setattr(s, "_selected_unit", pre_navigation_unit)
        env, _, _ = publish_decisions(scope, checkpoint_request=checkpoint, episode_id=ep,
            operation_id=uuid4().hex, actor={"type": "agent", "id": "supervisor"},
            entries=[{"target_id": root, "expected_version": 0, "action": "accept", "subject": "baseline", "reason": "explicit extraction choice"}])
    baseline_ref = env.metadata["selected_projection"]
    assert read_published_projection(scope, baseline_ref, finalized=True)[1]["ready"]
    patch = EnvelopeFieldPatch(envelope_id=env.envelope_id, expected_revision=1, object_id="one",
        field_path="subject.primary_external_id", operation=EnvelopeFieldPatchOperation.REPLACE_IDENTITY,
        before={"primary_external_id": "G:1", "gene_symbol": "original"},
        value={"primary_external_id": "G:2", "gene_symbol": "curator"})
    edited = apply_curator_field_patch(env, pack, patch, current_revision=1,
        actor_id=scope.owner_id, actor_display_name="Curator")
    assert edited.accepted, edited.errors
    assert edited.envelope.extracted_objects[0].metadata["validation_history"] == env.extracted_objects[0].metadata["validation_history"]
    with monkeypatch.context() as legacy_writer:
        legacy_writer.setattr(s, "_selected_unit", pre_navigation_unit)
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
        assert x.publish(answer)["ready"]
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

@pytest.mark.parametrize("specialized", [False, True], ids=["base", "gene-result"])
def test_profile_curator_revalidation_and_group_authorized_withdrawal(scope, monkeypatch, specialized):
    from dataclasses import replace
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history.candidates import retain_candidates
    from src.lib.validation_history.curator_values import resolve_curator_identity
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatch, apply_curator_field_patch
    from src.lib.domain_envelopes.persistence import write_domain_envelope_checkpoint
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_profile_validation import example
    from tests.unit.lib.domain_packs.test_profile_materialization import resolvable

    raw, cap, pack = example.__wrapped__()
    cap = replace(cap, binding=replace(cap.binding, required_any_active_group=("WB",)))
    raw["validator_mappings"][0]["capability_fingerprint"] = cap.fingerprint()
    # Prepare the pinned profile, then compile with actual authenticated groups.
    source, _ = resolvable((raw, cap, pack))
    source, context = _persist_test_profile(scope, raw, cap, pack, source, monkeypatch)
    source = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
        typed=source.model_dump(mode="json"), envelope=source, provenance={})
    aid = source.metadata["validation_history"]["artifact_id"]
    bound = runtime.CapturedArtifact(scope, aid)
    match, = context.registry.match_bindings(source)
    request = build_domain_validation_request(match).request
    target = runtime.request_target(bound, ValidatorDispatchJob(match, request))
    source = s.declare_envelope_episode(bound, source, [match], authenticated_groups=("WB",))
    revision = 0
    extraction_id = uuid4()

    def publish(value):
        nonlocal source, revision
        episode = source.metadata["review_episode_id"]
        job = ValidatorDispatchJob(match, request, history_episode_id=episode)
        invocation = runtime.begin_jobs(bound, [job], operation_id=uuid4().hex, receipt={})
        result = DomainValidatorResultBase(status="resolved", request_id=request.request_id,
            validator_binding_id=request.validator_binding_id, validator_agent=request.validator_agent,
            target=request.target, resolved_values={"identifier": value}, resolved_objects=[],
            missing_expected_fields=[], candidates=[], lookup_attempts=[], explanation="Reviewed lookup", curator_message=None)
        if specialized:
            from src.lib.config.schema_discovery import resolve_output_schema
            result = resolve_output_schema("GeneResultEnvelope").model_validate({
                **result.model_dump(mode="json"),
                "gene_candidates": [{"gene_id": "GENE:example-1", "symbol": "geneA"}],
            })
        runtime.complete_jobs(bound, invocation, [result])
        retain_candidates(source, [ValidatorResultMaterializationInput(match, request, result)], scope=scope)
        with h.SessionLocal() as db:
            version = h._current(db, aid, target)["version"]
        source, manifest, checkpoint = publish_decisions(scope,
            checkpoint_request=DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=source,
                expected_revision=revision, document_id=scope.document_id, adapter_key="fixture",
                source_extraction_result_id=extraction_id, authenticated_groups=("WB",), execution_receipt=context.receipt),
            episode_id=episode, operation_id=uuid4().hex, actor={"type": "agent", "id": "supervisor"},
            entries=[{"target_id": target, "expected_version": version, "action": "accept",
                "subject": result._history_ref["fact_id"], "reason": "explicit review"}])
        revision = checkpoint.revision
        assert manifest["ready"]

    def edit(value):
        nonlocal source, revision
        obj = source.extracted_objects[0]
        patched = apply_curator_field_patch(source, pack,
            EnvelopeFieldPatch(envelope_id=source.envelope_id, expected_revision=revision,
                object_id=obj.object_id or obj.pending_ref_id, field_path="attributes.gene.gene_id",
                before=obj.payload["attributes"]["gene"]["gene_id"], value=value),
            current_revision=revision, actor_id=scope.owner_id, actor_display_name="Curator", profile=context.profile)
        assert patched.accepted, patched.errors
        with h.SessionLocal.begin() as db:
            checkpoint = write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(
                project_key="fixture", envelope=patched.envelope, expected_revision=revision,
                document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=extraction_id,
                authenticated_groups=("WB",)))
        revision = checkpoint.revision
        from src.lib.validation_history.projection import latest_publication_reference
        source, _ = read_published_projection(scope, latest_publication_reference(scope, aid), current=True)

    publish("EX:FIRST")
    edit("EX:CURATOR")
    obj_id = source.extracted_objects[0].object_id or source.extracted_objects[0].pending_ref_id
    args = dict(envelope=source, object_id=obj_id, value_path="attributes.gene",
        identity_keys=("gene_id",), action="withdraw_override", reference=None, include_state=True)
    with pytest.raises(h.HistoryConflict):
        resolve_curator_identity(scope, **args, authenticated_groups=())
    underlying = resolve_curator_identity(scope, **args, authenticated_groups=("WB",))
    assert underlying["gene_id"] == "EX:FIRST"
    assert underlying["lookup_outcome"] == "matched"
    source = s.declare_envelope_episode(bound, source, [match], authenticated_groups=("WB",),
        curator_revalidation={"object_id": obj_id, "field_paths": ["attributes.gene.gene_id"], "actor_id": scope.owner_id})
    assert source.extracted_objects[0].payload["attributes"]["gene"]["gene_id"] == "EX:CURATOR"
    publish("EX:SECOND")
    gene = source.extracted_objects[0].payload["attributes"]["gene"]
    assert gene["gene_id"] == "EX:SECOND"
    assert gene["mention"] == "daf-16"
    assert "curator_override" not in gene
    assert h.read_baseline(scope, aid)["normalized"]["extracted_objects"][0]["payload"]["attributes"]["gene"]["gene_id"] is None


def test_overlay_sequence_and_retired_child_changes(scope):
    from tests.integration.persistence.test_selected_value_authority import captured_source
    from tests.integration.persistence.test_validation_history import decision
    env = captured_source(scope)
    aid = env.metadata["validation_history"]["artifact_id"]
    obj = env.extracted_objects[0]
    root = obj.metadata["validation_history"]["target_id"]
    def edit(anchor, value):
        with h.SessionLocal.begin() as db:
            c.record_patch(db, scope, aid, event_id=uuid4().hex, actor_id=scope.owner_id,
                body=c.bind_patch_anchors(env, {"target_id": root, "object_id": "one", "value_path": anchor,
                    "action": "override", "changes": [{"target_id": root, "path": "terms[0].label", "present": True, "value": value}]}))
    edit("terms", "first whole list edit")
    edit("terms[0].label", "older child edit")
    edit("terms", "newest whole list edit")
    with h.SessionLocal.begin() as db:
        assert c.apply_overlays(db, aid, env).extracted_objects[0].payload["terms"][0]["label"] == "newest whole list edit"
    metadata, retired = runtime.retire_element_projection(obj.metadata, "terms[0]", payload=obj.payload)
    obj.metadata = metadata
    obj.payload["terms"].pop(0)
    decision(scope, aid, retired, 0, "retire")
    with h.SessionLocal.begin() as db:
        assert c.apply_overlays(db, aid, env).extracted_objects[0].payload["terms"] == [{"label": "b"}]


def test_failed_intent_does_not_supersede_current_episode(scope, scenario):
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    x = scenario
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.validation_history.projection import read_dispatch_source
    source = read_dispatch_source(x.bound, x.env)
    matches = DomainPackValidationRegistry.from_domain_pack(resolve_curation_domain_pack_by_id(x.env.domain_pack_id)).match_bindings(source)
    before = x.env.metadata["selected_projection"]
    with pytest.raises(h.HistoryAccessError):
        s.declare_envelope_episode(x.bound, x.env, matches, curator_revalidation={
            "object_id": "one", "field_paths": [], "actor_id": "not-owner"})
    _, manifest = read_published_projection(scope, before, current=True)
    assert manifest["episode_id"] == x.episode
    assert manifest["ready"]


def _persist_test_profile(scope, raw, cap, pack, source, monkeypatch):
    from src.lib.agent_studio import generic_profile_service
    from src.lib.agent_studio.execution_revision_service import append_execution_revision, current_execution_receipt
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.curation_workspace.execution_contracts import resolve_receipt_profile
    from src.models.sql.agent import Agent
    from src.models.sql.pdf_document import PDFDocument
    from src.schemas.agent_execution_revision import AgentOutputContract
    from uuid import UUID
    from src.lib.domain_packs import profile_validation as pv
    monkeypatch.setattr("src.lib.agent_studio.profile_mapping_service.capability_catalog", lambda **kwargs: [cap])
    monkeypatch.setattr(pv, "capability_catalog", lambda **kwargs: [cap])
    with h.SessionLocal.begin() as db:
        owner = db.get(PDFDocument, UUID(scope.document_id)).user_id
        _, revision = generic_profile_service.create_profile(db, owner, raw, active_group_ids=("WB",))
        agent = Agent(id=uuid4(), agent_key="ca_" + uuid4().hex, user_id=owner,
            name="Authority fixture", instructions="Fixture", model_id="test-model",
            model_temperature=0.0, visibility="private", tool_ids=["stage_generic_object", "finalize_generic_extraction"], group_rules_enabled=False)
        db.add(agent)
        db.flush()
        contract = AgentOutputContract.model_validate({"output_state": "structured_extraction",
            "output_mode": "profile_bound_generic", "generic_profile_ref": {"profile_id": revision.profile_id,
            "profile_revision_id": revision.id, "revision": revision.revision, "fingerprint": revision.fingerprint}})
        append_execution_revision(db, agent, capture_execution_snapshot(db, agent, contract), user_id=owner, expected_revision_id=None)
        receipt = current_execution_receipt(db, agent.agent_key, owner, active_group_ids=["WB"])
        profile = resolve_receipt_profile(db, receipt)
    context = pv.compile_profile_validation(receipt, profile, pack, capabilities=[cap], active_group_ids=("WB",))
    source.envelope_id = uuid4().hex
    source.metadata["execution_receipt"] = receipt.model_dump(mode="json")
    source.metadata["extraction_metadata"]["provenance"].update(produced_by=receipt.agent_key,
        execution_receipt=receipt.model_dump(mode="json"), generic_profile_ref=profile.receipt)
    for obj in source.extracted_objects:
        obj.metadata["generic_profile_ref"] = profile.receipt
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    return source, context


def test_profile_parent_edit_retains_unrequested_siblings_after_leaf_acceptance(scope, monkeypatch):
    from copy import deepcopy
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history.candidates import retain_candidates
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatch, apply_curator_field_patch
    from src.lib.domain_envelopes.persistence import write_domain_envelope_checkpoint
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_profile_validation import example
    from tests.unit.lib.domain_packs.test_profile_materialization import prepared
    raw, cap, pack = example.__wrapped__()
    raw["fields"] = [{"key": "term", "required": True, "value_schema": {"kind": "object", "fields": [
        {"key": key, "required": key == "paper_name", "value_schema": {"kind": "string"}} for key in ("paper_name", "name", "rank", "note")]}}]
    raw["validator_mappings"][0].update(inputs={"mention": {"field_path": "attributes.term.paper_name"}},
        outputs={"identifier": "attributes.term.name"})
    rank_mapping = deepcopy(raw["validator_mappings"][0])
    rank_mapping.update(mapping_id="rank", outputs={"identifier": "attributes.term.rank"})
    raw["validator_mappings"].append(rank_mapping)
    source, _ = prepared((raw, cap, pack), attributes={"term": {"paper_name": "Paper", "name": "Extracted", "rank": "Extracted rank", "note": "Source note"}})
    source, context = _persist_test_profile(scope, raw, cap, pack, source, monkeypatch)
    source = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex, typed=source.model_dump(mode="json"), envelope=source, provenance={})
    aid = source.metadata["validation_history"]["artifact_id"]
    bound = runtime.CapturedArtifact(scope, aid)
    matches = context.registry.match_bindings(source)
    requests = [build_domain_validation_request(match).request for match in matches]
    source = s.declare_envelope_episode(bound, source, matches)
    revision = 0
    extraction_id = uuid4()
    def publish(reject_rank=False):
        nonlocal source, revision
        entries = []
        for match, request in zip(matches, requests, strict=True):
            job = ValidatorDispatchJob(match, request, history_episode_id=source.metadata["review_episode_id"])
            invocation = runtime.begin_jobs(bound, [job], operation_id=uuid4().hex, receipt={})
            result = DomainValidatorResultBase(status="resolved", request_id=request.request_id,
                validator_binding_id=request.validator_binding_id, validator_agent=request.validator_agent, target=request.target,
                resolved_values={"identifier": "Selected"}, resolved_objects=[], missing_expected_fields=[],
                candidates=[], lookup_attempts=[], explanation="Explicit result", curator_message=None)
            runtime.complete_jobs(bound, invocation, [result])
            retain_candidates(source, [ValidatorResultMaterializationInput(match, request, result)], scope=scope)
            target = runtime.request_target(bound, job)
            with h.SessionLocal() as db:
                version = h._current(db, aid, target)["version"]
            entries.append({"target_id": target, "expected_version": version,
                "action": "reject" if reject_rank and "attributes.term.rank" in request.expected_result_fields.values() else "accept",
                "subject": result._history_ref["fact_id"], "reason": "Explicit review"})
        source, manifest, checkpoint = publish_decisions(scope,
            checkpoint_request=DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=source,
                expected_revision=revision, document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=extraction_id,
                execution_receipt=context.receipt, authenticated_groups=("WB",)),
            episode_id=source.metadata["review_episode_id"], operation_id=uuid4().hex,
            actor={"type": "agent", "id": "supervisor"}, entries=entries)
        revision = checkpoint.revision
        return manifest
    publish()
    obj = source.extracted_objects[0]
    before = deepcopy(obj.payload["attributes"]["term"])
    edited = {**before, "name": "Curator name", "rank": "Curator rank", "note": "Curator note"}
    patch = apply_curator_field_patch(source, pack, EnvelopeFieldPatch(envelope_id=source.envelope_id,
        expected_revision=revision, object_id=obj.object_id or obj.pending_ref_id, field_path="attributes.term",
        before=before, value=edited), current_revision=revision, actor_id=scope.owner_id, actor_display_name="Curator", profile=context.profile)
    assert patch.accepted, patch.errors
    with h.SessionLocal.begin() as db:
        checkpoint = write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(project_key="fixture", envelope=patch.envelope,
            expected_revision=revision, document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=extraction_id, authenticated_groups=("WB",)))
    revision = checkpoint.revision
    from src.lib.validation_history.projection import latest_publication_reference
    source, _ = read_published_projection(scope, latest_publication_reference(scope, aid), current=True)
    old_reference = source.metadata["selected_projection"]
    source = s.declare_envelope_episode(bound, source, matches, curator_revalidation={"actor_id": scope.owner_id,
        "object_id": obj.object_id or obj.pending_ref_id, "field_paths": ["attributes.term.name"]})
    manifest = publish(reject_rank=True)
    assert not manifest["ready"]
    assert source.extracted_objects[0].payload["attributes"]["term"] == {**edited, "name": "Selected"}
    old, _ = read_published_projection(scope, old_reference)
    assert old.extracted_objects[0].payload["attributes"]["term"] == edited


def test_child_scope_cannot_authorize_whole_collection_override(scope):
    from tests.integration.persistence.test_selected_value_authority import captured_source
    env = captured_source(scope)
    aid = env.metadata["validation_history"]["artifact_id"]
    root = env.extracted_objects[0].metadata["validation_history"]["target_id"]
    bound = runtime.CapturedArtifact(scope, aid)
    with h.SessionLocal.begin() as db:
        edit = c.record_patch(db, scope, aid, event_id=uuid4().hex, actor_id=scope.owner_id,
            body=c.bind_patch_anchors(env, {"target_id": root, "object_id": "one", "value_path": "terms",
                "action": "override", "changes": [{"target_id": root, "path": "terms", "present": True,
                    "value": [{"label": "new whole list"}]}]}))
    ep = s.start_episode(scope, aid, episode={"episode_id": uuid4().hex, "reason": "review", "obligations": [{"target_id": root, "mode": "extraction_only"}]})
    for fields in (["terms[0].label"], ["terms"], []):
        with pytest.raises(h.HistoryConflict, match="structural curator edit"):
            c.record_revalidation_intent(bound, envelope=env, episode_id=ep, object_id="one", field_paths=fields, actor_id=scope.owner_id)
    assert h.read_fact(scope, aid, edit)["content"]["changes"][0]["value"] == [{"label": "new whole list"}]
    # A newer leaf must not be released and expose the older aggregate value.
    with h.SessionLocal.begin() as db:
        c.record_patch(db, scope, aid, event_id=uuid4().hex, actor_id=scope.owner_id,
            body=c.bind_patch_anchors(env, {"target_id": root, "object_id": "one", "value_path": "terms[0].label",
                "action": "override", "changes": [{"target_id": root, "path": "terms[0].label", "present": True, "value": "newer leaf"}]}))
    with pytest.raises(h.HistoryConflict, match="structural curator edit"):
        c.record_revalidation_intent(bound, envelope=env, episode_id=ep, object_id="one", field_paths=["terms[0].label"], actor_id=scope.owner_id)


@pytest.mark.parametrize("scenario", ["two"], indirect=True)
@pytest.mark.parametrize("freeze_before_reorder", [True, False])
def test_revalidation_release_follows_stable_target_across_reorder(scope, scenario, freeze_before_reorder):
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validator_dispatch import ValidatorDispatchJob
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.validation_history.candidates import retain_candidates
    from src.lib.validation_history.projection import read_dispatch_source
    from src.schemas.domain_validator import DomainValidatorResultBase
    from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload
    x = scenario
    x.edit("ECO:CURATOR_A")
    x.publish(x.first)
    x.episode_fn()
    c.record_revalidation_intent(x.bound, envelope=x.env, episode_id=x.episode, object_id="one",
        field_paths=["evidence_code_curies[0]"], actor_id=scope.owner_id)
    def freeze():
        source = read_dispatch_source(x.bound, x.env)
        match = DomainPackValidationRegistry.from_domain_pack(x.pack).match_bindings(source)[0]
        request = build_domain_validation_request(match).request
        job = ValidatorDispatchJob(match, request, history_episode_id=x.episode)
        invocation = runtime.begin_jobs(x.bound, [job], operation_id=uuid4().hex, receipt={})
        return match, request, job, invocation
    if freeze_before_reorder:
        match, request, job, invocation = freeze()
    obj = x.env.extracted_objects[0]
    ids = obj.metadata["validation_history"]["elements"]["evidence_code_curies"]
    x.env.extracted_objects[0] = runtime.reorder_element_projection(obj, "evidence_code_curies", list(reversed(ids)))
    if not freeze_before_reorder:
        match, request, job, invocation = freeze()
    payload = _result_payload(request, resolved_values={"curie": "ECO:NEW"})
    payload["resolved_objects"] = []
    result = DomainValidatorResultBase.model_validate(payload)
    runtime.complete_jobs(x.bound, invocation, [result])
    retain_candidates(x.env, [ValidatorResultMaterializationInput(match, request, result)], scope=scope)
    target = runtime.request_target(x.bound, job)
    with h.SessionLocal() as db:
        version = h._current(db, x.aid, target)["version"]
    env, _, _ = publish_decisions(scope, checkpoint_request=DomainEnvelopeCheckpointRequest(project_key="fixture",
        envelope=x.env, expected_revision=x.revision, document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=x.source),
        episode_id=x.episode, operation_id=uuid4().hex, actor={"type": "agent", "id": "supervisor"},
        entries=[{"target_id": target, "expected_version": version, "action": "accept",
            "subject": result._history_ref["fact_id"], "reason": "review stable target"}])
    expected = ["eco:2", "ECO:NEW"] if freeze_before_reorder else ["ECO:NEW", "ECO:CURATOR_A"]
    assert env.extracted_objects[0].payload["evidence_code_curies"] == expected
    original = env.validator_results["validation-fact:" + result._history_ref["fact_id"]]
    assert original["result"]["target"]["field_path"] == request.target.field_path


def test_new_object_and_null_to_object_changes_are_independent_leaves():
    for before in ({}, {"term": None}):
        changes = c.payload_changes(before, {"term": {"name": "A", "rank": "R", "note": None}}, "root")
        assert {item["path"] for item in changes} == {"term.name", "term.rank", "term.note"}


@pytest.mark.parametrize("requested", [["terms[0].label"], ["terms[0]"], []])
@pytest.mark.parametrize("initial", [{"label": "old"}, "old"])
def test_null_parent_then_new_leaf_cannot_resurrect_old_aggregate(scope, requested, initial):
    from tests.integration.persistence.test_selected_value_authority import captured_source
    env = captured_source(scope)
    aid = env.metadata["validation_history"]["artifact_id"]
    root = env.extracted_objects[0].metadata["validation_history"]["target_id"]
    with h.SessionLocal.begin() as db:
        for before, after in [(initial, None), (None, {"label": "new"})]:
            changes = c.payload_changes(before, after, root, "terms[0]")
            c.record_patch(db, scope, aid, event_id=uuid4().hex, actor_id=scope.owner_id,
                body=c.bind_patch_anchors(env, {"target_id": root, "object_id": "one", "value_path": "terms[0]",
                    "action": "override", "changes": changes}))
    ep = s.start_episode(scope, aid, episode={"episode_id": uuid4().hex, "reason": "review",
        "obligations": [{"target_id": root, "mode": "extraction_only"}]})
    with pytest.raises(h.HistoryConflict, match="structural curator edit"):
        c.record_revalidation_intent(runtime.CapturedArtifact(scope, aid), envelope=env, episode_id=ep,
            object_id="one", field_paths=requested, actor_id=scope.owner_id)
    with h.SessionLocal.begin() as db:
        assert c.apply_overlays(db, aid, env).extracted_objects[0].payload["terms"][0]["label"] == "new"


def test_queued_revalidation_cannot_authorize_edit_made_after_click(scope, scenario):
    from sqlalchemy import select, func
    from src.models.sql.validation_history import ValidationFact
    x = scenario
    original_edit = x.edit('ECO:AT_CLICK')
    with h.SessionLocal() as db:
        sequence = db.scalar(select(func.max(ValidationFact.sequence)).where(ValidationFact.artifact_id == x.aid))
    later_edit = x.edit('ECO:AFTER_CLICK')
    x.episode_fn()
    with h.SessionLocal.begin() as db:
        intent = c.record_revalidation_intent_in_transaction(db, x.bound, envelope=x.env,
            episode_id=x.episode, object_id='one', field_paths=[], actor_id=scope.owner_id,
            through_sequence=sequence)
    saved = h.read_fact(scope, x.aid, intent)['content']
    assert saved['overlay_ids'] == [original_edit]
    assert later_edit not in saved['overlay_ids']
    completion = x.propose('ECO:NEW_ANSWER')
    x.publish(completion)
    assert x.env.extracted_objects[0].payload['evidence_code_curies'] == ['ECO:AFTER_CLICK']


@pytest.mark.parametrize("scenario", ["absent"], indirect=True)
def test_selected_answer_for_absent_field_does_not_rewrite_original(scope, scenario):
    x = scenario
    assert h.read_baseline(scope, x.aid)["normalized"]["extracted_objects"][0]["payload"] == {}
    assert "evidence_code_curies" not in x.env.extracted_objects[0].metadata["validation_history"]["fields"]
    assert x.env.extracted_objects[0].payload["evidence_code_curies"] == "ECO:VALIDATED"
    first_reference = x.env.metadata["selected_projection"]
    x.episode_fn()
    unresolved = x.propose(None, status="unresolved")
    manifest = x.publish(unresolved)
    assert manifest["ready"]
    assert "evidence_code_curies" not in x.env.extracted_objects[0].payload
    assert h.read_fact(scope, x.aid, unresolved)["content"]["result"]["status"] == "unresolved"
    assert read_published_projection(scope, first_reference)[0].extracted_objects[0].payload["evidence_code_curies"] == "ECO:VALIDATED"


@pytest.mark.parametrize("scenario", ["absent"], indirect=True)
def test_curator_source_edit_after_selected_absent_answer(scope, scenario, monkeypatch):
    from dataclasses import replace
    from src.schemas.domain_pack_metadata import DomainPackFieldDefinition
    from src.lib.domain_envelopes.patches import EnvelopeFieldPatch, apply_curator_field_patch
    from src.lib.domain_envelopes.persistence import write_domain_envelope_checkpoint, load_domain_envelope
    from src.lib.validation_history.projection import source_envelope_in_transaction

    x = scenario
    old_reference = x.env.metadata["selected_projection"]
    definition = x.pack.metadata.object_definitions[0]
    definition = definition.model_copy(update={"fields": [*definition.fields,
        DomainPackFieldDefinition.model_validate({"field_path": "curator_note", "field_type": "string",
                                                 "metadata": {"editable": True}})]})
    pack = replace(x.pack, metadata=x.pack.metadata.model_copy(update={"object_definitions": [definition]}))
    monkeypatch.setattr("src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id", lambda _: pack)
    patched = apply_curator_field_patch(x.env, pack, EnvelopeFieldPatch(
        envelope_id=x.env.envelope_id, expected_revision=x.revision, object_id="one",
        field_path="curator_note", before=None, value="Curator source note"),
        current_revision=x.revision, actor_id=scope.owner_id, actor_display_name="Curator")
    assert patched.accepted, patched.errors
    with h.SessionLocal.begin() as db:
        saved = write_domain_envelope_checkpoint(db, DomainEnvelopeCheckpointRequest(
            project_key="fixture", envelope=patched.envelope, expected_revision=x.revision,
            document_id=scope.document_id, adapter_key="fixture", source_extraction_result_id=x.source))
        current = load_domain_envelope(db, x.env.envelope_id)
        source = source_envelope_in_transaction(db, scope, x.aid, current)
        assert source.extracted_objects[0].payload == {"curator_note": "Curator source note"}
        assert h._current(db, x.aid, x.target)["active"]
    assert saved.revision == x.revision + 1
    assert h.read_baseline(scope, x.aid)["normalized"]["extracted_objects"][0]["payload"] == {}
    assert read_published_projection(scope, old_reference)[0].extracted_objects[0].payload == {
        "evidence_code_curies": "ECO:VALIDATED"}
