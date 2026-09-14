"""Benchmark Chat persistence using the existing owner-scoped history store.

The authenticated API supplies the subject; no model or request context chooses
it. Callers own transaction commit/rollback and the existing executable-run
manager owns live producer/reconnect/cancellation coordination.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from src.lib.chat_history_repository import (
    BENCHMARK_ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageRecord,
    ChatSessionRecord,
)


class AssistantTurnConflict(ValueError):
    """A turn identifier was reused with a different message or context."""


@dataclass(frozen=True)
class PreparedAssistantTurn:
    session_id: str
    turn_id: str
    user_message: ChatMessageRecord
    replay: ChatMessageRecord | None
    created: bool


def require_assistant_session(
    repository: ChatHistoryRepository, *, subject: str, session_id: str,
) -> ChatSessionRecord:
    session = repository.get_session(session_id=session_id, user_auth_sub=subject)
    if session is None or session.chat_kind != BENCHMARK_ASSISTANT_CHAT_KIND:
        raise ChatHistorySessionNotFoundError("Benchmark assistance session not found")
    return session


def create_assistant_session(
    repository: ChatHistoryRepository, *, subject: str,
) -> ChatSessionRecord:
    return repository.create_session(
        session_id=str(uuid4()), user_auth_sub=subject, chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND,
    )


def prepare_assistant_turn(
    repository: ChatHistoryRepository, *, subject: str, session_id: str,
    turn_id: str, message: str, context: dict[str, Any],
) -> PreparedAssistantTurn:
    require_assistant_session(repository, subject=subject, session_id=session_id)
    fingerprint = hashlib.sha256(json.dumps(
        {"message": message, "context": context}, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()
    appended = repository.append_message(
        session_id=session_id, user_auth_sub=subject, chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND,
        turn_id=turn_id, role="user", content=message,
        payload_json={"request_fingerprint": fingerprint, "context": context},
    )
    user = appended.message
    # append_message returns the existing unique user-turn row on a concurrent
    # retry. Compare the saved request, not the newly supplied request payload.
    if not isinstance(user.payload_json, dict) or user.payload_json.get(
        "request_fingerprint",
    ) != fingerprint:
        raise AssistantTurnConflict("Turn request changed; start a new turn")
    replay = repository.get_message_by_turn_id(
        session_id=session_id, user_auth_sub=subject, turn_id=turn_id, role="assistant",
    )
    return PreparedAssistantTurn(session_id, turn_id, user, replay, appended.created)


def complete_assistant_turn(
    repository: ChatHistoryRepository, *, subject: str, session_id: str,
    turn_id: str, message: str, payload: dict[str, Any], trace_id: str | None,
) -> ChatMessageRecord:
    require_assistant_session(repository, subject=subject, session_id=session_id)
    original = repository.get_message_by_turn_id(
        session_id=session_id, user_auth_sub=subject, turn_id=turn_id, role="user",
    )
    if original is None:
        raise AssistantTurnConflict("An accepted user turn is required")
    # Existing unique assistant-turn rows preserve the first completed response,
    # its proposal receipts and assistant accounting when completion is retried.
    return repository.append_message(
        session_id=session_id, user_auth_sub=subject, chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND,
        turn_id=turn_id, role="assistant", content=message,
        payload_json=payload, trace_id=trace_id,
    ).message
