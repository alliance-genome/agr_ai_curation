"""One requested record never becomes a new review of its neighbors."""

from uuid import uuid4
import pytest
from sqlalchemy import select, func
from src.lib.validation_history import store as h, selections as s
from src.models.sql.validation_history import ValidationFact
from tests.integration.persistence.test_validation_history import (
    scope as scope,
    migrated as migrated,
    artifact,
)
from tests.integration.persistence.test_selected_value_authority import answer, review


def test_only_b_needs_a_new_review_and_a_c_keep_their_exact_selections(scope):
    aid = artifact(scope)
    targets = {
        name: h.register_target(
            scope, aid, identity=name, original={"binding_id": name}
        )
        for name in "ABC"
    }
    obligations = [
        {"target_id": tid, "binding_id": name, "mode": "validation"}
        for name, tid in targets.items()
    ]
    original = s.start_episode(
        scope,
        aid,
        episode={
            "episode_id": uuid4().hex,
            "reason": "original",
            "obligations": obligations,
        },
    )
    for name, tid in targets.items():
        review(scope, aid, original, tid, answer(scope, aid, tid, value=name))
    before = s.read_manifest(scope, aid, episode_id=original)
    assert before["ready"]
    new = s.start_episode(
        scope,
        aid,
        episode={
            "episode_id": uuid4().hex,
            "reason": "Curator requested B",
            "obligations": obligations,
            "review_target_ids": [targets["B"]],
            "retained_episode_ids": {targets["A"]: original, targets["C"]: original},
        },
    )
    pending = s.read_manifest(scope, aid, episode_id=new)
    assert {b["target_id"] for b in pending["blockers"]} == {targets["B"]}
    assert not pending["review_ready"]
    with pytest.raises(h.HistoryAccessError, match="outside the curator-requested"):
        review(
            scope,
            aid,
            new,
            targets["A"],
            answer(scope, aid, targets["A"], value="unrequested"),
        )
    # The failed neighboring mutation cannot change A's selection/version.
    with h.SessionLocal() as db:
        decision_count = db.scalar(
            select(func.count())
            .select_from(ValidationFact)
            .where(ValidationFact.artifact_id == aid, ValidationFact.kind == "decision")
        )
    review(
        scope, aid, new, targets["B"], answer(scope, aid, targets["B"], value="B-new")
    )
    after = s.read_manifest(scope, aid, episode_id=new)
    for name in ("A", "C"):
        old = next(u for u in before["units"] if u["target_id"] == targets[name])
        current = next(u for u in after["units"] if u["target_id"] == targets[name])
        assert current["selection_id"] == old["selection_id"]
        assert current["version"] == old["version"]
        assert current["selected"] == old["selected"]
    with h.SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ValidationFact)
                .where(
                    ValidationFact.artifact_id == aid, ValidationFact.kind == "decision"
                )
            )
            == decision_count + 1
        )


def test_b_can_finish_without_fabricating_acceptance_for_unreviewed_a(scope):
    aid = artifact(scope)
    targets = {
        name: h.register_target(
            scope, aid, identity=name, original={"binding_id": name}
        )
        for name in "AB"
    }
    obligations = [
        {"target_id": tid, "binding_id": name, "mode": "validation"}
        for name, tid in targets.items()
    ]
    original = s.start_episode(
        scope,
        aid,
        episode={
            "episode_id": uuid4().hex,
            "reason": "original",
            "obligations": obligations,
        },
    )
    new = s.start_episode(
        scope,
        aid,
        episode={
            "episode_id": uuid4().hex,
            "reason": "B only",
            "obligations": obligations,
            "review_target_ids": [targets["B"]],
            "retained_episode_ids": {targets["A"]: original},
        },
    )
    review(scope, aid, new, targets["B"], answer(scope, aid, targets["B"]))
    manifest = s.read_manifest(scope, aid, episode_id=new)
    assert manifest["review_ready"]
    assert not manifest["ready"]
    assert {b["target_id"] for b in manifest["blockers"]} == {targets["A"]}


def test_fresh_round_budget_counts_only_b(scope):
    from src.lib.validation_review.record_scope import validation_targets

    aid = artifact(scope)
    targets = {
        name: h.register_target(
            scope, aid, identity=name, original={"binding_id": name}
        )
        for name in "ABC"
    }
    obligations = [
        {"target_id": tid, "binding_id": name, "mode": "validation"}
        for name, tid in targets.items()
    ]
    body = {"obligations": obligations, "review_target_ids": [targets["B"]]}
    with h.SessionLocal() as db:
        assert validation_targets(db, aid, body) == {targets["B"]}
        assert validation_targets(db, aid, {"obligations": obligations}) == set(
            targets.values()
        )
