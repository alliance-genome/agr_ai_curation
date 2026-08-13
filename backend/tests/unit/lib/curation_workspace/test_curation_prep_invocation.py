"""Unit tests for chat-driven curation prep preview and execution."""

from __future__ import annotations

import pytest

import src.lib.curation_workspace.adapter_registry as adapter_registry_module
from src.lib.curation_workspace import curation_prep_invocation as module
from src.schemas.curation_prep import CurationPrepAgentOutput, CurationPrepChatRunRequest
from src.schemas.curation_workspace import CurationExtractionResultRecord, CurationExtractionSourceKind


@pytest.fixture(autouse=True, scope="module")
def _reset_adapter_registry():
    # Package-backed registry construction costs about 1.5s. These tests do not
    # mutate registry inputs, so clear once per module instead of once per test.
    adapter_registry_module.load_curation_adapter_registry.cache_clear()
    yield
    adapter_registry_module.load_curation_adapter_registry.cache_clear()


@pytest.fixture(autouse=True)
def _default_conversation_message_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "count_session_text_messages", lambda **_kwargs: 0)


def _make_extraction_result(
    *,
    candidate_count: int = 2,
    adapter_key: str | None = "gene",
    agent_key: str = "gene_extractor",
    payload_json: dict | None = None,
    metadata: dict | None = None,
) -> CurationExtractionResultRecord:
    default_payload = (
        _make_domain_envelope_extraction_payload(candidate_count)
        if adapter_key is not None
        else {
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
    )
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


def _make_domain_envelope_extraction_payload(candidate_count: int = 2) -> dict:
    return {
        "summary": "Domain-envelope extraction fixture.",
        "curatable_objects": [
            {
                "object_type": "gene_mention_evidence",
                "object_role": "validated_reference",
                "pending_ref_id": f"gene-mention-evidence-{index + 1}",
                "model_ref": "GeneMentionEvidencePayload",
                "definition_state": "in_development",
                "payload": {
                    "mention": f"Candidate {index + 1}",
                    "gene_symbol": f"GENE{index + 1}",
                    "primary_external_id": f"EXAMPLE:{index + 1}",
                    "taxon": "NCBITaxon:10116",
                    "confidence": "high",
                    "evidence_record_id": f"evidence-{index + 1}",
                    "verified_quote": (
                        f"Candidate {index + 1} was supported by a verified observation."
                    ),
                    "page": 4 + index,
                    "section": "Results",
                    "subsection": "Observation set",
                    "chunk_id": f"chunk-alpha-{index + 1}",
                },
                "evidence_record_ids": [f"evidence-{index + 1}"],
            }
            for index in range(max(candidate_count, 0))
        ],
        "metadata": {"evidence_records": []},
        "run_summary": {"candidate_count": candidate_count},
    }


def _make_allele_domain_extraction_payload(candidate_count: int = 3) -> dict:
    curatable_objects: list[dict] = []
    evidence_records: list[dict] = []

    for index in range(candidate_count):
        object_index = index + 1
        evidence_record_id = f"evidence-{object_index}"
        mention = f"Allele mention {object_index}"
        mention_ref_id = f"allele-mention-{object_index}"
        reference_ref_id = f"paper-reference-{object_index}"
        evidence_ref_id = f"evidence-quote-{object_index}"

        curatable_objects.extend(
            [
                {
                    "object_type": "Reference",
                    "pending_ref_id": reference_ref_id,
                    "payload": {"title": "Allele fixture paper"},
                },
                {
                    "object_type": "AlleleMention",
                    "pending_ref_id": mention_ref_id,
                    "payload": {
                        "mention": {"text": mention},
                        "associated_gene": {"symbol": "Crumbs"},
                        "taxon": {"curie": "NCBITaxon:7227"},
                        "source_mentions": [mention],
                    },
                },
                {
                    "object_type": "EvidenceQuote",
                    "pending_ref_id": evidence_ref_id,
                    "payload": {
                        "evidence_record_id": evidence_record_id,
                        "verified_quote": f"{mention} was observed.",
                        "page": 5 + index,
                        "section": "Results",
                        "chunk_id": f"allele-chunk-{object_index}",
                    },
                },
                {
                    "object_type": "AllelePaperEvidenceAssociation",
                    "pending_ref_id": f"allele-paper-evidence-association-{object_index}",
                    "payload": {
                        "association_kind": "allele_paper_evidence",
                        "allele_label": mention,
                        "associated_gene": "Crumbs",
                        "confidence": "high",
                        "evidence_record_ids": [evidence_record_id],
                    },
                    "object_refs": [
                        {"pending_ref_id": reference_ref_id, "object_type": "Reference"},
                        {"pending_ref_id": mention_ref_id, "object_type": "AlleleMention"},
                        {"pending_ref_id": evidence_ref_id, "object_type": "EvidenceQuote"},
                    ],
                    "evidence_record_ids": [evidence_record_id],
                },
            ]
        )
        evidence_records.append(
            {
                "evidence_record_id": evidence_record_id,
                "entity": mention,
                "verified_quote": f"{mention} was observed.",
                "page": 5 + index,
                "section": "Results",
                "chunk_id": f"allele-chunk-{object_index}",
            }
        )

    return {
        "curatable_objects": curatable_objects,
        "metadata": {"evidence_records": evidence_records},
        "run_summary": {"candidate_count": candidate_count},
    }


def _make_prep_output(candidate_count: int = 1) -> CurationPrepAgentOutput:
    return CurationPrepAgentOutput.model_validate(
        {
            "envelope_refs": [
                {
                    "envelope_id": "env-fixture-1",
                    "envelope_revision": 1,
                    "source_extraction_result_id": "extract-1",
                    "domain_pack_id": "gene",
                    "review_row_count": candidate_count,
                }
            ],
            "review_row_count": candidate_count,
            "candidates": [],
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
    assert preview.adapter_keys == ["gene"]
    assert preview.discussed_adapter_keys == ["gene"]
    assert preview.blocking_reasons == []
    assert "You discussed 2 candidate annotations" in preview.summary_text
    assert "gene adapter" in preview.summary_text


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


def test_build_chat_curation_prep_preview_blocks_legacy_items_as_semantic_source(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "list_extraction_results",
        lambda **_kwargs: [
            _make_extraction_result(
                candidate_count=1,
                adapter_key="gene",
                payload_json={
                    "items": [
                        {
                            "label": "Candidate Alpha",
                            "entity_type": "observation",
                            "normalized_id": "OBS:0001",
                            "source_mentions": ["Alpha mention"],
                            "evidence": [
                                {
                                    "entity": "Candidate Alpha",
                                    "verified_quote": (
                                        "Candidate Alpha was supported by a verified observation."
                                    ),
                                    "section": "Results",
                                    "page": 4,
                                    "chunk_id": "chunk-alpha-1",
                                }
                            ],
                        }
                    ],
                    "run_summary": {"candidate_count": 1},
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
    assert preview.candidate_count == 1
    assert preview.preparable_candidate_count == 0
    assert preview.adapter_keys == []
    assert preview.discussed_adapter_keys == ["gene"]
    assert preview.blocking_reasons == [
        "No evidence-verified candidates were available to prepare for curation review."
    ]
    with pytest.raises(ValueError, match="No evidence-verified candidates"):
        module.validate_chat_curation_prep_request(
            session_id="session-1",
            user_id="user-1",
            db=object(),
            requested_adapter_keys=["gene"],
        )


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


def test_build_chat_curation_prep_preview_counts_allele_domain_objects_as_preparable(
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
                payload_json=_make_allele_domain_extraction_payload(candidate_count=3),
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
    assert preview.preparable_candidate_count == 8
    assert preview.adapter_keys == ["gene", "allele"]
    assert preview.discussed_adapter_keys == ["gene", "allele"]
    assert (
        preview.summary_text
        == "You discussed 5 candidate annotations across gene and allele adapters. "
        "8 evidence-verified candidate annotations across gene and allele adapters "
        "are ready to prepare for curation review."
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

    assert result.summary_text == "Prepared 2 candidate annotations for curation review in gene adapter."
    assert result.document_id == "document-1"
    assert result.candidate_count == 2
    assert result.adapter_keys == ["gene"]
    assert result.prepared_sessions == []
    assert len(captured["extraction_results"]) == 1
    assert captured["scope_confirmation"].adapter_keys == ["gene"]
    assert captured["persistence_context"].origin_session_id == "session-1"
    assert captured["persistence_context"].user_id == "user-1"
    assert captured["persistence_context"].workflow == "curation_prep_chat"


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
