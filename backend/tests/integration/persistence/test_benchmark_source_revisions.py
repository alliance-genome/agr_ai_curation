"""Real PG source revisions through benchmark catalog, plan and agent adapter."""

from contextlib import nullcontext

import pytest
from src.models.sql.agent import Agent
from src.models.sql.user import (
    User,  # noqa: F401 - owning FK metadata for isolated fixtures
)

from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize("with_tool", [False, True])
async def test_custom_benchmark_executes_frozen_source_with_separate_model_route(execution_db, monkeypatch, with_tool):  # noqa: F811
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.execution_revision_service import (
        ExecutionRevisionNotFoundError,
        append_execution_revision,
    )
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.benchmarks import runtime, runtime_catalog
    from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
    from src.lib.benchmarks.lifecycle import (
        BenchmarkLifecycleFailure,
        authoritative_plan,
    )
    from src.lib.benchmarks.models import BenchmarkSuite
    from src.lib.benchmarks.suites import resolve_suite
    from src.lib.openai_agents import config, langfuse_client
    from src.models.sql import database
    from src.models.sql.tool_policy import ToolPolicy
    from src.schemas.agent_execution_revision import AgentOutputContract

    db, agent_id, _, _ = execution_db
    head = db.get(Agent, agent_id)
    head.model_id = "gpt-5.6-sol"
    if with_tool:
        ToolPolicy.__table__.create(db.connection())
        head.tool_ids = ["synthetic_lookup"]
        policy = ToolPolicy(tool_key="synthetic_lookup", display_name="Synthetic lookup", description="Test",
                            category="Test", curator_visible=True, allow_attach=True, allow_execute=True)
        db.add(policy)
        db.flush()
        # The real current ToolPolicy check remains active; no actual tool or
        # provider needs to be instantiated to prove revocation before dispatch.
        monkeypatch.setattr(catalog_service, "resolve_tools", lambda *_args: [])
    saved = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    first = append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(runtime_catalog, "list_agents_visible_to_user", lambda *a, **k: [head])
    monkeypatch.setattr(runtime_catalog, "load_benchmark_flow_templates", lambda _: [])
    monkeypatch.setattr(config, "get_model_for_agent", lambda model, **kwargs: model)
    monkeypatch.setattr(langfuse_client, "log_agent_config", lambda **kwargs: None)
    curator = BenchmarkCuratorContext(subject="owner", auth_provider="oidc", db_user_id=1, active_groups=())
    catalog = runtime_catalog.build_curator_route_catalog(db, curator)
    slot = f"agent:{head.agent_key}"
    source = next(t for t in catalog.targets if t.target.id == head.agent_key).source_execution_receipts[slot]
    suite = BenchmarkSuite.model_validate({
        "schema_version": 2, "suite_id": "source-revision-integration", "cases": [{
            "case_id": "paper", "target": {"kind": "agent", "id": head.agent_key},
            "input": {"resolver": "fixture", "reference": "paper", "version": "1", "digest": "sha256:" + "d" * 64},
            "user_query": "Extract the requested evidence",
        }], "configurations": [{"configuration_id": "different-model", "routes": {
            slot: {"provider": "openai", "model": "gpt-5.6-terra", "reasoning_effort": "low"},
        }}],
    })
    limits = dict(max_cases=1, max_configurations=1, max_repetitions=1, max_cells=1)
    plan = resolve_suite(suite, catalog, **limits)
    head.instructions = "Later mutable head instructions"
    head.model_id = "gpt-5.6-terra"
    second = append_execution_revision(db, head, capture_execution_snapshot(db, head, saved.output_contract),
                                       user_id=1, expected_revision_id=first.id)
    changed_catalog = runtime_catalog.build_curator_route_catalog(db, curator)
    with pytest.raises(BenchmarkLifecycleFailure) as stale:
        authoritative_plan(suite_value=suite.model_dump(mode="json"), submitted_plan=plan, catalog=changed_catalog)
    assert stale.value.code == "plan_drift"

    built_agents = []

    async def stream(**kwargs):
        assert "inline_chat_persistence" not in kwargs
        built_agents.append(kwargs["agent"])
        yield {"type": "STRUCTURED_RESULT", "data": {"result": {"records": []}}}
        yield {"type": "RUN_FINISHED", "data": {"response": "done"}}

    monkeypatch.setattr(runtime, "run_agent_streamed", stream)
    runtime_input = {"user_id": "owner", "db_user_id": 1, "active_groups": [],
                     "messages": [{"role": "user", "content": "Extract"}]}
    cell = plan.cells[0]
    for run_id in ("initial", "explicit-rerun"):
        await runtime.execute_resolved_agent_cell(cell, runtime_input, run_id)
    for agent in built_agents:
        assert saved.instructions in agent.instructions
        assert head.instructions not in agent.instructions
        assert agent.execution_revision_id == str(first.id)
        assert agent.execution_receipt == source.model_dump(mode="json")
        assert agent.execution_snapshot_fingerprint == saved.fingerprint()
        assert agent.model == agent.benchmark_requested_model == "gpt-5.6-terra"
        assert agent.benchmark_requested_provider == "openai"
        assert agent.benchmark_route_slot == slot
        assert agent.benchmark_reasoning_effort == "low"
    assert head.execution_revision_id == second.id
    assert first.snapshot == saved.model_dump(mode="json")
    if with_tool:
        policy.allow_execute = False
        db.flush()
        with pytest.raises(ValueError, match="no longer available"):
            await runtime.execute_resolved_agent_cell(cell, runtime_input, "tool-denied")
        policy.allow_execute = True
        db.flush()
    with pytest.raises(ValueError, match="cannot be overridden"):
        catalog_service.get_agent_by_id(head.agent_key, db_user_id=1, authenticated_groups=[],
                                       execution_revision_id=str(first.id), model_id_override="gpt-5.6-terra")
    with pytest.raises(ExecutionRevisionNotFoundError):
        await runtime.execute_resolved_agent_cell(cell, {**runtime_input, "db_user_id": 3}, "denied")
    with pytest.raises(ValueError, match="frozen source"):
        await runtime.execute_resolved_agent_cell(cell.model_copy(update={"source_execution_receipts": {}}),
                                                 runtime_input, "missing-source")
    assert len(built_agents) == 2


def test_saved_flow_capture_preserves_real_revision_and_rechecks_access(request, monkeypatch):
    from copy import deepcopy
    from sqlalchemy import text
    from fastapi import HTTPException
    from src.lib.agent_studio.execution_revision_service import append_execution_revision, current_execution_receipt
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.benchmarks import runtime
    from src.lib.benchmarks.flow_capture import capture_saved_flow
    from src.lib.benchmarks.saved_flows import flow_summary
    from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
    from src.models.sql import CurationFlow, database
    from src.schemas.agent_execution_revision import AgentOutputContract
    from src.schemas.flows import FlowDefinition
    from tests.unit.lib.flows.test_execution_revisions import flow
    from tests.unit.lib.benchmarks.test_runtime import _resolved_cell

    db, agent_id, _, _ = request.getfixturevalue("execution_db")
    db.execute(text("CREATE TABLE project_members (project_id uuid, user_id integer)"))
    CurationFlow.__table__.create(db.connection())
    head = db.get(Agent, agent_id)
    head.model_id = "gpt-5.6-sol"
    saved = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    first = append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)
    receipt = current_execution_receipt(db, head.agent_key, 1, active_group_ids=[])
    definition = flow(None).model_dump(mode="json")
    definition["nodes"][1]["data"].update(
        agent_id=head.agent_key, agent_revision_id=str(receipt.agent_revision_id),
        execution_receipt=receipt.model_dump(mode="json"),
    )
    source = CurationFlow(user_id=1, name="Original benchmark flow", description="Original task",
                          flow_definition=FlowDefinition.model_validate(definition).model_dump(mode="json"))
    db.add(source)
    db.flush()
    monkeypatch.setattr(database, "SessionLocal", lambda: nullcontext(db))
    curator = BenchmarkCuratorContext(subject="owner", auth_provider="oidc", db_user_id=1, active_groups=())
    revision = flow_summary(source).revision
    frozen = capture_saved_flow(db, curator, source.id, expected_revision=revision)
    assert frozen.definition["nodes"][1]["data"]["execution_receipt"]["agent_revision_id"] == str(first.id)
    original = frozen.model_dump(mode="json")
    source.name = "Changed flow"
    changed = deepcopy(source.flow_definition)
    changed["nodes"][0]["data"]["task_instructions"] = "Changed task"
    source.flow_definition = changed
    head.instructions = "New prompt"
    append_execution_revision(db, head, capture_execution_snapshot(db, head, saved.output_contract),
                              user_id=1, expected_revision_id=first.id)
    db.flush()
    with pytest.raises(ValueError, match="changed"):
        capture_saved_flow(db, curator, source.id, expected_revision=revision)
    cell = _resolved_cell("flow", str(source.id), {}).model_copy(update={"flow_snapshot": frozen})
    restored = runtime._flow_from_frozen_cell(cell, {"db_user_id": 1})
    assert restored.name == "Original benchmark flow"
    assert restored.flow_definition == original["definition"]
    with pytest.raises(HTTPException) as denied:
        runtime._flow_from_frozen_cell(cell, {"db_user_id": 3})
    assert denied.value.status_code == 403
    source.is_active = False
    db.flush()
    with pytest.raises(HTTPException) as archived:
        runtime._flow_from_frozen_cell(cell, {"db_user_id": 1})
    assert archived.value.status_code == 404
    assert frozen.model_dump(mode="json") == original
