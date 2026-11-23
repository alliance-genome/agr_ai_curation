"""A model version move is one catalog edit (ALL-1708), proven on real database rows.

Saved agents, pinned revisions and environment values keep the id they name. They run on
the catalog entry replacing that id, startup accepts them unchanged, and a rollback
catalog brings rows saved on the new id back. Ids come from the shipped catalog.
"""

import asyncio
from contextlib import nullcontext

import pytest
from agents import RunConfig, Runner
from sqlalchemy import text
from sqlalchemy.orm import Session

import src.lib.config.models_loader as models_loader
from src.lib.agent_studio import catalog_service, runtime_validation
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.lib.config.model_env_validation import model_env_errors
from src.lib.flows.execution_revisions import flow_execution_revision_findings
from src.lib.openai_agents import langfuse_client
from src.models.sql import database
from src.models.sql.agent import Agent
from src.models.sql.tool_policy import ToolPolicy
from tests.unit.lib.openai_agents.test_model_request_measurement import FakeResponsesHTTPModel, _final
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401
from .test_retired_model_conversion import (  # noqa: F401
    make_agent,
    make_flow,
    revision,
    save_on_retired,
    world,
)


def _families():
    """(replaced id, current entry) for each shipped OpenAI family, Sol and Astra."""
    pairs = [(replaced, model) for model in models_loader.list_models()
             if model.provider == "openai" for replaced in model.replaces]
    assert {model.model_id for _, model in pairs} >= {"gpt-6.2-sol", "gpt-6.2-astra"}
    return pairs


class RecordingProvider:
    """The provider boundary: records the model name each request asks for."""

    def __init__(self):
        self.requested = []

    def get_model(self, model_name):
        self.requested.append(model_name)
        model = FakeResponsesHTTPModel([_final()])
        model.model = model_name
        return model


def sent_model(agent):
    provider = RecordingProvider()
    asyncio.run(Runner.run(agent, "Read", run_config=RunConfig(model_provider=provider,
                                                               tracing_disabled=True)))
    return provider.requested


@pytest.fixture
def db(world, monkeypatch):  # noqa: F811
    ToolPolicy.__table__.create(world.connection())
    get_tool_policy_cache().refresh(world)
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(world))
    monkeypatch.setattr(runtime_validation, "SessionLocal", lambda: Session(
        bind=world.connection(), join_transaction_mode="create_savepoint"))
    monkeypatch.setattr(runtime_validation, "_load_expected_system_agent_keys", lambda: (set(), None))
    monkeypatch.setattr(langfuse_client, "log_agent_config", lambda **kwargs: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-credential")
    # No provider token counting from a test: the capacity pre-check gets a fixed count.
    from unittest.mock import AsyncMock
    monkeypatch.setattr("src.lib.openai_agents.context_capacity.NativeCapacityRequest.count",
                        AsyncMock(return_value=1000))
    return world


def _context(user_id=1):
    return {"db_user_id": user_id, "user_id": f"curator-{user_id}", "authenticated_groups": []}


@pytest.mark.parametrize("replaced,current", [(r, m.model_id) for r, m in _families()])
def test_a_saved_agent_and_an_old_pinned_version_run_on_the_current_entry(db, replaced, current):
    agent = make_agent(db, "Finder")
    older = save_on_retired(db, agent, instructions="Older instructions", model=replaced)
    head = save_on_retired(db, agent, instructions="Current instructions", model=replaced)
    agent.model_id = replaced
    flow = make_flow(db, [(agent, older)])
    revisions_before = db.scalar(text("SELECT count(*) FROM agent_execution_revisions"))
    pin_before = dict(flow.flow_definition["nodes"][1]["data"])

    head_agent = catalog_service.get_agent_by_id(agent.agent_key, **_context())
    pinned_agent = catalog_service.get_agent_by_id(
        agent.agent_key, execution_revision_id=str(older.id), **_context())

    assert head_agent.execution_revision_id == str(head.id)
    assert pinned_agent.execution_revision_id == str(older.id)
    assert sent_model(head_agent) == [current]
    assert sent_model(pinned_agent) == [current]
    # Nothing saved changed: no revision appended, the pin and the stored ids are as saved.
    assert db.scalar(text("SELECT count(*) FROM agent_execution_revisions")) == revisions_before
    assert flow.flow_definition["nodes"][1]["data"] == pin_before
    assert revision(db, older.id).snapshot["model_id"] == replaced
    assert agent.model_id == replaced
    assert flow_execution_revision_findings(db, flow.flow_definition, user_id=1, active_group_ids=[]) == []


def test_an_inactive_agent_on_a_replaced_id_runs_on_its_entry_when_reactivated(db):
    replaced, astra = next((r, m) for r, m in _families() if "xhigh" in m.reasoning_options)
    agent = make_agent(db, "Archived Astra")
    save_on_retired(db, agent, reasoning="xhigh", model=replaced)
    agent.model_id, agent.model_reasoning, agent.is_active = replaced, "xhigh", False
    db.flush()
    agent.is_active = True
    db.flush()
    assert sent_model(catalog_service.get_agent_by_id(agent.agent_key, **_context())) == [astra.model_id]


def test_startup_accepts_a_database_and_environment_full_of_replaced_ids(db, monkeypatch):
    """The proof of "one edit": every saved shape and every model setting still names a
    replaced id, and startup's two model gates pass with the new catalog alone."""
    project = db.scalar(text("SELECT id FROM projects LIMIT 1"))
    for index, (replaced, model) in enumerate(_families()):
        level = model.reasoning_options[-1]
        for active in (True, False):
            private = make_agent(db, f"Private {index} {active}")
            save_on_retired(db, private, reasoning=level, model=replaced)
            private.model_id, private.model_reasoning, private.is_active = replaced, level, active
        shared = make_agent(db, f"Project {index}", user_id=2)
        save_on_retired(db, shared, model=replaced)
        shared.model_id, shared.visibility, shared.project_id = replaced, "project", project
        db.add(Agent(agent_key=f"system_on_{index}", name=f"System {index}", instructions="System.",
                     model_id=replaced, model_reasoning="medium", visibility="system", is_active=True))
        monkeypatch.setenv(f"AGENT_FAMILY{index}_MODEL", replaced)
        monkeypatch.setenv(f"AGENT_FAMILY{index}_REASONING", level)
    sol = next(r for r, m in _families() if m.default)
    astra = next(r for r, m in _families() if "xhigh" in m.reasoning_options)
    for key in ("DEFAULT_AGENT_MODEL", "SUPERVISOR_MODEL", "VALIDATION_REVIEW_MODEL",
                "HIERARCHY_LLM_MODEL", "FIGURE_LOCATOR_LLM_MODEL", "ABSTRACT_EXTRACTION_MODEL"):
        monkeypatch.setenv(key, sol)
    monkeypatch.setenv("AGENT_STUDIO_OPENAI_MODEL", astra)
    db.flush()

    assert model_env_errors() == []
    report = runtime_validation.validate_and_cache_agent_runtime_contracts(strict_mode=False)
    assert report["errors"] == []


def test_startup_still_refuses_an_unknown_model_by_name(db):
    agent = make_agent(db, "Unknown")
    agent.model_id = "not-a-model"
    db.flush()
    with pytest.raises(RuntimeError, match=f"{agent.agent_key}: Unknown model_id 'not-a-model'"):
        runtime_validation.validate_and_cache_agent_runtime_contracts(strict_mode=False)


ROLLBACK = """
models:
  - model_id: {old_sol}
    name: Rollback Sol
    provider: openai
    default: true
    supports_temperature: false
    reasoning_options: [low, medium, high]
    replaces: [{new_sol}]
  - model_id: {old_astra}
    name: Rollback Astra
    provider: openai
    supports_temperature: false
    reasoning_options: [low, medium, high, xhigh]
    replaces: [{new_astra}]
"""


def test_rollback_catalog_brings_rows_saved_on_the_new_id_back(db, tmp_path, monkeypatch):
    """Rows saved on the new id after the move must keep working after a rollback: the
    rollback makes the old entry current and lists the new id in its replaces."""
    families = {model.model_id: replaced for replaced, model in _families()}
    agent = make_agent(db, "Saved after the move")
    save_on_retired(db, agent, model="gpt-6.2-sol")  # a version saved after the move
    agent.model_id = "gpt-6.2-sol"
    db.flush()
    path = tmp_path / "models.yaml"
    path.write_text(ROLLBACK.format(old_sol=families["gpt-6.2-sol"], new_sol="gpt-6.2-sol",
                                    old_astra=families["gpt-6.2-astra"], new_astra="gpt-6.2-astra"))
    models_loader.load_models(models_path=path, packages_dir=tmp_path / "none", force_reload=True)
    try:
        report = runtime_validation.validate_and_cache_agent_runtime_contracts(strict_mode=False)
        assert report["errors"] == []
        assert sent_model(catalog_service.get_agent_by_id(agent.agent_key, **_context())) == [
            families["gpt-6.2-sol"]]
    finally:
        models_loader.reset_cache()
