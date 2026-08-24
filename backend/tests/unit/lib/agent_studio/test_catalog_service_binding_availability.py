"""Focused coverage for Agent Studio tool-binding availability checks."""

from src.lib.agent_studio import catalog_service


def test_has_tool_binding_normalizes_tool_ids(monkeypatch):
    monkeypatch.setattr(catalog_service, "TOOL_BINDINGS", {"parent_tool": object()})
    monkeypatch.setattr(
        catalog_service,
        "METHOD_TOOL_ENTRIES",
        {"parent_tool.method": {"parent_tool": "parent_tool"}},
    )

    assert catalog_service.has_tool_binding("parent_tool") is True
    assert catalog_service.has_tool_binding("  parent_tool  ") is True
    assert catalog_service.has_tool_binding("parent_tool.method") is True
    assert catalog_service.has_tool_binding("unknown_tool") is False
