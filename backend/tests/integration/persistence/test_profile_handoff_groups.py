"""Real saved-profile handoff regression; no model calls or production data."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from src.lib.curation_workspace.bootstrap_service import (
    bootstrap_document_session,
    run_flow_curation_handoff,
)
from src.lib.curation_workspace.execution_contracts import resolve_receipt_profile
from src.lib.curation_workspace.extraction_results import ExtractionEnvelopeCandidate
from src.lib.curation_workspace.models import CurationCandidate, CurationReviewSession
from src.lib.flows.executor import _persist_flow_extraction_candidates
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import CurationDocumentBootstrapRequest
from tests.unit.lib.domain_packs.test_profile_validation import profile_envelope
from .test_profile_validator_workspace import (
    manual_profile_record,
    migrated_database,
    example,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("manual_profile_record", ["unmapped"], indirect=True)
@pytest.mark.parametrize("groups", [None, (), ("FB",)])
async def test_saved_profile_35_records_handoff_and_replay(
    manual_profile_record, groups, monkeypatch
):
    from src.lib.curation_workspace import bootstrap_service

    db, manual, receipt = manual_profile_record
    pipeline_contexts = []
    original_pipeline = bootstrap_service.run_post_curation_pipeline

    async def capture_pipeline(request, **kwargs):
        pipeline_contexts.append((request.active_groups, request.user_id))
        return await original_pipeline(request, **kwargs)

    monkeypatch.setattr(
        bootstrap_service, "run_post_curation_pipeline", capture_pipeline
    )
    # Keep real SQL/persistence and service transaction behavior, but contain
    # commits in the fixture transaction so its rollback removes all test data.
    monkeypatch.setattr(db, "commit", db.flush)
    document_id = str(manual.session.document_id)
    owner = str(db.get(PDFDocument, manual.session.document_id).user_id)
    flow_run_id, origin_session_id = str(uuid4()), str(uuid4())
    profile = resolve_receipt_profile(db, receipt)
    envelope = profile_envelope({"paper_name": "fixture-0"}, receipt, profile)
    envelope.envelope_id = "handoff-" + uuid4().hex
    seed = envelope.extracted_objects[0]
    envelope.extracted_objects = [
        seed.model_copy(
            deep=True,
            update={
                "object_id": f"record-{index}",
                "payload": {
                    "semantic_class": "record",
                    "attributes": {"paper_name": f"fixture-{index}"},
                },
            },
        )
        for index in range(35)
    ]
    # Synthetic evidence is fixture data, not a claim about Gillian's paper.
    for obj in envelope.extracted_objects:
        obj.evidence_record_ids = [f"evidence-{obj.object_id}"]
    envelope.metadata["extraction_metadata"]["evidence_records"] = [
        {
            "evidence_record_id": f"evidence-{obj.object_id}",
            "verified_quote": "Synthetic reagent evidence.",
            "chunk_id": "fixture-chunk",
            "page": 1,
        }
        for obj in envelope.extracted_objects
    ]
    records = _persist_flow_extraction_candidates(
        candidates=[
            ExtractionEnvelopeCandidate(
                agent_key=receipt.agent_key,
                adapter_key="generic",
                candidate_count=35,
                payload_json=envelope.model_dump(mode="json"),
                execution_receipt=receipt.model_dump(mode="json"),
            )
        ],
        document_id=document_id,
        user_id=owner,
        session_id=origin_session_id,
        trace_id="fixture-handoff",
        flow_run_id=flow_run_id,
        db=db,
    )
    result = await run_flow_curation_handoff(
        extraction_results=records,
        document_id=document_id,
        runner_user_id=owner,
        active_groups=groups,
        flow_run_id=flow_run_id,
        origin_session_id=origin_session_id,
        conversation_summary="35 synthetic reagent records",
        db=db,
    )
    assert len(result.review_session_ids) == 1
    assert pipeline_contexts == [(groups, owner)]
    session_id = result.review_session_ids[0]
    review = db.get(CurationReviewSession, session_id)
    assert review.created_by_id == owner
    assert review.assigned_curator_id == owner
    candidates = list(
        db.scalars(
            select(CurationCandidate).where(CurationCandidate.session_id == session_id)
        )
    )
    assert len(candidates) == 35
    assert all(
        candidate.execution_receipt == receipt.model_dump(mode="json")
        for candidate in candidates
    )

    # The supported bootstrap route reuses durable results and the same session.
    replay = await bootstrap_document_session(
        document_id,
        CurationDocumentBootstrapRequest(
            adapter_key="generic",
            flow_run_id=flow_run_id,
            origin_session_id=origin_session_id,
        ),
        current_user_id=owner,
        active_groups=groups,
        db=db,
    )
    assert replay.session.session_id == session_id
    assert not replay.created
    assert pipeline_contexts == [(groups, owner), (groups, owner)]
    assert (
        len(
            list(
                db.scalars(
                    select(CurationCandidate).where(
                        CurationCandidate.session_id == session_id
                    )
                )
            )
        )
        == 35
    )
