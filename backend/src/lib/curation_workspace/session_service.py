"""Compatibility facade for curation workspace session services."""

from __future__ import annotations

from importlib import import_module as _import_module

_IMPLEMENTATION_MODULES = (
    "session_types",
    "session_loading",
    "session_common",
    "session_serializers",
    "session_queries",
    "session_persistence",
    "prepared_session_service",
    "session_validation_service",
    "session_submission_service",
    "session_mutation_service",
)

for _module_name in _IMPLEMENTATION_MODULES:
    _module = _import_module(f"{__package__}.{_module_name}")
    for _name in dir(_module):
        if _name.startswith("__"):
            continue
        globals()[_name] = getattr(_module, _name)

del _import_module, _module, _module_name

__all__ = [
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
