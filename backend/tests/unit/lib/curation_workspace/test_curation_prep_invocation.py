"""Unit tests for chat-driven curation prep preview and execution."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace import curation_prep_invocation as module
from src.schemas.curation_prep import CurationPrepAgentOutput, CurationPrepChatRunRequest
from src.schemas.curation_workspace import CurationExtractionResultRecord, CurationExtractionSourceKind


def _make_extraction_result(
    *,
    candidate_count: int = 2,
    adapter_key: str | None = "reference_adapter",
    profile_key: str | None = "pilot",
    domain_key: str | None = "gene",
    agent_key: str = "gene_extractor",
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
            "conversation_summary": "Conversation focused on evidence-backed gene findings.",
            "payload_json": payload_json
            or {
                "annotations": [{"gene_symbol": "tinman"}],
                "evidence_records": [
                    {
                        "entity": "tinman",
                        "verified_quote": "tinman was detected in the embryonic heart.",
                        "section": "Results",
                        "subsection": "Expression analysis",
                        "page": 4,
                        "chunk_id": "chunk-tinman-1",
                    }
                ],
                "run_summary": {"candidate_count": candidate_count},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": metadata or {},
        }
    )


def _make_prep_output(candidate_count: int = 1) -> CurationPrepAgentOutput:
    return CurationPrepAgentOutput.model_validate(
        {
            "candidates": [
                {
                    "adapter_key": "gene",
                    "profile_key": "pilot",
                    "payload": {
                        "gene_symbol": f"GENE{index + 1}",
                        "anatomy_label": "embryonic heart",
                        "is_negative": False,
                    },
                    "evidence_records": [
                        {
                            "evidence_record_id": f"extract-{index + 1}:evidence:1",
                            "source": "extracted",
                            "extraction_result_id": f"extract-{index + 1}",
                            "field_paths": ["gene_symbol", "anatomy_label", "is_negative"],
                            "anchor": {
                                "anchor_kind": "snippet",
                                "locator_quality": "exact_quote",
                                "supports_decision": "supports",
                                "snippet_text": "Verified quote.",
                                "sentence_text": "Verified quote.",
                                "normalized_text": None,
                                "viewer_search_text": "Verified quote.",
                                "pdfx_markdown_offset_start": None,
                                "pdfx_markdown_offset_end": None,
                                "page_number": 4,
                                "page_label": None,
                                "section_title": "Results",
                                "subsection_title": "Expression analysis",
                                "figure_reference": None,
                                "table_reference": None,
                                "chunk_ids": ["chunk-1"],
                            },
                            "notes": [],
                        }
                    ],
                    "conversation_context_summary": "Conversation focused on evidence-backed gene findings.",
                }
                for index in range(candidate_count)
            ],
            "run_metadata": {
                "model_name": "deterministic_programmatic_mapper_v1",
                "token_usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "processing_notes": ["Deterministic prep mapper prepared 1 evidence-backed candidate."],
                "warnings": [],
            },
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

    assert preview.ready is True
    assert preview.candidate_count == 2
    assert preview.extraction_result_count == 1
    assert preview.conversation_message_count == 0
    assert preview.adapter_keys == ["reference_adapter"]
    assert preview.domain_keys == ["gene"]
    assert preview.blocking_reasons == []
    assert "You discussed 2 candidate annotations" in preview.summary_text


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

    assert preview.ready is True
    assert preview.adapter_keys == ["reference_adapter"]
    assert preview.domain_keys == ["gene"]
    assert preview.blocking_reasons == []
    assert "reference_adapter" not in preview.summary_text
    assert "gene domain" in preview.summary_text


@pytest.mark.asyncio
async def test_run_chat_curation_prep_passes_scope_confirmation_and_returns_summary(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [_make_extraction_result(candidate_count=2)],
    )

    async def _fake_run_curation_prep(
        extraction_results,
        *,
        scope_confirmation,
        db=None,
        persistence_context=None,
    ):
        captured["extraction_results"] = extraction_results
        captured["scope_confirmation"] = scope_confirmation
        captured["db"] = db
        captured["persistence_context"] = persistence_context
        return _make_prep_output(candidate_count=2)

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    result = await module.run_chat_curation_prep(
        CurationPrepChatRunRequest(session_id="session-1"),
        user_id="user-1",
        db=object(),
    )

    assert result.summary_text == "Prepared 2 candidate annotations for curation review."
    assert result.document_id == "document-1"
    assert result.candidate_count == 2
    assert result.adapter_keys == ["reference_adapter"]
    assert result.profile_keys == ["pilot"]
    assert result.domain_keys == ["gene"]
    assert len(captured["extraction_results"]) == 1
    assert captured["scope_confirmation"].adapter_keys == ["reference_adapter"]
    assert captured["scope_confirmation"].profile_keys == ["pilot"]
    assert captured["scope_confirmation"].domain_keys == ["gene"]
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
                payload_json={
                    "genes": [{"mention": "tinman"}],
                    "items": [{"label": "tinman"}],
                    "run_summary": {"candidate_count": 4},
                },
            )
        ],
    )

    async def _fake_run_curation_prep(
        _extraction_results,
        *,
        scope_confirmation,
        db=None,
        persistence_context=None,
    ):
        _ = (db, persistence_context)
        captured["scope_confirmation"] = scope_confirmation
        return _make_prep_output()

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    await module.run_chat_curation_prep(
        CurationPrepChatRunRequest(session_id="session-1"),
        user_id="user-1",
        db=object(),
    )

    assert captured["scope_confirmation"].adapter_keys == ["reference_adapter"]
    assert captured["scope_confirmation"].domain_keys == ["gene"]
