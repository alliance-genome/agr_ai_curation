"""Persistence and authorization service for ordinary-chat routing intent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.lib.agent_studio.agent_service import (
    get_agent_by_key,
    get_project_ids_for_user,
    inaccessible_flow_agent_keys,
    is_agent_visible_to_user,
    list_agents_visible_to_user,
)
from src.models.sql.agent import Agent
from src.models.sql.chat_route_preference import ChatRoutePreference
from src.models.sql.curation_flow import CurationFlow


class ChatRouteTargetUnavailableError(ValueError):
    """The requested target is not currently authorized and executable."""


@dataclass(frozen=True)
class ChatRouteTarget:
    id: str
    kind: Literal["agent", "flow"]
    display_name: str
    description: str | None
    category: str | None
    available: bool


@dataclass(frozen=True)
class ChatRoutePreferenceState:
    mode: Literal["automatic", "agent", "flow"]
    agent_id: str | None
    flow_id: UUID | None
    available: bool
    target: ChatRouteTarget | None


def _is_chat_selectable_agent(agent: Agent) -> bool:
    """Apply the persisted user-facing/runtime catalog predicate."""

    return bool(agent.is_active and agent.show_in_palette)


def _is_flow_executable_for_user(
    db: Session,
    flow: CurationFlow,
    *,
    user_id: int,
    active_group_ids: Iterable[str],
) -> bool:
    return bool(
        flow.is_active
        and flow.user_id == user_id
        and not inaccessible_flow_agent_keys(
            db,
            flow.flow_definition,
            user_id=user_id,
            active_group_ids=active_group_ids,
        )
    )


def _agent_target(agent: Agent, *, available: bool = True) -> ChatRouteTarget:
    return ChatRouteTarget(
        id=agent.agent_key,
        kind="agent",
        display_name=agent.name,
        description=agent.description,
        category=agent.category,
        available=available,
    )


def _flow_target(flow: CurationFlow, *, available: bool = True) -> ChatRouteTarget:
    return ChatRouteTarget(
        id=str(flow.id),
        kind="flow",
        display_name=flow.name,
        description=flow.description,
        category=None,
        available=available,
    )


def list_chat_route_picker_targets(
    db: Session,
    *,
    user_id: int,
    active_group_ids: Iterable[str],
) -> list[ChatRouteTarget]:
    """Return all currently authorized, user-facing executable targets."""

    groups = tuple(active_group_ids)
    agents = [
        _agent_target(agent)
        for agent in list_agents_visible_to_user(db, user_id, groups)
        if _is_chat_selectable_agent(agent)
    ]
    flows = [
        _flow_target(flow)
        for flow in db.query(CurationFlow)
        .filter(
            CurationFlow.user_id == user_id,
            CurationFlow.is_active == True,  # noqa: E712
        )
        .order_by(CurationFlow.updated_at.desc())
        .all()
        if _is_flow_executable_for_user(
            db,
            flow,
            user_id=user_id,
            active_group_ids=groups,
        )
    ]
    return sorted([*agents, *flows], key=lambda target: target.display_name.casefold())


def get_chat_route_preference(
    db: Session,
    *,
    user_id: int,
    active_group_ids: Iterable[str],
) -> ChatRoutePreferenceState:
    """Read routing intent and re-authorize its target under current claims."""

    preference = db.get(ChatRoutePreference, user_id)
    if preference is None or preference.mode == "automatic":
        return ChatRoutePreferenceState("automatic", None, None, True, None)

    groups = tuple(active_group_ids)
    if preference.mode == "agent":
        agent = db.get(Agent, preference.agent_id)
        project_ids = get_project_ids_for_user(db, user_id)
        if (
            agent is not None
            and _is_chat_selectable_agent(agent)
            and is_agent_visible_to_user(
                agent,
                user_id,
                project_ids=project_ids,
                active_group_ids=groups,
            )
        ):
            return ChatRoutePreferenceState(
                "agent", agent.agent_key, None, True, _agent_target(agent)
            )
        target_public_id = str(preference.target_public_id)
        target_display_name = str(preference.target_display_name)
        target = ChatRouteTarget(
            id=target_public_id,
            kind="agent",
            display_name=target_display_name,
            description=None,
            category=None,
            available=False,
        )
        return ChatRoutePreferenceState("agent", target.id, None, False, target)

    flow = db.get(CurationFlow, preference.flow_id)
    if flow is not None and _is_flow_executable_for_user(
        db,
        flow,
        user_id=user_id,
        active_group_ids=groups,
    ):
        return ChatRoutePreferenceState("flow", None, flow.id, True, _flow_target(flow))
    target_public_id = str(preference.target_public_id)
    target_display_name = str(preference.target_display_name)
    target = ChatRouteTarget(
        id=target_public_id,
        kind="flow",
        display_name=target_display_name,
        description=None,
        category=None,
        available=False,
    )
    return ChatRoutePreferenceState(
        "flow", None, UUID(target_public_id), False, target
    )


def update_chat_route_preference(
    db: Session,
    *,
    user_id: int,
    mode: Literal["automatic", "agent", "flow"],
    agent_key: str | None,
    flow_id: UUID | None,
    active_group_ids: Iterable[str],
) -> ChatRoutePreferenceState:
    """Atomically replace one user's preference after target authorization."""

    groups = tuple(active_group_ids)
    values: dict[str, object | None] = {
        "user_id": user_id,
        "mode": mode,
        "agent_id": None,
        "flow_id": None,
        "target_public_id": None,
        "target_display_name": None,
    }
    if mode == "agent":
        agent = get_agent_by_key(
            db,
            agent_key or "",
            user_id=user_id,
            active_group_ids=groups,
        )
        if agent is None or not _is_chat_selectable_agent(agent):
            raise ChatRouteTargetUnavailableError
        values.update(
            agent_id=agent.id,
            target_public_id=agent.agent_key,
            target_display_name=agent.name,
        )
    elif mode == "flow":
        flow = db.get(CurationFlow, flow_id)
        if flow is None or not _is_flow_executable_for_user(
            db,
            flow,
            user_id=user_id,
            active_group_ids=groups,
        ):
            raise ChatRouteTargetUnavailableError
        values.update(
            flow_id=flow.id,
            target_public_id=str(flow.id),
            target_display_name=flow.name,
        )

    statement = insert(ChatRoutePreference).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[ChatRoutePreference.user_id],
        set_={
            "mode": statement.excluded.mode,
            "agent_id": statement.excluded.agent_id,
            "flow_id": statement.excluded.flow_id,
            "target_public_id": statement.excluded.target_public_id,
            "target_display_name": statement.excluded.target_display_name,
            "updated_at": statement.excluded.updated_at,
        },
    )
    db.execute(statement)
    db.commit()
    db.expire_all()
    return get_chat_route_preference(
        db,
        user_id=user_id,
        active_group_ids=groups,
    )


def clear_chat_route_preference(db: Session, *, user_id: int) -> None:
    """Delete stored intent so the default automatic route applies."""

    db.query(ChatRoutePreference).filter(ChatRoutePreference.user_id == user_id).delete()
    db.commit()
