"""Review saves must not reload the selected-source plan per target or block the event loop.

Production runs one server worker. A saved source plan can be many megabytes,
and reloading it for every configured target on the event loop froze every
curator's requests for about 15 seconds per review save.
"""
import asyncio
import time

import pytest
from sqlalchemy import select

from src.lib.validation_history import selections, store
from src.lib.validation_review.audit import append_fact
from src.lib.validation_review.validator_adapter import ValidatorAdapter
from src.models.sql.validation_history import ValidationFact
from tests.integration.persistence.test_chat_review_adapter import (
    checkpoint_provider,
    migrated as migrated,
    planned as planned,
    scope as scope,
)
from tests.integration.persistence.test_followup_source_selection import retained_proposal
from tests.unit.lib.domain_packs.test_validator_dispatch import _result_payload


def _count_source_plan_loads(monkeypatch):
    loads = []
    original = store._content

    def counting(db, fact):
        if fact.kind == "review_source_plan":
            loads.append(fact.id)
        return original(db, fact)

    monkeypatch.setattr(store, "_content", counting)
    return loads


async def _publish_selected_source(item, monkeypatch):
    def run(request, binding, **kwargs):
        return {**_result_payload(request, resolved_values={"curie": "ECO:resolved"}), "resolved_objects": []}

    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_runner",
                        lambda: run)
    monkeypatch.setattr("src.lib.validation_review.validator_adapter._default_package_scoped_validator_batch_runner",
                        lambda: lambda *args, **kwargs: pytest.fail("Unexpected batch"))
    await item.adapter.initial()
    context, contribution = retained_proposal(item)
    checkpoint = checkpoint_provider(item)
    with store.SessionLocal() as db:
        digest = selections.source_state_digest(db, item.aid)
    await item.adapter.select_source(
        context, checkpoint, operation_id="select-source", contribution_ref=contribution,
        field_paths=["evidence_code_curies"], evidence_ids=[], expected_source_sha=digest,
        reason="The extractor clarified the first item from Methods")


@pytest.mark.asyncio
async def test_source_plan_body_loads_once_per_published_plan(planned, monkeypatch):
    item = planned
    await _publish_selected_source(item, monkeypatch)
    loads = _count_source_plan_loads(monkeypatch)

    targets = list(item.adapter.plan["matches"])
    for _ in range(3):
        for target in targets:
            item.adapter.match(target)
    # select_source already adopted the published plan.
    assert loads == []

    fresh = ValidatorAdapter(item.scope, item.aid, item.ep, item.adapter.plan_ref,
                             authenticated_groups=item.adapter.context.authenticated_groups)
    fresh.current_matches()
    fresh.current_matches()
    assert len(loads) == 1


@pytest.mark.asyncio
async def test_newly_published_source_plan_is_adopted_by_next_match(planned, monkeypatch):
    item = planned
    await _publish_selected_source(item, monkeypatch)
    with store.SessionLocal() as db:
        latest = db.scalar(select(ValidationFact).where(
            ValidationFact.artifact_id == item.aid,
            ValidationFact.kind == "review_source_plan",
        ).order_by(ValidationFact.sequence.desc()).limit(1))
        body = store._content(db, latest)
    body["envelope"]["metadata"]["fixture_marker"] = "second plan"
    with store.SessionLocal.begin() as db:
        append_fact(db, item.aid, operation_id="source-plan:fixture-second", kind="review_source_plan",
                    parent_id=item.ep, body=body)
    loads = _count_source_plan_loads(monkeypatch)

    target = next(iter(item.adapter.plan["matches"]))
    item.adapter.match(target)
    item.adapter.match(target)

    assert len(loads) == 1
    assert item.adapter.plan["envelope"]["metadata"]["fixture_marker"] == "second plan"


@pytest.mark.asyncio
async def test_review_save_keeps_the_event_loop_serving_other_requests(planned, monkeypatch):
    adapter = planned.adapter
    original = adapter.refresh_source_plan

    def slow_refresh():
        # Stands in for loading a multi-megabyte plan from the database.
        time.sleep(0.05)
        original()

    monkeypatch.setattr(adapter, "refresh_source_plan", slow_refresh)
    monkeypatch.setattr(adapter, "prepare_inputs",
                        lambda provider: pytest.fail("No binding runs after another"))
    ticks = 0
    saved = asyncio.Event()

    async def other_requests():
        nonlocal ticks
        while not saved.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    async def review_save():
        try:
            return await adapter.advance(lambda: pytest.fail("No inputs need preparing"))
        finally:
            saved.set()

    outcome, _ = await asyncio.gather(review_save(), other_requests())

    assert outcome == []
    assert ticks >= 3
