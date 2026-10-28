"""Frozen execution routes cannot replace current source or binding authority."""
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.lib.validation_history import replay_binding, store


@dataclass
class Binding:
    binding_id: str
    raw: dict
    batch_enabled: bool = True


@dataclass
class Match:
    binding: Binding
    object_envelope: object
    field_path: str


@pytest.fixture
def retained_route(monkeypatch):
    flow = {"node_id": "selected", "data": {"agent_id": "gene"}}
    raw = {"validator": "configured", "field": "value"}
    match = Match(Binding("binding", deepcopy(raw)), object(), "objects[2].value")
    execution = SimpleNamespace(parent_id=None)
    plan_fact = SimpleNamespace(id="plan", body_sha="plan-body", parent_id="episode")
    receipt = {"plan_ref": "plan", "contracts": {"binding": "frozen"}}
    plan = {"envelope": {"metadata": {"review_episode_id": "episode"}},
            "registry": {"bindings": [{"binding_id": "binding", "raw": {**raw, "flow_validator": flow}}]},
            "contracts": {"binding": "frozen"}}
    def fact(db, artifact, identity, *, kind):
        assert artifact in {"artifact", "other-artifact"}
        assert (identity, kind) in {("execution", "execution"), ("plan", "review_chat_plan")}
        return execution if identity == "execution" else plan_fact
    monkeypatch.setattr(store, "_fact", fact)
    def content(db, row):
        assert row is execution, "Replay must not materialize the full plan body"
        return {"receipt": receipt}
    reads = []
    def authority(db, row):
        reads.append((row.id, row.body_sha))
        return (plan["registry"]["bindings"], plan["contracts"],
                plan["envelope"]["metadata"].get("review_episode_id"))
    monkeypatch.setattr(store, "_content", content)
    monkeypatch.setattr(replay_binding, "_plan_authority", authority)
    return SimpleNamespace(match=match, receipt=receipt, plan=plan, plan_fact=plan_fact,
        reads=reads, attempt={"input": {"review_episode_id": "episode"}},
        attempt_fact=SimpleNamespace(parent_id="execution"))


def restore(item, plans=None, artifact="artifact"):
    return replay_binding.restore_execution_binding(None, artifact, item.attempt_fact,
        item.attempt, item.match, plans={} if plans is None else plans)


def test_saved_route_preserves_current_object_and_address(retained_route):
    item = retained_route
    result = restore(item)
    assert result.object_envelope is item.match.object_envelope
    assert result.field_path == "objects[2].value"
    assert result.binding.raw["flow_validator"]["node_id"] == "selected"
    assert not result.binding.batch_enabled
    assert "flow_validator" not in item.match.binding.raw


@pytest.mark.parametrize("fault", ["episode", "envelope_episode", "contract", "binding", "route", "duplicate"])
def test_changed_plan_authority_is_rejected(retained_route, fault):
    item = retained_route
    if fault == "episode":
        item.plan_fact.parent_id = "different"
    elif fault == "envelope_episode":
        item.plan["envelope"]["metadata"]["review_episode_id"] = "different"
    elif fault == "contract":
        item.receipt["contracts"]["binding"] = "different"
    elif fault == "binding":
        item.match.binding.raw["validator"] = "different"
    elif fault == "route":
        item.match.binding.raw["flow_validator"] = {"node_id": "different"}
    else:
        item.plan["registry"]["bindings"] *= 2
    with pytest.raises(store.HistoryConflict):
        restore(item)


def test_direct_dispatch_does_not_invent_route(retained_route):
    item = retained_route
    del item.receipt["plan_ref"]
    assert restore(item) is item.match
    assert item.reads == []


@pytest.mark.parametrize("changed_identity", ["id", "body_sha", "artifact"])
def test_plan_reads_are_shared_only_for_the_exact_plan(retained_route, changed_identity):
    item = retained_route
    plans = {}
    restore(item, plans)
    restore(item, plans)
    assert len(item.reads) == 1
    artifact = "artifact"
    if changed_identity == "artifact":
        artifact = "other-artifact"
    else:
        setattr(item.plan_fact, changed_identity, "another")
    restore(item, plans, artifact)
    assert len(item.reads) == 2
    restore(item, {})
    assert len(item.reads) == 3


@pytest.mark.parametrize("fault", ["episode", "contract", "binding", "route"])
def test_reused_authority_does_not_skip_per_completion_checks(retained_route, fault):
    item = retained_route
    plans = {}
    restore(item, plans)
    if fault == "episode":
        item.attempt["input"]["review_episode_id"] = "another"
    elif fault == "contract":
        item.receipt["contracts"]["binding"] = "another"
    elif fault == "binding":
        item.match.binding.raw["validator"] = "another"
    else:
        item.match.binding.raw["flow_validator"] = {"node_id": "another"}
    with pytest.raises(store.HistoryConflict):
        restore(item, plans)
    assert len(item.reads) == 1


def test_returned_route_cannot_mutate_reused_authority(retained_route):
    item = retained_route
    plans = {}
    first = restore(item, plans)
    first.binding.raw["flow_validator"]["data"]["agent_id"] = "changed"
    second = restore(item, plans)
    assert second.binding.raw["flow_validator"]["data"]["agent_id"] == "gene"
    assert item.plan["registry"]["bindings"][0]["raw"]["flow_validator"]["data"]["agent_id"] == "gene"
    assert second.object_envelope is item.match.object_envelope


@pytest.mark.parametrize("stored_episode", [None, 123])
def test_missing_or_nonstring_plan_episode_cannot_match(retained_route, stored_episode):
    item = retained_route
    item.plan["envelope"]["metadata"]["review_episode_id"] = stored_episode
    item.plan_fact.parent_id = item.attempt["input"]["review_episode_id"] = "123"
    with pytest.raises(store.HistoryConflict, match="another episode"):
        restore(item)


@pytest.mark.parametrize("bindings,contracts", [(None, {}), ({}, {}), ([], None), ([], [])])
def test_malformed_authority_fields_fail_closed(bindings, contracts):
    db = SimpleNamespace(execute=lambda statement: SimpleNamespace(
        one=lambda: (bindings, contracts, "episode")))
    with pytest.raises(store.HistoryConflict, match="malformed authority"):
        replay_binding._plan_authority(db, SimpleNamespace(body_sha="body"))
