"""Resume dependent bindings inside the same artifact and review episode."""

from .admission import lock_episode
from .scheduling import _enqueue
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
        ep, _ = lock_episode(db, scope, episode_id)
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
                raise store.HistoryConflict(
                    "Dependent validator wave requires exact validator answer sources"
                )
            sources[tid] = store._content(
                db, store._fact(db, ep.artifact_id, selected, kind="completion")
            )
        requests = build_requests(sources)
        if not requests:
            return {"state": "not_runnable", "reason": "required_inputs_unavailable"}
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
            identity={"requests": frozen, "receipt": receipt},
            reconsideration=False,
        )
        return {"state": "queued", "work_id": work_id, "prerequisites": pins}
