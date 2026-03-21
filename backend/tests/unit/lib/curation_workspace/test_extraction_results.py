"""Unit tests for extraction-result persistence helpers."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.lib.curation_workspace.extraction_results import (
    build_extraction_envelope_candidate,
    persist_extraction_result,
)
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionSourceKind,
)


def _sample_envelope_payload() -> dict:
    return {
        "actor": "gene_expression_specialist",
        "destination": "gene_expression",
        "confidence": 0.92,
        "reasoning": "Structured extraction completed.",
        "items": [{"label": "notch"}],
        "raw_mentions": [{"mention": "Notch", "evidence": []}],
        "exclusions": [],
        "ambiguities": [],
        "run_summary": {
            "candidate_count": 2,
            "kept_count": 1,
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
    }


class _FakeSession:
    def __init__(self, *, fail_commit: bool = False):
        self.fail_commit = fail_commit
        self.added = None
        self.commit_calls = 0
        self.refresh_calls = 0
        self.rollback_calls = 0
        self.closed = False

    def add(self, record):
        self.added = record

    def commit(self):
        self.commit_calls += 1
        if self.fail_commit:
            raise RuntimeError("db write failed")

    def refresh(self, record):
        self.refresh_calls += 1
        record.id = uuid4()
        record.created_at = datetime.now(timezone.utc)

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.closed = True


def test_build_extraction_envelope_candidate_parses_json_tool_output():
    candidate = build_extraction_envelope_candidate(
        json.dumps(_sample_envelope_payload()),
        agent_key="gene-expression",
        conversation_summary="Extract gene expression findings",
        metadata={"tool_name": "ask_gene_expression_specialist"},
    )

    assert candidate is not None
    assert candidate.agent_key == "gene-expression"
    assert candidate.candidate_count == 2
    assert candidate.domain_key == "gene_expression"
    assert candidate.payload_json["items"] == [{"label": "notch"}]
    assert candidate.metadata["tool_name"] == "ask_gene_expression_specialist"
    assert candidate.metadata["envelope_actor"] == "gene_expression_specialist"
    assert candidate.metadata["envelope_destination"] == "gene_expression"


def test_build_extraction_envelope_candidate_ignores_non_extraction_payload():
    candidate = build_extraction_envelope_candidate(
        json.dumps({"file_id": "file-1", "filename": "export.csv"}),
        agent_key="gene-expression",
    )

    assert candidate is None


def test_persist_extraction_result_writes_record_and_returns_schema():
    session = _FakeSession()
    request = CurationExtractionPersistenceRequest(
        document_id=str(uuid4()),
        adapter_key="gene_expression",
        domain_key="gene_expression",
        agent_key="gene-expression",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="session-1",
        trace_id="trace-1",
        user_id="user-1",
        candidate_count=2,
        conversation_summary="Extract gene expression findings",
        payload_json=_sample_envelope_payload(),
        metadata={"tool_name": "ask_gene_expression_specialist"},
    )

    response = persist_extraction_result(request, db=session)

    assert session.added is not None
    assert session.commit_calls == 1
    assert session.refresh_calls == 1
    assert session.rollback_calls == 0
    assert str(session.added.document_id) == request.document_id
    assert session.added.agent_key == "gene-expression"
    assert session.added.source_kind is CurationExtractionSourceKind.CHAT
    assert session.added.extraction_metadata == {"tool_name": "ask_gene_expression_specialist"}
    assert response.extraction_result.document_id == request.document_id
    assert response.extraction_result.agent_key == "gene-expression"
    assert response.extraction_result.metadata == {"tool_name": "ask_gene_expression_specialist"}


def test_persist_extraction_result_rolls_back_on_commit_error():
    session = _FakeSession(fail_commit=True)
    request = CurationExtractionPersistenceRequest(
        document_id=str(uuid4()),
        agent_key="gene-expression",
        source_kind=CurationExtractionSourceKind.FLOW,
        payload_json=_sample_envelope_payload(),
    )

    with pytest.raises(RuntimeError, match="db write failed"):
        persist_extraction_result(request, db=session)

    assert session.commit_calls == 1
    assert session.rollback_calls == 1
    assert session.refresh_calls == 0
