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
    plan_fact = SimpleNamespace(parent_id="episode")
    receipt = {"plan_ref": "plan", "contracts": {"binding": "frozen"}}
    plan = {"envelope": {"metadata": {"review_episode_id": "episode"}},
            "registry": {"bindings": [{"binding_id": "binding", "raw": {**raw, "flow_validator": flow}}]},
            "contracts": {"binding": "frozen"}}
    def fact(db, artifact, identity, *, kind):
        assert artifact == "artifact"
        assert (identity, kind) in {("execution", "execution"), ("plan", "review_chat_plan")}
        return execution if identity == "execution" else plan_fact
    monkeypatch.setattr(store, "_fact", fact)
    monkeypatch.setattr(store, "_content", lambda db, row: {"receipt": receipt} if row is execution else plan)
    return SimpleNamespace(match=match, receipt=receipt, plan=plan, plan_fact=plan_fact,
        attempt={"input": {"review_episode_id": "episode"}}, attempt_fact=SimpleNamespace(parent_id="execution"))


def restore(item):
    return replay_binding.restore_execution_binding(None, "artifact", item.attempt_fact, item.attempt, item.match)


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
