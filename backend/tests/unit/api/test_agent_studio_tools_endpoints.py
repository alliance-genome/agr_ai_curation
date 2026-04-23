"""Unit tests for Agent Studio tool metadata endpoints."""

import logging

import pytest
from fastapi import HTTPException

from src.api import agent_studio
from src.lib.agent_studio import catalog_service


@pytest.mark.asyncio
async def test_get_all_tools_endpoint_maps_unexpected_errors_to_500(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=agent_studio.logger.name)

    def _boom():
        raise RuntimeError("tool registry exploded")

    monkeypatch.setattr(catalog_service, "get_all_tools", _boom)

    with pytest.raises(HTTPException) as exc_info:
        await agent_studio.get_all_tools_endpoint(user={"sub": "auth-sub"})

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to retrieve tools"
    assert "tool registry exploded" not in str(exc_info.value.detail)
    assert "tool registry exploded" in caplog.text


@pytest.mark.asyncio
async def test_get_tool_details_endpoint_maps_unexpected_errors_to_500(monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=agent_studio.logger.name)

    def _boom(_tool_id):
        raise RuntimeError("tool details exploded")

    monkeypatch.setattr(catalog_service, "get_tool_details", _boom)

    with pytest.raises(HTTPException) as exc_info:
        await agent_studio.get_tool_details_endpoint(
            tool_id="agr_curation_query",
            agent_id=None,
            user={"sub": "auth-sub"},
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to retrieve tool details"
    assert "tool details exploded" not in str(exc_info.value.detail)
    assert "tool details exploded" in caplog.text
