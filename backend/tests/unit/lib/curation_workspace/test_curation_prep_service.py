"""Unit tests for the curation prep service layer."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace import curation_prep_service as module
from src.schemas.curation_prep import CurationPrepScopeConfirmation
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


def _make_extraction_result_payload() -> dict[str, object]:
    return {
        "extraction_result_id": "extract-1",
        "document_id": "document-1",
        "adapter_key": "disease",
        "profile_key": "primary",
        "domain_key": "disease",
        "agent_key": "pdf_extraction",
        "source_kind": CurationExtractionSourceKind.CHAT,
        "origin_session_id": "chat-session-1",
        "trace_id": "trace-upstream",
        "flow_run_id": None,
        "user_id": "user-upstream",
        "candidate_count": 1,
        "conversation_summary": "Conversation focused on APOE disease relevance.",
        "payload_json": {
            "items": [{"gene_symbol": "APOE"}],
            "run_summary": {"candidate_count": 1},
        },
        "created_at": "2026-03-20T21:55:00Z",
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_run_curation_prep_is_temporarily_unavailable():
    extraction_result = CurationExtractionResultRecord.model_validate(_make_extraction_result_payload())
    scope_confirmation = CurationPrepScopeConfirmation(
        confirmed=True,
        adapter_keys=["disease"],
        profile_keys=["primary"],
        domain_keys=["disease"],
        notes=["User confirmed the disease adapter scope."],
    )

    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        await module.run_curation_prep(
            [extraction_result],
            scope_confirmation=scope_confirmation,
        )


def test_curation_prep_persistence_context_keeps_optional_fields():
    context = module.CurationPrepPersistenceContext(
        document_id="document-1",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="chat-session-1",
        trace_id="trace-1",
        flow_run_id="flow-1",
        user_id="user-1",
        conversation_summary="Conversation summary.",
    )

    assert context.document_id == "document-1"
    assert context.source_kind is CurationExtractionSourceKind.CHAT
    assert context.flow_run_id == "flow-1"
