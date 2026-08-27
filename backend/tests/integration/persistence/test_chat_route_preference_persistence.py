"""PostgreSQL coverage for chat route persistence and authorization."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Literal
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
from sqlalchemy.exc import IntegrityError

from src.lib.config.groups_loader import get_valid_group_ids
from src.models.sql.agent import Agent, Project, ProjectMember
from src.models.sql.chat_route_preference import ChatRoutePreference
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.database import SessionLocal
from src.models.sql.user import User
from src.services.chat_route_preference_service import (
    ChatRouteTargetUnavailableError,
    clear_chat_route_preference,
    get_chat_route_preference,
    list_chat_route_picker_targets,
    resolve_chat_route_selection,
    update_chat_route_preference,
)


BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _agent(key: str, **overrides) -> Agent:
    values = {
        "agent_key": key,
        "name": key.replace("_", " ").title(),
        "description": f"Description for {key}",
        "instructions": "Test instructions",
        "model_id": "gpt-test",
        "tool_ids": [],
        "allowed_group_ids": [],
        "inherited_allowed_group_ids": [],
        "visibility": "system",
        "show_in_palette": True,
        "is_active": True,
    }
    values.update(overrides)
    return Agent(**values)


def _flow(user_id: int, name: str, agent_key: str, *, active: bool = True) -> CurationFlow:
    return CurationFlow(
        user_id=user_id,
        name=name,
        description=f"Description for {name}",
        flow_definition={
            "version": "1.1",
            "nodes": [{"id": "step", "data": {"agent_id": agent_key}}],
            "edges": [],
            "entry_node_id": "step",
        },
        is_active=active,
    )


def test_preference_constraints_authorization_stale_reads_and_atomic_replacement():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")
    valid_group_ids = get_valid_group_ids()
    assert len(valid_group_ids) >= 2
    matching_group_id, nonmatching_group_id = valid_group_ids[:2]
    suffix = uuid4().hex
    db = SessionLocal()
    created_user_ids: list[int] = []
    created_agent_ids = []
    created_flow_ids = []
    project_id = None
    try:
        owner = User(auth_sub=f"owner-{suffix}", email=f"owner-{suffix}@example.org")
        other = User(auth_sub=f"other-{suffix}", email=f"other-{suffix}@example.org")
        db.add_all([owner, other])
        db.commit()
        db.refresh(owner)
        db.refresh(other)
        created_user_ids.extend([owner.id, other.id])

        project = Project(name=f"Project {suffix}")
        db.add(project)
        db.flush()
        project_id = project.id
        db.add(ProjectMember(project_id=project.id, user_id=owner.id))

        system = _agent(f"system_{suffix}")
        hidden = _agent(f"hidden_{suffix}", show_in_palette=False)
        private_owner = _agent(
            f"private_owner_{suffix}", visibility="private", user_id=owner.id
        )
        private_other = _agent(
            f"private_other_{suffix}", visibility="private", user_id=other.id
        )
        project_agent = _agent(
            f"project_{suffix}", visibility="project", project_id=project.id
        )
        matching_group = _agent(
            f"matching_{suffix}", allowed_group_ids=[matching_group_id]
        )
        nonmatching_group = _agent(
            f"nonmatching_{suffix}", allowed_group_ids=[nonmatching_group_id]
        )
        agents = [
            system,
            hidden,
            private_owner,
            private_other,
            project_agent,
            matching_group,
            nonmatching_group,
        ]
        db.add_all(agents)
        db.flush()
        created_agent_ids.extend(agent.id for agent in agents)

        active_flow = _flow(owner.id, f"Active {suffix}", system.agent_key)
        inactive_flow = _flow(
            owner.id, f"Inactive {suffix}", system.agent_key, active=False
        )
        other_flow = _flow(other.id, f"Other {suffix}", system.agent_key)
        inaccessible_flow = _flow(
            owner.id, f"Restricted {suffix}", nonmatching_group.agent_key
        )
        flows = [active_flow, inactive_flow, other_flow, inaccessible_flow]
        db.add_all(flows)
        db.commit()
        created_flow_ids.extend(flow.id for flow in flows)

        picker_ids = {
            target.id
            for target in list_chat_route_picker_targets(
                db, user_id=owner.id, active_group_ids=[matching_group_id]
            )
        }
        assert {
            system.agent_key,
            private_owner.agent_key,
            project_agent.agent_key,
            matching_group.agent_key,
            str(active_flow.id),
        } <= picker_ids
        assert picker_ids.isdisjoint(
            {
                hidden.agent_key,
                private_other.agent_key,
                nonmatching_group.agent_key,
                str(inactive_flow.id),
                str(other_flow.id),
                str(inaccessible_flow.id),
            }
        )

        assert get_chat_route_preference(
            db, user_id=owner.id, active_group_ids=[matching_group_id]
        ).mode == "automatic"

        agent_state = update_chat_route_preference(
            db,
            user_id=owner.id,
            mode="agent",
            agent_key=system.agent_key,
            flow_id=None,
            active_group_ids=[matching_group_id],
        )
        assert agent_state.available is True
        assert agent_state.agent_id == system.agent_key
        idempotent_state = update_chat_route_preference(
            db,
            user_id=owner.id,
            mode="agent",
            agent_key=system.agent_key,
            flow_id=None,
            active_group_ids=[matching_group_id],
        )
        assert idempotent_state == agent_state
        assert (
            db.query(ChatRoutePreference)
            .filter(ChatRoutePreference.user_id == owner.id)
            .count()
            == 1
        )

        for unavailable_agent_key in (
            f"missing_{suffix}",
            hidden.agent_key,
            nonmatching_group.agent_key,
        ):
            with pytest.raises(ChatRouteTargetUnavailableError):
                update_chat_route_preference(
                    db,
                    user_id=owner.id,
                    mode="agent",
                    agent_key=unavailable_agent_key,
                    flow_id=None,
                    active_group_ids=[matching_group_id],
                )

        matching_state = update_chat_route_preference(
            db,
            user_id=owner.id,
            mode="agent",
            agent_key=matching_group.agent_key,
            flow_id=None,
            active_group_ids=[matching_group_id],
        )
        assert matching_state.available is True
        assert resolve_chat_route_selection(
            db,
            user_id=owner.id,
            mode="agent",
            agent_id=matching_group.agent_key,
            target_public_id=matching_group.agent_key,
            target_display_name=matching_group.name,
            active_group_ids=[matching_group_id],
        ) == matching_state
        assert resolve_chat_route_selection(
            db,
            user_id=owner.id,
            mode="agent",
            agent_id=matching_group.agent_key,
            target_public_id=matching_group.agent_key,
            target_display_name=matching_group.name,
            active_group_ids=[nonmatching_group_id],
        ).available is False
        revoked_group_state = get_chat_route_preference(
            db, user_id=owner.id, active_group_ids=[nonmatching_group_id]
        )
        assert revoked_group_state.available is False

        agent_state = update_chat_route_preference(
            db,
            user_id=owner.id,
            mode="agent",
            agent_key=system.agent_key,
            flow_id=None,
            active_group_ids=[matching_group_id],
        )
        assert agent_state.available is True

        system.is_active = False
        db.commit()
        stale_agent_state = get_chat_route_preference(
            db, user_id=owner.id, active_group_ids=[matching_group_id]
        )
        assert stale_agent_state.available is False
        assert stale_agent_state.target is not None
        assert stale_agent_state.target.id == system.agent_key
        assert stale_agent_state.target.description is None

        system.is_active = True
        db.commit()
        flow_state = update_chat_route_preference(
            db,
            user_id=owner.id,
            mode="flow",
            agent_key=None,
            flow_id=active_flow.id,
            active_group_ids=[matching_group_id],
        )
        assert flow_state.available is True
        row = db.get(ChatRoutePreference, owner.id)
        assert row is not None and row.agent_id is None and row.flow_id == active_flow.id

        barrier = Barrier(2)
        concurrent_user_id = owner.id
        concurrent_agent_key = system.agent_key
        concurrent_flow_id = active_flow.id

        def replace_concurrently(mode: Literal["agent", "flow"]):
            thread_db = SessionLocal()
            try:
                barrier.wait()
                return update_chat_route_preference(
                    thread_db,
                    user_id=concurrent_user_id,
                    mode=mode,
                    agent_key=concurrent_agent_key if mode == "agent" else None,
                    flow_id=concurrent_flow_id if mode == "flow" else None,
                    active_group_ids=[matching_group_id],
                )
            finally:
                thread_db.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            states = list(executor.map(replace_concurrently, ("agent", "flow")))
        assert all(
            (state.mode, state.agent_id is not None, state.flow_id is not None)
            in {("agent", True, False), ("flow", False, True)}
            for state in states
        )
        db.expire_all()
        row = db.get(ChatRoutePreference, owner.id)
        assert row is not None
        assert (row.mode, row.agent_id is not None, row.flow_id is not None) in {
            ("agent", True, False),
            ("flow", False, True),
        }

        with pytest.raises(ChatRouteTargetUnavailableError):
            update_chat_route_preference(
                db,
                user_id=other.id,
                mode="flow",
                agent_key=None,
                flow_id=active_flow.id,
                active_group_ids=[matching_group_id],
            )

        flow_state = update_chat_route_preference(
            db,
            user_id=owner.id,
            mode="flow",
            agent_key=None,
            flow_id=active_flow.id,
            active_group_ids=[matching_group_id],
        )
        assert flow_state.available is True
        resolved_flow_state = resolve_chat_route_selection(
            db,
            user_id=owner.id,
            mode="flow",
            flow_id=active_flow.id,
            target_public_id=str(active_flow.id),
            target_display_name=active_flow.name,
            active_group_ids=[matching_group_id],
        )
        assert resolved_flow_state == flow_state
        active_flow.is_active = False
        db.commit()
        stale_flow_state = get_chat_route_preference(
            db, user_id=owner.id, active_group_ids=[matching_group_id]
        )
        assert stale_flow_state.available is False
        assert stale_flow_state.flow_id == active_flow.id

        for invalid in (
            ChatRoutePreference(
                user_id=other.id,
                mode="automatic",
                agent_id=system.id,
                target_public_id=system.agent_key,
                target_display_name=system.name,
            ),
            ChatRoutePreference(
                user_id=other.id,
                mode="agent",
                agent_id=system.id,
                flow_id=other_flow.id,
                target_public_id=system.agent_key,
                target_display_name=system.name,
            ),
            ChatRoutePreference(user_id=other.id, mode="flow", flow_id=None),
        ):
            db.add(invalid)
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()

        clear_chat_route_preference(db, user_id=owner.id)
        assert db.get(ChatRoutePreference, owner.id) is None
    finally:
        db.rollback()
        if created_user_ids:
            db.query(ChatRoutePreference).filter(
                ChatRoutePreference.user_id.in_(created_user_ids)
            ).delete(synchronize_session=False)
        if created_flow_ids:
            db.query(CurationFlow).filter(CurationFlow.id.in_(created_flow_ids)).delete(
                synchronize_session=False
            )
        if created_agent_ids:
            db.query(Agent).filter(Agent.id.in_(created_agent_ids)).delete(
                synchronize_session=False
            )
        if project_id is not None:
            db.query(ProjectMember).filter(
                ProjectMember.project_id == project_id
            ).delete(synchronize_session=False)
            db.query(Project).filter(Project.id == project_id).delete(
                synchronize_session=False
            )
        if created_user_ids:
            db.query(User).filter(User.id.in_(created_user_ids)).delete(
                synchronize_session=False
            )
        db.commit()
        db.close()
