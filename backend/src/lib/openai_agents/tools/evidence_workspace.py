"""Active-run evidence workspace tools for extraction agents."""

from __future__ import annotations

from contextvars import ContextVar, Token
from copy import deepcopy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from agents import function_tool

if TYPE_CHECKING:
    from ..guardrails import ToolCallTracker


_ACTIVE_EVIDENCE_RECORDS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "active_evidence_records",
    default=None,
)


def set_active_evidence_records(
    records: list[dict[str, Any]] | None,
) -> Token[list[dict[str, Any]] | None]:
    """Bind a mutable evidence registry to the current agent run."""

    return _ACTIVE_EVIDENCE_RECORDS.set(records)


def reset_active_evidence_records(
    token: Token[list[dict[str, Any]] | None],
) -> None:
    """Restore the previous active evidence registry binding."""

    _ACTIVE_EVIDENCE_RECORDS.reset(token)


def _workspace_records() -> list[dict[str, Any]]:
    records = _ACTIVE_EVIDENCE_RECORDS.get()
    if records is None:
        raise RuntimeError("No active evidence workspace is bound to this run")
    return records


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _record_status(record: dict[str, Any]) -> str:
    status = _optional_string(record.get("workspace_status") or record.get("status"))
    return status.lower() if status else "active"


def _is_discarded(record: dict[str, Any]) -> bool:
    return _record_status(record) == "discarded"


def _matches_document(record: dict[str, Any], document_id: str) -> bool:
    record_document_id = _optional_string(record.get("document_id"))
    return record_document_id in (None, document_id)


def _normalize_target(
    *,
    object_id: Any = None,
    pending_ref_id: Any = None,
    field_path: Any = None,
) -> dict[str, str]:
    target: dict[str, str] = {}
    normalized_object_id = _optional_string(object_id)
    normalized_pending_ref_id = _optional_string(pending_ref_id)
    normalized_field_path = _optional_string(field_path)

    if normalized_object_id:
        target["object_id"] = normalized_object_id
    elif normalized_pending_ref_id:
        target["pending_ref_id"] = normalized_pending_ref_id
    if normalized_field_path:
        target["field_path"] = normalized_field_path
    return target


def _record_targets(record: dict[str, Any]) -> list[dict[str, str]]:
    raw_targets = record.get("envelope_targets")
    targets: list[dict[str, str]] = []
    if isinstance(raw_targets, list):
        for raw_target in raw_targets:
            if isinstance(raw_target, dict):
                target = _normalize_target(
                    object_id=raw_target.get("object_id"),
                    pending_ref_id=raw_target.get("pending_ref_id"),
                    field_path=raw_target.get("field_path"),
                )
                if target and target not in targets:
                    targets.append(target)

    raw_target = record.get("envelope_target")
    if isinstance(raw_target, dict):
        target = _normalize_target(
            object_id=raw_target.get("object_id"),
            pending_ref_id=raw_target.get("pending_ref_id"),
            field_path=raw_target.get("field_path"),
        )
        if target and target not in targets:
            targets.append(target)

    target = _normalize_target(
        object_id=record.get("object_id"),
        pending_ref_id=record.get("pending_ref_id"),
        field_path=record.get("field_path"),
    )
    if (target.get("object_id") or target.get("pending_ref_id")) and target not in targets:
        targets.append(target)
    return targets


def _sync_target_fields(record: dict[str, Any], targets: list[dict[str, str]]) -> None:
    for key in ("object_id", "pending_ref_id", "object_ref", "envelope_target"):
        record.pop(key, None)

    if targets:
        record["envelope_targets"] = [dict(target) for target in targets]
        first_target = targets[0]
        if first_target.get("object_id"):
            record["object_id"] = first_target["object_id"]
        if first_target.get("pending_ref_id"):
            record["pending_ref_id"] = first_target["pending_ref_id"]
        record["object_ref"] = {
            key: first_target[key]
            for key in ("object_id", "pending_ref_id")
            if first_target.get(key)
        }
        record["envelope_target"] = dict(first_target)
    else:
        record.pop("envelope_targets", None)

    field_paths = []
    seen = set()
    for target in targets:
        field_path = target.get("field_path")
        if field_path and field_path not in seen:
            seen.add(field_path)
            field_paths.append(field_path)
    existing_field_path = _optional_string(record.get("field_path"))
    if existing_field_path and existing_field_path not in seen:
        field_paths.append(existing_field_path)
        seen.add(existing_field_path)

    if field_paths:
        record["field_path"] = field_paths[0]
        record["field_paths"] = field_paths
    else:
        record.pop("field_path", None)
        record.pop("field_paths", None)


def _target_matches(existing: dict[str, str], requested: dict[str, str]) -> bool:
    if not requested:
        return True
    return all(existing.get(key) == value for key, value in requested.items())


def _find_record(
    evidence_record_id: str,
    *,
    document_id: str,
) -> dict[str, Any] | None:
    normalized_id = _optional_string(evidence_record_id)
    if not normalized_id:
        return None

    for record in _workspace_records():
        if (
            _optional_string(record.get("evidence_record_id")) == normalized_id
            and _matches_document(record, document_id)
        ):
            return record
    return None


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "evidence_record_id": record.get("evidence_record_id"),
        "status": _record_status(record),
        "entity": record.get("entity"),
        "verified_quote": record.get("verified_quote"),
        "page": record.get("page"),
        "section": record.get("section"),
        "chunk_id": record.get("chunk_id"),
        "source_span_count": len(record.get("source_span_ids") or []),
        "envelope_targets": _record_targets(record),
    }
    for key in (
        "subsection",
        "figure_reference",
        "document_id",
        "chunk_ids",
        "field_path",
        "field_paths",
        "agent_note",
        "discard_reason",
        "discarded_at",
    ):
        if record.get(key) not in (None, "", []):
            summary[key] = deepcopy(record[key])
    return summary


def _success(record: dict[str, Any], *, action: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "action": action,
        "record": _record_summary(record),
    }


def _not_found(evidence_record_id: str) -> dict[str, Any]:
    return {
        "status": "not_found",
        "evidence_record_id": evidence_record_id,
        "message": "Evidence record was not found in the active run workspace.",
    }


def _discarded_error(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "discarded",
        "evidence_record_id": record.get("evidence_record_id"),
        "message": "Discarded evidence cannot be attached, detached, or edited.",
        "record": _record_summary(record),
    }


def _track(tracker: Optional["ToolCallTracker"], tool_name: str) -> None:
    if tracker:
        tracker.record_call(tool_name)


def create_list_recorded_evidence_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a tool for listing evidence recorded during the active run."""

    @function_tool
    async def list_recorded_evidence(
        include_discarded: bool = False,
        object_id: str | None = None,
        pending_ref_id: str | None = None,
    ) -> dict[str, Any]:
        """List active-run evidence records, optionally filtered by object or pending ref."""

        _track(tracker, "list_recorded_evidence")
        requested = _normalize_target(object_id=object_id, pending_ref_id=pending_ref_id)
        records = []
        for record in _workspace_records():
            if not _matches_document(record, document_id):
                continue
            if _is_discarded(record) and not include_discarded:
                continue
            if requested and not any(
                _target_matches(target, requested) for target in _record_targets(record)
            ):
                continue
            records.append(_record_summary(record))

        return {
            "status": "ok",
            "document_id": document_id,
            "include_discarded": include_discarded,
            "count": len(records),
            "evidence_records": records,
        }

    return list_recorded_evidence


def create_get_recorded_evidence_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a tool for fetching one active-run evidence record."""

    @function_tool
    async def get_recorded_evidence(evidence_record_id: str) -> dict[str, Any]:
        """Fetch one active-run evidence record by evidence_record_id."""

        _track(tracker, "get_recorded_evidence")
        record = _find_record(evidence_record_id, document_id=document_id)
        if record is None:
            return _not_found(evidence_record_id)
        return {
            "status": "ok",
            "record": deepcopy(record),
        }

    return get_recorded_evidence


def create_attach_evidence_to_object_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a tool for attaching evidence to an object or pending ref."""

    @function_tool
    async def attach_evidence_to_object(
        evidence_record_id: str,
        object_id: str | None = None,
        pending_ref_id: str | None = None,
        field_path: str | None = None,
    ) -> dict[str, Any]:
        """Attach one evidence record to an intended object or pending ref."""

        _track(tracker, "attach_evidence_to_object")
        record = _find_record(evidence_record_id, document_id=document_id)
        if record is None:
            return _not_found(evidence_record_id)
        if _is_discarded(record):
            return _discarded_error(record)

        target = _normalize_target(
            object_id=object_id,
            pending_ref_id=pending_ref_id,
            field_path=field_path,
        )
        if not target:
            return {
                "status": "invalid_request",
                "message": "Provide object_id or pending_ref_id, optionally with field_path.",
            }

        targets = _record_targets(record)
        if target not in targets:
            targets.append(target)
        _sync_target_fields(record, targets)
        record["updated_at"] = _now_iso()
        return _success(record, action="attach")

    return attach_evidence_to_object


def create_detach_evidence_from_object_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a tool for detaching evidence from an object or pending ref."""

    @function_tool
    async def detach_evidence_from_object(
        evidence_record_id: str,
        object_id: str | None = None,
        pending_ref_id: str | None = None,
        field_path: str | None = None,
    ) -> dict[str, Any]:
        """Detach one evidence record from an object or pending ref."""

        _track(tracker, "detach_evidence_from_object")
        record = _find_record(evidence_record_id, document_id=document_id)
        if record is None:
            return _not_found(evidence_record_id)
        if _is_discarded(record):
            return _discarded_error(record)

        requested = _normalize_target(
            object_id=object_id,
            pending_ref_id=pending_ref_id,
            field_path=field_path,
        )
        targets = [
            target
            for target in _record_targets(record)
            if not _target_matches(target, requested)
        ]
        _sync_target_fields(record, targets)
        record["updated_at"] = _now_iso()
        return _success(record, action="detach")

    return detach_evidence_from_object


def create_discard_recorded_evidence_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a tool for discarding weak or wrong evidence without deleting it."""

    @function_tool
    async def discard_recorded_evidence(
        evidence_record_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Discard one active-run evidence record while retaining audit history."""

        _track(tracker, "discard_recorded_evidence")
        record = _find_record(evidence_record_id, document_id=document_id)
        if record is None:
            return _not_found(evidence_record_id)

        normalized_reason = _optional_string(reason)
        if not normalized_reason:
            return {
                "status": "invalid_request",
                "message": "reason is required when discarding evidence.",
            }

        record["status"] = "discarded"
        record["workspace_status"] = "discarded"
        record["discard_reason"] = normalized_reason
        record["discarded_at"] = _now_iso()
        return _success(record, action="discard")

    return discard_recorded_evidence


def create_update_recorded_evidence_metadata_tool(
    document_id: str,
    user_id: str,
    tracker: Optional["ToolCallTracker"] = None,
):
    """Create a tool for editing agent-owned evidence metadata only."""

    @function_tool
    async def update_recorded_evidence_metadata(
        evidence_record_id: str,
        entity: str | None = None,
        field_path: str | None = None,
        agent_note: str | None = None,
    ) -> dict[str, Any]:
        """Update editable evidence metadata without changing source quote/provenance."""

        _track(tracker, "update_recorded_evidence_metadata")
        record = _find_record(evidence_record_id, document_id=document_id)
        if record is None:
            return _not_found(evidence_record_id)
        if _is_discarded(record):
            return _discarded_error(record)

        normalized_entity = _optional_string(entity)
        normalized_field_path = _optional_string(field_path)
        normalized_agent_note = _optional_string(agent_note)

        if normalized_entity is not None:
            record["entity"] = normalized_entity
        if normalized_field_path is not None:
            record["field_path"] = normalized_field_path
            field_paths = [normalized_field_path]
            for value in record.get("field_paths") or []:
                existing = _optional_string(value)
                if existing and existing not in field_paths:
                    field_paths.append(existing)
            record["field_paths"] = field_paths
            targets = _record_targets(record)
            if len(targets) == 1 and not targets[0].get("field_path"):
                targets[0]["field_path"] = normalized_field_path
                _sync_target_fields(record, targets)
        if normalized_agent_note is not None:
            record["agent_note"] = normalized_agent_note

        record["updated_at"] = _now_iso()
        return _success(record, action="update_metadata")

    return update_recorded_evidence_metadata


__all__ = [
    "create_attach_evidence_to_object_tool",
    "create_detach_evidence_from_object_tool",
    "create_discard_recorded_evidence_tool",
    "create_get_recorded_evidence_tool",
    "create_list_recorded_evidence_tool",
    "create_update_recorded_evidence_metadata_tool",
    "reset_active_evidence_records",
    "set_active_evidence_records",
]
