"""ALL-1278 (B1): recall_chat_history stays inside the model-facing result budget."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

import src.lib.openai_agents.tool_result_bounds as tool_result_bounds
from src.lib.chat_history_repository import ASSISTANT_CHAT_KIND, ChatMessageRecord
from src.lib.openai_agents import supervisor_context_tools as module

BUDGET = 8192
UNICODE = "β-catenin 表达 im Flügel 😀 "


def _message(content: str, *, index: int, role: str = "assistant", turn_id: str | None = None):
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-recall",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id=turn_id or f"turn-{index}",
        role=role,
        message_type="text",
        content=content,
        payload_json=None,
        trace_id=None,
        created_at=datetime(2026, 9, 22, 12, index % 60, tzinfo=timezone.utc),
    )


@pytest.fixture(autouse=True)
def active_chat(monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", str(BUDGET))
    monkeypatch.setattr(module, "get_current_session_id", lambda: "session-recall")
    monkeypatch.setattr(module, "get_current_user_id", lambda: "user-recall")


@pytest.fixture
def reported(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tool_result_bounds,
        "report_payload_contract_violation",
        lambda violation, **kwargs: calls.append((violation, kwargs)) or True,
    )
    return calls


def _install_repo(monkeypatch, messages, *, search_results=None, turn_messages=None):
    by_id = {message.message_id: message for message in messages}
    lookups = []

    class _FakeRepo:
        def __init__(self, _db):
            pass

        def get_message_by_id(self, **kwargs):
            lookups.append(kwargs)
            assert kwargs["session_id"] == "session-recall"
            assert kwargs["user_auth_sub"] == "user-recall"
            assert kwargs["chat_kind"] == ASSISTANT_CHAT_KIND
            return by_id.get(kwargs["message_id"])

        def search_session_messages_ranked(self, **kwargs):
            return list(search_results or [])

        def list_messages_for_turn(self, **kwargs):
            return list(turn_messages or [])

    monkeypatch.setattr(module, "SessionLocal", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, "ChatHistoryRepository", _FakeRepo)
    monkeypatch.setattr(module, "_list_session_messages", lambda **_kwargs: list(messages))
    return lookups


def _recall(**kwargs):
    raw = asyncio.run(module.recall_chat_history(**kwargs))
    assert len(raw.encode("utf-8")) <= BUDGET
    return json.loads(raw)


def _read_exact(message_id: str) -> str:
    chunks, cursor = [], 0
    while cursor is not None:
        chunk = _recall(detail="message", message_id=message_id, content_cursor=cursor)
        assert chunk["status"] == "ok"
        chunks.append(chunk["content"])
        cursor = chunk["next_content_cursor"]
    return "".join(chunks)


def test_recent_pages_by_size_and_withheld_messages_read_exactly(monkeypatch, reported):
    messages = [_message(f"{index}: " + UNICODE * 40, index=index) for index in range(30)]
    messages[17] = _message("huge: " + UNICODE * 3000, index=17)
    _install_repo(monkeypatch, messages)

    pages = [_recall(detail="recent", limit=20)]
    while pages[-1]["truncated"] and len(pages) < 50:
        pages.append(_recall(detail="recent", limit=20, cursor=pages[-1]["next_cursor"]))

    assert pages[0]["page_ended_by"] == "size_budget"
    seen = []
    for page in reversed(pages):
        seen.extend(item["message_id"] for item in page["messages"])
    assert seen == [str(message.message_id) for message in messages]
    withheld = [item for page in pages for item in page["messages"] if item.get("withheld")]
    assert [item["message_id"] for item in withheld] == [str(messages[17].message_id)]
    assert withheld[0]["content_chars"] == len(messages[17].content)
    assert _read_exact(withheld[0]["detail_call"]["message_id"]) == messages[17].content
    assert reported == []  # paging is normal, not an alert


def test_turn_and_search_results_page_by_size(monkeypatch):
    turn = [_message(UNICODE * 40, index=index, turn_id="turn-big") for index in range(6)]
    _install_repo(monkeypatch, [], search_results=turn, turn_messages=turn)

    for kwargs in ({"detail": "turn", "turn_ref": "turn-big"}, {"detail": "search", "query": "catenin"}):
        pages = [_recall(**kwargs)]
        while pages[-1].get("truncated"):
            pages.append(_recall(**kwargs, cursor=pages[-1]["next_cursor"]))
        assert len(pages) > 1
        assert [item["content"] for page in pages for item in page["messages"]] == [
            message.content for message in turn
        ]


def test_message_detail_is_scoped_to_the_active_session(monkeypatch):
    own = _message("own text", index=1)
    lookups = _install_repo(monkeypatch, [own])

    foreign = _recall(detail="message", message_id=str(uuid4()))
    malformed = _recall(detail="message", message_id="not-a-uuid")
    exact = _recall(detail="message", message_id=str(own.message_id))

    assert foreign["status"] == "not_found"
    assert malformed["status"] == "invalid_request"
    assert exact["content"] == "own text" and exact["complete"] is True
    assert all(call["user_auth_sub"] == "user-recall" for call in lookups)


@pytest.mark.parametrize("cursor", ["abc", "-1", "31"])
def test_invalid_or_stale_recent_cursor_is_explicit(monkeypatch, cursor):
    _install_repo(monkeypatch, [_message(f"m{index}", index=index) for index in range(30)])

    result = _recall(detail="recent", cursor=cursor)

    assert result["status"] == "invalid_cursor"


def test_invalid_content_cursor_is_explicit(monkeypatch):
    own = _message("short", index=1)
    _install_repo(monkeypatch, [own])

    result = _recall(detail="message", message_id=str(own.message_id), content_cursor=99)

    assert result["status"] == "invalid_cursor"


def test_unmeetable_budget_returns_compact_failure_and_reports_once(monkeypatch, reported):
    _install_repo(monkeypatch, [_message("text", index=1)])
    monkeypatch.setattr(module, "tool_result_budget", lambda: 40)

    result = json.loads(asyncio.run(module.recall_chat_history(detail="recent")))

    assert result["error_code"] == "tool_result_budget_unmet"
    assert len(reported) == 1
    assert reported[0][1]["tool_name"] == "recall_chat_history"
