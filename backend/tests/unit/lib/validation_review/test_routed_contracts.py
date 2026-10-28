"""A route is a frozen execution identity, not a change to a sibling specialist."""
from contextlib import contextmanager
from dataclasses import dataclass, replace, asdict
from types import SimpleNamespace

from agents import Agent
import pytest
from pydantic_core import to_jsonable_python

from src.lib.domain_packs.validation_registry import ValidatorBinding, ValidatorRoute, ValidatorAgentRef, ValidationBindingState
from src.lib.validation_history import runtime, store
from src.lib.validation_review import admission, provider, validator_adapter as adapter
from src.schemas.validation_history import FrozenValidationInput


@dataclass
class Definition:
    agent_id: str
    instructions: str


def binding():
    return ValidatorBinding(binding_id="fixture.lookup", state=ValidationBindingState.ACTIVE,
        source_scope="pack", raw={"binding_id": "fixture.lookup", "route_by": "kind"},
        routes={name: ValidatorRoute(ValidatorAgentRef("fixture", name)) for name in ("one", "two")})


def test_routed_contract_covers_all_agents_and_does_not_depend_on_selected_route(monkeypatch):
    definitions = {name: Definition(name, "Original") for name in ("one", "two")}
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package", lambda package, name: definitions[name])
    configured = binding()
    contract = adapter.binding_contract(SimpleNamespace(binding=configured))
    assert all(adapter.binding_contract(SimpleNamespace(binding=configured.for_route(name))) == contract for name in configured.routes)
    definitions["two"].instructions = "Changed"
    assert adapter.binding_contract(SimpleNamespace(binding=configured.for_route("one"))) != contract


def test_direct_contract_keeps_original_representation(monkeypatch):
    definition = Definition("one", "Original")
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package", lambda *args: definition)
    configured = replace(binding(), routes=None, validator_agent=ValidatorAgentRef("fixture", "one"))
    expected = store.canonical({"binding": configured.raw, "agent": to_jsonable_python(asdict(definition)), "custom_validator": None})[1]
    assert adapter.binding_contract(SimpleNamespace(binding=configured)) == expected


def test_missing_runtime_specialist_has_typed_operational_reason(monkeypatch):
    monkeypatch.setattr("src.lib.config.agent_loader.get_agent_definition_for_package", lambda *args: None)
    with pytest.raises(admission.ReviewStopped, match="configured_validator_unavailable"):
        adapter.binding_contract(SimpleNamespace(binding=binding()))


def test_frozen_direct_request_does_not_gain_a_route_field():
    request = FrozenValidationInput(target_id="target", binding_id="binding", request={})
    assert "route_value" not in request.model_dump(mode="json")
    assert request.model_copy(update={"route_value": "one"}).model_dump(mode="json")["route_value"] == "one"


def test_route_pins_are_independent_but_changed_agent_and_unadmitted_route_are_rejected(monkeypatch):
    records = {}
    class DB:
        def scalar(self, statement):
            operation = statement.compile().params["operation_id_1"]
            return records.get(operation)
    @contextmanager
    def transaction():
        yield DB()
    attempts = {name: {"input": {"binding_id": "fixture.lookup", "route_value": name}} for name in ("one", "two")}
    monkeypatch.setattr(adapter.store, "SessionLocal", SimpleNamespace(begin=transaction))
    monkeypatch.setattr(adapter.store, "_artifact", lambda *args: None)
    monkeypatch.setattr(adapter.store, "_fact", lambda db, artifact, fact, **kw: attempts.get(fact))
    monkeypatch.setattr(adapter.store, "_content", lambda db, value: value)
    def append(db, artifact, **kw):
        records[kw["operation_id"]] = kw["body"]
    monkeypatch.setattr(adapter, "append_fact", append)
    execution_token = runtime.ACTIVE_VALIDATION_EXECUTION.set((SimpleNamespace(artifact_id="artifact", scope="scope"), {"execution_id": "execution", "attempt_ids": ["one", "two"]}))
    budget_token = provider.ACTIVE_BUDGET.set(SimpleNamespace(episode_id="episode"))
    try:
        one = Agent(name="one", model="fixture", instructions="Policy one")
        two = Agent(name="two", model="fixture", instructions="Policy two")
        adapter.pin_validator_agent(one, "fixture.lookup", route_value="one")
        adapter.pin_validator_agent(two, "fixture.lookup", route_value="two")
        assert len(records) == 2
        adapter.pin_validator_agent(one, "fixture.lookup", route_value="one")
        two.instructions = "Changed"
        with pytest.raises(admission.ReviewStopped, match="configured_validator_changed"):
            adapter.pin_validator_agent(two, "fixture.lookup", route_value="two")
        with pytest.raises(store.HistoryAccessError):
            adapter.pin_validator_agent(one, "fixture.lookup", route_value="unadmitted")
        with pytest.raises(store.HistoryAccessError):
            adapter.pin_validator_agent(one, "fixture.lookup")
    finally:
        provider.ACTIVE_BUDGET.reset(budget_token)
        runtime.ACTIVE_VALIDATION_EXECUTION.reset(execution_token)
