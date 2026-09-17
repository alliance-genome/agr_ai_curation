"""ALL-1244: Agent Studio reads failed flow runs from durable chat records.

Runs against PostgreSQL because the lookup filters JSONB payload fields and
joins the caller's own, non-deleted chat sessions.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.lib.agent_studio import saved_resource_inspection as inspection
from src.models.sql.chat_message import ChatMessage
from src.models.sql.chat_session import ChatSession
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User

# Unique per process so parallel workers never share fixture rows.
_TOKEN = uuid4().hex[:12]
OWNER_SUB = f"flow-run-inspection-owner-{_TOKEN}"
OTHER_SUB = f"flow-run-inspection-other-{_TOKEN}"
SESSION_PREFIX = f"flow-run-inspection-{_TOKEN}-"
FLOW_NAME = f"ALL-1244 Identify MGI Allele IDs {_TOKEN}"
# Identifiers from the production refusal fixture (2026-09-17 11:19 UTC).
REFUSED_RUN_ID = "d202ed58-76ca-474d-ab48-d152adfd32c6"
RUN = {name: str(uuid4()) for name in (
    "newest", "completed", "oldest", "outside-window", "other-flow", "deleted-session", "other-user",
)}
LEGACY_MESSAGE = (
    "Flow cannot start because these steps are unavailable: 1 (Mouse Allele Identification), "
    "2 (Allele/Variant Extraction Agent (Custom)2). If a step needs a PDF, open Documents in the "
    "top navigation and load a document into chat. In Flow Builder, check that each step uses an "
    "available agent; add validators as validation attachments, not ordinary steps."
)


def _cleanup(db) -> None:
    db.execute(delete(ChatMessage).where(ChatMessage.session_id.like(f"{SESSION_PREFIX}%")))
    db.execute(delete(ChatSession).where(ChatSession.session_id.like(f"{SESSION_PREFIX}%")))
    db.execute(delete(CurationFlow).where(CurationFlow.name == FLOW_NAME))
    db.execute(delete(User).where(User.auth_sub.in_((OWNER_SUB, OTHER_SUB))))
    db.commit()


@pytest.fixture
def db(test_db):
    bind = test_db.get_bind()
    for model in (User, PDFDocument, ChatSession, ChatMessage, CurationFlow):
        model.__table__.create(bind=bind, checkfirst=True)
    _cleanup(test_db)
    yield test_db
    test_db.rollback()
    _cleanup(test_db)


def _user(db, sub):
    user = User(auth_sub=sub, email=f"{sub}@example.org", display_name=sub, is_active=True)
    db.add(user)
    db.flush()
    return user


def _flow(db, user):
    flow = CurationFlow(user_id=user.id, name=FLOW_NAME, flow_definition={"nodes": [], "edges": []})
    db.add(flow)
    db.flush()
    return flow


def _session(db, sub, suffix, *, deleted=False):
    session_id = f"{SESSION_PREFIX}{suffix}"
    db.add(ChatSession(
        session_id=session_id, user_auth_sub=sub, chat_kind="assistant_chat",
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    ))
    db.flush()
    return session_id


def _summary(db, session_id, flow_id, run_id, *, age, codes="absent", status="failed",
             document_id=None, trace_id=None, failure_reason=LEGACY_MESSAGE):
    payload = {
        "flow_id": str(flow_id), "flow_name": FLOW_NAME, "flow_run_id": run_id,
        "session_id": session_id, "document_id": document_id, "status": status,
        "trace_id": trace_id, "failure_reason": failure_reason, "final_user_output": None,
    }
    if codes != "absent":
        payload["unavailable_step_reason_codes"] = codes
    db.add(ChatMessage(
        session_id=session_id, chat_kind="assistant_chat", turn_id=str(uuid4()), role="flow",
        message_type="flow_summary", content="Flow failed before producing a final output.",
        payload_json=payload, trace_id=trace_id,
        created_at=datetime.now(timezone.utc) - age,
    ))


def _inspect(db, user, **kwargs):
    return inspection.inspect_saved_resource(
        db, user_id=user.id, active_group_ids=["MGI"],
        request=inspection.SavedResourceInspection(**kwargs),
    )


def test_refused_run_is_listed_for_owner_only_and_found_by_run_id(db):
    owner, other = _user(db, OWNER_SUB), _user(db, OTHER_SUB)
    flow = _flow(db, owner)
    session_id = _session(db, OWNER_SUB, "owner")
    # Legacy (pre-fix) record from the production fixture: no reason codes.
    _summary(db, session_id, flow.id, REFUSED_RUN_ID, age=timedelta(minutes=5))
    db.commit()

    result = _inspect(db, owner, action="recent_flow_runs", flow_id=str(flow.id))
    assert [run["flow_run_id"] for run in result["runs"]] == [REFUSED_RUN_ID]
    run = result["runs"][0]
    assert run["status"] == "failed"
    assert run["document_loaded"] is False and run["document_id"] is None
    assert run["trace_id"] is None
    assert run["reason_codes"] is None
    assert run["failure_reason"] == LEGACY_MESSAGE

    by_id = _inspect(db, owner, action="flow_run_traces", flow_run_id=REFUSED_RUN_ID)
    assert by_id["trace_ids"] == []
    assert [item["flow_run_id"] for item in by_id["runs"]] == [REFUSED_RUN_ID]

    # Another user: the flow is unavailable and the run ID reveals nothing.
    with pytest.raises(ValueError, match="unavailable to you"):
        _inspect(db, other, action="recent_flow_runs", flow_id=str(flow.id))
    other_lookup = _inspect(db, other, action="flow_run_traces", flow_run_id=REFUSED_RUN_ID)
    assert other_lookup["trace_ids"] == [] and other_lookup["runs"] == []


def test_recent_runs_are_owned_windowed_ordered_and_paged(db, monkeypatch):
    monkeypatch.setattr(inspection, "get_tool_page_default_limit", lambda: 2)
    owner, other = _user(db, OWNER_SUB), _user(db, OTHER_SUB)
    flow = _flow(db, owner)
    owner_session = _session(db, OWNER_SUB, "paged")
    deleted_session = _session(db, OWNER_SUB, "deleted", deleted=True)
    other_session = _session(db, OTHER_SUB, "other")
    codes = [{"step": 1, "reason_code": "document_required"}]
    _summary(db, owner_session, flow.id, RUN["newest"], age=timedelta(minutes=1), codes=codes)
    _summary(db, owner_session, flow.id, RUN["completed"], age=timedelta(hours=2), codes=[],
             status="completed", document_id=str(uuid4()), trace_id="trace-ok", failure_reason=None)
    _summary(db, owner_session, flow.id, RUN["oldest"], age=timedelta(days=2),
             codes=[{"step": 2, "reason_code": "attachment_only_validator"}])
    _summary(db, owner_session, flow.id, RUN["outside-window"], age=timedelta(days=9))
    _summary(db, owner_session, uuid4(), RUN["other-flow"], age=timedelta(minutes=2))
    _summary(db, deleted_session, flow.id, RUN["deleted-session"], age=timedelta(minutes=3))
    # Even with the owner's flow_id, another user's session rows are never read.
    _summary(db, other_session, flow.id, RUN["other-user"], age=timedelta(minutes=4))
    db.commit()

    first = _inspect(db, owner, action="recent_flow_runs", flow_id=str(flow.id))
    assert [run["flow_run_id"] for run in first["runs"]] == [RUN["newest"], RUN["completed"]]
    assert first["runs"][0]["reason_codes"] == codes
    assert first["runs"][1]["status"] == "completed"
    assert first["runs"][1]["document_loaded"] is True
    assert first["runs"][1]["reason_codes"] == []
    assert first["complete"] is False
    second = _inspect(db, owner, **first["next_call"]["arguments"])
    assert [run["flow_run_id"] for run in second["runs"]] == [RUN["oldest"]]
    assert second["runs"][0]["reason_codes"] == [{"step": 2, "reason_code": "attachment_only_validator"}]
    assert second["complete"] is True and second["next_call"] is None

    assert _inspect(db, owner, action="flow_run_traces", flow_run_id=RUN["other-user"])["runs"] == []
    assert _inspect(db, owner, action="flow_run_traces", flow_run_id=RUN["deleted-session"])["runs"] == []
