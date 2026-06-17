"""Unit tests for extraction-result persistence helpers."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from src.lib.curation_workspace import extraction_results as module
from src.lib.curation_workspace.extraction_results import (
    build_extraction_envelope_candidate,
    build_extraction_envelope_candidate_with_evidence,
    persist_inline_validated_extraction_result,
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


def _sample_domain_envelope_payload() -> dict:
    return {
        "summary": "Domain-envelope extraction completed.",
        "curatable_objects": [
            {
                "object_type": "gene",
                "pending_ref_id": "gene-notch",
                "payload": {"mention": "notch", "normalized_symbol": "N"},
                "evidence_record_ids": ["evidence-notch"],
            }
        ],
        "metadata": {
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-notch",
                    "entity": "notch",
                    "verified_quote": "notch was experimentally analyzed.",
                    "page": 4,
                    "section": "Results",
                    "chunk_id": "chunk-1",
                }
            ],
            "raw_mentions": [{"mention": "notch", "entity_type": "gene"}],
        },
        "run_summary": {
            "candidate_count": 1,
            "kept_count": 1,
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
    }


def _sample_persisted_domain_envelope_payload() -> dict:
    return {
        "envelope_id": "envelope-gene-notch",
        "domain_pack_id": "gene",
        "domain_pack_version": "0.1.0",
        "status": "extracted",
        "extracted_objects": [
            {
                "object_type": "gene_mention_evidence",
                "pending_ref_id": "gene-notch",
                "payload": {
                    "mention": "notch",
                    "primary_external_id": "FB:FBgn0004647",
                },
                "evidence_record_ids": ["evidence-notch"],
            }
        ],
        "validation_findings": [],
        "history": [],
        "metadata": {},
    }


class _FakeSession:
    def __init__(
        self,
        *,
        fail_commit: bool = False,
        fail_flush: bool = False,
        existing_rows=None,
    ):
        self.fail_commit = fail_commit
        self.fail_flush = fail_flush
        self.existing_rows = list(existing_rows or [])
        self.added = None
        self.added_records = []
        self.commit_calls = 0
        self.flush_calls = 0
        self.refresh_calls = 0
        self.rollback_calls = 0
        self.closed = False
        self.execute_calls = 0

    def add(self, record):
        self.added = record
        self.added_records.append(record)

    def execute(self, _statement):
        self.execute_calls += 1
        return _FakeExecuteRowsResult(self.existing_rows)

    def flush(self):
        self.flush_calls += 1
        if self.fail_flush:
            raise RuntimeError("db write failed")

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


class _RaceConflictSession:
    """Fake session that loses the unique-index race on insert.

    The pre-check idempotency lookup sees no row, the insert flush raises
    ``IntegrityError`` (the race winner committed first), and the post-rollback
    reload returns the existing row.
    """

    def __init__(self, *, race_winner_row):
        self._race_winner_row = race_winner_row
        self.added = None
        self.added_records = []
        self.flush_calls = 0
        self.commit_calls = 0
        self.refresh_calls = 0
        self.rollback_calls = 0
        self.execute_calls = 0
        self.closed = False
        self._insert_conflict_raised = False

    def add(self, record):
        self.added = record
        self.added_records.append(record)

    def execute(self, _statement):
        self.execute_calls += 1
        # Before the conflict, the pre-check sees no existing row. After the
        # rollback, the reload sees the row the race winner committed.
        rows = [self._race_winner_row] if self._insert_conflict_raised else []
        return _FakeExecuteRowsResult(rows)

    def flush(self):
        self.flush_calls += 1
        if not self._insert_conflict_raised:
            self._insert_conflict_raised = True
            raise IntegrityError(
                "INSERT INTO curation_extraction_results",
                {},
                Exception("duplicate key value violates unique constraint"),
            )

    def commit(self):
        self.commit_calls += 1

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

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeExecuteRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalarResult(self._rows)


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


def test_build_extraction_envelope_candidate_parses_json_tool_output(monkeypatch):
    monkeypatch.setattr(
        module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {
            "adapter_key": "gene",
            "launchable": True,
        },
    )
    candidate = build_extraction_envelope_candidate(
        json.dumps(_sample_envelope_payload()),
        agent_key="gene-expression",
        conversation_summary="Extract gene expression findings",
        metadata={"tool_name": "ask_gene_expression_specialist"},
    )

    assert candidate is not None
    assert candidate.agent_key == "gene-expression"
    assert candidate.adapter_key == "gene"
    assert candidate.candidate_count == 2
    assert candidate.payload_json["items"] == [{"label": "notch"}]
    assert candidate.metadata["tool_name"] == "ask_gene_expression_specialist"
    assert candidate.metadata["envelope_actor"] == "gene_expression_specialist"
    assert candidate.metadata["envelope_destination"] == "gene_expression"


def test_build_extraction_envelope_candidate_prefers_explicit_adapter_key_over_agent_metadata(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {
            "adapter_key": "gene",
            "launchable": True,
        },
    )
    candidate = build_extraction_envelope_candidate(
        json.dumps(_sample_envelope_payload()),
        agent_key="gene-expression",
        adapter_key="caller_adapter",
    )

    assert candidate is not None
    assert candidate.adapter_key == "caller_adapter"


def test_build_extraction_envelope_candidate_with_evidence_prefers_candidate_payload_for_evidence(
    monkeypatch,
):
    observed_payloads = []

    monkeypatch.setattr(
        module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {
            "adapter_key": "gene",
            "launchable": True,
        },
    )
    monkeypatch.setattr(
        module,
        "extract_evidence_records_from_structured_result",
        lambda payload: observed_payloads.append(payload) or [{"entity": "notch"}],
    )

    raw_output = json.dumps(_sample_envelope_payload())
    candidate, evidence_metadata = build_extraction_envelope_candidate_with_evidence(
        raw_output,
        agent_key="gene-expression",
        conversation_summary="Extract gene expression findings",
    )

    assert candidate is not None
    assert observed_payloads == [candidate.payload_json]
    assert evidence_metadata["evidence_count"] == 1
    assert evidence_metadata["evidence_records"] == [{"entity": "notch"}]


def test_build_extraction_envelope_candidate_accepts_domain_envelope_curatable_objects():
    candidate, evidence_metadata = build_extraction_envelope_candidate_with_evidence(
        json.dumps(_sample_domain_envelope_payload()),
        agent_key="gene_extractor",
        adapter_key="gene",
        conversation_summary="Extract domain-envelope gene findings",
    )

    assert candidate is not None
    assert candidate.agent_key == "gene_extractor"
    assert candidate.adapter_key == "gene"
    assert candidate.candidate_count == 1
    assert candidate.payload_json["curatable_objects"][0]["pending_ref_id"] == "gene-notch"
    assert evidence_metadata["evidence_count"] == 1
    assert evidence_metadata["evidence_records"][0]["evidence_record_id"] == "evidence-notch"


def test_gene_adapter_drops_zfin_compound_like_gene_objects():
    payload = _sample_domain_envelope_payload()
    payload["curatable_objects"] = [
        {
            "object_type": "gene_mention_evidence",
            "pending_ref_id": "gene-mention-evidence-her1",
            "payload": {
                "mention": "her1",
                "primary_external_id": "ZFIN:ZDB-GENE-980526-125",
                "gene_symbol": "her1",
                "taxon": "NCBITaxon:7955",
                "species": "Danio rerio",
                "data_provider_hint": "ZFIN",
                "evidence_record_id": "evidence-her1",
            },
            "evidence_record_ids": ["evidence-her1"],
        },
        {
            "object_type": "gene_mention_evidence",
            "pending_ref_id": "gene-mention-evidence-SB225002",
            "payload": {
                "mention": "SB225002",
                "species": "Danio rerio",
                "taxon_hint": "NCBITaxon:7955",
                "data_provider_hint": "ZFIN",
                "evidence_record_id": "evidence-sb225002",
            },
            "evidence_record_ids": ["evidence-sb225002"],
        },
    ]
    payload["metadata"]["evidence_records"] = [
        {
            "evidence_record_id": "evidence-her1",
            "entity": "her1",
            "verified_quote": "the her1 mutant background was analyzed.",
            "page": 1,
            "section": "Results",
            "chunk_id": "chunk-her1",
        },
        {
            "evidence_record_id": "evidence-sb225002",
            "entity": "SB225002",
            "verified_quote": "SB225002 caused boundary disruptions.",
            "page": 1,
            "section": "Results",
            "chunk_id": "chunk-sb225002",
        },
    ]

    candidate = build_extraction_envelope_candidate(
        json.dumps(payload),
        agent_key="gene_extractor",
        adapter_key="gene",
        conversation_summary="Extract cross-domain gene evidence.",
    )

    assert candidate is not None
    assert [obj["pending_ref_id"] for obj in candidate.payload_json["curatable_objects"]] == [
        "gene-mention-evidence-her1"
    ]
    assert candidate.payload_json["metadata"]["exclusions"][-1] == {
        "mention": "SB225002",
        "reason_code": "unsupported_entity_type",
        "evidence_record_ids": ["evidence-sb225002"],
        "details": (
            "Dropped from gene curatable_objects because ZFIN context plus "
            "uppercase/digit notation indicates a compound or reagent without "
            "a gene identity hint."
        ),
    }
    assert "dropped_non_gene_zfin_candidate:SB225002" in candidate.payload_json[
        "run_summary"
    ]["warnings"]


def test_phenotype_adapter_materializes_nested_term_object_for_validation():
    payload = _sample_domain_envelope_payload()
    payload["curatable_objects"] = [
        {
            "object_type": "PhenotypeAnnotation",
            "pending_ref_id": "phenotype-annotation-1",
            "payload": {
                "annotation_kind": "phenotype_assertion",
                "phenotype_annotation_object": "boundary disruptions",
                "phenotype_terms": [
                    {
                        "resolution_state": "pending_ontology_resolution",
                        "curie": None,
                        "label": "boundary disruptions",
                        "source_mentions": ["boundary disruptions"],
                        "ontology_lookup_hint": {
                            "taxon_id": "NCBITaxon:7955",
                            "evidence_record_id": "evidence-phenotype",
                        },
                        "export_state": "blocked_pending_ontology_resolution",
                        "write_blocked_reason": "phenotype term CURIE unresolved",
                    }
                ],
                "evidence_record_ids": ["evidence-phenotype"],
            },
            "evidence_record_ids": ["evidence-phenotype"],
        }
    ]
    payload["metadata"]["evidence_records"] = [
        {
            "evidence_record_id": "evidence-phenotype",
            "entity": "phenotype",
            "verified_quote": "SB225002 caused boundary disruptions.",
            "page": 1,
            "section": "Results",
            "chunk_id": "chunk-phenotype",
        }
    ]

    candidate = build_extraction_envelope_candidate(
        json.dumps(payload),
        agent_key="phenotype_extractor",
        adapter_key="phenotype",
        conversation_summary="Extract phenotype evidence.",
    )

    assert candidate is not None
    objects = candidate.payload_json["curatable_objects"]
    assert [obj["object_type"] for obj in objects] == [
        "PhenotypeAnnotation",
        "PhenotypeTerm",
    ]
    annotation = objects[0]
    phenotype_term = objects[1]
    assert annotation["object_refs"] == [
        {
            "pending_ref_id": "phenotype-term-1-1",
            "object_type": "PhenotypeTerm",
        }
    ]
    assert phenotype_term["pending_ref_id"] == "phenotype-term-1-1"
    assert phenotype_term["object_role"] == "validated_reference"
    assert phenotype_term["model_ref"] == "PhenotypeTermPayload"
    assert phenotype_term["payload"]["label"] == "boundary disruptions"
    assert phenotype_term["payload"]["ontology_lookup_hint"] == {
        "taxon_id": "NCBITaxon:7955",
        "evidence_record_id": "evidence-phenotype",
    }
    assert phenotype_term["evidence_record_ids"] == ["evidence-phenotype"]
    assert phenotype_term["metadata"] == {
        "object_role": "validated_reference",
        "validation_state": "pending_ontology_resolution",
        "validator_binding_id": "phenotype_term_ontology_validator",
        "export_state": "blocked_pending_ontology_resolution",
        "write_blocked_reason": "phenotype term CURIE unresolved",
    }
    assert "materialized_nested_phenotype_terms:1" in candidate.payload_json[
        "run_summary"
    ]["warnings"]


def test_build_extraction_envelope_candidate_accepts_persisted_domain_envelope_shape():
    candidate = build_extraction_envelope_candidate(
        json.dumps(_sample_persisted_domain_envelope_payload()),
        agent_key="gene_extractor",
        adapter_key="gene",
        conversation_summary="Validated chat-time gene envelope",
    )

    assert candidate is not None
    assert candidate.agent_key == "gene_extractor"
    assert candidate.adapter_key == "gene"
    assert candidate.candidate_count == 1
    assert candidate.payload_json["envelope_id"] == "envelope-gene-notch"
    assert candidate.payload_json["extracted_objects"][0]["payload"]["primary_external_id"] == (
        "FB:FBgn0004647"
    )


def test_build_extraction_envelope_candidate_preserves_caller_adapter_when_envelope_omits_one(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {
            "adapter_key": None,
            "launchable": False,
        },
    )
    candidate = build_extraction_envelope_candidate(
        json.dumps(_sample_legacy_envelope_payload()),
        agent_key="gene-expression",
        adapter_key="caller_adapter",
    )

    assert candidate is not None
    assert candidate.adapter_key == "caller_adapter"


def test_build_extraction_envelope_candidate_returns_none_for_non_launchable_agents(
    monkeypatch,
):
    monkeypatch.setattr(
        module,
        "_get_agent_curation_metadata",
        lambda _agent_key: {
            "adapter_key": None,
            "launchable": False,
        },
    )
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

    assert candidate is None


def test_build_extraction_envelope_candidate_raises_when_agent_metadata_lookup_fails(monkeypatch):
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_metadata",
        lambda _agent_key: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )

    with pytest.raises(RuntimeError, match="catalog unavailable"):
        build_extraction_envelope_candidate(
            json.dumps(_sample_envelope_payload()),
            agent_key="gene-expression",
        )


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
        adapter_key="gene",
        agent_key="gene-expression",
        source_kind=CurationExtractionSourceKind.FLOW,
        payload_json=_sample_envelope_payload(),
    )

    with pytest.raises(RuntimeError, match="db write failed"):
        persist_extraction_result(request, db=session)

    assert session.commit_calls == 1
    assert session.rollback_calls == 1
    assert session.refresh_calls == 0


def test_persist_inline_validated_extraction_result_creates_idempotent_row():
    session = _FakeSession()
    builder_finalization = SimpleNamespace(
        summary=lambda: {
            "builder_run_id": "trace-1",
            "builder_invocation_id": "builder-invocation-1",
            "candidate_ids": ["candidate-1"],
            "source_candidate_ids": ["source-candidate-1"],
        }
    )

    response = persist_inline_validated_extraction_result(
        payload_json=_sample_persisted_domain_envelope_payload(),
        document_id=str(uuid4()),
        agent_key="gene",
        adapter_key="gene",
        tool_name="ask_gene_specialist",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="session-1",
        trace_id="trace-1",
        user_id="user-1",
        builder_finalization=builder_finalization,
        db=session,
    )

    assert len(session.added_records) == 1
    assert session.flush_calls == 1
    assert session.commit_calls == 0
    assert session.rollback_calls == 0
    assert session.added.idempotency_key == response.idempotency_key
    assert session.added.payload_hash == response.payload_hash
    assert session.added.extraction_metadata["persistence_phase"] == (
        "inline_validated_extraction"
    )
    assert session.added.extraction_metadata["builder_finalization"][
        "builder_invocation_id"
    ] == "builder-invocation-1"
    assert response.created_new is True
    assert response.result_ref == f"extraction-result:{response.extraction_result_id}"


def test_persist_inline_validated_extraction_result_reloads_existing_idempotent_row():
    existing_id = uuid4()
    document_id = uuid4()
    existing = SimpleNamespace(
        id=existing_id,
        document_id=document_id,
        adapter_key="gene",
        agent_key="gene",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="session-1",
        trace_id="trace-1",
        flow_run_id=None,
        user_id="user-1",
        candidate_count=1,
        conversation_summary=None,
        payload_json=_sample_persisted_domain_envelope_payload(),
        idempotency_key="inline-extraction:existing",
        payload_hash="existing-hash",
        extraction_metadata={"persistence_phase": "inline_validated_extraction"},
        created_at=datetime.now(timezone.utc),
    )
    session = _FakeSession(existing_rows=[existing])

    response = persist_inline_validated_extraction_result(
        payload_json=_sample_persisted_domain_envelope_payload(),
        document_id=str(document_id),
        agent_key="gene",
        adapter_key="gene",
        tool_name="ask_gene_specialist",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="session-1",
        trace_id="trace-1",
        user_id="user-1",
        builder_finalization={
            "builder_run_id": "trace-1",
            "builder_invocation_id": "builder-invocation-1",
        },
        db=session,
    )

    assert response.created_new is False
    assert response.extraction_result_id == str(existing_id)
    assert response.result_ref == f"extraction-result:{existing_id}"
    assert session.added_records == []
    assert session.flush_calls == 0


def test_persist_inline_validated_extraction_result_rejects_legacy_row_sources():
    with pytest.raises(ValueError, match="strict canonical domain envelope"):
        persist_inline_validated_extraction_result(
            payload_json=_sample_envelope_payload(),
            document_id=str(uuid4()),
            agent_key="gene",
            adapter_key="gene",
            tool_name="ask_gene_specialist",
            source_kind=CurationExtractionSourceKind.CHAT,
            origin_session_id="session-1",
            trace_id="trace-1",
            user_id="user-1",
            builder_finalization={
                "builder_run_id": "trace-1",
                "builder_invocation_id": "builder-invocation-1",
            },
            db=_FakeSession(),
        )


def test_persist_inline_validated_extraction_result_reloads_after_insert_conflict():
    """The unique-index race loser rolls back and returns the race winner's row."""

    document_id = uuid4()
    existing_id = uuid4()
    builder_finalization = {
        "builder_run_id": "trace-1",
        "builder_invocation_id": "builder-invocation-1",
    }
    race_winner_row = SimpleNamespace(
        id=existing_id,
        document_id=document_id,
        adapter_key="gene",
        agent_key="gene",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="session-1",
        trace_id="trace-1",
        flow_run_id=None,
        user_id="user-1",
        candidate_count=1,
        conversation_summary=None,
        payload_json=_sample_persisted_domain_envelope_payload(),
        idempotency_key="inline-extraction:race-winner",
        payload_hash="race-winner-hash",
        extraction_metadata={"persistence_phase": "inline_validated_extraction"},
        created_at=datetime.now(timezone.utc),
    )
    session = _RaceConflictSession(race_winner_row=race_winner_row)

    response = persist_inline_validated_extraction_result(
        payload_json=_sample_persisted_domain_envelope_payload(),
        document_id=str(document_id),
        agent_key="gene",
        adapter_key="gene",
        tool_name="ask_gene_specialist",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="session-1",
        trace_id="trace-1",
        user_id="user-1",
        builder_finalization=builder_finalization,
        db=session,
    )

    # The race loser attempted exactly one insert, hit the conflict, rolled back,
    # and did not insert a second time.
    assert len(session.added_records) == 1
    assert session.flush_calls == 1
    assert session.rollback_calls == 1
    assert session.refresh_calls == 0
    # Pre-check lookup (empty) + post-rollback reload (race winner) = 2 lookups.
    assert session.execute_calls == 2
    # The returned row is the existing race winner, flagged as not newly created.
    assert response.created_new is False
    assert response.extraction_result_id == str(existing_id)
    assert response.result_ref == f"extraction-result:{existing_id}"


def test_persist_inline_validated_extraction_result_retains_non_fatal_validator_finding():
    """A non-fatal ``validator_error`` finding survives into the persisted payload."""

    session = _FakeSession()
    payload = _sample_persisted_domain_envelope_payload()
    payload["validation_findings"] = [
        {
            "severity": "warning",
            "status": "open",
            "code": "domain_pack.validator_error",
            "message": "Allele validator could not be run for the target.",
            "object_ref": {
                "pending_ref_id": "gene-notch",
                "object_type": "gene_mention_evidence",
            },
            "details": {"fatal": False},
        }
    ]

    response = persist_inline_validated_extraction_result(
        payload_json=payload,
        document_id=str(uuid4()),
        agent_key="gene",
        adapter_key="gene",
        tool_name="ask_gene_specialist",
        source_kind=CurationExtractionSourceKind.CHAT,
        origin_session_id="session-1",
        trace_id="trace-1",
        user_id="user-1",
        builder_finalization={
            "builder_run_id": "trace-1",
            "builder_invocation_id": "builder-invocation-1",
        },
        db=session,
    )

    assert response.created_new is True
    persisted_findings = session.added.payload_json["validation_findings"]
    assert len(persisted_findings) == 1
    finding = persisted_findings[0]
    assert finding["code"] == "domain_pack.validator_error"
    assert finding["severity"] == "warning"
    assert finding["details"]["fatal"] is False
    # The persistence-layer response payload also carries the surviving finding.
    response_findings = response.extraction_result.payload_json["validation_findings"]
    assert response_findings == persisted_findings


def test_persist_extraction_results_flushes_all_records_on_shared_session():
    session = _FakeSession()
    requests = [
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            adapter_key="gene",
            agent_key="gene-expression",
            source_kind=CurationExtractionSourceKind.CHAT,
            payload_json=_sample_envelope_payload(),
        ),
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            adapter_key="pdf",
            agent_key="pdf-extraction",
            source_kind=CurationExtractionSourceKind.FLOW,
            payload_json=_sample_envelope_payload(),
        ),
    ]

    responses = persist_extraction_results(requests, db=session)

    assert len(session.added_records) == 2
    assert session.commit_calls == 0
    assert session.flush_calls == 1
    assert session.refresh_calls == 2
    assert session.rollback_calls == 0
    assert len(responses) == 2
    assert responses[0].extraction_result.agent_key == "gene-expression"
    assert responses[1].extraction_result.agent_key == "pdf-extraction"


def test_persist_extraction_results_commits_when_helper_owns_session(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(module, "SessionLocal", lambda: session)
    requests = [
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            adapter_key="gene",
            agent_key="gene-expression",
            source_kind=CurationExtractionSourceKind.CHAT,
            payload_json=_sample_envelope_payload(),
        ),
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            adapter_key="pdf",
            agent_key="pdf-extraction",
            source_kind=CurationExtractionSourceKind.FLOW,
            payload_json=_sample_envelope_payload(),
        ),
    ]

    responses = persist_extraction_results(requests)

    assert len(session.added_records) == 2
    assert session.commit_calls == 1
    assert session.flush_calls == 0
    assert session.refresh_calls == 2
    assert session.rollback_calls == 0
    assert session.closed is True
    assert len(responses) == 2


def test_persist_extraction_results_rolls_back_batch_on_shared_session_flush_error():
    session = _FakeSession(fail_flush=True)
    requests = [
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            adapter_key="gene",
            agent_key="gene-expression",
            source_kind=CurationExtractionSourceKind.CHAT,
            payload_json=_sample_envelope_payload(),
        ),
        CurationExtractionPersistenceRequest(
            document_id=str(uuid4()),
            adapter_key="pdf",
            agent_key="pdf-extraction",
            source_kind=CurationExtractionSourceKind.FLOW,
            payload_json=_sample_envelope_payload(),
        ),
    ]

    with pytest.raises(RuntimeError, match="db write failed"):
        persist_extraction_results(requests, db=session)

    assert len(session.added_records) == 2
    assert session.commit_calls == 0
    assert session.flush_calls == 1
    assert session.rollback_calls == 1
    assert session.refresh_calls == 0


def test_list_extraction_results_returns_empty_for_invalid_document_id(monkeypatch, caplog):
    session = _FakeSelectSession(rows=[])

    monkeypatch.setattr(module, "CurationExtractionResultRecordModel", _fake_select_model())
    monkeypatch.setattr(module, "select", lambda _model: _FakeSelectStatement())

    with caplog.at_level("WARNING"):
        results = module.list_extraction_results(
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

    monkeypatch.setattr(module, "CurationExtractionResultRecordModel", _fake_select_model())
    monkeypatch.setattr(module, "select", lambda _model: _FakeSelectStatement())

    results = module.list_extraction_results(
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
