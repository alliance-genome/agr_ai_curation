"""A deferred validator answer is offered again exactly when continuation counts it (v0.11.6 D)."""
from uuid import uuid4

import pytest

from src.lib.validation_history import selections, store
from src.lib.validation_review import continuation, decisions, reads
from src.lib.validation_review.audit import append_fact
from src.lib.validation_review.decisions import ReviewContext
from tests.integration.persistence.test_chat_review_adapter import (
    checkpoint_provider, migrated as migrated, planned as planned, scope as scope,
)
from tests.integration.persistence.test_review_followup_rounds import later_episode
from tests.integration.persistence.test_review_packet import _answers, _decide, _handles
from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload


def _resolved(request):
    curie = request.selected_inputs["curie"]
    return {**_result_payload(request, resolved_values={"curie": curie.upper()}), "resolved_objects": []}


def _pinned(item, packet_id):
    with store.SessionLocal() as db:
        return store._content(db, store._fact(db, item.aid, packet_id, kind=reads.PACKET_KIND))["handles"]


def _units(item, packet_id, handles, key="decision_unit_id"):
    pinned = _pinned(item, packet_id)
    return {pinned[handle][key] for handle in handles}


def _read(item, episode, tools=frozenset()):
    """The units continuation counts, the fresh packet, and the units it offers by handle."""
    view = continuation.inspect(ReviewContext(item.scope, episode, "supervisor"), tools)
    page = reads.review_packet(item.scope, episode)
    return view, page, {pinned["decision_unit_id"] for pinned in _pinned(item, page["packet_id"]).values()}


def _save(item, episode, page, packet_decisions):
    return decisions.record_packet_decisions(
        ReviewContext(item.scope, episode, "supervisor"), checkpoint_request=item.checkpoint(),
        operation_id=uuid4().hex, packet_id=page["packet_id"], packet_decisions=packet_decisions)


def _followup(item, record):
    """The durable request fact is what a targeted re-check leaves, as in extraction_scheduling.request."""
    with store.SessionLocal.begin() as db:
        append_fact(db, item.aid, operation_id="extraction-followup:" + uuid4().hex,
                    kind="extraction_followup_request", parent_id=item.ep,
                    body={"entry_ids": [record], "identity": uuid4().hex})


async def _defer_one_record(item, monkeypatch):
    """Production's first save: accept every other answer and defer one record's answers."""
    _answers(monkeypatch, _resolved)
    # One provider per extraction: each provider pins its own source extraction result.
    item.checkpoint = checkpoint_provider(item)
    await item.adapter.initial()
    first = reads.review_packet(item.scope, item.ep)
    deferred, *others = first["records"]
    _save(item, item.ep, first, [*_decide(_handles(deferred), "defer"),
                                 *_decide([handle for record in others for handle in _handles(record)])])
    return (deferred["target_id"], _units(item, first["packet_id"], _handles(deferred)),
            _units(item, first["packet_id"], _handles(deferred), key="target_id"))


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [{"count": 2}], indirect=True)
async def test_a_deferred_answer_is_offered_again_once_its_recheck_ran(planned, monkeypatch):
    # Production trace 1b937f7b on v0.11.5: the reviewer deferred the w validator answer and
    # asked for a targeted re-check. Continuation then counted the answer as decidable, but the
    # packet showed it as waiting with no handle, so reviewer runs 3 and 4 could change nothing.
    item = planned
    record, units, _ = await _defer_one_record(item, monkeypatch)
    view, page, offered = _read(item, item.ep)
    assert view["eligible"] == set() and offered == set(), "the reviewer's own defer, with nothing new"
    _followup(item, record)
    view, page, offered = _read(item, item.ep)
    assert view["eligible"] == units
    assert offered == units, "the packet offers every unit continuation counts"
    assert {entry["review"] for record in page["records"] for entry in record["review"]} == {"validator_answer"}
    result = _save(item, item.ep, page, _decide([handle for record in page["records"] for handle in _handles(record)]))
    assert result["ready"]


def _contribution(item, record):
    with store.SessionLocal.begin() as db:
        append_fact(db, item.aid, operation_id="late-contribution:" + uuid4().hex, kind="extraction_contribution",
                    target_id=record, parent_id=item.ep, body={"entry_id": record, "late": True})


def _defer_again(item, record):
    _followup(item, record)
    _, page, _ = _read(item, item.ep)
    _save(item, item.ep, page, _decide([handle for entry in page["records"] for handle in _handles(entry)], "defer"))


def _curator_defer(item, units):
    with store.SessionLocal.begin() as db:
        selections.decide_episode_in_transaction(
            db, item.scope, item.aid, episode_id=item.ep, operation_id=uuid4().hex,
            actor={"type": "curator", "id": item.scope.owner_id},
            entries=[{"target_id": unit, "expected_version": store._current(db, item.aid, unit)["version"],
                      "action": "defer", "reason": "Curator judgment"} for unit in sorted(units)])


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [{"count": 2}], indirect=True)
@pytest.mark.parametrize("after,counted", [
    ("nothing", False),  # the reviewer's own defer, with nothing new since
    ("followup", True),  # a targeted re-check ran after the defer
    ("contribution", True),  # the extractor contributed to the record after the defer
    ("deferred_again", False),  # deferred again after the re-check, with nothing new since
    ("curator_defer", False),  # a curator's pending decision is the curator's to change
    ("later_round", True),  # a decision of an earlier round does not settle this one
])
async def test_the_packet_offers_a_deferred_answer_exactly_when_continuation_counts_it(
        planned, monkeypatch, after, counted):
    item = planned
    record, units, _ = await _defer_one_record(item, monkeypatch)
    episode = item.ep
    if after == "followup":
        _followup(item, record)
    elif after == "contribution":
        _contribution(item, record)
    elif after == "deferred_again":
        _defer_again(item, record)
    elif after == "curator_defer":
        _followup(item, record)
        _curator_defer(item, units)
    elif after == "later_round":
        episode = later_episode(item, predecessor=item.ep, curator_round=False)
    view, page, offered = _read(item, episode)
    assert view["eligible"] & units == offered & units == (units if counted else set())
    entries = [entry for entry in page["records"][0]["review"]]
    assert {entry["review"] for entry in entries} == {"validator_answer" if counted else "waiting"}


@pytest.mark.asyncio
@pytest.mark.parametrize("planned", [{"count": 2}], indirect=True)
async def test_a_settled_answer_with_reconsideration_left_runs_only_for_that_repair(planned, monkeypatch):
    # ALL-1503 keeps a pending record eligible while a re-check or reconsideration is left.
    # The packet shows the settled answer as waiting with the target_id that repair names.
    item = planned
    _, units, targets = await _defer_one_record(item, monkeypatch)
    view, page, offered = _read(item, item.ep, {"request_validation_reconsideration"})
    assert view["eligible"] == units and view["causes"] == []
    assert offered == set()
    waiting = [entry for entry in page["records"][0]["review"]]
    assert {entry["review"] for entry in waiting} == {"waiting"}
    assert {entry["target_id"] for entry in waiting} == targets
