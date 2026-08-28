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
    _finding_blocks_readiness,
    _finding_waiver_allowed,
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
from src.lib.openai_agents.config import (
    get_domain_envelope_default_limit,
    get_domain_envelope_max_field_paths,
    get_domain_envelope_max_json_chars,
    get_domain_envelope_max_limit,
    get_domain_envelope_max_lookup_attempts,
    get_domain_envelope_max_summary_json_chars,
    get_domain_envelope_max_validator_lookup_attempts,
    get_domain_envelope_max_validator_summaries,
    get_domain_pack_validation_plan_default_limit,
    get_domain_pack_validation_plan_max_limit,
    get_domain_runtime_inspection_default_limit,
    get_domain_runtime_inspection_max_limit,
)
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    FieldRef,
    HistoryEvent,
    ObjectRef,
    ValidationFindingStatus,
)


SessionFactory = Callable[[], Session]

# Env-configurable (defaults unchanged); see config.py getters and .env.example
# (DOMAIN_ENVELOPE_* group).
_MAX_LIMIT = get_domain_envelope_max_limit()
_DEFAULT_LIMIT = get_domain_envelope_default_limit()
_MAX_JSON_CHARS = get_domain_envelope_max_json_chars()
_MAX_LOOKUP_ATTEMPTS = get_domain_envelope_max_lookup_attempts()
_MAX_VALIDATOR_SUMMARIES = get_domain_envelope_max_validator_summaries()
_MAX_VALIDATOR_LOOKUP_ATTEMPTS = get_domain_envelope_max_validator_lookup_attempts()
_MAX_SUMMARY_JSON_CHARS = get_domain_envelope_max_summary_json_chars()
_MAX_FIELD_PATHS = get_domain_envelope_max_field_paths()
_DOMAIN_PLAN_DEFAULT_LIMIT = get_domain_pack_validation_plan_default_limit()
_DOMAIN_PLAN_MAX_LIMIT = get_domain_pack_validation_plan_max_limit()
_RUNTIME_DEFAULT_LIMIT = get_domain_runtime_inspection_default_limit()
_RUNTIME_MAX_LIMIT = get_domain_runtime_inspection_max_limit()

_ENVELOPE_STATE_SECTIONS = (
    "objects",
    "validation_findings",
    "projections",
    "history",
    "lookup_attempts",
    "validator_summaries",
    "object_ref_index",
)
_ENVELOPE_STATE_FILTERS = {
    "objects": ("object_id", "query"),
    "validation_findings": ("object_id", "field_path", "query"),
    "projections": ("object_id", "query"),
    "history": ("object_id", "field_path", "query"),
    "lookup_attempts": ("query",),
    "validator_summaries": ("object_id", "field_path", "query"),
    "object_ref_index": ("object_id", "query"),
}
_REVIEW_ROW_SECTIONS = ("rows",)
_READINESS_SECTIONS = ("candidates", "blockers")
_READINESS_FILTERS = {
    "candidates": ("candidate_id", "query"),
    "blockers": (
        "candidate_id",
        "envelope_id",
        "object_id",
        "field_path",
        "code",
        "query",
    ),
}

_DOMAIN_PLAN_SECTIONS = (
    "object_definitions",
    "fields",
    "validators",
    "validator_bindings",
    "field_policies",
    "validation_attachments",
)
_DOMAIN_PLAN_FILTERS = {
    "object_definitions": ("object_type", "query"),
    "fields": ("object_type", "field_path", "query"),
    "validators": ("validator_id", "state", "query"),
    "validator_bindings": (
        "object_type",
        "field_path",
        "binding_id",
        "state",
        "query",
    ),
    "field_policies": ("object_type", "field_path", "query"),
    "validation_attachments": (
        "object_type",
        "field_path",
        "validator_id",
        "binding_id",
        "state",
        "query",
    ),
}


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
    revision: int | None = None,
    section: str | None = None,
    object_id: str | None = None,
    field_path: str | None = None,
    query: str | None = None,
    include_object_payload: bool = False,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return a compact envelope summary or one revision-pinned detail page."""

    db = session_factory()
    try:
        normalized_envelope_id = _required_text(envelope_id, "envelope_id")
        row = db.get(DomainEnvelopeModel, normalized_envelope_id)
        if row is None or not _envelope_visible_to_user(db, row=row, user_auth_sub=user_auth_sub):
            return _error(f"Domain envelope {normalized_envelope_id} was not found.")
        _require_current_revision(row=row, revision=revision)

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
            .where(DomainEnvelopeObject.envelope_revision == row.revision)
            .order_by(DomainEnvelopeObject.object_index.asc())
        )
        finding_query = (
            select(DomainValidationFinding)
            .where(DomainValidationFinding.envelope_id == normalized_envelope_id)
            .where(DomainValidationFinding.envelope_revision == row.revision)
            .order_by(DomainValidationFinding.finding_index.asc())
        )
        projection_query = (
            select(DomainEnvelopeProjectionIndex)
            .where(DomainEnvelopeProjectionIndex.envelope_id == normalized_envelope_id)
            .where(DomainEnvelopeProjectionIndex.envelope_revision == row.revision)
            .order_by(
                DomainEnvelopeProjectionIndex.object_id.asc(),
                DomainEnvelopeProjectionIndex.projection_type.asc(),
                DomainEnvelopeProjectionIndex.projection_key.asc(),
            )
        )
        history_query = (
            select(DomainEnvelopeHistory)
            .where(DomainEnvelopeHistory.envelope_id == normalized_envelope_id)
            .where(DomainEnvelopeHistory.envelope_revision <= row.revision)
            .order_by(DomainEnvelopeHistory.occurred_at.desc())
        )

        object_rows = db.scalars(object_query).all()
        finding_rows = db.scalars(finding_query).all()
        projection_rows = db.scalars(projection_query).all()
        history_rows = list(reversed(db.scalars(history_query).all()))
        lookup_attempts = _lookup_attempt_summary(
            envelope=envelope,
            projection_rows=projection_rows,
            include_all=True,
        )
        validator_summaries = _validator_summary_payload(finding_rows, include_all=True)
        section_items = {
            "objects": [
                _object_row_payload(item, include_payload=include_object_payload)
                for item in object_rows
            ],
            "validation_findings": [_finding_row_payload(item) for item in finding_rows],
            "projections": [_projection_row_payload(item) for item in projection_rows],
            "history": [_history_row_payload(item) for item in history_rows],
            "lookup_attempts": list(lookup_attempts["attempts"]),
            "validator_summaries": list(validator_summaries["summaries"]),
            "object_ref_index": _object_ref_index_payload(object_id_by_ref),
        }
        resolved_section = _optional_text(section) or "summary"
        filters = {
            "object_id": resolved_object_id,
            "field_path": normalized_field_path,
            "query": _optional_text(query),
            "include_object_payload": include_object_payload,
        }
        identity = {
            "success": True,
            "semantic_source": "domain_envelope.extracted_objects",
            "envelope": _envelope_row_summary(row),
            "section": resolved_section,
            "filters": filters,
        }
        if resolved_section == "summary":
            if any(value is not None for value in (limit, cursor)):
                raise ValueError("section is required when limit or cursor is provided")
            blocker_count = _envelope_validation_blocker_count(envelope)
            return {
                **identity,
                "section_counts": {
                    name: len(items) for name, items in section_items.items()
                },
                "envelope_status": envelope.status.value,
                "readiness_status": "blocked" if blocker_count else envelope.status.value,
                "blocker_count": blocker_count,
                "lookup_status_counts": lookup_attempts["by_status"],
                "validator_status_counts": validator_summaries["by_result_status"],
                "detail_requests": [
                    {
                        "envelope_id": normalized_envelope_id,
                        "section": name,
                        "revision": row.revision,
                        "supported_filters": list(_ENVELOPE_STATE_FILTERS[name]),
                    }
                    for name in _ENVELOPE_STATE_SECTIONS
                ],
                "instruction": (
                    "Request one detail section and follow next_request until complete. "
                    "Keep revision unchanged so later writes cannot splice snapshots."
                ),
            }

        if resolved_section not in _ENVELOPE_STATE_SECTIONS:
            raise ValueError(
                "section must be one of: summary, "
                + ", ".join(_ENVELOPE_STATE_SECTIONS)
            )
        active_filters = {
            key: value
            for key, value in filters.items()
            if key != "include_object_payload" and value is not None
        }
        unsupported = sorted(set(active_filters) - set(_ENVELOPE_STATE_FILTERS[resolved_section]))
        if unsupported:
            raise ValueError(
                f"section {resolved_section} does not support filter(s): "
                + ", ".join(unsupported)
            )
        filtered_items = _filter_runtime_items(
            section_items[resolved_section],
            filters=active_filters,
        )
        return {
            **identity,
            **_runtime_page(
                items=filtered_items,
                section_total_count=len(section_items[resolved_section]),
                limit=limit,
                cursor=cursor,
                next_request={
                    "envelope_id": normalized_envelope_id,
                    "revision": row.revision,
                    "section": resolved_section,
                    **active_filters,
                    **(
                        {"include_object_payload": True}
                        if resolved_section == "objects" and include_object_payload
                        else {}
                    ),
                },
            ),
            "instruction": (
                "Treat identifiers and provenance in this revision-pinned page as "
                "source-of-truth references; follow next_request until complete."
            ),
        }
    except (HTTPException, ValueError) as exc:
        detail = getattr(exc, "detail", str(exc))
        return _error(str(detail))
    finally:
        db.close()


def get_domain_envelope_review_rows(
    *,
    session_factory: SessionFactory,
    user_auth_sub: str,
    envelope_id: str,
    revision: int | None = None,
    section: str | None = None,
    object_id: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Summarize or page review rows regenerated from one envelope revision."""

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
        query_filter = _optional_text(query)
        if query_filter is not None:
            rows = _filter_runtime_items(rows, filters={"query": query_filter})
        resolved_section = _optional_text(section) or "summary"
        identity = {
            "success": True,
            "semantic_source": "domain_envelope.extracted_objects",
            "envelope_id": response.envelope_id,
            "envelope_revision": response.envelope_revision,
            "section": resolved_section,
            "row_count": response.row_count,
            "filtered_row_count": len(rows),
            "filters": {"object_id": normalized_object_id, "query": query_filter},
        }
        if resolved_section == "summary":
            if any(value is not None for value in (limit, cursor)):
                raise ValueError("section is required when limit or cursor is provided")
            return {
                **identity,
                "section_counts": {"rows": response.row_count},
                "detail_requests": [
                    {
                        "envelope_id": response.envelope_id,
                        "section": "rows",
                        "revision": response.envelope_revision,
                        "supported_filters": ["object_id", "query"],
                    }
                ],
                "instruction": (
                    "Request section=rows and follow next_request until complete. "
                    "Keep envelope_revision unchanged while traversing pages."
                ),
            }
        if resolved_section not in _REVIEW_ROW_SECTIONS:
            raise ValueError("section must be one of: summary, rows")
        return {
            **identity,
            **_runtime_page(
                items=rows,
                section_total_count=response.row_count,
                limit=limit,
                cursor=cursor,
                next_request={
                    "envelope_id": response.envelope_id,
                    "revision": response.envelope_revision,
                    "section": "rows",
                    **({"object_id": normalized_object_id} if normalized_object_id else {}),
                    **({"query": query_filter} if query_filter else {}),
                },
            ),
            "instruction": (
                "These rows are projections regenerated from the persisted envelope; "
                "cite envelope_id, object_id, envelope_revision, and field_path, and "
                "follow next_request until complete."
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
    section: str | None = None,
    object_type: str | None = None,
    field_path: str | None = None,
    validator_id: str | None = None,
    binding_id: str | None = None,
    state: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Return a compact domain-pack summary or one bounded detail section."""

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
            }
            for object_definition in metadata.object_definitions
            for field_definition in object_definition.fields
        ]
        section_items = {
            "object_definitions": sorted(
                (
                    {
                        "object_type": object_definition.object_type,
                        "display_name": object_definition.display_name,
                        "object_role": _optional_text(
                            object_definition.metadata.get("object_role")
                        ),
                        "model_ref": object_definition.model_ref,
                        "definition_state": object_definition.definition_state.value,
                        "provider_refs": _provider_refs(object_definition.metadata),
                        "field_paths": [
                            field_definition.field_path
                            for field_definition in object_definition.fields
                        ],
                    }
                    for object_definition in metadata.object_definitions
                ),
                key=lambda item: item["object_type"],
            ),
            "fields": sorted(
                fields,
                key=lambda item: (item["object_type"], item["field_path"]),
            ),
            "validators": sorted(
                (entry.identity_details() for entry in registry.validator_metadata),
                key=lambda item: (item["validator_id"], item["binding_state"]),
            ),
            "validator_bindings": sorted(
                (binding.identity_details() for binding in registry.bindings),
                key=lambda item: item["validator_binding_id"],
            ),
            "field_policies": sorted(
                (policy.identity_details() for policy in registry.field_policies),
                key=lambda item: (item["object_type"], item["field_path"]),
            ),
            "validation_attachments": sorted(
                attachment_options,
                key=lambda item: item["attachment_id"],
            ),
        }
        validation_attachment_summary = {
            "total": len(attachment_options),
            "by_state": {
                attachment_state: len(items)
                for attachment_state, items in attachments_by_state.items()
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
        }
        validation_dispatch_summary = {
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
            "validator_prompt_inspection": (
                "Read validator_bindings[].validator_agent.agent_id or "
                "validation_attachments[].validator_agent_id, then call "
                "get_prompt(agent_id=<validator agent id>) for the validator prompt."
            ),
        }
        automatic_validation_semantics = (
            "Active default-enabled attachments are the only validators scheduled "
            "automatically on extraction nodes, and runtime dispatch writes their "
            "findings back into domain envelopes after extraction. Under-development "
            "validator bindings are explanatory metadata, not scheduled work. Flow "
            "opt-outs mean an active default validator was skipped or replaced by "
            "flow configuration; replacement_validators and supplemental_validators "
            "appear in get_current_flow validation_schedule when configured. Do not "
            "ask extractor prompts to call validators directly."
        )
        identity = {
            "success": True,
            "agent_id": resolved_agent_id,
            "domain_pack_id": metadata.pack_id,
            "domain_pack_version": metadata.version,
            "display_name": metadata.display_name,
            "status": metadata.status.value,
            "metadata_api_version": metadata.metadata_api_version,
        }
        resolved_section = _optional_text(section)
        filters = {
            key: value
            for key, value in {
                "object_type": _optional_text(object_type),
                "field_path": _optional_text(field_path),
                "validator_id": _optional_text(validator_id),
                "binding_id": _optional_text(binding_id),
                "state": _optional_text(state),
                "query": _optional_text(query),
            }.items()
            if value is not None
        }
        if resolved_section is None:
            if filters or limit is not None or cursor is not None:
                raise ValueError(
                    "section is required when filters, limit, or cursor are provided"
                )
            return {
                **identity,
                "section": "summary",
                "schema_ref_count": len(metadata.schema_refs),
                "provider_ref_count": len(_provider_refs(metadata.metadata)),
                "section_counts": {
                    name: len(items) for name, items in section_items.items()
                },
                "validation_attachment_summary": validation_attachment_summary,
                "validation_dispatch_summary": validation_dispatch_summary,
                "automatic_validation_semantics": automatic_validation_semantics,
                "detail_requests": [
                    {
                        "section": name,
                        "filters": list(_DOMAIN_PLAN_FILTERS[name]),
                        "example_input": {
                            "domain_pack_id": metadata.pack_id,
                            "section": name,
                            "limit": _DOMAIN_PLAN_DEFAULT_LIMIT,
                        },
                    }
                    for name in _DOMAIN_PLAN_SECTIONS
                ],
            }

        if resolved_section not in section_items:
            raise ValueError(
                "section must be one of: " + ", ".join(_DOMAIN_PLAN_SECTIONS)
            )
        if filters.get("state") not in {None, "active", "under_development"}:
            raise ValueError("state must be one of: active, under_development")
        unsupported_filters = sorted(
            set(filters) - set(_DOMAIN_PLAN_FILTERS[resolved_section])
        )
        if unsupported_filters:
            raise ValueError(
                f"section {resolved_section} does not support filter(s): "
                + ", ".join(unsupported_filters)
            )
        page_limit = _domain_plan_limit(limit)
        filtered_items = _filter_domain_plan_items(
            section_items[resolved_section],
            filters=filters,
        )
        offset = _domain_plan_cursor(cursor, total_count=len(filtered_items))
        page_items = filtered_items[offset : offset + page_limit]
        next_offset = offset + len(page_items)
        complete = next_offset >= len(filtered_items)
        next_cursor = None if complete else str(next_offset)
        return {
            **identity,
            "section": resolved_section,
            "filters": filters,
            "section_total_count": len(section_items[resolved_section]),
            "total_count": len(filtered_items),
            "returned_count": len(page_items),
            "complete": complete,
            "truncated": not complete,
            "next_cursor": next_cursor,
            "items": page_items,
            "next_request": (
                None
                if next_cursor is None
                else {
                    "domain_pack_id": metadata.pack_id,
                    "section": resolved_section,
                    **filters,
                    "limit": page_limit,
                    "cursor": next_cursor,
                }
            ),
        }
    except ValueError as exc:
        return _error(str(exc))


def _domain_plan_limit(value: int | None) -> int:
    if value is None:
        return min(_DOMAIN_PLAN_DEFAULT_LIMIT, _DOMAIN_PLAN_MAX_LIMIT)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("limit must be an integer")
    if value < 1 or value > _DOMAIN_PLAN_MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_DOMAIN_PLAN_MAX_LIMIT}")
    return value


def _domain_plan_cursor(value: str | None, *, total_count: int) -> int:
    if value is None:
        return 0
    if not isinstance(value, str):
        raise ValueError("cursor must be a non-negative decimal offset string")
    normalized = _required_text(value, "cursor")
    if not normalized.isdigit():
        raise ValueError("cursor must be a non-negative decimal offset")
    offset = int(normalized)
    if offset > total_count:
        raise ValueError(f"cursor offset {offset} exceeds filtered total {total_count}")
    return offset


def _runtime_limit(value: int | None) -> int:
    resolved = _RUNTIME_DEFAULT_LIMIT if value is None else value
    if isinstance(resolved, bool) or not isinstance(resolved, int):
        raise ValueError("limit must be an integer")
    if resolved < 1:
        raise ValueError("limit must be greater than zero")
    return min(resolved, _RUNTIME_MAX_LIMIT)


def _runtime_page(
    *,
    items: Sequence[Mapping[str, Any]],
    section_total_count: int,
    limit: int | None,
    cursor: str | None,
    next_request: Mapping[str, Any],
) -> dict[str, Any]:
    page_limit = _runtime_limit(limit)
    offset = _domain_plan_cursor(cursor, total_count=len(items))
    page_items = [dict(item) for item in items[offset : offset + page_limit]]
    next_offset = offset + len(page_items)
    complete = next_offset >= len(items)
    next_cursor = None if complete else str(next_offset)
    return {
        "section_total_count": section_total_count,
        "total_count": len(items),
        "returned_count": len(page_items),
        "items": page_items,
        "complete": complete,
        "truncated": not complete,
        "next_cursor": next_cursor,
        "limit": page_limit,
        "next_request": (
            None
            if next_cursor is None
            else {**dict(next_request), "limit": page_limit, "cursor": next_cursor}
        ),
    }


def _filter_runtime_items(
    items: Sequence[Mapping[str, Any]],
    *,
    filters: Mapping[str, str],
) -> list[dict[str, Any]]:
    def matches(item: Mapping[str, Any]) -> bool:
        for filter_name, expected in filters.items():
            if filter_name == "query":
                if expected.casefold() not in json.dumps(
                    item,
                    sort_keys=True,
                    default=str,
                ).casefold():
                    return False
                continue
            actual = item.get(filter_name)
            if actual != expected:
                return False
        return True

    return [dict(item) for item in items if matches(item)]


def _require_current_revision(
    *,
    row: DomainEnvelopeModel,
    revision: int | None,
) -> None:
    if revision is None:
        return
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("revision must be a positive integer")
    if row.revision != revision:
        raise ValueError(
            f"Domain envelope {row.envelope_id} is at revision {row.revision}, "
            f"not requested revision {revision}"
        )


def _envelope_validation_blocker_count(envelope: DomainEnvelope) -> int:
    return sum(
        1
        for finding in envelope.validation_findings
        if _finding_blocks_readiness(finding)
        and finding.status is not ValidationFindingStatus.RESOLVED
        and not (
            finding.status is ValidationFindingStatus.WAIVED
            and _finding_waiver_allowed(finding)
        )
    )


def _filter_domain_plan_items(
    items: Sequence[Mapping[str, Any]],
    *,
    filters: Mapping[str, str],
) -> list[dict[str, Any]]:
    def matches(item: Mapping[str, Any]) -> bool:
        for filter_name, expected in filters.items():
            if filter_name == "query":
                if expected.casefold() not in json.dumps(
                    item,
                    sort_keys=True,
                    default=str,
                ).casefold():
                    return False
                continue
            item_key = {
                "binding_id": "validator_binding_id",
                "state": "binding_state",
            }.get(filter_name, filter_name)
            actual = item.get(item_key)
            if filter_name == "state" and actual is None:
                actual = item.get("state")
            if filter_name == "object_type" and actual is None:
                actual = item.get("source_object_type") or item.get("object_types")
            if filter_name == "field_path" and actual is None:
                actual = (
                    item.get("source_field_path")
                    or item.get("field_paths")
                    or item.get("affected_fields")
                )
            if isinstance(actual, list):
                if expected not in actual:
                    return False
            elif actual != expected:
                return False
        return True

    return [dict(item) for item in items if matches(item)]


def get_export_submission_readiness(
    *,
    session_factory: SessionFactory,
    user_auth_sub: str,
    session_id: str,
    candidate_ids: Sequence[str] | None = None,
    expected_envelope_revisions: Mapping[str, int] | None = None,
    mode: str = "readiness",
    section: str | None = None,
    candidate_id: str | None = None,
    envelope_id: str | None = None,
    object_id: str | None = None,
    field_path: str | None = None,
    code: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Summarize or page current readiness without executing submission."""

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
        for target_candidate_id in target_candidate_ids:
            candidate = candidate_map[target_candidate_id]
            latest_snapshot = _latest_candidate_validation_snapshot(candidate)
            readiness_item = _candidate_submission_readiness(
                candidate,
                latest_snapshot,
                domain_context=domain_context,
            )
            readiness.append(readiness_item.model_dump(mode="json"))

        blockers = [
            {"candidate_id": readiness_item.get("candidate_id"), **blocker}
            for readiness_item in readiness
            for blocker in readiness_item.get("blockers", [])
        ]
        candidate_items = []
        for readiness_item in readiness:
            item = dict(readiness_item)
            item_blockers = item.pop("blockers", [])
            item["blocker_count"] = len(item_blockers)
            candidate_items.append(item)
        resolved_section = _optional_text(section) or "summary"
        normalized_mode = _required_text(mode, "mode")
        current_envelope_revisions = {
            envelope_id: int(snapshot["envelope_revision"])
            for envelope_id, snapshot in domain_context.envelope_snapshots.items()
        }
        envelope_revisions = dict(
            expected_envelope_revisions or current_envelope_revisions
        )
        identity = {
            "success": True,
            "session_id": normalized_session_id,
            "mode": normalized_mode,
            "section": resolved_section,
            "candidate_count": len(readiness),
            "ready_count": sum(1 for item in readiness if item.get("ready") is True),
            "blocker_count": len(blockers),
            "domain_envelope_ids": sorted(envelope_revisions),
            "envelope_revisions": envelope_revisions,
        }
        if resolved_section == "summary":
            if any(value is not None for value in (limit, cursor)):
                raise ValueError("section is required when limit or cursor is provided")
            ready_count = identity["ready_count"]
            return {
                **identity,
                "ready": ready_count == len(readiness),
                "readiness_status": (
                    "ready" if ready_count == len(readiness) else "blocked"
                ),
                "section_counts": {
                    "candidates": len(candidate_items),
                    "blockers": len(blockers),
                },
                "detail_requests": [
                    {
                        "section": name,
                        "candidate_ids": target_candidate_ids,
                        "expected_envelope_revisions": envelope_revisions,
                        "supported_filters": list(_READINESS_FILTERS[name]),
                    }
                    for name in _READINESS_SECTIONS
                ],
                "instruction": (
                    "This is a read-only readiness summary. Request candidates or "
                    "blockers and follow next_request until complete; keep expected "
                    "envelope revisions unchanged while traversing pages."
                ),
            }
        if resolved_section not in _READINESS_SECTIONS:
            raise ValueError("section must be one of: summary, candidates, blockers")
        filters = {
            "candidate_id": _optional_text(candidate_id),
            "envelope_id": _optional_text(envelope_id),
            "object_id": _optional_text(object_id),
            "field_path": _optional_text(field_path),
            "code": _optional_text(code),
            "query": _optional_text(query),
        }
        active_filters = {key: value for key, value in filters.items() if value is not None}
        unsupported = sorted(set(active_filters) - set(_READINESS_FILTERS[resolved_section]))
        if unsupported:
            raise ValueError(
                f"section {resolved_section} does not support filter(s): "
                + ", ".join(unsupported)
            )
        section_items = {
            "candidates": candidate_items,
            "blockers": blockers,
        }
        filtered_items = _filter_runtime_items(
            section_items[resolved_section],
            filters=active_filters,
        )
        return {
            **identity,
            "filters": filters,
            **_runtime_page(
                items=filtered_items,
                section_total_count=len(section_items[resolved_section]),
                limit=limit,
                cursor=cursor,
                next_request={
                    "session_id": normalized_session_id,
                    "candidate_ids": target_candidate_ids,
                    "expected_envelope_revisions": envelope_revisions,
                    "mode": normalized_mode,
                    "section": resolved_section,
                    **active_filters,
                },
            ),
            "instruction": (
                "This is a read-only readiness explanation. It does not export or submit. "
                "Use blocker envelope_id, object_id, field_path, code, and message when "
                "explaining curator work, and follow next_request until complete."
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
    """Link envelope-producing nodes to compact domain-plan inspection."""

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
                "validation_plan_request": {
                    "tool": "get_domain_pack_validation_plan",
                    "input": {"agent_id": agent_id},
                },
                "validation_schedule": validation_schedule_from_node_data(node_data),
            }
        )

    return {
        "semantic_source": "domain_envelope.extracted_objects",
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
    include_all: bool = False,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    _collect_lookup_attempts(
        envelope.model_dump(mode="json"),
        path="envelope",
        attempts=attempts,
        max_attempts=None if include_all else _MAX_LOOKUP_ATTEMPTS,
    )
    for row in projection_rows:
        _collect_lookup_attempts(
            row.projection_json,
            path=(
                "projection:"
                f"{row.object_id}:{row.projection_type}:{row.projection_key}"
            ),
            attempts=attempts,
            max_attempts=None if include_all else _MAX_LOOKUP_ATTEMPTS,
        )

    statuses = Counter(_lookup_attempt_status(attempt) for attempt in attempts)
    return {
        "attempt_count": len(attempts),
        "by_status": dict(sorted(statuses.items())),
        "attempts": attempts if include_all else attempts[:_MAX_LOOKUP_ATTEMPTS],
        "truncated": not include_all and len(attempts) > _MAX_LOOKUP_ATTEMPTS,
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
    max_attempts: int | None = _MAX_LOOKUP_ATTEMPTS,
) -> None:
    if max_attempts is not None and (
        depth > 8 or len(attempts) > max_attempts * 3
    ):
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
            _collect_lookup_attempts(
                item,
                path=f"{path}.{key}",
                attempts=attempts,
                depth=depth + 1,
                max_attempts=max_attempts,
            )
    elif isinstance(value, list):
        selected = value if max_attempts is None else value[:25]
        for index, item in enumerate(selected):
            _collect_lookup_attempts(
                item,
                path=f"{path}[{index}]",
                attempts=attempts,
                depth=depth + 1,
                max_attempts=max_attempts,
            )


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
        "outcome",
        "candidate_count",
        "result_count",
        "resolved_id",
        "resolved_label",
        "explanation",
        "message",
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
    for status_key in ("lookup_status", "status", "outcome"):
        status = _optional_text(attempt.get(status_key))
        if status is not None:
            return status
    path = _optional_text(attempt.get("path")) or "<unknown lookup_attempt path>"
    raise ValueError(f"Lookup attempt at {path} is missing lookup_status/status/outcome.")


def _validator_summary_payload(
    finding_rows: Sequence[DomainValidationFinding],
    *,
    include_all: bool = False,
) -> dict[str, Any]:
    summaries = [
        summary
        for row in finding_rows
        if (summary := _validator_summary_for_finding(row)) is not None
    ]
    statuses = Counter(
        _optional_text(summary.get("result_status")) or "unknown"
        for summary in summaries
    )
    return {
        "summary_count": len(summaries),
        "by_result_status": dict(sorted(statuses.items())),
        "summaries": summaries if include_all else summaries[:_MAX_VALIDATOR_SUMMARIES],
        "truncated": not include_all and len(summaries) > _MAX_VALIDATOR_SUMMARIES,
        "interpretation": (
            "Each summary is reconstructed from persisted validation finding "
            "details. selected_inputs came from domain-pack selectors; "
            "materialization_paths map validator resolved_values keys to target "
            "payload field paths declared in expected_result_fields."
        ),
    }


def _validator_summary_for_finding(
    row: DomainValidationFinding,
) -> dict[str, Any] | None:
    finding = _as_mapping(row.finding_json)
    details = _as_mapping(finding.get("details"))
    validation_request = _as_mapping(details.get("validation_request"))
    validation_result = _as_mapping(details.get("validation_result"))
    validation_metadata = _as_mapping(details.get("validation_metadata"))
    if not (validation_request or validation_result or validation_metadata):
        return None

    binding_id = _optional_text(
        validation_request.get("validator_binding_id")
        or validation_result.get("validator_binding_id")
        or validation_metadata.get("validator_binding_id")
    )
    validator_agent = _as_mapping(
        validation_request.get("validator_agent")
        or validation_result.get("validator_agent")
        or validation_metadata.get("validator_agent")
    )
    target = _as_mapping(
        validation_request.get("target")
        or validation_result.get("target")
        or validation_metadata.get("target")
    )

    selected_inputs = _as_mapping(validation_request.get("selected_inputs"))
    input_selectors = _as_mapping(validation_request.get("input_selectors"))
    expected_result_fields = _as_mapping(
        validation_request.get("expected_result_fields")
    )
    resolved_values = _as_mapping(validation_result.get("resolved_values"))

    lookup_attempts = details.get("lookup_attempts")
    if not isinstance(lookup_attempts, list):
        lookup_attempts = validation_result.get("lookup_attempts")
    compact_attempts = _validator_lookup_attempts(lookup_attempts)

    return {
        "finding_id": row.finding_id,
        "finding_index": row.finding_index,
        "object_id": row.object_id,
        "field_path": row.field_path,
        "finding_status": _enum_value(row.status),
        "finding_severity": _enum_value(row.severity),
        "finding_code": row.code,
        "validator_binding_id": binding_id,
        "validator_agent": _bounded_summary_json(validator_agent),
        "target": _bounded_summary_json(target),
        "selected_inputs": _bounded_summary_json(selected_inputs),
        "input_selectors": _bounded_summary_json(input_selectors),
        "expected_result_fields": _bounded_summary_json(expected_result_fields),
        "result_status": _optional_text(validation_result.get("status")),
        "resolved_values": _bounded_summary_json(resolved_values),
        "missing_expected_fields": _bounded_summary_json(
            validation_result.get("missing_expected_fields") or []
        ),
        "materialization_paths": _bounded_summary_json(
            _materialization_paths(
                expected_result_fields=expected_result_fields,
                resolved_values=resolved_values,
            )
        ),
        "lookup_attempts": compact_attempts,
        "lookup_attempt_count": len(lookup_attempts)
        if isinstance(lookup_attempts, list)
        else 0,
        "lookup_attempts_truncated": (
            isinstance(lookup_attempts, list)
            and len(lookup_attempts) > _MAX_VALIDATOR_LOOKUP_ATTEMPTS
        ),
        "curator_message": (
            _optional_text(validation_result.get("curator_message"))
            or _optional_text(details.get("curator_message"))
        ),
        "explanation": _optional_text(validation_result.get("explanation")),
        "failure_classification": _optional_text(
            details.get("failure_classification")
        ),
    }


def _validator_lookup_attempts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    attempts = []
    for index, attempt in enumerate(value[:_MAX_VALIDATOR_LOOKUP_ATTEMPTS]):
        if not isinstance(attempt, Mapping):
            continue
        attempts.append(
            _lookup_attempt_payload(
                attempt,
                f"validation.lookup_attempts[{index}]",
            )
        )
    return attempts


def _materialization_paths(
    *,
    expected_result_fields: Mapping[str, Any],
    resolved_values: Mapping[str, Any],
) -> list[dict[str, Any]]:
    paths = []
    for result_field, target_field_path in sorted(expected_result_fields.items()):
        resolved_value = resolved_values.get(result_field)
        paths.append(
            {
                "result_field": result_field,
                "field_path": target_field_path,
                "resolved": not _summary_value_missing(resolved_value),
                **(
                    {"resolved_value": resolved_value}
                    if not _summary_value_missing(resolved_value)
                    else {}
                ),
            }
        )
    return paths


def _summary_value_missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _bounded_summary_json(value: Any) -> Any:
    return _bounded_json(value, max_chars=_MAX_SUMMARY_JSON_CHARS)


def _stable_object_id(domain_object: CuratableObjectEnvelope) -> str:
    if domain_object.object_id:
        return domain_object.object_id
    if domain_object.pending_ref_id:
        return domain_object.pending_ref_id
    raise ValueError("Domain envelope object is missing object_id and pending_ref_id")


def _object_id_by_ref(envelope: DomainEnvelope) -> dict[tuple[str, str], str]:
    object_id_by_ref: dict[tuple[str, str], str] = {}
    for domain_object in envelope.extracted_objects:
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
