"""Unit tests for the deterministic curation prep service layer."""

from __future__ import annotations

import pytest

import src.lib.curation_workspace.adapter_registry as adapter_registry_module
from src.lib.curation_workspace import curation_prep_service as module
from src.schemas.curation_prep import CurationPrepEnvelopeRef, CurationPrepScopeConfirmation
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    adapter_registry_module.load_curation_adapter_registry.cache_clear()
    yield
    adapter_registry_module.load_curation_adapter_registry.cache_clear()


def _make_scope_confirmation() -> CurationPrepScopeConfirmation:
    return CurationPrepScopeConfirmation(
        confirmed=True,
        adapter_keys=["gene"],
        notes=["User confirmed the current chat extraction scope."],
    )


def _make_item(
    *,
    label: str | None = "Candidate Alpha",
    entity_type: str | None = "observation",
    normalized_id: str | None = "OBS:0001",
    source_mentions: list[str] | None = None,
    evidence_record_ids: list[str] | None = None,
    evidence: list[dict] | None = None,
) -> dict:
    return {
        "label": label,
        "entity_type": entity_type,
        "normalized_id": normalized_id,
        "source_mentions": source_mentions if source_mentions is not None else ["Alpha mention"],
        "evidence_record_ids": evidence_record_ids if evidence_record_ids is not None else ["evidence-1"],
        "evidence": evidence if evidence is not None else [],
    }


def _make_evidence_record(
    *,
    evidence_record_id: str = "evidence-1",
    entity: str = "Candidate Alpha",
    verified_quote: str = "Candidate Alpha was supported by a verified observation.",
    page: int = 5,
    section: str = "Results",
    subsection: str = "Observation set",
    chunk_id: str = "chunk-1",
    figure_reference: str = "Table 2",
) -> dict:
    return {
        "evidence_record_id": evidence_record_id,
        "entity": entity,
        "verified_quote": verified_quote,
        "page": page,
        "section": section,
        "subsection": subsection,
        "chunk_id": chunk_id,
        "figure_reference": figure_reference,
    }


def _make_extraction_result(
    *,
    items: list[dict] | None = None,
    evidence_records: list[dict] | None = None,
    document_id: str = "document-1",
    adapter_key: str | None = "reference_adapter",
    conversation_summary: str | None = (
        "Conversation focused on evidence-backed extraction findings."
    ),
) -> CurationExtractionResultRecord:
    items = items or [_make_item()]
    evidence_records = evidence_records or [_make_evidence_record()]

    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-1",
            "document_id": document_id,
            "adapter_key": adapter_key,
            "agent_key": "observation_extractor",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "chat-session-1",
            "trace_id": "trace-upstream",
            "flow_run_id": None,
            "user_id": "user-upstream",
            "candidate_count": len(items),
            "conversation_summary": conversation_summary,
            "payload_json": {
                "items": items,
                "evidence_records": evidence_records,
                "run_summary": {"candidate_count": len(items)},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": {},
        }
    )


def _make_domain_envelope_extraction_result(
    *,
    document_id: str = "document-1",
    adapter_key: str = "gene",
    candidate_count: int = 1,
) -> CurationExtractionResultRecord:
    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-domain-1",
            "document_id": document_id,
            "adapter_key": adapter_key,
            "agent_key": "gene_extractor",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "chat-session-1",
            "trace_id": "trace-upstream",
            "flow_run_id": None,
            "user_id": "user-upstream",
            "candidate_count": candidate_count,
            "conversation_summary": "Conversation focused on domain envelopes.",
            "payload_json": {
                "summary": "One gene mention was extracted.",
                "curatable_objects": [
                    {
                        "object_type": "gene_mention_evidence",
                        "object_role": "validated_reference",
                        "object_id": "gene-row-1",
                        "payload": {
                            "mention": "abc-1",
                            "gene_symbol": "ABC-1",
                            "primary_external_id": "EXAMPLE:1",
                            "taxon": "NCBITaxon:10116",
                            "confidence": "high",
                            "evidence_record_id": "evidence-1",
                            "verified_quote": "abc-1 was observed in the paper.",
                            "page": 5,
                            "section": "Results",
                            "chunk_id": "chunk-1",
                        },
                        "evidence_record_ids": ["evidence-1"],
                    }
                ],
                "metadata": {
                    "evidence_records": [_make_evidence_record()],
                },
                "run_summary": {"candidate_count": candidate_count},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": {"project_key": "agr"},
        }
    )


def _make_allele_domain_payload(
    *,
    label: str,
    normalized_id: str,
    associated_gene: str = "Crumbs",
    evidence_record_ids: list[str] | None = None,
    index: int = 1,
) -> dict:
    evidence_record_ids = (
        evidence_record_ids if evidence_record_ids is not None else [f"evidence-{index}"]
    )
    mention_ref_id = f"allele-mention-{index}"
    allele_ref_id = f"allele-reference-{index}"
    reference_ref_id = f"paper-reference-{index}"
    evidence_ref_ids = [
        f"evidence-quote-{index}-{evidence_index}"
        for evidence_index, _evidence_id in enumerate(evidence_record_ids, start=1)
    ]

    return {
        "supporting_objects": [
            {
                "object_type": "Reference",
                "pending_ref_id": reference_ref_id,
                "payload": {"title": "Allele extraction fixture paper"},
            },
            {
                "object_type": "AlleleMention",
                "pending_ref_id": mention_ref_id,
                "payload": {
                    "mention_text": label,
                    "normalized_id": normalized_id,
                    "source_mentions": [label],
                },
            },
            {
                "object_type": "Allele",
                "pending_ref_id": allele_ref_id,
                "payload": {
                    "primary_external_id": normalized_id,
                    "allele_symbol": label,
                    "source_mentions": [label],
                },
            },
            *[
                {
                    "object_type": "EvidenceQuote",
                    "pending_ref_id": evidence_ref_id,
                    "payload": {
                        "evidence_record_id": evidence_record_id,
                        "verified_quote": f"{label} has verified allele evidence.",
                        "page": 5,
                        "section": "Results",
                        "chunk_id": f"chunk-{index}",
                    },
                }
                for evidence_ref_id, evidence_record_id in zip(
                    evidence_ref_ids,
                    evidence_record_ids,
                )
            ],
        ],
        "association": {
            "object_type": "AllelePaperEvidenceAssociation",
            "pending_ref_id": f"allele-paper-evidence-association-{index}",
            "payload": {
                "association_kind": "allele_paper_evidence",
                "allele_identifier": normalized_id,
                "allele_label": label,
                "associated_gene": associated_gene,
                "confidence": "high",
                "evidence_record_ids": evidence_record_ids,
            },
            "object_refs": [
                {"pending_ref_id": allele_ref_id, "object_type": "Allele"},
                {"pending_ref_id": reference_ref_id, "object_type": "Reference"},
                {"pending_ref_id": mention_ref_id, "object_type": "AlleleMention"},
                *[
                    {"pending_ref_id": evidence_ref_id, "object_type": "EvidenceQuote"}
                    for evidence_ref_id in evidence_ref_ids
                ],
            ],
            "evidence_record_ids": evidence_record_ids,
        },
    }


@pytest.mark.asyncio
async def test_run_curation_prep_selects_envelope_refs_and_persists_output(monkeypatch):
    extraction_result = _make_domain_envelope_extraction_result()
    captured: dict[str, object] = {}

    def _fake_ensure_domain_envelope_materialization(record, *, persist, db=None):
        assert record is extraction_result
        assert persist is True
        assert db == object_db
        return CurationPrepEnvelopeRef(
            envelope_id="env-gene-1",
            envelope_revision=2,
            source_extraction_result_id="extract-domain-1",
            domain_pack_id="gene",
            review_row_count=1,
        )

    def _fake_persist_extraction_result(request, *, db=None):
        captured["request"] = request
        captured["db"] = db
        return None

    object_db = object()
    monkeypatch.setattr(
        module,
        "_ensure_domain_envelope_materialization",
        _fake_ensure_domain_envelope_materialization,
    )
    monkeypatch.setattr(module, "persist_extraction_result", _fake_persist_extraction_result)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=_make_scope_confirmation(),
        db=object_db,
    )

    assert prep_output.candidates == []
    assert prep_output.review_row_count == 1
    assert prep_output.envelope_refs[0].envelope_id == "env-gene-1"
    assert prep_output.envelope_refs[0].envelope_revision == 2
    assert prep_output.envelope_refs[0].source_extraction_result_id == "extract-domain-1"
    assert prep_output.run_metadata.model_name == "deterministic_programmatic_mapper_v1"

    persisted_request = captured["request"]
    assert persisted_request.document_id == "document-1"
    assert persisted_request.agent_key == "curation_prep"
    assert persisted_request.source_kind is CurationExtractionSourceKind.CHAT
    assert persisted_request.adapter_key == "gene"
    assert persisted_request.origin_session_id == "chat-session-1"
    assert persisted_request.trace_id == "trace-upstream"
    assert persisted_request.user_id == "user-upstream"
    assert persisted_request.candidate_count == 1
    assert persisted_request.payload_json["candidates"] == []
    assert persisted_request.payload_json["envelope_refs"][0]["envelope_id"] == "env-gene-1"
    assert persisted_request.metadata["scope_adapter_keys"] == ["gene"]
    assert persisted_request.metadata["envelope_refs"][0]["envelope_id"] == "env-gene-1"
    assert (
        persisted_request.metadata["final_run_metadata"]["model_name"]
        == "deterministic_programmatic_mapper_v1"
    )


@pytest.mark.asyncio
async def test_run_curation_prep_rejects_legacy_items_as_semantic_source(monkeypatch):
    extraction_result = _make_extraction_result(adapter_key="gene")

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


def test_summarize_curation_prep_scope_counts_materialized_envelope_rows(monkeypatch):
    extraction_result = _make_domain_envelope_extraction_result()

    def _fake_ensure(record, *, persist, db=None):
        assert record is extraction_result
        assert persist is False
        assert db is None
        return CurationPrepEnvelopeRef(
            envelope_id="env-gene-1",
            envelope_revision=1,
            source_extraction_result_id="extract-domain-1",
            domain_pack_id="gene",
            review_row_count=3,
        )

    monkeypatch.setattr(module, "_ensure_domain_envelope_materialization", _fake_ensure)

    summary = module.summarize_curation_prep_scope(
        [extraction_result],
        adapter_keys=["gene"],
    )

    assert summary.candidate_count == 3
    assert summary.adapter_keys == ["gene"]
    assert summary.warnings == []


def test_summarize_curation_prep_scope_rejects_legacy_items_as_semantic_source():
    extraction_result = _make_extraction_result(adapter_key="gene")

    summary = module.summarize_curation_prep_scope(
        [extraction_result],
        adapter_keys=["gene"],
    )

    assert summary.candidate_count == 0
    assert summary.adapter_keys == []
    assert any("curatable_objects" in warning for warning in summary.warnings)


@pytest.mark.asyncio
async def test_run_curation_prep_rejects_mismatched_document_id_override(monkeypatch):
    extraction_result = _make_domain_envelope_extraction_result()

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


@pytest.mark.asyncio
async def test_run_curation_prep_allele_scope_uses_envelope_refs_not_prep_candidates(
    monkeypatch,
):
    extraction_result = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-allele-1",
            "document_id": "document-1",
            "adapter_key": "allele",
            "agent_key": "allele_extractor",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "chat-session-1",
            "trace_id": "trace-upstream",
            "flow_run_id": None,
            "user_id": "user-upstream",
            "candidate_count": 2,
            "conversation_summary": None,
            "payload_json": {
                "summary": "Allele envelope ready.",
                "curatable_objects": [
                    _make_allele_domain_payload(
                        label="crb11A22",
                        normalized_id="ALLELE:0000001",
                        index=1,
                    )["association"],
                ],
                "metadata": {"evidence_records": [_make_evidence_record()]},
                "run_summary": {"candidate_count": 2},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": {},
        }
    )

    def _fake_ensure(record, *, persist, db=None):
        assert record is extraction_result
        assert persist is True
        return CurationPrepEnvelopeRef(
            envelope_id="env-allele-1",
            envelope_revision=1,
            source_extraction_result_id="extract-allele-1",
            domain_pack_id="fixture.alliance.allele",
            review_row_count=2,
        )

    captured: dict[str, object] = {}

    def _fake_persist(request, *, db=None):
        captured["request"] = request

    monkeypatch.setattr(module, "_ensure_domain_envelope_materialization", _fake_ensure)
    monkeypatch.setattr(module, "persist_extraction_result", _fake_persist)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=CurationPrepScopeConfirmation(
            confirmed=True,
            adapter_keys=["allele"],
            notes=["User confirmed allele prep scope."],
        ),
    )

    assert prep_output.candidates == []
    assert prep_output.review_row_count == 2
    assert prep_output.envelope_refs[0].domain_pack_id == "fixture.alliance.allele"
    assert captured["request"].candidate_count == 2
