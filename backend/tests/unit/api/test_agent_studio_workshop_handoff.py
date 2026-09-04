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
