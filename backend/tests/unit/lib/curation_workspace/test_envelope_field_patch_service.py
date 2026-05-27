"""Unit tests for workspace curator envelope field patches."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.lib.curation_workspace import session_mutation_service as module
from src.lib.curation_workspace.models import (
    CurationActionLogEntry,
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
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    write_domain_envelope_checkpoint,
)
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.models.sql.database import Base
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from src.schemas.curation_workspace import (
    CurationCandidateDraftUpdateRequest,
    CurationCandidateSource,
    CurationCandidateStatus,
    CurationDraftFieldChange,
    CurationEnvelopeFieldPatchRequest,
    CurationSessionStatus,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    DomainEnvelopeStatus,
    HistoryEventKind,
)


@compiles(PostgresUUID, "sqlite")
def _compile_pg_uuid_for_sqlite(_type, _compiler, **_kwargs):
    return "CHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


TEST_TABLES = [
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
    CurationSubmissionRecord.__table__,
    CurationValidationSnapshot.__table__,
    CurationActionLogEntry.__table__,
]


@pytest.fixture
def db_session():
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
            if table.name.startswith("domain_") and column.name in {
                "created_at",
                "updated_at",
            }:
                continue
            column.server_default = None

    Base.metadata.create_all(bind=engine, tables=TEST_TABLES)
    session_local = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    session = session_local()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=TEST_TABLES)
        for table, indexes in restored_indexes:
            table.indexes.update(indexes)
        for column, server_default in restored_defaults:
            column.server_default = server_default


def _now() -> datetime:
    return datetime(2026, 5, 10, 12, 30, tzinfo=timezone.utc)


def _pack_text() -> str:
    return """
pack_id: fixture.curator_patch
display_name: Fixture Curator Patch Pack
version: 0.1.0
metadata_api_version: 1.0.0
status: active
object_definitions:
  - object_type: GeneAssertion
    display_name: Gene assertion
    fields:
      - field_path: gene.symbol
        field_type: string
        metadata:
          editable: true
      - field_path: protected_note
        field_type: string
        metadata:
          protected: true
""".strip()


def _loaded_pack(tmp_path: Path) -> LoadedDomainPack:
    pack_path = tmp_path / "fixture.curator_patch"
    pack_path.mkdir()
    metadata_path = pack_path / "domain_pack.yaml"
    metadata_path.write_text(_pack_text(), encoding="utf-8")
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


@pytest.fixture
def loaded_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LoadedDomainPack:
    pack = _loaded_pack(tmp_path)
    monkeypatch.setattr(
        module,
        "resolve_curation_domain_pack_by_id",
        lambda pack_id: pack if pack_id == pack.pack_id else None,
    )
    return pack


def _create_document(db_session) -> PDFDocument:
    now = _now()
    document = PDFDocument(
        id=uuid4(),
        filename="paper.pdf",
        title="Paper Title",
        file_path="/tmp/paper.pdf",
        file_hash="b" * 64,
        file_size=1024,
        page_count=2,
        upload_timestamp=now,
        last_accessed=now,
        status="processed",
    )
    db_session.add(document)
    db_session.commit()
    return document


def _create_session_with_envelope_projection(db_session) -> tuple[CurationReviewSession, CurationCandidate]:
    now = _now()
    document = _create_document(db_session)
    session = CurationReviewSession(
        id=uuid4(),
        status=CurationSessionStatus.NEW,
        adapter_key="fixture_adapter",
        document_id=document.id,
        session_version=1,
        total_candidates=1,
        pending_candidates=1,
        reviewed_candidates=0,
        accepted_candidates=0,
        rejected_candidates=0,
        manual_candidates=0,
        warnings=[],
        tags=[],
        prepared_at=now,
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    db_session.flush()

    envelope = DomainEnvelope(
        envelope_id="env-1",
        domain_pack_id="fixture.curator_patch",
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[
            CuratableObjectEnvelope(
                object_type="GeneAssertion",
                object_id="gene-1",
                payload={
                    "gene": {"symbol": "abc-1"},
                    "protected_note": "do not edit",
                },
            )
        ],
    )
    checkpoint = write_domain_envelope_checkpoint(
        db_session,
        DomainEnvelopeCheckpointRequest(
            project_key="agr",
            envelope=envelope,
            expected_revision=0,
            document_id=document.id,
            session_id=session.id,
        ),
    )
    assert checkpoint.revision == 1

    candidate = CurationCandidate(
        id=uuid4(),
        session_id=session.id,
        source=CurationCandidateSource.EXTRACTED,
        status=CurationCandidateStatus.PENDING,
        order=0,
        adapter_key="fixture_adapter",
        display_label="abc-1",
        envelope_id="env-1",
        object_id="gene-1",
        envelope_revision=1,
        normalized_payload={"gene": {"symbol": "abc-1"}},
        candidate_metadata={},
        created_at=now,
        updated_at=now,
    )
    db_session.add(candidate)
    db_session.flush()
    db_session.add(
        CurationDraft(
            id=uuid4(),
            candidate_id=candidate.id,
            adapter_key="fixture_adapter",
            version=1,
            title="abc-1",
            fields=[
                {
                    "field_key": "gene.symbol",
                    "label": "Gene symbol",
                    "value": "abc-1",
                    "seed_value": "abc-1",
                    "field_type": "string",
                    "group_key": "gene",
                    "group_label": "Gene",
                    "order": 0,
                    "required": True,
                    "read_only": False,
                    "dirty": False,
                    "stale_validation": False,
                    "evidence_anchor_ids": [],
                    "metadata": {"source_field_path": "gene.symbol"},
                }
            ],
            draft_metadata={},
            created_at=now,
            updated_at=now,
            last_saved_at=now,
        )
    )
    db_session.commit()
    return session, candidate


def _request(
    session_id: UUID,
    *,
    expected_revision: int = 1,
    before: object = "abc-1",
    value: object = "abc-2",
    field_path: str = "gene.symbol",
) -> CurationEnvelopeFieldPatchRequest:
    return CurationEnvelopeFieldPatchRequest(
        session_id=str(session_id),
        envelope_id="env-1",
        expected_revision=expected_revision,
        object_id="gene-1",
        field_path=field_path,
        before=before,
        value=value,
        reason="Curator correction.",
        patch_id=f"curator-field-patch:{uuid4().hex}",
    )


def test_patch_envelope_field_refreshes_projection_without_legacy_payload(
    db_session,
    loaded_pack,
):
    session, candidate = _create_session_with_envelope_projection(db_session)
    assert candidate.normalized_payload == {"gene": {"symbol": "abc-1"}}

    response = module.patch_envelope_field(
        db_session,
        session.id,
        _request(session.id),
        {"sub": "curator-1", "email": "curator@example.org"},
    )

    assert response.accepted is True
    assert response.previous_revision == 1
    assert response.envelope_revision == 2
    assert response.projection_ref.envelope_revision == 2
    assert response.candidate is not None
    assert response.candidate.projection_ref is not None
    assert response.candidate.projection_ref.envelope_revision == 2
    assert response.candidate.normalized_payload == {}
    assert response.candidate.draft.fields[0].value == "abc-2"
    assert response.candidate.draft.fields[0].seed_value == "abc-2"
    assert response.candidate.draft.fields[0].dirty is False
    assert response.candidate.draft.fields[0].stale_validation is True
    assert response.action_log_entry is not None
    assert response.action_log_entry.metadata["accepted"] is True

    envelope_row = db_session.get(DomainEnvelopeModel, "env-1")
    assert envelope_row.revision == 2
    assert envelope_row.envelope_json["objects"][0]["payload"]["gene"]["symbol"] == "abc-2"

    updated_candidate = db_session.get(CurationCandidate, candidate.id)
    assert updated_candidate.envelope_revision == 2
    assert updated_candidate.normalized_payload == {}

    history_events = db_session.scalars(
        select(DomainEnvelopeHistory).order_by(DomainEnvelopeHistory.event_index)
    ).all()
    assert [event.event_type for event in history_events] == [
        HistoryEventKind.FIELD_UPDATED,
        HistoryEventKind.CURATOR_FIELD_PATCH_ACCEPTED,
    ]


def test_update_candidate_draft_materializes_envelope_backed_payload(
    db_session,
    loaded_pack,
):
    session, candidate = _create_session_with_envelope_projection(db_session)
    draft_id = str(candidate.draft.id)

    response = module.update_candidate_draft(
        db_session,
        session.id,
        candidate.id,
        CurationCandidateDraftUpdateRequest(
            session_id=str(session.id),
            candidate_id=str(candidate.id),
            draft_id=draft_id,
            expected_version=1,
            field_changes=[
                CurationDraftFieldChange(
                    field_key="gene.symbol",
                    value="abc-3",
                )
            ],
            autosave=True,
        ),
        {"sub": "curator-1", "email": "curator@example.org"},
    )

    assert response.candidate.normalized_payload == {}
    assert response.candidate.projection_ref is not None
    assert response.candidate.projection_ref.envelope_revision == 2
    assert response.draft.fields[0].value == "abc-3"
    assert response.draft.fields[0].seed_value == "abc-3"
    assert response.draft.fields[0].dirty is False
    assert response.draft.fields[0].stale_validation is False

    envelope_row = db_session.get(DomainEnvelopeModel, "env-1")
    assert envelope_row.revision == 2
    assert envelope_row.envelope_json["objects"][0]["payload"]["gene"]["symbol"] == "abc-3"
    assert envelope_row.envelope_json["history"][-1]["details"]["reason"] == (
        "draft_materialization"
    )


def test_patch_envelope_field_rejects_stale_revision_without_checkpoint(
    db_session,
    loaded_pack,
):
    session, _candidate = _create_session_with_envelope_projection(db_session)

    with pytest.raises(HTTPException) as exc:
        module.patch_envelope_field(
            db_session,
            session.id,
            _request(session.id, expected_revision=2),
            {"sub": "curator-1"},
        )

    assert exc.value.status_code == 409
    assert db_session.get(DomainEnvelopeModel, "env-1").revision == 1
    assert db_session.scalars(select(DomainEnvelopeHistory)).all() == []


def test_patch_envelope_field_rejects_before_mismatch_and_records_history(
    db_session,
    loaded_pack,
):
    session, _candidate = _create_session_with_envelope_projection(db_session)

    with pytest.raises(HTTPException) as exc:
        module.patch_envelope_field(
            db_session,
            session.id,
            _request(session.id, before="stale"),
            {"sub": "curator-1"},
        )

    assert exc.value.status_code == 409
    envelope_row = db_session.get(DomainEnvelopeModel, "env-1")
    assert envelope_row.revision == 2
    assert envelope_row.envelope_json["objects"][0]["payload"]["gene"]["symbol"] == "abc-1"
    history_events = db_session.scalars(select(DomainEnvelopeHistory)).all()
    assert [event.event_type for event in history_events] == [
        HistoryEventKind.CURATOR_FIELD_PATCH_REJECTED
    ]
    action_log = db_session.scalars(select(CurationActionLogEntry)).one()
    assert action_log.action_metadata["accepted"] is False
    assert "before does not match" in action_log.action_metadata["errors"][0]
