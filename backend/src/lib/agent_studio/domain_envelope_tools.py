"""Read-only domain-envelope inspection tools for Agent Studio Opus chat."""

from __future__ import annotations

from collections import Counter
import json
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationReviewSession,
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
    DomainEnvelopeObject,
    DomainEnvelopeProjectionIndex,
    DomainValidationFinding,
)
from src.lib.curation_workspace.session_common import _latest_snapshot_record
from src.lib.curation_workspace.session_serializers import _validation_snapshot
from src.lib.curation_workspace.session_submission_service import (
    _build_domain_envelope_submission_context,
    _candidate_submission_readiness,
)
from src.lib.curation_workspace.session_validation_service import _load_session_for_validation
from src.lib.domain_packs.materialization import (
    DomainEnvelopeMaterializationError,
    materialize_persisted_envelope_review_rows,
)
from src.lib.flows.validation_attachments import (
    domain_pack_validation_registries,
    validation_schedule_from_node_data,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    HistoryEvent,
    ObjectRef,
)


SessionFactory = Callable[[], Session]

_MAX_LIMIT = 50
_DEFAULT_LIMIT = 10
_MAX_JSON_CHARS = 20_000
_MAX_LOOKUP_ATTEMPTS = 25
_MAX_FIELD_PATHS = 150


def list_domain_envelopes(
    *,
    session_factory: SessionFactory,
    user_auth_sub: str,
    session_id: str | None = None,
    document_id: str | None = None,
    flow_run_id: str | None = None,
    domain_pack_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """List persisted domain envelopes visible to the current curator."""

    resolved_limit = _bounded_limit(limit)
    db = session_factory()
    try:
        if session_id is not None and not _session_visible_to_user(
            db,
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        ):
            return _error(f"Curation review session {session_id} was not found.")

        query = select(DomainEnvelopeModel).order_by(DomainEnvelopeModel.updated_at.desc())
        if session_id:
            normalized_session_id = _uuid(session_id, "session_id")
            session_candidate_envelopes = (
                select(CurationCandidate.envelope_id)
                .where(CurationCandidate.session_id == normalized_session_id)
                .where(CurationCandidate.envelope_id.is_not(None))
            )
            query = query.where(
                or_(
                    DomainEnvelopeModel.session_id == normalized_session_id,
                    DomainEnvelopeModel.envelope_id.in_(session_candidate_envelopes),
                )
            )
        if document_id:
            query = query.where(DomainEnvelopeModel.document_id == _uuid(document_id, "document_id"))
        if flow_run_id:
            query = query.where(DomainEnvelopeModel.flow_run_id == flow_run_id.strip())
        if domain_pack_id:
            query = query.where(DomainEnvelopeModel.domain_pack_key == domain_pack_id.strip())

        rows = [
            row
            for row in db.scalars(query.limit(resolved_limit * 3)).all()
            if _envelope_visible_to_user(db, row=row, user_auth_sub=user_auth_sub)
        ][:resolved_limit]

        return {
            "success": True,
            "count": len(rows),
            "limit": resolved_limit,
            "envelopes": [_envelope_row_summary(row) for row in rows],
            "instruction": (
                "Use these envelope_id values with get_domain_envelope_state for live "
                "object, finding, history, projection, and lookup details."
            ),
        }
    except ValueError as exc:
        return _error(str(exc))
    finally:
        db.close()


def get_domain_envelope_state(
    *,
    session_factory: SessionFactory,
    user_auth_sub: str,
    envelope_id: str,
    object_id: str | None = None,
    field_path: str | None = None,
    include_object_payload: bool = False,
    history_limit: int | None = None,
) -> dict[str, Any]:
    """Return current persisted envelope state and indexed references."""

    db = session_factory()
    try:
        normalized_envelope_id = _required_text(envelope_id, "envelope_id")
        row = db.get(DomainEnvelopeModel, normalized_envelope_id)
        if row is None or not _envelope_visible_to_user(db, row=row, user_auth_sub=user_auth_sub):
            return _error(f"Domain envelope {normalized_envelope_id} was not found.")

        requested_object_ref = _optional_text(object_id)
        normalized_field_path = _optional_text(field_path)
        envelope = DomainEnvelope.model_validate(row.envelope_json)
        object_id_by_ref = _object_id_by_ref(envelope)
        resolved_object_id = _resolved_object_id(
            requested_object_ref,
            object_id_by_ref,
        )

        object_query = (
            select(DomainEnvelopeObject)
            .where(DomainEnvelopeObject.envelope_id == normalized_envelope_id)
            .order_by(DomainEnvelopeObject.object_index.asc())
        )
        if requested_object_ref:
            object_query = object_query.where(
                or_(
                    DomainEnvelopeObject.object_id == requested_object_ref,
                    DomainEnvelopeObject.pending_ref_id == requested_object_ref,
                )
            )

        finding_query = (
            select(DomainValidationFinding)
            .where(DomainValidationFinding.envelope_id == normalized_envelope_id)
            .order_by(DomainValidationFinding.finding_index.asc())
        )
        if resolved_object_id:
            finding_query = finding_query.where(DomainValidationFinding.object_id == resolved_object_id)
        if normalized_field_path:
            finding_query = finding_query.where(DomainValidationFinding.field_path == normalized_field_path)

        projection_query = (
            select(DomainEnvelopeProjectionIndex)
            .where(DomainEnvelopeProjectionIndex.envelope_id == normalized_envelope_id)
            .order_by(
                DomainEnvelopeProjectionIndex.object_id.asc(),
                DomainEnvelopeProjectionIndex.projection_type.asc(),
                DomainEnvelopeProjectionIndex.projection_key.asc(),
            )
        )
        if resolved_object_id:
            projection_query = projection_query.where(
                DomainEnvelopeProjectionIndex.object_id == resolved_object_id
            )

        history_query = (
            select(DomainEnvelopeHistory)
            .where(DomainEnvelopeHistory.envelope_id == normalized_envelope_id)
            .order_by(DomainEnvelopeHistory.occurred_at.desc())
            .limit(_bounded_limit(history_limit))
        )

        object_rows = db.scalars(object_query).all()
        finding_rows = db.scalars(finding_query).all()
        projection_rows = db.scalars(projection_query).all()
        history_rows = list(reversed(db.scalars(history_query).all()))
        lookup_attempts = _lookup_attempt_summary(
            envelope=envelope,
            projection_rows=projection_rows,
        )

        return {
            "success": True,
            "semantic_source": "domain_envelope.objects",
            "envelope": _envelope_row_summary(row),
            "objects": [
                _object_row_payload(
                    object_row,
                    include_payload=include_object_payload,
                )
                for object_row in object_rows
            ],
            "validation_findings": [
                _finding_row_payload(finding_row)
                for finding_row in finding_rows
            ],
            "history": [_history_row_payload(history_row) for history_row in history_rows],
            "lookup_attempts": lookup_attempts,
            "projections": [
                _projection_row_payload(projection_row)
                for projection_row in projection_rows
            ],
            "object_ref_index": _object_ref_index_payload(object_id_by_ref),
            "filters": {
                "requested_object_ref": requested_object_ref,
                "object_id": resolved_object_id,
                "field_path": normalized_field_path,
                "include_object_payload": include_object_payload,
            },
            "instruction": (
                "Treat the envelope/object/finding/history/projection identifiers in this "
                "tool result as the current source-of-truth references for follow-up turns."
            ),
        }
    except ValueError as exc:
        return _error(str(exc))
    finally:
        db.close()


def get_domain_envelope_review_rows(
    *,
    session_factory: SessionFactory,
    user_auth_sub: str,
    envelope_id: str,
    revision: int | None = None,
    object_id: str | None = None,
) -> dict[str, Any]:
    """Regenerate materialized review rows for a persisted envelope revision."""

    db = session_factory()
    try:
        normalized_envelope_id = _required_text(envelope_id, "envelope_id")
        row = db.get(DomainEnvelopeModel, normalized_envelope_id)
        if row is None or not _envelope_visible_to_user(db, row=row, user_auth_sub=user_auth_sub):
            return _error(f"Domain envelope {normalized_envelope_id} was not found.")

        response = materialize_persisted_envelope_review_rows(
            db,
            normalized_envelope_id,
            revision=revision,
        )
        normalized_object_id = _optional_text(object_id)
        rows = [
            review_row.model_dump(mode="json")
            for review_row in response.rows
            if normalized_object_id is None or review_row.object_id == normalized_object_id
        ]
        return {
            "success": True,
            "semantic_source": "domain_envelope.objects",
            "envelope_id": response.envelope_id,
            "envelope_revision": response.envelope_revision,
            "row_count": len(rows),
            "rows": rows,
            "instruction": (
                "These rows are projections regenerated from the persisted envelope; "
                "cite envelope_id, object_id, envelope_revision, and field_path back to the curator."
            ),
        }
    except (ValueError, DomainEnvelopeMaterializationError) as exc:
        return _error(str(exc))
    finally:
        db.close()


def get_domain_pack_validation_plan(
    *,
    agent_id: str | None = None,
    domain_pack_id: str | None = None,
) -> dict[str, Any]:
    """Return domain-pack validation and authoring metadata for Opus."""

    try:
        resolved_agent_id = _optional_text(agent_id)
        resolved_domain_pack_id = _optional_text(domain_pack_id)
        if not resolved_agent_id and not resolved_domain_pack_id:
            raise ValueError("Provide agent_id or domain_pack_id")

        if resolved_agent_id and not resolved_domain_pack_id:
            from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

            entry = AGENT_REGISTRY.get(resolved_agent_id)
            if entry is None:
                return _error(f"Agent {resolved_agent_id} was not found.")
            curation = entry.get("curation")
            if not isinstance(curation, Mapping):
                return _error(f"Agent {resolved_agent_id} does not declare a domain pack.")
            resolved_domain_pack_id = _optional_text(curation.get("domain_pack_id"))
            if resolved_domain_pack_id is None:
                return _error(f"Agent {resolved_agent_id} does not declare a domain pack.")

        registries = domain_pack_validation_registries()
        registry = registries.get(str(resolved_domain_pack_id))
        if registry is None:
            return _error(f"Domain pack {resolved_domain_pack_id} was not found.")

        metadata = registry.domain_pack.metadata
        attachment_options = [option.to_dict() for option in registry.validation_attachment_options()]
        attachments_by_state = _group_by_string_key(attachment_options, "state")
        fields = [
            {
                "object_type": object_definition.object_type,
                "object_display_name": object_definition.display_name,
                "field_path": field_definition.field_path,
                "display_name": field_definition.display_name,
                "field_type": field_definition.field_type.value,
                "required": field_definition.required,
                "definition_state": field_definition.definition_state.value,
                "provider_refs": _provider_refs(field_definition.metadata),
                "validation_policy": (
                    policy.identity_details()
                    if (
                        policy := registry.policy_for(
                            object_definition.object_type,
                            field_definition.field_path,
                        )
                    )
                    is not None
                    else None
                ),
            }
            for object_definition in metadata.object_definitions
            for field_definition in object_definition.fields
        ]

        return {
            "success": True,
            "agent_id": resolved_agent_id,
            "domain_pack_id": metadata.pack_id,
            "domain_pack_version": metadata.version,
            "display_name": metadata.display_name,
            "status": metadata.status.value,
            "metadata_api_version": metadata.metadata_api_version,
            "schema_refs": [
                schema_ref.model_dump(mode="json", exclude_none=True)
                for schema_ref in metadata.schema_refs
            ],
            "provider_refs": _provider_refs(metadata.metadata),
            "object_definitions": [
                {
                    "object_type": object_definition.object_type,
                    "display_name": object_definition.display_name,
                    "object_role": _optional_text(object_definition.metadata.get("object_role")),
                    "model_ref": object_definition.model_ref,
                    "definition_state": object_definition.definition_state.value,
                    "provider_refs": _provider_refs(object_definition.metadata),
                    "field_paths": [
                        field_definition.field_path
                        for field_definition in object_definition.fields
                    ],
                }
                for object_definition in metadata.object_definitions
            ],
            "fields": fields,
            "validators": [entry.identity_details() for entry in registry.validator_metadata],
            "validator_bindings": [
                binding.identity_details()
                for binding in registry.bindings
            ],
            "field_policies": [policy.identity_details() for policy in registry.field_policies],
            "validation_attachments": attachment_options,
            "validation_attachment_summary": {
                "total": len(attachment_options),
                "by_state": {
                    state: len(items)
                    for state, items in attachments_by_state.items()
                },
                "default_enabled": sum(
                    1 for option in attachment_options if option.get("default_enabled")
                ),
                "required": sum(1 for option in attachment_options if option.get("required")),
                "export_blocking": sum(
                    1 for option in attachment_options if option.get("export_blocking")
                ),
                "opt_out_allowed": sum(
                    1 for option in attachment_options if option.get("allow_opt_out")
                ),
            },
            "validation_dispatch_summary": {
                "active_automatic": sum(
                    1
                    for option in attachment_options
                    if option.get("state") == "active" and option.get("default_enabled")
                ),
                "active_flow_opt_out_capable": sum(
                    1
                    for option in attachment_options
                    if option.get("state") == "active" and option.get("allow_opt_out")
                ),
                "under_development_metadata": sum(
                    1
                    for option in attachment_options
                    if option.get("state") == "under_development"
                ),
                "metadata_only": sum(
                    1
                    for option in attachment_options
                    if option.get("state") == "under_development"
                ),
                "validator_prompt_inspection": (
                    "Read validator_bindings[].validator_agent.agent_id or "
                    "validation_attachments[].validator_agent_id, then call "
                    "get_prompt(agent_id=<validator agent id>) for the validator prompt."
                ),
            },
            "automatic_validation_semantics": (
                "Active default-enabled attachments are the only validators scheduled "
                "automatically on extraction nodes, and runtime dispatch writes their "
                "findings back into domain envelopes after extraction. Under-development "
                "validator bindings are explanatory metadata, not scheduled work. Flow "
                "opt-outs mean an active default validator was skipped or replaced by "
                "flow configuration; replacement_validators and supplemental_validators "
                "appear in get_current_flow validation_schedule when configured. Do not "
                "ask extractor prompts to call validators directly."
            ),
        }
    except ValueError as exc:
        return _error(str(exc))


def get_export_submission_readiness(
    *,
    session_factory: SessionFactory,
    user_auth_sub: str,
    session_id: str,
    candidate_ids: Sequence[str] | None = None,
    expected_envelope_revisions: Mapping[str, int] | None = None,
    mode: str = "readiness",
) -> dict[str, Any]:
    """Inspect current export/submission readiness without executing submission."""

    db = session_factory()
    try:
        normalized_session_id = _required_text(session_id, "session_id")
        if not _session_visible_to_user(
            db,
            session_id=normalized_session_id,
            user_auth_sub=user_auth_sub,
        ):
            return _error(f"Curation review session {normalized_session_id} was not found.")

        session_row = _load_session_for_validation(db, session_id=normalized_session_id)
        candidate_map = {str(candidate.id): candidate for candidate in session_row.candidates}
        target_candidate_ids = list(candidate_ids or candidate_map.keys())
        unknown_candidate_ids = sorted(set(target_candidate_ids) - set(candidate_map))
        if unknown_candidate_ids:
            return _error(
                "Unknown candidate(s) for session: " + ", ".join(unknown_candidate_ids)
            )

        domain_context = _build_domain_envelope_submission_context(
            db=db,
            candidates=candidate_map,
            target_candidate_ids=target_candidate_ids,
            expected_envelope_revisions=dict(expected_envelope_revisions or {}),
        )
        readiness = []
        for candidate_id in target_candidate_ids:
            candidate = candidate_map[candidate_id]
            latest_snapshot = _latest_candidate_validation_snapshot(candidate)
            readiness_item = _candidate_submission_readiness(
                candidate,
                latest_snapshot,
                domain_context=domain_context,
            )
            readiness.append(readiness_item.model_dump(mode="json"))

        blockers = [
            blocker
            for readiness_item in readiness
            for blocker in readiness_item.get("blockers", [])
        ]
        return {
            "success": True,
            "session_id": normalized_session_id,
            "mode": _required_text(mode, "mode"),
            "candidate_count": len(readiness),
            "ready_count": sum(1 for item in readiness if item.get("ready") is True),
            "blocker_count": len(blockers),
            "readiness": readiness,
            "domain_envelope_ids": sorted(domain_context.envelope_snapshots.keys()),
            "instruction": (
                "This is a read-only readiness explanation. It does not export or submit. "
                "Use blockers[].envelope_id, object_id, field_path, code, and message when "
                "explaining what needs curator review."
            ),
        }
    except (HTTPException, ValueError) as exc:
        detail = getattr(exc, "detail", str(exc))
        return _error(str(detail))
    finally:
        db.close()


def current_flow_domain_envelope_analysis(
    *,
    flow_context: Mapping[str, Any],
    agent_registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize envelope-producing nodes and validation schedules in a flow."""

    nodes = flow_context.get("nodes") if isinstance(flow_context, Mapping) else []
    if not isinstance(nodes, list):
        nodes = []

    analyses: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        node_data = node.get("data", node)
        if not isinstance(node_data, Mapping):
            continue
        agent_id = _optional_text(node_data.get("agent_id"))
        if agent_id is None:
            continue
        entry = agent_registry.get(agent_id, {})
        curation = entry.get("curation")
        if not isinstance(curation, Mapping):
            continue
        domain_pack_id = _optional_text(curation.get("domain_pack_id"))
        if domain_pack_id is None:
            continue
        plan = get_domain_pack_validation_plan(agent_id=agent_id)
        if not plan.get("success"):
            analyses.append(
                {
                    "node_id": node.get("id"),
                    "agent_id": agent_id,
                    "domain_pack_id": domain_pack_id,
                    "error": plan.get("error"),
                }
            )
            continue
        analyses.append(
            {
                "node_id": node.get("id"),
                "agent_id": agent_id,
                "agent_display_name": node_data.get("agent_display_name") or entry.get("name"),
                "domain_pack_id": domain_pack_id,
                "domain_pack_version": plan.get("domain_pack_version"),
                "object_definitions": plan.get("object_definitions", []),
                "validation_schedule": validation_schedule_from_node_data(node_data),
                "validation_attachment_summary": plan.get("validation_attachment_summary"),
            }
        )

    return {
        "semantic_source": "domain_envelope.objects",
        "envelope_node_count": len(analyses),
        "nodes": analyses,
    }


def _latest_candidate_validation_snapshot(candidate: CurationCandidate) -> Any | None:
    latest_snapshot = _latest_snapshot_record(candidate.validation_snapshots)
    if latest_snapshot is None:
        return None
    return _validation_snapshot(latest_snapshot)


def _session_visible_to_user(
    db: Session,
    *,
    session_id: str | UUID,
    user_auth_sub: str,
) -> bool:
    try:
        normalized_session_id = _uuid(session_id, "session_id")
    except ValueError:
        return False
    session_row = db.get(CurationReviewSession, normalized_session_id)
    if session_row is None:
        return False

    owner_values = {
        _optional_text(session_row.created_by_id),
        _optional_text(session_row.assigned_curator_id),
    }
    owner_values.discard(None)
    if not owner_values:
        return True
    return _optional_text(user_auth_sub) in owner_values


def _envelope_visible_to_user(
    db: Session,
    *,
    row: DomainEnvelopeModel,
    user_auth_sub: str,
) -> bool:
    if row.session_id is not None:
        return _session_visible_to_user(
            db,
            session_id=row.session_id,
            user_auth_sub=user_auth_sub,
        )

    return any(
        _session_visible_to_user(
            db,
            session_id=session_id,
            user_auth_sub=user_auth_sub,
        )
        for session_id in _candidate_session_ids_for_envelope(db, row.envelope_id)
    )


def _candidate_session_ids_for_envelope(
    db: Session,
    envelope_id: str | None,
) -> list[UUID]:
    normalized_envelope_id = _optional_text(envelope_id)
    if normalized_envelope_id is None:
        return []
    return [
        session_id
        for session_id in db.scalars(
            select(CurationCandidate.session_id)
            .where(CurationCandidate.envelope_id == normalized_envelope_id)
            .distinct()
        ).all()
        if session_id is not None
    ]


def _bounded_limit(value: int | None, *, default: int = _DEFAULT_LIMIT) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit must be an integer")
    return max(1, min(value, _MAX_LIMIT))


def _required_text(value: Any, field_name: str) -> str:
    normalized = _optional_text(value)
    if normalized is None:
        raise ValueError(f"Missing required parameter: {field_name}")
    return normalized


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _uuid(value: str | UUID, field_name: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(_required_text(value, field_name))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid UUID") from exc


def _error(message: str) -> dict[str, Any]:
    return {"success": False, "error": message}


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _provider_refs(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw_provider_refs = metadata.get("provider_refs")
    return dict(raw_provider_refs) if isinstance(raw_provider_refs, Mapping) else {}


def _envelope_row_summary(row: DomainEnvelopeModel) -> dict[str, Any]:
    return {
        "envelope_id": row.envelope_id,
        "envelope_revision": row.revision,
        "project_key": row.project_key,
        "domain_pack_id": row.domain_pack_key,
        "domain_pack_version": row.domain_pack_version,
        "status": _enum_value(row.status),
        "document_id": str(row.document_id) if row.document_id else None,
        "session_id": str(row.session_id) if row.session_id else None,
        "flow_run_id": row.flow_run_id,
        "schema_provider": row.schema_provider,
        "schema_ref": dict(row.schema_ref_json or {}),
        "updated_at": _iso(row.updated_at),
        "checkpointed_at": _iso(row.checkpointed_at),
    }


def _object_row_payload(
    row: DomainEnvelopeObject,
    *,
    include_payload: bool,
) -> dict[str, Any]:
    payload = dict(row.payload_json or {})
    result = {
        "envelope_id": row.envelope_id,
        "object_id": row.object_id,
        "pending_ref_id": row.pending_ref_id,
        "envelope_revision": row.envelope_revision,
        "object_index": row.object_index,
        "object_type": row.object_type,
        "status": _enum_value(row.status),
        "validation_state": row.validation_state,
        "schema_provider": row.schema_provider,
        "schema_ref": dict(row.schema_ref_json or {}),
        "object_model_ref": dict(row.object_model_ref_json or {}),
        "model_field_ref": dict(row.model_field_ref_json or {}),
        "field_paths": _field_paths(payload),
        "payload_keys": sorted(payload.keys()),
    }
    if include_payload:
        result["payload"] = _bounded_json(payload)
    return result


def _finding_row_payload(row: DomainValidationFinding) -> dict[str, Any]:
    return {
        "envelope_id": row.envelope_id,
        "finding_id": row.finding_id,
        "envelope_revision": row.envelope_revision,
        "finding_index": row.finding_index,
        "object_id": row.object_id,
        "field_path": row.field_path,
        "severity": _enum_value(row.severity),
        "status": _enum_value(row.status),
        "code": row.code,
        "object_model_ref": dict(row.object_model_ref_json or {}),
        "model_field_ref": dict(row.model_field_ref_json or {}),
        "finding": _bounded_json(dict(row.finding_json or {})),
    }


def _history_row_payload(row: DomainEnvelopeHistory) -> dict[str, Any]:
    event_json = dict(row.event_json or {})
    return {
        "envelope_id": row.envelope_id,
        "event_id": row.event_id,
        "envelope_revision": row.envelope_revision,
        "event_index": row.event_index,
        "event_type": _enum_value(row.event_type),
        "occurred_at": _iso(row.occurred_at),
        "actor_type": _enum_value(row.actor_type),
        "actor_id": row.actor_id,
        "object_id": row.object_id,
        "field_path": row.field_path,
        "message": event_json.get("message"),
        "details": _bounded_json(event_json.get("details", {})),
    }


def _projection_row_payload(row: DomainEnvelopeProjectionIndex) -> dict[str, Any]:
    return {
        "envelope_id": row.envelope_id,
        "object_id": row.object_id,
        "envelope_revision": row.envelope_revision,
        "object_type": row.object_type,
        "projection_type": row.projection_type,
        "projection_key": row.projection_key,
        "projection_status": row.projection_status,
        "schema_provider": row.schema_provider,
        "schema_ref": dict(row.schema_ref_json or {}),
        "object_model_ref": dict(row.object_model_ref_json or {}),
        "model_field_ref": dict(row.model_field_ref_json or {}),
        "projection": _bounded_json(row.projection_json),
    }


def _object_ref_payload(ref: ObjectRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return ref.model_dump(mode="json", exclude_none=True)


def _field_ref_payload(ref: FieldRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    return ref.model_dump(mode="json", exclude_none=True)


def _history_event_payload(event: HistoryEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "timestamp": event.timestamp.isoformat(),
        "actor_type": event.actor_type.value,
        "actor_id": event.actor_id,
        "message": event.message,
        "object_ref": _object_ref_payload(event.object_ref),
        "field_ref": _field_ref_payload(event.field_ref),
        "details": _bounded_json(event.details),
    }


def _lookup_attempt_summary(
    *,
    envelope: DomainEnvelope,
    projection_rows: Sequence[DomainEnvelopeProjectionIndex],
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    _collect_lookup_attempts(
        envelope.model_dump(mode="json"),
        path="envelope",
        attempts=attempts,
    )
    for row in projection_rows:
        _collect_lookup_attempts(
            row.projection_json,
            path=(
                "projection:"
                f"{row.object_id}:{row.projection_type}:{row.projection_key}"
            ),
            attempts=attempts,
        )

    statuses = Counter(_lookup_attempt_status(attempt) for attempt in attempts)
    return {
        "attempt_count": len(attempts),
        "by_status": dict(sorted(statuses.items())),
        "attempts": attempts[:_MAX_LOOKUP_ATTEMPTS],
        "truncated": len(attempts) > _MAX_LOOKUP_ATTEMPTS,
        "interpretation": (
            "lookup_attempts is an audit trail. Use top-level lookup_status or "
            "projection/finding status for final outcome; attempts may include "
            "transient failures before a later successful retry."
        ),
    }


def _collect_lookup_attempts(
    value: Any,
    *,
    path: str,
    attempts: list[dict[str, Any]],
    depth: int = 0,
) -> None:
    if depth > 8 or len(attempts) > _MAX_LOOKUP_ATTEMPTS * 3:
        return
    if isinstance(value, Mapping):
        raw_attempts = value.get("lookup_attempts")
        if isinstance(raw_attempts, list):
            for index, attempt in enumerate(raw_attempts):
                if isinstance(attempt, Mapping):
                    attempts.append(_lookup_attempt_payload(attempt, f"{path}.lookup_attempts[{index}]"))
        for key, item in value.items():
            if key == "lookup_attempts":
                continue
            _collect_lookup_attempts(item, path=f"{path}.{key}", attempts=attempts, depth=depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value[:25]):
            _collect_lookup_attempts(item, path=f"{path}[{index}]", attempts=attempts, depth=depth + 1)


def _lookup_attempt_payload(attempt: Mapping[str, Any], path: str) -> dict[str, Any]:
    selected_keys = (
        "source_tool",
        "method",
        "provider",
        "attempted_query",
        "query",
        "target_projection",
        "lookup_status",
        "status",
        "candidate_count",
        "resolved_id",
        "resolved_label",
        "explanation",
        "error",
    )
    payload = {
        key: attempt[key]
        for key in selected_keys
        if key in attempt and attempt[key] not in (None, "")
    }
    payload["path"] = path
    bounded = _bounded_json(payload)
    if isinstance(bounded, dict) and bounded.get("_truncated"):
        bounded["path"] = path
        for status_key in ("lookup_status", "status"):
            if status_key in payload:
                bounded[status_key] = payload[status_key]
    return bounded


def _lookup_attempt_status(attempt: Mapping[str, Any]) -> str:
    for status_key in ("lookup_status", "status"):
        status = _optional_text(attempt.get(status_key))
        if status is not None:
            return status
    path = _optional_text(attempt.get("path")) or "<unknown lookup_attempt path>"
    raise ValueError(f"Lookup attempt at {path} is missing lookup_status/status.")


def _stable_object_id(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_id:
        return domain_object.object_id
    if domain_object.pending_ref_id:
        return domain_object.pending_ref_id
    raise ValueError("Domain envelope object is missing object_id and pending_ref_id")


def _object_id_by_ref(envelope: DomainEnvelope) -> dict[tuple[str, str], str]:
    object_id_by_ref: dict[tuple[str, str], str] = {}
    for domain_object in envelope.objects:
        stable_object_id = _stable_object_id(domain_object)
        if domain_object.object_id is not None:
            object_id_by_ref[("object_id", domain_object.object_id)] = stable_object_id
        if domain_object.pending_ref_id is not None:
            object_id_by_ref[("pending_ref_id", domain_object.pending_ref_id)] = stable_object_id
    return object_id_by_ref


def _resolved_object_id(
    requested_object_ref: str | None,
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> str | None:
    if requested_object_ref is None:
        return None
    return (
        object_id_by_ref.get(("object_id", requested_object_ref))
        or object_id_by_ref.get(("pending_ref_id", requested_object_ref))
        or requested_object_ref
    )


def _object_ref_index_payload(
    object_id_by_ref: Mapping[tuple[str, str], str],
) -> list[dict[str, str]]:
    return [
        {"ref_type": ref_type, "ref_id": ref_id, "object_id": object_id}
        for (ref_type, ref_id), object_id in sorted(object_id_by_ref.items())
    ]


def _field_paths(payload: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []

    def _walk(value: Any, prefix: str) -> None:
        if len(paths) >= _MAX_FIELD_PATHS:
            return
        if isinstance(value, Mapping):
            if not value and prefix:
                paths.append(prefix)
            for key, item in value.items():
                next_prefix = f"{prefix}.{key}" if prefix else str(key)
                _walk(item, next_prefix)
            return
        if isinstance(value, list):
            if not value and prefix:
                paths.append(prefix)
            for index, item in enumerate(value[:10]):
                _walk(item, f"{prefix}[{index}]")
            return
        if prefix:
            paths.append(prefix)

    _walk(payload, "")
    return paths


def _bounded_json(value: Any, *, max_chars: int = _MAX_JSON_CHARS) -> Any:
    try:
        rendered = json.dumps(value, default=str, sort_keys=True)
    except TypeError:
        return str(value)
    if len(rendered) <= max_chars:
        return value
    return {
        "_truncated": True,
        "approx_chars": len(rendered),
        "preview_json": rendered[:max_chars],
    }


def _group_by_string_key(
    items: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        value = _optional_text(item.get(key))
        if value is None:
            item_id = (
                _optional_text(item.get("attachment_id"))
                or _optional_text(item.get("validator_binding_id"))
                or _optional_text(item.get("validator_id"))
                or "<unidentified item>"
            )
            raise ValueError(f"Item {item_id} is missing required grouping key: {key}")
        grouped.setdefault(value, []).append(item)
    return grouped


__all__ = [
    "current_flow_domain_envelope_analysis",
    "get_domain_envelope_review_rows",
    "get_domain_envelope_state",
    "get_domain_pack_validation_plan",
    "get_export_submission_readiness",
    "list_domain_envelopes",
]
