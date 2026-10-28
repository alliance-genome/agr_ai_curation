"""Resume dependent bindings inside the same artifact and review episode."""

from uuid import uuid4

from .admission import lock_episode
from . import source_inputs
from .scheduling import _enqueue, _existing
from src.lib.validation_history import store, selections


def advance(
    scope, episode_id, *, operation_id, prerequisite_targets, receipt, build_requests
):
    """Trusted pure builder receives exact selected sources under one short lock.

    The adapter applies its declared structural input selectors. Missing inputs
    are a not-runnable result from the adapter, never a synthetic scientific
    verdict. No provider/network work is allowed in this callback.
    """
    with store.SessionLocal.begin() as db:
        ep, _ = lock_episode(db, scope, episode_id, require_open=False)
        identity = {"prerequisite_targets": prerequisite_targets, "receipt": receipt}
        prior = _existing(db, ep, operation_id, identity)
        if prior is not None:
            return {
                "state": "queued",
                "work_id": prior.id,
                "prerequisites": prior.payload["requests"][0]["prerequisites"],
            }
        lock_episode(db, scope, episode_id)
        manifest = selections.manifest_in_transaction(
            db, scope, ep.artifact_id, episode_id=ep.id
        )
        blocked = {item["target_id"] for item in manifest["blockers"]}
        pending = [tid for tid in prerequisite_targets if tid in blocked]
        if pending:
            return {"state": "awaiting_review", "targets": pending}
        pins = selections.selected_prerequisites(manifest, prerequisite_targets)
        units = {unit["target_id"]: unit for unit in manifest["units"]}
        sources = {}
        for tid in prerequisite_targets:
            selected = units[tid]["selected"]
            if selected in {"baseline", "curator"}:
                return {
                    "state": "not_runnable",
                    "reason": "non_validator_prerequisite",
                    "targets": [tid],
                }
            sources[tid] = store._content(
                db, store._fact(db, ep.artifact_id, selected)
            )
        requests = build_requests(sources)
        if not requests:
            return {"state": "not_runnable", "reason": "required_inputs_unavailable"}
        absences = {request["target_id"]: {**request, "prerequisites": pins}
                    for request in requests if source_inputs.is_absence(request)}
        if absences:
            source_inputs.update(db, ep.artifact_id, operation_id="dependent-absence:" + str(uuid4()),
                                 before={}, after=absences)
            requests = [request for request in requests if not source_inputs.is_absence(request)]
            if not requests:
                return {"state": "awaiting_review", "reason": "optional_inputs_absent",
                        "targets": list(absences), "applicability_refs": {
                            target: source_inputs.reference(source_inputs.latest(db, ep.artifact_id, target))
                            for target in absences}}
        if any(
            not isinstance(request.get("request", {}).get("selected_inputs"), dict)
            for request in requests
        ):
            raise ValueError("Dependent requests require a selected_inputs mapping")
        frozen = [
            {**request, "review_episode_id": ep.id, "prerequisites": pins}
            for request in requests
        ]
        work_id = _enqueue(
            db,
            scope,
            ep,
            operation_id=operation_id,
            requests=frozen,
            receipt=receipt,
            identity=identity,
            reconsideration=False,
        )
        return {"state": "queued", "work_id": work_id, "prerequisites": pins}
