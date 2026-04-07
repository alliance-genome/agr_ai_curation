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
    agent_key: str = "observation_extractor",
    payload_json: dict | None = None,
    metadata: dict | None = None,
) -> CurationExtractionResultRecord:
    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-1",
            "document_id": "document-1",
            "adapter_key": adapter_key,
            "agent_key": agent_key,
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "session-1",
            "trace_id": "trace-1",
            "flow_run_id": None,
            "user_id": "user-1",
            "candidate_count": candidate_count,
            "conversation_summary": "Conversation focused on evidence-backed extraction findings.",
            "payload_json": payload_json
            or {
                "items": [
                    {
                        "label": "Candidate Alpha",
                        "entity_type": "observation",
                        "normalized_id": "OBS:0001",
                        "source_mentions": ["Alpha mention"],
                        "evidence": [
                            {
                                "entity": "Candidate Alpha",
                                "verified_quote": "Candidate Alpha was supported by a verified observation.",
                                "section": "Results",
                                "subsection": "Observation set",
                                "page": 4,
                                "chunk_id": "chunk-alpha-1",
                            }
                        ],
                    }
                ],
                "evidence_records": [
                    {
                        "entity": "Candidate Alpha",
                        "verified_quote": "Candidate Alpha was supported by a verified observation.",
                        "section": "Results",
                        "subsection": "Observation set",
                        "page": 4,
                        "chunk_id": "chunk-alpha-1",
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
                    "adapter_key": "observation",
                    "payload": {
                        "label": f"Candidate {index + 1}",
                        "entity_type": "observation",
                        "normalized_id": f"OBS:000{index + 1}",
                        "source_mentions": [f"Mention {index + 1}"],
                    },
                    "evidence_records": [
                        {
                            "evidence_record_id": f"extract-{index + 1}:candidate:1:evidence:1",
                            "source": "extracted",
                            "extraction_result_id": f"extract-{index + 1}",
                            "field_paths": [
                                "label",
                                "entity_type",
                                "normalized_id",
                                "source_mentions.0",
                            ],
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
                                "subsection_title": "Observation set",
                                "figure_reference": None,
                                "table_reference": None,
                                "chunk_ids": ["chunk-1"],
                            },
                            "notes": [],
                        }
                    ],
                    "conversation_context_summary": (
                        "Conversation focused on evidence-backed extraction findings."
                    ),
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
    assert preview.submit_adapter_keys == ["reference_adapter"]
    assert preview.requires_adapter_selection is False
    assert preview.blocking_reasons == []
    assert "You discussed 2 candidate annotations" in preview.summary_text
    assert "reference adapter" in preview.summary_text


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


def test_build_chat_curation_prep_preview_blocks_when_adapter_scope_is_missing(monkeypatch):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(
                candidate_count=4,
                adapter_key=None,
                payload_json={
                    "items": [
                        {
                            "label": "Candidate Alpha",
                            "entity_type": "observation",
                            "normalized_id": "OBS:0001",
                            "source_mentions": ["Alpha mention"],
                            "evidence": [],
                        }
                    ],
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
    assert preview.adapter_keys == []
    assert preview.submit_adapter_keys == []
    assert preview.requires_adapter_selection is False
    assert preview.blocking_reasons == [
        "The current chat extraction results do not include adapter scope, so prep cannot determine what to prepare."
    ]
    assert preview.summary_text == preview.blocking_reasons[0]


def test_build_chat_curation_prep_preview_blocks_when_multiple_adapters_are_present(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(adapter_key="gene"),
            _make_extraction_result(
                adapter_key="disease",
                agent_key="disease_extractor",
            ),
        ],
    )

    preview = module.build_chat_curation_prep_preview(
        session_id="session-1",
        user_id="user-1",
        db=object(),
    )

    assert preview.ready is False
    assert preview.adapter_keys == ["gene", "disease"]
    assert preview.submit_adapter_keys == []
    assert preview.requires_adapter_selection is True
    assert preview.blocking_reasons == [
        "This chat includes findings for multiple adapters. Narrow the extraction scope to one adapter before preparing for curation review."
    ]


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
    assert len(captured["extraction_results"]) == 1
    assert captured["scope_confirmation"].adapter_keys == ["reference_adapter"]
    assert captured["persistence_context"].origin_session_id == "session-1"
    assert captured["persistence_context"].user_id == "user-1"


@pytest.mark.asyncio
async def test_run_chat_curation_prep_blocks_when_adapter_scope_is_missing(monkeypatch):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(
                candidate_count=4,
                adapter_key=None,
                payload_json={
                    "items": [
                        {
                            "label": "Candidate Alpha",
                            "entity_type": "observation",
                            "normalized_id": "OBS:0001",
                            "source_mentions": ["Alpha mention"],
                            "evidence": [],
                        }
                    ],
                    "run_summary": {"candidate_count": 4},
                },
            )
        ],
    )

    async def _fake_run_curation_prep(*_args, **_kwargs):
        raise AssertionError("run_curation_prep should not be called when adapter scope is missing")

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    with pytest.raises(ValueError, match="do not include adapter scope"):
        await module.run_chat_curation_prep(
            CurationPrepChatRunRequest(session_id="session-1"),
            user_id="user-1",
            db=object(),
        )


@pytest.mark.asyncio
async def test_run_chat_curation_prep_allows_explicit_adapter_narrowing(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(adapter_key="gene"),
            _make_extraction_result(
                adapter_key="disease",
                agent_key="disease_extractor",
            ),
        ],
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
        return _make_prep_output(candidate_count=1)

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    result = await module.run_chat_curation_prep(
        CurationPrepChatRunRequest(session_id="session-1", adapter_keys=["gene"]),
        user_id="user-1",
        db=object(),
    )

    assert result.adapter_keys == ["gene"]
    assert captured["scope_confirmation"].adapter_keys == ["gene"]


@pytest.mark.asyncio
async def test_run_chat_curation_prep_rejects_multiple_requested_adapters(monkeypatch):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(adapter_key="gene"),
            _make_extraction_result(
                adapter_key="disease",
                agent_key="disease_extractor",
            ),
        ],
    )

    async def _fake_run_curation_prep(*_args, **_kwargs):
        raise AssertionError("run_curation_prep should not be called for multi-adapter chat prep")

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    with pytest.raises(ValueError, match="exactly one adapter scope"):
        await module.run_chat_curation_prep(
            CurationPrepChatRunRequest(
                session_id="session-1",
                adapter_keys=["gene", "disease"],
            ),
            user_id="user-1",
            db=object(),
        )
