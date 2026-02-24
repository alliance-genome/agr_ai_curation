"""Agent Workshop service helpers for first-class agents."""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from src.models.sql.agent import Agent, ProjectMember


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
    output_schema_key: Optional[str]
    group_rules_enabled: bool
    group_rules_component: Optional[str]
    mod_prompt_overrides: Dict[str, str]
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
) -> bool:
    """Visibility policy for agent browsing and execution."""
    if agent.visibility == "system":
        return True
    if agent.visibility == "private":
        return agent.user_id == user_id
    if agent.visibility == "project":
        if not project_ids or agent.project_id is None:
            return False
        return agent.project_id in set(project_ids)
    return False


def is_agent_editable_by_user(agent: Agent, user_id: int) -> bool:
    """Only the owner of non-system agents can edit."""
    if agent.visibility == "system":
        return False
    return agent.user_id == user_id


def list_agents_visible_to_user(db: Session, user_id: int) -> List[Agent]:
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

    return db.query(Agent).filter(
        Agent.is_active == True,  # noqa: E712
        or_(*visibility_filters),
    ).order_by(Agent.updated_at.desc(), Agent.created_at.desc()).all()


def get_agent_by_key(
    db: Session,
    agent_key: str,
    user_id: Optional[int] = None,
    include_inactive: bool = False,
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
        return query.filter(Agent.visibility == "system").first()

    agent = query.first()
    if not agent:
        return None

    project_ids = get_project_ids_for_user(db, user_id)
    if not is_agent_visible_to_user(agent, user_id, project_ids):
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
        output_schema_key=agent.output_schema_key,
        group_rules_enabled=bool(agent.group_rules_enabled),
        group_rules_component=agent.group_rules_component,
        mod_prompt_overrides=dict(agent.mod_prompt_overrides or {}),
        supervisor_enabled=bool(agent.supervisor_enabled),
        show_in_palette=bool(agent.show_in_palette),
    )
