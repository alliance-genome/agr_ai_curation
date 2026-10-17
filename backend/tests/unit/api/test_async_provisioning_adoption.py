"""Cold provisioning must yield without transferring request Sessions to workers."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.services import user_service


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [
    "flow_owner", "flow_list", "custom_list", "studio_catalog",
    "curation_list", "execute_flow",
])
async def test_routes_await_owned_provisioning(monkeypatch, route):
    from src.api import agent_studio, agent_studio_custom, curation_workspace, flows
    from src.api import chat_execute_flow
    from src.lib.agent_studio.models import PromptCatalog

    loop = asyncio.get_running_loop()
    entered, closed = asyncio.Event(), asyncio.Event()
    release = Event()
    request_thread = get_ident()
    worker_session = object()
    request_session = MagicMock()
    lifecycle = []
    claims = {"sub": "authenticated-subject", "email": "curator@example.org"}
    flow_id = uuid4()
    owned_flow = SimpleNamespace(id=flow_id, user_id=17)

    @contextmanager
    def session_factory():
        lifecycle.append(("open", get_ident()))
        try:
            yield worker_session
        finally:
            lifecycle.append(("close", get_ident()))
            loop.call_soon_threadsafe(closed.set)

    def provision(db, principal):
        assert db is worker_session
        assert principal.subject == claims["sub"]
        assert principal.email == claims["email"]
        lifecycle.append(("work", get_ident()))
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return SimpleNamespace(
            id=17, auth_sub=principal.subject, email=principal.email,
            display_name=None, created_at=datetime.now(timezone.utc),
            last_login=None, is_active=True,
        )

    def owned_read(db, user_id, **kwargs):
        assert get_ident() == request_thread
        assert db is request_session
        assert user_id == 17
        return []

    monkeypatch.setattr(user_service, "SessionLocal", session_factory)
    monkeypatch.setattr(user_service, "provision_user", provision)
    if route == "flow_owner":
        request_session.query.return_value.filter.return_value.first.return_value = owned_flow
        operation = flows.verify_flow_ownership(request_session, flow_id, claims)
    elif route == "flow_list":
        request_session.scalar.return_value = 0
        request_session.scalars.return_value.all.return_value = []
        operation = flows.list_flows(page=1, page_size=10, user=claims, db=request_session)
    elif route == "custom_list":
        monkeypatch.setattr(agent_studio_custom, "list_custom_agents_for_user", owned_read)
        operation = agent_studio_custom.list_custom_agents_endpoint(
            template_source=None, user=claims, db=request_session,
        )
    elif route == "studio_catalog":
        monkeypatch.setattr(agent_studio, "list_custom_agents_visible_to_user", owned_read)
        monkeypatch.setattr(agent_studio, "get_prompt_catalog", lambda: SimpleNamespace(
            catalog=PromptCatalog(total_agents=0, available_groups=[]),
        ))
        operation = agent_studio.get_catalog(user=claims, db=request_session)
    elif route == "curation_list":
        def list_sessions(db, request, *, current_user_id):
            assert get_ident() == request_thread
            assert db is request_session
            assert current_user_id == claims["sub"]
            return "curation sessions"
        monkeypatch.setattr(curation_workspace, "list_sessions", list_sessions)
        operation = curation_workspace.list_review_sessions(
            request=object(), user=claims, db=request_session,
        )
    else:
        def visible_flow(db, requested_id, user_id):
            owned_read(db, user_id)
            assert requested_id == flow_id
            raise HTTPException(404, "Flow not found")
        monkeypatch.setattr(chat_execute_flow, "get_visible_flow", visible_flow)
        monkeypatch.setattr(chat_execute_flow, "_get_chat_history_repository", lambda db: object())
        operation = chat_execute_flow.execute_flow_endpoint(
            request=SimpleNamespace(flow_id=flow_id), user=claims, db=request_session,
        )

    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # This coroutine runs while the route's real provisioning worker is held.
        assert not task.done()
        request_session.commit.assert_not_called()
        request_session.close.assert_not_called()
        release.set()
        if route == "execute_flow":
            with pytest.raises(HTTPException) as exc:
                await task
            assert exc.value.status_code == 404
        else:
            result = await task
            if route == "flow_owner":
                assert result is owned_flow
            elif route == "curation_list":
                assert result == "curation sessions"
            elif route in {"flow_list", "custom_list"}:
                assert result.total == 0
            else:
                assert result.catalog.total_agents == 0
        await asyncio.wait_for(closed.wait(), 5)
        assert [action for action, _ in lifecycle] == ["open", "work", "close"]
        assert len({thread for _, thread in lifecycle}) == 1
        assert lifecycle[0][1] != request_thread
        request_session.commit.assert_not_called()
        request_session.close.assert_not_called()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
