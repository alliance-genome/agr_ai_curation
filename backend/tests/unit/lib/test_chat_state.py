"""Unit tests for per-user chat document state."""

from src.lib.chat_state import DocumentSelectionState


def test_document_state_isolated_per_user():
    state = DocumentSelectionState()
    state.set_document("user-a", {"document_id": "doc-a"})
    state.set_document("user-b", {"document_id": "doc-b"})

    assert state.get_document("user-a") == {"document_id": "doc-a"}
    assert state.get_document("user-b") == {"document_id": "doc-b"}


def test_document_state_returns_copy_and_clear_is_idempotent():
    state = DocumentSelectionState()
    original = {"document_id": "doc-a", "name": "paper.pdf"}
    state.set_document("user-a", original)

    fetched = state.get_document("user-a")
    assert fetched == original
    assert fetched is not None

    fetched["document_id"] = "mutated"
    assert state.get_document("user-a") == original

    state.clear_document("user-a")
    assert state.get_document("user-a") is None
    state.clear_document("user-a")


def test_document_intent_claim_supersedes_older_operation():
    state = DocumentSelectionState()
    state.set_document("user-a", {"document_id": "doc-b"})

    assert state.claim_intent("user-a", "browser-a", 1) is True

    assert state.claim_intent("user-a", "browser-a", 2) is True
    assert state.claim_intent("user-a", "browser-a", 1) is False
    assert state.claim_intent("user-a", "older-browser", 1) is False
    assert state.clear_document_if_current("user-a", "browser-a", 1) is False
    assert state.get_document("user-a") == {"document_id": "doc-b"}
    assert state.clear_document_if_current("user-a", "browser-a", 2) is True
    assert state.get_document("user-a") is None
