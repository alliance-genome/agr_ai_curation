"""Review reruns only when something can change, and curator rounds renew one re-check (ALL-1503)."""
from uuid import uuid4

import pytest

from src.lib.validation_history import selections, store
from src.lib.validation_review import admission, continuation, extraction_scheduling, reads
from src.lib.validation_review.audit import append_fact
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_review.extractor_phase import TARGETED_EXTRACTION_PROTOCOL, TargetedFollowupTransport
from src.models.sql.validation_review import ValidationReviewEpisode
from tests.integration.persistence.test_chat_review_adapter import planned as planned
from tests.integration.persistence.test_extraction_followup_admission import request_args
from tests.integration.persistence.test_review_capabilities import EXTRACTION_TOOLS
from tests.integration.persistence.test_review_record_source import setup_record
from tests.integration.persistence.test_selected_value_authority import review
from tests.integration.persistence.test_validation_history import scope as scope, migrated as migrated


CONFIGURED = {"agent_key": "fixture_extractor", "extractor_contract": {
    "followup_protocol": TARGETED_EXTRACTION_PROTOCOL,
    "followup_transport_schema": TargetedFollowupTransport.model_json_schema(),
}}


def extraction_only_record(scope):
    """One extraction-only record whose saved extractor supports a targeted follow-up."""
    aid, _, record, *_ = setup_record(scope, provenance=CONFIGURED)
    ep = selections.start_episode(scope, aid, episode={
        "episode_id": uuid4().hex, "reason": "record review",
        "obligations": [{"target_id": record, "mode": "extraction_only"}],
    })
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=1, episodes=1)
    admission.attach_episode(scope, artifact_id=aid, episode_id=ep, scope_id=sid)
    return aid, ep, record


def used_followup(aid, ep, record):
    """The durable request fact is what consumes the re-check, as in extraction_scheduling.request."""
    with store.SessionLocal.begin() as db:
        append_fact(db, aid, operation_id="extraction-followup:" + uuid4().hex,
                    kind="extraction_followup_request", parent_id=ep,
                    body={"entry_ids": [record], "identity": uuid4().hex})


def packet_flag(scope, ep):
    record, = [item for item in reads.review_packet(scope, ep)["records"] if item["record"] is not None]
    return record["follow_up_available"]


def test_rejected_record_with_its_recheck_used_stops_with_an_honest_cause(scope):
    aid, ep, record = extraction_only_record(scope)
    context = ReviewContext(scope, ep, "supervisor")
    assert packet_flag(scope, ep) is True
    used_followup(aid, ep, record)
    assert packet_flag(scope, ep) is False
    review(scope, aid, ep, record, "baseline", action="reject")
    view = continuation.inspect(context, EXTRACTION_TOOLS)
    assert view["eligible"] == set()
    assert view["causes"] == [{"target_id": record, "code": "reviewer_left_pending", "decision": "reject"}]
    assert view["pending"] == [{"record_target_id": record, "why": "not_accepted", "recheck_left": False}]
    outcome = continuation.terminal(view, [{"tool": "request_targeted_extraction", "target_id": record,
                                            "code": "extraction_attempts_exhausted", "resource": record}], None)
    assert outcome["reason"] == "reviewer_left_pending"


def test_rejected_record_with_a_recheck_left_stays_eligible(scope):
    aid, ep, record = extraction_only_record(scope)
    review(scope, aid, ep, record, "baseline", action="reject")
    view = continuation.inspect(ReviewContext(scope, ep, "supervisor"), EXTRACTION_TOOLS)
    assert view["eligible"] == {record} and view["causes"] == []
    assert view["pending"] == [{"record_target_id": record, "why": "not_accepted", "recheck_left": True}]


def test_new_input_after_the_decision_is_read_in_another_run(scope):
    aid, ep, record = extraction_only_record(scope)
    used_followup(aid, ep, record)
    review(scope, aid, ep, record, "baseline", action="defer")
    with store.SessionLocal.begin() as db:
        append_fact(db, aid, operation_id="late-contribution:" + uuid4().hex, kind="extraction_contribution",
                    target_id=record, parent_id=ep, body={"entry_id": record, "late": True})
    view = continuation.inspect(ReviewContext(scope, ep, "supervisor"), EXTRACTION_TOOLS)
    assert view["eligible"] == {record}


def test_a_decision_from_an_earlier_episode_does_not_settle_this_one(scope):
    aid, earlier, record = extraction_only_record(scope)
    used_followup(aid, earlier, record)
    review(scope, aid, earlier, record, "baseline", action="reject")
    later = selections.start_episode(scope, aid, episode={
        "episode_id": uuid4().hex, "reason": "later review",
        "obligations": [{"target_id": record, "mode": "extraction_only"}]})
    sid = admission.create_scope(scope, operation_id=uuid4().hex, kind="document_run", targets=1, episodes=1)
    admission.attach_episode(scope, artifact_id=aid, episode_id=later, scope_id=sid)
    view = continuation.inspect(ReviewContext(scope, later, "supervisor"), EXTRACTION_TOOLS)
    assert view["eligible"] == {record}
    assert view["pending"][0]["why"] == "not_decided"


def later_episode(item, *, predecessor, curator_round):
    with store.SessionLocal() as db:
        obligations = store._content(db, store._fact(db, item.aid, item.ep))["obligations"]
        previous_scope = db.get(ValidationReviewEpisode, predecessor).scope_id
    episode = selections.start_episode(item.scope, item.aid, episode={
        "episode_id": str(uuid4()), "reason": "Another review round", "obligations": obligations})
    if curator_round:
        root = admission.create_scope(item.scope, operation_id=str(uuid4()), kind="workspace_operation",
                                      targets=len(obligations), episodes=1)
        sid = admission.create_scope(item.scope, operation_id=str(uuid4()), kind="document_run",
                                     targets=len(obligations), episodes=1, parent_id=root)
    else:
        sid = previous_scope
    admission.attach_episode(item.scope, artifact_id=item.aid, episode_id=episode, scope_id=sid,
                             predecessor_id=predecessor, renewal_actor_id=item.scope.owner_id,
                             renewal_reason="Explicit renewal", curator_round=curator_round)
    return episode


def followup_args(item, episode):
    """request_args, with the parent decision recorded in ``episode``."""
    args = request_args(item)
    with store.SessionLocal.begin() as db:
        parent = store._fact(db, item.aid, args["parent_decision_id"], kind="decision")
        op = uuid4().hex
        moved = store._append(db, item.aid, operation_id="decision:" + op, kind="decision",
                              body=store._content(db, parent))
        store._append(db, item.aid, operation_id="episode-disposition:" + op,
                      kind="episode_disposition", parent_id=episode, body={"selection_ids": []})
    return {**args, "operation_id": uuid4().hex, "parent_decision_id": moved.id}


def test_curator_round_renews_one_recheck_and_machine_renewal_does_not(planned):
    item = planned
    first = ReviewContext(item.scope, item.ep, "supervisor")
    args = request_args(item)
    extraction_scheduling.request(first, **args)
    # Inside the round, a new operation ID cannot reset the allowance.
    with pytest.raises(admission.ReviewStopped, match="extraction_attempts_exhausted"):
        extraction_scheduling.request(first, **{**args, "operation_id": uuid4().hex})
    machine = later_episode(item, predecessor=item.ep, curator_round=False)
    with pytest.raises(admission.ReviewStopped, match="extraction_attempts_exhausted"):
        extraction_scheduling.request(ReviewContext(item.scope, machine, "supervisor"),
                                      **followup_args(item, machine))
    curator = later_episode(item, predecessor=machine, curator_round=True)
    renewed = ReviewContext(item.scope, curator, "supervisor")
    work_id = extraction_scheduling.request(renewed, **followup_args(item, curator))
    with store.SessionLocal() as db:
        from src.models.sql.validation_review import ValidationReviewWork
        attempt = db.get(ValidationReviewWork, work_id).payload["specialist_input"]["context"]["attempt"]
        assert attempt[args["entry_ids"][0]] == {"ordinal": 2, "ceiling": 2, "remaining_after": 0}
    with pytest.raises(admission.ReviewStopped, match="extraction_attempts_exhausted"):
        extraction_scheduling.request(renewed, **followup_args(item, curator))
    with store.SessionLocal() as db:
        counts = {episode: extraction_scheduling.attempt_counts(
            db, db.get(ValidationReviewEpisode, episode), args["entry_ids"])[args["entry_ids"][0]]
            for episode in (item.ep, machine, curator)}
    assert counts == {item.ep: 2, machine: 2, curator: 2}
