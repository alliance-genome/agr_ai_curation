"""Count scientific attempts through explicit immutable item ancestry.

A split inherits its predecessor's consumed attempts on each successor. A merge
inherits the largest consumed predecessor depth, not a sum that would exhaust a
fresh merged target before it runs. This does not infer item identity from values.
"""
from sqlalchemy import select

from src.lib.validation_history import store
from src.models.sql.validation_history import ValidationBody, ValidationFact
from src.models.sql.validation_review import ValidationReviewEpisode


class Lineage:
    """One read of an artifact's targets and attempts, counted for many targets.

    Facts and bodies are immutable, so one bulk read answers every count until
    the caller writes another target or attempt. Bodies are read, never changed.
    """

    def __init__(self, db, episode):
        # Plain columns, not ORM rows: an artifact can have thousands of targets.
        rows = db.execute(
            select(ValidationFact.id, ValidationFact.parent_id, ValidationBody.content)
            .join(ValidationBody, ValidationBody.sha256 == ValidationFact.body_sha)
            .where(ValidationFact.artifact_id == episode.artifact_id, ValidationFact.kind == "target")
        ).all()
        self.parents = {row.id: row.parent_id for row in rows}
        self.bodies = {row.id: row.content for row in rows}
        episodes = set(db.scalars(select(ValidationReviewEpisode.id).where(
            ValidationReviewEpisode.artifact_id == episode.artifact_id,
            ValidationReviewEpisode.scope_id == episode.scope_id)))
        self.own = {}
        for target_id, content in db.execute(
            select(ValidationFact.target_id, ValidationBody.content)
            .join(ValidationBody, ValidationBody.sha256 == ValidationFact.body_sha)
            .where(ValidationFact.artifact_id == episode.artifact_id, ValidationFact.kind == "attempt")
        ):
            if content["input"]["review_episode_id"] in episodes:
                self.own[target_id] = self.own.get(target_id, 0) + 1
        self.memo = {}

    def below(self, tid, ancestor):
        seen = set()
        while tid in self.parents:
            if tid in seen:
                raise store.HistoryConflict("Cyclic target parentage")
            if tid == ancestor:
                return True
            seen.add(tid)
            tid = self.parents[tid]
        return False

    def count(self, tid, visiting=frozenset()):
        if tid in self.memo:
            return self.memo[tid]
        if tid in visiting or tid not in self.parents:
            raise store.HistoryConflict("Invalid scientific attempt lineage")
        binding = self.bodies[tid].get("binding_id")
        ancestors = set()
        parent = self.parents[tid]
        while parent in self.parents:
            origin = self.bodies[parent].get("origin", {})
            if origin.get("kind") == "extraction_contribution":
                ancestors.update(origin.get("predecessor_ids", []))
            parent = self.parents[parent]
        predecessors = {candidate for candidate, body in self.bodies.items()
                        if candidate != tid and body.get("binding_id") == binding
                        and any(self.below(candidate, ancestor) for ancestor in ancestors)}
        inherited = max((self.count(previous, visiting | {tid}) for previous in predecessors), default=0)
        self.memo[tid] = self.own.get(tid, 0) + inherited
        return self.memo[tid]


def counts(db, episode, target_ids):
    lineage = Lineage(db, episode)
    return {tid: lineage.count(tid) for tid in target_ids}
