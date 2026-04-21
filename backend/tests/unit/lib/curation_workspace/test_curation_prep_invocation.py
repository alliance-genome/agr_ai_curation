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
    default_payload = {
        "items": [
            {
                "label": f"Candidate {index + 1}",
                "entity_type": "observation",
                "normalized_id": f"OBS:{index + 1:04d}",
                "source_mentions": [f"Mention {index + 1}"],
                "evidence": [
                    {
                        "entity": f"Candidate {index + 1}",
                        "verified_quote": (
                            f"Candidate {index + 1} was supported by a verified observation."
                        ),
                        "section": "Results",
                        "subsection": "Observation set",
                        "page": 4 + index,
                        "chunk_id": f"chunk-alpha-{index + 1}",
                    }
                ],
            }
            for index in range(max(candidate_count, 0))
        ],
        "evidence_records": [
            {
                "entity": f"Candidate {index + 1}",
                "verified_quote": (
                    f"Candidate {index + 1} was supported by a verified observation."
                ),
                "section": "Results",
                "subsection": "Observation set",
                "page": 4 + index,
                "chunk_id": f"chunk-alpha-{index + 1}",
            }
            for index in range(max(candidate_count, 0))
        ],
        "run_summary": {"candidate_count": candidate_count},
    }
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
            "payload_json": payload_json or default_payload,
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
    monkeypatch.setattr(
        module,
        "count_session_text_messages",
        lambda **_kwargs: 6,
    )

    preview = module.build_chat_curation_prep_preview(
        session_id="session-1",
        user_id="user-1",
        db=object(),
    )

    assert preview.ready is True
    assert preview.candidate_count == 2
    assert preview.preparable_candidate_count == 2
    assert preview.extraction_result_count == 1
    assert preview.conversation_message_count == 6
    assert preview.adapter_keys == ["reference_adapter"]
    assert preview.discussed_adapter_keys == ["reference_adapter"]
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
    assert preview.preparable_candidate_count == 0
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
    assert preview.discussed_adapter_keys == []
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

    assert preview.ready is True
    assert preview.adapter_keys == ["gene", "disease"]
    assert preview.discussed_adapter_keys == ["gene", "disease"]
    assert preview.preparable_candidate_count == 4
    assert preview.blocking_reasons == []
    assert (
        preview.summary_text
        == "You discussed 4 candidate annotations across gene and disease adapters. Prepare all for curation review?"
    )


def test_build_chat_curation_prep_preview_blocks_when_no_evidence_verified_candidates_exist(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(
                candidate_count=5,
                adapter_key="gene",
                payload_json={
                    "items": [
                        {
                            "label": f"Candidate {index + 1}",
                            "entity_type": "observation",
                            "normalized_id": f"OBS:{index + 1:04d}",
                            "source_mentions": [f"Mention {index + 1}"],
                            "evidence": [
                                {
                                    "entity": f"Candidate {index + 1}",
                                    "page": 4 + index,
                                    "section": "Results",
                                    "chunk_id": f"chunk-{index + 1}",
                                }
                            ],
                        }
                        for index in range(5)
                    ],
                    "run_summary": {"candidate_count": 5},
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
    assert preview.candidate_count == 5
    assert preview.preparable_candidate_count == 0
    assert preview.adapter_keys == []
    assert preview.discussed_adapter_keys == ["gene"]
    assert preview.blocking_reasons == [
        "No evidence-verified candidates were available to prepare for curation review."
    ]
    assert preview.summary_text == preview.blocking_reasons[0]


def test_build_chat_curation_prep_preview_filters_to_preparable_adapters(monkeypatch):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(candidate_count=2, adapter_key="gene"),
            _make_extraction_result(
                candidate_count=3,
                adapter_key="allele",
                agent_key="allele_extractor",
                payload_json={
                    "items": [
                        {
                            "label": f"Allele Candidate {index + 1}",
                            "entity_type": "observation",
                            "normalized_id": f"ALL:{index + 1:04d}",
                            "source_mentions": [f"Allele mention {index + 1}"],
                            "evidence": [
                                {
                                    "entity": f"Allele Candidate {index + 1}",
                                    "page": 5 + index,
                                    "section": "Results",
                                    "chunk_id": f"allele-chunk-{index + 1}",
                                }
                            ],
                        }
                        for index in range(3)
                    ],
                    "run_summary": {"candidate_count": 3},
                },
            ),
        ],
    )

    preview = module.build_chat_curation_prep_preview(
        session_id="session-1",
        user_id="user-1",
        db=object(),
    )

    assert preview.ready is True
    assert preview.candidate_count == 5
    assert preview.preparable_candidate_count == 2
    assert preview.adapter_keys == ["gene"]
    assert preview.discussed_adapter_keys == ["gene", "allele"]
    assert (
        preview.summary_text
        == "You discussed 5 candidate annotations across gene and allele adapters. "
        "2 evidence-verified candidate annotations in gene adapter are ready to prepare for curation review."
    )


def test_build_chat_curation_prep_preview_counts_specialized_allele_payloads_as_preparable(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(candidate_count=2, adapter_key="gene"),
            _make_extraction_result(
                candidate_count=3,
                adapter_key="allele",
                agent_key="allele_extractor",
                payload_json={
                    "items": [
                        {
                            "label": None,
                            "entity_type": None,
                            "normalized_id": None,
                            "source_mentions": [],
                            "evidence_record_ids": [],
                        }
                    ],
                    "alleles": [
                        {
                            "mention": f"Allele mention {index + 1}",
                            "normalized_id": None,
                            "normalized_symbol": None,
                            "associated_gene": "Crumbs",
                            "confidence": "high",
                            "evidence_record_ids": [f"evidence-{index + 1}"],
                        }
                        for index in range(3)
                    ],
                    "evidence_records": [
                        {
                            "evidence_record_id": f"evidence-{index + 1}",
                            "entity": f"Allele mention {index + 1}",
                            "verified_quote": f"Allele mention {index + 1} was observed.",
                            "page": 5 + index,
                            "section": "Results",
                            "chunk_id": f"allele-chunk-{index + 1}",
                        }
                        for index in range(3)
                    ],
                    "run_summary": {"candidate_count": 3},
                },
            ),
        ],
    )

    preview = module.build_chat_curation_prep_preview(
        session_id="session-1",
        user_id="user-1",
        db=object(),
    )

    assert preview.ready is True
    assert preview.candidate_count == 5
    assert preview.preparable_candidate_count == 5
    assert preview.adapter_keys == ["gene", "allele"]
    assert preview.discussed_adapter_keys == ["gene", "allele"]
    assert (
        preview.summary_text
        == "You discussed 5 candidate annotations across gene and allele adapters. "
        "Prepare all for curation review?"
    )


def test_build_chat_curation_prep_preview_reports_unscoped_candidates(monkeypatch):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(candidate_count=2, adapter_key="gene"),
            _make_extraction_result(
                candidate_count=3,
                adapter_key=None,
                payload_json={
                    "items": [
                        {
                            "label": f"Loose Candidate {index + 1}",
                            "entity_type": "observation",
                            "normalized_id": f"LOOSE:{index + 1:04d}",
                            "source_mentions": [f"Loose mention {index + 1}"],
                            "evidence": [
                                {
                                    "entity": f"Loose Candidate {index + 1}",
                                    "verified_quote": f"Loose candidate {index + 1} was observed.",
                                    "page": 7 + index,
                                    "section": "Results",
                                    "chunk_id": f"loose-chunk-{index + 1}",
                                }
                            ],
                        }
                        for index in range(3)
                    ],
                    "run_summary": {"candidate_count": 3},
                },
            ),
        ],
    )

    preview = module.build_chat_curation_prep_preview(
        session_id="session-1",
        user_id="user-1",
        db=object(),
    )

    assert preview.ready is True
    assert preview.candidate_count == 5
    assert preview.preparable_candidate_count == 2
    assert preview.unscoped_candidate_count == 3
    assert preview.adapter_keys == ["gene"]
    assert preview.discussed_adapter_keys == ["gene"]
    assert (
        preview.summary_text
        == "You discussed 5 candidate annotations. "
        "2 evidence-verified candidate annotations in gene adapter are ready to prepare for curation review. "
        "3 additional candidate annotations did not retain adapter scope and cannot be prepared from this chat."
    )


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

    assert result.summary_text == "Prepared 2 candidate annotations for curation review in reference adapter."
    assert result.document_id == "document-1"
    assert result.candidate_count == 2
    assert result.adapter_keys == ["reference_adapter"]
    assert result.prepared_sessions == []
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
async def test_run_chat_curation_prep_prepares_all_adapters_in_scope(monkeypatch):
    captured_scope_keys: list[list[str]] = []

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
        captured_scope_keys.append(list(scope_confirmation.adapter_keys))
        return _make_prep_output(candidate_count=1)

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    result = await module.run_chat_curation_prep(
        CurationPrepChatRunRequest(session_id="session-1"),
        user_id="user-1",
        db=object(),
    )

    assert captured_scope_keys == [["gene"], ["disease"]]
    assert result.summary_text == (
        "Prepared 2 candidate annotations for curation review across gene and disease adapters."
    )
    assert result.candidate_count == 2
    assert result.adapter_keys == ["gene", "disease"]
    assert result.prepared_sessions == []


@pytest.mark.asyncio
async def test_run_chat_curation_prep_allows_explicit_adapter_subset(monkeypatch):
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
