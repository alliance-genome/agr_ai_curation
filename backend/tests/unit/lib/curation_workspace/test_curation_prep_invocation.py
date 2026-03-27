"""Unit tests for chat-driven curation prep preview and execution."""

from types import SimpleNamespace

import pytest

from src.lib.curation_workspace import curation_prep_invocation as module
from src.schemas.curation_prep import CurationPrepAgentOutput, CurationPrepChatRunRequest
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
                "items": [
                    {
                        "label": "APOE",
                        "evidence_records": [
                            {
                                "snippet": "APOE was implicated in the disease model.",
                                "section": "Results",
                                "subsection": "Disease findings",
                                "page": 4,
                                "figure_reference": "Fig. 2",
                            }
                        ],
                    }
                ],
                "run_summary": {"candidate_count": candidate_count},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": metadata or {},
        }
    )


def _make_prep_output(candidate_count: int = 2) -> CurationPrepAgentOutput:
    return CurationPrepAgentOutput.model_validate(
        {
            "candidates": [
                {
                    "adapter_key": "reference_adapter",
                    "profile_key": "primary",
                    "extracted_fields": [
                        {
                            "field_path": "gene_symbol",
                            "value_type": "string",
                            "string_value": "APOE",
                            "number_value": None,
                            "boolean_value": None,
                            "json_value": None,
                        }
                    ],
                    "evidence_references": [
                        {
                            "field_path": "gene_symbol",
                            "evidence_record_id": "extract-1:evidence:1",
                            "extraction_result_id": "extract-1",
                            "anchor": {
                                "anchor_kind": "snippet",
                                "locator_quality": "exact_quote",
                                "supports_decision": "supports",
                                "snippet_text": "APOE was implicated in the disease model.",
                                "sentence_text": "APOE was implicated in the disease model.",
                                "viewer_search_text": "APOE was implicated in the disease model.",
                                "page_number": 4,
                                "section_title": "Results",
                                "subsection_title": "Disease findings",
                                "figure_reference": "Fig. 2",
                                "chunk_ids": [],
                            },
                            "rationale": "The retained evidence explicitly references APOE.",
                        }
                    ],
                    "conversation_context_summary": "Conversation narrowed to disease findings for APOE.",
                    "confidence": 0.91,
                    "unresolved_ambiguities": [],
                }
            ]
            * candidate_count,
            "run_metadata": {
                "model_name": "gpt-5-mini",
                "token_usage": {
                    "input_tokens": 10,
                    "output_tokens": 12,
                    "total_tokens": 22,
                },
                "processing_notes": ["Prepared from chat extraction context."],
                "warnings": ["Review evidence alignment before downstream normalization."],
            },
        }
    )


def test_build_chat_curation_prep_preview_summarizes_scope(monkeypatch):
    monkeypatch.setattr(
        module,
        "conversation_manager",
        SimpleNamespace(
            get_session_stats=lambda _user_id, _session_id: {
                "history": [
                    {"user": "Prepare disease annotations for APOE", "assistant": "I found two disease candidates."}
                ]
            }
        ),
    )
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
    assert preview.conversation_message_count == 2
    assert preview.adapter_keys == ["reference_adapter"]
    assert preview.domain_keys == ["disease"]
    assert "You discussed 2 candidate annotations" in preview.summary_text


def test_build_chat_curation_prep_preview_blocks_when_no_candidates(monkeypatch):
    monkeypatch.setattr(
        module,
        "conversation_manager",
        SimpleNamespace(get_session_stats=lambda _user_id, _session_id: {"history": []}),
    )
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
        "conversation_manager",
        SimpleNamespace(
            get_session_stats=lambda _user_id, _session_id: {
                "history": [
                    {"user": "Prepare the central gene findings", "assistant": "I found four gene candidates."}
                ]
            }
        ),
    )
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
                    "raw_mentions": [{"mention": "tinman", "evidence": []}],
                    "exclusions": [],
                    "ambiguities": [],
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
async def test_run_chat_curation_prep_builds_agent_input_and_returns_summary(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        module,
        "conversation_manager",
        SimpleNamespace(
            get_session_stats=lambda _user_id, _session_id: {
                "history": [
                    {"user": "Prepare disease annotations for APOE", "assistant": "I found two disease candidates."}
                ]
            }
        ),
    )
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [_make_extraction_result(candidate_count=2)],
    )

    async def _fake_run_curation_prep(agent_input, *, db=None, persistence_context=None):
        captured["agent_input"] = agent_input
        captured["db"] = db
        captured["persistence_context"] = persistence_context
        return _make_prep_output(candidate_count=2)

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    result = await module.run_chat_curation_prep(
        CurationPrepChatRunRequest(session_id="session-1"),
        user_id="user-1",
        db=object(),
    )

    agent_input = captured["agent_input"]
    assert len(agent_input.conversation_history) == 2
    assert agent_input.scope_confirmation.adapter_keys == ["reference_adapter"]
    assert agent_input.scope_confirmation.profile_keys == ["primary"]
    assert agent_input.scope_confirmation.domain_keys == ["disease"]
    assert len(agent_input.adapter_metadata) == 1
    assert agent_input.adapter_metadata[0].adapter_key == "reference_adapter"
    assert len(agent_input.evidence_records) == 1
    assert agent_input.evidence_records[0].anchor.page_number == 4
    assert captured["persistence_context"].origin_session_id == "session-1"
    assert captured["persistence_context"].user_id == "user-1"

    assert result.candidate_count == 2
    assert result.adapter_keys == ["reference_adapter"]
    assert result.warnings == ["Review evidence alignment before downstream normalization."]
    assert "Prepared 2 candidate annotations for curation review." == result.summary_text


@pytest.mark.asyncio
async def test_run_chat_curation_prep_infers_scope_from_unscoped_results(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        module,
        "conversation_manager",
        SimpleNamespace(
            get_session_stats=lambda _user_id, _session_id: {
                "history": [
                    {"user": "Prepare the central gene findings", "assistant": "I found four gene candidates."}
                ]
            }
        ),
    )
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
                    "evidence_records": [
                        {
                            "snippet": "tinman controls cardiogenesis in embryos.",
                            "section": "Results",
                            "page": 3,
                        }
                    ],
                    "raw_mentions": [{"mention": "tinman", "evidence": []}],
                    "exclusions": [],
                    "ambiguities": [],
                    "run_summary": {"candidate_count": 4},
                },
            )
        ],
    )

    async def _fake_run_curation_prep(agent_input, *, db=None, persistence_context=None):
        captured["agent_input"] = agent_input
        captured["db"] = db
        captured["persistence_context"] = persistence_context
        return _make_prep_output(candidate_count=1)

    monkeypatch.setattr(module, "run_curation_prep", _fake_run_curation_prep)

    result = await module.run_chat_curation_prep(
        CurationPrepChatRunRequest(session_id="session-1"),
        user_id="user-1",
        db=object(),
    )

    agent_input = captured["agent_input"]
    assert agent_input.scope_confirmation.adapter_keys == ["reference_adapter"]
    assert agent_input.scope_confirmation.domain_keys == ["gene"]
    assert agent_input.adapter_metadata[0].adapter_key == "reference_adapter"
    assert result.adapter_keys == ["reference_adapter"]
