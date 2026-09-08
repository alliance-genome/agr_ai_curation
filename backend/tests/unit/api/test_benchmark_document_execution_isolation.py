"""Frozen copies cannot be selected through ordinary curator execution APIs."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.services.document_access import exclude_benchmark_document


@pytest.mark.parametrize("mode", [None, "pdf", "benchmark_frozen"])
@pytest.mark.parametrize("as_string", [False, True])
def test_exclusion_checks_persisted_document_mode(mode, as_string):
    document_id = uuid4()
    db = MagicMock()
    db.scalar.return_value = mode
    supplied_id = str(document_id) if as_string else document_id
    if mode == "benchmark_frozen":
        with pytest.raises(HTTPException) as caught:
            exclude_benchmark_document(db, supplied_id)
        assert caught.value.status_code == 404
    else:
        exclude_benchmark_document(db, supplied_id)
    statement = db.scalar.call_args.args[0]
    assert document_id in statement.compile().params.values()


@pytest.mark.parametrize("document_id", [None, "", "invalid-id"])
def test_exclusion_does_not_replace_existing_input_validation(document_id):
    db = MagicMock()
    exclude_benchmark_document(db, document_id)
    db.scalar.assert_not_called()


def test_flow_rejects_frozen_document_before_runtime(monkeypatch):
    from src.api import chat_execute_flow as api

    db = MagicMock()
    db.scalar.return_value = "benchmark_frozen"
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(user_id=42)
    monkeypatch.setattr(api, "set_global_user_from_cognito", lambda *_: SimpleNamespace(id=42))
    monkeypatch.setattr(api, "_get_chat_history_repository", lambda *_: MagicMock())
    runtime = MagicMock()
    monkeypatch.setattr(api, "execute_flow", runtime)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.execute_flow_endpoint(
            request=api.ExecuteFlowRequest(
                flow_id=uuid4(), session_id="isolation", document_id=uuid4(),
                turn_id=None, user_query=None,
            ),
            db=db, user={"sub": "curator"},
        ))
    assert caught.value.status_code == 404
    runtime.assert_not_called()


def test_custom_agent_rejects_frozen_document_before_runtime(monkeypatch):
    from src.api import agent_studio_custom as api

    db = MagicMock()
    db.scalar.return_value = "benchmark_frozen"
    agent_id = uuid4()
    monkeypatch.setattr(api, "set_global_user_from_cognito", lambda *_: SimpleNamespace(id=42))
    monkeypatch.setattr(api, "get_custom_agent_for_user", lambda *_: SimpleNamespace(id=agent_id))
    monkeypatch.setattr(api, "_require_custom_agent_group_access", lambda *_: None)
    monkeypatch.setattr(api, "get_custom_agent_runtime_info", lambda *_, **__: SimpleNamespace(requires_document=True))
    runtime = MagicMock()
    monkeypatch.setattr(api, "get_agent_by_id", runtime)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(api.test_custom_agent_endpoint(
            custom_agent_id=agent_id,
            request=api.TestCustomAgentRequest(input="test", document_id=str(uuid4()), group_id=None),
            db=db, user={"sub": "curator"},
        ))
    assert caught.value.status_code == 404
    runtime.assert_not_called()
