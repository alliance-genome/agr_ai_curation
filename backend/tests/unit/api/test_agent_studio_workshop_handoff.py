"""Saved Workshop references must remain authorized before Flow continuation."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.agent_studio_custom import get_workshop_saved_reference
from src.lib.agent_studio import capability_catalog
from src.lib.agent_studio.custom_agent_service import make_custom_agent_id


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["available", "hidden", "inactive", "not_selectable", "not_flow_selectable"])
async def test_saved_reference_rechecks_catalog_without_writing(monkeypatch, state):
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(id=7)
    agent_uuid = uuid4()
    agent_id = make_custom_agent_id(agent_uuid)
    record = capability_catalog.CapabilityRecord(
        kind="agent", resource_id=agent_id, name="Saved", description="",
        availability="unavailable" if state == "inactive" else "available",
        selectable=state != "not_selectable",
        compatibility={"flow_selectable": state != "not_flow_selectable"},
        detail={"identity_contract": {"agent_revision_id": str(uuid4())}},
    )
    catalog = MagicMock(return_value=[] if state == "hidden" else [record])
    monkeypatch.setattr(capability_catalog, "build_authorized_capability_catalog", catalog)
    if state == "available":
        result = await get_workshop_saved_reference(agent_uuid, user={"sub": "curator"}, db=db)
        assert result.agent_id == agent_id
    else:
        with pytest.raises(HTTPException) as raised:
            await get_workshop_saved_reference(agent_uuid, user={"sub": "curator"}, db=db)
        assert raised.value.status_code == 409
    context = catalog.call_args.kwargs["context"]
    assert context.user_id == 7
    assert context.active_tab == "flows"
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_workshop_action_endpoint_reauthorizes_without_writing(monkeypatch):
    from src.api import agent_studio_custom as api
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(id=7)
    prepare = MagicMock(return_value={"success": True, "saved": False})
    monkeypatch.setattr(api, "prepare_workshop_action", prepare)
    request = api.WorkshopActionValidationRequest.model_validate({
        "context": {"active_tab": "agents"}, "action": {"action": "new_agent", "mode": "scratch"},
    })
    result = await api.validate_workshop_action(request, user={"sub": "curator"}, db=db)
    assert result["saved"] is False
    assert prepare.call_args.kwargs["user_id"] == 7
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_workshop_action_endpoint_rejects_tampered_draft_before_preparing(monkeypatch):
    from src.api import agent_studio_custom as api
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(id=7)
    prepare = MagicMock()
    monkeypatch.setattr(api, "prepare_workshop_action", prepare)
    request = api.WorkshopActionValidationRequest.model_validate({
        "context": {"active_tab": "agent_workshop", "agent_workshop": {
            "draft_name": "Changed name", "draft_fingerprint": "sha256:" + "a" * 64,
        }}, "action": {"action": "save"},
    })
    with pytest.raises(HTTPException) as raised:
        await api.validate_workshop_action(request, user={"sub": "curator"}, db=db)
    assert raised.value.status_code == 409
    prepare.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_shared_clone_source_is_read_only_and_group_authorized(monkeypatch, allowed):
    from src.api import agent_studio_custom as api
    db, agent = MagicMock(), uuid4()
    db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(id=7)
    source = SimpleNamespace(id=agent, visibility="project")
    read = MagicMock(return_value=source)
    monkeypatch.setattr(api, "get_custom_agent_visible_to_user", read)
    authorize = MagicMock(side_effect=None if allowed else HTTPException(status_code=403, detail="Unavailable"))
    monkeypatch.setattr(api, "_require_custom_agent_group_access", authorize)
    serialize = MagicMock(return_value={"id": str(agent)})
    monkeypatch.setattr(api, "_as_response_payload", serialize)
    if allowed:
        result = await api.get_workshop_clone_source(agent, user={"sub": "curator"}, db=db)
        assert result == {"id": str(agent)}
    else:
        with pytest.raises(HTTPException):
            await api.get_workshop_clone_source(agent, user={"sub": "curator"}, db=db)
        serialize.assert_not_called()
    read.assert_called_once_with(db, agent, 7)
    authorize.assert_called_once_with(source, {"sub": "curator"})
    db.add.assert_not_called()
    db.commit.assert_not_called()
