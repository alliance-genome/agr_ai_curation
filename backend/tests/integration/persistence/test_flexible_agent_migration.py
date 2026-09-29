"""Converting a Flexible agent from a reviewed plan, and rolling the flow pin back."""

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api import flows as flows_api
from src.lib.agent_studio import custom_agent_service as service
from src.lib.agent_studio import flexible_migration as migration
from src.lib.agent_studio.execution_revision_service import (
    append_execution_revision,
    current_execution_receipt,
    get_execution_revision,
)
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.models.sql import database
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.custom_agent import CustomAgentVersion
from src.schemas.agent_execution_revision import AgentOutputContract
from .test_agent_execution_revision_persistence import builder_policies, execution_db  # noqa: F401
from .test_flow_revision_persistence import migrate
from .test_generic_profile_persistence import profile_db  # noqa: F401

FLEXIBLE = AgentOutputContract(output_state="structured_extraction", output_mode="unprofiled_generic")


@pytest.fixture
def converted_world(execution_db, builder_policies, monkeypatch):  # noqa: F811
    db, *_ = execution_db
    CustomAgentVersion.__table__.create(db.connection())
    CurationFlow.__table__.create(db.connection())
    migrate(db)  # installs the flow pin-sync triggers
    # Saving as the owner with their active groups reads their project memberships.
    db.execute(text("CREATE TABLE project_members (project_id uuid, user_id integer)"))
    # Custom-validator discovery opens its own session; keep it on this isolated schema.
    monkeypatch.setattr(database, "SessionLocal", lambda: Session(
        bind=db.connection(), join_transaction_mode="create_savepoint"))
    # The real flow validator runs; only the agent-policy lookup is stubbed, as in
    # tests/integration/persistence/test_flow_sharing.py.
    monkeypatch.setattr(flows_api, "_flow_agent_policy_entry",
                        lambda *_, **__: {"category": "Extraction", "supervisor": {}})
    monkeypatch.setattr(migration, "_result_payloads", lambda db, key: [])
    agent = service.create_custom_agent(db, 1, "Widget finder", model_id="gpt-6-sol",
                                        custom_prompt="Find widgets", include_group_rules=False)
    snapshot = capture_execution_snapshot(db, agent, FLEXIBLE)
    append_execution_revision(db, agent, snapshot, user_id=1,
                              expected_revision_id=agent.execution_revision_id)
    receipt = current_execution_receipt(db, agent.agent_key, 1, active_group_ids=[])
    task = {"id": "task", "type": "task_input", "position": {"x": 0, "y": 0},
            "data": {"agent_id": "task_input", "agent_display_name": "Task",
                     "output_key": "task", "task_instructions": "Extract"}}
    step = {"id": "node_0", "type": "agent", "position": {"x": 100, "y": 100},
            "data": {"agent_id": agent.agent_key, "agent_display_name": "Finder",
                     "output_key": "result_0", "agent_revision_id": str(receipt.agent_revision_id),
                     "execution_receipt": receipt.model_dump(mode="json")}}
    flow = CurationFlow(user_id=1, name="Widget flow", flow_definition={
        "nodes": [task, step], "entry_node_id": "task",
        "edges": [{"id": "e0", "source": "task", "target": "node_0"}]})
    db.add(flow)
    db.flush()
    plan = migration.ConversionPlan.model_validate({
        "schema_version": migration.PLAN_SCHEMA, "agent_id": str(agent.id),
        "expected_head_revision_id": str(receipt.agent_revision_id), "active_group_ids": [],
        "profile": {"name": "Widgets", "semantic_class": "widget", "fields": [
            {"key": "widget_type", "source_labels": ["widget_types"],
             "value_schema": {"kind": "string"}}]},
        "custom_prompt": "Find widgets and record each widget type.",
        "key_merges": {"widget_types": "widget_type"}, "other_object_kinds": {"generic_claim": "dropped"},
        "steps": [{"flow_id": str(flow.id), "node_id": "node_0"}],
        "owner_review": "Reviewed with the agent's owner.",
    })
    return db, agent, flow, receipt, plan


def pinned(flow):
    return next(node for node in flow.flow_definition["nodes"] if node["id"] == "node_0")["data"]


def mode(db, agent, revision_id):
    _, saved = get_execution_revision(db, agent.id, revision_id, 1, active_group_ids=[])
    return saved.output_contract.output_mode


def test_inventory_is_read_only_and_finds_the_step(converted_world):
    db, agent, flow, receipt, _ = converted_world
    report = migration.inventory(db)
    [found] = report["agents"]
    assert found["agent_key"] == agent.agent_key
    assert found["head_revision_id"] == str(receipt.agent_revision_id)
    assert found["steps"] == [{"flow_id": str(flow.id), "node_id": "node_0"}]
    assert not (db.new or db.dirty or db.deleted)


def test_apply_converts_and_repins_then_rollback_repins_the_old_revision(converted_world):
    db, agent, flow, receipt, plan = converted_world
    result = migration.apply(db, plan)
    assert result.old_revision_id == receipt.agent_revision_id
    assert agent.execution_revision_id == result.new_revision_id != result.old_revision_id
    assert mode(db, agent, result.new_revision_id) == "profile_bound_generic"
    db.refresh(flow)
    assert pinned(flow)["agent_revision_id"] == str(result.new_revision_id)
    migration.rollback(db, plan, result)
    db.refresh(flow)
    assert pinned(flow)["agent_revision_id"] == str(result.old_revision_id)
    assert pinned(flow)["execution_receipt"]["output_contract"]["output_mode"] == "unprofiled_generic"
    assert agent.execution_revision_id == result.new_revision_id


def test_apply_refuses_a_plan_written_for_an_older_head(converted_world):
    db, agent, flow, _, plan = converted_world
    service.update_custom_agent(db, agent, expected_revision_id=agent.execution_revision_id,
                                model_temperature=0.3)
    with pytest.raises(ValueError, match="changed since the plan was written"):
        migration.apply(db, plan)
    assert mode(db, agent, agent.execution_revision_id) == "unprofiled_generic"


def test_rollback_after_an_owner_edit_leaves_the_head_and_refuses_a_changed_flow(converted_world):
    # Review Focus 5.
    db, agent, flow, _, plan = converted_world
    result = migration.apply(db, plan)
    service.update_custom_agent(db, agent, expected_revision_id=agent.execution_revision_id,
                                model_temperature=0.3)
    edited_head = agent.execution_revision_id
    migration.rollback(db, plan, result)
    db.refresh(flow)
    assert pinned(flow)["agent_revision_id"] == str(result.old_revision_id)
    assert agent.execution_revision_id == edited_head
    with pytest.raises(ValueError, match="no longer pins"):
        migration.rollback(db, plan, result)


def test_apply_refuses_a_step_in_a_flow_the_agent_owner_does_not_own(converted_world):
    # Flows are saved as their owner with the plan's groups, which are the agent owner's.
    db, agent, flow, receipt, plan = converted_world
    flow.user_id = 2
    db.flush()
    report = migration.inventory(db)
    [found] = report["agents"]
    assert found["steps"] == []
    assert found["other_owner_steps"] == [{"flow_id": str(flow.id), "node_id": "node_0"}]
    with pytest.raises(ValueError, match="not owned by the agent's owner"):
        migration.apply(db, plan)
    assert agent.execution_revision_id == receipt.agent_revision_id
    assert pinned(flow)["agent_revision_id"] == str(receipt.agent_revision_id)


# The CLI, run through main() against this isolated schema.

def _cli():
    path = Path(__file__).resolve().parents[4] / "scripts/one_off/convert_flexible_agents_0100.py"
    spec = importlib.util.spec_from_file_location("convert_flexible_agents_0100", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def _sessions(db, monkeypatch, on_commit=None):
    class ScriptSession(Session):
        def commit(self):
            if on_commit is not None:
                on_commit()
            super().commit()

    monkeypatch.setattr(database, "SessionLocal", lambda: ScriptSession(
        bind=db.connection(), join_transaction_mode="create_savepoint"))


def _args(tmp_path, plan, command="apply"):
    plan_file = tmp_path / "agent.plan.json"
    plan_file.write_text(plan.model_dump_json(indent=2))
    digest = hashlib.sha256(plan_file.read_bytes()).hexdigest()
    return [command, "--plan", str(plan_file), "--plan-sha256", digest,
            "--result", str(tmp_path / "agent.result.json"), "--commit"]


def _head(db, agent):
    db.expire_all()
    return db.get(type(agent), agent.id).execution_revision_id


def test_cli_refuses_to_overwrite_an_existing_result_file(converted_world, tmp_path, monkeypatch, capsys):
    db, agent, _, receipt, plan = converted_world
    _sessions(db, monkeypatch)
    args = _args(tmp_path, plan)
    (tmp_path / "agent.result.json").write_text("earlier apply")
    assert _cli()(args) == 1
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "agent.result.json").read_text() == "earlier apply"
    assert _head(db, agent) == receipt.agent_revision_id


def test_cli_writes_the_result_file_before_it_commits(converted_world, tmp_path, monkeypatch):
    db, agent, flow, receipt, plan = converted_world
    result_file = tmp_path / "agent.result.json"
    seen = []
    _sessions(db, monkeypatch, on_commit=lambda: seen.append(
        migration.ConversionResult.model_validate_json(result_file.read_text())))
    assert _cli()(_args(tmp_path, plan)) == 0
    [written] = seen
    assert written.old_revision_id == receipt.agent_revision_id
    assert _head(db, agent) == written.new_revision_id
    db.refresh(flow)
    assert pinned(flow)["agent_revision_id"] == str(written.new_revision_id)


def test_cli_keeps_the_result_file_when_the_commit_outcome_is_unknown(
        converted_world, tmp_path, monkeypatch, capsys):
    db, _, _, _, plan = converted_world

    def lost():
        raise ConnectionError("server closed the connection")

    _sessions(db, monkeypatch, on_commit=lost)
    assert _cli()(_args(tmp_path, plan)) == 2
    assert "commit outcome unknown; check the agent head before retrying" in capsys.readouterr().err
    migration.ConversionResult.model_validate_json((tmp_path / "agent.result.json").read_text())


def test_cli_a_refused_second_repin_leaves_the_agent_unconverted(
        converted_world, tmp_path, monkeypatch, capsys):
    db, agent, flow, receipt, plan = converted_world
    second = CurationFlow(user_id=1, name="Second widget flow",
                          flow_definition=deepcopy(flow.flow_definition))
    db.add(second)
    db.flush()
    plan = plan.model_copy(update={"steps": [
        *plan.steps, migration.StepPin(flow_id=second.id, node_id="node_0")]})
    real_save = migration.save_flow_definition
    calls = []

    def save(db_, flow_, definition, *, active_group_ids):
        calls.append(flow_.id)
        if len(calls) == 2:
            raise HTTPException(status_code=422, detail={"valid": False, "findings": [
                {"code": "example_finding", "message": "Example finding"}]})
        real_save(db_, flow_, definition, active_group_ids=active_group_ids)

    monkeypatch.setattr(migration, "save_flow_definition", save)
    _sessions(db, monkeypatch)
    assert _cli()(_args(tmp_path, plan)) == 1
    err = capsys.readouterr().err
    assert "example_finding" in err and "Traceback" not in err
    assert calls == [flow.id, second.id]
    assert not (tmp_path / "agent.result.json").exists()
    assert _head(db, agent) == receipt.agent_revision_id
    db.refresh(flow)
    assert pinned(flow)["agent_revision_id"] == str(receipt.agent_revision_id)
