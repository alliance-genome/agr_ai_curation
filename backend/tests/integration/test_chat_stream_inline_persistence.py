"""Integration coverage for the chat completion path over inline-persisted rows.

This branch moved the first durable extraction write into the specialist
runtime (``run_specialist_with_events`` -> ``persist_inline_validated_extraction_result``).
The chat-stream / non-stream completion paths must therefore stop first-inserting
builder-backed extractions: when an ``INTERNAL_EXTRACTION_RESULT`` event already
carries a persisted ``extraction_result_id``, the completion path only
link-updates that row's metadata. These tests assert that contract.

Harness reality: the integration harness stubs ``run_agent_streamed``, so the
real specialist runtime (where inline persistence runs) is bypassed. We
therefore simulate the runtime's output: we persist a canonical row through the
real inline helper, then drive the endpoint with an event carrying that row's
id, exactly as the live runtime would emit it. This deterministically exercises
the completion-path plumbing (link-only, no duplicate) without a live model.

Design Part 6 integration scenarios covered:
- 2: streaming and non-streaming paths both link-only (no duplicate) for an
  event carrying a persisted id.
- 3: cancelling after a validated/persisted extraction leaves an inspectable row.
- 4: completing the turn after inline persistence creates no duplicate and links
  the final turn id into the row metadata.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from src.lib.curation_workspace.extraction_results import (
    persist_inline_validated_extraction_result,
)
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as ExtractionResultModel,
)
from src.models.sql.chat_message import ChatMessage as ChatMessageModel
from src.schemas.curation_workspace import CurationExtractionSourceKind
from tests.chat_api_test_support import patch_chat_impl
from tests.integration.evidence_test_support import collect_sse_events

pytest_plugins = ["tests.integration.evidence_test_support"]


def _configure_stream_mocks(
    monkeypatch,
    *,
    run_agent_streamed,
    document_state_payload,
    tool_agent_map,
    check_cancel_signal=None,
) -> None:
    from src.api import chat_common, chat_stream

    chat_modules = (chat_common, chat_stream)

    patch_chat_impl(monkeypatch, chat_modules, "set_current_session_id", lambda _s: None)
    patch_chat_impl(monkeypatch, chat_modules, "set_current_user_id", lambda _u: None)
    patch_chat_impl(
        monkeypatch,
        chat_modules,
        "document_state",
        SimpleNamespace(get_document=lambda _uid: document_state_payload),
    )
    patch_chat_impl(monkeypatch, chat_modules, "get_groups_from_cognito", lambda _g: [])
    patch_chat_impl(
        monkeypatch,
        chat_modules,
        "get_supervisor_tool_agent_map",
        lambda: dict(tool_agent_map),
    )

    async def _register_active_stream(session_id, user_id=None, stream_token=None):
        return True

    async def _unregister_active_stream(session_id, user_id=None, stream_token=None):
        return None

    async def _clear_cancel_signal(_session_id):
        return None

    async def _default_check_cancel_signal(_session_id) -> bool:
        return False

    patch_chat_impl(monkeypatch, chat_modules, "register_active_stream", _register_active_stream)
    patch_chat_impl(monkeypatch, chat_modules, "unregister_active_stream", _unregister_active_stream)
    patch_chat_impl(monkeypatch, chat_modules, "clear_cancel_signal", _clear_cancel_signal)
    patch_chat_impl(
        monkeypatch,
        chat_modules,
        "check_cancel_signal",
        check_cancel_signal or _default_check_cancel_signal,
    )
    patch_chat_impl(monkeypatch, chat_modules, "run_agent_streamed", run_agent_streamed)


def _canonical_gene_envelope(*, object_count: int = 1) -> dict:
    objects = [
        {
            "object_type": "gene_mention_evidence",
            "object_role": "curatable_unit",
            "pending_ref_id": f"gene-mention-{index}",
            "payload": {
                "mention": f"gene-{index}",
                "gene_symbol": f"sym-{index}",
                "primary_external_id": f"FB:FBgn{index:07d}",
                "taxon": "NCBITaxon:7227",
            },
            "evidence_record_ids": [],
        }
        for index in range(1, object_count + 1)
    ]
    return {
        "envelope_id": f"envelope-inline-{object_count}",
        "domain_pack_id": "gene",
        "domain_pack_version": "0.1.0",
        "status": "extracted",
        "objects": objects,
        "validation_findings": [],
        "history": [],
        "metadata": {},
    }


def _persist_inline_row(*, document_id: str, session_id: str, trace_id: str):
    """Simulate the specialist runtime's inline durable write for this turn."""

    return persist_inline_validated_extraction_result(
        payload_json=_canonical_gene_envelope(object_count=2),
        document_id=document_id,
        agent_key="gene",
        adapter_key="gene",
        tool_name="ask_gene_specialist",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id=session_id,
        trace_id=trace_id,
        user_id="user-inline-chat",
        builder_finalization={
            "builder_run_id": trace_id,
            "builder_invocation_id": "builder-invocation-inline-chat",
        },
        db=None,  # commit to the shared DB so the endpoint sees the row
    )


def _internal_extraction_result_event(persisted_ref, trace_id: str) -> dict:
    """Build the event the live runtime emits after inline persistence.

    Because it carries ``extraction_result_id``/``result_ref``, the completion
    path takes the link-only branch (no first insert).
    """

    return {
        "type": "INTERNAL_EXTRACTION_RESULT",
        "details": {
            "toolName": "ask_gene_specialist",
            "extraction_result_id": persisted_ref.extraction_result_id,
            "result_ref": persisted_ref.result_ref,
        },
        "internal": {
            "extraction_result_id": persisted_ref.extraction_result_id,
            "result_ref": persisted_ref.result_ref,
        },
    }


def test_stream_completion_links_inline_row_without_duplicate(
    client, monkeypatch, test_db, evidence_integration_context
):
    """Scenario 4 (streaming): completion links the inline row, no duplicate."""

    session_id = "inline-stream-link-session"
    turn_id = "inline-stream-link-turn"
    trace_id = "trace-inline-stream-link"
    document_id = evidence_integration_context["document_id"]

    persisted_ref = _persist_inline_row(
        document_id=document_id, session_id=session_id, trace_id=trace_id
    )

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": trace_id}}
        yield _internal_extraction_result_event(persisted_ref, trace_id)
        yield {
            "type": "TEXT_MESSAGE_CONTENT",
            "data": {"content": "Found 2 gene mentions.", "trace_id": trace_id},
        }
        yield {
            "type": "RUN_FINISHED",
            "data": {"response": "Found 2 gene mentions.", "trace_id": trace_id},
        }

    _configure_stream_mocks(
        monkeypatch,
        run_agent_streamed=_run_agent_streamed,
        document_state_payload={"id": document_id, "filename": "paper.pdf"},
        tool_agent_map={"ask_gene_specialist": "gene"},
    )

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "list genes", "session_id": session_id, "turn_id": turn_id},
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    assert events[-1]["type"] == "turn_completed"

    test_db.expire_all()
    rows = test_db.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    ).all()
    # Exactly the one inline row -- the completion path did not first-insert.
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == persisted_ref.extraction_result_id
    # The completion path link-updated final-turn metadata onto the existing row.
    final_turn = row.extraction_metadata.get("final_chat_turn")
    assert final_turn is not None
    assert final_turn["session_id"] == session_id
    assert final_turn["turn_id"] == turn_id


def test_non_stream_completion_links_inline_row_without_duplicate(
    client, monkeypatch, test_db, evidence_integration_context
):
    """Scenario 2 (non-streaming parity): same link-only behavior, no duplicate."""

    session_id = "inline-nonstream-link-session"
    turn_id = "inline-nonstream-link-turn"
    trace_id = "trace-inline-nonstream-link"
    document_id = evidence_integration_context["document_id"]

    persisted_ref = _persist_inline_row(
        document_id=document_id, session_id=session_id, trace_id=trace_id
    )

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": trace_id}}
        yield _internal_extraction_result_event(persisted_ref, trace_id)
        yield {
            "type": "RUN_FINISHED",
            "data": {"response": "Found 2 gene mentions.", "trace_id": trace_id},
        }

    _configure_stream_mocks(
        monkeypatch,
        run_agent_streamed=_run_agent_streamed,
        document_state_payload={"id": document_id, "filename": "paper.pdf"},
        tool_agent_map={"ask_gene_specialist": "gene"},
    )

    response = client.post(
        "/api/chat",
        json={"message": "list genes", "session_id": session_id, "turn_id": turn_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["response"] == "Found 2 gene mentions."

    test_db.expire_all()
    rows = test_db.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == persisted_ref.extraction_result_id
    final_turn = row.extraction_metadata.get("final_chat_turn")
    assert final_turn is not None
    assert final_turn["turn_id"] == turn_id


def test_cancel_after_inline_persistence_leaves_inspectable_row(
    client, monkeypatch, test_db, evidence_integration_context
):
    """Scenario 3: cancelling after a validated extraction leaves a durable row.

    The inline durable write happened inside the (simulated) specialist runtime
    BEFORE the stream is cancelled. The turn is interrupted before RUN_FINISHED,
    so no assistant row is saved -- but the extraction row persists and stays
    queryable/inspectable, which is the whole point of moving persistence inline.
    """

    session_id = "inline-cancel-session"
    turn_id = "inline-cancel-turn"
    trace_id = "trace-inline-cancel"
    document_id = evidence_integration_context["document_id"]

    persisted_ref = _persist_inline_row(
        document_id=document_id, session_id=session_id, trace_id=trace_id
    )

    check_calls = {"count": 0}

    async def _check_cancel_signal(_session_id) -> bool:
        # Allow the extraction event through, then cancel before RUN_FINISHED.
        check_calls["count"] += 1
        return check_calls["count"] > 1

    async def _run_agent_streamed(**_kwargs):
        yield {"type": "RUN_STARTED", "data": {"trace_id": trace_id}}
        yield _internal_extraction_result_event(persisted_ref, trace_id)
        yield {"type": "TEXT_MESSAGE_CONTENT", "data": {"content": "partial"}}
        yield {
            "type": "RUN_FINISHED",
            "data": {"response": "never reached", "trace_id": trace_id},
        }

    _configure_stream_mocks(
        monkeypatch,
        run_agent_streamed=_run_agent_streamed,
        document_state_payload={"id": document_id, "filename": "paper.pdf"},
        tool_agent_map={"ask_gene_specialist": "gene"},
        check_cancel_signal=_check_cancel_signal,
    )

    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "list genes", "session_id": session_id, "turn_id": turn_id},
    ) as stream_response:
        events = collect_sse_events(stream_response)
        assert stream_response.status_code == 200

    assert events[-1]["type"] == "turn_interrupted"

    test_db.expire_all()
    # No assistant row was saved (turn interrupted before completion).
    chat_rows = test_db.scalars(
        select(ChatMessageModel)
        .where(ChatMessageModel.session_id == session_id)
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.message_id.asc())
    ).all()
    assert [(row.role, row.content) for row in chat_rows] == [("user", "list genes")]

    # The inline-persisted extraction row survived cancellation and is queryable.
    rows = test_db.scalars(
        select(ExtractionResultModel).where(
            ExtractionResultModel.origin_session_id == session_id
        )
    ).all()
    assert len(rows) == 1
    assert str(rows[0].id) == persisted_ref.extraction_result_id
    assert len(rows[0].payload_json["extracted_objects"]) == 2
