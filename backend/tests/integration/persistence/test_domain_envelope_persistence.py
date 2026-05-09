"""Integration tests for domain envelope checkpoint persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import delete, select

from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationDraft,
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)
from src.lib.domain_envelopes.persistence import (
    DEFAULT_OBJECT_PROJECTION_TYPE,
    DomainEnvelopeCheckpointRequest,
    DomainEnvelopePersistenceError,
    StaleDomainEnvelopeRevisionError,
    load_domain_envelope,
    write_domain_envelope_checkpoint,
    _stable_object_id,
)
from src.models.sql.database import SessionLocal
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    DomainEnvelopeStatus,
    FieldRef,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    SchemaRef,
    ValidationFinding,
    ValidationFindingSeverity,
)


BACKEND_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")


@pytest.fixture
def db_session():
    session = SessionLocal()
    _clean_domain_tables(session)
    try:
        yield session
    finally:
        session.rollback()
        _clean_domain_tables(session)
        session.close()


def _clean_domain_tables(session):
    for model in (
        DomainEnvelopeProjectionIndex,
        DomainEnvelopeHistory,
        DomainValidationFinding,
        DomainEnvelopeObject,
        DomainEnvelopeModel,
    ):
        session.execute(delete(model))
    session.commit()


def _checkpoint_request(
    envelope: DomainEnvelope,
    *,
    expected_revision: int,
) -> DomainEnvelopeCheckpointRequest:
    return DomainEnvelopeCheckpointRequest(
        project_key="agr",
        envelope=envelope,
        expected_revision=expected_revision,
        flow_run_id="flow-run-1290",
        object_model_ref_json={"registry": "domain-pack"},
        model_field_ref_json={"fields": "provider-neutral"},
    )


def _legacy_semantic_row_counts(session) -> dict[str, int]:
    return {
        "curation_candidates": len(session.scalars(select(CurationCandidate)).all()),
        "annotation_drafts": len(session.scalars(select(CurationDraft)).all()),
    }


def _envelope(*, include_second_object: bool = True, symbol: str = "ABC-1") -> DomainEnvelope:
    schema_ref = SchemaRef(
        schema_id="gene.assertion.schema",
        provider="json-schema",
        version="1.0.0",
    )
    objects = [
        CuratableObjectEnvelope(
            object_type="GeneAssertion",
            object_id="gene-1",
            schema_ref=schema_ref,
            status=CuratableObjectStatus.PENDING,
            payload={"gene": {"symbol": symbol}},
            metadata={
                "provider_refs": {"schema_ref": "provider.generic"},
                "model_field_ref": {"gene.symbol": {"field": "symbol"}},
                "projections": [
                    {
                        "projection_type": "workspace_row",
                        "projection_key": "gene-1",
                        "projection_status": "pending",
                        "projection_json": {"label": symbol, "object_id": "gene-1"},
                    }
                ],
            },
        )
    ]
    if include_second_object:
        objects.append(
            CuratableObjectEnvelope(
                object_type="Reference",
                object_id="reference-1",
                status=CuratableObjectStatus.EXTRACTED,
                payload={"reference": {"curie": "PMID:1"}},
            )
        )

    history = [
        HistoryEvent(
            event_id="evt-created",
            event_type=HistoryEventKind.CREATED,
            timestamp=datetime(2026, 5, 9, 12, 0, tzinfo=timezone.utc),
            actor_type=HistoryActorType.SYSTEM,
            actor_id="checkpoint-test",
            message="Created test envelope",
        )
    ]
    if not include_second_object:
        history.append(
            HistoryEvent(
                event_id="evt-updated",
                event_type=HistoryEventKind.OBJECT_UPDATED,
                timestamp=datetime(2026, 5, 9, 12, 5, tzinfo=timezone.utc),
                actor_type=HistoryActorType.AGENT,
                actor_id="validator",
                object_ref=ObjectRef(object_id="gene-1"),
                message="Updated test envelope",
            )
        )

    return DomainEnvelope(
        envelope_id="env-persistence-test",
        domain_pack_id="fixture.core",
        domain_pack_version="0.7.0",
        status=DomainEnvelopeStatus.EXTRACTED,
        schema_ref=schema_ref,
        objects=objects,
        validation_findings=[
            ValidationFinding(
                finding_id="finding-symbol-case",
                severity=ValidationFindingSeverity.WARNING,
                message="Symbol casing should be reviewed",
                field_ref=FieldRef(
                    object_ref=ObjectRef(object_id="gene-1"),
                    field_path="gene.symbol",
                ),
                code="fixture.symbol_case",
                details={"model_field_ref": {"field": "gene.symbol"}},
            )
        ],
        history=history,
        metadata={
            "projection_index": [
                {
                    "object_id": "gene-1",
                    "object_type": "GeneAssertion",
                    "projection_type": "export_preview",
                    "projection_key": "gene-1-json",
                    "projection_status": "blocked",
                    "projection_json": {"symbol": symbol},
                }
            ]
        },
    )


@pytest.mark.integration
def test_checkpoint_insert_update_stale_rejection_and_index_regeneration(db_session):
    legacy_counts_before = _legacy_semantic_row_counts(db_session)

    result = write_domain_envelope_checkpoint(
        db_session,
        _checkpoint_request(_envelope(), expected_revision=0),
    )

    assert result.revision == 1
    assert result.object_count == 2
    assert result.finding_count == 1
    assert result.projection_count == 4
    assert result.inserted_history_event_count == 1

    envelope_row = db_session.get(DomainEnvelopeModel, "env-persistence-test")
    assert envelope_row is not None
    assert envelope_row.revision == 1
    assert envelope_row.domain_pack_key == "fixture.core"
    assert envelope_row.schema_provider == "json-schema"
    assert _legacy_semantic_row_counts(db_session) == legacy_counts_before

    object_rows = db_session.scalars(
        select(DomainEnvelopeObject).order_by(DomainEnvelopeObject.object_id)
    ).all()
    assert [row.object_id for row in object_rows] == ["gene-1", "reference-1"]
    assert object_rows[0].validation_state == "warning"

    projection_keys = {
        (row.object_id, row.projection_type, row.projection_key)
        for row in db_session.scalars(select(DomainEnvelopeProjectionIndex)).all()
    }
    assert (
        "gene-1",
        DEFAULT_OBJECT_PROJECTION_TYPE,
        "gene-1",
    ) in projection_keys
    assert ("gene-1", "workspace_row", "gene-1") in projection_keys
    assert ("gene-1", "export_preview", "gene-1-json") in projection_keys
    assert (
        "reference-1",
        DEFAULT_OBJECT_PROJECTION_TYPE,
        "reference-1",
    ) in projection_keys

    with pytest.raises(StaleDomainEnvelopeRevisionError):
        write_domain_envelope_checkpoint(
            db_session,
            _checkpoint_request(
                _envelope(include_second_object=False, symbol="XYZ-2"),
                expected_revision=0,
            ),
        )

    unchanged = db_session.get(DomainEnvelopeModel, "env-persistence-test")
    assert unchanged.revision == 1
    assert unchanged.envelope_json["objects"][0]["payload"]["gene"]["symbol"] == "ABC-1"

    update_result = write_domain_envelope_checkpoint(
        db_session,
        _checkpoint_request(
            _envelope(include_second_object=False, symbol="XYZ-2"),
            expected_revision=1,
        ),
    )

    assert update_result.revision == 2
    assert update_result.object_count == 1
    assert update_result.projection_count == 3
    assert update_result.inserted_history_event_count == 1

    refreshed = db_session.get(DomainEnvelopeModel, "env-persistence-test")
    assert refreshed.revision == 2
    assert refreshed.envelope_json["objects"][0]["payload"]["gene"]["symbol"] == "XYZ-2"
    assert _legacy_semantic_row_counts(db_session) == legacy_counts_before
    assert load_domain_envelope(
        db_session,
        "env-persistence-test",
        revision=2,
    ).objects[0].payload["gene"]["symbol"] == "XYZ-2"

    assert db_session.scalar(
        select(DomainEnvelopeObject).where(DomainEnvelopeObject.object_id == "reference-1")
    ) is None
    assert db_session.scalar(
        select(DomainEnvelopeProjectionIndex).where(
            DomainEnvelopeProjectionIndex.object_id == "reference-1"
        )
    ) is None

    history_rows = db_session.scalars(
        select(DomainEnvelopeHistory).order_by(DomainEnvelopeHistory.event_id)
    ).all()
    assert [(row.event_id, row.envelope_revision) for row in history_rows] == [
        ("evt-created", 1),
        ("evt-updated", 2),
    ]

    with pytest.raises(DomainEnvelopePersistenceError):
        load_domain_envelope(db_session, "env-persistence-test", revision=1)


@pytest.mark.integration
def test_projection_uniqueness_rolls_back_checkpoint(db_session):
    envelope = _envelope(include_second_object=False)
    first_object = envelope.objects[0]
    duplicate_projection = {
        "projection_type": "workspace_row",
        "projection_key": "gene-1",
        "projection_json": {"duplicate": True},
    }
    envelope = envelope.model_copy(
        update={
            "objects": [
                first_object.model_copy(
                    update={
                        "metadata": {
                            **first_object.metadata,
                            "projections": [
                                *first_object.metadata["projections"],
                                duplicate_projection,
                            ],
                        }
                    }
                )
            ]
        }
    )

    with pytest.raises(DomainEnvelopePersistenceError):
        write_domain_envelope_checkpoint(
            db_session,
            _checkpoint_request(envelope, expected_revision=0),
        )

    assert db_session.get(DomainEnvelopeModel, "env-persistence-test") is None


@pytest.mark.integration
def test_malformed_projection_metadata_rolls_back_checkpoint(db_session):
    envelope = _envelope(include_second_object=False)
    first_object = envelope.objects[0]
    envelope = envelope.model_copy(
        update={
            "objects": [
                first_object.model_copy(
                    update={
                        "metadata": {
                            **first_object.metadata,
                            "projections": None,
                        }
                    }
                )
            ]
        }
    )

    with pytest.raises(
        DomainEnvelopePersistenceError,
        match="projections must be a list of objects, got null",
    ):
        write_domain_envelope_checkpoint(
            db_session,
            _checkpoint_request(envelope, expected_revision=0),
        )

    assert db_session.get(DomainEnvelopeModel, "env-persistence-test") is None


@pytest.mark.integration
def test_projection_entries_require_projection_json(db_session):
    envelope = _envelope(include_second_object=False)
    first_object = envelope.objects[0]
    envelope = envelope.model_copy(
        update={
            "objects": [
                first_object.model_copy(
                    update={
                        "metadata": {
                            **first_object.metadata,
                            "projections": [
                                {
                                    "projection_type": "workspace_row",
                                    "projection_key": "gene-1",
                                    "projection_data": {"legacy": True},
                                }
                            ],
                        }
                    }
                )
            ]
        }
    )

    with pytest.raises(
        DomainEnvelopePersistenceError,
        match="projection entries must provide projection_json",
    ):
        write_domain_envelope_checkpoint(
            db_session,
            _checkpoint_request(envelope, expected_revision=0),
        )

    assert db_session.get(DomainEnvelopeModel, "env-persistence-test") is None


def test_missing_stable_object_id_fails_loudly():
    envelope = _envelope(include_second_object=False)
    first_object = envelope.objects[0]
    object_without_identity = first_object.model_copy(
        update={
            "object_id": None,
            "pending_ref_id": None,
        }
    )

    with pytest.raises(
        DomainEnvelopePersistenceError,
        match="CuratableObjectEnvelope has neither object_id nor pending_ref_id",
    ):
        _stable_object_id(object_without_identity)
