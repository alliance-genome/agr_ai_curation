"""Selected-value authority and episode readiness, independent of scientific judgment.

All records reuse the append-only history ledger. An episode declares obligations
before work starts, including bindings that cannot run. A previous selection may
remain current without satisfying an obligation in a new episode. Readers never
infer approval from completion, resolution status, or a materialized payload.
"""

from __future__ import annotations

from copy import deepcopy

from sqlalchemy import select

from src.models.sql.validation_history import ValidationArtifact, ValidationFact
from src.schemas.validation_history import HistoryActor, ReviewEpisode
from . import store


def _lock_artifact(db, scope, artifact_id, *, write=False):
    artifact = store._artifact(db, artifact_id, scope)
    db.scalar(
        select(ValidationArtifact.id)
        .where(ValidationArtifact.id == artifact_id)
        .with_for_update(read=not write)
    )
    return artifact


def start_episode(scope, artifact_id: str, *, episode: dict) -> str:
    """Declare exact coverage, not just the validators that happened to succeed."""
    with store.SessionLocal.begin() as db:
        return start_episode_in_transaction(db, scope, artifact_id, episode=episode)


def effective_episode(db, episode):
    """Approved source revisions may change coverage, never the episode/budget ID."""
    body = deepcopy(store._content(db, episode))
    extension = db.scalar(select(ValidationFact).where(
        ValidationFact.artifact_id == episode.artifact_id,
        ValidationFact.parent_id == episode.id,
        ValidationFact.kind == "review_coverage_revision").order_by(ValidationFact.sequence.desc()).limit(1))
    if extension is not None:
        revised = store._content(db, extension)["obligations"]
        if body.get("review_target_ids") is not None:
            old = {item["target_id"] for item in body["obligations"]}
            current = {item["target_id"] for item in revised}
            body["review_target_ids"] = sorted((set(body["review_target_ids"]) & current) | (current - old))
        body["obligations"] = revised
    return body


def revise_coverage_in_transaction(db, scope, artifact_id, *, episode_id, source_selection_id, obligations):
    """Trusted binding resolver calls this inside source-selection publication."""
    from src.schemas.validation_history import ReviewObligation
    _lock_artifact(db, scope, artifact_id, write=True)
    episode = store._fact(db, artifact_id, episode_id, kind="review_episode")
    selection = store._fact(db, artifact_id, source_selection_id, kind="extraction_source_selection")
    current = effective_episode(db, episode)["obligations"]
    proposed = [ReviewObligation.model_validate(item).model_dump(mode="json") for item in obligations]
    if len({item["target_id"] for item in proposed}) != len(proposed):
        raise store.HistoryConflict("Revised coverage repeats a target")
    new_ids = {item["target_id"] for item in proposed}
    for item in current:
        if item["target_id"] not in new_ids:
            try:
                store._require_active_target(db, artifact_id, item["target_id"])
            except store.HistoryConflict:
                continue
            raise store.HistoryConflict("Source revision cannot drop a live review obligation")
    for item in proposed:
        target = store._fact(db, artifact_id, item["target_id"], kind="target")
        store._require_active_target(db, artifact_id, target.id)
        if item["mode"] == "validation" and store._content(db, target).get("binding_id") != item["binding_id"]:
            raise store.HistoryConflict("Revised obligation differs from its saved binding")
    return store._append(db, artifact_id, operation_id="review-coverage:" + selection.id,
        kind="review_coverage_revision", parent_id=episode.id,
        body={"source_selection_id": selection.id, "obligations": proposed}).id


def start_episode_in_transaction(db, scope, artifact_id, *, episode):
    body = ReviewEpisode.model_validate(episode).model_dump(mode="json")
    _lock_artifact(db, scope, artifact_id, write=True)
    store._require_live_artifact(db, artifact_id)
    for obligation in body["obligations"]:
        target = store._fact(db, artifact_id, obligation["target_id"], kind="target")
        store._require_active_target(db, artifact_id, target.id)
        original = store._content(db, target)
        if obligation["mode"] != "validation" and original.get("binding_id"):
            raise store.HistoryConflict("Validator target cannot become extraction-only or curator-only")
        if obligation["mode"] == "validation":
            if original.get("binding_id") != obligation["binding_id"]:
                raise store.HistoryConflict("Obligation does not match its binding")
    covered = {item["target_id"] for item in body["obligations"]}
    if body["review_target_ids"] is not None and not set(body["review_target_ids"]).issubset(covered):
        raise store.HistoryConflict("Record review scope exceeds declared coverage")
    for tid, origin_id in body["retained_episode_ids"].items():
        origin = store._fact(db, artifact_id, origin_id, kind="review_episode")
        if tid not in covered or tid not in {item["target_id"] for item in effective_episode(db, origin)["obligations"]}:
            raise store.HistoryConflict("Retained review has no matching historical coverage")
    return store._append(
        db, artifact_id, operation_id="review-episode:" + body["episode_id"],
        kind="review_episode", body=body,
    ).id


def record_opinion(scope, artifact_id, *, operation_id, actor, completion_id, reason):
    """Preserve agreement/commentary without making incomplete output selectable."""
    actor = HistoryActor.model_validate(actor).model_dump(mode="json")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("An opinion requires an explicit reason")
    with store.SessionLocal.begin() as db:
        _lock_artifact(db, scope, artifact_id, write=True)
        store._require_live_artifact(db, artifact_id)
        store._fact(db, artifact_id, completion_id, kind="completion")
        return store._append(
            db, artifact_id, operation_id="review-opinion:" + operation_id,
            kind="review_opinion", parent_id=completion_id,
            body={"actor": actor, "reason": reason, "selects_value": False},
        ).id


def decide_episode_in_transaction(
    db, scope, artifact_id, *, episode_id, operation_id, actor, entries,
):
    """Record decisions and episode dispositions under the publication transaction."""
    _lock_artifact(db, scope, artifact_id, write=True)
    episode = store._fact(db, artifact_id, episode_id, kind="review_episode")
    obligations = {
        item["target_id"]: item for item in effective_episode(db, episode)["obligations"]
    }
    for entry in entries:
        obligation = obligations.get(entry["target_id"])
        if obligation is None:
            raise store.HistoryConflict("Decision target is outside episode coverage")
        if entry["action"] == "accept" and entry.get("subject") == "baseline":
            if obligation["mode"] != "extraction_only":
                raise store.HistoryConflict("Required validation cannot become extraction-only")
        if entry.get("subject") == "curator" and obligation["mode"] != "curator":
            raise store.HistoryConflict("Curator source cannot satisfy required validator review")
        if obligation["mode"] == "curator" and actor["type"] != "curator":
            raise store.HistoryAccessError("Agent review cannot stand in for a curator")
    if actor["type"] != "curator":
        from src.lib.validation_review.record_scope import require_units
        require_units(effective_episode(db, episode), [entry["target_id"] for entry in entries])
    versions = store.apply_decisions_in_transaction(
        db, scope, artifact_id, operation_id=operation_id, actor=actor, entries=entries,
    )
    store._append(
        db, artifact_id, operation_id="episode-disposition:" + operation_id,
        kind="episode_disposition", parent_id=episode.id,
        body={"selection_ids": versions},
    )
    return versions


def _selected_unit(db, scope, artifact_id, target_id):
    target = store._fact(db, artifact_id, target_id, kind="target")
    state = store._current(db, artifact_id, target_id)
    unit = {
        "target_id": target_id,
        "version": state["version"],
        "selection_generation": state["selection_generation"],
        "selected": state["selected"],
        "review": state["review"],
        "selection_id": None,
        "eligible": False,
        "reason": "unreviewed",
        "scientific_status": None,
    }
    # The current head may describe rejection of a replacement. Prerequisites
    # pin the exact accepting fact, not that later non-selecting event.
    if state["selected"] is not None:
        for selection in db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.kind == "selection",
            ValidationFact.target_id == target_id,
        ).order_by(ValidationFact.sequence.desc())):
            selected = store._content(db, selection)
            if (selected["action"] == "accept"
                    and selected["selected"] == state["selected"]
                    and selected["selection_generation"] == state["selection_generation"]):
                unit["selection_id"] = selection.id
                break
        if unit["selection_id"] is None:
            raise store.HistoryConflict("Current selection has no accepting fact")
    try:
        store._require_active_target(db, artifact_id, target_id)
    except store.HistoryConflict:
        unit["reason"] = "retired"
        return unit
    if state["selected"] is None:
        unit["decision_unit"] = store._content(db, target).get("decision_unit")
        return unit
    if state["selected"] == "curator":
        if not store._has_curator_subject(db, artifact_id, target_id):
            raise store.HistoryConflict("Selected curator source has no attested provenance")
        unit.update(eligible=True, reason=None)
        return unit
    if state["selected"] == "baseline":
        artifact = store._artifact(db, artifact_id, scope)
        if artifact.subject_kind == "historical" or not store._has_baseline_subject(
            db, artifact_id, target_id
        ):
            unit["reason"] = "historical_review_unknown"
            return unit
        unit.update(eligible=True, reason=None)
        return unit
    completion = store._fact(db, artifact_id, state["selected"], kind="completion")
    content = store._content(db, completion)
    attempt = store._fact(db, artifact_id, completion.parent_id, kind="attempt")
    frozen = store._content(db, attempt)
    if target.id not in frozen["coverage"]:
        raise store.HistoryConflict("Selected completion does not cover this unit")
    if content["state"] != "completed":
        unit["reason"] = "incomplete"
        return unit
    if not store._attempt_is_current(db, scope, artifact_id, frozen, target.id):
        unit["reason"] = "stale"
        return unit
    unit.update(
        eligible=True, reason=None,
        scientific_status=content["result"].get("status"),
        attempt_id=attempt.id,
        decision_unit=store._content(db, target).get("decision_unit"),
    )
    return unit


def source_state_digest(db, artifact_id):
    """Pin source identities/lifecycle, including edited descendants of a root."""
    targets = {fact.id: (fact, store._content(db, fact)) for fact in db.scalars(
        select(ValidationFact).where(ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "target"))}
    def contributed(target_id):
        while target_id in targets:
            fact, body = targets[target_id]
            if body.get("origin", {}).get("kind") == "validator_contribution":
                return True
            target_id = fact.parent_id
        return False
    states = []
    for tid, (fact, body) in targets.items():
        if contributed(tid) or not ("object_ref" in body or "initial_path" in body):
            continue
        state = store._current(db, artifact_id, tid)
        states.append((tid, fact.body_sha, state["active"], state["lifecycle_generation"]))
    from .curator_authority import active_overlays
    overlays = sorted((fact.id, fact.body_sha) for fact, _ in active_overlays(db, artifact_id))
    from src.lib.validation_review.source_selection import selected_entries
    contributions = sorted((fact.id, fact.body_sha) for fact, _ in selected_entries(db, artifact_id).values())
    return store.canonical({"targets": sorted(states), "curator_values": overlays,
                            "extractor_contributions": contributions})[0]


def manifest_in_transaction(db, scope, artifact_id, *, episode_id):
    """Resolve a consistent snapshot; no inference from caveats or confidence."""
    artifact = _lock_artifact(db, scope, artifact_id)
    episode = store._fact(db, artifact_id, episode_id, kind="review_episode")
    episode_body = effective_episode(db, episode)
    obligations = episode_body["obligations"]
    retained = episode_body.get("retained_episode_ids", {})
    origins = {episode.id, *retained.values()}
    disposition_ids = {origin: set() for origin in origins}
    for fact in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.kind == "episode_disposition",
        ValidationFact.parent_id.in_(origins),
    )):
        disposition_ids[fact.parent_id].update(store._content(db, fact)["selection_ids"])
    attempts_by_target = {}
    for attempt in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "attempt",
    )):
        attempt_body = store._content(db, attempt)
        recorded_episode = attempt_body["input"].get("review_episode_id")
        for target_id in attempt_body["coverage"]:
            if recorded_episode in {episode.id, retained.get(target_id)} and recorded_episode is not None:
                attempts_by_target.setdefault(target_id, []).append(attempt)
            elif recorded_episode is None and attempt.sequence > episode.sequence:
                attempts_by_target.setdefault(target_id, []).append(attempt)
    completions = {fact.parent_id: fact for fact in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == artifact_id, ValidationFact.kind == "completion",
    ))}
    reviewed_answers = {}
    for origin, ids in disposition_ids.items():
        reviewed_answers[origin] = set()
        decisions = {store._fact(db, artifact_id, sid, kind="selection").parent_id for sid in ids}
        for did in decisions:
            decision = store._content(db, store._fact(db, artifact_id, did, kind="decision"))
            reviewed_answers[origin].update((entry["target_id"], entry.get("subject")) for entry in decision["entries"])
    units = []
    blockers = [{"target_id": None, "code": "required_validator_unavailable", "binding_id": binding_id}
                for binding_id in episode_body["unavailable_bindings"]]
    if store._document_retirement(db, artifact_id) is not None:
        blockers.append({"target_id": None, "code": "document_retired"})
    if artifact.subject_kind == "historical" and not obligations:
        blockers.append({"target_id": None, "code": "historical_review_unknown"})
    for obligation in obligations:
        unit = _selected_unit(db, scope, artifact_id, obligation["target_id"])
        units.append(unit)
        origin = retained.get(unit["target_id"], episode.id)
        unit_dispositions = disposition_ids[episode.id] | disposition_ids[origin]
        unit_reviewed_answers = reviewed_answers[episode.id] | reviewed_answers[origin]
        if origin != episode.id:
            unit["retained_review_episode_id"] = origin
        if not unit["eligible"]:
            blockers.append({"target_id": unit["target_id"], "code": unit["reason"]})
        elif unit["review"] != "accept" or unit["selection_id"] not in unit_dispositions:
            blockers.append({"target_id": unit["target_id"], "code": "episode_review_pending"})
        else:
            selection = store._content(
                db, store._fact(db, artifact_id, unit["selection_id"], kind="selection")
            )
            if selection["action"] != "accept":
                blockers.append({"target_id": unit["target_id"], "code": "episode_review_pending"})
        # A new answer requires an explicit disposition even if an older one
        # remains selected. Automatic reconsideration stays within this episode;
        # later human renewal creates a linked episode without resetting parent quotas.
        for attempt in attempts_by_target.get(unit["target_id"], ()):
            completion = completions.get(attempt.id)
            attempt_ref = {"fact_id": attempt.id, "sha256": attempt.body_sha}
            if completion is None and (unit["target_id"], attempt.id) not in unit_reviewed_answers:
                blockers.append({"target_id": unit["target_id"], "code": "execution_pending",
                                 "attempt_ref": attempt_ref})
            elif completion is not None and (unit["target_id"], completion.id) not in unit_reviewed_answers:
                blockers.append({"target_id": unit["target_id"], "code": "answer_review_pending",
                                 "attempt_ref": attempt_ref,
                                 "completion_ref": {"fact_id": completion.id, "sha256": completion.body_sha}})
    from src.lib.validation_review.record_scope import allowed_units
    review_targets = allowed_units(episode_body)
    review_blockers = [item for item in blockers if item["target_id"] is None or item["target_id"] in review_targets]
    manifest = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "baseline_sha256": artifact.baseline_sha,
        "source_state_sha256": source_state_digest(db, artifact_id),
        "episode_id": episode.id,
        "excluded_bindings": store._content(db, episode)["excluded_bindings"],
        "provenance": deepcopy(artifact.provenance),
        "units": units,
        "blockers": blockers,
        "ready": not blockers,
    }
    if episode_body.get("review_target_ids") is not None:
        manifest.update(review_target_ids=sorted(review_targets), review_ready=not review_blockers,
                        review_blockers=review_blockers)
    digest, manifest = store.canonical(manifest)
    return {**manifest, "sha256": digest}


def read_manifest(scope, artifact_id, *, episode_id):
    with store.SessionLocal.begin() as db:
        return manifest_in_transaction(db, scope, artifact_id, episode_id=episode_id)


def require_ready(manifest):
    if not manifest["ready"]:
        raise store.SelectionNotReady("Selected output is not ready for finalization")


def selected_prerequisites(manifest, target_ids):
    """Supply only eligible exact selected versions, never prior completion hints."""
    units = {unit["target_id"]: unit for unit in manifest["units"]}
    pins = []
    for target_id in target_ids:
        unit = units.get(target_id)
        if unit is None or not unit["eligible"]:
            raise store.HistoryConflict("Prerequisite is awaiting an eligible selection")
        pins.append({
            "artifact_id": manifest["artifact_id"], "target_id": target_id,
            "selection_id": unit["selection_id"],
            "selection_generation": unit["selection_generation"],
        })
    return pins


def declare_envelope_episode(binding, envelope, matches, *, episode_key=None, review_object_ids=None, unavailable_bindings=(), curator_revalidation=None, authenticated_groups=None, flow_selections=None):
    """Declare all matching obligations before selectors/preflight can skip work."""
    from uuid import uuid4
    from .runtime import binding_target

    obligations = []
    exclusions = []
    covered_objects = set()
    for match in matches:
        target_id = binding_target(binding, match)
        reason = None
        required_groups = match.binding.required_any_active_group
        choice = (flow_selections or {}).get(match.binding.binding_id, {})
        from src.lib.domain_packs.validator_dispatch import _binding_has_dispatch_contract
        if required_groups and authenticated_groups is not None and not set(required_groups).intersection(authenticated_groups):
            reason = "group_not_applicable"
        elif choice.get("state") == "skipped" and (match.binding.allow_opt_out or not match.binding.required):
            reason = "configured_opt_out"
        elif not match.binding.required and not _binding_has_dispatch_contract(match.binding):
            reason = "optional_declarative_binding"
        if reason is not None:
            exclusions.append({"target_id": target_id, "binding_id": match.binding.binding_id, "reason": reason})
            continue
        if match.object_envelope is not None:
            covered_objects.add(match.object_envelope.metadata["validation_history"]["target_id"])
        if match.binding.decision_units:
            with store.SessionLocal.begin() as db:
                _lock_artifact(db, binding.scope, binding.artifact_id, write=True)
                for unit in match.binding.decision_units:
                    target = store._append(
                        db, binding.artifact_id,
                        operation_id="unit:" + target_id + ":" + unit["key"],
                        kind="target", parent_id=target_id,
                        body={"decision_unit": unit, "binding_id": match.binding.binding_id},
                    )
                    obligations.append({
                        "target_id": target.id, "mode": "validation",
                        "binding_id": match.binding.binding_id,
                    })
        else:
            obligations.append({
                "target_id": target_id, "mode": "validation",
                "binding_id": match.binding.binding_id,
            })
    from .projection import read_dispatch_source
    source = read_dispatch_source(binding, envelope)
    for obj in source.extracted_objects:
        target_id = obj.metadata["validation_history"]["target_id"]
        if target_id not in covered_objects:
            with store.SessionLocal.begin() as db:
                store._artifact(db, binding.artifact_id, binding.scope)
                mode = "curator" if store._has_curator_subject(db, binding.artifact_id, target_id) else "extraction_only"
            obligations.append({"target_id": target_id, "mode": mode})
    if not source.extracted_objects and not obligations:
        obligations.append({
            "target_id": envelope.metadata["validation_history"]["target_id"],
            "mode": "extraction_only",
        })
    obligations = list({item["target_id"]: item for item in obligations}.values())
    from .curator_authority import record_revalidation_intent_in_transaction, revalidation_intents
    with store.SessionLocal.begin() as db:
        scoped = {}
        if review_object_ids is not None:
            from src.lib.validation_review.record_scope import compile_matches, retained_coverage
            requested, allowed = compile_matches(binding, matches, set(review_object_ids))
            roots = {obj.metadata["validation_history"]["target_id"] for obj in source.extracted_objects
                     if obj.object_id in review_object_ids or obj.pending_ref_id in review_object_ids}
            ids, retained = retained_coverage(db, binding.artifact_id, envelope.metadata.get("review_episode_id"),
                obligations, requested, allowed, roots)
            scoped = {"review_target_ids": ids, "retained_episode_ids": retained}
        episode_id = start_episode_in_transaction(db, binding.scope, binding.artifact_id, episode={
            "episode_id": episode_key or str(uuid4()), "reason": "Explicit review of this validation run",
            "obligations": obligations, "unavailable_bindings": list(unavailable_bindings), "excluded_bindings": exclusions,
            **scoped,
        })
        intent_ids = [record_revalidation_intent_in_transaction(db, binding, envelope=envelope,
            episode_id=episode_id, **intent) for intent in revalidation_intents(curator_revalidation)]
    intent_reference = {"curator_revalidation_intent": {"fact_ids": intent_ids, "episode_id": episode_id}} if intent_ids else {}
    return envelope.model_copy(update={"metadata": {
        **{key: value for key, value in envelope.metadata.items() if key != "curator_revalidation_intent"},
        "review_episode_id": episode_id, **intent_reference,
    }})
