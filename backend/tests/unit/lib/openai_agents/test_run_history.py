"""Exact recovery, authorization and write-failure behavior of canonical history."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.lib.openai_agents.run_history import (
    ACTIVE_HISTORY, HistoryScope, HistoryStore, RunHistory, exact_json, history_tools, wrap_history_tools,
)


@pytest.fixture
def store():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AgentHistoryPayload.__table__.create(engine)
    AgentHistoryEvent.__table__.create(engine)
    result = HistoryStore(sessionmaker(bind=engine, expire_on_commit=False))
    yield result
    engine.dispose()


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "alice")
    monkeypatch.setattr("src.lib.context.get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr("src.lib.context.get_current_session_id", lambda: "chat-a")
    return HistoryScope(owner_id="alice", run_id="run-a", invocation_id="root-a",
                        agent_key="supervisor", lineage="root-a/", session_id="chat-a", session_reader=True)


def test_restart_preserves_nested_payload_negation_nulls_and_duplicate_events(store, scope):
    value = {"nested": [{"negated": True, "quote": "Not expressed α😀.", "id": None}], "empty": []}
    first = store.append(scope, "result", exact_json(value), call_id="one")
    second = store.append(scope, "result", exact_json(value), call_id="two")
    restarted = HistoryStore(store.session_factory)
    assert first != second
    assert restarted.read(scope, first)["payload"] == value
    assert len(restarted.search(scope, "Not expressed", after=0, limit=10)) == 2
    with store.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentHistoryPayload)) == 1
        assert db.scalar(select(func.count()).select_from(AgentHistoryEvent)) == 2


def test_scope_denies_other_owner_session_sibling_and_parent_but_allows_child(store, scope):
    root = store.append(scope, "input", exact_json({"text": "secret"}))
    child_scope = replace(scope, invocation_id="child", lineage="root-a/child/", parent_invocation_id="root-a", session_reader=False)
    child = store.append(child_scope, "output", exact_json({"text": "secret child"}))
    sibling_scope = replace(child_scope, invocation_id="sibling", lineage="root-a/sibling/")
    for unauthorized, event in ((replace(scope, owner_id="bob"), root),
                                (replace(scope, session_id="chat-b"), root),
                                (child_scope, root), (sibling_scope, child)):
        with pytest.raises(ValueError, match="unavailable"):
            store.read(unauthorized, event)
        assert event not in {row["event_id"] for row in store.search(unauthorized, "secret", after=0, limit=10)}
    assert store.read(scope, child)["payload"]["text"] == "secret child"
    next_turn = replace(scope, run_id="run-b", invocation_id="root-b", lineage="root-b/")
    assert store.read(next_turn, root)["payload"]["text"] == "secret"


def test_literal_search_paging_does_not_treat_wildcards_as_sql_patterns(store, scope):
    for text in ("α_100%", "other", "α_100% repeated"):
        store.append(scope, "output", exact_json({"text": text}), tool_name="lookup")
    page = store.search(scope, "_100%", after=0, limit=1)
    second = store.search(scope, "_100%", after=page[-1]["sequence"], limit=1)
    assert len(page) == len(second) == 1
    assert page[0]["event_id"] != second[0]["event_id"]
    assert store.search(scope, "_100%", after=second[-1]["sequence"], limit=1) == []
    assert store.search(scope, "", after=0, limit=10, tool_name="different") == []


@pytest.mark.asyncio
async def test_large_nested_exact_read_pages_and_search_are_bounded(store, scope, monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "2048")
    value = {"findings": [{"quote": "NOT observed α😀 " * 500, "negated": True}]}
    ref = store.append(scope, "output", exact_json(value))
    tools = {tool.name: tool for tool in history_tools(RunHistory(scope, store))}
    cursor, sha, parts = 0, None, []
    while cursor is not None:
        result = await tools["read_run_history"].on_invoke_tool(SimpleNamespace(tool_name="read_run_history", tool_call_id="read", run_config=None), json.dumps({
            "event_id": ref, "detail_path": "payload.findings.0", "detail_cursor": cursor, "result_sha256": sha}))
        assert len(exact_json(result).encode()) <= 2048
        detail = result["detail"]
        parts.append(detail["content"])
        cursor, sha = detail["next_cursor"], detail["sha256"]
    assert json.loads("".join(parts)) == value["findings"][0]
    bad = await tools["read_run_history"].on_invoke_tool(SimpleNamespace(tool_name="read_run_history", tool_call_id="read", run_config=None), json.dumps({
        "event_id": ref, "result_sha256": "stale"}))
    assert bad["error_code"] == "stale_result_cursor"


@pytest.mark.asyncio
async def test_failed_archive_write_does_not_retry_or_hide_a_completed_mutation(scope):
    from agents import function_tool
    class BrokenStore:
        def append(self, *args, **kwargs):
            raise OSError("database unavailable")
    history = RunHistory(scope, BrokenStore())
    calls = []
    @function_tool
    async def save_record(value: str) -> dict:
        calls.append(value)
        assert ACTIVE_HISTORY.get() is history
        return {"saved": True, "id": "saved-1"}
    tool = wrap_history_tools([save_record], history)[0]
    result = await tool.on_invoke_tool(SimpleNamespace(tool_call_id="call-one", tool_name="save_record", run_config=None), '{"value":"one"}')
    assert calls == ["one"]
    assert result == {"saved": True, "id": "saved-1"}
    assert not history.healthy
    assert ACTIVE_HISTORY.get() is None


@pytest.mark.asyncio
async def test_capture_input_deduplicates_payloads_but_preserves_repeated_messages(store, scope):
    history = RunHistory(scope, store)
    items = [{"role": "user", "content": "repeat"}] * 2
    refs = await history.capture_input(items, "instructions")
    assert refs[0] != refs[1]
    assert await history.capture_input(items, "instructions") == refs
    assert len(store.search(scope, "repeat", after=0, limit=10)) == 2


@pytest.mark.asyncio
async def test_reused_history_tool_checks_request_identity_again(store, scope, monkeypatch):
    ref = store.append(scope, "output", exact_json({"private": "alice only"}))
    tool = next(t for t in history_tools(RunHistory(scope, store)) if t.name == "read_run_history")
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "bob")
    result = await tool.on_invoke_tool(SimpleNamespace(tool_name=tool.name, tool_call_id="read", run_config=None), json.dumps({"event_id": ref}))
    assert "alice only" not in str(result)
    assert result["status"] == "invalid_request"
