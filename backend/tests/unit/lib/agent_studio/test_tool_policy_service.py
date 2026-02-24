"""Tests for tool policy cache service refresh behavior."""

from src.lib.agent_studio.tool_policy_service import ToolPolicyCacheService, ToolPolicyEntry


def _entry(tool_key: str) -> ToolPolicyEntry:
    return ToolPolicyEntry(
        tool_key=tool_key,
        display_name=tool_key,
        description="",
        category="General",
        curator_visible=True,
        allow_attach=True,
        allow_execute=True,
        config={},
    )


def test_list_all_uses_cache_when_not_stale(monkeypatch):
    service = ToolPolicyCacheService()
    service._ttl_seconds = 60.0
    calls = {"count": 0}

    def _fake_load(_db):
        calls["count"] += 1
        service._loaded_at_monotonic = 100.0
        return [_entry(f"tool_{calls['count']}")]

    monkeypatch.setattr(service, "_load", _fake_load)
    monkeypatch.setattr("src.lib.agent_studio.tool_policy_service.time.monotonic", lambda: 120.0)

    first = service.list_all(db=object())
    second = service.list_all(db=object())

    assert calls["count"] == 1
    assert first[0].tool_key == "tool_1"
    assert second[0].tool_key == "tool_1"


def test_list_all_refreshes_when_stale(monkeypatch):
    service = ToolPolicyCacheService()
    service._ttl_seconds = 10.0
    calls = {"count": 0}
    current_time = {"value": 100.0}

    def _fake_load(_db):
        calls["count"] += 1
        service._loaded_at_monotonic = current_time["value"]
        return [_entry(f"tool_{calls['count']}")]

    monkeypatch.setattr(service, "_load", _fake_load)
    monkeypatch.setattr(
        "src.lib.agent_studio.tool_policy_service.time.monotonic",
        lambda: current_time["value"],
    )

    first = service.list_all(db=object())
    current_time["value"] = 112.0
    second = service.list_all(db=object())

    assert calls["count"] == 2
    assert first[0].tool_key == "tool_1"
    assert second[0].tool_key == "tool_2"
