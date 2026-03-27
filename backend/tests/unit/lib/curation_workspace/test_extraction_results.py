"""Unit tests for extraction-result persistence helpers."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.curation_workspace.extraction_results import (
    build_extraction_envelope_candidate,
    list_extraction_results,
    persist_extraction_result,
    persist_extraction_results,
)
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionSourceKind,
)


def _sample_envelope_payload() -> dict:
    return {
        "adapter_key": "reference_adapter",
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


def _sample_legacy_envelope_payload() -> dict:
    payload = _sample_envelope_payload()
    payload.pop("adapter_key")
    return payload


class _FakeSession:
    def __init__(self, *, fail_commit: bool = False):
        self.fail_commit = fail_commit
        self.added = None
        self.added_records = []
        self.commit_calls = 0
        self.refresh_calls = 0
        self.rollback_calls = 0
        self.closed = False

    def add(self, record):
        self.added = record
        self.added_records.append(record)

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


class _Field:
    def __init__(self, name=None):
        self.name = name

    def __eq__(self, _other):
        if self.name is None:
            return True
        return _FilterCondition(field_name=self.name, operator="eq", value=_other)

    def in_(self, _values):
        return True

    def notin_(self, values):
        if self.name is None:
            return True
        return _FilterCondition(field_name=self.name, operator="not_in", value=tuple(values))

    def asc(self):
        if self.name is None:
            return self
        return _SortField(self.name)


class _FilterCondition:
    def __init__(self, *, field_name, operator, value):
        self.field_name = field_name
        self.operator = operator
        self.value = value


class _SortField:
    def __init__(self, field_name):
        self.field_name = field_name


class _FakeSelectStatement:
    def __init__(self):
        self.conditions = []
        self.order_fields = []

    def order_by(self, *fields):
        self.order_fields.extend(fields)
        return self

    def where(self, *conditions):
        self.conditions.extend(conditions)
        return self


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows, statement):
        self._rows = rows
        self._statement = statement

    def scalars(self):
        rows = list(self._rows)
        for condition in self._statement.conditions:
            if condition.operator == "eq":
                rows = [
                    row
                    for row in rows
                    if getattr(row, condition.field_name) == condition.value
                ]
            elif condition.operator == "not_in":
                rows = [
                    row
                    for row in rows
                    if getattr(row, condition.field_name) not in condition.value
                ]
            else:
                raise AssertionError(f"Unsupported operator in fake select evaluator: {condition.operator}")

        for sort_field in reversed(self._statement.order_fields):
            rows.sort(key=lambda row: getattr(row, sort_field.field_name))

        return _FakeScalarResult(rows)


class _FakeSelectSession:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False
        self.execute_calls = 0
        self.last_statement = None

    def execute(self, statement):
        self.execute_calls += 1
        self.last_statement = statement
        return _FakeExecuteResult(self._rows, statement)

    def close(self):
        self.closed = True


def _fake_select_model():
    return SimpleNamespace(
        origin_session_id=_Field("origin_session_id"),
        user_id=_Field("user_id"),
        source_kind=_Field("source_kind"),
        document_id=_Field("document_id"),
        agent_key=_Field("agent_key"),
        created_at=_Field("created_at"),
        id=_Field("id"),
    )


def _record_row(**overrides):
    payload = {
        "id": 1,
        "document_id": uuid4(),
        "adapter_key": "reference_adapter",
        "profile_key": None,
        "domain_key": "disease",
        "agent_key": "disease_extractor",
        "source_kind": CurationExtractionSourceKind.CHAT,
        "origin_session_id": "session-1",
        "trace_id": "trace-1",
        "flow_run_id": None,
        "user_id": "user-1",
        "candidate_count": 1,
        "conversation_summary": "summary",
        "payload_json": _sample_envelope_payload(),
        "created_at": datetime(2026, 3, 21, 0, 0, tzinfo=timezone.utc),
        "extraction_metadata": {},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_build_extraction_envelope_candidate_parses_json_tool_output():
    candidate = build_extraction_envelope_candidate(
        json.dumps(_sample_envelope_payload()),
        agent_key="gene-expression",
        conversation_summary="Extract gene expression findings",
        metadata={"tool_name": "ask_gene_expression_specialist"},
    )

    assert candidate is not None
    assert candidate.agent_key == "gene-expression"
    assert candidate.adapter_key == "reference_adapter"
    assert candidate.candidate_count == 2
    assert candidate.domain_key == "gene_expression"
    assert candidate.payload_json["items"] == [{"label": "notch"}]
    assert candidate.metadata["tool_name"] == "ask_gene_expression_specialist"
    assert candidate.metadata["envelope_actor"] == "gene_expression_specialist"
    assert candidate.metadata["envelope_destination"] == "gene_expression"


def test_build_extraction_envelope_candidate_prefers_envelope_adapter_key_over_caller_fallback():
    candidate = build_extraction_envelope_candidate(
        json.dumps(_sample_envelope_payload()),
        agent_key="gene-expression",
        adapter_key="caller_adapter",
        domain_key="caller_domain",
    )

    assert candidate is not None
    assert candidate.adapter_key == "reference_adapter"
    assert candidate.domain_key == "caller_domain"


def test_build_extraction_envelope_candidate_uses_destination_as_adapter_fallback():
    candidate = build_extraction_envelope_candidate(
        json.dumps(_sample_legacy_envelope_payload()),
        agent_key="gene-expression",
        adapter_key="caller_adapter",
    )

    assert candidate is not None
    assert candidate.adapter_key == "gene_expression"
    assert candidate.domain_key == "gene_expression"


def test_build_extraction_envelope_candidate_defaults_reference_adapter_and_inferrs_domain():
    candidate = build_extraction_envelope_candidate(
        json.dumps(
            {
                "genes": [{"mention": "tinman"}],
                "items": [{"label": "tinman"}],
                "raw_mentions": [{"mention": "tinman", "evidence": []}],
                "exclusions": [],
                "ambiguities": [],
                "run_summary": {
                    "candidate_count": 1,
                    "kept_count": 1,
                    "excluded_count": 0,
                    "ambiguous_count": 0,
                    "warnings": [],
                },
            }
        ),
        agent_key="gene_extractor",
    )

    assert candidate is not None
    assert candidate.adapter_key == "reference_adapter"
    assert candidate.domain_key == "gene"
    assert candidate.metadata["inferred_adapter_key"] == "reference_adapter"
    assert candidate.metadata["inferred_domain_key"] == "gene"


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


def test_persist_extraction_result_sanitizes_nul_characters_before_persisting():
    session = _FakeSession()
    request = CurationExtractionPersistenceRequest(
        document_id=str(uuid4()),
        adapter_key="reference_adapter",
        domain_key="gene",
        agent_key="gene_extractor",
        source_kind=CurationExtractionSourceKind.CHAT,
        candidate_count=1,
        conversation_summary="Focus gene\x00 summary",
        payload_json={
            "items": [
                {
                    "gene": "wg\x00",
                    "evidence": {
                        "snippet": "266 \x00b1 51 fmoles",
                        "nested": ["ok", "bad\x00value"],
                    },
                }
            ],
            "raw_mentions": [],
            "exclusions": [],
            "ambiguities": [],
            "run_summary": {"candidate_count": 1},
            "bad\x00key": "value\x00",
        },
        metadata={
            "tool_name": "ask_gene_extractor_specialist",
            "evidence_preview": "A\x00B",
        },
    )

    response = persist_extraction_result(request, db=session)

    assert session.added is not None
    assert session.added.conversation_summary == "Focus gene summary"
    assert session.added.payload_json["items"][0]["gene"] == "wg"
    assert session.added.payload_json["items"][0]["evidence"]["snippet"] == "266 b1 51 fmoles"
    assert session.added.payload_json["items"][0]["evidence"]["nested"] == ["ok", "badvalue"]
    assert session.added.payload_json["badkey"] == "value"
    assert session.added.extraction_metadata == {
        "tool_name": "ask_gene_extractor_specialist",
        "evidence_preview": "AB",
    }
    assert response.extraction_result.metadata["evidence_preview"] == "AB"


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


def test_persist_extraction_results_writes_all_records_in_one_commit():
    session = _FakeSession()
    requests = [
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            agent_key="gene-expression",
            source_kind=CurationExtractionSourceKind.CHAT,
            payload_json=_sample_envelope_payload(),
        ),
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            agent_key="pdf-extraction",
            source_kind=CurationExtractionSourceKind.FLOW,
            payload_json=_sample_envelope_payload(),
        ),
    ]

    responses = persist_extraction_results(requests, db=session)

    assert len(session.added_records) == 2
    assert session.commit_calls == 1
    assert session.refresh_calls == 2
    assert session.rollback_calls == 0
    assert len(responses) == 2
    assert responses[0].extraction_result.agent_key == "gene-expression"
    assert responses[1].extraction_result.agent_key == "pdf-extraction"


def test_persist_extraction_results_rolls_back_batch_on_commit_error():
    session = _FakeSession(fail_commit=True)
    requests = [
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            agent_key="gene-expression",
            source_kind=CurationExtractionSourceKind.CHAT,
            payload_json=_sample_envelope_payload(),
        ),
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            agent_key="pdf-extraction",
            source_kind=CurationExtractionSourceKind.FLOW,
            payload_json=_sample_envelope_payload(),
        ),
    ]

    with pytest.raises(RuntimeError, match="db write failed"):
        persist_extraction_results(requests, db=session)

    assert len(session.added_records) == 2
    assert session.commit_calls == 1
    assert session.rollback_calls == 1
    assert session.refresh_calls == 0


def test_list_extraction_results_returns_empty_for_invalid_document_id(monkeypatch, caplog):
    session = _FakeSelectSession(rows=[])

    monkeypatch.setattr(
        "src.lib.curation_workspace.extraction_results.CurationExtractionResultRecordModel",
        _fake_select_model(),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.extraction_results.select",
        lambda _model: _FakeSelectStatement(),
    )

    with caplog.at_level("WARNING"):
        results = list_extraction_results(
            origin_session_id="session-1",
            user_id="user-1",
            source_kind=CurationExtractionSourceKind.CHAT,
            document_id="not-a-uuid",
            db=session,
        )

    assert results == []
    assert session.execute_calls == 0
    assert "Ignoring invalid document_id filter" in caplog.text


def test_list_extraction_results_applies_filters_and_ordering(monkeypatch):
    document_id = uuid4()
    kept_first = _record_row(
        id=1,
        document_id=document_id,
        created_at=datetime(2026, 3, 21, 0, 1, tzinfo=timezone.utc),
    )
    kept_second = _record_row(
        id=3,
        document_id=document_id,
        created_at=datetime(2026, 3, 21, 0, 2, tzinfo=timezone.utc),
        extraction_metadata={"tool_name": "ask_disease_specialist"},
    )
    rows = [
        _record_row(
            id=7,
            document_id=document_id,
            origin_session_id="other-session",
            created_at=datetime(2026, 3, 21, 0, 7, tzinfo=timezone.utc),
        ),
        _record_row(
            id=6,
            document_id=document_id,
            user_id="user-2",
            created_at=datetime(2026, 3, 21, 0, 6, tzinfo=timezone.utc),
        ),
        _record_row(
            id=5,
            document_id=document_id,
            source_kind=CurationExtractionSourceKind.FLOW,
            created_at=datetime(2026, 3, 21, 0, 5, tzinfo=timezone.utc),
        ),
        _record_row(
            id=4,
            document_id=document_id,
            agent_key="curation_prep",
            created_at=datetime(2026, 3, 21, 0, 4, tzinfo=timezone.utc),
        ),
        _record_row(
            id=2,
            document_id=uuid4(),
            created_at=datetime(2026, 3, 21, 0, 3, tzinfo=timezone.utc),
        ),
        kept_second,
        kept_first,
    ]
    session = _FakeSelectSession(rows=rows)

    monkeypatch.setattr(
        "src.lib.curation_workspace.extraction_results.CurationExtractionResultRecordModel",
        _fake_select_model(),
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.extraction_results.select",
        lambda _model: _FakeSelectStatement(),
    )

    results = list_extraction_results(
        origin_session_id="session-1",
        user_id="user-1",
        source_kind=CurationExtractionSourceKind.CHAT,
        document_id=str(document_id),
        exclude_agent_keys=["curation_prep", "   "],
        db=session,
    )

    assert [record.extraction_result_id for record in results] == ["1", "3"]
    assert [record.agent_key for record in results] == ["disease_extractor", "disease_extractor"]
    assert results[1].metadata == {"tool_name": "ask_disease_specialist"}
    assert session.execute_calls == 1
    assert [field.field_name for field in session.last_statement.order_fields] == ["created_at", "id"]
