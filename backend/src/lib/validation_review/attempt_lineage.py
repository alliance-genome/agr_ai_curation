"""Count scientific attempts through explicit immutable item ancestry.

A split inherits its predecessor's consumed attempts on each successor. A merge
inherits the largest consumed predecessor depth, not a sum that would exhaust a
fresh merged target before it runs. This does not infer item identity from values.
"""
from sqlalchemy import select

from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import ValidationReviewEpisode


def counts(db, episode, target_ids):
    targets = {fact.id: fact for fact in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == episode.artifact_id, ValidationFact.kind == "target"))}
    bodies = {tid: store._content(db, fact) for tid, fact in targets.items()}
    episodes = set(db.scalars(select(ValidationReviewEpisode.id).where(
        ValidationReviewEpisode.artifact_id == episode.artifact_id,
        ValidationReviewEpisode.scope_id == episode.scope_id)))
    own = {}
    for fact in db.scalars(select(ValidationFact).where(
        ValidationFact.artifact_id == episode.artifact_id, ValidationFact.kind == "attempt")):
        if store._content(db, fact)["input"]["review_episode_id"] in episodes:
            own[fact.target_id] = own.get(fact.target_id, 0) + 1

    def below(tid, ancestor):
        seen = set()
        while tid in targets:
            if tid in seen:
                raise store.HistoryConflict("Cyclic target parentage")
            if tid == ancestor:
                return True
            seen.add(tid)
            tid = targets[tid].parent_id
        return False

    memo = {}

    def total(tid, visiting):
        if tid in memo:
            return memo[tid]
        if tid in visiting or tid not in targets:
            raise store.HistoryConflict("Invalid scientific attempt lineage")
        binding = bodies[tid].get("binding_id")
        ancestors = set()
        parent = targets[tid].parent_id
        while parent in targets:
            origin = bodies[parent].get("origin", {})
            if origin.get("kind") == "extraction_contribution":
                ancestors.update(origin.get("predecessor_ids", []))
            parent = targets[parent].parent_id
        predecessors = {candidate for candidate, body in bodies.items()
                        if candidate != tid and body.get("binding_id") == binding
                        and any(below(candidate, ancestor) for ancestor in ancestors)}
        inherited = max((total(previous, visiting | {tid}) for previous in predecessors), default=0)
        memo[tid] = own.get(tid, 0) + inherited
        return memo[tid]

    return {tid: total(tid, set()) for tid in target_ids}
