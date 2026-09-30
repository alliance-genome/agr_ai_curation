"""Moving saved custom agents and their pinned flow steps off GPT-6 Sol."""

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.api import flows as flows_api
from src.lib.agent_studio import custom_agent_service as service
from src.lib.agent_studio import retired_model_conversion as conversion
from src.lib.agent_studio.execution_revision_service import append_execution_revision
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.lib.flows.execution_revisions import flow_execution_revision_findings
from src.models.sql import database
from src.models.sql.agent_execution_revision import AgentExecutionRevision
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.custom_agent import CustomAgentVersion
from src.schemas.agent_execution_revision import (
    AgentExecutionReceipt,
    AgentExecutionSnapshot,
    AgentOutputContract,
)
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_flow_revision_persistence import migrate
from .test_generic_profile_persistence import profile_db  # noqa: F401

NO_OUTPUT = AgentOutputContract(output_state="none")
RETIRED, TARGET = "gpt-6-sol", "gpt-6.1-sol"


@pytest.fixture
def world(execution_db, monkeypatch):  # noqa: F811
    db, *_ = execution_db
    CustomAgentVersion.__table__.create(db.connection())
    CurationFlow.__table__.create(db.connection())
    migrate(db)  # installs the flow pin-sync triggers
    db.execute(text("CREATE TABLE project_members (project_id uuid, user_id integer)"))
    monkeypatch.setattr(database, "SessionLocal", lambda: Session(
        bind=db.connection(), join_transaction_mode="create_savepoint"))
    # The real flow validator runs; only the agent-policy lookup is stubbed, as in
    # tests/integration/persistence/test_flexible_agent_migration.py.
    monkeypatch.setattr(flows_api, "_flow_agent_policy_entry",
                        lambda *_, **__: {"category": "Extraction", "supervisor": {}})
    return db


def make_agent(db, name, user_id=1):
    return service.create_custom_agent(db, user_id, name, model_id=TARGET,
                                       custom_prompt=f"{name} instructions", include_group_rules=False)


def save_on_retired(db, agent, instructions=None, reasoning="medium", model=RETIRED):
    """A saved version from before the retirement (the catalog no longer offers the model)."""
    if instructions is not None:
        agent.instructions = instructions
    snapshot = capture_execution_snapshot(db, agent, NO_OUTPUT).model_copy(
        update={"model_id": model, "model_reasoning": reasoning})
    row = append_execution_revision(db, agent, snapshot, user_id=agent.user_id,
                                    expected_revision_id=agent.execution_revision_id)
    # Alembic s6b7c8d9e0f1 has already moved the editable row.
    agent.model_id = TARGET
    db.flush()
    return row


def receipt(agent, row):
    return AgentExecutionReceipt(
        agent_id=agent.id, agent_key=agent.agent_key, agent_revision_id=row.id,
        revision=row.revision, fingerprint=row.fingerprint,
        output_contract=AgentExecutionSnapshot.model_validate(row.snapshot).output_contract,
    ).model_dump(mode="json")


def make_flow(db, pins, user_id=1, name="Flow", is_active=True):
    task = {"id": "task", "type": "task_input", "position": {"x": 0, "y": 0},
            "data": {"agent_id": "task_input", "agent_display_name": "Task",
                     "output_key": "task", "task_instructions": "Extract"}}
    nodes, edges, previous = [task], [], "task"
    for index, (agent, row) in enumerate(pins):
        node_id = f"node_{index}"
        nodes.append({"id": node_id, "type": "agent", "position": {"x": 100, "y": 100 * index},
                      "data": {"agent_id": agent.agent_key, "agent_display_name": agent.name,
                               "output_key": f"result_{index}", "agent_revision_id": str(row.id),
                               "execution_receipt": receipt(agent, row)}})
        edges.append({"id": f"e{index}", "source": previous, "target": node_id})
        previous = node_id
    flow = CurationFlow(user_id=user_id, name=name, is_active=is_active, flow_definition={
        "nodes": nodes, "entry_node_id": "task", "edges": edges})
    db.add(flow)
    db.flush()
    return flow


def pin(flow, node_id="node_0"):
    return next(node for node in flow.flow_definition["nodes"] if node["id"] == node_id)["data"]


def revision(db, revision_id):
    return db.get(AgentExecutionRevision, UUID(str(revision_id)))


def revision_count(db, agent):
    return db.scalar(select(func.count()).select_from(AgentExecutionRevision)
                     .where(AgentExecutionRevision.agent_id == agent.id))


FULL_MAP = {"minimal": "low", "disabled": "medium", "xhigh": "high"}


def run(db, owner_groups=None, reasoning_map=None):
    return conversion.convert(db, retired_model_ids=frozenset({RETIRED, "gpt-5.6-terra"}),
                              target_model_id=TARGET,
                              reasoning_map=FULL_MAP if reasoning_map is None else reasoning_map,
                              owner_groups={1: [], 2: []} if owner_groups is None else owner_groups,
                              notes="test conversion")


def same_except_model(old, new):
    changed = {key for key in set(old) | set(new) if old.get(key) != new.get(key)}
    return changed <= {"model_id", "model_reasoning"}


def test_the_head_and_its_flow_step_move_to_the_new_model(world):
    db = world
    agent = make_agent(db, "Finder")
    old_head = save_on_retired(db, agent, reasoning="xhigh")
    flow = make_flow(db, [(agent, old_head)])
    assert flow_execution_revision_findings(db, flow.flow_definition, user_id=1,
                                            active_group_ids=[])[0]["code"] == "unavailable_model"

    report = run(db)

    new_head = revision(db, agent.execution_revision_id)
    assert new_head.id != old_head.id and new_head.revision == old_head.revision + 1
    assert new_head.snapshot["model_id"] == TARGET and new_head.snapshot["model_reasoning"] == "high"
    assert same_except_model(old_head.snapshot, new_head.snapshot)
    assert new_head.creator_id == agent.user_id and new_head.notes == "test conversion"
    assert old_head.snapshot["model_id"] == RETIRED  # immutable history is untouched
    assert (agent.model_id, agent.model_reasoning) == (TARGET, "high")
    db.refresh(flow)
    assert pin(flow)["agent_revision_id"] == str(new_head.id)
    assert pin(flow)["execution_receipt"]["agent_revision_id"] == str(new_head.id)
    assert flow_execution_revision_findings(db, flow.flow_definition, user_id=1, active_group_ids=[]) == []
    assert report["before"]["active_agent_heads"] == 1 and report["before"]["active_flow_pins"] == 1
    assert report["after"] == {"active_agent_heads": 0, "archived_agent_heads": 0,
                               "active_flow_pins": 0, "deleted_flow_pins": 0}
    [owner] = report["owners"]
    assert owner["user_id"] == 1 and owner["refused_agents"] == owner["refused_flows"] == []
    assert [item["agent_key"] for item in owner["agents"]] == [agent.agent_key]
    assert owner["flows"][0]["steps"] == [{"node_id": "node_0", "agent_key": agent.agent_key,
                                           "from_revision_id": str(old_head.id),
                                           "to_revision_id": str(new_head.id)}]
    assert report["counts"]["revisions_appended"] == 1 and report["counts"]["steps_repinned"] == 1


def test_a_step_pinned_to_an_older_version_keeps_that_version_on_the_new_model(world):
    db = world
    agent = make_agent(db, "Finder")
    older = save_on_retired(db, agent, instructions="Older instructions")
    head = save_on_retired(db, agent, instructions="Current instructions", reasoning="high")
    older_flow = make_flow(db, [(agent, older)], name="Older")
    head_flow = make_flow(db, [(agent, head)], name="Current")

    run(db)

    db.refresh(older_flow)
    db.refresh(head_flow)
    older_copy = revision(db, pin(older_flow)["agent_revision_id"])
    head_copy = revision(db, pin(head_flow)["agent_revision_id"])
    assert older_copy.snapshot["instructions"] == "Older instructions"
    assert older_copy.snapshot["model_id"] == TARGET and same_except_model(older.snapshot, older_copy.snapshot)
    assert head_copy.snapshot["instructions"] == "Current instructions"
    assert agent.execution_revision_id == head_copy.id  # the head keeps its own configuration
    assert head_copy.revision > older_copy.revision


def test_a_current_head_is_kept_when_only_an_older_pin_is_on_the_retired_model(world):
    db = world
    agent = make_agent(db, "Finder")
    older = save_on_retired(db, agent, instructions="Older instructions")
    service.update_custom_agent(db, agent, expected_revision_id=agent.execution_revision_id,
                                custom_prompt="Re-saved on the new model")
    current = revision(db, agent.execution_revision_id)
    flow = make_flow(db, [(agent, older)])

    run(db)

    db.refresh(flow)
    assert revision(db, pin(flow)["agent_revision_id"]).snapshot["instructions"] == "Older instructions"
    head = revision(db, agent.execution_revision_id)
    assert head.id != current.id and head.fingerprint == current.fingerprint


def test_a_second_run_changes_nothing(world):
    db = world
    agent = make_agent(db, "Finder")
    older = save_on_retired(db, agent, instructions="Older")
    head = save_on_retired(db, agent)
    make_flow(db, [(agent, older), (agent, head)])
    run(db)
    count, head_id = revision_count(db, agent), agent.execution_revision_id

    again = run(db)

    assert again["owners"] == [] and again["counts"]["revisions_appended"] == 0
    assert revision_count(db, agent) == count and agent.execution_revision_id == head_id


def test_an_archived_agent_is_converted_and_a_deleted_flow_is_left_alone(world):
    db = world
    agent = make_agent(db, "Archived")
    old_head = save_on_retired(db, agent)
    agent.is_active = False
    deleted = make_flow(db, [(agent, old_head)], is_active=False)
    before = deepcopy(deleted.flow_definition)

    report = run(db)

    assert revision(db, agent.execution_revision_id).snapshot["model_id"] == TARGET
    assert report["owners"][0]["agents"][0]["is_active"] is False
    db.refresh(deleted)
    assert deleted.flow_definition == before
    assert report["after"]["deleted_flow_pins"] == 1 and report["after"]["archived_agent_heads"] == 0


def test_a_version_with_an_unmapped_reasoning_level_is_refused_and_its_flow_left_unchanged(world):
    db = world
    good, bad = make_agent(db, "Good"), make_agent(db, "Bad")
    good_head, bad_head = save_on_retired(db, good), save_on_retired(db, bad, reasoning="minimal")
    flow = make_flow(db, [(good, good_head), (bad, bad_head)])
    before = deepcopy(flow.flow_definition)

    report = run(db, reasoning_map={"xhigh": "high"})

    [owner] = report["owners"]
    assert [item["agent_key"] for item in owner["refused_agents"]] == [bad.agent_key]
    assert "'minimal' is not offered" in owner["refused_agents"][0]["reason"]
    assert bad.execution_revision_id == bad_head.id
    assert revision(db, good.execution_revision_id).snapshot["model_id"] == TARGET
    db.refresh(flow)
    assert flow.flow_definition == before
    assert owner["refused_flows"][0]["flow_id"] == str(flow.id)
    assert report["counts"]["flows_refused"] == 1
    assert report["after"]["active_agent_heads"] == 1 and report["after"]["active_flow_pins"] == 2

    # The good agent's pin is still retired in the refused flow: a re-run finds its
    # existing copy and lists the agent without appending or counting it again.
    again = run(db, reasoning_map={"xhigh": "high"})
    assert again["counts"]["agents_converted"] == again["counts"]["revisions_appended"] == 0
    assert good.agent_key in [item["agent_key"] for item in again["owners"][0]["agents"]]


@pytest.mark.parametrize("model,saved,expected", [
    ("gpt-5.6-terra", "disabled", "medium"), ("gpt-5.6-terra", "minimal", "low"),
    (RETIRED, "minimal", "low"), (RETIRED, "high", "high"), (RETIRED, None, None),
])
def test_every_retired_model_moves_with_the_reviewed_reasoning(world, model, saved, expected):
    db = world
    agent = make_agent(db, "Finder")
    old_head = save_on_retired(db, agent, reasoning=saved, model=model)
    flow = make_flow(db, [(agent, old_head)])

    report = run(db)

    head = revision(db, agent.execution_revision_id)
    assert (head.snapshot["model_id"], head.snapshot["model_reasoning"]) == (TARGET, expected)
    assert same_except_model(old_head.snapshot, head.snapshot)
    db.refresh(flow)
    assert pin(flow)["agent_revision_id"] == str(head.id)
    assert report["from_models"] == ["gpt-5.6-terra", RETIRED]
    assert report["after"]["active_agent_heads"] == report["after"]["active_flow_pins"] == 0


def test_a_run_is_refused_while_a_retired_model_is_still_offered(world):
    with pytest.raises(conversion.ConversionRefused, match=r"still in the model catalog: \['gpt-6.1-sol'\]"):
        conversion.convert(world, retired_model_ids=frozenset({TARGET}), target_model_id=TARGET,
                           reasoning_map={}, owner_groups={}, notes="n")


def test_a_flow_owner_without_reviewed_groups_stops_the_run_before_any_write(world):
    db = world
    agent = make_agent(db, "Finder")
    head = save_on_retired(db, agent)
    make_flow(db, [(agent, head)], user_id=1)
    count = revision_count(db, agent)

    with pytest.raises(conversion.ConversionRefused, match=r"flow owner\(s\) \[1\]"):
        run(db, owner_groups={2: []})
    assert revision_count(db, agent) == count


# The CLI, run through main() against this isolated schema.

def _script():
    path = Path(__file__).resolve().parents[4] / "scripts/one_off/convert_gpt6_sol_agents_0100.py"
    spec = importlib.util.spec_from_file_location("convert_gpt6_sol_agents_0100", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_makes_the_backend_and_its_runtime_helpers_importable(monkeypatch):
    backend = Path(__file__).resolve().parents[3]
    wanted = {str(backend), str(backend / "src")}
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path
                                      if str(Path(entry or ".").resolve()) not in wanted])
    _script()
    assert wanted <= set(sys.path)


def _groups_file(tmp_path, groups):
    path = tmp_path / "owner-groups.json"
    path.write_text(json.dumps(groups))
    return str(path)


def _head(db, agent):
    db.expire_all()
    return db.get(type(agent), agent.id).execution_revision_id


def test_cli_dry_run_reports_and_rolls_back_then_apply_commits(world, tmp_path, monkeypatch, capsys):
    db = world
    agent = make_agent(db, "Finder")
    old_head = save_on_retired(db, agent)
    make_flow(db, [(agent, old_head)])
    args = ["--owner-groups", _groups_file(tmp_path, {"1": []})]

    assert _script().main(args) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["committed"] is False and dry["counts"]["steps_repinned"] == 1
    assert _head(db, agent) == old_head.id

    assert _script().main([*args, "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["committed"] is True and applied["after"]["active_flow_pins"] == 0
    assert _head(db, agent) != old_head.id


def test_cli_exits_3_when_something_is_refused(world, tmp_path, capsys):
    db = world
    agent = make_agent(db, "Finder")
    save_on_retired(db, agent, reasoning="minimal")
    script = _script()
    script.REASONING_MAP = {}
    assert script.main(["--owner-groups", _groups_file(tmp_path, {}), "--apply"]) == 3
    assert json.loads(capsys.readouterr().out)["counts"]["agents_refused"] == 1


def test_cli_refuses_unknown_groups_and_missing_owners(world, tmp_path, capsys):
    db = world
    agent = make_agent(db, "Finder")
    make_flow(db, [(agent, save_on_retired(db, agent))])
    assert _script().main(["--owner-groups", _groups_file(tmp_path, {"1": ["NOT_A_GROUP"]})]) == 1
    assert "NOT_A_GROUP" in capsys.readouterr().err
    assert _script().main(["--owner-groups", _groups_file(tmp_path, {})]) == 1
    assert "flow owner(s) [1]" in capsys.readouterr().err
