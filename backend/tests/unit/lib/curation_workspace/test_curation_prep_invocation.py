"""Unit tests for chat-driven curation prep preview and execution."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_UNAVAILABLE_MESSAGE
from src.lib.curation_workspace import curation_prep_invocation as module
from src.schemas.curation_prep import CurationPrepChatRunRequest
from src.schemas.curation_workspace import CurationExtractionResultRecord, CurationExtractionSourceKind


def _make_extraction_result(
    *,
    candidate_count: int = 2,
    adapter_key: str | None = "reference_adapter",
    profile_key: str | None = "primary",
    domain_key: str | None = "disease",
    agent_key: str = "disease_extractor",
    payload_json: dict | None = None,
    metadata: dict | None = None,
) -> CurationExtractionResultRecord:
    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-1",
            "document_id": "document-1",
            "adapter_key": adapter_key,
            "profile_key": profile_key,
            "domain_key": domain_key,
            "agent_key": agent_key,
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "session-1",
            "trace_id": "trace-1",
            "flow_run_id": None,
            "user_id": "user-1",
            "candidate_count": candidate_count,
            "conversation_summary": "Conversation focused on disease findings.",
            "payload_json": payload_json
            or {
                "items": [{"label": "APOE"}],
                "run_summary": {"candidate_count": candidate_count},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": metadata or {},
        }
    )


def test_build_chat_curation_prep_preview_summarizes_scope(monkeypatch):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [_make_extraction_result(candidate_count=2)],
    )

    preview = module.build_chat_curation_prep_preview(
        session_id="session-1",
        user_id="user-1",
        db=object(),
    )

    assert preview.ready is False
    assert preview.candidate_count == 2
    assert preview.extraction_result_count == 1
    assert preview.conversation_message_count == 0
    assert preview.adapter_keys == ["reference_adapter"]
    assert preview.domain_keys == ["disease"]
    assert "You discussed 2 candidate annotations" in preview.summary_text
    assert preview.blocking_reasons == [CURATION_PREP_UNAVAILABLE_MESSAGE]


def test_build_chat_curation_prep_preview_blocks_when_no_candidates(monkeypatch):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [_make_extraction_result(candidate_count=0)],
    )

    preview = module.build_chat_curation_prep_preview(
        session_id="session-1",
        user_id="user-1",
        db=object(),
    )

    assert preview.ready is False
    assert preview.blocking_reasons == [
        "This chat has extraction context, but it did not retain any candidate annotations to prepare yet."
    ]


def test_build_chat_curation_prep_preview_infers_scope_from_unscoped_results(monkeypatch):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(
                candidate_count=4,
                adapter_key=None,
                profile_key=None,
                domain_key=None,
                agent_key="gene_extractor",
                payload_json={
                    "genes": [{"mention": "tinman"}],
                    "items": [{"label": "tinman"}],
                    "run_summary": {"candidate_count": 4},
                },
            )
        ],
    )

    preview = module.build_chat_curation_prep_preview(
        session_id="session-1",
        user_id="user-1",
        db=object(),
    )

    assert preview.ready is False
    assert preview.adapter_keys == ["reference_adapter"]
    assert preview.domain_keys == ["gene"]
    assert preview.blocking_reasons == [CURATION_PREP_UNAVAILABLE_MESSAGE]
    assert "reference_adapter" not in preview.summary_text
    assert "gene domain" in preview.summary_text


@pytest.mark.asyncio
async def test_run_chat_curation_prep_maps_unavailable_error_to_value_error(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [_make_extraction_result(candidate_count=2)],
    )

    async def _fake_run_curation_prep(extraction_results, *, scope_confirmation, db=None, persistence_context=None):
        captured["extraction_results"] = extraction_results
        captured["scope_confirmation"] = scope_confirmation
        captured["db"] = db
        captured["persistence_context"] = persistence_context
        raise RuntimeError(CURATION_PREP_UNAVAILABLE_MESSAGE)

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    with pytest.raises(ValueError, match="temporarily unavailable"):
        await module.run_chat_curation_prep(
            CurationPrepChatRunRequest(session_id="session-1"),
            user_id="user-1",
            db=object(),
        )

    assert len(captured["extraction_results"]) == 1
    assert captured["scope_confirmation"].adapter_keys == ["reference_adapter"]
    assert captured["scope_confirmation"].profile_keys == ["primary"]
    assert captured["scope_confirmation"].domain_keys == ["disease"]
    assert captured["persistence_context"].origin_session_id == "session-1"
    assert captured["persistence_context"].user_id == "user-1"


@pytest.mark.asyncio
async def test_run_chat_curation_prep_infers_scope_from_unscoped_results(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(
                candidate_count=4,
                adapter_key=None,
                profile_key=None,
                domain_key=None,
                agent_key="gene_extractor",
                payload_json={
                    "genes": [{"mention": "tinman"}],
                    "items": [{"label": "tinman"}],
                    "run_summary": {"candidate_count": 4},
                },
            )
        ],
    )

    async def _fake_run_curation_prep(extraction_results, *, scope_confirmation, db=None, persistence_context=None):
        captured["scope_confirmation"] = scope_confirmation
        raise RuntimeError(CURATION_PREP_UNAVAILABLE_MESSAGE)

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    with pytest.raises(ValueError, match="temporarily unavailable"):
        await module.run_chat_curation_prep(
            CurationPrepChatRunRequest(session_id="session-1"),
            user_id="user-1",
            db=object(),
        )

    assert captured["scope_confirmation"].adapter_keys == ["reference_adapter"]
    assert captured["scope_confirmation"].domain_keys == ["gene"]
