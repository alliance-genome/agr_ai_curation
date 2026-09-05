"""Read-only, authorized saved references to every revision of a profile."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import String, and_, cast, literal, or_, select, union_all
from sqlalchemy.orm import Session

from src.lib.agent_access import normalize_allowed_group_ids
from src.lib.agent_studio.agent_service import get_project_ids_for_user
from src.lib.agent_studio.execution_revision_service import get_execution_revision
from src.lib.agent_studio.generic_profile_service import get_profile
from src.lib.openai_agents.config import get_generic_profile_list_page_size
from src.models.sql.agent import Agent
from src.models.sql.agent_execution_revision import AgentExecutionRevision as Execution
from src.models.sql.curation_flow import CurationFlow as Flow
from src.models.sql.curation_flow_agent_revision import CurationFlowAgentRevision as Node
from src.models.sql.generic_extraction_profile import GenericExtractionProfileRevision as Revision


class ProfileConsumer(BaseModel):
    key: str
    kind: Literal["agent", "flow"]
    name: str
    agent_id: UUID
    agent_revision_id: UUID
    agent_revision: int
    profile_revision: int
    is_current_agent_revision: bool
    archived: bool
    flow_id: UUID | None = None
    node_id: str | None = None


class ProfileConsumerPage(BaseModel):
    consumers: list[ProfileConsumer]
    next_cursor: str | None
    head_revision: int


def list_profile_consumers(
    db: Session, profile_id: UUID, user_id: int, *, active_group_ids: list[str],
    after: str | None = None,
) -> ProfileConsumerPage:
    """Include history and archived references; never change a consumer's pin.

    Filter ordinary visibility AND saved group policy before pagination, so
    inaccessible references do not contribute names, counts, or cursors. Exact
    revision reads then reuse the runtime's integrity/authorization boundary.
    """
    profile = get_profile(db, profile_id, user_id, include_archived=True)
    groups = normalize_allowed_group_ids(active_group_ids)
    visible = or_(
        and_(Agent.visibility == "private", Agent.user_id == user_id),
        and_(Agent.visibility == "project", Agent.project_id.in_(get_project_ids_for_user(db, user_id))),
    )
    saved_groups = Execution.snapshot["allowed_group_ids"]
    allowed = or_(saved_groups == [], *(saved_groups.contains([group]) for group in groups))
    common = (
        Agent.id.label("agent_id"), Execution.id.label("agent_revision_id"),
        Execution.revision.label("agent_revision"), Revision.revision.label("profile_revision"),
        (Agent.execution_revision_id == Execution.id).label("is_current_agent_revision"),
    )
    agents = select(
        (literal("agent/") + cast(Execution.id, String)).label("key"),
        literal("agent").label("kind"), Agent.name.label("name"), *common,
        (~Agent.is_active).label("archived"),
        cast(literal(None), Flow.id.type).label("flow_id"),
        cast(literal(None), String).label("node_id"),
    ).select_from(Execution).join(Agent, Agent.id == Execution.agent_id).join(
        Revision, Revision.id == Execution.profile_revision_id,
    ).where(Revision.profile_id == profile_id, Agent.agent_key.startswith("ca_", autoescape=True), visible, allowed)
    flows = select(
        (literal("flow/") + cast(Flow.id, String) + literal("/") + Node.node_id).label("key"),
        literal("flow").label("kind"), Flow.name.label("name"), *common,
        (~Flow.is_active).label("archived"), Flow.id.label("flow_id"), Node.node_id.label("node_id"),
    ).select_from(Node).join(Flow, Flow.id == Node.flow_id).join(
        Execution, Execution.id == Node.agent_revision_id,
    ).join(Agent, Agent.id == Execution.agent_id).join(
        Revision, Revision.id == Execution.profile_revision_id,
    ).where(
        Revision.profile_id == profile_id, Flow.user_id == user_id,
        Agent.agent_key.startswith("ca_", autoescape=True), visible, allowed,
    )
    consumers = union_all(agents, flows).subquery()
    query = select(consumers)
    if after is not None:
        query = query.where(consumers.c.key > after)
    size = get_generic_profile_list_page_size()
    rows = list(db.execute(query.order_by(consumers.c.key).limit(size + 1)).mappings())
    page = []
    for row in rows[:size]:
        get_execution_revision(
            db, row["agent_id"], row["agent_revision_id"], user_id, active_group_ids=groups,
        )
        page.append(ProfileConsumer.model_validate(dict(row)))
    return ProfileConsumerPage(
        consumers=page, next_cursor=page[-1].key if len(rows) > size else None,
        head_revision=profile.head_revision,
    )
