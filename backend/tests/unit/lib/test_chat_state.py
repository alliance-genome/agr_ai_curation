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

    fetched["document_id"] = "mutated"
    assert state.get_document("user-a") == original

    state.clear_document("user-a")
    assert state.get_document("user-a") is None
    state.clear_document("user-a")

