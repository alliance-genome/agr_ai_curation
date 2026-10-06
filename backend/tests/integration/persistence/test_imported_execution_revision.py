"""An imported revision keeps its given id and number, and checks its output structure pin."""

from uuid import uuid4

import pytest

from src.lib.agent_studio.execution_revision_service import (ExecutionRevisionNotFoundError,
                                                             insert_imported_execution_revision)
from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
from src.models.sql.agent import Agent
from src.schemas.agent_execution_revision import AgentExecutionSnapshot, AgentOutputContract
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


def _snapshot(db, agent):
    agent.tool_ids = []
    agent.group_rules_enabled = False
    return capture_execution_snapshot(db, agent, AgentOutputContract(output_state="none"))


def test_an_imported_revision_keeps_its_id_and_number_and_leaves_the_head(execution_db):  # noqa: F811
    db, agent_id, _, _ = execution_db
    agent = db.get(Agent, agent_id)
    saved = _snapshot(db, agent)
    revision_id = uuid4()
    row = insert_imported_execution_revision(db, agent, saved, revision_id=revision_id,
                                             revision_number=7, creator_id=1)
    assert (row.id, row.revision, row.creator_id) == (revision_id, 7, 1)
    assert row.fingerprint == saved.fingerprint() and row.snapshot == saved.model_dump(mode="json")
    assert agent.execution_revision_id is None


def test_an_imported_revision_needs_the_owner(execution_db):  # noqa: F811
    db, agent_id, _, _ = execution_db
    agent = db.get(Agent, agent_id)
    with pytest.raises(ExecutionRevisionNotFoundError):
        insert_imported_execution_revision(db, agent, _snapshot(db, agent), revision_id=uuid4(),
                                           revision_number=1, creator_id=2)


def test_an_imported_revision_checks_its_output_structure_pin(execution_db):  # noqa: F811
    db, agent_id, _, profile_revision = execution_db
    agent = db.get(Agent, agent_id)
    data = _snapshot(db, agent).model_dump(mode="json")
    data["output_contract"] = {
        "output_state": "structured_extraction", "output_mode": "profile_bound_generic",
        "generic_profile_ref": {"profile_id": str(profile_revision.profile_id),
                                "profile_revision_id": str(uuid4()),
                                "revision": profile_revision.revision,
                                "fingerprint": profile_revision.fingerprint},
    }
    with pytest.raises(ValueError, match="identity mismatch"):
        insert_imported_execution_revision(db, agent, AgentExecutionSnapshot.model_validate(data),
                                           revision_id=uuid4(), revision_number=1, creator_id=1)
