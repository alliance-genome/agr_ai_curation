"""Unit tests for flow API ownership and soft-delete behavior."""

import asyncio
import importlib
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.modules.setdefault(
    "rapidfuzz",
    SimpleNamespace(
        fuzz=SimpleNamespace(
            partial_ratio_alignment=lambda *_args, **_kwargs: SimpleNamespace(
                dest_start=0,
                dest_end=0,
                score=0.0,
            )
        )
    ),
)

flows = importlib.import_module("src.api.flows")


class _DummyQuery:
    def __init__(self, flow):
        self._flow = flow

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        if self._flow is not None and getattr(self._flow, "is_active", True) is False:
            return None
        return self._flow


class _DummyDB:
    def __init__(self, flow=None):
        self._flow = flow
        self.commit_called = False

    def query(self, _model):
        return _DummyQuery(self._flow)

    def commit(self):
        self.commit_called = True


def test_flows_crud_enforces_ownership_and_soft_delete(monkeypatch):
    flow_id = uuid4()
    owned_flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="WB Expression Flow",
        is_active=True,
    )

    db = _DummyDB(flow=owned_flow)

    monkeypatch.setattr(
        flows,
        "set_global_user_from_cognito",
        lambda _db, _auth_user: SimpleNamespace(id=7),
    )
    resolved = flows.verify_flow_ownership(db, flow_id, {"sub": "auth-sub"})
    assert resolved is owned_flow

    monkeypatch.setattr(
        flows,
        "set_global_user_from_cognito",
        lambda _db, _auth_user: SimpleNamespace(id=99),
    )
    with pytest.raises(HTTPException) as exc:
        flows.verify_flow_ownership(db, flow_id, {"sub": "auth-sub"})
    assert exc.value.status_code == 403

    delete_db = _DummyDB()
    monkeypatch.setattr(
        flows,
        "verify_flow_ownership",
        lambda _db, _flow_id, _user: owned_flow,
    )

    result = asyncio.run(
        flows.delete_flow(
            flow_id=flow_id,
            user={"sub": "auth-sub"},
            db=delete_db,
        )
    )

    assert owned_flow.is_active is False
    assert delete_db.commit_called is True
    assert result.success is True
    assert "deleted" in result.message.lower()


def test_verify_flow_ownership_returns_404_for_missing_or_deleted_flow(monkeypatch):
    flow_id = uuid4()
    db = _DummyDB(flow=None)

    monkeypatch.setattr(
        flows,
        "set_global_user_from_cognito",
        lambda _db, _auth_user: SimpleNamespace(id=7),
    )

    with pytest.raises(HTTPException) as exc:
        flows.verify_flow_ownership(db, flow_id, {"sub": "auth-sub"})

    assert exc.value.status_code == 404

    soft_deleted_flow = SimpleNamespace(
        id=flow_id,
        user_id=7,
        name="Deleted Flow",
        is_active=False,
    )
    soft_deleted_db = _DummyDB(flow=soft_deleted_flow)
    with pytest.raises(HTTPException) as soft_deleted_exc:
        flows.verify_flow_ownership(soft_deleted_db, flow_id, {"sub": "auth-sub"})
    assert soft_deleted_exc.value.status_code == 404
