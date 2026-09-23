"""Real PostgreSQL visibility for custom-agent contract lookup (ALL-1295).

A curator's private custom agent is readable through ``get_agent_contract`` by
its owner and by the running agent itself, never by another curator or outside
the agent's group restrictions.
"""

from contextlib import nullcontext

import pytest

from src.lib import agent_contracts
from src.lib.context import _current_user_id
from src.models.sql.agent import Agent
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


@pytest.fixture
def private_custom_agent(execution_db, monkeypatch):  # noqa: F811 - pytest injects the imported fixture
    from src.lib.agent_studio.execution_revision_service import (
        append_execution_revision,
        current_execution_receipt,
    )
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.schemas.agent_execution_revision import AgentOutputContract

    db, agent_id, _, _ = execution_db
    head = db.get(Agent, agent_id)
    head.allowed_group_ids = ["FB"]
    head.inherited_allowed_group_ids = ["FB"]
    saved = capture_execution_snapshot(db, head, AgentOutputContract(output_state="none"))
    append_execution_revision(db, head, saved, user_id=1, expected_revision_id=None)
    db.flush()
    monkeypatch.setattr(agent_contracts, "_custom_agent_session", lambda: nullcontext(db))
    receipt = current_execution_receipt(db, head.agent_key, 1, active_group_ids=["FB"])
    return head, receipt


def _contract_as(user_id, agent_key, caller):
    token = _current_user_id.set(user_id)
    try:
        return agent_contracts.get_agent_contract(
            agent_id=agent_key, topic="output_schema", caller=caller
        )
    finally:
        _current_user_id.reset(token)


def test_private_custom_agent_contract_is_visible_only_to_owner_and_itself(private_custom_agent):
    from types import SimpleNamespace

    head, receipt = private_custom_agent
    supervisor = SimpleNamespace(agent_key="supervisor", authenticated_groups=("FB",))
    running = SimpleNamespace(
        agent_key=head.agent_key,
        execution_receipt=receipt.model_dump(mode="json"),
        authenticated_groups=("FB",),
    )

    owner = _contract_as("1", head.agent_key, supervisor)
    other_curator = _contract_as("2", head.agent_key, supervisor)
    outside_groups = _contract_as(
        "1", head.agent_key, SimpleNamespace(agent_key="supervisor", authenticated_groups=("WB",))
    )
    anonymous = _contract_as(None, head.agent_key, supervisor)
    itself = _contract_as(None, head.agent_key, running)

    assert owner["success"] is True, owner
    assert owner["custom_agent"]["revision_source"] == "saved_head"
    assert owner["custom_agent"]["name"] == "Test agent"
    assert "no structured output" in owner["note"]
    for hidden in (other_curator, outside_groups, anonymous):
        assert hidden["success"] is False
        assert hidden["error"] == f"Agent {head.agent_key} was not found."
        assert "custom_agent" not in hidden
    assert itself["success"] is True, itself
    assert itself["custom_agent"]["revision_source"] == "running_agent"
    assert itself["custom_agent"]["execution_revision"] == receipt.revision
