"""One-off migration from legacy curation workspace rows to domain envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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
)
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    write_domain_envelope_checkpoint,
)
from src.schemas.curation_workspace import (
    CurationCandidateStatus,
    CurationSessionStatus,
    CurationSubmissionStatus,
    CurationValidationSnapshotState,
    FieldValidationStatus,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    CuratableObjectStatus,
    DomainEnvelope,
    DomainEnvelopeStatus,
    HistoryActorType,
    HistoryEvent,
    HistoryEventKind,
    ObjectRef,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)


MIGRATION_NAME = "legacy_curation_workspace_to_domain_envelopes"
MIGRATION_VERSION = "0.7.0"
DEFAULT_LEGACY_DOMAIN_PACK_ID = "legacy_curation_workspace"
DEFAULT_LEGACY_DOMAIN_PACK_VERSION = "0.7.0"
DEFAULT_PROJECT_KEY = "agr_ai_curation"
LEGACY_CANDIDATE_OBJECT_TYPE = "LegacyCurationCandidate"
LEGACY_EXTRACTION_OBJECT_TYPE = "LegacyExtractionResult"
LEGACY_WORKSPACE_PROJECTION_TYPE = "legacy_workspace_candidate"
LEGACY_DRAFT_PROJECTION_TYPE = "legacy_annotation_draft"
LEGACY_EXTRACTION_PROJECTION_TYPE = "legacy_extraction_result"

SOURCE_TABLE_REVIEW_SESSIONS = "curation_review_sessions"
SOURCE_TABLE_EXTRACTION_RESULTS = "extraction_results"
SOURCE_TABLE_CANDIDATES = "curation_candidates"
SOURCE_TABLE_DRAFTS = "annotation_drafts"
SOURCE_TABLE_EVIDENCE = "evidence_anchors"
SOURCE_TABLE_VALIDATION = "validation_snapshots"
SOURCE_TABLE_SUBMISSIONS = "curation_submissions"
SOURCE_TABLE_ACTION_LOG = "curation_action_log"


@dataclass(frozen=True, order=True)
class LegacySourceRef:
    """Stable reference to a legacy source row recorded in migration history."""

    table_name: str
    row_id: str

    def to_json(self) -> dict[str, str]:
        return {"table_name": self.table_name, "row_id": self.row_id}


@dataclass(frozen=True)
class LegacyMigrationBlocker:
    """A legacy row that cannot be converted without ambiguous or lossy behavior."""

    source_table: str
    source_id: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "source_table": self.source_table,
            "source_id": self.source_id,
            "reason": self.reason,
            "details": _json_safe(self.details),
        }


@dataclass(frozen=True)
class LegacyCurationWorkspaceMigrationOptions:
    """Execution options for the one-off legacy workspace migration."""

    project_key: str = DEFAULT_PROJECT_KEY
    domain_pack_id: str = DEFAULT_LEGACY_DOMAIN_PACK_ID
    domain_pack_version: str = DEFAULT_LEGACY_DOMAIN_PACK_VERSION
    actor_id: str = MIGRATION_NAME
    dry_run: bool = False


@dataclass
class LegacyCurationWorkspaceMigrationSummary:
    """Structured migration result used by scripts, tests, and workpad handoffs."""

    dry_run: bool = False
    inspected_sessions: int = 0
    inspected_extraction_results: int = 0
    migrated_envelopes: int = 0
    would_migrate_envelopes: int = 0
    skipped_already_migrated_sources: int = 0
    linked_candidate_projection_refs: int = 0
    blockers: list[LegacyMigrationBlocker] = field(default_factory=list)

    @property
    def blocker_count(self) -> int:
        return len(self.blockers)

    @property
    def has_blockers(self) -> bool:
        return bool(self.blockers)

    def to_json(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "inspected_sessions": self.inspected_sessions,
            "inspected_extraction_results": self.inspected_extraction_results,
            "migrated_envelopes": self.migrated_envelopes,
            "would_migrate_envelopes": self.would_migrate_envelopes,
            "skipped_already_migrated_sources": self.skipped_already_migrated_sources,
            "linked_candidate_projection_refs": self.linked_candidate_projection_refs,
            "blocker_count": self.blocker_count,
            "blockers": [blocker.to_json() for blocker in self.blockers],
        }


def migrate_legacy_curation_workspace_to_domain_envelopes(
    db: Session,
    options: LegacyCurationWorkspaceMigrationOptions | None = None,
) -> LegacyCurationWorkspaceMigrationSummary:
    """Convert retained legacy workspace rows into domain envelopes.

    The migration is deliberately idempotent. It recognizes already-migrated
    source rows from envelope history provenance, repairs missing candidate
    projection refs for previously migrated sessions, and leaves blocker rows
    untouched so a later retry can process them after the data issue is fixed.
    """

    options = options or LegacyCurationWorkspaceMigrationOptions()
    _validate_options(options)

    summary = LegacyCurationWorkspaceMigrationSummary(dry_run=options.dry_run)
    migrated_sources = _load_migrated_source_refs(db)
    blocked_extraction_ids: set[str] = set()

    for session_row in _load_review_sessions(db):
        summary.inspected_sessions += 1
        session_ref = _source_ref(SOURCE_TABLE_REVIEW_SESSIONS, session_row.id)
        blockers = _session_blockers(session_row, migrated_sources)
        if blockers:
            summary.blockers.extend(blockers)
            blocked_extraction_ids.update(
                str(candidate.extraction_result_id)
                for candidate in session_row.candidates
                if candidate.extraction_result_id is not None
            )
            continue

        if session_ref in migrated_sources:
            summary.skipped_already_migrated_sources += 1
            if not options.dry_run:
                summary.linked_candidate_projection_refs += _link_session_candidates(
                    db,
                    session_row,
                    envelope_id=_session_envelope_id(session_row.id),
                    envelope_revision=_current_envelope_revision(
                        db,
                        _session_envelope_id(session_row.id),
                    ),
                )
            continue

        collision = _existing_envelope_collision(
            db,
            envelope_id=_session_envelope_id(session_row.id),
            migrated_sources=migrated_sources,
        )
        if collision is not None:
            summary.blockers.append(collision)
            continue

        envelope = _session_envelope(session_row, options=options)
        if options.dry_run:
            summary.would_migrate_envelopes += 1
            migrated_sources.update(_source_refs_from_envelope(envelope))
            continue

        checkpoint = write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key=options.project_key,
                envelope=envelope,
                expected_revision=0,
                document_id=session_row.document_id,
                session_id=session_row.id,
                flow_run_id=session_row.flow_run_id,
                object_model_ref_json=_legacy_model_ref_json(),
                model_field_ref_json=_legacy_field_ref_json(),
            ),
        )
        summary.migrated_envelopes += 1
        summary.linked_candidate_projection_refs += _link_session_candidates(
            db,
            session_row,
            envelope_id=checkpoint.envelope_id,
            envelope_revision=checkpoint.revision,
        )
        migrated_sources.update(_source_refs_from_envelope(envelope))

    for extraction_result in _load_extraction_results(db):
        summary.inspected_extraction_results += 1
        extraction_ref = _source_ref(SOURCE_TABLE_EXTRACTION_RESULTS, extraction_result.id)
        if extraction_ref in migrated_sources:
            summary.skipped_already_migrated_sources += 1
            continue
        if str(extraction_result.id) in blocked_extraction_ids:
            continue
        if extraction_result.candidates:
            continue

        collision = _existing_envelope_collision(
            db,
            envelope_id=_extraction_envelope_id(extraction_result.id),
            migrated_sources=migrated_sources,
        )
        if collision is not None:
            summary.blockers.append(collision)
            continue

        envelope = _extraction_result_envelope(extraction_result, options=options)
        if options.dry_run:
            summary.would_migrate_envelopes += 1
            migrated_sources.update(_source_refs_from_envelope(envelope))
            continue

        write_domain_envelope_checkpoint(
            db,
            DomainEnvelopeCheckpointRequest(
                project_key=options.project_key,
                envelope=envelope,
                expected_revision=0,
                document_id=extraction_result.document_id,
                flow_run_id=extraction_result.flow_run_id,
                object_model_ref_json=_legacy_model_ref_json(),
                model_field_ref_json=_legacy_field_ref_json(),
            ),
        )
        summary.migrated_envelopes += 1
        migrated_sources.update(_source_refs_from_envelope(envelope))

    return summary


def _validate_options(options: LegacyCurationWorkspaceMigrationOptions) -> None:
    for field_name in ("project_key", "domain_pack_id", "actor_id"):
        value = getattr(options, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        if value != value.strip():
            raise ValueError(f"{field_name} must not include surrounding whitespace")
    if (
        options.domain_pack_version is not None
        and (
            not options.domain_pack_version.strip()
            or options.domain_pack_version != options.domain_pack_version.strip()
        )
    ):
        raise ValueError("domain_pack_version must be non-empty when provided")


def _load_review_sessions(db: Session) -> list[CurationReviewSession]:
    return list(
        db.scalars(
            select(CurationReviewSession)
            .options(
                selectinload(CurationReviewSession.candidates).selectinload(
                    CurationCandidate.draft
                ),
                selectinload(CurationReviewSession.candidates).selectinload(
                    CurationCandidate.evidence_anchors
                ),
                selectinload(CurationReviewSession.candidates).selectinload(
                    CurationCandidate.validation_snapshots
                ),
                selectinload(CurationReviewSession.candidates).selectinload(
                    CurationCandidate.action_log_entries
                ),
                selectinload(CurationReviewSession.candidates).selectinload(
                    CurationCandidate.extraction_result
                ),
                selectinload(CurationReviewSession.validation_snapshots),
                selectinload(CurationReviewSession.submissions),
                selectinload(CurationReviewSession.action_log_entries),
            )
            .order_by(CurationReviewSession.prepared_at.asc(), CurationReviewSession.id.asc())
        )
        .unique()
        .all()
    )


def _load_extraction_results(db: Session) -> list[CurationExtractionResultRecord]:
    return list(
        db.scalars(
            select(CurationExtractionResultRecord)
            .options(selectinload(CurationExtractionResultRecord.candidates))
            .order_by(
                CurationExtractionResultRecord.created_at.asc(),
                CurationExtractionResultRecord.id.asc(),
            )
        )
        .unique()
        .all()
    )


def _load_migrated_source_refs(db: Session) -> set[LegacySourceRef]:
    refs: set[LegacySourceRef] = set()
    for event_json in db.scalars(select(DomainEnvelopeHistory.event_json)).all():
        refs.update(_source_refs_from_event_json(event_json))
    return refs


def _source_refs_from_event_json(event_json: Any) -> set[LegacySourceRef]:
    if not isinstance(event_json, Mapping):
        return set()
    details = event_json.get("details")
    if not isinstance(details, Mapping):
        return set()
    migration = details.get("legacy_migration")
    if not isinstance(migration, Mapping) or migration.get("name") != MIGRATION_NAME:
        return set()
    return _source_refs_from_json(migration.get("source_records"))


def _source_refs_from_envelope(envelope: DomainEnvelope) -> set[LegacySourceRef]:
    refs: set[LegacySourceRef] = set()
    for event in envelope.history:
        migration = event.details.get("legacy_migration")
        if isinstance(migration, Mapping) and migration.get("name") == MIGRATION_NAME:
            refs.update(_source_refs_from_json(migration.get("source_records")))
    return refs


def _source_refs_from_json(value: Any) -> set[LegacySourceRef]:
    refs: set[LegacySourceRef] = set()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return refs
    for item in value:
        if not isinstance(item, Mapping):
            continue
        table_name = item.get("table_name")
        row_id = item.get("row_id")
        if isinstance(table_name, str) and table_name and isinstance(row_id, str) and row_id:
            refs.add(LegacySourceRef(table_name=table_name, row_id=row_id))
    return refs


def _session_blockers(
    session_row: CurationReviewSession,
    migrated_sources: set[LegacySourceRef],
) -> list[LegacyMigrationBlocker]:
    blockers: list[LegacyMigrationBlocker] = []
    candidate_ids = {str(candidate.id) for candidate in session_row.candidates}
    if (
        session_row.current_candidate_id is not None
        and str(session_row.current_candidate_id) not in candidate_ids
    ):
        blockers.append(
            LegacyMigrationBlocker(
                source_table=SOURCE_TABLE_REVIEW_SESSIONS,
                source_id=str(session_row.id),
                reason=(
                    "review session current_candidate_id does not point at a retained "
                    "candidate in the session"
                ),
                details={"current_candidate_id": str(session_row.current_candidate_id)},
            )
        )

    blockers.extend(_action_log_relationship_blockers(session_row, candidate_ids))

    for candidate in session_row.candidates:
        blocker = _candidate_projection_blocker(candidate, migrated_sources)
        if blocker is not None:
            blockers.append(blocker)

    return blockers


def _action_log_relationship_blockers(
    session_row: CurationReviewSession,
    candidate_ids: set[str],
) -> list[LegacyMigrationBlocker]:
    blockers: list[LegacyMigrationBlocker] = []
    session_id = str(session_row.id)
    for entry in session_row.action_log_entries:
        if entry.candidate_id is not None and str(entry.candidate_id) not in candidate_ids:
            blockers.append(
                LegacyMigrationBlocker(
                    source_table=SOURCE_TABLE_ACTION_LOG,
                    source_id=str(entry.id),
                    reason=(
                        "action log candidate_id does not point at a retained candidate "
                        "in the same review session"
                    ),
                    details={
                        "session_id": session_id,
                        "candidate_id": str(entry.candidate_id),
                    },
                )
            )

    for candidate in session_row.candidates:
        for entry in candidate.action_log_entries:
            if str(entry.session_id) == session_id:
                continue
            blockers.append(
                LegacyMigrationBlocker(
                    source_table=SOURCE_TABLE_ACTION_LOG,
                    source_id=str(entry.id),
                    reason=(
                        "action log session_id does not match the retained candidate's "
                        "review session"
                    ),
                    details={
                        "action_log_session_id": str(entry.session_id),
                        "candidate_session_id": session_id,
                        "candidate_id": str(candidate.id),
                    },
                )
            )
    return blockers


def _candidate_projection_blocker(
    candidate: CurationCandidate,
    migrated_sources: set[LegacySourceRef],
) -> LegacyMigrationBlocker | None:
    provided = (
        candidate.envelope_id is not None,
        candidate.object_id is not None,
        candidate.envelope_revision is not None,
    )
    if not any(provided):
        return None
    if not all(provided):
        return LegacyMigrationBlocker(
            source_table=SOURCE_TABLE_CANDIDATES,
            source_id=str(candidate.id),
            reason="candidate has an incomplete domain envelope projection reference",
            details={
                "envelope_id": candidate.envelope_id,
                "object_id": candidate.object_id,
                "envelope_revision": candidate.envelope_revision,
            },
        )

    candidate_ref = _source_ref(SOURCE_TABLE_CANDIDATES, candidate.id)
    expected_envelope_id = _session_envelope_id(candidate.session_id)
    expected_object_id = _candidate_object_id(candidate.id)
    if candidate_ref in migrated_sources:
        if (
            candidate.envelope_id == expected_envelope_id
            and candidate.object_id == expected_object_id
            and candidate.envelope_revision is not None
            and candidate.envelope_revision >= 1
        ):
            return None
        return LegacyMigrationBlocker(
            source_table=SOURCE_TABLE_CANDIDATES,
            source_id=str(candidate.id),
            reason="candidate projection reference does not match recorded migration provenance",
            details={
                "expected_envelope_id": expected_envelope_id,
                "expected_object_id": expected_object_id,
                "actual_envelope_id": candidate.envelope_id,
                "actual_object_id": candidate.object_id,
                "actual_envelope_revision": candidate.envelope_revision,
            },
        )

    return LegacyMigrationBlocker(
        source_table=SOURCE_TABLE_CANDIDATES,
        source_id=str(candidate.id),
        reason=(
            "candidate already points at a domain envelope but has no legacy migration "
            "provenance; refusing to keep legacy and envelope stores as coequal sources"
        ),
        details={
            "envelope_id": candidate.envelope_id,
            "object_id": candidate.object_id,
            "envelope_revision": candidate.envelope_revision,
        },
    )


def _existing_envelope_collision(
    db: Session,
    *,
    envelope_id: str,
    migrated_sources: set[LegacySourceRef],
) -> LegacyMigrationBlocker | None:
    if db.get(DomainEnvelopeModel, envelope_id) is None:
        return None

    known_for_envelope = any(
        event.envelope_id == envelope_id
        and _source_refs_from_event_json(event.event_json).intersection(migrated_sources)
        for event in db.scalars(
            select(DomainEnvelopeHistory).where(
                DomainEnvelopeHistory.envelope_id == envelope_id
            )
        ).all()
    )
    if known_for_envelope:
        return None

    return LegacyMigrationBlocker(
        source_table="domain_envelopes",
        source_id=envelope_id,
        reason="target deterministic envelope id already exists without migration provenance",
    )


def _session_envelope(
    session_row: CurationReviewSession,
    *,
    options: LegacyCurationWorkspaceMigrationOptions,
) -> DomainEnvelope:
    source_records = _session_source_refs(session_row)
    objects = [
        _candidate_object(
            candidate,
            session_row=session_row,
        )
        for candidate in sorted(
            session_row.candidates,
            key=lambda item: (item.order, str(item.id)),
        )
    ]
    validation_findings = _session_validation_findings(session_row)
    history = _session_history(session_row, options=options)

    return DomainEnvelope(
        envelope_id=_session_envelope_id(session_row.id),
        domain_pack_id=options.domain_pack_id,
        domain_pack_version=options.domain_pack_version,
        status=_envelope_status_for_session(session_row),
        objects=objects,
        validation_findings=validation_findings,
        history=history,
        metadata={
            "legacy_migration": _migration_metadata(source_records),
            "legacy_session": _session_payload(session_row),
            "legacy_extraction_results": [
                _extraction_result_payload(extraction_result)
                for extraction_result in _session_extraction_results(session_row)
            ],
            "legacy_submissions": [
                _submission_payload(submission)
                for submission in sorted(session_row.submissions, key=_submission_sort_key)
            ],
            "legacy_validation_snapshots": [
                _validation_snapshot_payload(snapshot)
                for snapshot in sorted(
                    session_row.validation_snapshots,
                    key=_validation_snapshot_sort_key,
                )
            ],
            "legacy_action_log": [
                _action_log_payload(entry)
                for entry in sorted(session_row.action_log_entries, key=_action_log_sort_key)
            ],
        },
    )


def _extraction_result_envelope(
    extraction_result: CurationExtractionResultRecord,
    *,
    options: LegacyCurationWorkspaceMigrationOptions,
) -> DomainEnvelope:
    source_records = [_source_ref(SOURCE_TABLE_EXTRACTION_RESULTS, extraction_result.id)]
    object_id = _extraction_object_id(extraction_result.id)
    object_payload = _extraction_result_payload(extraction_result)

    return DomainEnvelope(
        envelope_id=_extraction_envelope_id(extraction_result.id),
        domain_pack_id=options.domain_pack_id,
        domain_pack_version=options.domain_pack_version,
        status=DomainEnvelopeStatus.EXTRACTED,
        objects=[
            CuratableObjectEnvelope(
                object_type=LEGACY_EXTRACTION_OBJECT_TYPE,
                object_id=object_id,
                status=CuratableObjectStatus.EXTRACTED,
                payload=object_payload,
                metadata={
                    "legacy_migration": _migration_metadata(source_records),
                    "projections": [
                        {
                            "projection_type": LEGACY_EXTRACTION_PROJECTION_TYPE,
                            "projection_key": str(extraction_result.id),
                            "projection_status": "extracted",
                            "projection_json": {
                                "extraction_result_id": str(extraction_result.id),
                                "document_id": str(extraction_result.document_id),
                                "adapter_key": extraction_result.adapter_key,
                                "agent_key": extraction_result.agent_key,
                                "candidate_count": extraction_result.candidate_count,
                                "source_records": [
                                    source.to_json() for source in source_records
                                ],
                            },
                        }
                    ],
                },
            )
        ],
        history=[
            _history_event(
                event_id=f"legacy-extraction-result-created:{extraction_result.id}",
                event_type=HistoryEventKind.CREATED,
                timestamp=extraction_result.created_at,
                actor_id=options.actor_id,
                message="Migrated legacy extraction result into a domain envelope",
                source_records=source_records,
                details={"legacy_extraction_result": object_payload},
            ),
            _history_event(
                event_id=f"legacy-extraction-result-object:{extraction_result.id}",
                event_type=HistoryEventKind.OBJECT_EXTRACTED,
                timestamp=extraction_result.created_at,
                actor_id=options.actor_id,
                message="Imported legacy extraction result payload as an envelope object",
                source_records=source_records,
                object_ref=ObjectRef(object_id=object_id, object_type=LEGACY_EXTRACTION_OBJECT_TYPE),
            ),
        ],
        metadata={
            "legacy_migration": _migration_metadata(source_records),
            "legacy_extraction_result": object_payload,
        },
    )


def _candidate_object(
    candidate: CurationCandidate,
    *,
    session_row: CurationReviewSession,
) -> CuratableObjectEnvelope:
    source_records = _candidate_source_refs(candidate)
    object_id = _candidate_object_id(candidate.id)
    payload = _candidate_payload(candidate, session_row=session_row)
    projections: list[dict[str, Any]] = [
        {
            "projection_type": LEGACY_WORKSPACE_PROJECTION_TYPE,
            "projection_key": str(candidate.id),
            "projection_status": candidate.status.value,
            "projection_json": {
                "candidate_id": str(candidate.id),
                "session_id": str(candidate.session_id),
                "adapter_key": candidate.adapter_key,
                "status": candidate.status.value,
                "display_label": candidate.display_label,
                "secondary_label": candidate.secondary_label,
                "source_records": [source.to_json() for source in source_records],
            },
        }
    ]
    if candidate.draft is not None:
        projections.append(
            {
                "projection_type": LEGACY_DRAFT_PROJECTION_TYPE,
                "projection_key": str(candidate.draft.id),
                "projection_status": candidate.status.value,
                "projection_json": {
                    "draft_id": str(candidate.draft.id),
                    "candidate_id": str(candidate.id),
                    "title": candidate.draft.title,
                    "summary": candidate.draft.summary,
                    "fields": _json_safe(candidate.draft.fields or []),
                },
            }
        )

    return CuratableObjectEnvelope(
        object_type=LEGACY_CANDIDATE_OBJECT_TYPE,
        object_id=object_id,
        status=_object_status_for_candidate(candidate),
        payload=payload,
        metadata={
            "legacy_migration": _migration_metadata(source_records),
            "projections": projections,
        },
    )


def _session_history(
    session_row: CurationReviewSession,
    *,
    options: LegacyCurationWorkspaceMigrationOptions,
) -> list[HistoryEvent]:
    events = [
        _history_event(
            event_id=f"legacy-session-created:{session_row.id}",
            event_type=HistoryEventKind.CREATED,
            timestamp=session_row.prepared_at,
            actor_id=options.actor_id,
            message="Migrated legacy curation review session into a domain envelope",
            source_records=_session_source_refs(session_row),
            details={"legacy_session": _session_payload(session_row)},
        )
    ]

    for candidate in sorted(session_row.candidates, key=lambda item: (item.order, str(item.id))):
        object_ref = ObjectRef(
            object_id=_candidate_object_id(candidate.id),
            object_type=LEGACY_CANDIDATE_OBJECT_TYPE,
        )
        events.append(
            _history_event(
                event_id=f"legacy-candidate-imported:{candidate.id}",
                event_type=HistoryEventKind.OBJECT_EXTRACTED,
                timestamp=candidate.created_at,
                actor_id=options.actor_id,
                message="Imported legacy curation candidate as an envelope object",
                source_records=_candidate_source_refs(candidate),
                object_ref=object_ref,
            )
        )
        if candidate.draft is not None:
            events.append(
                _history_event(
                    event_id=f"legacy-draft-imported:{candidate.draft.id}",
                    event_type=HistoryEventKind.OBJECT_UPDATED,
                    timestamp=candidate.draft.updated_at,
                    actor_id=options.actor_id,
                    message="Imported legacy annotation draft into envelope payload",
                    source_records=[_source_ref(SOURCE_TABLE_DRAFTS, candidate.draft.id)],
                    object_ref=object_ref,
                    details={"legacy_draft": _draft_payload(candidate.draft)},
                )
            )
        for evidence_record in sorted(candidate.evidence_anchors, key=_evidence_sort_key):
            events.append(
                _history_event(
                    event_id=f"legacy-evidence-imported:{evidence_record.id}",
                    event_type=HistoryEventKind.OBJECT_UPDATED,
                    timestamp=evidence_record.updated_at,
                    actor_id=options.actor_id,
                    message="Imported legacy evidence anchor into envelope payload",
                    source_records=[_source_ref(SOURCE_TABLE_EVIDENCE, evidence_record.id)],
                    object_ref=object_ref,
                    details={"legacy_evidence_anchor": _evidence_payload(evidence_record)},
                )
            )
        for snapshot in sorted(candidate.validation_snapshots, key=_validation_snapshot_sort_key):
            events.append(
                _history_event(
                    event_id=f"legacy-validation-imported:{snapshot.id}",
                    event_type=HistoryEventKind.VALIDATION_FINDING_ADDED,
                    timestamp=snapshot.completed_at or snapshot.requested_at,
                    actor_id=options.actor_id,
                    message="Imported legacy candidate validation snapshot",
                    source_records=[_source_ref(SOURCE_TABLE_VALIDATION, snapshot.id)],
                    object_ref=object_ref,
                    details={"legacy_validation_snapshot": _validation_snapshot_payload(snapshot)},
                )
            )

    for snapshot in sorted(session_row.validation_snapshots, key=_validation_snapshot_sort_key):
        events.append(
            _history_event(
                event_id=f"legacy-session-validation-imported:{snapshot.id}",
                event_type=HistoryEventKind.VALIDATION_FINDING_ADDED,
                timestamp=snapshot.completed_at or snapshot.requested_at,
                actor_id=options.actor_id,
                message="Imported legacy session validation snapshot",
                source_records=[_source_ref(SOURCE_TABLE_VALIDATION, snapshot.id)],
                details={"legacy_validation_snapshot": _validation_snapshot_payload(snapshot)},
            )
        )

    for submission in sorted(session_row.submissions, key=_submission_sort_key):
        events.append(
            _history_event(
                event_id=f"legacy-submission-imported:{submission.id}",
                event_type=HistoryEventKind.SUBMITTED,
                timestamp=submission.completed_at or submission.requested_at,
                actor_id=options.actor_id,
                message="Imported legacy curation submission state",
                source_records=[_source_ref(SOURCE_TABLE_SUBMISSIONS, submission.id)],
                details={"legacy_submission": _submission_payload(submission)},
            )
        )

    for entry in sorted(session_row.action_log_entries, key=_action_log_sort_key):
        object_ref = (
            ObjectRef(
                object_id=_candidate_object_id(entry.candidate_id),
                object_type=LEGACY_CANDIDATE_OBJECT_TYPE,
            )
            if entry.candidate_id is not None
            else None
        )
        events.append(
            _history_event(
                event_id=f"legacy-action-log-imported:{entry.id}",
                event_type=HistoryEventKind.STATUS_CHANGED,
                timestamp=entry.occurred_at,
                actor_id=options.actor_id,
                message="Imported legacy curation action log entry",
                source_records=[_source_ref(SOURCE_TABLE_ACTION_LOG, entry.id)],
                object_ref=object_ref,
                details={"legacy_action_log_entry": _action_log_payload(entry)},
            )
        )

    return events


def _session_validation_findings(
    session_row: CurationReviewSession,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for candidate in session_row.candidates:
        object_ref = ObjectRef(
            object_id=_candidate_object_id(candidate.id),
            object_type=LEGACY_CANDIDATE_OBJECT_TYPE,
        )
        for snapshot in candidate.validation_snapshots:
            findings.extend(
                _validation_findings_for_snapshot(
                    snapshot,
                    object_ref=object_ref,
                )
            )

    for snapshot in session_row.validation_snapshots:
        findings.extend(_validation_findings_for_snapshot(snapshot, object_ref=None))
    return findings


def _validation_findings_for_snapshot(
    snapshot: CurationValidationSnapshot,
    *,
    object_ref: ObjectRef | None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    field_results = snapshot.field_results if isinstance(snapshot.field_results, Mapping) else {}
    for field_key, result_payload in field_results.items():
        result_status = _field_validation_status(result_payload)
        if result_status in (
            FieldValidationStatus.VALIDATED,
            FieldValidationStatus.OVERRIDDEN,
            FieldValidationStatus.SKIPPED,
            None,
        ):
            continue
        findings.append(
            ValidationFinding(
                finding_id=f"legacy-validation:{snapshot.id}:{field_key}",
                severity=_severity_for_field_status(result_status),
                status=ValidationFindingStatus.OPEN,
                code=f"legacy_validation.{result_status.value}",
                object_ref=object_ref,
                message=f"Legacy validation result for {field_key} was {result_status.value}.",
                details={
                    "field_key": str(field_key),
                    "field_result": _json_safe(result_payload),
                    "legacy_migration": _migration_metadata(
                        [_source_ref(SOURCE_TABLE_VALIDATION, snapshot.id)]
                    ),
                },
            )
        )

    if snapshot.state in (
        CurationValidationSnapshotState.FAILED,
        CurationValidationSnapshotState.STALE,
    ):
        findings.append(
            ValidationFinding(
                finding_id=f"legacy-validation-state:{snapshot.id}",
                severity=ValidationFindingSeverity.ERROR,
                status=ValidationFindingStatus.OPEN,
                code=f"legacy_validation_snapshot.{snapshot.state.value}",
                object_ref=object_ref,
                message=f"Legacy validation snapshot was {snapshot.state.value}.",
                details={
                    "legacy_validation_snapshot": _validation_snapshot_payload(snapshot),
                    "legacy_migration": _migration_metadata(
                        [_source_ref(SOURCE_TABLE_VALIDATION, snapshot.id)]
                    ),
                },
            )
        )

    for index, warning in enumerate(snapshot.warnings or []):
        findings.append(
            ValidationFinding(
                finding_id=f"legacy-validation-warning:{snapshot.id}:{index}",
                severity=ValidationFindingSeverity.WARNING,
                status=ValidationFindingStatus.OPEN,
                code="legacy_validation_snapshot.warning",
                object_ref=object_ref,
                message=str(warning),
                details={
                    "legacy_migration": _migration_metadata(
                        [_source_ref(SOURCE_TABLE_VALIDATION, snapshot.id)]
                    )
                },
            )
        )
    return findings


def _field_validation_status(value: Any) -> FieldValidationStatus | None:
    if isinstance(value, Mapping):
        raw_status = value.get("status")
    else:
        raw_status = getattr(value, "status", None)
    if isinstance(raw_status, FieldValidationStatus):
        return raw_status
    if isinstance(raw_status, str):
        try:
            return FieldValidationStatus(raw_status)
        except ValueError:
            return None
    return None


def _severity_for_field_status(status: FieldValidationStatus) -> ValidationFindingSeverity:
    if status in (FieldValidationStatus.AMBIGUOUS, FieldValidationStatus.CONFLICT):
        return ValidationFindingSeverity.WARNING
    if status in (
        FieldValidationStatus.NOT_FOUND,
        FieldValidationStatus.INVALID_FORMAT,
    ):
        return ValidationFindingSeverity.ERROR
    return ValidationFindingSeverity.INFO


def _link_session_candidates(
    db: Session,
    session_row: CurationReviewSession,
    *,
    envelope_id: str,
    envelope_revision: int,
) -> int:
    linked_count = 0
    candidates = db.scalars(
        select(CurationCandidate).where(CurationCandidate.session_id == session_row.id)
    ).all()
    for candidate in candidates:
        expected_object_id = _candidate_object_id(candidate.id)
        if (
            candidate.envelope_id == envelope_id
            and candidate.object_id == expected_object_id
            and candidate.envelope_revision == envelope_revision
        ):
            continue
        candidate.envelope_id = envelope_id
        candidate.object_id = expected_object_id
        candidate.envelope_revision = envelope_revision
        linked_count += 1
    if linked_count:
        db.commit()
    return linked_count


def _current_envelope_revision(db: Session, envelope_id: str) -> int:
    envelope_row = db.get(DomainEnvelopeModel, envelope_id)
    if envelope_row is None:
        raise RuntimeError(f"Migrated envelope {envelope_id} is missing")
    return envelope_row.revision


def _envelope_status_for_session(session_row: CurationReviewSession) -> DomainEnvelopeStatus:
    latest_submission = _latest_submission(session_row.submissions)
    if latest_submission is not None:
        if latest_submission.status is CurationSubmissionStatus.ACCEPTED:
            return DomainEnvelopeStatus.SUBMITTED
        if latest_submission.status in (
            CurationSubmissionStatus.EXPORT_READY,
            CurationSubmissionStatus.PREVIEW_READY,
        ):
            return DomainEnvelopeStatus.READY_FOR_EXPORT
        if latest_submission.status in (
            CurationSubmissionStatus.FAILED,
            CurationSubmissionStatus.VALIDATION_ERRORS,
            CurationSubmissionStatus.CONFLICT,
        ):
            return DomainEnvelopeStatus.FAILED

    if session_row.status is CurationSessionStatus.SUBMITTED:
        return DomainEnvelopeStatus.SUBMITTED
    if session_row.status is CurationSessionStatus.READY_FOR_SUBMISSION:
        return DomainEnvelopeStatus.READY_FOR_EXPORT
    if session_row.status is CurationSessionStatus.REJECTED:
        return DomainEnvelopeStatus.FAILED
    return DomainEnvelopeStatus.EXTRACTED


def _object_status_for_candidate(candidate: CurationCandidate) -> CuratableObjectStatus:
    if candidate.status is CurationCandidateStatus.ACCEPTED:
        return CuratableObjectStatus.READY_FOR_EXPORT
    if candidate.status is CurationCandidateStatus.REJECTED:
        return CuratableObjectStatus.REJECTED
    return CuratableObjectStatus.NEEDS_REVIEW


def _latest_submission(
    submissions: Sequence[CurationSubmissionRecord],
) -> CurationSubmissionRecord | None:
    if not submissions:
        return None
    return max(submissions, key=_submission_sort_key)


def _session_payload(session_row: CurationReviewSession) -> dict[str, Any]:
    return _json_safe(
        {
            "session_id": session_row.id,
            "status": session_row.status,
            "adapter_key": session_row.adapter_key,
            "profile_key": session_row.profile_key,
            "document_id": session_row.document_id,
            "flow_run_id": session_row.flow_run_id,
            "current_candidate_id": session_row.current_candidate_id,
            "assigned_curator_id": session_row.assigned_curator_id,
            "created_by_id": session_row.created_by_id,
            "session_version": session_row.session_version,
            "notes": session_row.notes,
            "tags": session_row.tags,
            "total_candidates": session_row.total_candidates,
            "reviewed_candidates": session_row.reviewed_candidates,
            "pending_candidates": session_row.pending_candidates,
            "accepted_candidates": session_row.accepted_candidates,
            "rejected_candidates": session_row.rejected_candidates,
            "manual_candidates": session_row.manual_candidates,
            "rejection_reason": session_row.rejection_reason,
            "warnings": session_row.warnings,
            "prepared_at": session_row.prepared_at,
            "last_worked_at": session_row.last_worked_at,
            "submitted_at": session_row.submitted_at,
            "paused_at": session_row.paused_at,
            "created_at": session_row.created_at,
            "updated_at": session_row.updated_at,
        }
    )


def _candidate_payload(
    candidate: CurationCandidate,
    *,
    session_row: CurationReviewSession,
) -> dict[str, Any]:
    return _json_safe(
        {
            "session_id": session_row.id,
            "candidate_id": candidate.id,
            "source": candidate.source,
            "status": candidate.status,
            "order": candidate.order,
            "adapter_key": candidate.adapter_key,
            "profile_key": candidate.profile_key,
            "display_label": candidate.display_label,
            "secondary_label": candidate.secondary_label,
            "conversation_summary": candidate.conversation_summary,
            "extraction_result_id": candidate.extraction_result_id,
            "legacy_projection_ref": {
                "envelope_id": candidate.envelope_id,
                "object_id": candidate.object_id,
                "envelope_revision": candidate.envelope_revision,
            },
            "normalized_payload": candidate.normalized_payload or {},
            "metadata": candidate.candidate_metadata or {},
            "draft": _draft_payload(candidate.draft) if candidate.draft is not None else None,
            "evidence_anchors": [
                _evidence_payload(evidence_record)
                for evidence_record in sorted(candidate.evidence_anchors, key=_evidence_sort_key)
            ],
            "validation_snapshots": [
                _validation_snapshot_payload(snapshot)
                for snapshot in sorted(
                    candidate.validation_snapshots,
                    key=_validation_snapshot_sort_key,
                )
            ],
            "action_log": [
                _action_log_payload(entry)
                for entry in sorted(candidate.action_log_entries, key=_action_log_sort_key)
            ],
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
            "last_reviewed_at": candidate.last_reviewed_at,
        }
    )


def _draft_payload(draft: CurationDraft) -> dict[str, Any]:
    return _json_safe(
        {
            "draft_id": draft.id,
            "candidate_id": draft.candidate_id,
            "adapter_key": draft.adapter_key,
            "version": draft.version,
            "title": draft.title,
            "summary": draft.summary,
            "fields": draft.fields,
            "notes": draft.notes,
            "metadata": draft.draft_metadata,
            "created_at": draft.created_at,
            "updated_at": draft.updated_at,
            "last_saved_at": draft.last_saved_at,
        }
    )


def _evidence_payload(evidence_record: CurationEvidenceRecord) -> dict[str, Any]:
    return _json_safe(
        {
            "evidence_anchor_id": evidence_record.id,
            "candidate_id": evidence_record.candidate_id,
            "source": evidence_record.source,
            "field_keys": evidence_record.field_keys,
            "field_group_keys": evidence_record.field_group_keys,
            "is_primary": evidence_record.is_primary,
            "anchor": evidence_record.anchor,
            "warnings": evidence_record.warnings,
            "created_at": evidence_record.created_at,
            "updated_at": evidence_record.updated_at,
        }
    )


def _validation_snapshot_payload(snapshot: CurationValidationSnapshot) -> dict[str, Any]:
    return _json_safe(
        {
            "validation_snapshot_id": snapshot.id,
            "scope": snapshot.scope,
            "session_id": snapshot.session_id,
            "candidate_id": snapshot.candidate_id,
            "adapter_key": snapshot.adapter_key,
            "state": snapshot.state,
            "field_results": snapshot.field_results,
            "summary": snapshot.summary,
            "warnings": snapshot.warnings,
            "requested_at": snapshot.requested_at,
            "completed_at": snapshot.completed_at,
        }
    )


def _submission_payload(submission: CurationSubmissionRecord) -> dict[str, Any]:
    return _json_safe(
        {
            "submission_id": submission.id,
            "session_id": submission.session_id,
            "adapter_key": submission.adapter_key,
            "mode": submission.mode,
            "target_key": submission.target_key,
            "status": submission.status,
            "readiness": submission.readiness,
            "payload": submission.payload,
            "external_reference": submission.external_reference,
            "response_message": submission.response_message,
            "validation_errors": submission.validation_errors,
            "warnings": submission.warnings,
            "requested_at": submission.requested_at,
            "completed_at": submission.completed_at,
        }
    )


def _action_log_payload(entry: CurationActionLogEntry) -> dict[str, Any]:
    return _json_safe(
        {
            "action_log_id": entry.id,
            "session_id": entry.session_id,
            "candidate_id": entry.candidate_id,
            "draft_id": entry.draft_id,
            "action_type": entry.action_type,
            "actor_type": entry.actor_type,
            "actor": entry.actor,
            "occurred_at": entry.occurred_at,
            "previous_session_status": entry.previous_session_status,
            "new_session_status": entry.new_session_status,
            "previous_candidate_status": entry.previous_candidate_status,
            "new_candidate_status": entry.new_candidate_status,
            "changed_field_keys": entry.changed_field_keys,
            "evidence_anchor_ids": entry.evidence_anchor_ids,
            "reason": entry.reason,
            "message": entry.message,
            "metadata": entry.action_metadata,
        }
    )


def _extraction_result_payload(
    extraction_result: CurationExtractionResultRecord,
) -> dict[str, Any]:
    return _json_safe(
        {
            "extraction_result_id": extraction_result.id,
            "document_id": extraction_result.document_id,
            "adapter_key": extraction_result.adapter_key,
            "agent_key": extraction_result.agent_key,
            "source_kind": extraction_result.source_kind,
            "origin_session_id": extraction_result.origin_session_id,
            "trace_id": extraction_result.trace_id,
            "flow_run_id": extraction_result.flow_run_id,
            "user_id": extraction_result.user_id,
            "candidate_count": extraction_result.candidate_count,
            "conversation_summary": extraction_result.conversation_summary,
            "payload_json": extraction_result.payload_json,
            "metadata": extraction_result.extraction_metadata,
            "created_at": extraction_result.created_at,
        }
    )


def _session_extraction_results(
    session_row: CurationReviewSession,
) -> list[CurationExtractionResultRecord]:
    by_id: dict[str, CurationExtractionResultRecord] = {}
    for candidate in session_row.candidates:
        if candidate.extraction_result is not None:
            by_id[str(candidate.extraction_result.id)] = candidate.extraction_result
    return sorted(by_id.values(), key=lambda item: (item.created_at, str(item.id)))


def _session_source_refs(session_row: CurationReviewSession) -> list[LegacySourceRef]:
    refs = [_source_ref(SOURCE_TABLE_REVIEW_SESSIONS, session_row.id)]
    for extraction_result in _session_extraction_results(session_row):
        refs.append(_source_ref(SOURCE_TABLE_EXTRACTION_RESULTS, extraction_result.id))
    for candidate in session_row.candidates:
        refs.extend(_candidate_source_refs(candidate))
    for snapshot in session_row.validation_snapshots:
        refs.append(_source_ref(SOURCE_TABLE_VALIDATION, snapshot.id))
    for submission in session_row.submissions:
        refs.append(_source_ref(SOURCE_TABLE_SUBMISSIONS, submission.id))
    for entry in session_row.action_log_entries:
        refs.append(_source_ref(SOURCE_TABLE_ACTION_LOG, entry.id))
    return _dedupe_source_refs(refs)


def _candidate_source_refs(candidate: CurationCandidate) -> list[LegacySourceRef]:
    refs = [_source_ref(SOURCE_TABLE_CANDIDATES, candidate.id)]
    if candidate.draft is not None:
        refs.append(_source_ref(SOURCE_TABLE_DRAFTS, candidate.draft.id))
    if candidate.extraction_result_id is not None:
        refs.append(_source_ref(SOURCE_TABLE_EXTRACTION_RESULTS, candidate.extraction_result_id))
    for evidence_record in candidate.evidence_anchors:
        refs.append(_source_ref(SOURCE_TABLE_EVIDENCE, evidence_record.id))
    for snapshot in candidate.validation_snapshots:
        refs.append(_source_ref(SOURCE_TABLE_VALIDATION, snapshot.id))
    for entry in candidate.action_log_entries:
        refs.append(_source_ref(SOURCE_TABLE_ACTION_LOG, entry.id))
    return _dedupe_source_refs(refs)


def _dedupe_source_refs(refs: Iterable[LegacySourceRef]) -> list[LegacySourceRef]:
    return sorted(set(refs), key=lambda item: (item.table_name, item.row_id))


def _history_event(
    *,
    event_id: str,
    event_type: HistoryEventKind,
    timestamp: datetime | None,
    actor_id: str,
    message: str,
    source_records: Sequence[LegacySourceRef],
    object_ref: ObjectRef | None = None,
    details: Mapping[str, Any] | None = None,
) -> HistoryEvent:
    event_details = dict(details or {})
    event_details["legacy_migration"] = _migration_metadata(source_records)
    return HistoryEvent(
        event_id=event_id,
        event_type=event_type,
        timestamp=_timestamp(timestamp),
        actor_type=HistoryActorType.SYSTEM,
        actor_id=actor_id,
        message=message,
        object_ref=object_ref,
        details=_json_safe(event_details),
    )


def _migration_metadata(source_records: Sequence[LegacySourceRef]) -> dict[str, Any]:
    return {
        "name": MIGRATION_NAME,
        "version": MIGRATION_VERSION,
        "source_records": [source.to_json() for source in _dedupe_source_refs(source_records)],
    }


def _legacy_model_ref_json() -> dict[str, Any]:
    return {
        "legacy_migration": {
            "name": MIGRATION_NAME,
            "domain_pack_id": DEFAULT_LEGACY_DOMAIN_PACK_ID,
        }
    }


def _legacy_field_ref_json() -> dict[str, Any]:
    return {
        "legacy_migration": {
            "field_semantics": "legacy workspace payload paths are preserved as source data"
        }
    }


def _source_ref(table_name: str, row_id: str | UUID) -> LegacySourceRef:
    return LegacySourceRef(table_name=table_name, row_id=str(row_id))


def _session_envelope_id(session_id: str | UUID) -> str:
    return f"legacy-curation-workspace:session:{session_id}"


def _extraction_envelope_id(extraction_result_id: str | UUID) -> str:
    return f"legacy-curation-workspace:extraction-result:{extraction_result_id}"


def _candidate_object_id(candidate_id: str | UUID) -> str:
    return f"legacy-curation-candidate:{candidate_id}"


def _extraction_object_id(extraction_result_id: str | UUID) -> str:
    return f"legacy-extraction-result:{extraction_result_id}"


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _evidence_sort_key(record: CurationEvidenceRecord) -> tuple[datetime, str]:
    return (_timestamp(record.created_at), str(record.id))


def _validation_snapshot_sort_key(snapshot: CurationValidationSnapshot) -> tuple[datetime, str]:
    return (_timestamp(snapshot.requested_at or snapshot.completed_at), str(snapshot.id))


def _submission_sort_key(submission: CurationSubmissionRecord) -> tuple[datetime, str]:
    return (_timestamp(submission.requested_at), str(submission.id))


def _action_log_sort_key(entry: CurationActionLogEntry) -> tuple[datetime, str]:
    return (_timestamp(entry.occurred_at), str(entry.id))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _timestamp(value).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=str)]
    return value


__all__ = [
    "DEFAULT_LEGACY_DOMAIN_PACK_ID",
    "DEFAULT_LEGACY_DOMAIN_PACK_VERSION",
    "DEFAULT_PROJECT_KEY",
    "LEGACY_CANDIDATE_OBJECT_TYPE",
    "LEGACY_EXTRACTION_OBJECT_TYPE",
    "LEGACY_WORKSPACE_PROJECTION_TYPE",
    "LegacyCurationWorkspaceMigrationOptions",
    "LegacyCurationWorkspaceMigrationSummary",
    "LegacyMigrationBlocker",
    "LegacySourceRef",
    "MIGRATION_NAME",
    "migrate_legacy_curation_workspace_to_domain_envelopes",
]
