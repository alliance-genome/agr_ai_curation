"""Durable partial chat results preserve judgments and enforce ownership."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.api import chat_common


@pytest.mark.parametrize("available", [True, False])
def test_partial_result_requires_owned_durable_refs_and_preserves_failure(monkeypatch, available):
    result_id = uuid4()
    db = Mock()
    db.execute.return_value.scalars.return_value.all.return_value = [result_id] if available else []
    repo = Mock()
    repo.get_message_by_turn_id.return_value = None
    record = SimpleNamespace(message_id=uuid4(), content="Saved results; summary failed")
    repo.append_message.return_value = SimpleNamespace(message=record)
    monkeypatch.setattr(chat_common, "SessionLocal", lambda: db)
    monkeypatch.setattr(chat_common, "_get_chat_history_repository", lambda _: repo)
    persist = Mock()
    link = Mock()
    monkeypatch.setattr(chat_common, "_persist_extraction_candidates", persist)
    monkeypatch.setattr(chat_common, "_link_persisted_extraction_results_to_chat_turn", link)
    args = dict(session_id="session-1", user_id="owner-1", turn_id="turn-1", user_message="extract",
                assistant_message=record.content, trace_id="trace-1", extraction_candidates=[],
                persisted_extraction_refs=[SimpleNamespace(extraction_result_id=result_id)],
                document_id=None, failure_message="Summary failed; saved results available")
    if available:
        assert chat_common._persist_completed_chat_stream_turn(**args) is record
        payload = repo.append_message.call_args.kwargs["payload_json"]
        assert payload == {"terminal_state": "turn_failed", "terminal_message": args["failure_message"]}
        assert persist.call_args.kwargs["candidates"] == []
        assert link.call_args.kwargs["refs"] == args["persisted_extraction_refs"]
    else:
        with pytest.raises(ValueError, match="not owned"):
            chat_common._persist_completed_chat_stream_turn(**args)
        repo.append_message.assert_not_called()
        persist.assert_not_called()
        link.assert_not_called()
        db.rollback.assert_called()
    statement = db.execute.call_args.args[0].compile()
    assert "extraction_results.user_id =" in str(statement)
    assert "extraction_results.origin_session_id =" in str(statement)
    assert "owner-1" in statement.params.values()
    assert "session-1" in statement.params.values()
    db.close.assert_called_once()


def test_partial_recovery_deleted_session_is_not_recreated(monkeypatch):
    db = Mock()
    repo = Mock()
    repo.get_session.return_value = None
    monkeypatch.setattr(chat_common, "SessionLocal", lambda: db)
    monkeypatch.setattr(chat_common, "_get_chat_history_repository", lambda _: repo)
    with pytest.raises(chat_common.ChatHistorySessionNotFoundError):
        chat_common._persist_completed_chat_stream_turn(
            session_id="deleted", user_id="owner", turn_id="turn", user_message="extract",
            assistant_message="saved", trace_id=None, extraction_candidates=[],
            persisted_extraction_refs=[SimpleNamespace(extraction_result_id=uuid4())],
            document_id=None, failure_message="summary failed")
    repo.append_message.assert_not_called()
    db.execute.assert_not_called()
