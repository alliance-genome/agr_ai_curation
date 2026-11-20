"""A many-record review plan saves its record set once and reads back exactly.

Saving every match whole repeated the whole envelope per match: a 97-record
paper's plan body reached 2.2 GB and PostgreSQL could not buffer the insert.
"""
import json

import pytest

from src.lib.validation_history import runtime, store
from src.lib.validation_review import saved_matches
from src.lib.validation_review.validator_adapter import ValidatorAdapter
from tests.integration.persistence.test_chat_review_adapter import (
    migrated as migrated,
    planned as planned,
    scope as scope,
)

RECORDS = 100


def _size(value):
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")))


@pytest.mark.parametrize("planned", [{"shape": "evidence", "count": RECORDS}], indirect=True)
def test_many_record_plan_stores_the_record_set_once_and_reads_back_equal(planned):
    item = planned
    assert len(item.plan.envelope.extracted_objects) == RECORDS
    assert len(item.plan.matches) >= RECORDS
    body = store.read_fact(item.scope, item.aid, item.adapter.plan_ref)["content"]

    # The match envelope (the dispatch source) is at most one stored part beside
    # the plan's own envelope; no saved match carries a record.
    assert len(body["match_parts"]["envelopes"]) <= 1
    envelope_size = _size(body["envelope"])
    assert max(_size(saved) for saved in body["matches"].values()) < 1024
    shared = _size(body["envelope"]) + _size(body["match_parts"])
    assert shared < 2 * envelope_size + _size(body["registry"]) + 200_000
    assert _size(body["matches"]) < 1024 * len(body["matches"])

    with store.SessionLocal.begin() as db:
        targets = [runtime.binding_target(item.plan.history_binding, match, db=db)
                   for match in item.plan.matches]
    fresh = ValidatorAdapter(item.scope, item.aid, item.ep, item.adapter.plan_ref,
                             authenticated_groups=item.adapter.context.authenticated_groups)
    restored = fresh.current_matches()
    assert set(restored) == set(targets)
    for target, match in zip(targets, item.plan.matches, strict=True):
        assert restored[target] == match
    # One parsed record set serves every match, and later reads reuse it.
    assert len({id(match.envelope) for match in restored.values()}) == 1
    assert fresh.match(targets[0]) is restored[targets[0]]
    assert saved_matches.decode(body) == restored
