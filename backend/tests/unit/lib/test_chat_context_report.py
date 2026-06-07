from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from src.lib.chat_context_report import build_chat_context_report
from src.lib.chat_history_repository import (
    ASSISTANT_CHAT_KIND,
    ChatMessagePage,
    ChatMessageRecord,
)


class _Repository:
    def __init__(self, messages):
        self.messages = messages

    def list_messages(self, **kwargs):
        return ChatMessagePage(items=self.messages, next_cursor=None)


def _message(
    *,
    role: str,
    message_type: str,
    content: str,
    payload_json=None,
    trace_id=None,
) -> ChatMessageRecord:
    return ChatMessageRecord(
        message_id=uuid4(),
        session_id="session-1",
        chat_kind=ASSISTANT_CHAT_KIND,
        turn_id="turn-1",
        role=role,
        message_type=message_type,
        content=content,
        payload_json=payload_json,
        trace_id=trace_id,
        created_at=datetime.now(timezone.utc),
    )


def test_chat_context_report_classifies_flow_memory_without_raw_payloads():
    messages = [
        _message(role="user", message_type="text", content="Which genes?"),
        _message(
            role="flow",
            message_type="execute_flow_transcript",
            content="Visible transcript",
            payload_json={
                "_assistant_message": "Hidden replay answer",
                "large_observability_blob": "x" * 1000,
            },
            trace_id="trace-1",
        ),
    ]

    report = build_chat_context_report(
        repository=cast(Any, _Repository(messages)),
        session_id="session-1",
        user_auth_sub="user-1",
        chat_kind=ASSISTANT_CHAT_KIND,
    )

    assert report is not None
    assert report["message_count"] == 2
    assert report["hidden_flow_memory_chars"] == len("Hidden replay answer")
    assert report["flow_memory_message_count"] == 1
    assert report["trace_ids"] == ["trace-1"]
    assert report["messages"][1]["model_live"] is True
    assert report["messages"][1]["model_live_source"] == "_assistant_message"
    assert report["messages"][1]["payload_json_model_live"] is False
    assert "large_observability_blob" not in str(report)
