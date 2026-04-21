"""Integration coverage for durable /api/chat/stream terminal events and rescue flow."""

from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import select

from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.models.sql.chat_message import ChatMessage as ChatMessageModel
from src.models.sql.chat_session import ChatSession as ChatSessionModel
from tests.integration.evidence_test_support import collect_sse_events

pytest_plugins = ["tests.integration.evidence_test_support"]


def _configure_stream_mocks(
    monkeypatch,
    *,
    run_agent_streamed,
    document_state_payload: dict[str, str] | None = None,
    tool_agent_map: dict[str, str] | None = None,
    check_cancel_signal=None,
) -> None:
    from src.api import chat

    monkeypatch.setattr(chat, "set_current_session_id", lambda _session_id: None)
    monkeypatch.setattr(chat, "set_current_user_id", lambda _user_id: None)
    monkeypatch.setattr(
        chat,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: document_state_payload),
    )
    monkeypatch.setattr(chat, "get_groups_from_cognito", lambda _groups: [])
    monkeypatch.setattr(chat, "get_supervisor_tool_agent_map", lambda: dict(tool_agent_map or {}))

    async def _register_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        return True

    async def _unregister_active_stream(
        session_id: str,
        user_id: str | None = None,
        stream_token: str | None = None,
    ):
        return None

    async def _clear_cancel_signal(_session_id: str):
        return None

    async def _default_check_cancel_signal(_session_id: str) -> bool:
        return False

    monkeypatch.setattr(chat, "register_active_stream", _register_active_stream)
    monkeypatch.setattr(chat, "unregister_active_stream", _unregister_active_stream)
    monkeypatch.setattr(chat, "clear_cancel_signal", _clear_cancel_signal)
    monkeypatch.setattr(
        chat,
        "check_cancel_signal",
        check_cancel_signal or _default_check_cancel_signal,
    )
    monkeypatch.setattr(chat, "run_agent_streamed", run_agent_streamed)


def test_chat_stream_persists_durable_rows_and_emits_turn_completed(client, monkeypatch, test_db):
    session_id = "chat-stream-durable-session"
    turn_id = "chat-stream-durable-turn"

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-stream-durable"}}
        yield {
            "type": "TEXT_MESSAGE_CONTENT",
            "data": {"content": "streamed reply", "trace_id": "trace-stream-durable"},
        }
        yield {
            "type": "RUN_FINISHED",
            "data": {"response": "streamed reply", "trace_id": "trace-stream-durable"},
        }

    _configure_stream_mocks(monkeypatch, run_agent_streamed=_run_agent_streamed)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hello", "session_id": session_id, "turn_id": turn_id},
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_CONTENT",
        "turn_completed",
    ]
    assert events[-1]["turn_id"] == turn_id

    test_db.expire_all()
    session_row = test_db.scalar(
        select(ChatSessionModel).where(ChatSessionModel.session_id == session_id)
    )
    assert session_row is not None

    rows = test_db.scalars(
        select(ChatMessageModel)
        .where(ChatMessageModel.session_id == session_id)
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()
    assert [(row.role, row.turn_id, row.content) for row in rows] == [
        ("user", turn_id, "hello"),
        ("assistant", turn_id, "streamed reply"),
    ]


def test_chat_stream_emits_turn_interrupted_and_leaves_only_user_row(client, monkeypatch, test_db):
    session_id = "chat-stream-interrupted-session"
    turn_id = "chat-stream-interrupted-turn"
    check_calls = {"count": 0}

    async def _check_cancel_signal(_session_id: str) -> bool:
        check_calls["count"] += 1
        return check_calls["count"] > 1

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-stream-stop"}}
        yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"content": "partial"}}

    _configure_stream_mocks(
        monkeypatch,
        run_agent_streamed=_run_agent_streamed,
        check_cancel_signal=_check_cancel_signal,
    )

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hello", "session_id": session_id, "turn_id": turn_id},
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    assert [event["type"] for event in events] == ["RUN_STARTED", "turn_interrupted"]
    assert events[-1]["turn_id"] == turn_id

    test_db.expire_all()
    rows = test_db.scalars(
        select(ChatMessageModel)
        .where(ChatMessageModel.session_id == session_id)
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()
    assert [(row.role, row.turn_id, row.content) for row in rows] == [
        ("user", turn_id, "hello"),
    ]


def test_chat_stream_extraction_persistence_failure_emits_turn_failed(client, monkeypatch, test_db, evidence_integration_context):
    from src.api import chat

    session_id = "chat-stream-extraction-failed-session"
    turn_id = "chat-stream-extraction-failed-turn"
    document_id = evidence_integration_context["document_id"]

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-extraction-failed"}}
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "ask_gene_expression_specialist"},
            "internal": {
                "tool_output": json.dumps(
                    {
                        "actor": "gene_expression_specialist",
                        "destination": "gene_expression",
                        "confidence": 0.9,
                        "reasoning": "done",
                        "items": [{"label": "notch"}],
                        "raw_mentions": [],
                        "exclusions": [],
                        "ambiguities": [],
                        "run_summary": {
                            "candidate_count": 1,
                            "kept_count": 1,
                            "excluded_count": 0,
                            "ambiguous_count": 0,
                            "warnings": [],
                        },
                    }
                )
            },
        }
        yield {
            "type": "RUN_FINISHED",
            "data": {"response": "failed response", "trace_id": "trace-extraction-failed"},
        }

    _configure_stream_mocks(
        monkeypatch,
        run_agent_streamed=_run_agent_streamed,
        document_state_payload={"id": document_id, "filename": "paper.pdf"},
        tool_agent_map={"ask_gene_expression_specialist": "gene-expression"},
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.extraction_results._get_agent_curation_metadata",
        lambda _agent_key: {"adapter_key": "gene_expression", "launchable": True},
    )
    monkeypatch.setattr(
        chat,
        "persist_extraction_results",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hello", "session_id": session_id, "turn_id": turn_id},
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "TOOL_COMPLETE",
        "SUPERVISOR_ERROR",
        "turn_failed",
    ]
    assert events[-1]["turn_id"] == turn_id

    test_db.expire_all()
    rows_after_failure = test_db.scalars(
        select(ChatMessageModel)
        .where(ChatMessageModel.session_id == session_id)
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()
    assert [(row.role, row.turn_id, row.content) for row in rows_after_failure] == [
        ("user", turn_id, "hello"),
    ]
    extraction_rows = test_db.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id,
        )
    ).all()
    assert extraction_rows == []


def test_chat_stream_turn_save_failed_then_assistant_rescue_is_idempotent(
    client,
    monkeypatch,
    test_db,
    evidence_integration_context,
):
    from src.api import chat

    session_id = "chat-stream-save-failed-session"
    turn_id = "chat-stream-save-failed-turn"
    document_id = evidence_integration_context["document_id"]
    assistant_failures = {"remaining": 1}

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": "trace-save-failed"}}
        yield {
            "type": "TOOL_COMPLETE",
            "details": {"toolName": "ask_gene_expression_specialist"},
            "internal": {
                "tool_output": json.dumps(
                    {
                        "actor": "gene_expression_specialist",
                        "destination": "gene_expression",
                        "confidence": 0.9,
                        "reasoning": "done",
                        "items": [{"label": "notch"}],
                        "raw_mentions": [],
                        "exclusions": [],
                        "ambiguities": [],
                        "run_summary": {
                            "candidate_count": 1,
                            "kept_count": 1,
                            "excluded_count": 0,
                            "ambiguous_count": 0,
                            "warnings": [],
                        },
                    }
                )
            },
        }
        yield {
            "type": "RUN_FINISHED",
            "data": {"response": "rescued response", "trace_id": "trace-save-failed"},
        }

    original_get_repository = chat._get_chat_history_repository

    def _get_repository(db):
        repository = original_get_repository(db)
        original_append_message = repository.append_message

        def _append_message(**kwargs):
            if (
                kwargs["session_id"] == session_id
                and kwargs["role"] == "assistant"
                and assistant_failures["remaining"] > 0
            ):
                assistant_failures["remaining"] -= 1
                raise RuntimeError("assistant row unavailable")
            return original_append_message(**kwargs)

        repository.append_message = _append_message
        return repository

    _configure_stream_mocks(
        monkeypatch,
        run_agent_streamed=_run_agent_streamed,
        document_state_payload={"id": document_id, "filename": "paper.pdf"},
        tool_agent_map={"ask_gene_expression_specialist": "gene-expression"},
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.extraction_results._get_agent_curation_metadata",
        lambda _agent_key: {"adapter_key": "gene_expression", "launchable": True},
    )
    monkeypatch.setattr(chat, "_get_chat_history_repository", _get_repository)

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "hello", "session_id": session_id, "turn_id": turn_id},
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    assert [event["type"] for event in events] == [
        "RUN_STARTED",
        "TOOL_COMPLETE",
        "SUPERVISOR_ERROR",
        "turn_save_failed",
    ]
    assert events[-1]["turn_id"] == turn_id
    assert assistant_failures["remaining"] == 0

    test_db.expire_all()
    rows_after_failure = test_db.scalars(
        select(ChatMessageModel)
        .where(ChatMessageModel.session_id == session_id)
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()
    assert [(row.role, row.turn_id, row.content) for row in rows_after_failure] == [
        ("user", turn_id, "hello"),
    ]
    extraction_rows = test_db.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id,
        )
    ).all()
    assert len(extraction_rows) == 1

    first_rescue = client.post(
        f"/api/chat/{session_id}/assistant-rescue",
        json={
            "turn_id": turn_id,
            "content": "rescued response",
            "trace_id": "trace-save-failed",
        },
    )
    assert first_rescue.status_code == 200, first_rescue.text
    assert first_rescue.json()["created"] is True

    second_rescue = client.post(
        f"/api/chat/{session_id}/assistant-rescue",
        json={
            "turn_id": turn_id,
            "content": "rescued response",
            "trace_id": "trace-save-failed",
        },
    )
    assert second_rescue.status_code == 200, second_rescue.text
    assert second_rescue.json()["created"] is False

    test_db.expire_all()
    rows_after_rescue = test_db.scalars(
        select(ChatMessageModel)
        .where(ChatMessageModel.session_id == session_id)
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()
    assert [(row.role, row.turn_id, row.content) for row in rows_after_rescue] == [
        ("user", turn_id, "hello"),
        ("assistant", turn_id, "rescued response"),
    ]
