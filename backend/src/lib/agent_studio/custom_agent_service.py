"""Custom agent service for Agent Workshop CRUD and runtime resolution."""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from src.lib.agent_studio.agent_service import get_agent_by_key, get_project_ids_for_user
from src.lib.agent_studio.agent_identity import require_canonical_agent_identity
from src.lib.agent_studio.catalog_service import DOCUMENT_TOOL_IDS, has_tool_binding
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.lib.agent_studio.authoring_validation import (
    AgentModelValidationRecord,
    AgentToolValidationRecord,
    AgentValidationSources,
    AuthoringValidationContext,
    AuthoringValidationError,
    LOCKED_PROMPT_MARKERS,
    report_authoring_validation_engine_failure,
    validate_custom_agent_authoring_draft,
)
from src.lib.agent_access import (
    normalize_allowed_group_ids,
    require_allowed_group_ids_narrowing,
)
from src.lib.config.models_loader import get_model
from src.lib.config.groups_loader import get_valid_group_ids
from src.lib.config.schema_discovery import resolve_output_schema
from src.lib.group_tool_policy import parse_group_tool_policy
from src.lib.prompts.assembly import build_agent_prompt_layers
from src.models.sql.agent import Agent as CustomAgent, ProjectMember
from src.models.sql.custom_agent import CustomAgentVersion
from src.models.sql.database import SessionLocal
from src.schemas.agent_execution_revision import AgentOutputContract, GenericProfilePin, initial_output_contract
from src.schemas.generic_extraction_profile import GenericProfileContract


CUSTOM_AGENT_PREFIX = "ca_"
_SYSTEM_MANAGED_INHERITED_TOOL_IDS = {
    "get_agent_contract",
    "record_evidence",
    "list_recorded_evidence",
    "get_recorded_evidence",
    "attach_evidence_to_object",
    "detach_evidence_from_object",
    "discard_recorded_evidence",
    "update_recorded_evidence_metadata",
}
logger = logging.getLogger(__name__)


class CustomAgentError(Exception):
    """Base class for custom-agent service errors."""


class CustomAgentNotFoundError(CustomAgentError):
    """Raised when a custom agent does not exist or is not active."""


class CustomAgentAccessError(CustomAgentError):
    """Raised when a user attempts to access another user's custom agent."""


@dataclass(frozen=True)
class CustomOverlayNormalization:
    """Result of checking legacy custom-agent prompt text for copied locked layers."""

    content: str
    status: str
    removed_layer_kinds: List[str]
    warning: Optional[str] = None


def _read_allowed_group_ids(record: Any, *, field_name: str = "allowed_group_ids") -> list[str]:
    return normalize_allowed_group_ids(
        list(record.allowed_group_ids),
        field_name=field_name,
    )


def _looks_like_legacy_curator_overlay(prompt: str) -> bool:
    """Detect rows saved by the interim overlay-only custom-agent editor."""
    stripped = str(prompt or "").strip()
    lowered = stripped.lower()
    return lowered.startswith("<curator_overlay>") and "</curator_overlay>" in lowered


def _unwrap_legacy_curator_overlay(prompt: str) -> str:
    stripped = str(prompt or "").strip()
    lowered = stripped.lower()
    start_tag = "<curator_overlay>"
    end_tag = "</curator_overlay>"
    if not lowered.startswith(start_tag) or end_tag not in lowered:
        return stripped

    end_index = lowered.rfind(end_tag)
    return stripped[len(start_tag):end_index].strip()


def _parent_base_prompt_for_agent(parent_agent_key: Optional[str]) -> str:
    parent_key = str(parent_agent_key or "").strip()
    if not parent_key:
        return ""

    try:
        bundle = build_agent_prompt_layers(parent_key)
    except Exception as exc:
        logger.warning(
            "Could not resolve parent base prompt for custom-agent main prompt.",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"parent_agent_key": parent_key},
        )
        return ""

    base_parts = [
        str(layer.content or "").strip()
        for layer in bundle.layers
        if getattr(layer, "kind", None) == "base_prompt" and str(layer.content or "").strip()
    ]
    return "\n\n".join(base_parts).strip()


def custom_main_prompt_for_parent(
    parent_agent_key: Optional[str],
    custom_prompt: Optional[str],
) -> str:
    """Return editable custom-agent main prompt text with legacy rows expanded.

    The restored Agent Workshop treats `instructions` as the full editable
    main/base prompt. Rows saved by the interim UI may contain only a
    `<curator_overlay>` block; expand those rows with the parent base prompt so
    display and runtime keep the inherited behavior until the curator saves.
    """
    prompt = str(custom_prompt or "").strip()
    if not prompt:
        return prompt
    if locked_prompt_marker_in(prompt):
        normalization = normalize_custom_overlay_for_parent(
            parent_agent_key,
            prompt,
        )
        if normalization.status == "needs_review":
            raise ValueError(
                normalization.warning
                or "Custom-agent prompt contains copied locked/core prompt text."
            )
        prompt = normalization.content
        if not prompt:
            return _parent_base_prompt_for_agent(parent_agent_key)
        base_prompt = _parent_base_prompt_for_agent(parent_agent_key)
        if not base_prompt:
            return prompt
        return f"{base_prompt}\n\n## Custom instructions\n{prompt}".strip()
    if not _looks_like_legacy_curator_overlay(prompt):
        return prompt

    base_prompt = _parent_base_prompt_for_agent(parent_agent_key)
    overlay_content = _unwrap_legacy_curator_overlay(prompt)
    if not base_prompt:
        return overlay_content or prompt
    if not overlay_content:
        return base_prompt
    return f"{base_prompt}\n\n## Custom instructions\n{overlay_content}".strip()


def _collapse_prompt_whitespace(prompt: str) -> str:
    lines = [line.rstrip() for line in str(prompt or "").splitlines()]
    collapsed: List[str] = []
    blank_seen = False
    for line in lines:
        if line.strip():
            collapsed.append(line)
            blank_seen = False
            continue
        if not blank_seen:
            collapsed.append("")
        blank_seen = True
    return "\n".join(collapsed).strip()


def locked_prompt_marker_in(prompt: str) -> Optional[str]:
    """Return the locked/core prompt marker copied into editable text, if any."""
    lowered_prompt = str(prompt or "").lower()
    for marker in LOCKED_PROMPT_MARKERS:
        if marker.lower() in lowered_prompt:
            return marker
    return None


def reject_locked_prompt_markers(prompt: str, *, target: str) -> None:
    """Reject editable prompt text that copies locked/generated contracts."""
    marker = locked_prompt_marker_in(prompt)
    if marker:
        raise ValueError(
            f"{target} targets editable curator-authored prompt text only. "
            "Locked core/generated prompt contracts cannot be edited or copied."
        )


def normalize_custom_overlay_for_parent(
    parent_agent_key: Optional[str],
    custom_prompt: Optional[str],
    *,
    group_id: str | List[str] | None = None,
) -> CustomOverlayNormalization:
    """Remove copied parent/core layers from custom prompts when exact and safe.

    Custom agents store curator-authored overlay instructions in `instructions`.
    Older rows and clone flows may contain a copy of the system/base prompt there.
    Exact parent layer copies are removed from the overlay so final prompt assembly
    cannot duplicate locked contracts; ambiguous partial copies are flagged.
    """

    original = str(custom_prompt or "")
    content = original
    removed_layer_kinds: List[str] = []
    parent_key = str(parent_agent_key or "").strip()
    if not parent_key or not content.strip():
        return CustomOverlayNormalization(
            content=_collapse_prompt_whitespace(content),
            status="clean",
            removed_layer_kinds=[],
        )

    try:
        bundle = build_agent_prompt_layers(parent_key, group_id=group_id)
    except Exception as exc:
        logger.warning(
            "Could not resolve parent prompt layers for custom-agent prompt cleanup.",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={
                "parent_agent_key": parent_key,
                "group_id": group_id,
            },
        )
        return CustomOverlayNormalization(
            content=_collapse_prompt_whitespace(content),
            status="needs_review",
            removed_layer_kinds=[],
            warning="Parent prompt layers could not be resolved for overlay cleanup.",
        )

    for layer in bundle.layers:
        if layer.kind in {"curator_overlay", "runtime_context"}:
            continue
        layer_content = str(layer.content or "").strip()
        if not layer_content or layer_content not in content:
            continue
        content = content.replace(layer_content, "\n\n")
        removed_layer_kinds.append(layer.kind)

    normalized_content = _collapse_prompt_whitespace(content)
    if locked_prompt_marker_in(normalized_content):
        return CustomOverlayNormalization(
            content=normalized_content,
            status="needs_review",
            removed_layer_kinds=removed_layer_kinds,
            warning=(
                "Custom-agent prompt still contains locked/core prompt markers after "
                "safe cleanup."
            ),
        )

    if removed_layer_kinds:
        return CustomOverlayNormalization(
            content=normalized_content,
            status="deduplicated",
            removed_layer_kinds=removed_layer_kinds,
        )

    return CustomOverlayNormalization(
        content=normalized_content,
        status="clean",
        removed_layer_kinds=[],
    )


def _normalize_editable_custom_prompt(
    parent_agent_key: Optional[str],
    custom_prompt: Optional[str],
    *,
    target: str,
) -> str:
    """Apply the canonical parent-aware policy for stored editable prompts."""
    normalization = normalize_custom_overlay_for_parent(
        parent_agent_key,
        custom_prompt,
    )
    if normalization.status == "needs_review":
        raise ValueError(
            normalization.warning
            or f"{target} contains copied locked/core prompt text."
        )
    reject_locked_prompt_markers(normalization.content, target=target)
    return normalization.content


def _read_group_prompt_overrides(agent_obj: Any) -> Dict[str, str]:
    """Read canonical group overrides from an agent-like object."""
    return normalize_group_prompt_overrides(
        getattr(agent_obj, "group_prompt_overrides", None)
    )


def _write_group_prompt_overrides(agent_obj: Any, overrides: Dict[str, str]) -> None:
    """Write canonical group overrides back to an agent-like object."""
    agent_obj.group_prompt_overrides = dict(overrides)


def make_custom_agent_id(custom_agent_uuid: uuid.UUID | str) -> str:
    """Build runtime agent ID format used by flows and palette."""
    return f"{CUSTOM_AGENT_PREFIX}{str(custom_agent_uuid)}"


def parse_custom_agent_id(agent_id: str) -> Optional[uuid.UUID]:
    """Parse `ca_<uuid>` runtime IDs to UUID."""
    if not agent_id or not agent_id.startswith(CUSTOM_AGENT_PREFIX):
        return None
    raw_uuid = agent_id[len(CUSTOM_AGENT_PREFIX):]
    try:
        return uuid.UUID(raw_uuid)
    except Exception:
        return None


def normalize_group_prompt_overrides(
    group_prompt_overrides: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Normalize group override payloads to a clean GROUP_ID -> prompt map."""
    if not group_prompt_overrides:
        return {}

    normalized: Dict[str, str] = {}
    for raw_group_id, raw_prompt in group_prompt_overrides.items():
        if raw_group_id is None:
            continue
        group_id = str(raw_group_id).strip().upper()
        if not group_id:
            continue
        prompt = str(raw_prompt or "")
        if not prompt.strip():
            # Empty overrides are treated as "no override" and omitted.
            continue
        normalized[group_id] = prompt

    return normalized


def normalize_editable_group_prompt_overrides(
    group_prompt_overrides: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Normalize and validate editable group prompt override payloads."""
    normalized = normalize_group_prompt_overrides(group_prompt_overrides)
    for group_id, prompt in normalized.items():
        reject_locked_prompt_markers(
            prompt,
            target=f"Group prompt override '{group_id}'",
        )
    return normalized


def _dedupe_tool_ids(tool_ids: List[str]) -> List[str]:
    """Return tool IDs in first-seen order after trimming blanks."""
    deduped: List[str] = []
    seen: set[str] = set()
    for tool_id in tool_ids:
        normalized = str(tool_id).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _builder_finalization_tool_ids() -> set[str]:
    from src.lib.openai_agents.streaming_tools import builder_finalization_tool_names

    return set(builder_finalization_tool_names())


def _tool_policy_by_key(db: Session) -> Dict[str, Any]:
    return {
        entry.tool_key: entry
        for entry in get_tool_policy_cache().list_all(db)
    }


def _system_managed_tool_ids(db: Session, tool_ids: List[str]) -> List[str]:
    """Tools inherited from system templates that curators cannot attach manually."""
    policy_by_key = _tool_policy_by_key(db)
    builder_finalization_tool_ids = _builder_finalization_tool_ids()
    managed: List[str] = []
    for tool_id in _dedupe_tool_ids(tool_ids):
        if (
            tool_id not in _SYSTEM_MANAGED_INHERITED_TOOL_IDS
            and tool_id not in builder_finalization_tool_ids
        ):
            continue
        policy = policy_by_key.get(tool_id)
        if policy is None or not policy.allow_attach:
            managed.append(tool_id)
    return managed


def _merge_system_managed_tool_ids(
    requested_tool_ids: List[str],
    inherited_tool_ids: List[str],
) -> List[str]:
    return _dedupe_tool_ids([*requested_tool_ids, *inherited_tool_ids])


def _validate_requested_tool_ids(
    db: Session,
    tool_ids: Optional[List[str]],
    inherited_tool_ids: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """Validate requested tool attachments against DB tool policies."""
    if tool_ids is None:
        return None

    normalized = _dedupe_tool_ids(tool_ids)
    if not normalized:
        return []

    policy_by_key = _tool_policy_by_key(db)
    builder_finalization_tool_ids = _builder_finalization_tool_ids()
    inherited_system_managed = {
        tool_id
        for tool_id in _dedupe_tool_ids(inherited_tool_ids or [])
        if tool_id in _SYSTEM_MANAGED_INHERITED_TOOL_IDS
        or tool_id in builder_finalization_tool_ids
    }
    unknown = sorted({
        tool_id
        for tool_id in normalized
        if tool_id not in policy_by_key and tool_id not in inherited_system_managed
    })
    if unknown:
        raise ValueError(f"Unknown tool_ids: {', '.join(unknown)}")

    disallowed = sorted(
        {
            tool_id
            for tool_id in normalized
            if tool_id in policy_by_key
            and not policy_by_key[tool_id].allow_attach
            and tool_id not in inherited_system_managed
        }
    )
    if disallowed:
        raise ValueError(f"Tool(s) are not attachable: {', '.join(disallowed)}")

    unbound = sorted({
        tool_id
        for tool_id in normalized
        if tool_id not in inherited_system_managed and not has_tool_binding(tool_id)
    })
    if unbound:
        raise ValueError(f"Tool(s) have no installed binding: {', '.join(unbound)}")

    return normalized


def _validate_envelope_output_requires_finalize_tool(
    *,
    output_schema_key: Optional[str],
    tool_ids: List[str],
) -> None:
    output_schema = str(output_schema_key or "").strip()
    if not output_schema:
        return

    builder_finalize_tools = sorted(
        set(_dedupe_tool_ids(tool_ids)) & _builder_finalization_tool_ids()
    )
    if builder_finalize_tools:
        return

    raise ValueError(
        "Agents using an envelope output schema must include a builder finalize "
        f"tool before saving. Output schema '{output_schema}' has no finalize_* "
        "tool in tool_ids; add the appropriate builder-finalization tool or clear "
        "the output schema."
    )


def _normalize_output_schema_key(value: Optional[str]) -> Optional[str]:
    """Normalize the persisted no-output contract to ``None``."""

    normalized = str(value or "").strip()
    return normalized or None


def _validate_model_id(model_id: str) -> str:
    """Validate model selection against the configured model catalog."""
    normalized = str(model_id or "").strip()
    if not normalized:
        raise ValueError("model_id is required")
    model_def = get_model(normalized)
    if model_def is None:
        raise ValueError(f"Unknown model_id: {normalized}")
    if not bool(getattr(model_def, "curator_visible", True)):
        raise ValueError(f"Model is not selectable in Agent Workshop: {normalized}")
    return normalized


def _agent_validation_sources(
    db: Session,
    *,
    model_id: str,
    tool_ids: List[str],
    output_schema_key: Optional[str],
    prevalidated_tool_ids: Optional[List[str]] = None,
    trusted_output_schema_keys: Optional[List[str]] = None,
) -> AgentValidationSources:
    """Capture the live read-only catalogs used by canonical draft validation."""

    model_def = get_model(model_id)
    models = {}
    if model_def is not None:
        supports_reasoning = bool(getattr(model_def, "supports_reasoning", True))
        reasoning_options = tuple(
            getattr(model_def, "reasoning_options", ()) or ()
        )
        if supports_reasoning and not hasattr(model_def, "reasoning_options"):
            reasoning_options = ("minimal", "low", "medium", "high", "xhigh")
        models[model_id] = AgentModelValidationRecord(
            model_id=model_id,
            curator_visible=bool(getattr(model_def, "curator_visible", True)),
            supports_reasoning=supports_reasoning,
            reasoning_options=reasoning_options,
        )

    prevalidated = set(_dedupe_tool_ids(prevalidated_tool_ids or []))
    unresolved_tool_ids = [tool_id for tool_id in tool_ids if tool_id not in prevalidated]
    policies = _tool_policy_by_key(db) if unresolved_tool_ids else {}
    system_managed = (
        set(_system_managed_tool_ids(db, unresolved_tool_ids))
        if unresolved_tool_ids
        else set()
    )
    tools = {}
    for tool_id in _dedupe_tool_ids(tool_ids):
        policy = policies.get(tool_id)
        tools[tool_id] = AgentToolValidationRecord(
            tool_id=tool_id,
            attachable=bool(tool_id in prevalidated or (policy and policy.allow_attach)),
            installed=bool(
                tool_id in prevalidated
                or has_tool_binding(tool_id)
                or tool_id in system_managed
            ),
            system_managed=tool_id in system_managed,
        )

    normalized_schema = str(output_schema_key or "").strip()
    trusted_schemas = set(trusted_output_schema_keys or [])
    output_schema_keys = frozenset(
        [normalized_schema]
        if normalized_schema
        and (
            normalized_schema in trusted_schemas
            or resolve_output_schema(normalized_schema) is not None
        )
        else []
    )
    return AgentValidationSources(
        models=models,
        tools=tools,
        output_schema_keys=output_schema_keys,
        group_ids=frozenset(get_valid_group_ids()),
        builder_finalization_tool_ids=frozenset(_builder_finalization_tool_ids()),
    )


def authorized_agent_validation_sources(db, *, user_id, active_group_ids, sources, inherited_tool_ids=()):
    """Intersect canonical sources with the current authenticated authoring catalog."""
    from dataclasses import replace
    from src.lib.agent_studio.capability_catalog import CapabilityCatalogContext, build_authorized_capability_catalog
    records = build_authorized_capability_catalog(
        db=db, context=CapabilityCatalogContext(
            user_id=user_id, active_group_ids=tuple(active_group_ids),
            active_tab="agent_workshop", artifact_kind="agent",
        ),
    )
    available = {
        kind: {record.resource_id for record in records
               if record.kind == kind and record.selectable and record.availability == "available"}
        for kind in ("model", "tool", "output_contract", "group")
    }
    inherited = set(_system_managed_tool_ids(db, list(inherited_tool_ids))) if inherited_tool_ids else set()
    return replace(
        sources,
        models={key: value for key, value in sources.models.items() if key in available["model"]},
        tools={key: replace(value, system_managed=key in inherited or value.system_managed)
               for key, value in sources.tools.items() if key in available["tool"] or key in inherited},
        output_schema_keys=sources.output_schema_keys & available["output_contract"],
        group_ids=sources.group_ids & available["group"],
    )


def _require_valid_custom_agent_draft(
    db: Session,
    *,
    user_id: int,
    active_group_ids: Optional[List[str]],
    candidate: Dict[str, Any],
    prevalidated_tool_ids: Optional[List[str]] = None,
    trusted_output_schema_keys: Optional[List[str]] = None,
) -> None:
    """Apply the canonical complete-draft contract before any ORM mutation."""

    try:
        sources = _agent_validation_sources(
            db, model_id=str(candidate.get("model_id") or ""),
            tool_ids=list(candidate.get("tool_ids") or []),
            output_schema_key=candidate.get("output_schema_key"),
            prevalidated_tool_ids=prevalidated_tool_ids,
            trusted_output_schema_keys=trusted_output_schema_keys,
        )
        if active_group_ids is not None:
            sources = authorized_agent_validation_sources(
                db, user_id=user_id, active_group_ids=active_group_ids, sources=sources,
                inherited_tool_ids=prevalidated_tool_ids or (),
            )
        result = validate_custom_agent_authoring_draft(
            candidate,
            context=AuthoringValidationContext.from_values(
                db_user_id=user_id,
                active_group_ids=active_group_ids,
            ),
            sources=sources,
            phase="save",
        )
    except Exception:
        raise report_authoring_validation_engine_failure(
            artifact_kind="custom_agent",
            phase="save",
        ) from None
    if not result.valid:
        raise AuthoringValidationError(result)


def _resolve_system_template_agent(
    db: Session,
    template_source: str,
    *,
    active_group_ids: Optional[List[str]] = None,
) -> CustomAgent:
    """Resolve a system template by canonical unified `agent_key` only."""
    raw_id = require_canonical_agent_identity(
        template_source,
        field_name="template_source",
    )
    if not raw_id:
        raise ValueError("template_source is required")

    by_key = db.query(CustomAgent).filter(
        CustomAgent.agent_key == raw_id,
        CustomAgent.visibility == "system",
        CustomAgent.is_active == True,  # noqa: E712
    ).first()
    if by_key:
        from src.lib.agent_access import is_resource_access_allowed

        if is_resource_access_allowed(
            visibility_allowed=True,
            allowed_group_ids=_read_allowed_group_ids(by_key),
            active_group_ids=list(active_group_ids or []),
            resource_kind="agent_template",
        ):
            return by_key

    raise ValueError(f"No active system agent found for parent id '{raw_id}'")


def _validate_inherited_access_floor(
    custom_agent: CustomAgent,
    requested_allowed_group_ids: list[str],
) -> list[str]:
    """Enforce the immutable access floor captured from a template/clone source."""

    requested = normalize_allowed_group_ids(requested_allowed_group_ids)
    return require_allowed_group_ids_narrowing(
        list(custom_agent.inherited_allowed_group_ids),
        requested,
        source_name="clone/template source",
    )


def custom_agent_name_exists(db: Session, user_id: int, name: str, *, excluding_id=None) -> bool:
    """Match the database's case-insensitive active custom-name uniqueness rule."""
    query = db.query(CustomAgent).filter(
        CustomAgent.user_id == user_id,
        func.lower(CustomAgent.name) == name.lower(),
        CustomAgent.visibility.in_(["private", "project"]),
        CustomAgent.is_active == True,  # noqa: E712
    )
    if excluding_id is not None:
        query = query.filter(CustomAgent.id != excluding_id)
    return query.first() is not None


def _has_active_custom_name(db: Session, user_id: int, name: str) -> bool:
    """Case-insensitive active-name check for a user's private/project custom agents."""
    return db.query(CustomAgent).filter(
        CustomAgent.user_id == user_id,
        func.lower(CustomAgent.name) == name.lower(),
        CustomAgent.visibility.in_(["private", "project"]),
        CustomAgent.is_active == True,  # noqa: E712
    ).first() is not None


def _get_primary_project_id_for_user(db: Session, user_id: int) -> uuid.UUID:
    """Resolve the first project membership for a user (v1 has one default project)."""
    row = db.query(ProjectMember.project_id).filter(
        ProjectMember.user_id == user_id,
    ).order_by(ProjectMember.joined_at.asc()).first()
    if not row:
        raise ValueError("User is not assigned to any project")
    return row[0]


def _generate_clone_name(db: Session, user_id: int, source_name: str) -> str:
    """Generate a non-colliding clone name for a user."""
    base_name = (source_name or "Custom Agent").strip() or "Custom Agent"
    candidate = f"{base_name} (Copy)"
    if not _has_active_custom_name(db, user_id, candidate):
        return candidate

    suffix = 2
    while True:
        next_candidate = f"{base_name} (Copy {suffix})"
        if not _has_active_custom_name(db, user_id, next_candidate):
            return next_candidate
        suffix += 1


def _prepare_execution_update(db, agent, expected_revision_id, expected_updated_at, active_group_ids):
    from src.lib.agent_studio.execution_revision_service import (
        ExecutionRevisionConflictError, get_execution_revision,
    )

    if expected_revision_id is None and expected_updated_at is None:
        raise ExecutionRevisionConflictError("An expected agent revision is required before saving")
    db.refresh(agent, with_for_update=True)
    if expected_revision_id is not None and agent.execution_revision_id != expected_revision_id:
        raise ExecutionRevisionConflictError("This agent changed since it was opened. Reopen it before saving.")
    if expected_updated_at is not None:
        expected = expected_updated_at.replace(tzinfo=expected_updated_at.tzinfo or timezone.utc)
        actual = agent.updated_at.replace(tzinfo=agent.updated_at.tzinfo or timezone.utc)
        if expected != actual:
            raise ExecutionRevisionConflictError("This agent changed since it was opened. Reopen it before saving.")
    if agent.execution_revision_id is None:
        raise ValueError("Custom agent has no executable baseline; complete the database migration")
    _, saved = get_execution_revision(
        db, agent.id, agent.execution_revision_id, agent.user_id,
        active_group_ids=list(active_group_ids or []),
    )
    return agent.execution_revision_id, saved


def _selected_output_schema(output_contract, new_generic_profile, schema, schema_provided):
    if output_contract is not None and new_generic_profile is not None:
        raise ValueError("Select an existing output contract or create a profile, not both")
    if (output_contract is not None or new_generic_profile is not None) and schema_provided:
        raise ValueError("Use one output transition, not output_contract and output_schema_key together")
    if output_contract is not None:
        return AgentOutputContract.model_validate(output_contract).output_schema_key
    if new_generic_profile is not None:
        return None
    return schema


def _record_execution_save(
    db, agent, *, expected_revision_id, output_contract=None, new_generic_profile=None,
    previous_output=None, previous_snapshot=None, schema_provided=False, notes=None,
    active_group_ids=(),
):
    from src.lib.agent_studio.execution_revision_service import append_execution_revision
    from src.lib.agent_studio.execution_snapshot import capture_execution_snapshot
    from src.lib.agent_studio.generic_profile_service import create_profile

    if new_generic_profile is not None:
        profile, revision = create_profile(
            db, agent.user_id, new_generic_profile,
            visibility=agent.visibility, project_id=agent.project_id,
            active_group_ids=active_group_ids,
        )
        selected = AgentOutputContract(
            output_state="structured_extraction", output_mode="profile_bound_generic",
            generic_profile_ref=GenericProfilePin(
                profile_id=profile.id, profile_revision_id=revision.id,
                revision=revision.revision, fingerprint=revision.fingerprint,
            ),
        )
    elif output_contract is not None:
        selected = AgentOutputContract.model_validate(output_contract)
    elif schema_provided or previous_output is None:
        selected = initial_output_contract(agent.output_schema_key)
    else:
        selected = previous_output
    saved = capture_execution_snapshot(db, agent, selected)
    if previous_snapshot is not None:
        # Inherited policy belongs to the saved agent, not today's parent/tool
        # catalog. New selectable tools are validated separately at this save.
        saved = saved.model_copy(update={
            "system_managed_tool_ids": list(previous_snapshot.system_managed_tool_ids),
            "group_tool_policy": previous_snapshot.group_tool_policy,
        })
    return append_execution_revision(
        db, agent, saved, user_id=agent.user_id, expected_revision_id=expected_revision_id,
        notes=notes,
        allow_archived_profile=(
            previous_output is not None
            and previous_output.generic_profile_ref is not None
            and previous_output.generic_profile_ref == selected.generic_profile_ref
        ),
    )


def create_custom_agent(
    db: Session,
    user_id: int,
    name: str,
    template_source: Optional[str] = None,
    custom_prompt: Optional[str] = None,
    group_prompt_overrides: Optional[Dict[str, str]] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    include_group_rules: bool = True,
    model_id: Optional[str] = None,
    tool_ids: Optional[List[str]] = None,
    output_schema_key: Optional[str] = None,
    output_schema_key_provided: bool = False,
    category: Optional[str] = None,
    model_temperature: Optional[float] = None,
    model_reasoning: Optional[str] = None,
    model_reasoning_provided: bool = False,
    allowed_group_ids: Optional[List[str]] = None,
    inherited_allowed_group_ids: Optional[List[str]] = None,
    inherited_group_tool_policy: Optional[Dict[str, Any]] = None,
    active_group_ids: Optional[List[str]] = None,
    visibility: str = "private",
    output_contract: AgentOutputContract | None = None,
    new_generic_profile: GenericProfileContract | None = None,
) -> CustomAgent:
    """Create a new custom agent and seed version snapshot."""
    selected_template_key = str(template_source or "").strip()
    parent_defaults: Dict[str, Any] = {}
    parent_agent_key: Optional[str] = None

    if selected_template_key:
        parent_template = _resolve_system_template_agent(
            db,
            selected_template_key,
            active_group_ids=active_group_ids,
        )
        parent_agent_key = parent_template.agent_key
        parent_defaults = {
            "model_id": parent_template.model_id,
            "model_temperature": float(
                parent_template.model_temperature
                if parent_template.model_temperature is not None else 0.1
            ),
            "model_reasoning": parent_template.model_reasoning,
            "tool_ids": list(parent_template.tool_ids or []),
            "output_schema_key": parent_template.output_schema_key,
            "category": parent_template.category,
            "allowed_group_ids": _read_allowed_group_ids(parent_template),
            "group_tool_policy": parse_group_tool_policy(
                getattr(parent_template, "group_tool_policy", {}) or {},
                field_name=f"Template '{parent_agent_key}' group_tool_policy",
            ).to_dict(),
        }
    else:
        if not str(model_id or "").strip():
            raise ValueError("model_id is required when template_source is not provided")
        parent_defaults = {
            "model_id": str(model_id).strip(),
            "model_temperature": 0.1,
            "model_reasoning": None,
            "tool_ids": [],
            "output_schema_key": None,
            "category": "Custom",
            "allowed_group_ids": [],
            "group_tool_policy": {},
        }

    agent_prompt = _normalize_editable_custom_prompt(
        parent_agent_key,
        custom_prompt,
        target="Custom agent main prompt",
    )
    normalized_group_overrides = normalize_editable_group_prompt_overrides(group_prompt_overrides)
    normalized_allowed_group_ids = normalize_allowed_group_ids(
        allowed_group_ids
        if allowed_group_ids is not None
        else list(parent_defaults["allowed_group_ids"]),
    )
    normalized_allowed_group_ids = require_allowed_group_ids_narrowing(
        list(parent_defaults["allowed_group_ids"]),
        normalized_allowed_group_ids,
        source_name=(
            f"template '{parent_agent_key}'" if parent_agent_key else "scratch source"
        ),
    )
    normalized_inherited_allowed_group_ids = normalize_allowed_group_ids(
        inherited_allowed_group_ids
        if inherited_allowed_group_ids is not None
        else list(parent_defaults["allowed_group_ids"]),
        field_name="inherited_allowed_group_ids",
    )
    normalized_allowed_group_ids = require_allowed_group_ids_narrowing(
        normalized_inherited_allowed_group_ids,
        normalized_allowed_group_ids,
        source_name="clone/template source",
    )
    normalized_group_tool_policy = parse_group_tool_policy(
        inherited_group_tool_policy
        if inherited_group_tool_policy is not None
        else parent_defaults["group_tool_policy"],
        field_name="inherited_group_tool_policy",
    ).to_dict()
    custom_uuid = uuid.uuid4()

    effective_model_id = _validate_model_id(model_id or parent_defaults["model_id"] or "")

    parent_tool_ids = list(parent_defaults["tool_ids"] or [])
    requested_tool_ids = _validate_requested_tool_ids(
        db,
        tool_ids,
        inherited_tool_ids=parent_tool_ids,
    )
    if requested_tool_ids is not None:
        inherited_system_tool_ids = _system_managed_tool_ids(db, parent_tool_ids)
        effective_tool_ids = _merge_system_managed_tool_ids(
            requested_tool_ids,
            inherited_system_tool_ids,
        )
    else:
        effective_tool_ids = parent_tool_ids
    effective_output_schema_key = _normalize_output_schema_key(
        _selected_output_schema(
            output_contract, new_generic_profile,
            output_schema_key if output_schema_key_provided or output_schema_key is not None
            else parent_defaults["output_schema_key"],
            output_schema_key_provided or output_schema_key is not None,
        )
    )
    _validate_envelope_output_requires_finalize_tool(
        output_schema_key=effective_output_schema_key,
        tool_ids=list(effective_tool_ids),
    )
    effective_model_temperature = float(
        model_temperature
        if model_temperature is not None
        else parent_defaults["model_temperature"]
    )
    effective_model_reasoning = (
        model_reasoning
        if model_reasoning_provided or model_reasoning is not None
        else parent_defaults["model_reasoning"]
    )
    effective_category = (
        category if category is not None else parent_defaults["category"]
    )
    _require_valid_custom_agent_draft(
        db,
        user_id=user_id,
        active_group_ids=active_group_ids,
        candidate={
            "name": name,
            "description": description,
            "custom_prompt": agent_prompt,
            "group_prompt_overrides": normalized_group_overrides,
            "icon": icon or "\U0001F527",
            "visibility": visibility,
            "allowed_group_ids": normalized_allowed_group_ids,
            "inherited_allowed_group_ids": normalized_inherited_allowed_group_ids,
            "include_group_rules": include_group_rules,
            "model_id": effective_model_id,
            "model_reasoning": effective_model_reasoning,
            "model_temperature": effective_model_temperature,
            "tool_ids": list(effective_tool_ids),
            "output_schema_key": effective_output_schema_key,
            "category": effective_category,
        },
        prevalidated_tool_ids=list(effective_tool_ids),
        trusted_output_schema_keys=(
            [str(parent_defaults["output_schema_key"])]
            if parent_defaults.get("output_schema_key")
            else []
        ),
    )

    custom_agent = CustomAgent(
        id=custom_uuid,
        agent_key=make_custom_agent_id(custom_uuid),
        user_id=user_id,
        visibility="private",
        name=name,
        description=description,
        instructions=agent_prompt,
        model_id=effective_model_id,
        model_temperature=effective_model_temperature,
        model_reasoning=effective_model_reasoning,
        tool_ids=list(effective_tool_ids),
        group_tool_policy=normalized_group_tool_policy,
        output_schema_key=effective_output_schema_key,
        group_rules_enabled=include_group_rules,
        group_rules_component=parent_agent_key,
        group_prompt_overrides=normalized_group_overrides,
        allowed_group_ids=normalized_allowed_group_ids,
        inherited_allowed_group_ids=normalized_inherited_allowed_group_ids,
        icon=(icon or "\U0001F527"),
        category=effective_category,
        template_source=parent_agent_key,
        supervisor_enabled=False,
        supervisor_batchable=False,
        show_in_palette=True,
        version=1,
        is_active=True,
    )

    if custom_agent_name_exists(db, user_id, name):
        raise ValueError("A custom agent with this name already exists")

    set_custom_agent_visibility(db, custom_agent, user_id, visibility)
    db.add(custom_agent)
    db.flush()

    _record_execution_save(
        db, custom_agent, expected_revision_id=None,
        output_contract=output_contract, new_generic_profile=new_generic_profile,
        active_group_ids=active_group_ids,
    )

    return custom_agent


def get_custom_agent_for_user(
    db: Session,
    custom_agent_uuid: uuid.UUID,
    user_id: int,
    include_inactive: bool = False,
) -> CustomAgent:
    """Fetch custom agent with ownership check."""
    query = db.query(CustomAgent).filter(CustomAgent.id == custom_agent_uuid)
    if not include_inactive:
        query = query.filter(CustomAgent.is_active == True)  # noqa: E712
    query = query.filter(
        CustomAgent.visibility.in_(["private", "project"]),
        CustomAgent.agent_key.like(f"{CUSTOM_AGENT_PREFIX}%"),
    )
    custom_agent = query.first()
    if not custom_agent:
        raise CustomAgentNotFoundError(f"Custom agent '{custom_agent_uuid}' not found")
    if custom_agent.user_id != user_id:
        raise CustomAgentAccessError(
            "You do not have permission to access this custom agent"
        )
    return custom_agent


def get_custom_agent_visible_to_user(
    db: Session,
    custom_agent_uuid: uuid.UUID,
    user_id: int,
    include_inactive: bool = False,
) -> CustomAgent:
    """Fetch custom agent visible to user (owner private + project-shared)."""
    query = db.query(CustomAgent).filter(CustomAgent.id == custom_agent_uuid)
    if not include_inactive:
        query = query.filter(CustomAgent.is_active == True)  # noqa: E712
    query = query.filter(
        CustomAgent.visibility.in_(["private", "project"]),
        CustomAgent.agent_key.like(f"{CUSTOM_AGENT_PREFIX}%"),
    )
    custom_agent = query.first()
    if not custom_agent:
        raise CustomAgentNotFoundError(f"Custom agent '{custom_agent_uuid}' not found")

    if custom_agent.visibility == "private":
        if custom_agent.user_id != user_id:
            raise CustomAgentAccessError(
                "You do not have permission to access this custom agent"
            )
        return custom_agent

    project_ids = get_project_ids_for_user(db, user_id)
    if not custom_agent.project_id or custom_agent.project_id not in project_ids:
        raise CustomAgentAccessError(
            "You do not have permission to access this custom agent"
        )
    return custom_agent


def list_custom_agents_for_user(
    db: Session,
    user_id: int,
    template_source: Optional[str] = None,
) -> List[CustomAgent]:
    """List active custom agents for a user, optionally filtered by template source."""
    query = db.query(CustomAgent).filter(
        CustomAgent.user_id == user_id,
        CustomAgent.is_active == True,  # noqa: E712
        CustomAgent.visibility.in_(["private", "project"]),
        CustomAgent.agent_key.like(f"{CUSTOM_AGENT_PREFIX}%"),
    )
    if template_source:
        query = query.filter(
            CustomAgent.template_source == str(template_source).strip()
        )

    return query.order_by(
        CustomAgent.updated_at.desc(),
        CustomAgent.created_at.desc(),
    ).all()


def list_custom_agents_visible_to_user(
    db: Session,
    user_id: int,
    template_source: Optional[str] = None,
) -> List[CustomAgent]:
    """List active custom agents visible to user (own + project-shared)."""
    project_ids = list(get_project_ids_for_user(db, user_id))

    visibility_filters = [
        and_(CustomAgent.visibility == "private", CustomAgent.user_id == user_id),
    ]
    if project_ids:
        visibility_filters.append(
            and_(
                CustomAgent.visibility == "project",
                CustomAgent.project_id.in_(project_ids),
            )
        )

    query = db.query(CustomAgent).filter(
        CustomAgent.is_active == True,  # noqa: E712
        CustomAgent.visibility.in_(["private", "project"]),
        CustomAgent.agent_key.like(f"{CUSTOM_AGENT_PREFIX}%"),
        or_(*visibility_filters),
    )
    if template_source:
        query = query.filter(CustomAgent.template_source == str(template_source).strip())

    return query.order_by(
        CustomAgent.updated_at.desc(),
        CustomAgent.created_at.desc(),
    ).all()


def set_custom_agent_visibility(
    db: Session,
    custom_agent: CustomAgent,
    user_id: int,
    visibility: str,
) -> CustomAgent:
    """Set owner-controlled visibility for a custom agent."""
    if custom_agent.user_id != user_id:
        raise CustomAgentAccessError(
            "You do not have permission to modify this custom agent"
        )

    target_visibility = str(visibility or "").strip().lower()
    if target_visibility not in {"private", "project"}:
        raise ValueError("visibility must be 'private' or 'project'")

    if target_visibility == "private":
        custom_agent.visibility = "private"
        custom_agent.project_id = None
        custom_agent.shared_at = None
        return custom_agent

    project_id = _get_primary_project_id_for_user(db, user_id)
    custom_agent.visibility = "project"
    custom_agent.project_id = project_id
    custom_agent.shared_at = datetime.now(timezone.utc)
    return custom_agent


def clone_saved_custom_agent(
    db: Session, user_id: int, source: CustomAgent, *, name: str,
    allowed_group_ids: Optional[List[str]] = None,
    active_group_ids: Optional[List[str]] = None,
    visibility: str = "private", edits: Optional[Dict[str, Any]] = None,
) -> CustomAgent:
    """Clone an exact executable head, not a reconstruction from mutable fields."""
    from copy import deepcopy
    from src.lib.agent_studio.execution_revision_service import (
        append_execution_revision, get_execution_revision,
    )
    from src.schemas.agent_execution_revision import AgentExecutionSnapshot

    if source.execution_revision_id is None:
        raise ValueError("Clone source has no executable baseline")
    _, saved = get_execution_revision(
        db, source.id, source.execution_revision_id, user_id,
        active_group_ids=list(active_group_ids or []),
    )
    allowed = require_allowed_group_ids_narrowing(
        saved.allowed_group_ids,
        saved.allowed_group_ids if allowed_group_ids is None else allowed_group_ids,
        source_name="saved clone source",
    )
    data = saved.model_dump(mode="json")
    data["allowed_group_ids"] = allowed
    data["inherited_allowed_group_ids"] = list(saved.allowed_group_ids)
    cloned_snapshot = AgentExecutionSnapshot.model_validate(data)
    if custom_agent_name_exists(db, user_id, name):
        raise ValueError("A custom agent with this name already exists")
    clone_id = uuid.uuid4()
    clone = CustomAgent(
        id=clone_id, agent_key=make_custom_agent_id(clone_id), user_id=user_id,
        name=name, description=source.description, icon=source.icon, category=source.category,
        is_active=True, visibility="private", version=1,
        supervisor_enabled=False, supervisor_batchable=False, show_in_palette=True,
        output_schema_key=saved.output_contract.output_schema_key,
        **{
            field: deepcopy(getattr(cloned_snapshot, field))
            for field in (
                "model_id", "model_temperature", "model_reasoning", "instructions",
                "tool_ids", "group_tool_policy", "allowed_group_ids",
                "inherited_allowed_group_ids", "group_rules_enabled",
                "group_rules_component", "group_prompt_overrides", "template_source",
            )
        },
    )
    set_custom_agent_visibility(db, clone, user_id, visibility)
    db.add(clone)
    db.flush()
    # Keep original prompt manifests/provenance and full resolved contracts. No
    # parent lookup, prompt normalization, or second editable profile copy.
    append_execution_revision(
        db, clone, cloned_snapshot, user_id=user_id, expected_revision_id=None,
    )
    changed = {}
    field_names = {"custom_prompt": "instructions", "include_group_rules": "group_rules_enabled"}
    for key, value in (edits or {}).items():
        if key == "template_source":
            if value != saved.template_source:
                raise ValueError("The clone template changed; reopen the source")
            continue
        if key == "category":
            clone.category = value
            continue
        if key in {"output_contract", "new_generic_profile"}:
            if key == "output_contract" and AgentOutputContract.model_validate(value) == saved.output_contract:
                continue
            changed[key] = value
        elif value != getattr(clone, field_names.get(key, key)):
            changed[key] = value
    if changed:
        update_custom_agent(
            db, clone, expected_revision_id=clone.execution_revision_id,
            active_group_ids=active_group_ids,
            output_schema_key_provided="output_schema_key" in changed,
            model_reasoning_provided="model_reasoning" in changed,
            allow_empty_tool_ids=changed.get("tool_ids") == [],
            **changed,
        )
    return clone


def clone_visible_agent_for_user(
    db: Session,
    user_id: int,
    source_agent_key: str,
    name: Optional[str] = None,
    allowed_group_ids: Optional[List[str]] = None,
    active_group_ids: Optional[List[str]] = None,
) -> CustomAgent:
    """Clone any user-visible agent (system/private/project) into user's private space."""
    source_key = str(source_agent_key or "").strip()
    if not source_key:
        raise ValueError("source_agent_id is required")

    source_agent = get_agent_by_key(
        db,
        source_key,
        user_id=user_id,
        active_group_ids=active_group_ids,
    )
    if source_agent is None:
        raise CustomAgentNotFoundError(f"Agent '{source_key}' not found")
    if source_agent.visibility not in {"system", "private", "project"}:
        raise ValueError("Only system/private/project agents can be cloned")

    requested_name = str(name or "").strip()
    clone_name = requested_name or _generate_clone_name(db, user_id, source_agent.name)
    if _has_active_custom_name(db, user_id, clone_name):
        raise ValueError("A custom agent with this name already exists")

    if source_agent.visibility != "system":
        return clone_saved_custom_agent(
            db, user_id, source_agent, name=clone_name,
            allowed_group_ids=allowed_group_ids, active_group_ids=active_group_ids,
        )

    template_source = str(source_agent.template_source or "").strip() or (
        source_agent.agent_key if source_agent.visibility == "system" else None
    )
    source_allowed_group_ids = normalize_allowed_group_ids(
        _read_allowed_group_ids(source_agent),
        field_name=f"source agent '{source_key}' allowed_group_ids",
    )
    clone_allowed_group_ids = require_allowed_group_ids_narrowing(
        source_allowed_group_ids,
        source_allowed_group_ids if allowed_group_ids is None else allowed_group_ids,
        source_name=f"source agent '{source_key}'",
    )
    return create_custom_agent(
        db=db,
        user_id=user_id,
        name=clone_name,
        template_source=template_source,
        custom_prompt="" if source_agent.visibility == "system" else source_agent.instructions,
        group_prompt_overrides=_read_group_prompt_overrides(source_agent),
        description=source_agent.description,
        icon=source_agent.icon,
        include_group_rules=bool(source_agent.group_rules_enabled),
        model_id=source_agent.model_id,
        tool_ids=list(source_agent.tool_ids or []),
        output_schema_key=source_agent.output_schema_key,
        category=source_agent.category,
        model_temperature=source_agent.model_temperature,
        model_reasoning=source_agent.model_reasoning,
        allowed_group_ids=clone_allowed_group_ids,
        inherited_allowed_group_ids=source_allowed_group_ids,
        inherited_group_tool_policy=dict(
            getattr(source_agent, "group_tool_policy", {}) or {}
        ),
        active_group_ids=active_group_ids,
    )


def update_custom_agent(
    db: Session,
    custom_agent: CustomAgent,
    expected_updated_at: Optional[datetime] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    custom_prompt: Optional[str] = None,
    group_prompt_overrides: Optional[Dict[str, str]] = None,
    icon: Optional[str] = None,
    include_group_rules: Optional[bool] = None,
    notes: Optional[str] = None,
    model_id: Optional[str] = None,
    model_temperature: Optional[float] = None,
    model_reasoning: Optional[str] = None,
    model_reasoning_provided: bool = False,
    tool_ids: Optional[List[str]] = None,
    output_schema_key: Optional[str] = None,
    output_schema_key_provided: bool = False,
    allow_empty_tool_ids: bool = False,
    allowed_group_ids: Optional[List[str]] = None,
    active_group_ids: Optional[List[str]] = None,
    visibility: Optional[str] = None,
    expected_revision_id: uuid.UUID | None = None,
    output_contract: AgentOutputContract | None = None,
    new_generic_profile: GenericProfileContract | None = None,
) -> CustomAgent:
    """Save a complete new executable revision with inherited policy preserved."""
    previous_revision_id, previous_snapshot = _prepare_execution_update(
        db, custom_agent, expected_revision_id, expected_updated_at, active_group_ids,
    )
    previous_output = previous_snapshot.output_contract
    if name is not None and custom_agent_name_exists(
        db, custom_agent.user_id, name, excluding_id=custom_agent.id,
    ):
        raise ValueError("A custom agent with this name already exists")

    current_group_overrides = _read_group_prompt_overrides(custom_agent)
    next_group_overrides: Optional[Dict[str, str]] = None
    if group_prompt_overrides is not None:
        next_group_overrides = normalize_editable_group_prompt_overrides(group_prompt_overrides)
    next_custom_prompt = custom_prompt
    if custom_prompt is not None:
        next_custom_prompt = _normalize_editable_custom_prompt(
            getattr(custom_agent, "template_source", None),
            custom_prompt,
            target="Custom agent main prompt",
        )

    prompt_changed = (
        next_custom_prompt is not None
        and next_custom_prompt != custom_agent.instructions
    )
    group_overrides_changed = (
        next_group_overrides is not None
        and next_group_overrides != current_group_overrides
    )
    current_allowed_group_ids = normalize_allowed_group_ids(
        _read_allowed_group_ids(custom_agent),
    )
    next_allowed_group_ids: Optional[list[str]] = None
    if allowed_group_ids is not None:
        next_allowed_group_ids = _validate_inherited_access_floor(
            custom_agent,
            allowed_group_ids,
        )
    allowed_group_ids_changed = (
        next_allowed_group_ids is not None
        and next_allowed_group_ids != current_allowed_group_ids
    )

    next_tool_ids = list(custom_agent.tool_ids or [])
    if tool_ids is not None:
        inherited_system_tool_ids = list(previous_snapshot.system_managed_tool_ids)
        validated_tool_ids = _validate_requested_tool_ids(
            db,
            tool_ids,
            inherited_tool_ids=inherited_system_tool_ids,
        ) or []
        next_tool_ids = _merge_system_managed_tool_ids(
            validated_tool_ids,
            inherited_system_tool_ids,
        )
        existing_tool_ids = list(custom_agent.tool_ids or [])
        if existing_tool_ids and not next_tool_ids and not allow_empty_tool_ids:
            raise ValueError(
                "Refusing to clear all tool_ids from an existing agent without "
                "explicit override. "
                "Re-attach at least one tool before saving."
            )
    next_output_schema_key = _normalize_output_schema_key(
        _selected_output_schema(
            output_contract, new_generic_profile,
            output_schema_key if output_schema_key_provided or output_schema_key is not None
            else custom_agent.output_schema_key,
            output_schema_key_provided or output_schema_key is not None,
        )
    )
    _validate_envelope_output_requires_finalize_tool(
        output_schema_key=next_output_schema_key,
        tool_ids=list(next_tool_ids),
    )
    effective_model_id = (
        _validate_model_id(model_id)
        if model_id is not None
        else custom_agent.model_id
    )
    _require_valid_custom_agent_draft(
        db,
        user_id=custom_agent.user_id,
        active_group_ids=active_group_ids,
        candidate={
            "name": name if name is not None else custom_agent.name,
            "description": (
                description
                if description is not None
                else getattr(custom_agent, "description", None)
            ),
            "custom_prompt": (
                str(next_custom_prompt)
                if next_custom_prompt is not None
                else getattr(custom_agent, "instructions", "")
            ),
            "group_prompt_overrides": (
                next_group_overrides
                if next_group_overrides is not None
                else current_group_overrides
            ),
            "icon": icon if icon is not None else getattr(custom_agent, "icon", None),
            "visibility": visibility if visibility is not None else (getattr(custom_agent, "visibility", None) or "private"),
            "allowed_group_ids": (
                next_allowed_group_ids
                if next_allowed_group_ids is not None
                else current_allowed_group_ids
            ),
            "inherited_allowed_group_ids": list(
                custom_agent.inherited_allowed_group_ids or []
            ),
            "include_group_rules": (
                include_group_rules
                if include_group_rules is not None
                else (
                    True
                    if getattr(custom_agent, "group_rules_enabled", None) is None
                    else bool(custom_agent.group_rules_enabled)
                )
            ),
            "model_id": effective_model_id,
            "model_reasoning": (
                model_reasoning
                if model_reasoning_provided or model_reasoning is not None
                else custom_agent.model_reasoning
            ),
            "model_temperature": (
                model_temperature
                if model_temperature is not None
                else custom_agent.model_temperature
            ),
            "tool_ids": next_tool_ids,
            "output_schema_key": next_output_schema_key,
            "category": getattr(custom_agent, "category", None),
        },
        prevalidated_tool_ids=(list(next_tool_ids) if tool_ids is not None else []),
    )

    if visibility is not None:
        set_custom_agent_visibility(db, custom_agent, custom_agent.user_id, visibility)
    if prompt_changed:
        custom_agent.instructions = str(next_custom_prompt)
    if group_overrides_changed and next_group_overrides is not None:
        _write_group_prompt_overrides(custom_agent, next_group_overrides)
    if allowed_group_ids_changed and next_allowed_group_ids is not None:
        custom_agent.allowed_group_ids = next_allowed_group_ids

    if name is not None:
        custom_agent.name = name
    if description is not None:
        custom_agent.description = description
    if icon is not None:
        custom_agent.icon = icon
    if include_group_rules is not None:
        custom_agent.group_rules_enabled = include_group_rules
    if model_id is not None:
        custom_agent.model_id = effective_model_id
    if model_temperature is not None:
        custom_agent.model_temperature = float(model_temperature)
    if model_reasoning_provided or model_reasoning is not None:
        custom_agent.model_reasoning = model_reasoning
    if tool_ids is not None:
        custom_agent.tool_ids = next_tool_ids
    if output_schema_key_provided or output_schema_key is not None or output_contract is not None or new_generic_profile is not None:
        custom_agent.output_schema_key = next_output_schema_key

    if prompt_changed or group_overrides_changed or allowed_group_ids_changed:
        custom_agent.version = int(custom_agent.version or 1) + 1
    _record_execution_save(
        db, custom_agent, expected_revision_id=previous_revision_id,
        output_contract=output_contract, new_generic_profile=new_generic_profile,
        previous_output=previous_output, previous_snapshot=previous_snapshot, notes=notes,
        schema_provided=output_schema_key_provided or output_schema_key is not None,
        active_group_ids=active_group_ids,
    )
    return custom_agent


def soft_delete_custom_agent(custom_agent: CustomAgent) -> None:
    """Soft delete custom agent (flow references can remain as historical data)."""
    custom_agent.is_active = False


def list_custom_agent_versions(
    db: Session,
    custom_agent_uuid: uuid.UUID,
) -> List[CustomAgentVersion]:
    """List versions newest-first."""
    return (
        db.query(CustomAgentVersion)
        .filter(CustomAgentVersion.custom_agent_id == custom_agent_uuid)
        .order_by(CustomAgentVersion.version.desc())
        .all()
    )


@dataclass
class CustomAgentRuntimeInfo:
    """Runtime data needed to execute a custom agent by `ca_<uuid>` id."""

    custom_agent_uuid: uuid.UUID
    custom_agent_id: str
    display_name: str
    instructions: str
    group_prompt_overrides: Dict[str, str]
    include_group_rules: bool
    requires_document: bool
    allowed_group_ids: List[str]


def get_custom_agent_runtime_info(
    custom_agent_id: str,
    db: Optional[Session] = None,
    *, user_id: int | None = None, active_group_ids: Optional[List[str]] = None,
) -> Optional[CustomAgentRuntimeInfo]:
    """Resolve runtime requirements from the authorized saved configuration."""
    from src.lib.group_tool_policy import resolve_group_tool_policy
    from src.lib.agent_studio.execution_revision_service import get_execution_revision, ExecutionRevisionNotFoundError

    custom_uuid = parse_custom_agent_id(custom_agent_id)
    if not custom_uuid or user_id is None:
        return None

    own_session = db is None
    if own_session:
        db = SessionLocal()
    assert db is not None

    try:
        custom_agent = db.query(CustomAgent).filter(
            CustomAgent.id == custom_uuid,
            CustomAgent.is_active == True,  # noqa: E712
            CustomAgent.visibility.in_(["private", "project"]),
            CustomAgent.agent_key == custom_agent_id,
        ).first()
        if not custom_agent or custom_agent.execution_revision_id is None:
            return None
        try:
            _, saved = get_execution_revision(
                db, custom_agent.id, custom_agent.execution_revision_id, user_id,
                active_group_ids=list(active_group_ids or []),
            )
        except ExecutionRevisionNotFoundError:
            return None
        tool_ids = resolve_group_tool_policy(saved.tool_ids, saved.group_tool_policy, active_group_ids).tool_ids
        requires_document = bool(set(tool_ids) & DOCUMENT_TOOL_IDS)

        return CustomAgentRuntimeInfo(
            custom_agent_uuid=custom_agent.id,
            custom_agent_id=make_custom_agent_id(custom_agent.id),
            display_name=custom_agent.name,
            instructions=saved.instructions,
            group_prompt_overrides=dict(saved.group_prompt_overrides),
            include_group_rules=saved.group_rules_enabled,
            requires_document=requires_document,
            allowed_group_ids=list(saved.allowed_group_ids),
        )
    finally:
        if own_session and db is not None:
            db.close()


def custom_agent_to_dict(custom_agent: CustomAgent) -> Dict[str, Any]:
    """Serialize SQL model to API-friendly dict."""
    group_prompt_overrides = _read_group_prompt_overrides(custom_agent)
    include_group_rules = bool(custom_agent.group_rules_enabled)

    overlay_normalization = normalize_custom_overlay_for_parent(
        custom_agent.template_source,
        custom_agent.instructions,
    )
    return {
        "id": str(custom_agent.id),
        "agent_id": make_custom_agent_id(custom_agent.id),
        "execution_revision_id": getattr(custom_agent, "execution_revision_id", None),
        "user_id": custom_agent.user_id,
        "template_source": custom_agent.template_source,
        "name": custom_agent.name,
        "description": custom_agent.description,
        "custom_prompt": overlay_normalization.content,
        "custom_prompt_overlay_status": overlay_normalization.status,
        "custom_prompt_removed_layer_kinds": overlay_normalization.removed_layer_kinds,
        "custom_prompt_warning": overlay_normalization.warning,
        "group_prompt_overrides": group_prompt_overrides,
        "allowed_group_ids": _read_allowed_group_ids(custom_agent),
        "inherited_allowed_group_ids": list(custom_agent.inherited_allowed_group_ids),
        "icon": custom_agent.icon,
        "include_group_rules": include_group_rules,
        "model_id": custom_agent.model_id,
        "model_temperature": float(
            custom_agent.model_temperature
            if custom_agent.model_temperature is not None else 0.1
        ),
        "model_reasoning": custom_agent.model_reasoning,
        "tool_ids": list(custom_agent.tool_ids or []),
        "output_schema_key": custom_agent.output_schema_key,
        "visibility": custom_agent.visibility,
        "project_id": str(custom_agent.project_id) if custom_agent.project_id else None,
        "is_active": custom_agent.is_active,
        "created_at": custom_agent.created_at,
        "updated_at": custom_agent.updated_at,
    }


def get_custom_agent_group_prompt(
    parent_agent_key: str,
    group_id: str,
    group_prompt_overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Resolve effective group prompt content with custom overrides first."""
    normalized_group_id = (group_id or "").strip().upper()
    if not normalized_group_id:
        return None

    overrides = normalize_editable_group_prompt_overrides(group_prompt_overrides)
    override = overrides.get(normalized_group_id)
    if override:
        return override

    from src.lib.prompts.cache import get_prompt_optional

    rule_prompt = get_prompt_optional(
        parent_agent_key,
        prompt_type="group_rules",
        group_id=normalized_group_id,
    )
    if not rule_prompt:
        return None
    return rule_prompt.content
