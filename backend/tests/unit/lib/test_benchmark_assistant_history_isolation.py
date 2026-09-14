"""Benchmark conversations are not a Workshop history-tool input."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.lib.agent_studio.chat_session import (
    get_chat_conversation_payload,
    get_chat_turn_payload,
)
from src.lib.chat_history_repository import BENCHMARK_ASSISTANT_CHAT_KIND


def test_workshop_cannot_expand_a_benchmark_conversation_or_turn():
    repository = MagicMock()
    session = SimpleNamespace(chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND)
    repository.get_session_detail.return_value = SimpleNamespace(session=session)
    repository.get_session.return_value = session
    conversation = get_chat_conversation_payload(
        repository=repository, session_id="benchmark-session", user_auth_sub="same-owner",
        cursor=None, limit=10, provider_inline_max_chars=10000,
    )
    turn = get_chat_turn_payload(
        repository=repository, session_id="benchmark-session", turn_id="turn-1",
        user_auth_sub="same-owner", cursor=None, limit=10, message_id=None,
        field=None, start=None, max_chars=None, field_hash=None,
        chunk_max_chars=1000, provider_inline_max_chars=10000,
    )
    assert conversation == turn == {"success": False, "error": "Chat session not found."}
    repository.count_messages.assert_not_called()
    repository.list_messages.assert_not_called()
