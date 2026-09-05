"""Session membership is multi-source; candidates retain their source revision."""
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError

from .test_envelope_revision_persistence import envelope_db, insert_envelope  # noqa: F401
from .test_extraction_revision_persistence import extraction_db, insert_result  # noqa: F401
from .test_flow_revision_persistence import flow_db, add_revision  # noqa: F401
from .test_agent_execution_revision_persistence import execution_db  # noqa: F401
from .test_generic_profile_persistence import profile_db  # noqa: F401


@pytest.fixture
def workspace_db(envelope_db):  # noqa: F811
    db, receipt, result_id, _ = envelope_db
    db.execute(sa.text("CREATE TABLE curation_review_sessions(id uuid PRIMARY KEY)"))
    db.execute(sa.text("ALTER TABLE domain_envelopes ADD COLUMN session_id uuid REFERENCES curation_review_sessions(id)"))
    db.execute(sa.text("""CREATE TABLE curation_candidates(id uuid PRIMARY KEY,
        session_id uuid NOT NULL REFERENCES curation_review_sessions(id), envelope_id text,
        extraction_result_id uuid)"""))
    session_id = uuid4()
    db.execute(sa.text("INSERT INTO curation_review_sessions VALUES (:id)"), {"id": session_id})
    envelope_id = insert_envelope(db, receipt, result_id)
    db.execute(sa.text("UPDATE domain_envelopes SET session_id=:session WHERE envelope_id=:id"),
               {"session": session_id, "id": envelope_id})
    candidate_id = uuid4()
    db.execute(sa.text("INSERT INTO curation_candidates VALUES (:id,:session,:envelope,NULL)"),
               {"id": candidate_id, "session": session_id, "envelope": envelope_id})
    path = Path(__file__).resolve().parents[3] / "alembic/versions/l9a0b1c2d3e4_workspace_revision_receipts.py"
    spec = importlib.util.spec_from_file_location("workspace_pin_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db.connection())):
        module.upgrade()
    return db, receipt, session_id, candidate_id, envelope_id


def test_candidate_migration_copies_only_existing_envelope_receipt(workspace_db):
    db, receipt, session_id, candidate_id, _ = workspace_db
    row = db.execute(sa.text("SELECT agent_revision_id,execution_receipt FROM curation_candidates WHERE id=:id"),
                     {"id": candidate_id}).one()
    assert row == (receipt.agent_revision_id, receipt.model_dump(mode="json"))
    assert db.scalar(sa.text("SELECT count(*) FROM curation_session_agent_revisions WHERE session_id=:id"), {"id": session_id}) == 1


def test_session_retains_multiple_revisions_even_when_source_has_no_candidates(workspace_db):
    from src.models.sql.agent import Agent
    from src.lib.agent_studio.execution_revision_service import current_execution_receipt
    from src.schemas.agent_execution_revision import AgentOutputContract
    db, first, session_id, _, _ = workspace_db
    agent = db.get(Agent, first.agent_id)
    agent.instructions = "Second immutable revision"
    add_revision(db, agent, AgentOutputContract(output_state="none"))
    second = current_execution_receipt(db, agent.agent_key, 1, active_group_ids=[])
    result_id = insert_result(db, second)
    envelope_id = insert_envelope(db, second, result_id)
    db.execute(sa.text("UPDATE domain_envelopes SET session_id=:session WHERE envelope_id=:id"),
               {"session": session_id, "id": envelope_id})
    rows = db.execute(sa.text("SELECT agent_revision_id FROM curation_session_agent_revisions WHERE session_id=:id"),
                      {"id": session_id}).scalars().all()
    assert set(rows) == {first.agent_revision_id, second.agent_revision_id}
    assert db.scalar(sa.text("SELECT count(*) FROM curation_candidates")) == 1


@pytest.mark.parametrize("change", ["omit", "erase", "session_ref", "forged"])
def test_workspace_rejects_dangling_or_conflicting_identity(workspace_db, change):
    db, receipt, session_id, candidate_id, envelope_id = workspace_db
    with pytest.raises(DBAPIError), db.begin_nested():
        if change == "omit":
            db.execute(sa.text("INSERT INTO curation_candidates(id,session_id,envelope_id) VALUES (:id,:session,:envelope)"),
                       {"id": uuid4(), "session": session_id, "envelope": envelope_id})
        elif change == "erase":
            db.execute(sa.text("UPDATE curation_candidates SET agent_revision_id=NULL,execution_receipt=NULL WHERE id=:id"),
                       {"id": candidate_id})
        elif change == "session_ref":
            db.execute(sa.text("DELETE FROM curation_session_agent_revisions WHERE session_id=:id"), {"id": session_id})
        else:
            payload = receipt.model_dump(mode="json")
            payload["revision"] += 1
            db.execute(sa.text("UPDATE curation_session_agent_revisions SET execution_receipt=CAST(:receipt AS jsonb) WHERE session_id=:id"),
                       {"id": session_id, "receipt": json.dumps(payload)})
