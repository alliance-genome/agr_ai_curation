"""Unit tests for the deterministic curation prep service layer."""

from __future__ import annotations

import pytest

import src.lib.curation_workspace.adapter_registry as adapter_registry_module
from src.lib.curation_workspace import curation_prep_service as module
from src.schemas.curation_prep import CurationPrepScopeConfirmation
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
        adapter_keys=["reference_adapter"],
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
async def test_run_curation_prep_maps_generic_items_and_persists_output(monkeypatch):
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
    assert candidate.adapter_key == "reference_adapter"
    assert candidate.payload == {
        "label": "Candidate Alpha",
        "entity_type": "observation",
        "normalized_id": "OBS:0001",
        "source_mentions": ["Alpha mention"],
    }
    assert candidate.evidence_records[0].field_paths == [
        "label",
        "entity_type",
        "normalized_id",
        "source_mentions.0",
    ]
    assert candidate.evidence_records[0].evidence_record_id == "evidence-1"
    assert candidate.evidence_records[0].anchor.snippet_text == (
        "Candidate Alpha was supported by a verified observation."
    )
    assert candidate.evidence_records[0].anchor.page_number == 5
    assert candidate.evidence_records[0].anchor.section_title == "Results"
    assert candidate.evidence_records[0].anchor.subsection_title == "Observation set"
    assert candidate.evidence_records[0].anchor.figure_reference is None
    assert candidate.evidence_records[0].anchor.table_reference == "Table 2"
    assert candidate.evidence_records[0].anchor.chunk_ids == ["chunk-1"]
    assert prep_output.run_metadata.model_name == "deterministic_programmatic_mapper_v1"

    persisted_request = captured["request"]
    assert persisted_request.document_id == "document-1"
    assert persisted_request.agent_key == "curation_prep"
    assert persisted_request.source_kind is CurationExtractionSourceKind.CHAT
    assert persisted_request.adapter_key == "reference_adapter"
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
async def test_run_curation_prep_gates_candidates_without_item_level_evidence(monkeypatch):
    extraction_result = _make_extraction_result(
        items=[
            _make_item(
                label="Candidate Alpha",
                normalized_id="OBS:0001",
                source_mentions=["Alpha mention"],
                evidence_record_ids=["evidence-1"],
            ),
            _make_item(
                label="Candidate Beta",
                normalized_id="OBS:0002",
                source_mentions=["Beta mention"],
                evidence_record_ids=[],
            ),
        ],
        evidence_records=[
            _make_evidence_record(
                evidence_record_id="evidence-1",
            )
        ],
    )

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=_make_scope_confirmation(),
    )

    assert [candidate.payload["label"] for candidate in prep_output.candidates] == [
        "Candidate Alpha"
    ]
    assert prep_output.run_metadata.warnings == ["Skipped 1 candidate without verified evidence."]


@pytest.mark.asyncio
async def test_run_curation_prep_does_not_fall_back_to_top_level_evidence_records(monkeypatch):
    extraction_result = _make_extraction_result(
        items=[
            _make_item(
                evidence_record_ids=[],
            )
        ],
        evidence_records=[
            _make_evidence_record()
        ],
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
async def test_run_curation_prep_items_do_not_use_metadata_evidence_records(monkeypatch):
    extraction_result = _make_extraction_result(
        items=[
            _make_item(
                evidence_record_ids=["evidence-1"],
                evidence=[],
            )
        ],
        evidence_records=[],
    )
    extraction_result.payload_json["evidence_records"] = []
    extraction_result.payload_json["metadata"] = {
        "evidence_records": [_make_evidence_record()]
    }

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
async def test_run_curation_prep_rejects_when_all_candidates_fail_evidence_gate(monkeypatch):
    extraction_result = _make_extraction_result(
        items=[
            _make_item(
                evidence_record_ids=[],
                evidence=[
                    {
                        "entity": "Candidate Alpha",
                        "page": 5,
                        "section": "Results",
                        "chunk_id": "chunk-1",
                    }
                ],
            )
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
async def test_run_curation_prep_supports_legacy_inline_item_evidence(monkeypatch):
    extraction_result = _make_extraction_result(
        items=[
            _make_item(
                evidence_record_ids=[],
                evidence=[
                    {
                        "entity": "Candidate Alpha",
                        "verified_quote": "Candidate Alpha was supported by a verified observation.",
                        "page": 5,
                        "section": "Results",
                        "subsection": "Observation set",
                        "chunk_id": "chunk-1",
                    }
                ],
            )
        ],
        evidence_records=[],
    )

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=_make_scope_confirmation(),
    )

    assert len(prep_output.candidates) == 1


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


def test_candidate_blueprints_skip_empty_compacted_payload_without_error():
    extraction_result = _make_extraction_result(
        items=[
            _make_item(
                label=None,
                entity_type=None,
                normalized_id=None,
                source_mentions=[],
                evidence=[],
            )
        ],
        conversation_summary=None,
    )

    blueprints = module._candidate_blueprints(  # noqa: SLF001
        extraction_result,
        extraction_result.payload_json,
        candidate_adapter_key="observation",
    )

    assert blueprints == []


def test_candidate_conversation_summary_falls_back_to_generic_item_context():
    extraction_result = _make_extraction_result(conversation_summary=None)

    blueprints = module._candidate_blueprints(  # noqa: SLF001
        extraction_result,
        extraction_result.payload_json,
        candidate_adapter_key="observation",
    )

    assert len(blueprints) == 1
    assert (
        blueprints[0].conversation_context_summary
        == "Prepared deterministic observation candidate for Candidate Alpha."
    )


@pytest.mark.asyncio
async def test_run_curation_prep_maps_allele_candidates_from_domain_envelope_objects(monkeypatch):
    first = _make_allele_domain_payload(
        label="crb11A22",
        normalized_id="ALLELE:0000001",
        evidence_record_ids=["evidence-1"],
        index=1,
    )
    second = _make_allele_domain_payload(
        label="crb8F105",
        normalized_id="ALLELE:0000002",
        evidence_record_ids=["evidence-2"],
        index=2,
    )
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
                "alleles": [
                    {
                        "mention": "legacy-only-allele",
                        "associated_gene": "Legacy",
                        "evidence_record_ids": ["legacy-evidence"],
                    }
                ],
                "curatable_objects": [
                    *first["supporting_objects"],
                    first["association"],
                    *second["supporting_objects"],
                    second["association"],
                ],
                "metadata": {
                    "evidence_records": [
                        _make_evidence_record(
                            evidence_record_id="evidence-1",
                            entity="crb11A22",
                            verified_quote="crb11A22 has fused rhabdomeres.",
                        ),
                        _make_evidence_record(
                            evidence_record_id="evidence-2",
                            entity="crb8F105",
                            verified_quote="crb8F105 truncates the protein.",
                        ),
                    ]
                },
                "run_summary": {"candidate_count": 2},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": {},
        }
    )

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=CurationPrepScopeConfirmation(
            confirmed=True,
            adapter_keys=["allele"],
            notes=["User confirmed allele prep scope."],
        ),
    )

    assert [candidate.payload["label"] for candidate in prep_output.candidates] == [
        "crb11A22",
        "crb8F105",
    ]
    assert prep_output.candidates[0].payload["entity_type"] == "allele"
    assert prep_output.candidates[0].payload["source_mentions"] == ["crb11A22"]
    assert prep_output.candidates[0].payload["normalized_id"] == "ALLELE:0000001"
    assert prep_output.candidates[0].payload["associated_gene"] == "Crumbs"
    assert prep_output.candidates[0].evidence_records[0].anchor.snippet_text == (
        "crb11A22 has fused rhabdomeres."
    )
    assert all(
        candidate.payload["label"] != "legacy-only-allele"
        for candidate in prep_output.candidates
    )


@pytest.mark.asyncio
async def test_run_curation_prep_allele_domain_objects_still_require_evidence_ids(
    monkeypatch,
):
    first = _make_allele_domain_payload(
        label="crb11A22",
        normalized_id="ALLELE:0000001",
        evidence_record_ids=["evidence-1"],
        index=1,
    )
    second = _make_allele_domain_payload(
        label="crb8F105",
        normalized_id="ALLELE:0000002",
        evidence_record_ids=[],
        index=2,
    )
    second["association"]["payload"]["evidence_record_ids"] = ["evidence-2"]
    extraction_result = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-allele-2",
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
                "curatable_objects": [
                    *first["supporting_objects"],
                    first["association"],
                    *second["supporting_objects"],
                    second["association"],
                ],
                "metadata": {
                    "evidence_records": [
                        _make_evidence_record(
                            evidence_record_id="evidence-1",
                            entity="crb11A22",
                            verified_quote="crb11A22 has fused rhabdomeres.",
                        ),
                        _make_evidence_record(
                            evidence_record_id="evidence-2",
                            entity="crb8F105",
                            verified_quote="crb8F105 truncates the protein.",
                        ),
                    ]
                },
                "run_summary": {"candidate_count": 2},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": {},
        }
    )

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=CurationPrepScopeConfirmation(
            confirmed=True,
            adapter_keys=["allele"],
            notes=["User confirmed allele prep scope."],
        ),
    )

    assert [candidate.payload["label"] for candidate in prep_output.candidates] == ["crb11A22"]
    assert prep_output.run_metadata.warnings == ["Skipped 1 candidate without verified evidence."]


@pytest.mark.asyncio
async def test_run_curation_prep_allele_domain_objects_derive_label_from_reference(
    monkeypatch,
):
    first = _make_allele_domain_payload(
        label="crb11A22",
        normalized_id="ALLELE:0000001",
        evidence_record_ids=["evidence-1"],
        index=1,
    )
    second = _make_allele_domain_payload(
        label="crb8F105",
        normalized_id="ALLELE:0000002",
        evidence_record_ids=["evidence-2"],
        index=2,
    )
    second["association"]["payload"].pop("allele_label")
    extraction_result = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-allele-3",
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
                "curatable_objects": [
                    *first["supporting_objects"],
                    first["association"],
                    *second["supporting_objects"],
                    second["association"],
                ],
                "metadata": {
                    "evidence_records": [
                        _make_evidence_record(
                            evidence_record_id="evidence-1",
                            entity="crb11A22",
                            verified_quote="crb11A22 has fused rhabdomeres.",
                        ),
                        _make_evidence_record(
                            evidence_record_id="evidence-2",
                            entity="crb8F105",
                            verified_quote="crb8F105 truncates the protein.",
                        ),
                    ]
                },
                "run_summary": {"candidate_count": 2},
            },
            "created_at": "2026-03-20T21:55:00Z",
            "metadata": {},
        }
    )

    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    prep_output = await module.run_curation_prep(
        [extraction_result],
        scope_confirmation=CurationPrepScopeConfirmation(
            confirmed=True,
            adapter_keys=["allele"],
            notes=["User confirmed allele prep scope."],
        ),
    )

    assert [candidate.payload["label"] for candidate in prep_output.candidates] == [
        "crb11A22",
        "crb8F105",
    ]
