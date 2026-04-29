"""Thin re-export facade for curation workspace session services."""

from __future__ import annotations

from src.lib.curation_workspace.models import (
    CurationActionLogEntry as SessionActionLogModel,
)
from src.lib.curation_workspace.prepared_session_service import (
    find_reusable_prepared_session,
    upsert_prepared_session,
)
from src.lib.curation_workspace.session_common import (
    build_actor_claims_payload,
    normalize_uuid,
)
from src.lib.curation_workspace.session_mutation_service import (
    create_manual_candidate,
    decide_candidate,
    delete_candidate,
    update_candidate_draft,
    update_session,
)
from src.lib.curation_workspace.session_queries import (
    _list_session_summaries,
    get_candidate_detail,
    get_next_session,
    get_session_detail,
    get_session_stats,
    get_session_workspace,
    list_flow_run_sessions,
    list_flow_runs,
    list_sessions,
)
from src.lib.curation_workspace.session_serializers import (
    _serialize_submission_payload_contract,
    _submission_record,
    build_action_log_entry,
    build_evidence_record,
)
from src.lib.curation_workspace.session_submission_service import (
    SUBMISSION_TRANSPORT_FAILURE_MESSAGE,
    _build_submission_execute_payload,
    _resolve_submission_transport_adapter,
    _submission_adapter_registry,
    _submission_validation_blocking_reason,
    execute_submission,
    get_submission,
    retry_submission,
    submission_preview,
)
from src.lib.curation_workspace.session_types import (
    PreparedCandidateInput,
    PreparedDraftFieldInput,
    PreparedEvidenceRecordInput,
    PreparedSessionUpsertRequest,
    PreparedSessionUpsertResult,
    PreparedValidationSnapshotInput,
    ReusablePreparedSessionContext,
)
from src.lib.curation_workspace.session_validation_service import (
    CurationDraftFieldSchema,
    validate_candidate,
    validate_session,
)

__all__ = [
    "SUBMISSION_TRANSPORT_FAILURE_MESSAGE",
    "CurationDraftFieldSchema",
    "SessionActionLogModel",
    "_build_submission_execute_payload",
    "_list_session_summaries",
    "_resolve_submission_transport_adapter",
    "_serialize_submission_payload_contract",
    "_submission_adapter_registry",
    "_submission_record",
    "_submission_validation_blocking_reason",
    "build_action_log_entry",
    "build_actor_claims_payload",
    "build_evidence_record",
    "create_manual_candidate",
    "decide_candidate",
    "delete_candidate",
    "execute_submission",
    "get_next_session",
    "get_candidate_detail",
    "get_submission",
    "find_reusable_prepared_session",
    "get_session_detail",
    "get_session_workspace",
    "get_session_stats",
    "list_flow_run_sessions",
    "list_flow_runs",
    "list_sessions",
    "normalize_uuid",
    "PreparedCandidateInput",
    "PreparedDraftFieldInput",
    "PreparedEvidenceRecordInput",
    "PreparedSessionUpsertRequest",
    "PreparedSessionUpsertResult",
    "ReusablePreparedSessionContext",
    "PreparedValidationSnapshotInput",
    "retry_submission",
    "submission_preview",
    "upsert_prepared_session",
    "update_candidate_draft",
    "update_session",
    "validate_candidate",
    "validate_session",
]
