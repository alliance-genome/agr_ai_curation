"""Saved-work inspection must preserve caller scope and exact revision identity."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api import agent_studio as api
from src.lib.agent_studio import saved_resource_inspection as inspection
from src.lib.agent_studio.models import ChatContext


def flow():
    return SimpleNamespace(id=uuid4(), name="Stock extraction", description="Saved description",
                           updated_at=datetime.now(timezone.utc), execution_count=2,
                           flow_definition={"nodes": [{"id": "stock", "agent_revision_id": str(uuid4())}]})


def test_saved_flow_reads_only_owned_active_record_and_never_loads_editor():
    db = MagicMock()
    row = flow()
    db.scalars.return_value.one_or_none.return_value = row
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="flow", flow_id=str(row.id)))
    query = db.scalars.call_args.args[0].compile()
    assert "curation_flows.user_id =" in str(query)
    assert "curation_flows.is_active IS true" in str(query)
    assert set(query.params.values()) == {7, row.id}
    assert result["flow_definition"] == row.flow_definition
    assert result["saved"] is True and result["loaded_in_editor"] is False
    db.commit.assert_not_called()
    db.add.assert_not_called()


def test_unavailable_flow_does_not_disclose_record():
    db = MagicMock()
    db.scalars.return_value.one_or_none.return_value = None
    with pytest.raises(ValueError, match="unavailable to you"):
        inspection.inspect_saved_resource(db, user_id=8, active_group_ids=[],
            request=inspection.SavedResourceInspection(action="flow", flow_id=str(uuid4())))


def test_flow_search_is_owned_bounded_and_continuable(monkeypatch):
    monkeypatch.setattr(inspection, "get_tool_page_default_limit", lambda: 1)
    db = MagicMock()
    db.scalars.return_value.all.return_value = [flow(), flow()]
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=[],
        request=inspection.SavedResourceInspection(action="list_flows", query="%_", offset=3))
    assert len(result["flows"]) == 1
    assert result["next_call"]["arguments"] == {"action": "list_flows", "query": "%_", "offset": 4}
    query = db.scalars.call_args.args[0].compile()
    assert "curation_flows.user_id =" in str(query)
    assert "ESCAPE" in str(query)  # search metacharacters are literal curator text


def test_exact_revision_read_preserves_saved_identity_and_access_arguments(monkeypatch):
    agent, revision = uuid4(), uuid4()
    saved = MagicMock()
    saved.output_contract.generic_profile_ref = None
    saved.model_dump.return_value = {"custom_prompt": "Original prompt"}
    read = MagicMock(return_value=(SimpleNamespace(id=revision, revision=2, fingerprint="exact"), saved))
    monkeypatch.setattr(inspection, "get_execution_revision", read)
    db = MagicMock()
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=["TEAM"],
        request=inspection.SavedResourceInspection(action="agent_revision", agent_id=f"ca_{agent}", revision_id=str(revision)))
    read.assert_called_once_with(db, agent, revision, 7, active_group_ids=["TEAM"])
    assert result["revision_id"] == str(revision)
    assert result["snapshot"] == {"custom_prompt": "Original prompt"}
    assert result["loaded_in_editor"] is False


def test_revision_list_uses_authorized_service_cursor(monkeypatch):
    read = MagicMock(return_value=([], 5))
    monkeypatch.setattr(inspection, "list_execution_revisions", read)
    db, agent = MagicMock(), uuid4()
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=["TEAM"],
        request=inspection.SavedResourceInspection(action="agent_revisions", agent_id=str(agent), before_revision=9))
    read.assert_called_once_with(db, agent, 7, active_group_ids=["TEAM"], before_revision=9)
    assert result["next_call"]["arguments"]["before_revision"] == 5


@pytest.mark.parametrize("tab", ["agents", "flows", "agent_workshop"])
async def test_dispatch_exposes_tool_and_enforces_readonly_transaction(monkeypatch, tab):
    context = ChatContext.model_validate({"active_tab": tab})
    tools = {item["name"]: item for item in api._get_all_opus_tools(context)}
    assert "inspect_saved_studio_resource" in tools
    assert api._agent_studio_tool_namespace("inspect_saved_studio_resource")[0] == "studio_saved_work"
    db = MagicMock()
    session = MagicMock()
    session.return_value.__enter__.return_value = db
    monkeypatch.setattr(api, "SessionLocal", session)
    read = MagicMock(return_value={"saved": True, "flows": []})
    monkeypatch.setattr(inspection, "inspect_saved_resource", read)
    result = await api._handle_tool_call("inspect_saved_studio_resource", {"action": "list_flows"},
        context, "curator@example.org", "curator", user_db_id=7, active_group_ids=["TEAM"])
    assert result["success"] is True
    assert str(db.execute.call_args.args[0]) == "SET TRANSACTION READ ONLY"
    assert read.call_args.kwargs["user_id"] == 7
    assert read.call_args.kwargs["active_group_ids"] == ["TEAM"]
    db.commit.assert_not_called()


async def test_dispatch_rejects_missing_identity_before_database_access(monkeypatch):
    session = MagicMock()
    monkeypatch.setattr(api, "SessionLocal", session)
    result = await api._handle_tool_call("inspect_saved_studio_resource", {"action": "list_flows"},
        ChatContext.model_validate({"active_tab": "flows"}), "curator@example.org", "curator")
    assert result["success"] is False
    session.assert_not_called()


def test_raw_sql_and_write_actions_are_not_tool_inputs():
    for data in ({"action": "list_flows", "sql": "SELECT * FROM users"}, {"action": "save_flow"}):
        with pytest.raises(ValidationError):
            inspection.SavedResourceInspection.model_validate(data)


@pytest.mark.parametrize("authorized", [True, False])
def test_saved_revision_includes_only_its_authorized_pinned_structure(monkeypatch, authorized):
    db, agent, revision, profile_id, profile_revision = MagicMock(), uuid4(), uuid4(), uuid4(), uuid4()
    saved = MagicMock()
    saved.output_contract.generic_profile_ref = SimpleNamespace(profile_id=profile_id, revision=3)
    saved.model_dump.return_value = {"output_contract": "exact saved contract"}
    read = MagicMock(return_value=(SimpleNamespace(id=revision, revision=2, fingerprint="agent-pin"), saved))
    profile = SimpleNamespace(id=profile_revision, revision=3, fingerprint="profile-pin",
                              contract={"description": "Only experimental genes", "fields": [{"key": "gene"}]})
    read_profile = MagicMock(return_value=profile)
    monkeypatch.setattr(inspection, "get_execution_revision", read)
    monkeypatch.setattr(inspection, "get_profile_revision", read_profile)
    request = inspection.SavedResourceInspection(action="agent_revision", agent_id=str(agent), revision_id=str(revision))
    if not authorized:
        read.side_effect = ValueError("Unavailable saved revision")
        with pytest.raises(ValueError, match="Unavailable"):
            inspection.inspect_saved_resource(db, user_id=7, active_group_ids=[], request=request)
        read_profile.assert_not_called()
        return
    result = inspection.inspect_saved_resource(db, user_id=7, active_group_ids=[], request=request)
    read_profile.assert_called_once_with(db, profile_id, 3, 7, include_archived=True)
    assert result["output_profile"] == {"profile_id": str(profile_id), "revision_id": str(profile_revision),
                                        "revision": 3, "fingerprint": "profile-pin", "contract": profile.contract}
    assert result["loaded_in_editor"] is False
    db.commit.assert_not_called()
