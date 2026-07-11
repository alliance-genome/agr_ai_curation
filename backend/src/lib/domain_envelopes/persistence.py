"""Checkpoint and index persistence for provider-neutral domain envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    HistoryEvent,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)


OBJECT_VALIDATION_STATE_CLEAR = "clear"
OBJECT_VALIDATION_STATE_INFO = "info"
OBJECT_VALIDATION_STATE_WARNING = "warning"
OBJECT_VALIDATION_STATE_ERROR = "error"
OBJECT_VALIDATION_STATE_BLOCKED = "blocked"
DEFAULT_OBJECT_PROJECTION_TYPE = "object_payload"

_VALIDATION_STATE_BY_SEVERITY = {
    ValidationFindingSeverity.INFO: OBJECT_VALIDATION_STATE_INFO,
    ValidationFindingSeverity.WARNING: OBJECT_VALIDATION_STATE_WARNING,
    ValidationFindingSeverity.ERROR: OBJECT_VALIDATION_STATE_ERROR,
    ValidationFindingSeverity.BLOCKER: OBJECT_VALIDATION_STATE_BLOCKED,
}
_VALIDATION_STATE_RANK = {
    OBJECT_VALIDATION_STATE_CLEAR: 0,
    OBJECT_VALIDATION_STATE_INFO: 1,
    OBJECT_VALIDATION_STATE_WARNING: 2,
    OBJECT_VALIDATION_STATE_ERROR: 3,
    OBJECT_VALIDATION_STATE_BLOCKED: 4,
}


class DomainEnvelopePersistenceError(RuntimeError):
    """Base error for invalid domain envelope persistence requests."""


class StaleDomainEnvelopeRevisionError(DomainEnvelopePersistenceError):
    """Raised when a checkpoint write targets an obsolete envelope revision."""

    def __init__(
        self,
        *,
        envelope_id: str,
        expected_revision: int,
        actual_revision: int | None,
    ) -> None:
        actual_label = "missing" if actual_revision is None else str(actual_revision)
        super().__init__(
            f"Domain envelope {envelope_id} revision mismatch: expected "
            f"{expected_revision}, found {actual_label}. Reload the envelope before "
            "writing the next safe checkpoint."
        )
        self.envelope_id = envelope_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


@dataclass(frozen=True)
class DomainEnvelopeIndexCounts:
    """Counts produced by regenerating current-revision envelope indexes."""

    object_count: int
    finding_count: int
    projection_count: int


@dataclass(frozen=True)
class DomainEnvelopeCheckpointRequest:
    """Input for an atomic envelope checkpoint write."""

    project_key: str
    envelope: DomainEnvelope
    expected_revision: int
    document_id: str | UUID | None = None
    session_id: str | UUID | None = None
    flow_run_id: str | None = None
    adapter_key: str | None = None
    source_extraction_result_id: str | UUID | None = None
    source_payload_hash: str | None = None
    object_model_ref_json: Mapping[str, Any] = field(default_factory=dict)
    model_field_ref_json: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DomainEnvelopeCheckpointResult:
    """Summary returned after a successful checkpoint flush."""

    envelope_id: str
    revision: int
    object_count: int
    finding_count: int
    projection_count: int
    inserted_history_event_count: int


def write_domain_envelope_checkpoint(
    db: Session,
    request: DomainEnvelopeCheckpointRequest,
) -> DomainEnvelopeCheckpointResult:
    """Persist a completed envelope checkpoint and regenerate current indexes.

    The caller supplies the fully patched ``DomainEnvelope`` for the next
    revision. It locks the current envelope row, verifies the expected revision,
    writes the new envelope JSON, regenerates object/finding/projection indexes from that
    stored JSON, and appends unseen history events by event_id. The supplied
    session is flushed but never committed or rolled back; the caller owns the
    transaction boundary.
    """

    envelope = request.envelope
    if request.expected_revision < 0:
        raise DomainEnvelopePersistenceError("expected_revision must be zero or greater")

    project_key = _required_string(request.project_key, field_name="project_key")
    flow_run_id = _optional_non_empty_string(request.flow_run_id, field_name="flow_run_id")
    envelope_json = envelope.model_dump(mode="json")
    requested_source_payload_hash = request.source_payload_hash
    now = datetime.now(timezone.utc)

    envelope_row = db.scalars(
        select(DomainEnvelopeModel)
        .where(DomainEnvelopeModel.envelope_id == envelope.envelope_id)
        .with_for_update()
    ).first()

    if envelope_row is None:
        if request.expected_revision != 0:
            raise StaleDomainEnvelopeRevisionError(
                envelope_id=envelope.envelope_id,
                expected_revision=request.expected_revision,
                actual_revision=None,
            )
        next_revision = 1
        envelope_row = DomainEnvelopeModel(
            envelope_id=envelope.envelope_id,
            revision=next_revision,
            created_at=now,
        )
        adapter_key = _required_string(request.adapter_key, field_name="adapter_key")
        document_id = _optional_uuid(request.document_id, field_name="document_id")
        if document_id is None:
            raise DomainEnvelopePersistenceError("A new domain envelope requires document_id")
        source_payload_hash = (
            _required_string(requested_source_payload_hash, field_name="source_payload_hash")
            if requested_source_payload_hash is not None
            else domain_envelope_payload_hash(envelope)
        )
        source_extraction_result_id = _optional_non_empty_string(
            None
            if request.source_extraction_result_id is None
            else str(request.source_extraction_result_id),
            field_name="source_extraction_result_id",
        )
        session_id = _optional_uuid(request.session_id, field_name="session_id")
        if source_extraction_result_id is None and session_id is None:
            raise DomainEnvelopePersistenceError(
                "A new domain envelope requires a source extraction result or review session"
            )
        db.add(envelope_row)
    else:
        if envelope_row.revision != request.expected_revision:
            raise StaleDomainEnvelopeRevisionError(
                envelope_id=envelope.envelope_id,
                expected_revision=request.expected_revision,
                actual_revision=envelope_row.revision,
            )
        adapter_key = envelope_row.adapter_key
        source_extraction_result_id = envelope_row.source_extraction_result_id
        session_id = envelope_row.session_id
        source_payload_hash = envelope_row.source_payload_hash
        _validate_checkpoint_scope(
            envelope_row,
            project_key=project_key,
            domain_pack_key=envelope.domain_pack_id,
            domain_pack_version=envelope.domain_pack_version,
            document_id=_optional_uuid(request.document_id, field_name="document_id"),
            flow_run_id=flow_run_id,
            adapter_key=request.adapter_key,
            source_extraction_result_id=_optional_non_empty_string(
                None
                if request.source_extraction_result_id is None
                else str(request.source_extraction_result_id),
                field_name="source_extraction_result_id",
            ),
            session_id=_optional_uuid(request.session_id, field_name="session_id"),
            source_payload_hash=requested_source_payload_hash,
        )
        if session_id is None and request.session_id is not None:
            session_id = _optional_uuid(request.session_id, field_name="session_id")
        next_revision = envelope_row.revision + 1
        envelope_row.revision = next_revision

    envelope_row.project_key = project_key
    envelope_row.domain_pack_key = envelope.domain_pack_id
    envelope_row.domain_pack_version = envelope.domain_pack_version
    envelope_row.status = envelope.status
    envelope_row.document_id = _optional_uuid(request.document_id, field_name="document_id")
    envelope_row.session_id = session_id
    envelope_row.flow_run_id = flow_run_id
    envelope_row.adapter_key = adapter_key
    envelope_row.source_extraction_result_id = source_extraction_result_id
    envelope_row.source_payload_hash = source_payload_hash
    envelope_row.schema_provider = (
        envelope.schema_ref.provider if envelope.schema_ref is not None else None
    )
    envelope_row.schema_ref_json = _schema_ref_json(envelope.schema_ref)
    envelope_row.object_model_ref_json = dict(request.object_model_ref_json)
    envelope_row.model_field_ref_json = dict(request.model_field_ref_json)
    envelope_row.envelope_json = envelope_json
    envelope_row.updated_at = now
    envelope_row.checkpointed_at = now
    db.flush()

    index_counts = _regenerate_indexes_for_row(db, envelope_row)
    inserted_history_event_count = _append_history_events_for_row(db, envelope_row)
    db.flush()

    return DomainEnvelopeCheckpointResult(
        envelope_id=envelope.envelope_id,
        revision=next_revision,
        object_count=index_counts.object_count,
        finding_count=index_counts.finding_count,
        projection_count=index_counts.projection_count,
        inserted_history_event_count=inserted_history_event_count,
    )


def domain_envelope_payload_hash(envelope: DomainEnvelope) -> str:
    """Return the PostgreSQL-JSONB canonical hash for the materialized source payload."""

    payload = json.dumps(
        _postgres_jsonb_key_order(envelope.model_dump(mode="json")),
        ensure_ascii=False,
        separators=(", ", ": "),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _postgres_jsonb_key_order(value: Any) -> Any:
    """Mirror JSONB object-key ordering so Python and migration hashes agree."""

    if isinstance(value, Mapping):
        return {
            key: _postgres_jsonb_key_order(value[key])
            for key in sorted(value, key=_postgres_jsonb_sort_key)
        }
    if isinstance(value, list):
        return [_postgres_jsonb_key_order(item) for item in value]
    return value


def _postgres_jsonb_sort_key(value: Any) -> tuple[int, bytes]:
    encoded = str(value).encode("utf-8")
    return len(encoded), encoded


def _validate_checkpoint_scope(
    row: DomainEnvelopeModel,
    *,
    project_key: str,
    domain_pack_key: str,
    domain_pack_version: str | None,
    document_id: UUID | None,
    flow_run_id: str | None,
    adapter_key: str | None,
    source_extraction_result_id: str | None,
    session_id: UUID | None,
    source_payload_hash: str | None,
) -> None:
    expected = {
        "project_key": project_key,
        "domain_pack_key": domain_pack_key,
        "domain_pack_version": domain_pack_version,
        "document_id": document_id,
        "flow_run_id": flow_run_id,
    }
    if adapter_key is not None:
        expected["adapter_key"] = _required_string(adapter_key, field_name="adapter_key")
    if source_extraction_result_id is not None:
        expected["source_extraction_result_id"] = source_extraction_result_id
    if source_payload_hash is not None:
        expected["source_payload_hash"] = source_payload_hash
    for field_name, expected_value in expected.items():
        if getattr(row, field_name) != expected_value:
            raise DomainEnvelopePersistenceError(
                f"Domain envelope {row.envelope_id} cannot be reused with a different "
                f"{field_name}"
            )
    if row.session_id is not None and session_id != row.session_id:
        raise DomainEnvelopePersistenceError(
            f"Domain envelope {row.envelope_id} belongs to a different review session"
        )


def load_domain_envelope(
    db: Session,
    envelope_id: str,
    *,
    revision: int | None = None,
) -> DomainEnvelope:
    """Load an envelope JSON snapshot at the current stored revision."""

    normalized_envelope_id = _required_string(envelope_id, field_name="envelope_id")
    envelope_row = db.get(DomainEnvelopeModel, normalized_envelope_id)
    if envelope_row is None:
        raise DomainEnvelopePersistenceError(
            f"Domain envelope {normalized_envelope_id} was not found"
        )
    if revision is not None and envelope_row.revision != revision:
        raise DomainEnvelopePersistenceError(
            f"Domain envelope {normalized_envelope_id} is at revision "
            f"{envelope_row.revision}, not requested revision {revision}"
        )
    return DomainEnvelope.model_validate(envelope_row.envelope_json)


def regenerate_domain_envelope_indexes(
    db: Session,
    envelope_id: str,
) -> DomainEnvelopeIndexCounts:
    """Regenerate indexes and flush without committing the supplied session."""

    normalized_envelope_id = _required_string(envelope_id, field_name="envelope_id")
    envelope_row = db.scalars(
        select(DomainEnvelopeModel)
        .where(DomainEnvelopeModel.envelope_id == normalized_envelope_id)
        .with_for_update()
    ).first()
    if envelope_row is None:
        raise DomainEnvelopePersistenceError(
            f"Domain envelope {normalized_envelope_id} was not found"
        )

    counts = _regenerate_indexes_for_row(db, envelope_row)
    db.flush()
    return counts


def _regenerate_indexes_for_row(
    db: Session,
    envelope_row: DomainEnvelopeModel,
) -> DomainEnvelopeIndexCounts:
    envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
    indexed_at = datetime.now(timezone.utc)
    object_id_by_ref = _object_id_by_ref(envelope)
    validation_state_by_object = _validation_state_by_object(envelope, object_id_by_ref)

    db.execute(
        delete(DomainEnvelopeProjectionIndex).where(
            DomainEnvelopeProjectionIndex.envelope_id == envelope_row.envelope_id
        )
    )
    db.execute(
        delete(DomainValidationFinding).where(
            DomainValidationFinding.envelope_id == envelope_row.envelope_id
        )
    )
    db.execute(
        delete(DomainEnvelopeObject).where(
            DomainEnvelopeObject.envelope_id == envelope_row.envelope_id
        )
    )
    db.flush()

    object_count = 0
    for object_index, domain_object in enumerate(envelope.extracted_objects):
        object_id = _stable_object_id(domain_object)
        schema_ref_json = _schema_ref_json(domain_object.schema_ref)
        object_metadata = dict(domain_object.metadata)
        db.add(
            DomainEnvelopeObject(
                envelope_id=envelope_row.envelope_id,
                object_id=object_id,
                pending_ref_id=domain_object.pending_ref_id,
                envelope_revision=envelope_row.revision,
                object_index=object_index,
                object_type=domain_object.object_type,
                status=domain_object.status,
                validation_state=validation_state_by_object[object_id],
                schema_provider=(
                    domain_object.schema_ref.provider
                    if domain_object.schema_ref is not None
                    else None
                ),
                schema_ref_json=schema_ref_json,
                object_model_ref_json=_object_model_ref_json(object_metadata),
                model_field_ref_json=_model_field_ref_json(object_metadata),
                payload_json=dict(domain_object.payload),
                object_json=domain_object.model_dump(mode="json"),
                created_at=indexed_at,
                updated_at=indexed_at,
            )
        )
        object_count += 1

    finding_count = 0
    for finding_index, finding in enumerate(envelope.validation_findings):
        object_id, field_path = _finding_target(finding, object_id_by_ref)
        db.add(
            DomainValidationFinding(
                envelope_id=envelope_row.envelope_id,
                finding_id=finding.finding_id,
                envelope_revision=envelope_row.revision,
                finding_index=finding_index,
                object_id=object_id,
                field_path=field_path,
                severity=finding.severity,
                status=finding.status,
                code=finding.code,
                object_model_ref_json=_object_model_ref_json(finding.details),
                model_field_ref_json=_model_field_ref_json(finding.details),
                finding_json=finding.model_dump(mode="json"),
                created_at=indexed_at,
                updated_at=indexed_at,
            )
        )
        finding_count += 1

    projection_count = 0
    for projection_row in _projection_rows(envelope_row, envelope, object_id_by_ref):
        db.add(projection_row)
        projection_count += 1

    try:
        db.flush()
    except IntegrityError as exc:
        raise DomainEnvelopePersistenceError(
            "Domain envelope projection indexes must be unique by "
            "(envelope_id, object_id, projection_type, projection_key)"
        ) from exc

    return DomainEnvelopeIndexCounts(
        object_count=object_count,
        finding_count=finding_count,
        projection_count=projection_count,
    )


def _append_history_events_for_row(
    db: Session,
    envelope_row: DomainEnvelopeModel,
) -> int:
    envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
    indexed_at = datetime.now(timezone.utc)
    existing_event_ids = set(
        db.scalars(
            select(DomainEnvelopeHistory.event_id).where(
                DomainEnvelopeHistory.envelope_id == envelope_row.envelope_id
            )
        ).all()
    )
    object_id_by_ref = _object_id_by_ref(envelope)

    inserted_count = 0
    for event_index, event in enumerate(envelope.history):
        event_id = _history_event_id(envelope.envelope_id, event_index, event)
        if event_id in existing_event_ids:
            continue
        object_id, field_path = _history_target(event, object_id_by_ref)
        db.add(
            DomainEnvelopeHistory(
                envelope_id=envelope_row.envelope_id,
                event_id=event_id,
                envelope_revision=envelope_row.revision,
                event_index=event_index,
                event_type=event.event_type,
                occurred_at=event.timestamp,
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                object_id=object_id,
                field_path=field_path,
                model_field_ref_json=_model_field_ref_json(event.details),
                event_json=event.model_dump(mode="json"),
                created_at=indexed_at,
            )
        )
        existing_event_ids.add(event_id)
        inserted_count += 1

    db.flush()
    return inserted_count


def _projection_rows(
    envelope_row: DomainEnvelopeModel,
    envelope: DomainEnvelope,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> list[DomainEnvelopeProjectionIndex]:
    rows: list[DomainEnvelopeProjectionIndex] = []

    for domain_object in envelope.extracted_objects:
        object_id = _stable_object_id(domain_object)
        object_metadata = dict(domain_object.metadata)
        rows.append(
            _projection_row(
                envelope_row=envelope_row,
                object_id=object_id,
                object_type=domain_object.object_type,
                projection_type=DEFAULT_OBJECT_PROJECTION_TYPE,
                projection_key=object_id,
                projection_status=domain_object.status.value,
                schema_provider=(
                    domain_object.schema_ref.provider
                    if domain_object.schema_ref is not None
                    else None
                ),
                schema_ref_json=_schema_ref_json(domain_object.schema_ref),
                object_model_ref_json=_object_model_ref_json(object_metadata),
                model_field_ref_json=_model_field_ref_json(object_metadata),
                projection_json=domain_object.model_dump(mode="json"),
            )
        )

        for projection in _metadata_projection_entries(
            object_metadata,
            metadata_key="projections",
        ):
            rows.append(
                _projection_row_from_entry(
                    envelope_row=envelope_row,
                    entry=projection,
                    object_id=object_id,
                    object_type=domain_object.object_type,
                    schema_provider=(
                        domain_object.schema_ref.provider
                        if domain_object.schema_ref is not None
                        else None
                    ),
                    schema_ref_json=_schema_ref_json(domain_object.schema_ref),
                    object_model_ref_json=_object_model_ref_json(object_metadata),
                    model_field_ref_json=_model_field_ref_json(projection),
                )
            )

    for projection in _metadata_projection_entries(
        envelope.metadata,
        metadata_key="projection_index",
    ):
        object_id = _projection_entry_object_id(projection, object_id_by_ref)
        rows.append(
            _projection_row_from_entry(
                envelope_row=envelope_row,
                entry=projection,
                object_id=object_id,
                object_type=_optional_non_empty_string(
                    projection.get("object_type"),
                    field_name="projection_index.object_type",
                ),
                schema_provider=None,
                schema_ref_json={},
                object_model_ref_json=_object_model_ref_json(projection),
                model_field_ref_json=_model_field_ref_json(projection),
            )
        )

    return rows


def _projection_row_from_entry(
    *,
    envelope_row: DomainEnvelopeModel,
    entry: Mapping[str, Any],
    object_id: str,
    object_type: str | None,
    schema_provider: str | None,
    schema_ref_json: Mapping[str, Any],
    object_model_ref_json: Mapping[str, Any],
    model_field_ref_json: Mapping[str, Any],
) -> DomainEnvelopeProjectionIndex:
    return _projection_row(
        envelope_row=envelope_row,
        object_id=object_id,
        object_type=object_type,
        projection_type=_required_string(
            entry.get("projection_type"),
            field_name="projection_type",
        ),
        projection_key=_required_string(
            entry.get("projection_key"),
            field_name="projection_key",
        ),
        projection_status=_optional_non_empty_string(
            entry.get("projection_status"),
            field_name="projection_status",
        ),
        schema_provider=schema_provider,
        schema_ref_json=schema_ref_json,
        object_model_ref_json=object_model_ref_json,
        model_field_ref_json=model_field_ref_json,
        projection_json=_projection_json(entry),
    )


def _projection_row(
    *,
    envelope_row: DomainEnvelopeModel,
    object_id: str,
    object_type: str | None,
    projection_type: str,
    projection_key: str,
    projection_status: str | None,
    schema_provider: str | None,
    schema_ref_json: Mapping[str, Any],
    object_model_ref_json: Mapping[str, Any],
    model_field_ref_json: Mapping[str, Any],
    projection_json: dict[str, Any] | list[Any],
) -> DomainEnvelopeProjectionIndex:
    return DomainEnvelopeProjectionIndex(
        envelope_id=envelope_row.envelope_id,
        object_id=object_id,
        envelope_revision=envelope_row.revision,
        object_type=object_type,
        projection_type=projection_type,
        projection_key=projection_key,
        projection_status=projection_status,
        schema_provider=schema_provider,
        schema_ref_json=dict(schema_ref_json),
        object_model_ref_json=dict(object_model_ref_json),
        model_field_ref_json=dict(model_field_ref_json),
        projection_json=projection_json,
        created_at=envelope_row.updated_at,
        updated_at=envelope_row.updated_at,
    )


def _metadata_projection_entries(
    metadata: Mapping[str, Any],
    *,
    metadata_key: str,
) -> list[Mapping[str, Any]]:
    entries = metadata.get(metadata_key, [])
    if entries is None:
        raise DomainEnvelopePersistenceError(
            f"{metadata_key} must be a list of objects, got null"
        )
    if not isinstance(entries, list) or not all(isinstance(item, Mapping) for item in entries):
        raise DomainEnvelopePersistenceError(f"{metadata_key} must be a list of objects")
    return entries


def _projection_entry_object_id(
    entry: Mapping[str, Any],
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> str:
    object_id = _optional_non_empty_string(entry.get("object_id"), field_name="object_id")
    if object_id is not None:
        return object_id

    pending_ref_id = _optional_non_empty_string(
        entry.get("pending_ref_id"),
        field_name="pending_ref_id",
    )
    if pending_ref_id is not None:
        resolved_object_id = object_id_by_ref.get(("pending_ref_id", pending_ref_id))
        if resolved_object_id is not None:
            return resolved_object_id

    raise DomainEnvelopePersistenceError(
        "projection_index entries must provide object_id or a resolvable pending_ref_id"
    )


def _projection_json(entry: Mapping[str, Any]) -> dict[str, Any] | list[Any]:
    projection_json = entry.get("projection_json")
    if not isinstance(projection_json, (dict, list)):
        raise DomainEnvelopePersistenceError(
            "projection entries must provide projection_json"
        )
    return projection_json


def _object_id_by_ref(envelope: DomainEnvelope) -> dict[tuple[str, str], str]:
    object_id_by_ref: dict[tuple[str, str], str] = {}
    for domain_object in envelope.extracted_objects:
        stable_object_id = _stable_object_id(domain_object)
        if domain_object.object_id is not None:
            object_id_by_ref[("object_id", domain_object.object_id)] = stable_object_id
        if domain_object.pending_ref_id is not None:
            object_id_by_ref[("pending_ref_id", domain_object.pending_ref_id)] = stable_object_id
    return object_id_by_ref


def _stable_object_id(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_id is not None:
        return domain_object.object_id
    if domain_object.pending_ref_id is not None:
        return domain_object.pending_ref_id
    raise DomainEnvelopePersistenceError(
        "CuratableObjectEnvelope has neither object_id nor pending_ref_id"
    )


def _validation_state_by_object(
    envelope: DomainEnvelope,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> dict[str, str]:
    validation_state_by_object = {
        _stable_object_id(domain_object): OBJECT_VALIDATION_STATE_CLEAR
        for domain_object in envelope.extracted_objects
    }

    for finding in envelope.validation_findings:
        if finding.status is not ValidationFindingStatus.OPEN:
            continue
        object_id, _field_path = _finding_target(finding, object_id_by_ref)
        if object_id is None:
            continue

        current_state = validation_state_by_object[object_id]
        candidate_state = _VALIDATION_STATE_BY_SEVERITY[finding.severity]
        if _VALIDATION_STATE_RANK[candidate_state] > _VALIDATION_STATE_RANK[current_state]:
            validation_state_by_object[object_id] = candidate_state

    return validation_state_by_object


def _finding_target(
    finding: ValidationFinding,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> tuple[str | None, str | None]:
    if finding.field_ref is not None:
        return (
            _resolve_object_ref(finding.field_ref.object_ref, object_id_by_ref),
            finding.field_ref.field_path,
        )
    if finding.object_ref is not None:
        return _resolve_object_ref(finding.object_ref, object_id_by_ref), None
    return None, None


def _history_target(
    event: HistoryEvent,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> tuple[str | None, str | None]:
    if event.field_ref is not None:
        return (
            _resolve_object_ref(event.field_ref.object_ref, object_id_by_ref),
            event.field_ref.field_path,
        )
    if event.object_ref is not None:
        return _resolve_object_ref(event.object_ref, object_id_by_ref), None
    return None, None


def _resolve_object_ref(
    object_ref: ObjectRef,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> str | None:
    return object_id_by_ref.get(object_ref.ref_key())


def _history_event_id(envelope_id: str, event_index: int, event: HistoryEvent) -> str:
    if event.event_id is not None:
        return event.event_id
    seed_payload = {
        "envelope_id": envelope_id,
        "event_index": event_index,
        "event": event.model_dump(mode="json"),
    }
    digest = sha256(json.dumps(seed_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return f"derived:{digest}"


def _schema_ref_json(schema_ref: Any | None) -> dict[str, Any]:
    return schema_ref.model_dump(mode="json") if schema_ref is not None else {}


def _object_model_ref_json(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return _selected_metadata_refs(
        metadata,
        keys=("object_model_ref", "object_model_ref_json", "provider_refs"),
    )


def _model_field_ref_json(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return _selected_metadata_refs(
        metadata,
        keys=("model_field_ref", "model_field_ref_json", "field_provider_refs"),
    )


def _selected_metadata_refs(
    metadata: Mapping[str, Any],
    *,
    keys: tuple[str, ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, Mapping):
            payload[key] = dict(value)
    return payload


def _required_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainEnvelopePersistenceError(f"{field_name} must be a non-empty string")
    normalized = value.strip()
    if value != normalized:
        raise DomainEnvelopePersistenceError(
            f"{field_name} must not include leading or trailing whitespace"
        )
    return normalized


def _optional_non_empty_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name=field_name)


def _optional_uuid(value: str | UUID | None, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise DomainEnvelopePersistenceError(f"{field_name} must be a UUID") from exc


__all__ = [
    "DEFAULT_OBJECT_PROJECTION_TYPE",
    "DomainEnvelopeCheckpointRequest",
    "DomainEnvelopeCheckpointResult",
    "DomainEnvelopeIndexCounts",
    "DomainEnvelopePersistenceError",
    "OBJECT_VALIDATION_STATE_BLOCKED",
    "OBJECT_VALIDATION_STATE_CLEAR",
    "OBJECT_VALIDATION_STATE_ERROR",
    "OBJECT_VALIDATION_STATE_INFO",
    "OBJECT_VALIDATION_STATE_WARNING",
    "StaleDomainEnvelopeRevisionError",
    "domain_envelope_payload_hash",
    "load_domain_envelope",
    "regenerate_domain_envelope_indexes",
    "write_domain_envelope_checkpoint",
]
