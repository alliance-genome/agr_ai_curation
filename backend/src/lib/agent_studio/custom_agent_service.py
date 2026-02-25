"""Custom agent service for Agent Workshop CRUD and runtime resolution."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from src.lib.agent_studio.agent_service import get_agent_by_key, get_project_ids_for_user
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.lib.config.models_loader import get_model
from src.models.sql.agent import Agent as CustomAgent, ProjectMember
from src.models.sql.custom_agent import CustomAgentVersion
from src.models.sql.database import SessionLocal


CUSTOM_AGENT_PREFIX = "ca_"
_DOCUMENT_TOOL_IDS = {"search_document", "read_section", "read_subsection"}


class CustomAgentError(Exception):
    """Base class for custom-agent service errors."""


class CustomAgentNotFoundError(CustomAgentError):
    """Raised when a custom agent does not exist or is not active."""


class CustomAgentAccessError(CustomAgentError):
    """Raised when a user attempts to access another user's custom agent."""


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


def normalize_mod_prompt_overrides(
    mod_prompt_overrides: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """Normalize MOD override payloads to a clean MOD_ID -> prompt map."""
    if not mod_prompt_overrides:
        return {}

    normalized: Dict[str, str] = {}
    for raw_mod_id, raw_prompt in mod_prompt_overrides.items():
        if raw_mod_id is None:
            continue
        mod_id = str(raw_mod_id).strip().upper()
        if not mod_id:
            continue
        prompt = str(raw_prompt or "")
        if not prompt.strip():
            # Empty overrides are treated as "no override" and omitted.
            continue
        normalized[mod_id] = prompt

    return normalized


def _get_next_version(db: Session, custom_agent_uuid: uuid.UUID) -> int:
    max_version = db.query(func.max(CustomAgentVersion.version)).filter(
        CustomAgentVersion.custom_agent_id == custom_agent_uuid
    ).scalar()
    return int(max_version or 0) + 1


def _validate_requested_tool_ids(
    db: Session,
    tool_ids: Optional[List[str]],
) -> Optional[List[str]]:
    """Validate requested tool attachments against DB tool policies."""
    if tool_ids is None:
        return None

    normalized = [str(tool_id).strip() for tool_id in tool_ids if str(tool_id).strip()]
    if not normalized:
        return []

    policy_by_key = {
        entry.tool_key: entry
        for entry in get_tool_policy_cache().list_all(db)
    }
    unknown = sorted({tool_id for tool_id in normalized if tool_id not in policy_by_key})
    if unknown:
        raise ValueError(f"Unknown tool_ids: {', '.join(unknown)}")

    disallowed = sorted(
        {tool_id for tool_id in normalized if not policy_by_key[tool_id].allow_attach}
    )
    if disallowed:
        raise ValueError(f"Tool(s) are not attachable: {', '.join(disallowed)}")

    return normalized


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


def _resolve_system_template_agent(db: Session, template_source: str) -> CustomAgent:
    """Resolve a system template by canonical unified `agent_key` only."""
    raw_id = str(template_source or "").strip()
    if not raw_id:
        raise ValueError("template_source is required")

    by_key = db.query(CustomAgent).filter(
        CustomAgent.agent_key == raw_id,
        CustomAgent.visibility == "system",
        CustomAgent.is_active == True,  # noqa: E712
    ).first()
    if by_key:
        return by_key

    raise ValueError(f"No active system agent found for parent id '{raw_id}'")


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


def create_custom_agent(
    db: Session,
    user_id: int,
    name: str,
    template_source: Optional[str] = None,
    custom_prompt: Optional[str] = None,
    mod_prompt_overrides: Optional[Dict[str, str]] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    include_mod_rules: bool = True,
    model_id: Optional[str] = None,
    tool_ids: Optional[List[str]] = None,
    output_schema_key: Optional[str] = None,
    category: Optional[str] = None,
    model_temperature: Optional[float] = None,
    model_reasoning: Optional[str] = None,
) -> CustomAgent:
    """Create a new custom agent and seed version snapshot."""
    selected_template_key = str(template_source or "").strip()
    parent_defaults: Dict[str, Any] = {}
    parent_prompt = ""
    parent_agent_key: Optional[str] = None

    if selected_template_key:
        parent_template = _resolve_system_template_agent(db, selected_template_key)
        parent_agent_key = parent_template.agent_key
        parent_prompt = parent_template.instructions
        parent_defaults = {
            "model_id": parent_template.model_id,
            "model_temperature": float(parent_template.model_temperature or 0.1),
            "model_reasoning": parent_template.model_reasoning,
            "tool_ids": list(parent_template.tool_ids or []),
            "output_schema_key": parent_template.output_schema_key,
            "category": parent_template.category,
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
        }

    agent_prompt = custom_prompt if custom_prompt is not None else parent_prompt
    normalized_mod_overrides = normalize_mod_prompt_overrides(mod_prompt_overrides)
    custom_uuid = uuid.uuid4()

    effective_model_id = _validate_model_id(model_id or parent_defaults["model_id"] or "")

    requested_tool_ids = _validate_requested_tool_ids(db, tool_ids)

    custom_agent = CustomAgent(
        id=custom_uuid,
        agent_key=make_custom_agent_id(custom_uuid),
        user_id=user_id,
        visibility="private",
        name=name,
        description=description,
        instructions=agent_prompt,
        model_id=effective_model_id,
        model_temperature=float(
            model_temperature
            if model_temperature is not None
            else parent_defaults["model_temperature"]
        ),
        model_reasoning=model_reasoning if model_reasoning is not None else parent_defaults["model_reasoning"],
        tool_ids=list(
            requested_tool_ids
            if requested_tool_ids is not None
            else parent_defaults["tool_ids"]
        ),
        output_schema_key=output_schema_key if output_schema_key is not None else parent_defaults["output_schema_key"],
        group_rules_enabled=include_mod_rules,
        group_rules_component=parent_agent_key,
        mod_prompt_overrides=normalized_mod_overrides,
        icon=(icon or "\U0001F527"),
        category=category if category is not None else parent_defaults["category"],
        template_source=parent_agent_key,
        supervisor_enabled=False,
        supervisor_batchable=False,
        show_in_palette=True,
        version=1,
        is_active=True,
    )

    existing_name = db.query(CustomAgent).filter(
        CustomAgent.user_id == user_id,
        CustomAgent.name == name,
        CustomAgent.visibility.in_(["private", "project"]),
        CustomAgent.is_active == True,  # noqa: E712
    ).first()
    if existing_name:
        raise ValueError("A custom agent with this name already exists")

    db.add(custom_agent)
    db.flush()

    # Seed version history with the initial prompt.
    db.add(CustomAgentVersion(
        custom_agent_id=custom_agent.id,
        version=1,
        custom_prompt=agent_prompt,
        mod_prompt_overrides=normalized_mod_overrides,
        notes="Initial version",
    ))

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


def clone_visible_agent_for_user(
    db: Session,
    user_id: int,
    source_agent_key: str,
    name: Optional[str] = None,
) -> CustomAgent:
    """Clone any user-visible agent (system/private/project) into user's private space."""
    source_key = str(source_agent_key or "").strip()
    if not source_key:
        raise ValueError("source_agent_id is required")

    source_agent = get_agent_by_key(db, source_key, user_id=user_id)
    if source_agent is None:
        raise CustomAgentNotFoundError(f"Agent '{source_key}' not found")
    if source_agent.visibility not in {"system", "private", "project"}:
        raise ValueError("Only system/private/project agents can be cloned")

    requested_name = str(name or "").strip()
    clone_name = requested_name or _generate_clone_name(db, user_id, source_agent.name)
    if _has_active_custom_name(db, user_id, clone_name):
        raise ValueError("A custom agent with this name already exists")

    template_source = str(source_agent.template_source or "").strip() or (
        source_agent.agent_key if source_agent.visibility == "system" else None
    )

    return create_custom_agent(
        db=db,
        user_id=user_id,
        name=clone_name,
        template_source=template_source,
        custom_prompt=source_agent.instructions,
        mod_prompt_overrides=dict(source_agent.mod_prompt_overrides or {}),
        description=source_agent.description,
        icon=source_agent.icon,
        include_mod_rules=bool(source_agent.group_rules_enabled),
        model_id=source_agent.model_id,
        tool_ids=list(source_agent.tool_ids or []),
        output_schema_key=source_agent.output_schema_key,
        category=source_agent.category,
        model_temperature=source_agent.model_temperature,
        model_reasoning=source_agent.model_reasoning,
    )


def update_custom_agent(
    db: Session,
    custom_agent: CustomAgent,
    name: Optional[str] = None,
    description: Optional[str] = None,
    custom_prompt: Optional[str] = None,
    mod_prompt_overrides: Optional[Dict[str, str]] = None,
    icon: Optional[str] = None,
    include_mod_rules: Optional[bool] = None,
    notes: Optional[str] = None,
    model_id: Optional[str] = None,
    model_temperature: Optional[float] = None,
    model_reasoning: Optional[str] = None,
    tool_ids: Optional[List[str]] = None,
    output_schema_key: Optional[str] = None,
    allow_empty_tool_ids: bool = False,
) -> CustomAgent:
    """Update custom-agent config and snapshot previous prompt when prompt changes."""
    if name is not None:
        existing_name = db.query(CustomAgent).filter(
            CustomAgent.user_id == custom_agent.user_id,
            CustomAgent.name == name,
            CustomAgent.id != custom_agent.id,
            CustomAgent.visibility.in_(["private", "project"]),
            CustomAgent.is_active == True,  # noqa: E712
        ).first()
        if existing_name:
            raise ValueError("A custom agent with this name already exists")

    current_mod_overrides = normalize_mod_prompt_overrides(
        custom_agent.mod_prompt_overrides
    )
    next_mod_overrides: Optional[Dict[str, str]] = None
    if mod_prompt_overrides is not None:
        next_mod_overrides = normalize_mod_prompt_overrides(mod_prompt_overrides)

    prompt_changed = (
        custom_prompt is not None
        and custom_prompt != custom_agent.custom_prompt
    )
    mod_overrides_changed = (
        next_mod_overrides is not None
        and next_mod_overrides != current_mod_overrides
    )

    if prompt_changed or mod_overrides_changed:
        next_version = _get_next_version(db, custom_agent.id)
        db.add(
            CustomAgentVersion(
                custom_agent_id=custom_agent.id,
                version=next_version,
                custom_prompt=custom_agent.custom_prompt,
                mod_prompt_overrides=current_mod_overrides,
                notes=notes or "Auto-snapshot before prompt update",
            )
        )

    if prompt_changed:
        custom_agent.custom_prompt = custom_prompt
    if mod_overrides_changed and next_mod_overrides is not None:
        custom_agent.mod_prompt_overrides = next_mod_overrides

    if name is not None:
        custom_agent.name = name
    if description is not None:
        custom_agent.description = description
    if icon is not None:
        custom_agent.icon = icon
    if include_mod_rules is not None:
        custom_agent.include_mod_rules = include_mod_rules
    if model_id is not None:
        clean_model_id = _validate_model_id(model_id)
        custom_agent.model_id = clean_model_id
    if model_temperature is not None:
        custom_agent.model_temperature = float(model_temperature)
    if model_reasoning is not None:
        custom_agent.model_reasoning = model_reasoning
    if tool_ids is not None:
        validated_tool_ids = _validate_requested_tool_ids(db, tool_ids) or []
        existing_tool_ids = list(custom_agent.tool_ids or [])
        if existing_tool_ids and not validated_tool_ids and not allow_empty_tool_ids:
            raise ValueError(
                "Refusing to clear all tool_ids from an existing agent without explicit override. "
                "Re-attach at least one tool before saving."
            )
        custom_agent.tool_ids = validated_tool_ids
    if output_schema_key is not None:
        custom_agent.output_schema_key = output_schema_key

    if prompt_changed or mod_overrides_changed:
        custom_agent.version = int(custom_agent.version or 1) + 1
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


def revert_custom_agent_to_version(
    db: Session,
    custom_agent: CustomAgent,
    version: int,
    notes: Optional[str] = None,
) -> CustomAgent:
    """Revert custom agent prompt to a previous version and snapshot current prompt."""
    target = db.query(CustomAgentVersion).filter(
        CustomAgentVersion.custom_agent_id == custom_agent.id,
        CustomAgentVersion.version == version,
    ).first()
    if not target:
        raise CustomAgentNotFoundError(
            f"Version {version} not found for custom agent '{custom_agent.id}'"
        )

    snapshot_version = _get_next_version(db, custom_agent.id)
    db.add(
        CustomAgentVersion(
            custom_agent_id=custom_agent.id,
            version=snapshot_version,
            custom_prompt=custom_agent.custom_prompt,
            mod_prompt_overrides=normalize_mod_prompt_overrides(
                custom_agent.mod_prompt_overrides
            ),
            notes=notes or f"Snapshot before revert to v{version}",
        )
    )

    custom_agent.custom_prompt = target.custom_prompt
    custom_agent.mod_prompt_overrides = normalize_mod_prompt_overrides(
        target.mod_prompt_overrides
    )
    custom_agent.version = int(custom_agent.version or 1) + 1
    return custom_agent


@dataclass
class CustomAgentRuntimeInfo:
    """Runtime data needed to execute a custom agent by `ca_<uuid>` id."""

    custom_agent_uuid: uuid.UUID
    custom_agent_id: str
    display_name: str
    custom_prompt: str
    mod_prompt_overrides: Dict[str, str]
    include_mod_rules: bool
    requires_document: bool
    parent_exists: bool


def get_custom_agent_runtime_info(
    custom_agent_id: str,
    db: Optional[Session] = None,
) -> Optional[CustomAgentRuntimeInfo]:
    """Resolve active custom agent to runtime info."""
    custom_uuid = parse_custom_agent_id(custom_agent_id)
    if not custom_uuid:
        return None

    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        custom_agent = db.query(CustomAgent).filter(
            CustomAgent.id == custom_uuid,
            CustomAgent.is_active == True,  # noqa: E712
            CustomAgent.visibility.in_(["private", "project"]),
            CustomAgent.agent_key == custom_agent_id,
        ).first()
        if not custom_agent:
            return None

        # Legacy compatibility field: custom agents are first-class and executable
        # regardless of whether they originated from a template.
        parent_exists = True
        tool_ids = list(custom_agent.tool_ids or [])
        requires_document = bool(set(tool_ids) & _DOCUMENT_TOOL_IDS)

        return CustomAgentRuntimeInfo(
            custom_agent_uuid=custom_agent.id,
            custom_agent_id=make_custom_agent_id(custom_agent.id),
            display_name=custom_agent.name,
            custom_prompt=custom_agent.custom_prompt,
            mod_prompt_overrides=normalize_mod_prompt_overrides(
                custom_agent.mod_prompt_overrides
            ),
            include_mod_rules=custom_agent.include_mod_rules,
            requires_document=requires_document,
            parent_exists=parent_exists,
        )
    finally:
        if own_session and db is not None:
            db.close()


def custom_agent_to_dict(custom_agent: CustomAgent) -> Dict[str, Any]:
    """Serialize SQL model to API-friendly dict."""
    # Legacy compatibility field: custom agents are first-class and executable
    # regardless of whether they originated from a template.
    parent_exists = True

    return {
        "id": str(custom_agent.id),
        "agent_id": make_custom_agent_id(custom_agent.id),
        "user_id": custom_agent.user_id,
        "template_source": custom_agent.template_source,
        "name": custom_agent.name,
        "description": custom_agent.description,
        "custom_prompt": custom_agent.custom_prompt,
        "mod_prompt_overrides": normalize_mod_prompt_overrides(
            custom_agent.mod_prompt_overrides
        ),
        "icon": custom_agent.icon,
        "include_mod_rules": custom_agent.include_mod_rules,
        "model_id": custom_agent.model_id,
        "model_temperature": float(custom_agent.model_temperature or 0.1),
        "model_reasoning": custom_agent.model_reasoning,
        "tool_ids": list(custom_agent.tool_ids or []),
        "output_schema_key": custom_agent.output_schema_key,
        "visibility": custom_agent.visibility,
        "project_id": str(custom_agent.project_id) if custom_agent.project_id else None,
        "parent_exists": parent_exists,
        "is_active": custom_agent.is_active,
        "created_at": custom_agent.created_at,
        "updated_at": custom_agent.updated_at,
    }


def get_custom_agent_mod_prompt(
    parent_agent_key: str,
    mod_id: str,
    mod_prompt_overrides: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Resolve effective MOD prompt content with custom overrides first."""
    normalized_mod_id = (mod_id or "").strip().upper()
    if not normalized_mod_id:
        return None

    overrides = normalize_mod_prompt_overrides(mod_prompt_overrides)
    override = overrides.get(normalized_mod_id)
    if override:
        return override

    from src.lib.prompts.cache import get_prompt_optional

    rule_prompt = get_prompt_optional(
        parent_agent_key,
        prompt_type="group_rules",
        mod_id=normalized_mod_id,
    ) or get_prompt_optional(
        parent_agent_key,
        prompt_type="mod_rules",
        mod_id=normalized_mod_id,
    )
    if not rule_prompt:
        return None
    return rule_prompt.content
