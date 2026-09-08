"""Project discovery and owner-only conversation serialization."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.lib.agent_studio import tool_idea_service as service
from src.models.sql.tool_idea_request import ToolIdeaRequest


@pytest.mark.parametrize("member", [True, False])
def test_visible_requests_use_all_memberships_and_keep_unassigned_requests_private(member):
    # Execute the production ORM predicate against isolated, minimal SQLite tables.
    engine = create_engine("sqlite://")
    project, second_project, other_project = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE project_members (project_id UUID, user_id INTEGER)")
        connection.exec_driver_sql("""CREATE TABLE tool_idea_requests (
            id UUID PRIMARY KEY, user_id INTEGER, project_id UUID, title TEXT,
            description TEXT, opus_conversation JSON, status TEXT,
            developer_notes TEXT, resulting_tool_key TEXT,
            created_at DATETIME, updated_at DATETIME
        )""")
        if member:
            for project_id in (project, second_project):
                connection.exec_driver_sql("INSERT INTO project_members VALUES (?, ?)", (project_id.hex, 1))
    with Session(engine) as db:
        for title, owner, project_id in (
            ("own unassigned", 1, None), ("own project", 1, project),
            ("teammate", 2, project), ("second membership", 3, second_project),
            ("nonmember", 2, other_project), ("unassigned teammate", 2, None),
        ):
            db.add(ToolIdeaRequest(id=uuid4(), user_id=owner, project_id=project_id,
                                   title=title, description="Request", opus_conversation=[],
                                   created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
        db.flush()
        visible = service.list_tool_idea_requests_visible_to_user(db, 1)
        expected = {"own unassigned", "own project"}
        if member:
            expected |= {"teammate", "second membership"}
        assert {row.title for row in visible} == expected
    engine.dispose()


def test_teammate_serializer_never_reads_conversation_or_triage_fields():
    # Absence of private fields verifies the summary path does not even read them.
    record = SimpleNamespace(id=uuid4(), user_id=2, project_id=uuid4(), title="Idea",
                             description="Description", status="submitted",
                             created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
    summary = service.tool_idea_request_to_dict(cast(ToolIdeaRequest, record), viewer_user_id=1)
    assert "opus_conversation" not in summary
    assert "developer_notes" not in summary
    assert summary["user_id"] == 2
