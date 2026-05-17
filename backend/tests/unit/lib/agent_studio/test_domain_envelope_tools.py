"""Unit tests for Agent Studio domain-envelope inspection helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.lib.agent_studio.domain_envelope_tools as domain_tools
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationExtractionResultRecord,
    CurationReviewSession,
    DomainEnvelopeModel,
)
from src.models.sql.database import Base
from src.models.sql.pdf_document import PDFDocument
from src.schemas.curation_workspace import (
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationSessionStatus,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    DomainEnvelopeStatus,
)


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


TEST_TABLES = [
    PDFDocument.__table__,
    CurationReviewSession.__table__,
    CurationExtractionResultRecord.__table__,
    DomainEnvelopeModel.__table__,
    CurationCandidate.__table__,
]


def test_domain_pack_validation_plan_exposes_validator_agent_owner(
    monkeypatch,
    tmp_path,
):
    pack_path = tmp_path / "fixture.validation"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(
        """
pack_id: fixture.validation
display_name: Fixture Validation Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
model_definitions:
  - model_id: AssertionPayload
    display_name: Assertion payload
object_definitions:
  - object_type: Assertion
    display_name: Assertion
    model_ref: AssertionPayload
    fields:
      - field_path: assertion.curie
        display_name: Assertion CURIE
        field_type: string
metadata:
  validator_bindings:
    active:
      - binding_id: fixture.agent_validator
        validator_agent:
          package_id: org.validators
          agent_id: shared_validator
        applies_to:
          domain_pack_id: fixture.validation
    under_development:
      - binding_id: fixture.assertion_curie_lookup
        display_name: Assertion CURIE lookup
        state_explanation: Lookup dispatch is still being configured.
        applies_to:
          domain_pack_id: fixture.validation
          object_types: [Assertion]
          field_paths: [assertion.curie]
""".strip(),
        encoding="utf-8",
    )
    metadata = load_domain_pack_metadata(metadata_path)
    loaded_pack = LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
        package_id="org.owner",
    )
    registry = DomainPackValidationRegistry.from_domain_pack(loaded_pack)
    monkeypatch.setattr(
        domain_tools,
        "domain_pack_validation_registries",
        lambda: {"fixture.validation": registry},
    )

    result = domain_tools.get_domain_pack_validation_plan(
        domain_pack_id="fixture.validation",
    )

    assert result["success"] is True
    binding = result["validator_bindings"][0]
    attachment = result["validation_attachments"][0]
    assert binding["validator_agent"] == {
        "package_id": "org.validators",
        "agent_id": "shared_validator",
    }
    assert attachment["validator_package_id"] == "org.validators"
    assert attachment["validator_agent_id"] == "shared_validator"
    under_development_attachment = next(
        option
        for option in result["validation_attachments"]
        if option["state"] == "under_development"
    )
    under_development_binding = next(
        item
        for item in result["validator_bindings"]
        if item["binding_state"] == "under_development"
    )
    assert under_development_attachment["state_explanation"] == (
        "Lookup dispatch is still being configured."
    )
    assert under_development_attachment["affected_fields"] == ["assertion.curie"]
    assert under_development_binding["state_explanation"] == (
        "Lookup dispatch is still being configured."
    )
    assert "automatic_validation_semantics" in result
    assert "repair" not in json.dumps(result).lower()


@pytest.fixture
def db_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    restored_defaults = []
    restored_indexes = []
    for table in TEST_TABLES:
        restored_indexes.append((table, set(table.indexes)))
        table.indexes.clear()
        for column in table.columns:
            restored_defaults.append((column, column.server_default))
            column.server_default = None

    Base.metadata.create_all(bind=engine, tables=TEST_TABLES)
    session_local = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    try:
        yield session_local
    finally:
        Base.metadata.drop_all(bind=engine, tables=TEST_TABLES)
        for table, indexes in restored_indexes:
            table.indexes.update(indexes)
        for column, server_default in restored_defaults:
            column.server_default = server_default


def _now() -> datetime:
    return datetime(2026, 5, 11, tzinfo=timezone.utc)


def _persist_document(db, *, suffix: str = "1"):
    now = _now()
    document = PDFDocument(
        id=uuid4(),
        filename=f"paper-{suffix}.pdf",
        title=f"Paper {suffix}",
        file_path=f"/tmp/paper-{suffix}.pdf",
        file_hash=suffix.rjust(64, "a")[-64:],
        file_size=1024,
        page_count=1,
        upload_timestamp=now,
        last_accessed=now,
        status="processed",
    )
    db.add(document)
    db.flush()
    return document


def _persist_review_session(db, *, document_id, curator_id: str):
    now = _now()
    session = CurationReviewSession(
        id=uuid4(),
        status=CurationSessionStatus.NEW,
        adapter_key="fixture",
        document_id=document_id,
        assigned_curator_id=curator_id,
        created_by_id=curator_id,
        session_version=1,
        total_candidates=1,
        reviewed_candidates=0,
        pending_candidates=1,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        tags=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    return session


def _persist_domain_envelope(db, *, envelope_id: str, document_id, session_id=None):
    now = _now()
    envelope = DomainEnvelope(
        envelope_id=envelope_id,
        domain_pack_id="fixture.pack",
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id=f"{envelope_id}-object",
                payload={"symbol": envelope_id},
            )
        ],
    )
    db.add(
        DomainEnvelopeModel(
            envelope_id=envelope_id,
            revision=1,
            project_key="fixture",
            domain_pack_key="fixture.pack",
            domain_pack_version=None,
            status=DomainEnvelopeStatus.EXTRACTED,
            document_id=document_id,
            session_id=session_id,
            schema_ref_json={},
            object_model_ref_json={},
            model_field_ref_json={},
            envelope_json=envelope.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
            checkpointed_at=now,
        )
    )
    db.flush()
    return envelope


def _persist_candidate_for_envelope(db, *, session_id, envelope: DomainEnvelope):
    now = _now()
    db.add(
        CurationCandidate(
            id=uuid4(),
            session_id=session_id,
            source=CurationCandidateSource.EXTRACTED,
            status=CurationCandidateStatus.PENDING,
            order=0,
            adapter_key="fixture",
            display_label=envelope.envelope_id,
            envelope_id=envelope.envelope_id,
            object_id=envelope.objects[0].object_id,
            envelope_revision=1,
            normalized_payload={},
            candidate_metadata={"semantic_source": "domain_envelope.objects"},
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()


def test_current_flow_domain_envelope_analysis_summarizes_validation_schedule(monkeypatch):
    monkeypatch.setattr(
        domain_tools,
        "get_domain_pack_validation_plan",
        lambda **_kwargs: {
            "success": True,
            "domain_pack_version": "0.7.0",
            "object_definitions": [
                {
                    "object_type": "allele",
                    "display_name": "Allele",
                    "field_paths": ["gene.symbol", "allele.symbol"],
                }
            ],
            "validation_attachment_summary": {
                "total": 3,
                "default_enabled": 1,
                "required": 1,
                "export_blocking": 1,
            },
        },
    )

    result = domain_tools.current_flow_domain_envelope_analysis(
        flow_context={
            "nodes": [
                {
                    "id": "extract_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "allele_extractor",
                        "agent_display_name": "Allele Extraction",
                        "validation_attachments": [
                            {
                                "attachment_id": "active-binding",
                                "domain_pack_id": "alliance_allele",
                                "validator_id": "allele_lookup",
                                "validator_binding_id": "active-binding",
                                "state": "active",
                                "enabled": True,
                                "required": True,
                                "blocking": True,
                                "export_blocking": True,
                            },
                            {
                                "attachment_id": "opted-out-binding",
                                "domain_pack_id": "alliance_allele",
                                "validator_id": "manual_check",
                                "validator_binding_id": "opted-out-binding",
                                "state": "active",
                                "enabled": False,
                                "required": False,
                                "blocking": True,
                                "export_blocking": True,
                            },
                            {
                                "attachment_id": "under-development-binding",
                                "domain_pack_id": "alliance_allele",
                                "validator_id": "future_validator",
                                "validator_binding_id": "under-development-binding",
                                "state": "under_development",
                                "enabled": False,
                            },
                        ],
                    },
                }
            ]
        },
        agent_registry={
            "allele_extractor": {
                "name": "Allele Extraction",
                "curation": {"domain_pack_id": "alliance_allele"},
            }
        },
    )

    node = result["nodes"][0]
    assert result["semantic_source"] == "domain_envelope.objects"
    assert result["envelope_node_count"] == 1
    assert node["domain_pack_id"] == "alliance_allele"
    assert node["domain_pack_version"] == "0.7.0"
    assert node["object_definitions"][0]["object_type"] == "allele"
    assert node["validation_schedule"]["scheduled_validators"][0][
        "validator_binding_id"
    ] == "active-binding"
    assert node["validation_schedule"]["opt_outs"][0]["validator_binding_id"] == (
        "opted-out-binding"
    )
    assert node["validation_schedule"]["inactive_metadata"][0][
        "validator_binding_id"
    ] == "under-development-binding"


def test_resolved_object_id_accepts_pending_ref_id():
    object_id_by_ref = {
        ("object_id", "obj-1"): "obj-1",
        ("pending_ref_id", "pending-1"): "obj-1",
    }

    assert domain_tools._resolved_object_id("pending-1", object_id_by_ref) == "obj-1"
    assert domain_tools._resolved_object_id("obj-1", object_id_by_ref) == "obj-1"
    assert domain_tools._resolved_object_id("missing-ref", object_id_by_ref) == "missing-ref"


def test_sessionless_domain_envelope_visibility_requires_visible_candidate_session(
    db_session_factory,
):
    seed_db = db_session_factory()
    try:
        document = _persist_document(seed_db)
        visible_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-1",
        )
        hidden_session = _persist_review_session(
            seed_db,
            document_id=document.id,
            curator_id="curator-2",
        )
        visible_envelope = _persist_domain_envelope(
            seed_db,
            envelope_id="env-visible-sessionless",
            document_id=document.id,
        )
        hidden_envelope = _persist_domain_envelope(
            seed_db,
            envelope_id="env-hidden-sessionless",
            document_id=document.id,
        )
        _persist_domain_envelope(
            seed_db,
            envelope_id="env-orphan-sessionless",
            document_id=document.id,
        )
        _persist_candidate_for_envelope(
            seed_db,
            session_id=visible_session.id,
            envelope=visible_envelope,
        )
        _persist_candidate_for_envelope(
            seed_db,
            session_id=hidden_session.id,
            envelope=hidden_envelope,
        )
        seed_db.commit()
        visible_session_id = str(visible_session.id)
    finally:
        seed_db.close()

    document_result = domain_tools.list_domain_envelopes(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        document_id=str(document.id),
        limit=10,
    )
    session_result = domain_tools.list_domain_envelopes(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        session_id=visible_session_id,
        limit=10,
    )
    hidden_state = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-hidden-sessionless",
    )
    orphan_state = domain_tools.get_domain_envelope_state(
        session_factory=db_session_factory,
        user_auth_sub="curator-1",
        envelope_id="env-orphan-sessionless",
    )

    assert document_result["success"] is True
    assert {row["envelope_id"] for row in document_result["envelopes"]} == {
        "env-visible-sessionless"
    }
    assert session_result["success"] is True
    assert [row["envelope_id"] for row in session_result["envelopes"]] == [
        "env-visible-sessionless"
    ]
    assert hidden_state == {
        "success": False,
        "error": "Domain envelope env-hidden-sessionless was not found.",
    }
    assert orphan_state == {
        "success": False,
        "error": "Domain envelope env-orphan-sessionless was not found.",
    }


def test_lookup_attempt_summary_preserves_transient_attempts_separate_from_final_status():
    envelope = DomainEnvelope(
        envelope_id="env-lookup",
        domain_pack_id="alliance_gene",
        status=DomainEnvelopeStatus.VALIDATED,
        objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id="obj-1",
                payload={
                    "primary_external_id": "GENE:00000001",
                    "lookup_status": "success",
                    "lookup_attempts": [
                        {
                            "lookup_status": "transient_error",
                            "attempted_query": {"symbol": "unc-54"},
                            "error": {"type": "TimeoutError"},
                        },
                        {
                            "lookup_status": "success",
                            "attempted_query": {"symbol": "unc-54"},
                            "resolved_id": "GENE:00000001",
                            "resolved_label": "unc-54",
                        },
                    ],
                },
            )
        ],
    )
    projection_row = SimpleNamespace(
        object_id="obj-1",
        projection_type="review_row",
        projection_key="gene:unc-54",
        projection_json={
            "lookup_status": "success",
            "lookup_attempts": [
                {
                    "lookup_status": "success",
                    "target_projection": "gene:unc-54",
                    "resolved_id": "GENE:00000001",
                }
            ],
        },
    )

    summary = domain_tools._lookup_attempt_summary(
        envelope=envelope,
        projection_rows=[projection_row],
    )

    assert summary["attempt_count"] == 3
    assert summary["by_status"] == {"success": 2, "transient_error": 1}
    assert summary["attempts"][0]["lookup_status"] == "transient_error"
    assert "audit trail" in summary["interpretation"]
    assert "final outcome" in summary["interpretation"]


def test_lookup_attempt_summary_rejects_attempts_without_status():
    envelope = DomainEnvelope(
        envelope_id="env-lookup-missing-status",
        domain_pack_id="alliance_gene",
        status=DomainEnvelopeStatus.VALIDATED,
        objects=[
            CuratableObjectEnvelope(
                object_type="gene",
                object_id="obj-1",
                payload={
                    "lookup_attempts": [
                        {"attempted_query": {"symbol": "unc-54"}},
                    ],
                },
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match=(
            "Lookup attempt at "
            r"envelope.objects\[0\].payload.lookup_attempts\[0\] "
            "is missing lookup_status/status"
        ),
    ):
        domain_tools._lookup_attempt_summary(
            envelope=envelope,
            projection_rows=[],
        )


def test_group_by_string_key_rejects_missing_grouping_key():
    with pytest.raises(
        ValueError,
        match="Item active-binding is missing required grouping key: state",
    ):
        domain_tools._group_by_string_key(
            [{"attachment_id": "active-binding", "validator_id": "validator-1"}],
            "state",
        )


def test_export_submission_readiness_returns_read_only_blockers(monkeypatch):
    class FakeDb:
        def close(self):
            pass

    class FakeReadiness:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {
                "candidate_id": "candidate-1",
                "ready": False,
                "blockers": [
                    {
                        "code": "domain_validation_blocker",
                        "envelope_id": "env-1",
                        "object_id": "obj-1",
                        "field_path": "gene.symbol",
                        "message": "Resolve required validation finding.",
                    }
                ],
            }

    monkeypatch.setattr(
        domain_tools,
        "_session_visible_to_user",
        lambda _db, **_kwargs: True,
    )
    monkeypatch.setattr(
        domain_tools,
        "_load_session_for_validation",
        lambda _db, *, session_id: SimpleNamespace(
            candidates=[SimpleNamespace(id="candidate-1")]
        ),
    )
    monkeypatch.setattr(
        domain_tools,
        "_build_domain_envelope_submission_context",
        lambda **_kwargs: SimpleNamespace(envelope_snapshots={"env-1": object()}),
    )
    monkeypatch.setattr(
        domain_tools,
        "_latest_candidate_validation_snapshot",
        lambda _candidate: {"status": "failed"},
    )
    monkeypatch.setattr(
        domain_tools,
        "_candidate_submission_readiness",
        lambda *_args, **_kwargs: FakeReadiness(),
    )

    result = domain_tools.get_export_submission_readiness(
        session_factory=FakeDb,
        user_auth_sub="curator-1",
        session_id="session-1",
        candidate_ids=["candidate-1"],
        expected_envelope_revisions={"env-1": 3},
        mode="submission",
    )

    assert result["success"] is True
    assert result["mode"] == "submission"
    assert result["ready_count"] == 0
    assert result["blocker_count"] == 1
    assert result["domain_envelope_ids"] == ["env-1"]
    assert result["readiness"][0]["blockers"][0]["envelope_id"] == "env-1"
    assert "read-only readiness explanation" in result["instruction"]
