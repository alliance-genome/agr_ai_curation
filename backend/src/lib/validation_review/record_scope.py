"""Structural record/dependency boundaries for an explicit curator round.

Coverage is retained by reference to actual earlier review episodes. This never
creates an acceptance decision or infers scientific agreement from an answer.
"""

from src.lib.validation_history import store


def allowed_units(body):
    requested = body.get("review_target_ids")
    return (
        {item["target_id"] for item in body["obligations"]}
        if requested is None
        else set(requested)
    )


def require_units(body, target_ids):
    if not set(target_ids).issubset(allowed_units(body)):
        raise store.HistoryAccessError(
            "Target is outside the curator-requested record and its dependencies"
        )


def compile_matches(binding, matches, object_ids):
    """Use saved selectors/dependency declarations, never text similarity."""
    from src.lib.validation_history.runtime import binding_target

    all_matches = {binding_target(binding, match): match for match in matches}
    requested = {
        tid
        for tid, match in all_matches.items()
        if match.object_envelope is not None
        and (
            match.object_envelope.object_id in object_ids
            or match.object_envelope.pending_ref_id in object_ids
        )
    }
    allowed = set(requested)
    while True:
        dependencies = set()
        for tid in allowed:
            match = all_matches[tid]
            for other_id, other in all_matches.items():
                same = (
                    match.object_envelope is None
                    or other.object_envelope is None
                    or match.object_envelope.metadata["validation_history"]["target_id"]
                    == other.object_envelope.metadata["validation_history"]["target_id"]
                )
                if same and other.binding.binding_id in match.binding.runs_after:
                    dependencies.add(other_id)
        extended = allowed | dependencies
        if extended == allowed:
            return requested, allowed
        allowed = extended


def origin_episode(db, artifact_id, prior_id, target_id):
    """Flatten references at creation so repeated human rounds do not recurse."""
    from src.lib.validation_history.selections import effective_episode

    prior = effective_episode(
        db, store._fact(db, artifact_id, prior_id, kind="review_episode")
    )
    if target_id not in {item["target_id"] for item in prior["obligations"]}:
        return None
    return prior.get("retained_episode_ids", {}).get(target_id, prior_id)


def retained_coverage(
    db,
    artifact_id,
    previous_id,
    obligations,
    requested_bindings,
    allowed_bindings,
    object_targets,
):
    retained = {}
    allowed = []
    for obligation in obligations:
        tid = obligation["target_id"]
        target = store._fact(db, artifact_id, tid, kind="target")
        root = (
            target.parent_id if "decision_unit" in store._content(db, target) else tid
        )
        requested = root in requested_bindings or tid in object_targets
        if root in allowed_bindings or tid in object_targets:
            allowed.append(tid)
        if not requested and previous_id is not None:
            origin = origin_episode(db, artifact_id, previous_id, tid)
            if origin is not None:
                retained[tid] = origin
    return allowed, retained


def validation_targets(db, artifact_id, body):
    """Count only executable bindings covered by this round, once per binding."""
    units = allowed_units(body)
    targets = set()
    for obligation in body["obligations"]:
        if obligation["mode"] != "validation" or obligation["target_id"] not in units:
            continue
        target = store._fact(db, artifact_id, obligation["target_id"], kind="target")
        content = store._content(db, target)
        targets.add(target.parent_id if "decision_unit" in content else target.id)
    return targets


def review_ready(manifest):
    """Unscoped runs cover the artifact; scoped rounds cover their explicit units."""
    return (
        manifest["review_ready"]
        if "review_target_ids" in manifest
        else manifest["ready"]
    )


def review_blockers(manifest):
    return (
        manifest["review_blockers"]
        if "review_target_ids" in manifest
        else manifest["blockers"]
    )
