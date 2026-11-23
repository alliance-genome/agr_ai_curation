"""The standing model-move command: saved agents, flow pins and editable rows leave retired models.

These tests read the newest reviewed moves file under ``config/model_moves/`` and the live
model catalog, so the next monthly move needs no edits here.
"""

import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select, text

from src.lib.agent_studio import retired_model_conversion as conversion
from src.lib.agent_studio import runtime_validation
from src.lib.config.groups_loader import list_groups
from src.lib.config.models_loader import get_default_model
from src.lib.flows.execution_revisions import flow_execution_revision_findings
from src.models.sql import database
from src.models.sql.agent import Agent
from src.models.sql.agent_execution_revision import AgentExecutionRevision
from src.services import model_move_cli
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401
from .test_retired_model_conversion import (  # noqa: F401
    make_agent,
    make_flow,
    pin,
    revision,
    revision_count,
    same_except_model,
    save_on_retired,
    world,
)

MOVES_DIR = Path(__file__).resolve().parents[4] / "config" / "model_moves"


def newest_moves_file() -> Path:
    return sorted(MOVES_DIR.glob("*.yaml"))[-1]


@pytest.fixture
def moves():
    return conversion.load_moves(newest_moves_file())


@pytest.fixture
def db(world, monkeypatch):  # noqa: F811
    world.execute(text("ALTER TABLE users ADD COLUMN auth_sub varchar(255)"))
    world.execute(text("UPDATE users SET auth_sub = 'sub-' || user_id"))
    monkeypatch.setattr(runtime_validation, "SessionLocal", database.SessionLocal)
    monkeypatch.setattr(runtime_validation, "_load_expected_system_agent_keys", lambda: (set(), None))
    return world


def project_id(db):
    return db.scalar(text("SELECT id FROM projects LIMIT 1"))


def system_row(db, model_id):
    row = Agent(agent_key=f"system_{model_id}", name=f"System {model_id}", instructions="System",
                model_id=model_id, model_reasoning="medium", visibility="system", is_active=False)
    db.add(row)
    db.flush()
    return row


def run_moves(db, moves, groups=None):
    return conversion.run_moves(db, moves, owner_groups={1: [], 2: []} if groups is None else groups)


def test_the_moves_file_names_only_ids_the_catalog_has_retired_and_targets_it_offers(moves):
    assert moves, "the newest moves file lists no moves"
    conversion.check_moves(moves)  # refuses on any inconsistency


def test_every_saved_shape_on_each_retired_model_moves_in_one_run(db, moves):
    """Production shapes: active and archived private agents, a project agent, an agent
    whose editable row is retired but whose head is current, an agent with no saved
    version, a flow pinning an older version, a flow pinning agents on two retired
    models, and an inactive system row (startup's sync owns system rows)."""
    created = []
    for move in moves:
        for retired in move.from_ids:
            active = make_agent(db, f"Active {retired}")
            active_head = save_on_retired(db, active, model=retired)
            older_pinned = make_agent(db, f"Older {retired}")
            older = save_on_retired(db, older_pinned, instructions="Older", model=retired)
            save_on_retired(db, older_pinned, instructions="Current", model=retired)
            archived = make_agent(db, f"Archived {retired}", user_id=2)
            save_on_retired(db, archived, model=retired)
            archived.is_active = False
            shared = make_agent(db, f"Project {retired}", user_id=2)
            save_on_retired(db, shared, model=retired)
            shared.visibility, shared.project_id = "project", project_id(db)
            draft_only = make_agent(db, f"Draft {retired}")
            draft_only.model_id = retired  # head is current; only the editable row is retired
            no_version = make_agent(db, f"Unsaved {retired}")
            no_version.execution_revision_id, no_version.model_id = None, retired
            system = system_row(db, retired)
            make_flow(db, [(active, active_head)], name=f"Head {retired}")
            make_flow(db, [(older_pinned, older)], name=f"Older {retired}")
            created.append((move, retired, active, older_pinned, archived, shared, draft_only,
                            no_version, system))
    both = make_flow(db, [(row[2], revision(db, row[2].execution_revision_id)) for row in created],
                     name="Pins two retired models")
    db.flush()
    retired_ids = frozenset(retired for move in moves for retired in move.from_ids)
    assert conversion.verify(db, moves)["blocking"] > 0

    report = run_moves(db, moves)

    assert [owner["refused_agents"] + owner["refused_flows"] for owner in report["owners"]] == [[], []]
    assert report["editable_rows"]["refused"] == []
    for move, retired, active, older_pinned, archived, shared, draft_only, no_version, system in created:
        for agent in (active, older_pinned, archived, shared):
            head = revision(db, agent.execution_revision_id)
            assert head.snapshot["model_id"] == move.to_id and head.notes == move.note
            assert (agent.model_id, agent.model_reasoning) == (move.to_id, head.snapshot["model_reasoning"])
        assert draft_only.model_id == move.to_id and no_version.model_id == move.to_id
        assert no_version.execution_revision_id is None
        assert (system.model_id, system.is_active) == (retired, False)
    db.refresh(both)
    for node in both.flow_definition["nodes"][1:]:
        assert revision(db, node["data"]["agent_revision_id"]).snapshot["model_id"] not in retired_ids
    assert flow_execution_revision_findings(db, both.flow_definition, user_id=1, active_group_ids=[]) == []
    after = conversion.verify(db, moves)
    assert after["blocking"] == 0
    assert after["counts"]["non_system_rows"] == after["counts"]["active_flow_pins"] == 0
    assert after["counts"]["system_rows"] == len(retired_ids)  # rewritten by startup's sync

    counts = {agent.id: revision_count(db, agent) for row in created for agent in row[2:6]}
    again = run_moves(db, moves)
    assert again["counts"]["revisions_appended"] == again["counts"]["editable_rows_moved"] == 0
    assert {agent.id: revision_count(db, agent) for row in created for agent in row[2:6]} == counts


def test_a_converted_database_passes_startup_validation_and_an_unconverted_row_is_named(db, moves):
    move = moves[0]
    retired = move.from_ids[0]
    agent = make_agent(db, "Finder")
    save_on_retired(db, agent, model=retired)
    agent.model_id = retired
    db.flush()

    with pytest.raises(RuntimeError) as refused:
        runtime_validation.validate_and_cache_agent_runtime_contracts(strict_mode=False)
    assert f"{agent.agent_key}: Unknown model_id '{retired}'" in str(refused.value)

    run_moves(db, moves)

    report = runtime_validation.validate_and_cache_agent_runtime_contracts(strict_mode=False)
    assert report["errors"] == []


def _synthetic(reasoning=None, retired="gpt-retired-test"):
    target = get_default_model().model_id
    return [conversion.ModelMove(from_ids=(retired,), to_id=target, reasoning_map=reasoning or {},
                                 note="test move")]


def test_a_refused_agent_keeps_its_editable_row_and_its_flow(db):
    moves = _synthetic()
    target = get_default_model()
    unoffered = next(level for level in ("xhigh", "max", "minimal") if level not in target.reasoning_options)
    bad = make_agent(db, "Bad")
    bad_head = save_on_retired(db, bad, reasoning=unoffered, model="gpt-retired-test")
    bad.model_id, bad.model_reasoning = "gpt-retired-test", unoffered
    unsaved = make_agent(db, "Unsaved")
    unsaved.execution_revision_id, unsaved.model_id, unsaved.model_reasoning = None, "gpt-retired-test", unoffered
    flow = make_flow(db, [(bad, bad_head)])
    before = deepcopy(flow.flow_definition)

    report = run_moves(db, moves)

    assert report["counts"]["agents_refused"] == 1 and report["counts"]["flows_refused"] == 1
    assert report["counts"]["editable_rows_refused"] == 1  # the unsaved row; bad was skipped
    assert (bad.model_id, bad.model_reasoning, bad.execution_revision_id) == (
        "gpt-retired-test", unoffered, bad_head.id)
    assert unsaved.model_id == "gpt-retired-test"
    db.refresh(flow)
    assert flow.flow_definition == before
    assert conversion.verify(db, moves)["blocking"] > 0


@pytest.mark.parametrize("change,message", [
    (lambda moves: [moves[0].__class__(from_ids=(moves[0].to_id,), to_id=moves[0].to_id,
                                       reasoning_map={}, note="n")], "still in the model catalog"),
    (lambda moves: [moves[0].__class__(from_ids=("gpt-retired-test",), to_id="not-a-model",
                                       reasoning_map={}, note="n")], "not in the model catalog"),
    (lambda moves: moves + moves, "more than one move"),
])
def test_an_inconsistent_moves_list_is_refused_before_any_write(db, change, message):
    agent = make_agent(db, "Finder")
    head = save_on_retired(db, agent, model="gpt-retired-test")
    with pytest.raises(conversion.ConversionRefused, match=message):
        run_moves(db, change(_synthetic()))
    assert agent.execution_revision_id == head.id


def test_a_retired_model_left_in_the_environment_stops_the_run(db, monkeypatch):
    monkeypatch.setenv("AGENT_GENE_MODEL", "gpt-retired-test")
    agent = make_agent(db, "Finder")
    head = save_on_retired(db, agent, model="gpt-retired-test")
    with pytest.raises(conversion.ConversionRefused, match="AGENT_GENE_MODEL='gpt-retired-test'"):
        run_moves(db, _synthetic())
    assert agent.execution_revision_id == head.id


def test_moves_file_shape_is_checked(tmp_path):
    path = tmp_path / "moves.yaml"
    path.write_text("moves:\n  - from: gpt-a\n    to: gpt-b\n    reasoning: {}\n    note: n\n")
    with pytest.raises(conversion.ConversionRefused, match="from must be a non-empty list"):
        conversion.load_moves(path)
    path.write_text("moves:\n  - from: [gpt-a]\n    to: gpt-b\n    note: n\n")
    with pytest.raises(conversion.ConversionRefused, match="reasoning"):
        conversion.load_moves(path)
    path.write_text("moves:\n  - from: [gpt-a]\n    to: gpt-b\n    reasoning: {XHigh: High}\n    note: n\n")
    assert conversion.load_moves(path)[0].reasoning_map == {"xhigh": "high"}


# The command line, run through main() against this isolated schema.

def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    return str(path)


def _synthetic_moves_file(tmp_path, reasoning="{}"):
    return _write(tmp_path, "moves.yaml", (
        f"moves:\n  - from: [gpt-retired-test]\n    to: {get_default_model().model_id}\n"
        f"    reasoning: {reasoning}\n    note: test move\n"))


def _head(db, agent):
    db.expire_all()
    return db.get(Agent, agent.id).execution_revision_id


def test_cli_dry_run_rolls_back_then_apply_commits_then_verify_is_clean(db, tmp_path, capsys):
    agent = make_agent(db, "Finder")
    old_head = save_on_retired(db, agent, model="gpt-retired-test")
    make_flow(db, [(agent, old_head)])
    moves = _synthetic_moves_file(tmp_path)
    # A login maps provider groups to active groups; the command maps them the same way.
    group = next(group for group in list_groups() if group.provider_groups)
    groups = _write(tmp_path, "groups.json", {"1": [group.provider_groups[0]]})

    assert model_move_cli.main(["--moves", moves, "--provider-groups", groups]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["committed"] is False and dry["counts"]["steps_repinned"] == 1
    assert dry["owner_groups"] == {"1": [group.group_id]}
    assert _head(db, agent) == old_head.id
    assert model_move_cli.main(["--moves", moves, "--verify"]) == 3
    capsys.readouterr()

    assert model_move_cli.main(["--moves", moves, "--provider-groups", groups, "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["committed"] is True and applied["remaining"]["blocking"] == 0
    assert _head(db, agent) != old_head.id
    assert model_move_cli.main(["--moves", moves, "--verify"]) == 0
    assert json.loads(capsys.readouterr().out)["blocking"] == 0


def test_cli_lists_exactly_the_flow_owners_that_need_groups(db, tmp_path, capsys):
    agent = make_agent(db, "Finder", user_id=2)
    make_flow(db, [(agent, save_on_retired(db, agent, model="gpt-retired-test"))], user_id=2)
    other = make_agent(db, "Untouched")
    make_flow(db, [(other, revision(db, other.execution_revision_id))])

    assert model_move_cli.main(["--moves", _synthetic_moves_file(tmp_path), "--list-flow-owners"]) == 0
    assert json.loads(capsys.readouterr().out) == {"flow_owners": {"2": "sub-2"}}


def test_cli_exits_3_when_something_is_refused_and_1_when_the_run_cannot_start(db, tmp_path, capsys):
    agent = make_agent(db, "Finder")
    make_flow(db, [(agent, save_on_retired(db, agent, model="gpt-retired-test", reasoning="minimal"))])
    moves = _synthetic_moves_file(tmp_path)
    assert model_move_cli.main(["--moves", moves, "--provider-groups",
                                _write(tmp_path, "groups.json", {"1": []}), "--apply"]) == 3
    assert json.loads(capsys.readouterr().out)["counts"]["agents_refused"] == 1
    assert model_move_cli.main(["--moves", moves, "--provider-groups",
                                _write(tmp_path, "none.json", {})]) == 1
    assert "flow owner(s) [1]" in capsys.readouterr().err


def test_cli_needs_a_mode(tmp_path):
    with pytest.raises(SystemExit):
        model_move_cli.main(["--moves", _synthetic_moves_file(tmp_path)])


def test_report_revision_ids_are_real(db, tmp_path, capsys):
    agent = make_agent(db, "Finder")
    make_flow(db, [(agent, save_on_retired(db, agent, model="gpt-retired-test"))])
    assert model_move_cli.main(["--moves", _synthetic_moves_file(tmp_path), "--provider-groups",
                                _write(tmp_path, "groups.json", {"1": []}), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    [step] = report["owners"][0]["flows"][0]["steps"]
    assert db.scalar(select(AgentExecutionRevision.agent_id).where(
        AgentExecutionRevision.id == UUID(step["to_revision_id"]))) == agent.id
