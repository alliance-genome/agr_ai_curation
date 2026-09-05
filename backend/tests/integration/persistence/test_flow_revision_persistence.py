"""Real PostgreSQL migration and normalized references for mutable flow nodes."""

from copy import deepcopy
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError

from src.lib.agent_studio.execution_revision_service import append_execution_revision, current_execution_receipt
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.models.sql.agent import Agent
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.curation_flow_agent_revision import CurationFlowAgentRevision
from src.schemas.agent_execution_revision import AgentOutputContract
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


def migrate(db):
    path = Path(__file__).resolve().parents[3] / "alembic/versions/i6d7e8f9a0b1_pin_custom_flow_revisions.py"
    spec = importlib.util.spec_from_file_location("flow_pin_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db.connection())):
        module.upgrade()


def add_revision(db, agent, contract):
    snapshot = capture_execution_snapshot(db, agent, contract)
    return append_execution_revision(db, agent, snapshot, user_id=1,
                                     expected_revision_id=agent.execution_revision_id)


def legacy_node(key, index=0):
    return {"id": f"node_{index}", "type": "agent", "position": {"x": 0, "y": 0},
            "data": {"agent_id": key, "agent_display_name": key, "prompt_version": 999,
                     "output_key": f"result_{index}"}}


@pytest.fixture
def flow_db(execution_db):  # noqa: F811 - pytest injects the imported shared fixture
    db, agent_id, _, profile = execution_db
    CurationFlow.__table__.create(db.connection())
    agent = db.get(Agent, agent_id)
    agent.tool_ids = []
    agent.group_rules_enabled = False
    first = add_revision(db, agent, AgentOutputContract(output_state="none"))
    return db, agent, first, profile


def test_migration_pins_four_states_and_preserves_unresolved_and_system_nodes(flow_db, monkeypatch):
    from pydantic import create_model
    from src.lib.prompts import assembly

    # This database contract test does not depend on an installed domain package.
    output_type = create_model("FixtureEnvelope", records=(list[str], ...))
    monkeypatch.setattr(assembly, "resolve_output_schema", lambda _key: output_type)
    db, first_agent, _, profile = flow_db
    contracts = [
        AgentOutputContract(output_state="none"),
        AgentOutputContract(output_state="structured_extraction", output_mode="domain", output_schema_key="FixtureEnvelope"),
        AgentOutputContract(output_state="structured_extraction", output_mode="unprofiled_generic"),
        AgentOutputContract(output_state="structured_extraction", output_mode="profile_bound_generic", generic_profile_ref={
            "profile_id": profile.profile_id, "profile_revision_id": profile.id,
            "revision": profile.revision, "fingerprint": profile.fingerprint,
        }),
    ]
    agents = [first_agent]
    for index, contract in enumerate(contracts[1:], 1):
        agent = Agent(id=uuid4(), agent_key=f"ca_mode_{index}", user_id=1, name="Fixture",
                      instructions="Saved", model_id="test-model", model_temperature=0.0,
                      visibility="private", tool_ids=[], group_rules_enabled=False)
        db.add(agent)
        db.flush()
        add_revision(db, agent, contract)
        agents.append(agent)
    nodes = [legacy_node(agent.agent_key, i) for i, agent in enumerate(agents)]
    nodes += [legacy_node("ca_missing", 4), legacy_node("pdf_extraction", 5)]
    flow = CurationFlow(user_id=1, name="Legacy", flow_definition={"nodes": nodes, "edges": []})
    db.add(flow)
    db.flush()
    migrate(db)
    db.refresh(flow)
    refs = db.execute(sa.select(CurationFlowAgentRevision).where(CurationFlowAgentRevision.flow_id == flow.id)).scalars().all()
    assert len(refs) == 4
    for index, agent in enumerate(agents):
        data = flow.flow_definition["nodes"][index]["data"]
        assert data["agent_revision_id"] == str(agent.execution_revision_id)
        assert data["execution_receipt"] == current_execution_receipt(db, agent.agent_key, 1, active_group_ids=[]).model_dump(mode="json")
        assert data["execution_receipt"]["output_contract"] == contracts[index].model_dump(mode="json")
        assert data["prompt_version"] == 999  # Audit only; never used as a revision.
    assert flow.flow_definition["nodes"][4:] == nodes[4:]


def pinned_flow(db, agent):
    receipt = current_execution_receipt(db, agent.agent_key, 1, active_group_ids=[])
    node = legacy_node(agent.agent_key)
    node["data"].update(agent_revision_id=str(receipt.agent_revision_id),
                       execution_receipt=receipt.model_dump(mode="json"))
    flow = CurationFlow(user_id=1, name="Pinned", flow_definition={"nodes": [node], "edges": []})
    db.add(flow)
    db.flush()
    return flow, receipt


def test_head_change_does_not_retarget_and_explicit_edit_updates_reference(flow_db):
    db, agent, first, _ = flow_db
    migrate(db)
    flow, original = pinned_flow(db, agent)
    agent.instructions = "Later instructions"
    second = add_revision(db, agent, AgentOutputContract(output_state="none"))
    db.refresh(flow)
    assert flow.flow_definition["nodes"][0]["data"]["execution_receipt"] == original.model_dump(mode="json")
    assert db.get(CurationFlowAgentRevision, (flow.id, "node_0")).agent_revision_id == first.id
    newer = current_execution_receipt(db, agent.agent_key, 1, active_group_ids=[])
    edited = deepcopy(flow.flow_definition)
    edited["nodes"][0]["data"].update(agent_revision_id=str(second.id), execution_receipt=newer.model_dump(mode="json"))
    flow.flow_definition = edited
    db.flush()
    db.expire_all()
    assert db.get(CurationFlowAgentRevision, (flow.id, "node_0")).agent_revision_id == second.id
    assert db.scalar(sa.select(sa.func.count()).select_from(CurationFlow)) == 1


@pytest.mark.parametrize("change", ["fingerprint", "agent", "revision", "profile"])
def test_database_rejects_receipt_tampering(flow_db, change):
    db, agent, _, _ = flow_db
    migrate(db)
    flow, _ = pinned_flow(db, agent)
    edited = deepcopy(flow.flow_definition)
    data = edited["nodes"][0]["data"]
    if change == "fingerprint":
        data["execution_receipt"]["fingerprint"] = "sha256:" + "c" * 64
    elif change == "agent":
        data["agent_id"] = "ca_other"
    elif change == "revision":
        data["agent_revision_id"] = str(uuid4())
    else:
        data["execution_receipt"]["output_contract"]["output_mode"] = "unprofiled_generic"
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(sa.update(CurationFlow).where(CurationFlow.id == flow.id).values(flow_definition=edited))


def test_normalized_reference_cannot_be_removed_or_dangle(flow_db):
    db, agent, _, _ = flow_db
    migrate(db)
    flow, _ = pinned_flow(db, agent)
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(sa.delete(CurationFlowAgentRevision).where(CurationFlowAgentRevision.flow_id == flow.id))
        db.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    with pytest.raises(DBAPIError), db.begin_nested():
        db.execute(sa.update(CurationFlowAgentRevision).where(CurationFlowAgentRevision.flow_id == flow.id)
                   .values(agent_revision_id=uuid4()))
    assert db.get(CurationFlowAgentRevision, (flow.id, "node_0")) is not None
