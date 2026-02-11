"""Custom agent service for Prompt Workshop CRUD and runtime resolution."""

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.sql.custom_agent import CustomAgent, CustomAgentVersion
from src.models.sql.database import SessionLocal
from src.lib.prompts.cache import get_prompt, PromptNotFoundError


CUSTOM_AGENT_PREFIX = "ca_"


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


def compute_prompt_hash(prompt: str) -> str:
    """Compute SHA-256 hash for staleness detection."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


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


def _get_parent_base_prompt(parent_agent_key: str) -> str:
    try:
        return get_prompt(parent_agent_key, prompt_type="system").content
    except PromptNotFoundError as exc:
        raise ValueError(f"No base prompt found for parent agent '{parent_agent_key}'") from exc


def resolve_parent_agent_key(parent_agent_id: str) -> str:
    """Resolve incoming registry ID/alias to canonical prompt key."""
    from src.lib.agent_studio.catalog_service import get_prompt_key_for_agent

    return get_prompt_key_for_agent(parent_agent_id)


def create_custom_agent(
    db: Session,
    user_id: int,
    parent_agent_id: str,
    name: str,
    custom_prompt: Optional[str] = None,
    mod_prompt_overrides: Optional[Dict[str, str]] = None,
    description: Optional[str] = None,
    icon: Optional[str] = None,
    include_mod_rules: bool = True,
) -> CustomAgent:
    """Create a new custom agent and write initial version snapshot."""
    parent_agent_key = resolve_parent_agent_key(parent_agent_id)
    parent_prompt = _get_parent_base_prompt(parent_agent_key)

    agent_prompt = custom_prompt if custom_prompt is not None else parent_prompt
    normalized_mod_overrides = normalize_mod_prompt_overrides(mod_prompt_overrides)
    parent_hash = compute_prompt_hash(parent_prompt)

    custom_agent = CustomAgent(
        user_id=user_id,
        parent_agent_key=parent_agent_key,
        name=name,
        description=description,
        custom_prompt=agent_prompt,
        mod_prompt_overrides=normalized_mod_overrides,
        icon=(icon or "\U0001F527"),
        include_mod_rules=include_mod_rules,
        parent_prompt_hash=parent_hash,
        is_active=True,
    )
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
    custom_agent = query.first()
    if not custom_agent:
        raise CustomAgentNotFoundError(f"Custom agent '{custom_agent_uuid}' not found")
    if custom_agent.user_id != user_id:
        raise CustomAgentAccessError("You do not have permission to access this custom agent")
    return custom_agent


def list_custom_agents_for_user(
    db: Session,
    user_id: int,
    parent_agent_id: Optional[str] = None,
) -> List[CustomAgent]:
    """List active custom agents for a user, optionally filtered by parent."""
    query = db.query(CustomAgent).filter(
        CustomAgent.user_id == user_id,
        CustomAgent.is_active == True,  # noqa: E712
    )
    if parent_agent_id:
        query = query.filter(
            CustomAgent.parent_agent_key == resolve_parent_agent_key(parent_agent_id)
        )

    return query.order_by(CustomAgent.updated_at.desc(), CustomAgent.created_at.desc()).all()


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
    rebase_parent_hash: bool = False,
) -> CustomAgent:
    """Update custom-agent config and snapshot previous prompt when prompt changes."""
    current_mod_overrides = normalize_mod_prompt_overrides(custom_agent.mod_prompt_overrides)
    next_mod_overrides: Optional[Dict[str, str]] = None
    if mod_prompt_overrides is not None:
        next_mod_overrides = normalize_mod_prompt_overrides(mod_prompt_overrides)

    prompt_changed = custom_prompt is not None and custom_prompt != custom_agent.custom_prompt
    mod_overrides_changed = (
        next_mod_overrides is not None
        and next_mod_overrides != current_mod_overrides
    )

    if prompt_changed or mod_overrides_changed:
        next_version = _get_next_version(db, custom_agent.id)
        db.add(CustomAgentVersion(
            custom_agent_id=custom_agent.id,
            version=next_version,
            custom_prompt=custom_agent.custom_prompt,
            mod_prompt_overrides=current_mod_overrides,
            notes=notes or "Auto-snapshot before prompt update",
        ))

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
    if rebase_parent_hash:
        current_hash = get_current_parent_prompt_hash(custom_agent.parent_agent_key)
        custom_agent.parent_prompt_hash = current_hash

    return custom_agent


def soft_delete_custom_agent(custom_agent: CustomAgent) -> None:
    """Soft delete custom agent (flow references can remain as historical data)."""
    custom_agent.is_active = False


def list_custom_agent_versions(
    db: Session,
    custom_agent_uuid: uuid.UUID,
) -> List[CustomAgentVersion]:
    """List versions newest-first."""
    return db.query(CustomAgentVersion).filter(
        CustomAgentVersion.custom_agent_id == custom_agent_uuid
    ).order_by(CustomAgentVersion.version.desc()).all()


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
    db.add(CustomAgentVersion(
        custom_agent_id=custom_agent.id,
        version=snapshot_version,
        custom_prompt=custom_agent.custom_prompt,
        mod_prompt_overrides=normalize_mod_prompt_overrides(custom_agent.mod_prompt_overrides),
        notes=notes or f"Snapshot before revert to v{version}",
    ))

    custom_agent.custom_prompt = target.custom_prompt
    custom_agent.mod_prompt_overrides = normalize_mod_prompt_overrides(target.mod_prompt_overrides)
    return custom_agent


def get_current_parent_prompt_hash(parent_agent_key: str) -> Optional[str]:
    """Get current parent base-prompt hash for staleness checks."""
    try:
        return compute_prompt_hash(_get_parent_base_prompt(parent_agent_key))
    except (ValueError, RuntimeError):
        return None


def is_parent_agent_available(parent_agent_key: str) -> bool:
    """Check if parent agent still exists and has executable factory."""
    from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

    entry = AGENT_REGISTRY.get(parent_agent_key)
    return bool(entry and entry.get("factory") is not None)


@dataclass
class CustomAgentRuntimeInfo:
    """Runtime data needed to execute a custom agent by `ca_<uuid>` id."""

    custom_agent_uuid: uuid.UUID
    custom_agent_id: str
    parent_agent_key: str
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
    """Resolve active custom agent to runtime info (for flow execution + tool naming)."""
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
        ).first()
        if not custom_agent:
            return None

        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        parent_entry = AGENT_REGISTRY.get(custom_agent.parent_agent_key)
        parent_exists = bool(parent_entry and parent_entry.get("factory") is not None)
        requires_document = bool(parent_entry and parent_entry.get("requires_document", False))

        return CustomAgentRuntimeInfo(
            custom_agent_uuid=custom_agent.id,
            custom_agent_id=make_custom_agent_id(custom_agent.id),
            parent_agent_key=custom_agent.parent_agent_key,
            display_name=custom_agent.name,
            custom_prompt=custom_agent.custom_prompt,
            mod_prompt_overrides=normalize_mod_prompt_overrides(custom_agent.mod_prompt_overrides),
            include_mod_rules=custom_agent.include_mod_rules,
            requires_document=requires_document,
            parent_exists=parent_exists,
        )
    finally:
        if own_session and db is not None:
            db.close()


def custom_agent_to_dict(custom_agent: CustomAgent) -> Dict[str, Any]:
    """Serialize SQL model to API-friendly dict."""
    current_hash = get_current_parent_prompt_hash(custom_agent.parent_agent_key)
    parent_exists = is_parent_agent_available(custom_agent.parent_agent_key)
    stale = (
        current_hash is not None
        and custom_agent.parent_prompt_hash is not None
        and current_hash != custom_agent.parent_prompt_hash
    )

    return {
        "id": str(custom_agent.id),
        "agent_id": make_custom_agent_id(custom_agent.id),
        "user_id": custom_agent.user_id,
        "parent_agent_key": custom_agent.parent_agent_key,
        "name": custom_agent.name,
        "description": custom_agent.description,
        "custom_prompt": custom_agent.custom_prompt,
        "mod_prompt_overrides": normalize_mod_prompt_overrides(custom_agent.mod_prompt_overrides),
        "icon": custom_agent.icon,
        "include_mod_rules": custom_agent.include_mod_rules,
        "parent_prompt_hash": custom_agent.parent_prompt_hash,
        "current_parent_prompt_hash": current_hash,
        "parent_prompt_stale": stale,
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
