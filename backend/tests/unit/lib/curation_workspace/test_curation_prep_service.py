"""Unit tests for the deterministic curation prep service layer."""

from __future__ import annotations

import pytest

from src.lib.curation_workspace import curation_prep_service as module
from src.schemas.curation_prep import CurationPrepScopeConfirmation
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


def _make_scope_confirmation() -> CurationPrepScopeConfirmation:
    return CurationPrepScopeConfirmation(
        confirmed=True,
        adapter_keys=["reference_adapter"],
        profile_keys=["pilot"],
        domain_keys=["gene"],
        notes=["User confirmed the current chat extraction scope."],
    )


def _make_extraction_result(
    *,
    annotations: list[dict] | None = None,
    evidence_records: list[dict] | None = None,
    document_id: str = "document-1",
) -> CurationExtractionResultRecord:
    annotations = annotations or [
        {
            "gene_symbol": "tinman",
            "gene_id": "FB:FBgn0004110",
            "reagent_name": "tinman::GFP",
            "anatomy_label": "embryonic heart",
            "life_stage_label": "embryo",
            "is_negative": False,
        }
    ]
    evidence_records = evidence_records or [
        {
            "entity": "tinman",
            "verified_quote": "tinman::GFP was detected in the embryonic heart.",
            "page": 5,
            "section": "Results",
            "subsection": "Expression analysis",
            "chunk_id": "chunk-1",
            "figure_reference": "Table 2",
        }
    ]

    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-1",
            "document_id": document_id,
            "adapter_key": "reference_adapter",
            "profile_key": "pilot",
            "domain_key": "gene",
            "agent_key": "gene_extractor",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "chat-session-1",
            "trace_id": "trace-upstream",
            "flow_run_id": None,
            "user_id": "user-upstream",
            "candidate_count": len(annotations),
            "conversation_summary": "Conversation focused on evidence-backed gene findings.",
            "payload_json": {
                "organism": "D. melanogaster",
                "annotations": annotations,
                "evidence_records": evidence_records,
                "run_summary": {"candidate_count": len(annotations)},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": {},
        }
    )


@pytest.mark.asyncio
async def test_run_curation_prep_maps_gene_annotations_and_persists_output(monkeypatch):
    extraction_result = _make_extraction_result()
    captured: dict[str, object] = {}

    def _fake_persist_extraction_result(request, *, db=None):
        captured["request"] = request
        captured["db"] = db
        return None

    monkeypatch.setattr(module, "persist_extraction_result", _fake_persist_extraction_result)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=_make_scope_confirmation(),
        db=object(),
    )

    assert len(prep_output.candidates) == 1
    candidate = prep_output.candidates[0]
    assert candidate.adapter_key == "gene"
    assert candidate.profile_key == "pilot"
    assert candidate.payload == {
        "gene_symbol": "tinman",
        "gene_id": "FB:FBgn0004110",
        "organism": "D. melanogaster",
        "reagent_name": "tinman::GFP",
        "anatomy_label": "embryonic heart",
        "life_stage_label": "embryo",
        "is_negative": False,
    }
    assert candidate.evidence_records[0].field_paths == [
        "gene_symbol",
        "gene_id",
        "organism",
        "reagent_name",
        "anatomy_label",
        "life_stage_label",
        "is_negative",
    ]
    assert candidate.evidence_records[0].anchor.snippet_text == (
        "tinman::GFP was detected in the embryonic heart."
    )
    assert candidate.evidence_records[0].anchor.page_number == 5
    assert candidate.evidence_records[0].anchor.section_title == "Results"
    assert candidate.evidence_records[0].anchor.subsection_title == "Expression analysis"
    assert candidate.evidence_records[0].anchor.figure_reference is None
    assert candidate.evidence_records[0].anchor.table_reference == "Table 2"
    assert candidate.evidence_records[0].anchor.chunk_ids == ["chunk-1"]
    assert prep_output.run_metadata.model_name == "deterministic_programmatic_mapper_v1"

    persisted_request = captured["request"]
    assert persisted_request.document_id == "document-1"
    assert persisted_request.agent_key == "curation_prep"
    assert persisted_request.source_kind is CurationExtractionSourceKind.CHAT
    assert persisted_request.adapter_key == "gene"
    assert persisted_request.profile_key == "pilot"
    assert persisted_request.domain_key == "gene"
    assert persisted_request.origin_session_id == "chat-session-1"
    assert persisted_request.trace_id == "trace-upstream"
    assert persisted_request.user_id == "user-upstream"
    assert persisted_request.candidate_count == 1
    assert persisted_request.metadata["scope_adapter_keys"] == ["reference_adapter"]
    assert (
        persisted_request.metadata["final_run_metadata"]["model_name"]
        == "deterministic_programmatic_mapper_v1"
    )


@pytest.mark.asyncio
async def test_run_curation_prep_gates_candidates_without_verified_evidence(monkeypatch):
    extraction_result = _make_extraction_result(
        annotations=[
            {
                "gene_symbol": "tinman",
                "anatomy_label": "embryonic heart",
                "is_negative": False,
            },
            {
                "gene_symbol": "hand",
                "anatomy_label": "dorsal vessel",
                "is_negative": False,
            },
        ],
        evidence_records=[
            {
                "entity": "tinman",
                "verified_quote": "tinman was detected in the embryonic heart.",
                "page": 5,
                "section": "Results",
                "chunk_id": "chunk-1",
            }
        ],
    )

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=_make_scope_confirmation(),
    )

    assert [candidate.payload["gene_symbol"] for candidate in prep_output.candidates] == ["tinman"]
    assert prep_output.run_metadata.warnings == ["Skipped 1 candidate without verified evidence."]


@pytest.mark.asyncio
async def test_run_curation_prep_rejects_ambiguous_verified_evidence_without_entity(monkeypatch):
    extraction_result = _make_extraction_result(
        annotations=[
            {
                "gene_symbol": "tinman",
                "anatomy_label": "embryonic heart",
                "is_negative": False,
            },
            {
                "gene_symbol": "hand",
                "anatomy_label": "dorsal vessel",
                "is_negative": False,
            },
        ],
        evidence_records=[
            {
                "verified_quote": "tinman was detected in the embryonic heart.",
                "page": 5,
                "section": "Results",
                "chunk_id": "chunk-1",
            }
        ],
    )

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    with pytest.raises(
        ValueError,
        match="No evidence-verified candidates were available",
    ):
        await module.run_curation_prep(
            [extraction_result],
            scope_confirmation=_make_scope_confirmation(),
        )


@pytest.mark.asyncio
async def test_run_curation_prep_allows_single_candidate_verified_evidence_without_entity(monkeypatch):
    extraction_result = _make_extraction_result(
        evidence_records=[
            {
                "verified_quote": "tinman was detected in the embryonic heart.",
                "page": 5,
                "section": "Results",
                "chunk_id": "chunk-1",
            }
        ]
    )

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=_make_scope_confirmation(),
    )

    assert [candidate.payload["gene_symbol"] for candidate in prep_output.candidates] == ["tinman"]
    assert (
        prep_output.candidates[0].evidence_records[0].anchor.snippet_text
        == "tinman was detected in the embryonic heart."
    )


@pytest.mark.asyncio
async def test_run_curation_prep_rejects_when_all_candidates_fail_evidence_gate(monkeypatch):
    extraction_result = _make_extraction_result(
        evidence_records=[
            {
                "entity": "tinman",
                "page": 5,
                "section": "Results",
                "chunk_id": "chunk-1",
            }
        ]
    )

    persist_called = False

    def _fake_persist(*_args, **_kwargs):
        nonlocal persist_called
        persist_called = True

    monkeypatch.setattr(module, "persist_extraction_result", _fake_persist)

    with pytest.raises(
        ValueError,
        match="No evidence-verified candidates were available",
    ):
        await module.run_curation_prep(
            [extraction_result],
            scope_confirmation=_make_scope_confirmation(),
        )

    assert persist_called is False


@pytest.mark.asyncio
async def test_run_curation_prep_rejects_mismatched_document_id_override(monkeypatch):
    extraction_result = _make_extraction_result()

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="must match"):
        await module.run_curation_prep(
            [extraction_result],
            scope_confirmation=_make_scope_confirmation(),
            persistence_context=module.CurationPrepPersistenceContext(document_id="document-2"),
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
