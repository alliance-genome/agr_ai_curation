"""Rebuild source values from immutable targets and publish selected projections.

The live envelope supplies stable identity/order and presentation metadata, never
the scientific source values used to rebuild a projection.
"""
from __future__ import annotations

from copy import deepcopy

from sqlalchemy import select

from src.models.sql.validation_history import ValidationBody, ValidationFact
from src.schemas.domain_envelope import DomainEnvelope
from . import selections, store


def source_envelope_in_transaction(db, scope, artifact_id, envelope):
    """Recover source/curator values by stable IDs, including reordered lists."""
    artifact = store._artifact(db, artifact_id, scope)
    ref = envelope.metadata.get("validation_history")
    if not isinstance(ref, dict) or ref.get("artifact_id") != artifact_id:
        raise store.HistoryConflict("Projection does not identify this artifact")
    baseline = db.get(ValidationBody, artifact.baseline_sha).content["normalized"]
    if baseline["envelope_id"] != envelope.envelope_id:
        raise store.HistoryConflict("Projection envelope does not match baseline")
    target_rows = db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "target",
    ))
    targets = {row.id: (row, store._content(db, row)) for row in target_rows}

    def original(tid):
        if tid not in targets:
            raise store.HistoryConflict("Projection references an unknown source target")
        return targets[tid][1]

    supplied_roots = {
        obj.metadata.get("validation_history", {}).get("target_id")
        for obj in envelope.extracted_objects
    }
    for target_id, (_, body) in targets.items():
        if "object_ref" not in body or body.get("origin", {}).get("kind") == "validator_contribution":
            continue
        try:
            store._require_active_target(db, artifact_id, target_id)
        except store.HistoryConflict:
            continue
        if target_id not in supplied_roots:
            raise store.HistoryConflict("Projection omits an active source object")
    baseline_objects = {
        obj.get("object_id") or obj.get("pending_ref_id"): obj for obj in baseline["extracted_objects"]
    }
    objects = []
    for obj in envelope.extracted_objects:
        refs = obj.metadata.get("validation_history")
        if not isinstance(refs, dict):
            raise store.HistoryConflict("Projection object has no immutable source")
        root = original(refs["target_id"])
        if root.get("origin", {}).get("kind") == "validator_contribution":
            # Selected contributions are rebuilt from their exact completion below.
            continue
        try:
            store._require_active_target(db, artifact_id, refs["target_id"])
        except store.HistoryConflict:
            continue
        if root.get("object_ref") not in {obj.object_id, obj.pending_ref_id}:
            raise store.HistoryConflict("Projection object identity mismatch")

        def value_at(value, path, parent):
            if isinstance(value, dict):
                # Current fields may include an explicit curator-added field.
                names = set(value)
                prefix = path + "." if path else ""
                for candidate in refs["fields"]:
                    if candidate.startswith(prefix):
                        suffix = candidate[len(prefix):]
                        if suffix and "." not in suffix and "[" not in suffix:
                            names.add(suffix)
                result = {}
                for key in sorted(names):
                    child_path = prefix + key
                    tid = refs["fields"].get(child_path)
                    if tid is None:
                        raise store.HistoryConflict("Source field lacks stable identity")
                    row, body = targets[tid]
                    if row.parent_id != parent:
                        raise store.HistoryConflict("Source field belongs to another parent")
                    store._require_active_target(db, artifact_id, tid)
                    result[key] = value_at(body["value"], child_path, tid)
                return result
            if isinstance(value, list):
                if path not in refs["elements"]:
                    raise store.HistoryConflict("Source list lacks stable element order")
                result = []
                for index, tid in enumerate(refs["elements"][path]):
                    row, body = targets[tid]
                    if row.parent_id != parent:
                        raise store.HistoryConflict("Source element belongs to another list")
                    store._require_active_target(db, artifact_id, tid)
                    result.append(value_at(body["value"], f"{path}[{index}]", tid))
                return result
            return deepcopy(value)

        payload = value_at(root["payload"], "", refs["target_id"])
        from src.schemas.domain_envelope import ObjectRef
        source_record = baseline_objects.get(root["object_ref"], root)
        source_refs = [ObjectRef.model_validate(ref) for ref in source_record.get("object_refs", [])]
        objects.append(obj.model_copy(update={"payload": payload, "object_refs": source_refs}))
    return envelope.model_copy(update={"extracted_objects": objects})


def read_dispatch_source(binding, envelope):
    """Resolve validator inputs from immutable extraction/curator source targets.

    Selected output remains a separate projection. A reconsideration must not
    silently feed the preceding validator's correction back as extracted text.
    Explicit selected prerequisites are supplied separately by the supervisor.
    """
    with store.SessionLocal.begin() as db:
        selections._lock_artifact(db, binding.scope, binding.artifact_id)
        return source_envelope_in_transaction(db, binding.scope, binding.artifact_id, envelope)


def pin_publication_in_transaction(
    db, scope, artifact_id, *, operation_id, manifest, envelope, envelope_revision=None,
):
    """Retain exact approved meaning; call in the same transaction as its checkpoint."""
    selections._lock_artifact(db, scope, artifact_id, write=True)
    current = selections.manifest_in_transaction(
        db, scope, artifact_id, episode_id=manifest["episode_id"],
    )
    if current != manifest:
        raise store.HistoryConflict("Selection changed before projection publication")
    ref = envelope.metadata.get("validation_history", {})
    if ref.get("artifact_id") != artifact_id:
        raise store.HistoryConflict("Published projection has a different artifact")
    body = {
        "manifest": manifest,
        "envelope_revision": envelope_revision,
        "projection": envelope.model_dump(mode="json"),
    }
    fact = store._append(
        db, artifact_id, operation_id="selected-projection:" + operation_id,
        kind="selected_projection", body=body,
    )
    metadata = {**envelope.metadata, "selected_projection": {
        "schema_version": 1, "artifact_id": artifact_id,
        "fact_id": fact.id, "body_sha256": fact.body_sha,
        "manifest_sha256": manifest["sha256"],
        "episode_id": manifest["episode_id"], "envelope_revision": envelope_revision,
    }}
    return envelope.model_copy(update={"metadata": metadata})


def read_published_projection(scope, reference, *, current=False, finalized=False):
    """Exact old reads never reinterpret content using today's selection heads."""
    with store.SessionLocal.begin() as db:
        return read_published_projection_in_transaction(db, scope, reference, current=current, finalized=finalized)


def read_published_projection_in_transaction(db, scope, reference, *, current=False, finalized=False):
    """Read under a caller's existing checkpoint lock, including uncommitted publication."""
    artifact_id = reference["artifact_id"]
    selections._lock_artifact(db, scope, artifact_id)
    fact = store._fact(db, artifact_id, reference["fact_id"], kind="selected_projection")
    if fact.body_sha != reference["body_sha256"]:
        raise store.HistoryConflict("Published projection hash mismatch")
    body = store._content(db, fact)
    manifest = body["manifest"]
    if reference.get("envelope_revision") != body["envelope_revision"]:
        raise store.HistoryConflict("Published envelope revision mismatch")
    if (manifest["sha256"] != reference["manifest_sha256"]
            or manifest["episode_id"] != reference["episode_id"]):
        raise store.HistoryConflict("Published selection manifest mismatch")
    if current or finalized:
        latest_episode = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.kind == "review_episode",
        ).order_by(ValidationFact.sequence.desc()).limit(1))
        if latest_episode is None:
            raise store.HistoryConflict("Published projection has no review episode")
        live = selections.manifest_in_transaction(
            db, scope, artifact_id, episode_id=latest_episode.id,
        )
        if finalized:
            if live != manifest:
                raise store.SelectionNotReady("Finalization requires the current episode publication")
        else:
            # Pending reconsideration changes readiness, not the previous
            # selected scientific value. A changed/stale selection still
            # requires an atomic rebuild before it can be presented.
            def values(snapshot):
                return {unit["target_id"]: (unit["selected"], unit["selection_generation"], unit["eligible"])
                        for unit in snapshot["units"] if unit["selected"] is not None}
            if (values(live) != values(manifest)
                    or live["source_state_sha256"] != manifest["source_state_sha256"]):
                raise store.SelectionNotReady("Published projection is no longer current")
            manifest = live
    if finalized:
        selections.require_ready(manifest)
    projection = DomainEnvelope.model_validate(body["projection"])
    projection.metadata["selected_projection"] = deepcopy(reference)
    return projection, deepcopy(manifest)


def rebuild_selected_envelope(db, scope, artifact_id, *, envelope, manifest, domain_pack, profile_context=None, apply_curator=True):
    """Replay exact selected recipes onto immutable source values."""
    from dataclasses import replace
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
    from src.lib.domain_packs.materialization import (
        ValidatorResultMaterializationInput, materialize_validator_results_into_envelope,
    )
    from src.lib.domain_packs.profile_materialization import materialize_profile_validator_results
    from src.schemas.domain_validator import DomainValidationRequest, DomainValidatorResultBase

    working = source_envelope_in_transaction(db, scope, artifact_id, envelope)
    working = working.model_copy(update={"validation_findings": [
        finding for finding in working.validation_findings
        if not finding.details.get("validation_result_ref")
        and finding.details.get("machine_review") != "pending"
    ]})
    registry = profile_context.registry if profile_context is not None else DomainPackValidationRegistry.from_domain_pack(domain_pack)
    matches = registry.match_bindings(working)
    grouped = {}
    for unit in manifest["units"]:
        if unit["eligible"] and unit["selected"] not in {"baseline", "curator"}:
            grouped.setdefault(unit["selected"], []).append(unit)

    def match_for(attempt):
        target = store._content(db, store._fact(
            db, artifact_id, attempt["input"]["target_id"], kind="target",
        ))
        found = []
        for match in matches:
            if match.binding.binding_id != attempt["input"]["binding_id"]:
                continue
            obj = match.object_envelope
            if obj is None:
                if "object_target" not in target:
                    found.append(match)
                continue
            refs = obj.metadata["validation_history"]
            if refs["target_id"] != target.get("object_target"):
                continue
            expected_path_target = target.get("stable_path", [])
            if expected_path_target:
                current_path_target = refs["fields"].get(match.field_path)
                for list_path, element_ids in refs["elements"].items():
                    for index, element_id in enumerate(element_ids):
                        if match.field_path == f"{list_path}[{index}]":
                            current_path_target = element_id
                if current_path_target != expected_path_target[-1]:
                    continue
            elif match.field_path:
                continue
            found.append(match)
        if len(found) != 1:
            raise store.HistoryConflict("Selected recipe has no unique current binding target")
        return found[0]

    items = []
    for completion_id, units in grouped.items():
        completion = store._fact(db, artifact_id, completion_id, kind="completion")
        attempt = store._content(db, store._fact(
            db, artifact_id, completion.parent_id, kind="attempt",
        ))
        recipe = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.operation_id == "candidate:" + completion_id,
            ValidationFact.kind == "candidate",
        ))
        if recipe is None:
            raise store.HistoryConflict("Selected completion lacks its normalized projection recipe")
        body = store._content(db, recipe)
        request = DomainValidationRequest.model_validate(body["request"])
        result = DomainValidatorResultBase.model_validate(body["result"])
        result._history_ref = deepcopy(body["history_ref"])
        match = match_for(attempt)
        fingerprint = store.canonical({
            "binding": match.binding.raw,
            "object_definition": match.object_definition.model_dump(mode="json")
            if match.object_definition is not None else None,
        })[0]
        if fingerprint != body["binding_fingerprint"]:
            raise store.HistoryConflict("Selected projection binding contract changed")
        original_result = result
        # Stable identity survives reorder; only the derived view's address moves.
        if request.target.field_path != match.field_path:
            from src.lib.domain_packs.input_selectors import build_domain_validation_request
            rebound = build_domain_validation_request(match).request
            if rebound is None:
                raise store.HistoryConflict("Selected target no longer has a structural request")
            request = rebound
            result = result.model_copy(update={"request_id": rebound.request_id, "target": rebound.target})
        slots = frozenset(
            slot for unit in units
            for slot in (
                unit["decision_unit"]["fields"] if unit.get("decision_unit") is not None
                else [*request.expected_result_fields, *(request.optional_result_fields or {})]
            )
        )
        # Identity fields form one structural value. Independently selected
        # answers must not splice a label from one answer into another's ID.
        identity_slots = {}
        destinations = {**request.expected_result_fields, **(request.optional_result_fields or {})}
        for slot, destination in destinations.items():
            if profile_context is not None:
                target = profile_context.profile.resolvable_container(destination)
                container = target[0] if target is not None else None
            elif match.object_envelope is not None:
                from src.lib.domain_envelopes.patches import _resolvable_target
                from src.lib.domain_packs.resolvable_values import declared_resolvable_fields
                target = _resolvable_target(destination, declared_resolvable_fields(
                    domain_pack.metadata, match.object_envelope.object_type,
                ))
                container = target[0] if target is not None else None
            else:
                container = None
            if container is not None:
                identity_slots.setdefault(container, set()).add(slot)
        if any(group & slots and not group <= slots for group in identity_slots.values()):
            raise store.HistoryConflict("Selected decision units split a composite identity")
        if result.resolved_objects and slots != frozenset(
            [*request.expected_result_fields, *(request.optional_result_fields or {})]
        ):
            raise store.HistoryConflict("Partial selection cannot assign unpartitioned derived objects")
        items.append(ValidatorResultMaterializationInput(
            match=match, request=request, result=result, selected_result_slots=slots,
            original_result=original_result, original_source_revision=body["source_envelope_revision"],
        ))
    # Profile materialization must compose all selected units as one record transaction.
    if profile_context is not None:
        projected = materialize_profile_validator_results(working, profile_context, items)
        if any(f.details.get("materialization_error") or f.code == "domain_pack.validator_output_incomplete" for f in projected.appended_findings):
            raise store.HistoryConflict("Selected profile projection is structurally incomplete")
        working = projected.envelope
    else:
        for item in items:
            before_objects = {o.object_id or o.pending_ref_id: o for o in working.extracted_objects}
            before_ids = set(before_objects)
            from src.lib.domain_packs.validator_dispatch import _match_on_envelope
            rebound_match = _match_on_envelope(item.match, working)
            projected = materialize_validator_results_into_envelope(
                working, domain_pack.metadata, [replace(item, match=rebound_match)],
            )
            if any(f.details.get("materialization_error") for f in projected.appended_findings):
                raise store.HistoryConflict("Selected package projection is structurally invalid")
            working = projected.envelope
            for obj in working.extracted_objects:
                key = obj.object_id or obj.pending_ref_id
                if key in before_objects:
                    from .runtime import _check_collection_materialization
                    _check_collection_materialization(before_objects[key], obj)
                if key not in before_ids:
                    obj.metadata["selected_contribution"] = {
                        "completion_id": item.result._history_ref["fact_id"],
                        "selected_result_slots": sorted(item.selected_result_slots),
                    }
            from .runtime import attach_object_targets_in_transaction
            working = attach_object_targets_in_transaction(
                db, working, artifact_id,
                working.metadata["validation_history"]["target_id"],
                origin={"kind": "validator_contribution",
                        "completion_id": item.result._history_ref["fact_id"],
                        "exact_contributor_available": True},
            )
    from .curator_authority import apply_overlays
    # Replaying a recipe derives values, not a second execution/history event.
    # Immutable attempts and publication facts already record both operations.
    working = working.model_copy(update={"history": deepcopy(envelope.history)})
    projected = apply_overlays(db, artifact_id, working) if apply_curator else working
    if profile_context is not None:
        for obj in projected.extracted_objects:
            profile_context.profile.require_attributes(obj.payload["attributes"])
    return projected


def publish_decisions(scope, *, checkpoint_request, episode_id, operation_id, actor, entries):
    """Select, rebuild and checkpoint in one transaction; no provider calls inside."""
    with store.SessionLocal.begin() as db:
        return publish_decisions_in_transaction(
            db, scope, checkpoint_request=checkpoint_request, episode_id=episode_id,
            operation_id=operation_id, actor=actor, entries=entries,
        )


def publish_decisions_in_transaction(db, scope, *, checkpoint_request, episode_id,
                                     operation_id, actor, entries):
    """Shared atomic boundary for review admission plus publication."""
    from dataclasses import replace
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.domain_packs.profile_validation import resolve_envelope_profile_validation
    from src.lib.domain_envelopes.persistence import write_domain_envelope_checkpoint

    envelope = checkpoint_request.envelope
    artifact_id = envelope.metadata["validation_history"]["artifact_id"]
    selections._lock_artifact(db, scope, artifact_id, write=True)
    selections.decide_episode_in_transaction(
        db, scope, artifact_id, episode_id=episode_id,
        operation_id=operation_id, actor=actor, entries=entries,
    )
    from .curator_authority import release_accepted_overlays
    release_accepted_overlays(db, scope, artifact_id, episode_id=episode_id,
                             operation_id=operation_id, accepted_entries=entries)
    manifest = selections.manifest_in_transaction(db, scope, artifact_id, episode_id=episode_id)
    pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
    if pack is None:
        raise store.HistoryConflict("Selected projection domain pack is unavailable")
    profile = resolve_envelope_profile_validation(
        envelope, pack, db=db, user_id=scope.owner_id,
        active_group_ids=checkpoint_request.authenticated_groups or (),
    )
    projected = rebuild_selected_envelope(
        db, scope, artifact_id, envelope=envelope, manifest=manifest,
        domain_pack=pack, profile_context=profile,
    )
    projected = pin_publication_in_transaction(
        db, scope, artifact_id, operation_id=operation_id,
        manifest=manifest, envelope=projected, envelope_revision=checkpoint_request.expected_revision + 1,
    )
    checkpoint = write_domain_envelope_checkpoint(
        db, replace(checkpoint_request, envelope=projected),
    )
    refresh_workspace_projection(db, projected, checkpoint.revision)
    return projected, manifest, checkpoint

def latest_publication_reference(scope, artifact_id):
    """Discover a publication only inside the caller-authorized exact artifact."""
    with store.SessionLocal.begin() as db:
        selections._lock_artifact(db, scope, artifact_id)
        fact = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.kind == "selected_projection",
        ).order_by(ValidationFact.sequence.desc()).limit(1))
        if fact is None:
            return None  # Explicit awaiting-publication state, not an older source.
        body = store._content(db, fact)
        return {
            "schema_version": 1, "artifact_id": artifact_id,
            "fact_id": fact.id, "body_sha256": fact.body_sha,
            "manifest_sha256": body["manifest"]["sha256"],
            "episode_id": body["manifest"]["episode_id"], "envelope_revision": body["envelope_revision"],
        }


def refresh_workspace_projection(db, envelope, revision):
    """Keep existing workspace draft caches on the atomic published revision."""
    from datetime import datetime, timezone
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    from src.lib.curation_workspace.session_queries import load_envelope_candidates_for_patch
    from src.lib.curation_workspace.session_mutation_service import _refresh_candidate_projection_from_envelope, _projected_value_for_draft_field
    from src.schemas.curation_workspace import CurationDraftField

    row = db.scalar(select(DomainEnvelopeModel).where(
        DomainEnvelopeModel.envelope_id == envelope.envelope_id,
    ))
    if row is None or row.session_id is None:
        return  # Extraction-only artifacts do not yet have a workspace.
    objects = {obj.object_id or obj.pending_ref_id: obj for obj in envelope.extracted_objects}
    now = datetime.now(timezone.utc)
    for candidate in load_envelope_candidates_for_patch(db, session_id=row.session_id, envelope_id=envelope.envelope_id):
        obj = objects.get(candidate.object_id)
        if obj is None:
            # Do not leave an old selected payload pretending to be current.
            raise store.HistoryConflict("Workspace candidate is absent from selected projection")
        changed_fields = []
        for raw in candidate.draft.fields if candidate.draft is not None else ():
            field = CurationDraftField.model_validate(raw)
            if _projected_value_for_draft_field(obj.payload, field) != field.value:
                changed_fields.append(field.field_key)
        _refresh_candidate_projection_from_envelope(
            candidate, obj, envelope_revision=revision,
            changed_field_path=tuple(changed_fields), updated_at=now,
        )
    db.flush()


def republish_curator_checkpoint(db, envelope, *, revision, authenticated_groups):
    """Rebuild after newly committed curator facts inside the checkpoint transaction."""
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.domain_packs.profile_validation import resolve_envelope_profile_validation
    from src.models.sql.validation_history import ValidationArtifact
    ref = envelope.metadata.get("validation_history")
    if ref is None:
        return envelope
    artifact = db.get(ValidationArtifact, ref["artifact_id"])
    scope = store.HistoryScope(artifact.owner_id, artifact.document_id, ref["session_id"])
    episode = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact.id, ValidationFact.kind == "review_episode",
    ).order_by(ValidationFact.sequence.desc()).limit(1))
    if episode is None:
        return envelope  # No review was invented by the curator's edit.
    manifest = selections.manifest_in_transaction(db, scope, artifact.id, episode_id=episode.id)
    pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
    if pack is None:
        raise store.HistoryConflict("Curator projection domain pack is unavailable")
    profile = resolve_envelope_profile_validation(envelope, pack, db=db,
        user_id=scope.owner_id, active_group_ids=authenticated_groups or ())
    projected = rebuild_selected_envelope(db, scope, artifact.id, envelope=envelope,
        manifest=manifest, domain_pack=pack, profile_context=profile)
    return pin_publication_in_transaction(db, scope, artifact.id,
        operation_id=f"curator-checkpoint:{envelope.envelope_id}:{revision}",
        manifest=manifest, envelope=projected, envelope_revision=revision)


def read_underlying_curator_value(scope, envelope, *, object_id, value_path, authenticated_groups=()):
    """Resolve the current selected value without replaying curator overlays."""
    from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
    from src.lib.domain_packs.profile_validation import resolve_envelope_profile_validation
    from .curator_authority import active_overlays, _overlap, anchored_path, rebind_output_path
    artifact_id = envelope.metadata["validation_history"]["artifact_id"]
    with store.SessionLocal.begin() as db:
        selections._lock_artifact(db, scope, artifact_id)
        object_refs = {obj.metadata["validation_history"]["target_id"]: obj.metadata["validation_history"]
                       for obj in envelope.extracted_objects}
        if not any(body["object_id"] == object_id and _overlap(anchored_path(object_refs[body["target_id"]], body["anchor"]), value_path)
                   for _, body in active_overlays(db, artifact_id)):
            return None
        episode = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "review_episode",
        ).order_by(ValidationFact.sequence.desc()).limit(1))
        if episode is None:
            return {}
        manifest = selections.manifest_in_transaction(db, scope, artifact_id, episode_id=episode.id)
        original_obj = next((obj for obj in envelope.extracted_objects if object_id in {obj.object_id, obj.pending_ref_id}), None)
        if original_obj is None:
            return {}
        root = original_obj.metadata["validation_history"]["target_id"]
        eligible = False
        for unit in manifest["units"]:
            if not unit["eligible"]:
                continue
            if unit["selected"] == "baseline" and unit["target_id"] == root:
                eligible = True
            elif unit.get("attempt_id"):
                frozen = store._content(db, store._fact(db, artifact_id, unit["attempt_id"], kind="attempt"))["input"]
                binding_fact = store._fact(db, artifact_id, frozen["target_id"], kind="target")
                binding = store._content(db, binding_fact)
                request = frozen["request"]
                destinations = {**request["expected_result_fields"], **(request.get("optional_result_fields") or {})}
                allowed = set(unit["decision_unit"]["fields"]) if unit.get("decision_unit") else set(destinations)
                current_target_path = anchored_path(original_obj.metadata["validation_history"],
                    {"target_id": binding_fact.parent_id, "suffix": []}) if binding.get("object_target") == root else ""
                if binding.get("object_target") == root and any(
                    slot in allowed and isinstance(path, str) and _overlap(rebind_output_path(path, request["target"].get("field_path"), current_target_path), value_path)
                    for slot, path in destinations.items()):
                    eligible = True
                if original_obj.metadata.get("selected_contribution", {}).get("completion_id") == unit["selected"]:
                    eligible = True
        if not eligible:
            return {}
        pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
        if pack is None:
            raise store.HistoryConflict("Underlying selected domain pack is unavailable")
        profile = resolve_envelope_profile_validation(envelope, pack, db=db, user_id=scope.owner_id,
            active_group_ids=authenticated_groups)
        result = rebuild_selected_envelope(db, scope, artifact_id, envelope=envelope,
            manifest=manifest, domain_pack=pack, profile_context=profile, apply_curator=False)
        obj = next((obj for obj in result.extracted_objects if object_id in {obj.object_id, obj.pending_ref_id}), None)
        if obj is None:
            return {}
        from src.schemas.domain_envelope import parse_field_path
        value = obj.payload
        try:
            for part in parse_field_path(value_path):
                value = value[part]
        except (KeyError, IndexError, TypeError):
            return {}
        return deepcopy(value) if isinstance(value, dict) else {}
