"""Unit tests for flow CRUD endpoint handlers."""

from datetime import datetime, timezone
import inspect
import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from src.schemas.flows import CreateFlowRequest, UpdateFlowRequest

flows = importlib.import_module("src.api.flows")


def _flow_definition():
    return {
        "version": "1.0",
        "entry_node_id": "task_input_1",
        "nodes": [
            {
                "id": "task_input_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Task Input",
                    "task_instructions": "Extract curated observations from this paper.",
                    "output_key": "task_input_text",
                },
            },
            {
                "id": "agent_1",
                "type": "agent",
                "position": {"x": 1, "y": 1},
                "data": {
                    "agent_id": "gene_expression",
                    "agent_display_name": "Gene Expression",
                    "output_key": "gene_expression_output",
                },
            },
        ],
        "edges": [{"id": "e1", "source": "task_input_1", "target": "agent_1"}],
    }


def _flow(name="Flow A"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid4(),
        user_id=17,
        name=name,
        description="desc",
        flow_definition=_flow_definition(),
        execution_count=0,
        last_executed_at=None,
        created_at=now,
        updated_at=now,
        is_active=True,
    )


class _ScalarsResult:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


@pytest.mark.asyncio
async def test_list_flows_returns_paginated_response(monkeypatch):
    flow_a = _flow(name="A")
    flow_b = _flow(name="B")

    db = SimpleNamespace(
        scalar=lambda _query: 2,
        scalars=lambda _query: _ScalarsResult([flow_a, flow_b]),
    )
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    response = await flows.list_flows(page=1, page_size=20, user={"sub": "u1"}, db=db)
    assert response.total == 2
    assert [item.name for item in response.flows] == ["A", "B"]
    assert response.flows[0].step_count == 2


def test_list_flows_uses_shared_default_page_size():
    page_size_default = inspect.signature(flows.list_flows).parameters["page_size"].default

    assert page_size_default.default == flows.DEFAULT_FLOW_LIST_PAGE_SIZE
    assert flows.DEFAULT_FLOW_LIST_PAGE_SIZE == 50


@pytest.mark.asyncio
async def test_get_flow_uses_verify_ownership(monkeypatch):
    owned = _flow(name="Owned")
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: owned)

    response = await flows.get_flow(flow_id=owned.id, user={"sub": "u1"}, db=object())
    assert response.id == owned.id
    assert response.name == "Owned"


@pytest.mark.asyncio
async def test_create_flow_success(monkeypatch):
    class _DB:
        def __init__(self):
            self.added = None
            self.committed = False
            self.refreshed = False

        def add(self, obj):
            self.added = obj

        def commit(self):
            self.committed = True

        def refresh(self, _obj):
            now = datetime.now(timezone.utc)
            _obj.id = uuid4()
            _obj.execution_count = 0
            _obj.created_at = now
            _obj.updated_at = now
            self.refreshed = True

    db = _DB()
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    request = CreateFlowRequest(name="Created", description="new", flow_definition=_flow_definition())
    response = await flows.create_flow(request=request, user={"sub": "u1"}, db=db)

    assert db.committed is True
    assert db.refreshed is True
    assert response.name == "Created"
    assert response.user_id == 17


@pytest.mark.asyncio
async def test_create_flow_maps_unique_integrity_error_to_409(monkeypatch):
    class _DB:
        def add(self, _obj):
            return None

        def commit(self):
            raise IntegrityError(
                statement="insert into curation_flows",
                params={},
                orig=Exception("duplicate key value violates constraint uq_user_flow_name_active"),
            )

        def rollback(self):
            self.rolled_back = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    with pytest.raises(HTTPException) as exc:
        await flows.create_flow(
            request=CreateFlowRequest(name="Dup", description=None, flow_definition=_flow_definition()),
            user={"sub": "u1"},
            db=db,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_flow_maps_other_integrity_error_to_500(monkeypatch):
    class _DB:
        def add(self, _obj):
            return None

        def commit(self):
            raise IntegrityError(
                statement="insert into curation_flows",
                params={},
                orig=Exception("some other integrity error"),
            )

        def rollback(self):
            self.rolled_back = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "set_global_user_from_cognito", lambda *_args, **_kwargs: SimpleNamespace(id=17))

    with pytest.raises(HTTPException) as exc:
        await flows.create_flow(
            request=CreateFlowRequest(name="Err", description=None, flow_definition=_flow_definition()),
            user={"sub": "u1"},
            db=db,
        )
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_update_flow_commits_and_flags_json(monkeypatch):
    flow_obj = _flow(name="Before")
    captured = {"flagged": False}

    class _DB:
        def __init__(self):
            self.committed = False
            self.refreshed = False

        def commit(self):
            self.committed = True

        def refresh(self, _obj):
            self.refreshed = True

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)
    monkeypatch.setattr(
        flows,
        "flag_modified",
        lambda _obj, field: captured.__setitem__("flagged", field == "flow_definition"),
    )

    request = UpdateFlowRequest(
        name="After",
        description="",
        flow_definition=_flow_definition(),
    )
    response = await flows.update_flow(flow_id=flow_obj.id, request=request, user={"sub": "u1"}, db=db)

    assert db.committed is True
    assert db.refreshed is True
    assert captured["flagged"] is True
    assert response.name == "After"
    assert response.description is None


@pytest.mark.asyncio
async def test_update_flow_without_changes_skips_commit(monkeypatch):
    flow_obj = _flow(name="No Change")

    class _DB:
        def __init__(self):
            self.committed = False

        def commit(self):
            self.committed = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)

    response = await flows.update_flow(
        flow_id=flow_obj.id,
        request=UpdateFlowRequest(),
        user={"sub": "u1"},
        db=db,
    )
    assert db.committed is False
    assert response.name == "No Change"


@pytest.mark.asyncio
async def test_update_flow_maps_unique_integrity_error_to_409(monkeypatch):
    flow_obj = _flow(name="Before")

    class _DB:
        def commit(self):
            raise IntegrityError(
                statement="update curation_flows",
                params={},
                orig=Exception("duplicate key value violates constraint uq_user_flow_name_active"),
            )

        def rollback(self):
            self.rolled_back = True

        def refresh(self, _obj):
            return None

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)

    with pytest.raises(HTTPException) as exc:
        await flows.update_flow(
            flow_id=flow_obj.id,
            request=UpdateFlowRequest(name="Duplicate"),
            user={"sub": "u1"},
            db=db,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_flow_marks_inactive(monkeypatch):
    flow_obj = _flow(name="Delete Me")

    class _DB:
        def __init__(self):
            self.committed = False

        def commit(self):
            self.committed = True

    db = _DB()
    monkeypatch.setattr(flows, "verify_flow_ownership", lambda *_args, **_kwargs: flow_obj)

    response = await flows.delete_flow(flow_id=flow_obj.id, user={"sub": "u1"}, db=db)
    assert flow_obj.is_active is False
    assert db.committed is True
    assert response.success is True
