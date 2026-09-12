"""Agent Workshop service helpers for first-class agents."""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from src.models.sql.agent import Agent, ProjectMember
from src.lib.agent_access import is_resource_access_allowed


@dataclass
class AgentExecutionSpec:
    """Runtime spec materialized from the unified agents table."""

    agent_key: str
    name: str
    instructions: str
    model_id: str
    model_temperature: float
    model_reasoning: Optional[str]
    tool_ids: List[str]
    allowed_group_ids: List[str]
    output_schema_key: Optional[str]
    group_rules_enabled: bool
    group_rules_component: Optional[str]
    group_prompt_overrides: Dict[str, str]
    supervisor_enabled: bool
    show_in_palette: bool


def get_project_ids_for_user(db: Session, user_id: int) -> set[UUID]:
    """Fetch project membership IDs for a user."""
    rows = db.query(ProjectMember.project_id).filter(
        ProjectMember.user_id == user_id
    ).all()
    return {row[0] for row in rows}


def is_agent_visible_to_user(
    agent: Agent,
    user_id: int,
    project_ids: Optional[Iterable[UUID]] = None,
    active_group_ids: Optional[Iterable[str]] = None,
) -> bool:
    """Combined visibility and authenticated-group policy for agent access."""
    visibility_allowed = False
    if agent.visibility == "system":
        visibility_allowed = True
    elif agent.visibility == "private":
        visibility_allowed = agent.user_id == user_id
    elif agent.visibility == "project":
        if not project_ids or agent.project_id is None:
            visibility_allowed = False
        else:
            visibility_allowed = agent.project_id in set(project_ids)
    return is_resource_access_allowed(
        visibility_allowed=visibility_allowed,
        allowed_group_ids=list(agent.allowed_group_ids),
        active_group_ids=list(active_group_ids or []),
        resource_kind="agent",
    )


def is_agent_editable_by_user(agent: Agent, user_id: int) -> bool:
    """Only the owner of non-system agents can edit."""
    if agent.visibility == "system":
        return False
    return agent.user_id == user_id


def list_agents_visible_to_user(
    db: Session,
    user_id: int,
    active_group_ids: Optional[Iterable[str]] = None,
) -> List[Agent]:
    """List active agents visible to a user under private/project/system rules."""
    project_ids = list(get_project_ids_for_user(db, user_id))
    visibility_filters = [
        Agent.visibility == "system",
        and_(Agent.visibility == "private", Agent.user_id == user_id),
    ]
    if project_ids:
        visibility_filters.append(
            and_(Agent.visibility == "project", Agent.project_id.in_(project_ids))
        )

    rows = db.query(Agent).filter(
        Agent.is_active == True,  # noqa: E712
        or_(*visibility_filters),
    ).order_by(Agent.updated_at.desc(), Agent.created_at.desc()).all()
    return [
        agent
        for agent in rows
        if is_agent_visible_to_user(
            agent,
            user_id,
            project_ids,
            active_group_ids,
        )
    ]


def get_agent_by_key(
    db: Session,
    agent_key: str,
    user_id: Optional[int] = None,
    include_inactive: bool = False,
    active_group_ids: Optional[Iterable[str]] = None,
) -> Optional[Agent]:
    """Fetch one agent by key, with visibility enforcement.

    Visibility rules:
    - `user_id` provided: enforce full user visibility (system/private/project).
    - `user_id` omitted: system agents only.
    """
    query = db.query(Agent).filter(Agent.agent_key == agent_key)
    if not include_inactive:
        query = query.filter(Agent.is_active == True)  # noqa: E712
    if user_id is None:
        agent = query.filter(Agent.visibility == "system").first()
        if agent is None:
            return None
        return (
            agent
            if is_resource_access_allowed(
                visibility_allowed=True,
                allowed_group_ids=list(agent.allowed_group_ids),
                active_group_ids=list(active_group_ids or []),
                resource_kind="agent",
            )
            else None
        )

    agent = query.first()
    if not agent:
        return None

    project_ids = get_project_ids_for_user(db, user_id)
    if not is_agent_visible_to_user(
        agent,
        user_id,
        project_ids,
        active_group_ids,
    ):
        return None
    return agent


def agent_to_execution_spec(agent: Agent) -> AgentExecutionSpec:
    """Map SQL model fields into the generic runtime execution spec."""
    return AgentExecutionSpec(
        agent_key=agent.agent_key,
        name=agent.name,
        instructions=agent.instructions,
        model_id=agent.model_id,
        model_temperature=float(agent.model_temperature),
        model_reasoning=agent.model_reasoning,
        tool_ids=list(agent.tool_ids or []),
        allowed_group_ids=list(agent.allowed_group_ids),
        output_schema_key=agent.output_schema_key,
        group_rules_enabled=bool(agent.group_rules_enabled),
        group_rules_component=agent.group_rules_component,
        group_prompt_overrides=dict(agent.group_prompt_overrides or {}),
        supervisor_enabled=bool(agent.supervisor_enabled),
        show_in_palette=bool(agent.show_in_palette),
    )


def inaccessible_flow_agent_keys(
    db: Session,
    flow_definition: Dict[str, object],
    *,
    user_id: int,
    active_group_ids: Iterable[str],
) -> List[str]:
    """Return referenced agent keys unavailable under the current auth snapshot."""

    inaccessible: List[str] = []
    groups = list(active_group_ids)
    nodes = flow_definition.get("nodes", [])
    if not isinstance(nodes, list):
        return inaccessible
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data", node)
        if not isinstance(data, dict):
            continue
        agent_key = str(data.get("agent_id") or "").strip()
        if not agent_key or agent_key == "task_input" or agent_key in inaccessible:
            continue
        if agent_key.startswith("ca_"):
            # Current visibility still applies, but saved executable policy is
            # authoritative: an archived/edited head must not change a flow pin.
            from src.lib.agent_studio.execution_revision_service import authorize_execution_receipt
            from src.schemas.flows import FlowNodeData

            try:
                parsed = FlowNodeData.model_validate(data)
                if parsed.agent_revision_id is None or parsed.execution_receipt is None:
                    raise ValueError("Missing executable pin")
                authorize_execution_receipt(
                    db, parsed.execution_receipt.model_dump(mode="json"), user_id,
                    active_group_ids=groups,
                )
            except ValueError:
                inaccessible.append(agent_key)
            continue
        if get_agent_by_key(
            db,
            agent_key,
            user_id=user_id,
            active_group_ids=groups,
        ) is None:
            inaccessible.append(agent_key)
    return inaccessible
