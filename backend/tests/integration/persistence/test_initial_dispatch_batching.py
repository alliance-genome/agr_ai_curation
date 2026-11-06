"""Initial validator work is queued in one transaction with the same durable rows.

Before validators start, the runtime queues one work item per run group. Each
item once had its own transaction, which made the pause before validators grow
with records times envelope size. These tests pin the durable result, so the
change touches only how the work is done.
"""
import asyncio
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext, prepare_validator_dispatch
from src.lib.validation_history import runtime, store
from src.lib.validation_review import admission, scheduling
from src.lib.validation_review.validator_adapter import ValidatorAdapter, save_plan
from src.models.sql.validation_history import ValidationFact
from src.models.sql.validation_review import (
    ValidationReviewEpisode as Episode,
    ValidationReviewReservation as Reservation,
    ValidationReviewWork as Work,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from tests.integration.persistence.test_validation_history import (
    migrated as migrated,
    scope as scope,
)
from tests.unit.lib.domain_packs.test_validator_dispatch import _multivalued_dispatch_pack

RECORDS = 5  # Batches of two: run groups of 2, 2 and 1 requests.
SCHEDULED_KINDS = {"execution", "attempt", "review_reservation", "review_work", "review_budget_warning"}


@pytest.fixture
def agents(monkeypatch):
    from src.lib.config.agent_loader import AgentDefinition

    monkeypatch.setattr(
        "src.lib.config.agent_loader.get_agent_definition_for_package",
        lambda package, agent: AgentDefinition(folder_name=agent, agent_id=agent, name=agent, package_id=package),
    )


def admitted_plan(scope, tmp_path):
    """A saved, admitted review plan whose binding batches two requests per run."""
    folder = tmp_path / uuid4().hex
    folder.mkdir()
    pack = _multivalued_dispatch_pack(folder)
    raw = deepcopy(pack.metadata.metadata)
    raw["validator_bindings"]["active"][0]["batch"] = {"enabled": True, "family": "fixture-evidence", "max_size": 2}
    pack = replace(pack, metadata=pack.metadata.model_copy(update={"metadata": raw}))
    envelope = DomainEnvelope(
        envelope_id=uuid4().hex,
        domain_pack_id=pack.pack_id,
        extracted_objects=[
            CuratableObjectEnvelope(object_type="Annotation", pending_ref_id=f"record-{index}",
                                    payload={"evidence_code_curies": [f"eco:{index}"]})
            for index in range(RECORDS)
        ],
    )
    envelope = runtime.capture_extraction(scope=scope, producer_id=uuid4().hex,
                                          typed=envelope.model_dump(mode="json"), envelope=envelope, provenance={})
    context = ValidatorRuntimeContext(user_id=scope.owner_id, document_id=scope.document_id, authenticated_groups=())
    plan = prepare_validator_dispatch(envelope, pack, runtime_context=context)
    plan_ref = save_plan(plan, context=context)
    artifact_id = plan.history_binding.artifact_id
    episode_id = plan.envelope.metadata["review_episode_id"]
    scope_id = admission.create_scope(scope, operation_id=uuid4().hex, kind="discovered_document_run",
                                      targets=0, episodes=0)
    admission.attach_episode(scope, artifact_id=artifact_id, episode_id=episode_id, scope_id=scope_id)
    return ValidatorAdapter(scope, artifact_id, episode_id, plan_ref, authenticated_groups=())


def queue_initial_work(adapter, monkeypatch, *, one_transaction_per_item=False):
    """Run the real initial() up to validator execution; return the queued items."""
    real = scheduling.initial
    queued = []

    def initial(scope, episode_id, items):
        queued.extend(items)
        if one_transaction_per_item:
            # The previous behaviour: every work item committed on its own.
            return [real(scope, episode_id, [item])[0] for item in items]
        return real(scope, episode_id, items)

    monkeypatch.setattr(scheduling, "initial", initial)
    monkeypatch.setattr(adapter, "execute", AsyncMock(side_effect=lambda wid: {"state": "not_started", "work_id": wid}))
    asyncio.run(adapter.initial())
    monkeypatch.setattr(scheduling, "initial", real)
    return queued


def durable_rows(adapter):
    """Every row initial scheduling writes, in commit order."""
    with store.SessionLocal() as db:
        facts = list(db.scalars(select(ValidationFact).where(
            ValidationFact.artifact_id == adapter.artifact_id, ValidationFact.kind.in_(SCHEDULED_KINDS))
            .order_by(ValidationFact.sequence)))
        contents = [store._content(db, fact) for fact in facts]
        work = {row.id: row for row in db.scalars(select(Work).where(Work.episode_id == adapter.episode_id))}
        reservations = {row.id: row for row in db.scalars(
            select(Reservation).where(Reservation.episode_id == adapter.episode_id))}
        episode = db.get(Episode, adapter.episode_id)
        return {
            "facts": [{"kind": fact.kind, "operation_id": fact.operation_id, "target_id": fact.target_id,
                       "parent_id": fact.parent_id, "content": content}
                      for fact, content in zip(facts, contents)],
            "work": [{"operation_id": work[content["work_id"]].operation_id, "payload": work[content["work_id"]].payload,
                      "state": work[content["work_id"]].state, "kind": work[content["work_id"]].kind,
                      "lease_token": work[content["work_id"]].lease_token}
                     for fact, content in zip(facts, contents) if fact.kind == "review_work"],
            "reservations": [{"operation_id": reservations[content["reservation_id"]].operation_id,
                              "request": reservations[content["reservation_id"]].request,
                              "charges": reservations[content["reservation_id"]].charges,
                              "state": reservations[content["reservation_id"]].state}
                             for fact, content in zip(facts, contents) if fact.kind == "review_reservation"],
            "work_count": len(work),
            "reservation_count": len(reservations),
            "episode": {"charged": episode.charged, "state": episode.state, "started": episode.started_at is not None},
        }


_IDENTITY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{64}|[0-9a-f]{32}")


def without_identities(rows):
    """Two artifacts differ only in random IDs and the digests built from them."""
    return _IDENTITY.sub("<id>", json.dumps(rows, sort_keys=True))


def test_one_transaction_writes_the_rows_one_transaction_per_item_wrote(scope, tmp_path, monkeypatch, agents):
    before = admitted_plan(scope, tmp_path)
    after = admitted_plan(scope, tmp_path)

    items_before = queue_initial_work(before, monkeypatch, one_transaction_per_item=True)
    items_after = queue_initial_work(after, monkeypatch)

    assert [len(item["requests"]) for item in items_after] == [2, 2, 1]
    assert [len(item["requests"]) for item in items_before] == [2, 2, 1]
    rows_before, rows_after = durable_rows(before), durable_rows(after)
    assert rows_after["work_count"] == rows_after["reservation_count"] == 3
    assert without_identities(rows_after) == without_identities(rows_before)

    # The masked comparison cannot see links between rows, so check them directly.
    with store.SessionLocal() as db:
        for item, work in zip(items_after, rows_after["work"], strict=True):
            assert work["operation_id"] == item["operation_id"] and work["state"] == "queued"
            assert work["payload"]["identity"] == store.canonical(
                {"requests": item["requests"], "receipt": item["receipt"]})[1]
            invocation = work["payload"]["invocation"]
            execution = store._fact(db, after.artifact_id, invocation["execution_id"], kind="execution")
            assert execution.operation_id == "execution:review:" + after.episode_id + ":" + item["operation_id"]
            attempts = [store._fact(db, after.artifact_id, aid, kind="attempt") for aid in invocation["attempt_ids"]]
            assert [attempt.parent_id for attempt in attempts] == [execution.id] * len(attempts)
            assert [attempt.target_id for attempt in attempts] == [r["target_id"] for r in item["requests"]]
    assert [reservation["operation_id"] for reservation in rows_after["reservations"]] == [
        "work:" + item["operation_id"] for item in items_after]


def test_queueing_again_returns_the_same_work_and_writes_nothing(scope, tmp_path, monkeypatch, agents):
    adapter = admitted_plan(scope, tmp_path)
    items = queue_initial_work(adapter, monkeypatch)
    rows = durable_rows(adapter)
    with store.SessionLocal() as db:
        first = [db.scalar(select(Work.id).where(Work.episode_id == adapter.episode_id,
                                                 Work.operation_id == item["operation_id"])) for item in items]

    assert scheduling.initial(adapter.scope, adapter.episode_id, items) == first
    # An item queued earlier on its own is kept; the rest are queued with it.
    assert scheduling.initial(adapter.scope, adapter.episode_id, items[:1]) == first[:1]
    assert durable_rows(adapter) == rows
    # A second receive finds the work already queued and queues nothing.
    assert queue_initial_work(adapter, monkeypatch) == []
    assert durable_rows(adapter) == rows


def test_an_earlier_single_item_is_kept_when_the_rest_are_queued(scope, tmp_path, monkeypatch, agents):
    reference = admitted_plan(scope, tmp_path)
    queue_initial_work(reference, monkeypatch)
    adapter = admitted_plan(scope, tmp_path)
    items = []
    real = scheduling.initial

    def first_only(scope, episode_id, all_items):
        items.extend(all_items)
        return real(scope, episode_id, all_items[:1])

    monkeypatch.setattr(scheduling, "initial", first_only)
    monkeypatch.setattr(adapter, "execute", AsyncMock(return_value={"state": "not_started"}))
    asyncio.run(adapter.initial())
    monkeypatch.setattr(scheduling, "initial", real)

    ids = scheduling.initial(adapter.scope, adapter.episode_id, items)
    with store.SessionLocal() as db:
        kept = db.scalar(select(Work.id).where(Work.episode_id == adapter.episode_id,
                                               Work.operation_id == items[0]["operation_id"]))
    assert ids[0] == kept and len(set(ids)) == len(items)
    assert without_identities(durable_rows(adapter)) == without_identities(durable_rows(reference))


def test_concurrent_receives_queue_each_item_once(scope, tmp_path, monkeypatch, agents):
    reference = admitted_plan(scope, tmp_path)
    queue_initial_work(reference, monkeypatch)
    adapter = admitted_plan(scope, tmp_path)
    items = []
    real = scheduling.initial
    monkeypatch.setattr(scheduling, "initial", lambda scope, episode_id, all_items: items.extend(all_items) or [])
    monkeypatch.setattr(adapter, "execute", AsyncMock(return_value={"state": "not_started"}))
    asyncio.run(adapter.initial())
    monkeypatch.setattr(scheduling, "initial", real)
    barrier = Barrier(2)

    def receive():
        barrier.wait(5)
        return scheduling.initial(adapter.scope, adapter.episode_id, items)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [future.result(30) for future in [pool.submit(receive), pool.submit(receive)]]

    assert first == second and len(set(first)) == len(items)
    assert without_identities(durable_rows(adapter)) == without_identities(durable_rows(reference))


def test_a_failure_part_way_leaves_no_queued_work(scope, tmp_path, monkeypatch, agents):
    reference = admitted_plan(scope, tmp_path)
    queue_initial_work(reference, monkeypatch)
    adapter = admitted_plan(scope, tmp_path)
    real_enqueue = scheduling.enqueue_in_transaction
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(kwargs["operation_id"])
        if len(calls) == 2:
            raise RuntimeError("database lost part-way")
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(scheduling, "enqueue_in_transaction", fail_second)
    monkeypatch.setattr(adapter, "execute", AsyncMock(return_value={"state": "not_started"}))
    with pytest.raises(RuntimeError, match="part-way"):
        asyncio.run(adapter.initial())

    # All or nothing: the first item, written before the failure, rolled back too.
    rows = durable_rows(adapter)
    assert rows["facts"] == rows["work"] == rows["reservations"] == []
    assert rows["work_count"] == rows["reservation_count"] == 0
    assert rows["episode"]["charged"] == {}

    monkeypatch.setattr(scheduling, "enqueue_in_transaction", real_enqueue)
    queue_initial_work(adapter, monkeypatch)
    assert without_identities(durable_rows(adapter)) == without_identities(durable_rows(reference))


def test_job_targets_resolve_off_the_event_loop_in_one_transaction(scope, tmp_path, monkeypatch, agents):
    adapter = admitted_plan(scope, tmp_path)
    real = runtime.binding_target
    calls = []

    def recorded(binding, match, *, db=None):
        calls.append((threading.current_thread() is threading.main_thread(), id(db)))
        return real(binding, match, db=db)

    monkeypatch.setattr(runtime, "binding_target", recorded)
    queue_initial_work(adapter, monkeypatch)

    assert len(calls) == RECORDS
    assert not any(on_loop for on_loop, _ in calls)
    assert len({session for _, session in calls}) == 1
