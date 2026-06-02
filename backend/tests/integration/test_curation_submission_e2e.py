"""End-to-end submission workflow coverage for the curation workspace substrate."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import MOCK_USERS


REPO_ROOT = Path(__file__).resolve().parents[3]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

FORBIDDEN_LEGACY_SEMANTIC_KEYS = {
    "items",
    "annotations",
    "genes",
    "alleles",
    "diseases",
    "chemicals",
    "phenotypes",
    "CurationPrepCandidate",
    "NormalizedCandidate",
    "normalized_payload",
    "annotation_drafts",
}


def _hash(char: str) -> str:
    return char * 64


def _patch_submission_transport_adapter(monkeypatch, adapter_factory):
    from src.lib.curation_workspace import session_service, session_submission_service

    # The public facade re-exports functions owned by session_submission_service.
    # Patch the owning module deliberately so endpoint tests still exercise the
    # real facade import path.
    assert session_service.execute_submission is session_submission_service.execute_submission
    assert session_service.retry_submission is session_submission_service.retry_submission
    monkeypatch.setattr(
        session_submission_service,
        "_resolve_submission_transport_adapter",
        adapter_factory,
    )


def _fixture_yaml(*parts: str) -> dict:
    fixture_path = REPO_ROOT / "backend" / "tests" / "fixtures" / "domain_packs"
    for part in parts:
        fixture_path /= part
    return yaml.safe_load(fixture_path.read_text(encoding="utf-8"))


def _iter_mapping_keys(value):
    if isinstance(value, Mapping):
        yield from value.keys()
        for child in value.values():
            yield from _iter_mapping_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_mapping_keys(child)


def _retag_envelope(envelope, *, envelope_id: str):
    metadata = dict(envelope.metadata or {})
    metadata["semantic_source"] = "domain_envelope.objects"
    return envelope.model_copy(
        update={"envelope_id": envelope_id, "metadata": metadata}
    )


def _alliance_gate_case(case_key: str):
    if case_key == "gene":
        from agr_ai_curation_alliance.domain_packs.gene import (
            GENE_MENTION_EVIDENCE_OBJECT_TYPE,
            GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY,
            tool_verified_gene_output_to_pending_envelope,
        )

        envelope = tool_verified_gene_output_to_pending_envelope(
            _fixture_yaml("gene", "tool_verified_gene_output.yaml")
        )
        return {
            "adapter_key": "gene",
            "envelope": _retag_envelope(
                envelope,
                envelope_id="gene-alliance-e2e-envelope",
            ),
            "target_key": GENE_VALIDATED_REFERENCE_EXPORT_TARGET_KEY,
            "target_object_type": GENE_MENTION_EVIDENCE_OBJECT_TYPE,
            "expected_ready": True,
        }

    elif case_key == "gene_expression":
        from agr_ai_curation_alliance.domain_packs.gene_expression import (
            GENE_EXPRESSION_OBJECT_TYPE,
            GENE_EXPRESSION_TARGET_KEY,
        )

        return {
            "adapter_key": "gene_expression",
            "envelope": _retag_envelope(
                _tmem67_gene_expression_envelope(
                    envelope_id="gene-expression-alliance-e2e-envelope"
                ),
                envelope_id="gene-expression-alliance-e2e-envelope",
            ),
            "target_key": GENE_EXPRESSION_TARGET_KEY,
            "target_object_type": GENE_EXPRESSION_OBJECT_TYPE,
            "expected_ready": True,
        }

    elif case_key == "allele":
        from agr_ai_curation_alliance.domain_packs.allele import (
            ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
            build_pending_allele_envelope_from_tool_verified_fixture,
        )
        from tests.fixtures.evidence.harness import load_evidence_fixture

        envelope = build_pending_allele_envelope_from_tool_verified_fixture(
            load_evidence_fixture("tool_verified_allele_paper"),
            envelope_id="allele-alliance-e2e-envelope",
        )
        return {
            "adapter_key": "allele",
            "envelope": _retag_envelope(
                envelope,
                envelope_id="allele-alliance-e2e-envelope",
            ),
            "target_key": ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
            "target_object_type": "AllelePaperEvidenceAssociation",
            "expected_ready": False,
            "expected_blocker_codes": {
                "domain_envelope.definition_state_blocked",
                "domain_envelope.export_behavior_blocked",
                "alliance.allele.write_blocked",
            },
        }

    elif case_key == "disease":
        from agr_ai_curation_alliance.domain_packs.disease import (
            DISEASE_EXPORT_TARGET_ID,
            DISEASE_OBJECT_TYPE,
            tool_verified_disease_output_to_pending_envelope,
        )

        envelope = tool_verified_disease_output_to_pending_envelope(
            _fixture_yaml("disease", "tool_verified_disease_output.yaml")
        )
        return {
            "adapter_key": "disease",
            "envelope": _retag_envelope(
                envelope,
                envelope_id="disease-alliance-e2e-envelope",
            ),
            "target_key": DISEASE_EXPORT_TARGET_ID,
            "target_object_type": DISEASE_OBJECT_TYPE,
            "expected_ready": False,
            "expected_blocker_codes": {
                "domain_envelope.definition_state_blocked",
                "domain_envelope.export_behavior_blocked",
            },
        }

    elif case_key == "phenotype":
        from agr_ai_curation_alliance.domain_packs.phenotype import (
            PHENOTYPE_EXPORT_TARGET_ID,
            PHENOTYPE_OBJECT_TYPE,
            build_pending_phenotype_envelope_from_tool_verified_fixture,
        )
        from tests.fixtures.evidence.harness import load_evidence_fixture

        envelope = build_pending_phenotype_envelope_from_tool_verified_fixture(
            load_evidence_fixture("tool_verified_phenotype_paper"),
            envelope_id="phenotype-alliance-e2e-envelope",
        )
        return {
            "adapter_key": "phenotype",
            "envelope": _retag_envelope(
                envelope,
                envelope_id="phenotype-alliance-e2e-envelope",
            ),
            "target_key": PHENOTYPE_EXPORT_TARGET_ID,
            "target_object_type": PHENOTYPE_OBJECT_TYPE,
            "expected_ready": False,
            "expected_blocker_codes": {
                "domain_envelope.pack_export_policy_blocked",
                "domain_envelope.definition_state_blocked",
                "domain_envelope.export_behavior_blocked",
            },
        }

    raise AssertionError(f"Unhandled domain-envelope gate case: {case_key}")


def _tmem67_gene_expression_envelope(*, envelope_id: str):
    from agr_ai_curation_alliance.domain_packs.gene_expression import (
        gene_expression_extraction_output_to_pending_envelope,
    )

    raw_fixture = _fixture_yaml(
        "gene_expression",
        "tmem67_gene_expression_output.yaml",
    )
    context = raw_fixture["envelope_context"]
    return gene_expression_extraction_output_to_pending_envelope(
        raw_fixture["output"],
        envelope_id=envelope_id,
        document_id=context["document_id"],
        produced_by=context["produced_by"],
        produced_at=context["produced_at"],
    )


def _tmem67_missing_where_statement_envelope(*, envelope_id: str):
    from agr_ai_curation_alliance.domain_packs.gene_expression import (
        GENE_EXPRESSION_OBJECT_TYPE,
    )

    envelope = _tmem67_gene_expression_envelope(envelope_id=envelope_id)
    objects = []
    for domain_object in envelope.objects:
        if domain_object.object_type != GENE_EXPRESSION_OBJECT_TYPE:
            objects.append(domain_object)
            continue
        payload = dict(domain_object.payload)
        payload.pop("where_expressed_statement", None)
        objects.append(domain_object.model_copy(update={"payload": payload}))
    return envelope.model_copy(update={"objects": objects, "validation_findings": []})


def _domain_envelope_extraction_record(
    submission_e2e_context,
    *,
    envelope,
    adapter_key: str,
    case_key: str,
):
    from src.schemas.curation_workspace import (
        CurationExtractionResultRecord,
        CurationExtractionSourceKind,
    )

    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": f"extract-{case_key}",
            "document_id": submission_e2e_context["document_id"],
            "adapter_key": adapter_key,
            "agent_key": f"{adapter_key}_extractor",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": f"chat-session-{case_key}",
            "trace_id": f"trace-{case_key}",
            "flow_run_id": f"flow-{case_key}",
            "user_id": submission_e2e_context["current_user_auth_sub"],
            "candidate_count": len(envelope.objects),
            "conversation_summary": (
                f"Prepared {adapter_key} domain-envelope gate fixture."
            ),
            "payload_json": envelope.model_dump(mode="json"),
            "created_at": "2026-05-10T12:00:00Z",
            "metadata": {
                "envelope_id": envelope.envelope_id,
                "project_key": "agr",
            },
        }
    )


def _run_prep_and_bootstrap_domain_envelope(
    client: TestClient,
    submission_e2e_context,
    test_db,
    *,
    envelope,
    adapter_key: str,
    case_key: str,
):
    from src.lib.curation_workspace.curation_prep_service import (
        CurationPrepPersistenceContext,
        run_curation_prep,
    )
    from src.schemas.curation_prep import CurationPrepScopeConfirmation
    from src.schemas.curation_workspace import CurationExtractionSourceKind

    extraction_result = _domain_envelope_extraction_record(
        submission_e2e_context,
        envelope=envelope,
        adapter_key=adapter_key,
        case_key=case_key,
    )
    prep_output = asyncio.run(
        run_curation_prep(
            [extraction_result],
            scope_confirmation=CurationPrepScopeConfirmation(
                confirmed=True,
                adapter_keys=[adapter_key],
                notes=[f"Confirmed {case_key} domain-envelope gate fixture."],
            ),
            db=test_db,
            persistence_context=CurationPrepPersistenceContext(
                origin_session_id=f"chat-session-{case_key}",
                trace_id=f"trace-{case_key}",
                flow_run_id=f"flow-{case_key}",
                user_id=submission_e2e_context["current_user_auth_sub"],
                source_kind=CurationExtractionSourceKind.CHAT,
            ),
        )
    )
    assert prep_output.candidates == []
    assert prep_output.review_row_count > 0
    assert prep_output.envelope_refs[0].envelope_id == envelope.envelope_id

    bootstrap_response = client.post(
        (
            "/api/curation-workspace/documents/"
            f"{submission_e2e_context['document_id']}/bootstrap"
        ),
        json={
            "adapter_key": adapter_key,
            "origin_session_id": f"chat-session-{case_key}",
        },
    )
    assert bootstrap_response.status_code == 200, bootstrap_response.text
    bootstrap_payload = bootstrap_response.json()
    session_id = bootstrap_payload["session"]["session_id"]
    workspace_response = client.get(
        f"/api/curation-workspace/sessions/{session_id}",
        params={"include_workspace": "true"},
    )
    assert workspace_response.status_code == 200, workspace_response.text
    return prep_output, bootstrap_payload, workspace_response.json()["workspace"]


def _assert_workspace_candidates_use_persisted_envelopes(workspace_payload):
    assert workspace_payload["candidates"]
    for candidate in workspace_payload["candidates"]:
        assert candidate["projection_ref"] is not None
        assert candidate["normalized_payload"] == {}
        assert candidate["metadata"]["semantic_source"] == "domain_envelope.objects"
        assert candidate["metadata"]["object_type"]
        assert candidate["metadata"]["object_role"]


def _candidate_for_object_type(workspace_payload, object_type: str) -> dict:
    matches = [
        candidate
        for candidate in workspace_payload["candidates"]
        if candidate["metadata"].get("object_type") == object_type
    ]
    assert len(matches) == 1
    return matches[0]


def _accept_candidate(client: TestClient, *, session_id: str, candidate_id: str) -> dict:
    response = client.post(
        f"/api/curation-workspace/candidates/{candidate_id}/decision",
        json={
            "session_id": session_id,
            "candidate_id": candidate_id,
            "action": "accept",
            "advance_queue": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_system_agents_for_integration(test_db) -> None:
    """Seed unified system agents used by package validator dispatch."""

    from src.lib.agent_studio.system_agent_sync import sync_system_agents
    from src.models.sql.agent import Agent as UnifiedAgent
    from src.models.sql.agent import Project
    from src.models.sql.database import Base
    from src.models.sql.prompts import PromptExecutionLog, PromptTemplate
    from src.models.sql.user import User

    Base.metadata.create_all(
        bind=test_db.get_bind(),
        tables=[
            User.__table__,
            Project.__table__,
            UnifiedAgent.__table__,
            PromptTemplate.__table__,
            PromptExecutionLog.__table__,
        ],
    )
    sync_system_agents(test_db, force_reload=True)
    test_db.commit()


@pytest.fixture
def client(test_db, get_auth_mock, monkeypatch):
    """Create isolated app client with auth and database overrides."""
    monkeypatch.setenv("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", "test-key"))
    monkeypatch.setenv("GROQ_API_KEY", os.getenv("GROQ_API_KEY", "test-key"))
    monkeypatch.setenv("LLM_PROVIDER_STRICT_MODE", "false")

    get_auth_mock.set_user("curator1")

    from main import create_app
    from src.api.auth import _get_user_from_cookie_impl
    from src.models.sql.database import get_db

    _seed_system_agents_for_integration(test_db)

    app = create_app()

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_get_user_from_cookie_impl] = get_auth_mock.get_user
    try:
        test_client = TestClient(app)
        test_client.current_user_auth_sub = MOCK_USERS["curator1"]["sub"]
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def submission_e2e_context(client: TestClient, test_db):
    """Seed the document and cleanup all curation workspace records after the e2e run."""
    from src.lib.curation_workspace.models import (
        CurationActionLogEntry as SessionActionLogModel,
        CurationCandidate,
        CurationDraft,
        CurationEvidenceRecord,
        CurationExtractionResultRecord,
        CurationReviewSession,
        CurationSubmissionRecord,
        CurationValidationSnapshot,
        DomainEnvelopeHistory,
        DomainEnvelopeModel,
        DomainEnvelopeObject,
        DomainEnvelopeProjectionIndex,
        DomainValidationFinding,
    )
    from src.models.sql.database import Base
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    Base.metadata.create_all(
        bind=test_db.get_bind(),
        tables=[
            User.__table__,
            PDFDocument.__table__,
            CurationReviewSession.__table__,
            CurationExtractionResultRecord.__table__,
            DomainEnvelopeModel.__table__,
            DomainEnvelopeObject.__table__,
            DomainValidationFinding.__table__,
            DomainEnvelopeHistory.__table__,
            DomainEnvelopeProjectionIndex.__table__,
            CurationCandidate.__table__,
            CurationEvidenceRecord.__table__,
            CurationDraft.__table__,
            CurationValidationSnapshot.__table__,
            CurationSubmissionRecord.__table__,
            SessionActionLogModel.__table__,
        ],
    )

    current_user_auth_sub = client.current_user_auth_sub
    test_db.add(
        User(
            auth_sub=current_user_auth_sub,
            email="curator1@alliancegenome.org",
            display_name="Curator One",
            is_active=True,
        )
    )

    document_id = uuid4()
    test_db.add(
        PDFDocument(
            id=document_id,
            filename="test_submission_e2e.pdf",
            title="Submission E2E Paper",
            file_path=f"{document_id}/submission-e2e.pdf",
            file_hash=_hash("e"),
            file_size=4096,
            page_count=4,
        )
    )
    test_db.commit()

    yield {
        "document_id": str(document_id),
        "current_user_auth_sub": current_user_auth_sub,
    }

    session_ids = [
        row[0]
        for row in (
            test_db.query(CurationReviewSession.id)
            .filter(CurationReviewSession.document_id == document_id)
            .all()
        )
    ]
    candidate_ids = [
        row[0]
        for row in (
            test_db.query(CurationCandidate.id)
            .filter(CurationCandidate.session_id.in_(session_ids))
            .all()
        )
    ] if session_ids else []

    if session_ids:
        test_db.query(SessionActionLogModel).filter(
            SessionActionLogModel.session_id.in_(session_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationSubmissionRecord).filter(
            CurationSubmissionRecord.session_id.in_(session_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationValidationSnapshot).filter(
            CurationValidationSnapshot.session_id.in_(session_ids)
        ).delete(synchronize_session=False)

    if candidate_ids:
        test_db.query(CurationEvidenceRecord).filter(
            CurationEvidenceRecord.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationDraft).filter(
            CurationDraft.candidate_id.in_(candidate_ids)
        ).delete(synchronize_session=False)
        test_db.query(CurationCandidate).filter(
            CurationCandidate.id.in_(candidate_ids)
        ).delete(synchronize_session=False)

    if session_ids:
        test_db.query(CurationReviewSession).filter(
            CurationReviewSession.id.in_(session_ids)
        ).delete(synchronize_session=False)

    envelope_ids = [
        row[0]
        for row in (
            test_db.query(DomainEnvelopeModel.envelope_id)
            .filter(DomainEnvelopeModel.document_id == document_id)
            .all()
        )
    ]
    if envelope_ids:
        test_db.query(DomainEnvelopeProjectionIndex).filter(
            DomainEnvelopeProjectionIndex.envelope_id.in_(envelope_ids)
        ).delete(synchronize_session=False)
        test_db.query(DomainEnvelopeHistory).filter(
            DomainEnvelopeHistory.envelope_id.in_(envelope_ids)
        ).delete(synchronize_session=False)
        test_db.query(DomainValidationFinding).filter(
            DomainValidationFinding.envelope_id.in_(envelope_ids)
        ).delete(synchronize_session=False)
        test_db.query(DomainEnvelopeObject).filter(
            DomainEnvelopeObject.envelope_id.in_(envelope_ids)
        ).delete(synchronize_session=False)
        test_db.query(DomainEnvelopeModel).filter(
            DomainEnvelopeModel.envelope_id.in_(envelope_ids)
        ).delete(synchronize_session=False)

    test_db.query(CurationExtractionResultRecord).filter(
        CurationExtractionResultRecord.document_id == document_id
    ).delete(synchronize_session=False)
    test_db.query(PDFDocument).filter(PDFDocument.id == document_id).delete(synchronize_session=False)
    test_db.query(User).filter(User.auth_sub == current_user_auth_sub).delete(synchronize_session=False)
    test_db.commit()


def _gene_envelope_extraction_payload(
    submission_e2e_context,
    *,
    extraction_result_id: str,
    origin_session_id: str,
    trace_id: str,
    flow_run_id: str | None,
    envelope_id: str,
) -> dict[str, object]:
    return {
        "extraction_result_id": extraction_result_id,
        "document_id": submission_e2e_context["document_id"],
        "adapter_key": "gene",
        "agent_key": "gene_extractor",
        "source_kind": "chat",
        "origin_session_id": origin_session_id,
        "trace_id": trace_id,
        "flow_run_id": flow_run_id,
        "user_id": submission_e2e_context["current_user_auth_sub"],
        "candidate_count": 1,
        "conversation_summary": "Conversation focused on evidence-backed extraction findings.",
        "payload_json": {
            "summary": "Prepared one domain-envelope fixture object.",
            "curatable_objects": [
                {
                    "object_type": "gene_mention_evidence",
                    "object_role": "validated_reference",
                    "pending_ref_id": f"{envelope_id}-object-1",
                    "payload": {
                        "gene_symbol": "alpha-1",
                        "entity_type": "gene",
                        "normalized_id": "FB:FBgn0000008",
                        "source_mentions": ["Alpha mention"],
                    },
                    "field_refs": [
                        {
                            "object_ref": {
                                "pending_ref_id": f"{envelope_id}-object-1",
                                "object_type": "gene_mention_evidence",
                            },
                            "field_path": "gene_symbol",
                        },
                        {
                            "object_ref": {
                                "pending_ref_id": f"{envelope_id}-object-1",
                                "object_type": "gene_mention_evidence",
                            },
                            "field_path": "entity_type",
                        },
                        {
                            "object_ref": {
                                "pending_ref_id": f"{envelope_id}-object-1",
                                "object_type": "gene_mention_evidence",
                            },
                            "field_path": "normalized_id",
                        },
                        {
                            "object_ref": {
                                "pending_ref_id": f"{envelope_id}-object-1",
                                "object_type": "gene_mention_evidence",
                            },
                            "field_path": "source_mentions[0]",
                        },
                    ],
                    "evidence_record_ids": ["alpha-1-evidence-1"],
                    "metadata": {
                        "semantic_source": "curatable_objects",
                        "workspace_display": {
                            "primary_label_field": "gene_symbol",
                            "secondary_label_field": "normalized_id",
                            "summary_fields": [
                                "gene_symbol",
                                "entity_type",
                                "normalized_id",
                                "source_mentions[0]",
                            ],
                            "projection_key": f"{envelope_id}-object-1",
                        },
                    },
                }
            ],
            "metadata": {
                "evidence_records": [
                    {
                        "evidence_record_id": "alpha-1-evidence-1",
                        "entity": "alpha-1",
                        "verified_quote": "alpha-1 was supported by a verified observation.",
                        "page": 6,
                        "section": "Results",
                        "subsection": "Observation set",
                        "chunk_id": "chunk-alpha-1",
                        "figure_reference": "Figure 3B",
                    }
                ],
                "provenance": {"semantic_source": "curatable_objects"},
            },
            "run_summary": {"candidate_count": 1, "kept_count": 1},
        },
        "created_at": "2026-03-28T12:00:00Z",
        "metadata": {"envelope_id": envelope_id},
    }


@pytest.mark.asyncio
async def test_deterministic_prep_bootstrap_materializes_domain_envelope_review_rows(
    submission_e2e_context,
    test_db,
):
    from src.lib.curation_workspace.bootstrap_service import bootstrap_document_session
    from src.lib.curation_workspace.curation_prep_service import (
        CurationPrepPersistenceContext,
        run_curation_prep,
    )
    from src.lib.curation_workspace.session_service import get_session_workspace
    from src.schemas.curation_prep import CurationPrepScopeConfirmation
    from src.schemas.curation_workspace import (
        CurationDocumentBootstrapRequest,
        CurationExtractionResultRecord,
        CurationExtractionSourceKind,
    )

    extraction_result = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "extract-observation-1",
            "document_id": submission_e2e_context["document_id"],
            "adapter_key": "gene",
            "agent_key": "gene_extractor",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "chat-session-1",
            "trace_id": "trace-observation-1",
            "flow_run_id": None,
            "user_id": submission_e2e_context["current_user_auth_sub"],
            "candidate_count": 1,
            "conversation_summary": "Conversation focused on evidence-backed extraction findings.",
            "payload_json": {
                "summary": "Prepared one domain-envelope fixture object.",
                "curatable_objects": [
                    {
                        "object_type": "gene_mention_evidence",
                        "object_role": "validated_reference",
                        "pending_ref_id": "gene-fixture-review-object-1",
                        "payload": {
                            "gene_symbol": "alpha-1",
                            "entity_type": "gene",
                            "normalized_id": "FB:FBgn0000008",
                            "source_mentions": ["Alpha mention"],
                        },
                        "field_refs": [
                            {
                                "object_ref": {
                                    "pending_ref_id": "gene-fixture-review-object-1",
                                    "object_type": "gene_mention_evidence",
                                },
                                "field_path": "gene_symbol",
                            },
                            {
                                "object_ref": {
                                    "pending_ref_id": "gene-fixture-review-object-1",
                                    "object_type": "gene_mention_evidence",
                                },
                                "field_path": "entity_type",
                            },
                            {
                                "object_ref": {
                                    "pending_ref_id": "gene-fixture-review-object-1",
                                    "object_type": "gene_mention_evidence",
                                },
                                "field_path": "normalized_id",
                            },
                            {
                                "object_ref": {
                                    "pending_ref_id": "gene-fixture-review-object-1",
                                    "object_type": "gene_mention_evidence",
                                },
                                "field_path": "source_mentions[0]",
                            },
                        ],
                        "evidence_record_ids": ["alpha-1-evidence-1"],
                        "metadata": {
                            "semantic_source": "curatable_objects",
                            "workspace_display": {
                                "primary_label_field": "gene_symbol",
                                "secondary_label_field": "normalized_id",
                                "summary_fields": [
                                    "gene_symbol",
                                    "entity_type",
                                    "normalized_id",
                                    "source_mentions[0]",
                                ],
                                "projection_key": "gene-fixture-review-object-1",
                            }
                        },
                    }
                ],
                "metadata": {
                    "evidence_records": [
                        {
                            "evidence_record_id": "alpha-1-evidence-1",
                            "entity": "alpha-1",
                            "verified_quote": "alpha-1 was supported by a verified observation.",
                            "page": 6,
                            "section": "Results",
                            "subsection": "Observation set",
                            "chunk_id": "chunk-alpha-1",
                            "figure_reference": "Figure 3B",
                        }
                    ],
                    "provenance": {"semantic_source": "curatable_objects"},
                },
                "run_summary": {"candidate_count": 1, "kept_count": 1},
            },
            "created_at": "2026-03-28T12:00:00Z",
            "metadata": {"envelope_id": "gene-fixture-review-envelope"},
        }
    )

    prep_output = await run_curation_prep(
        [extraction_result],
        scope_confirmation=CurationPrepScopeConfirmation(
            confirmed=True,
            adapter_keys=["gene"],
            notes=["Confirmed from chat session bootstrap test."],
        ),
        db=test_db,
        persistence_context=CurationPrepPersistenceContext(
            origin_session_id="chat-session-1",
            user_id=submission_e2e_context["current_user_auth_sub"],
            source_kind=CurationExtractionSourceKind.CHAT,
        ),
    )

    assert prep_output.candidates == []
    assert prep_output.review_row_count == 1
    assert len(prep_output.envelope_refs) == 1

    bootstrap_response = await bootstrap_document_session(
        submission_e2e_context["document_id"],
        CurationDocumentBootstrapRequest(origin_session_id="chat-session-1"),
        current_user_id=submission_e2e_context["current_user_auth_sub"],
        db=test_db,
    )

    assert bootstrap_response.created is True
    assert bootstrap_response.session.adapter.adapter_key == "gene"
    assert bootstrap_response.session.progress.total_candidates == 1

    workspace = get_session_workspace(test_db, bootstrap_response.session.session_id)
    candidate = workspace.workspace.candidates[0]
    assert candidate.adapter_key == "gene"
    assert candidate.projection_ref.envelope_id == "gene-fixture-review-envelope"
    assert candidate.projection_ref.envelope_revision >= (
        prep_output.envelope_refs[0].envelope_revision
    )
    assert candidate.projection_ref.object_id == "gene-fixture-review-object-1"
    assert candidate.normalized_payload == {}
    assert candidate.metadata["semantic_source"] == "domain_envelope.objects"
    assert candidate.metadata["projection_key"] == "gene-fixture-review-object-1"
    label_field = next(
        field for field in candidate.draft.fields if field.field_key == "gene_symbol"
    )
    assert label_field.value == "alpha-1"
    assert [field.field_key for field in candidate.draft.fields] == [
        "gene_symbol",
        "entity_type",
        "normalized_id",
        "source_mentions[0]",
    ]
    assert candidate.evidence_anchors == []


def test_submission_workflow_e2e_with_retry_and_history(
    client: TestClient,
    submission_e2e_context,
    test_db,
    monkeypatch,
):
    from src.lib.curation_workspace import session_service
    from src.lib.curation_workspace.models import CurationReviewSession, CurationSubmissionRecord
    from src.lib.curation_workspace.submission_adapters import NoOpSubmissionAdapter
    from src.schemas.curation_workspace import (
        CurationActionType,
        CurationSessionStatus,
        CurationSubmissionStatus,
    )

    gate_case = _alliance_gate_case("gene")
    _prep_output, bootstrap_payload, workspace_payload = (
        _run_prep_and_bootstrap_domain_envelope(
            client,
            submission_e2e_context,
            test_db,
            envelope=gate_case["envelope"],
            adapter_key=gate_case["adapter_key"],
            case_key="submission-e2e",
        )
    )
    session_id = bootstrap_payload["session"]["session_id"]
    assert bootstrap_payload["session"]["adapter"]["adapter_key"] == "gene"
    assert bootstrap_payload["session"]["progress"]["total_candidates"] == 1
    assert workspace_payload["session"]["session_id"] == session_id
    assert workspace_payload["submission_history"] == []
    assert len(workspace_payload["candidates"]) == 1

    candidate = _candidate_for_object_type(
        workspace_payload,
        gate_case["target_object_type"],
    )
    candidate_id = candidate["candidate_id"]
    draft = candidate["draft"]
    string_field = next(
        field
        for field in draft["fields"]
        if isinstance(field.get("value"), str) and not field.get("read_only", False)
    )
    edited_value = f"{string_field['value']} (reviewed)"

    draft_response = client.patch(
        (
            "/api/curation-workspace/sessions/"
            f"{session_id}/candidates/{candidate_id}/draft"
        ),
        json={
            "session_id": session_id,
            "candidate_id": candidate_id,
            "draft_id": draft["draft_id"],
            "expected_version": draft["version"],
            "field_changes": [
                {
                    "field_key": string_field["field_key"],
                    "value": edited_value,
                }
            ],
            "autosave": True,
        },
    )
    assert draft_response.status_code == 200, draft_response.text
    draft_payload = draft_response.json()
    assert any(
        field["field_key"] == string_field["field_key"] and field["value"] == edited_value
        for field in draft_payload["draft"]["fields"]
    )
    assert draft_payload["action_log_entry"]["action_type"] == "candidate_updated"

    decision_response = client.post(
        f"/api/curation-workspace/candidates/{candidate_id}/decision",
        json={
            "session_id": session_id,
            "candidate_id": candidate_id,
            "action": "accept",
            "advance_queue": True,
        },
    )
    assert decision_response.status_code == 200, decision_response.text
    decision_payload = decision_response.json()
    assert decision_payload["candidate"]["status"] == "accepted"

    validation_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/validate-all",
        json={"session_id": session_id},
    )
    assert validation_response.status_code == 200, validation_response.text
    validation_payload = validation_response.json()
    assert validation_payload["session"]["session_id"] == session_id
    assert len(validation_payload["candidate_validations"]) == 1
    assert validation_payload["candidate_validations"][0]["candidate_id"] == candidate_id

    preview_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/submission-preview",
        json={
            "session_id": session_id,
            "mode": "export",
            "target_key": gate_case["target_key"],
            "include_payload": True,
        },
    )
    assert preview_response.status_code == 200, preview_response.text
    preview_payload = preview_response.json()
    assert preview_payload["submission"]["status"] == "export_ready"
    assert preview_payload["submission"]["payload"]["candidate_ids"] == [candidate_id]
    assert preview_payload["submission"]["payload"]["payload_json"]["candidate_count"] == 1

    _patch_submission_transport_adapter(
        monkeypatch,
        lambda _target_key: NoOpSubmissionAdapter(
            target_key=gate_case["target_key"],
            response_status=CurationSubmissionStatus.FAILED,
        ),
    )
    failed_submit_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/submit",
        json={
            "session_id": session_id,
            "target_key": gate_case["target_key"],
        },
    )
    assert failed_submit_response.status_code == 200, failed_submit_response.text
    failed_submit_payload = failed_submit_response.json()
    failed_submission_id = failed_submit_payload["submission"]["submission_id"]
    assert failed_submit_payload["submission"]["status"] == "failed"
    assert failed_submit_payload["action_log_entry"]["action_type"] == "submission_executed"

    session_row = test_db.get(CurationReviewSession, UUID(session_id))
    assert session_row is not None
    assert session_row.status != CurationSessionStatus.SUBMITTED

    persisted_failed_submission = test_db.get(
        CurationSubmissionRecord,
        UUID(failed_submission_id),
    )
    assert persisted_failed_submission is not None
    assert persisted_failed_submission.status == CurationSubmissionStatus.FAILED

    _patch_submission_transport_adapter(
        monkeypatch,
        lambda _target_key: NoOpSubmissionAdapter(
            target_key=gate_case["target_key"]
        ),
    )
    retry_response = client.post(
        (
            "/api/curation-workspace/sessions/"
            f"{session_id}/submissions/{failed_submission_id}/retry"
        ),
        json={
            "submission_id": failed_submission_id,
            "reason": "Retry after downstream transport recovered.",
        },
    )
    assert retry_response.status_code == 200, retry_response.text
    retry_payload = retry_response.json()
    retried_submission_id = retry_payload["submission"]["submission_id"]
    assert retried_submission_id != failed_submission_id
    assert retry_payload["submission"]["status"] == "accepted"
    assert retry_payload["action_log_entry"]["action_type"] == "submission_retried"
    assert retry_payload["action_log_entry"]["metadata"]["original_submission_id"] == failed_submission_id

    history_response = client.get(
        (
            "/api/curation-workspace/sessions/"
            f"{session_id}/submissions/{retried_submission_id}"
        )
    )
    assert history_response.status_code == 200, history_response.text
    history_payload = history_response.json()
    assert history_payload["submission"]["submission_id"] == retried_submission_id
    assert history_payload["submission"]["status"] == "accepted"
    assert history_payload["submission"]["external_reference"] == (
        f"noop:{gate_case['target_key']}:1"
    )

    final_workspace_response = client.get(
        f"/api/curation-workspace/sessions/{session_id}",
        params={"include_workspace": "true"},
    )
    assert final_workspace_response.status_code == 200, final_workspace_response.text
    final_workspace_payload = final_workspace_response.json()["workspace"]
    assert [entry["status"] for entry in final_workspace_payload["submission_history"]] == [
        "failed",
        "accepted",
    ]

    final_session_row = test_db.get(CurationReviewSession, UUID(session_id))
    assert final_session_row is not None
    assert final_session_row.status == CurationSessionStatus.SUBMITTED

    action_types = [
        row.action_type.value
        for row in (
            test_db.query(session_service.SessionActionLogModel)
            .filter(session_service.SessionActionLogModel.session_id == UUID(session_id))
            .order_by(session_service.SessionActionLogModel.occurred_at.asc())
            .all()
        )
    ]
    assert CurationActionType.SUBMISSION_EXECUTED.value in action_types
    assert CurationActionType.SUBMISSION_RETRIED.value in action_types


@pytest.mark.parametrize(
    "case_key",
    (
        "gene",
        "gene_expression",
        "allele",
        "disease",
        "phenotype",
    ),
)
def test_alliance_domain_pack_gate_materializes_review_and_export_from_envelopes(
    client: TestClient,
    submission_e2e_context,
    test_db,
    case_key: str,
):
    from src.lib.curation_workspace.models import (
        DomainEnvelopeModel,
        DomainEnvelopeObject,
    )

    gate_case = _alliance_gate_case(case_key)
    envelope = gate_case["envelope"]
    _prep_output, bootstrap_payload, workspace_payload = (
        _run_prep_and_bootstrap_domain_envelope(
            client,
            submission_e2e_context,
            test_db,
            envelope=envelope,
            adapter_key=gate_case["adapter_key"],
            case_key=case_key,
        )
    )

    session_id = bootstrap_payload["session"]["session_id"]
    _assert_workspace_candidates_use_persisted_envelopes(workspace_payload)
    expected_envelope_revision = workspace_payload["candidates"][0]["projection_ref"][
        "envelope_revision"
    ]

    envelope_row = test_db.get(DomainEnvelopeModel, envelope.envelope_id)
    assert envelope_row is not None
    assert envelope_row.revision == expected_envelope_revision
    assert envelope_row.envelope_json["metadata"]["semantic_source"] == (
        "domain_envelope.objects"
    )
    assert FORBIDDEN_LEGACY_SEMANTIC_KEYS.isdisjoint(
        set(_iter_mapping_keys(envelope_row.envelope_json))
    )
    assert (
        test_db.query(DomainEnvelopeObject)
        .filter(DomainEnvelopeObject.envelope_id == envelope.envelope_id)
        .count()
        == len(envelope.objects)
    )

    target_candidate = _candidate_for_object_type(
        workspace_payload,
        gate_case["target_object_type"],
    )
    candidate_id = target_candidate["candidate_id"]
    _accept_candidate(client, session_id=session_id, candidate_id=candidate_id)

    preview_request = {
        "session_id": session_id,
        "candidate_ids": [candidate_id],
        "mode": "export",
        "target_key": gate_case["target_key"],
        "include_payload": True,
    }
    preview_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/submission-preview",
        json=preview_request,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview_payload = preview_response.json()
    readiness = preview_payload["submission"]["readiness"]
    assert len(readiness) == 1
    assert readiness[0]["candidate_id"] == candidate_id
    assert readiness[0]["ready"] is gate_case["expected_ready"], readiness[0]

    blocker_codes = {
        blocker["code"]
        for readiness_item in readiness
        for blocker in readiness_item["blockers"]
    }
    if gate_case["expected_ready"]:
        assert blocker_codes == set()
        assert preview_payload["submission"]["payload"]["candidate_ids"] == [candidate_id]
        assert preview_payload["submission"]["payload"]["payload_json"]["candidate_count"] == 1
    else:
        assert blocker_codes & gate_case["expected_blocker_codes"]
        assert preview_payload["submission"]["payload"]["payload_json"][
            "readiness_blockers"
        ]


def test_tmem67_gene_expression_e2e_repairs_exports_and_records_submission_history(
    client: TestClient,
    submission_e2e_context,
    test_db,
):
    from agr_ai_curation_alliance.domain_packs.gene_expression import (
        GENE_EXPRESSION_MODEL_ID,
        GENE_EXPRESSION_OBJECT_TYPE,
        GENE_EXPRESSION_TARGET_KEY,
    )
    from src.lib.curation_workspace.models import (
        CurationCandidate,
        CurationReviewSession,
        DomainEnvelopeHistory,
        DomainEnvelopeModel,
    )
    from src.schemas.curation_workspace import CurationSessionStatus

    envelope_id = "gene-expression-tmem67-repair-e2e-envelope"
    envelope = _tmem67_missing_where_statement_envelope(envelope_id=envelope_id)
    _prep_output, bootstrap_payload, workspace_payload = (
        _run_prep_and_bootstrap_domain_envelope(
            client,
            submission_e2e_context,
            test_db,
            envelope=envelope,
            adapter_key="gene_expression",
            case_key="tmem67-repair",
        )
    )
    session_id = bootstrap_payload["session"]["session_id"]
    _assert_workspace_candidates_use_persisted_envelopes(workspace_payload)

    target_candidate = _candidate_for_object_type(
        workspace_payload,
        GENE_EXPRESSION_OBJECT_TYPE,
    )
    candidate_id = target_candidate["candidate_id"]
    object_id = target_candidate["projection_ref"]["object_id"]
    initial_envelope_revision = target_candidate["projection_ref"]["envelope_revision"]
    _accept_candidate(client, session_id=session_id, candidate_id=candidate_id)

    blocked_preview_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/submission-preview",
        json={
            "session_id": session_id,
            "candidate_ids": [candidate_id],
            "mode": "direct_submit",
            "target_key": GENE_EXPRESSION_TARGET_KEY,
            "include_payload": True,
        },
    )
    assert blocked_preview_response.status_code == 200, blocked_preview_response.text
    blocked_readiness = blocked_preview_response.json()["submission"]["readiness"][0]
    assert blocked_readiness["ready"] is False
    assert {
        (blocker["code"], blocker["field_path"])
        for blocker in blocked_readiness["blockers"]
    } >= {("domain_envelope.required_field_missing", "where_expressed_statement")}

    envelope_row = test_db.get(DomainEnvelopeModel, envelope_id)
    assert envelope_row is not None
    assert envelope_row.revision >= initial_envelope_revision
    expected_envelope_revision = envelope_row.revision

    repaired_statement = "Tmem67 expression was detected in the metanephros."
    patch_response = client.patch(
        f"/api/curation-workspace/sessions/{session_id}/envelopes/{envelope_id}/field",
        json={
            "session_id": session_id,
            "envelope_id": envelope_id,
            "expected_revision": expected_envelope_revision,
            "object_id": object_id,
            "field_path": "where_expressed_statement",
            "operation": "replace",
            "before": None,
            "value": repaired_statement,
            "reason": "Curator resolved the required expression statement.",
        },
    )
    assert patch_response.status_code == 200, patch_response.text
    patch_payload = patch_response.json()
    repaired_envelope_revision = expected_envelope_revision + 1
    assert patch_payload["accepted"] is True
    assert patch_payload["previous_revision"] == expected_envelope_revision
    assert patch_payload["envelope_revision"] == repaired_envelope_revision
    assert (
        patch_payload["candidate"]["projection_ref"]["envelope_revision"]
        == repaired_envelope_revision
    )
    assert patch_payload["candidate"]["normalized_payload"] == {}
    assert patch_payload["history_event_ids"]

    poisoned_normalized_payload = {
        "source_payload": {
            "where_expressed_statement": "POISONED STALE NORMALIZED PAYLOAD"
        }
    }
    candidate_row = test_db.get(CurationCandidate, UUID(candidate_id))
    assert candidate_row is not None
    candidate_row.normalized_payload = poisoned_normalized_payload
    test_db.add(candidate_row)
    test_db.commit()

    submit_response = client.post(
        f"/api/curation-workspace/sessions/{session_id}/submit",
        json={
            "session_id": session_id,
            "candidate_ids": [candidate_id],
            "mode": "direct_submit",
            "target_key": GENE_EXPRESSION_TARGET_KEY,
        },
    )
    assert submit_response.status_code == 200, submit_response.text
    submit_payload = submit_response.json()
    submission = submit_payload["submission"]
    assert submission["status"] == "manual_review_required"
    assert submission["payload"]["candidate_ids"] == [candidate_id]
    assert "POISONED" not in json.dumps(submission["payload"]["payload_json"])
    assert submission["payload"]["payload_json"]["domain_pack_id"] == (
        envelope.domain_pack_id
    )
    assert submission["payload"]["payload_json"]["domain_pack_version"] == (
        envelope.domain_pack_version
    )
    assert submission["payload"]["payload_json"]["schema_ref"] == {
        "class": "GeneExpressionAnnotation",
        "name": "GeneExpressionAnnotation",
        "provider": "alliance_linkml",
        "schema_id": "alliance.linkml.GeneExpressionAnnotation",
        "source_file": "model/schema/expression.yaml",
        "uri": (
            "https://github.com/alliance-genome/agr_curation_schema/blob/"
            "1b11d0888f19eba4ca72022200bb7d96b30d4a52/model/schema/expression.yaml"
        ),
        "version": "1b11d0888f19eba4ca72022200bb7d96b30d4a52",
    }
    annotation = submission["payload"]["payload_json"]["gene_expression_annotations"][0]
    assert annotation["envelope"]["domain_pack_id"] == envelope.domain_pack_id
    assert annotation["envelope"]["domain_pack_version"] == envelope.domain_pack_version
    assert annotation["envelope"]["envelope_id"] == envelope_id
    exported_envelope_revision = annotation["envelope"]["envelope_revision"]
    assert exported_envelope_revision >= repaired_envelope_revision
    assert annotation["envelope"]["model_ref"] == GENE_EXPRESSION_MODEL_ID
    assert annotation["envelope"]["object_id"] == object_id
    assert annotation["envelope"]["object_type"] == GENE_EXPRESSION_OBJECT_TYPE
    assert annotation["envelope"]["schema_ref"]["schema_id"] == (
        "alliance.linkml.GeneExpressionAnnotation"
    )
    assert annotation["source_payload"]["where_expressed_statement"] == repaired_statement
    assert submission["submission_state"]["write_mode"] == "read_only_handoff"
    assert submission["submission_state"]["envelope_revisions"] == [
        {"envelope_id": envelope_id, "envelope_revision": exported_envelope_revision}
    ]

    envelope_row = test_db.get(DomainEnvelopeModel, envelope_id)
    assert envelope_row is not None
    assert envelope_row.revision == exported_envelope_revision
    persisted_object = next(
        item
        for item in envelope_row.envelope_json["objects"]
        if item["object_type"] == GENE_EXPRESSION_OBJECT_TYPE
    )
    assert persisted_object["payload"]["where_expressed_statement"] == repaired_statement

    candidate_row = test_db.get(CurationCandidate, UUID(candidate_id))
    assert candidate_row is not None
    assert candidate_row.envelope_revision == exported_envelope_revision
    assert candidate_row.normalized_payload == poisoned_normalized_payload

    history_event_types = [
        row.event_type.value
        for row in (
            test_db.query(DomainEnvelopeHistory)
            .filter(DomainEnvelopeHistory.envelope_id == envelope_id)
            .order_by(DomainEnvelopeHistory.event_index.asc())
            .all()
        )
    ]
    assert "field_updated" in history_event_types
    assert "curator_field_patch_accepted" in history_event_types
    assert "submitted" in history_event_types

    session_row = test_db.get(CurationReviewSession, UUID(session_id))
    assert session_row is not None
    assert session_row.status == CurationSessionStatus.SUBMITTED


@pytest.mark.parametrize(
    "adapter_key",
    ("gene", "gene_expression", "allele", "disease", "chemical", "phenotype"),
)
def test_domain_envelope_prep_rejects_legacy_semantic_payloads_for_current_packs(
    submission_e2e_context,
    test_db,
    adapter_key: str,
):
    from src.lib.curation_workspace.curation_prep_service import (
        CurationPrepPersistenceContext,
        run_curation_prep,
    )
    from src.lib.curation_workspace.models import DomainEnvelopeModel
    from src.schemas.curation_prep import CurationPrepScopeConfirmation
    from src.schemas.curation_workspace import (
        CurationExtractionResultRecord,
        CurationExtractionSourceKind,
    )

    extraction_result = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": f"legacy-{adapter_key}-payload",
            "document_id": submission_e2e_context["document_id"],
            "adapter_key": adapter_key,
            "agent_key": f"{adapter_key}_extractor",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": f"legacy-chat-{adapter_key}",
            "trace_id": f"legacy-trace-{adapter_key}",
            "user_id": submission_e2e_context["current_user_auth_sub"],
            "candidate_count": 1,
            "conversation_summary": "Legacy semantic-list extraction payload.",
            "payload_json": {
                "summary": "Legacy output must not be materialized.",
                "items": [{"label": "legacy semantic item"}],
                "run_summary": {"candidate_count": 1, "kept_count": 1},
            },
            "created_at": "2026-05-10T13:00:00Z",
            "metadata": {"project_key": "agr"},
        }
    )

    with pytest.raises(
        ValueError,
        match="No evidence-verified candidates were available",
    ):
        asyncio.run(
            run_curation_prep(
                [extraction_result],
                scope_confirmation=CurationPrepScopeConfirmation(
                    confirmed=True,
                    adapter_keys=[adapter_key],
                    notes=["Legacy semantic payload should be rejected."],
                ),
                db=test_db,
                persistence_context=CurationPrepPersistenceContext(
                    origin_session_id=f"legacy-chat-{adapter_key}",
                    user_id=submission_e2e_context["current_user_auth_sub"],
                    source_kind=CurationExtractionSourceKind.CHAT,
                ),
            )
        )

    assert (
        test_db.query(DomainEnvelopeModel)
        .filter(DomainEnvelopeModel.document_id == UUID(submission_e2e_context["document_id"]))
        .count()
        == 0
    )
