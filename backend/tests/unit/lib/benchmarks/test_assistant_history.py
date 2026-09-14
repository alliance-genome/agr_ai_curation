from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.lib.benchmarks.assistant_history import (
    AssistantTurnConflict,
    complete_assistant_turn,
    create_assistant_session,
    prepare_assistant_turn,
)
from src.lib.chat_history_repository import (
    BENCHMARK_ASSISTANT_CHAT_KIND,
    ChatHistorySessionNotFoundError,
)


def repository():
    result = MagicMock()
    result.get_session.return_value = SimpleNamespace(chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND)
    result.append_message.side_effect = lambda **values: SimpleNamespace(
        message=SimpleNamespace(**values), created=True,
    )
    result.get_message_by_turn_id.return_value = None
    return result


@pytest.mark.parametrize("session", [None, SimpleNamespace(chat_kind="agent_studio")])
def test_wrong_owner_or_kind_cannot_append_turn(session):
    store = repository()
    store.get_session.return_value = session
    with pytest.raises(ChatHistorySessionNotFoundError):
        prepare_assistant_turn(store, subject="verified-human", session_id="wrong-session",
                               turn_id="turn", message="Hello", context={})
    store.append_message.assert_not_called()


def test_create_and_prepare_use_verified_subject_and_explicit_kind():
    store = repository()
    create_assistant_session(store, subject="verified-human")
    assert store.create_session.call_args.kwargs["user_auth_sub"] == "verified-human"
    assert store.create_session.call_args.kwargs["chat_kind"] == BENCHMARK_ASSISTANT_CHAT_KIND
    prepared = prepare_assistant_turn(
        store, subject="verified-human", session_id="session", turn_id="turn",
        message="Check this draft", context={"draft_id": "draft"},
    )
    assert prepared.replay is None
    assert store.append_message.call_args.kwargs["chat_kind"] == BENCHMARK_ASSISTANT_CHAT_KIND
    assert store.append_message.call_args.kwargs["user_auth_sub"] == "verified-human"


def test_same_turn_replays_but_changed_request_conflicts():
    store = repository()
    args = dict(subject="verified-human", session_id="session", turn_id="turn",
                message="Check this draft", context={"revision": 1})
    original = prepare_assistant_turn(store, **args)
    store.append_message.side_effect = None
    store.append_message.return_value = SimpleNamespace(message=original.user_message, created=False)
    completed = SimpleNamespace(content="Saved answer")
    store.get_message_by_turn_id.return_value = completed
    assert prepare_assistant_turn(store, **args).replay is completed
    with pytest.raises(AssistantTurnConflict):
        prepare_assistant_turn(store, **(args | {"context": {"revision": 2}}))


def test_completion_requires_user_turn_and_preserves_existing_completion():
    store = repository()
    args = dict(subject="verified-human", session_id="session", turn_id="turn",
                message="Answer", payload={"assistant_usage": {"cost_usd": None}}, trace_id=None)
    with pytest.raises(AssistantTurnConflict):
        complete_assistant_turn(store, **args)
    store.append_message.assert_not_called()
    store.get_message_by_turn_id.return_value = SimpleNamespace(content="Accepted request")
    receipt = SimpleNamespace(content="First answer", payload_json={"assistant_usage": {"cost_usd": None}})
    store.append_message.side_effect = None
    store.append_message.return_value = SimpleNamespace(message=receipt)
    assert complete_assistant_turn(store, **args) is receipt
