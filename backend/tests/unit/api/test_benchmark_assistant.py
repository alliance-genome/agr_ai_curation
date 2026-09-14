from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import benchmark_assistant as api
from src.api.benchmark_auth import require_benchmark_assist
from src.api.benchmark_curator import require_benchmark_assistant_curator
from src.lib.chat_history_repository import BENCHMARK_ASSISTANT_CHAT_KIND, ChatSessionCursor
from src.lib.executable_runs import ExecutableRunManager


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    db = MagicMock()
    sessions = MagicMock()
    sessions.return_value.__enter__.return_value = db
    monkeypatch.setattr(api, "SessionLocal", sessions)
    store = MagicMock()
    monkeypatch.setattr(api, "ChatHistoryRepository", lambda _db: store)
    now = datetime.now(timezone.utc)
    record = SimpleNamespace(
        session_id=str(uuid4()), effective_title="Paper preparation", created_at=now,
        updated_at=now, last_message_at=now, chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND,
    )
    store.create_session.return_value = record
    store.get_session.return_value = record
    store.list_sessions.return_value = SimpleNamespace(items=[record], next_cursor=None)
    store.list_messages.return_value = SimpleNamespace(items=[SimpleNamespace(
        message_id=uuid4(), turn_id="turn", role="assistant", message_type="message",
        content="Check this gene identifier", created_at=now,
        payload_json={"internal": "do-not-return"},
    )], next_cursor=None)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_benchmark_assistant_curator] = lambda: SimpleNamespace(subject="human")
    with TestClient(app) as client:
        yield client, app, store, db, record, sessions


def test_create_and_list_bind_subject_and_kind(setup):
    client, _, store, db, record, _ = setup
    response = client.post("/api/v1/benchmarks/assistant/sessions", json={"subject": "someone-else"})
    assert response.status_code == 201
    assert response.json()["session_id"] == record.session_id
    assert store.create_session.call_args.kwargs["user_auth_sub"] == "human"
    assert store.create_session.call_args.kwargs["chat_kind"] == BENCHMARK_ASSISTANT_CHAT_KIND
    db.commit.assert_called_once()
    response = client.get("/api/v1/benchmarks/assistant/sessions")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert store.list_sessions.call_args.kwargs["user_auth_sub"] == "human"
    assert store.list_sessions.call_args.kwargs["chat_kind"] == BENCHMARK_ASSISTANT_CHAT_KIND


def test_read_uses_owner_and_omits_internal_payload(setup):
    client, _, store, _, record, _ = setup
    response = client.get(f"/api/v1/benchmarks/assistant/sessions/{record.session_id}")
    assert response.status_code == 200
    assert response.json()["messages"][0]["content"] == "Check this gene identifier"
    assert "do-not-return" not in response.text
    store.get_session.assert_called_once_with(session_id=record.session_id, user_auth_sub="human")
    assert store.list_messages.call_args.kwargs["chat_kind"] == BENCHMARK_ASSISTANT_CHAT_KIND


def test_history_returns_only_valid_proposal_references_not_internal_tool_payload(setup):
    client, _, store, _, record, _ = setup
    proposal_id = str(uuid4())
    experiment_id, draft_id = str(uuid4()), str(uuid4())
    store.list_messages.return_value.items[0].payload_json = {"tool_calls": [
        {"name": "propose_paper_reference_draft", "output": {
            "contract_version": "paper_reference_proposal.v1", "proposal_id": proposal_id,
            "candidate": {"secret_field": "not-a-browser-history-field"},
        }},
        {"name": "other", "output": {"proposal_id": str(uuid4())}},
        {"name": "propose_experiment_draft", "output": {
            "contract_version": "experiment_draft_proposal.v1", "proposal_id": experiment_id,
            "artifact_identity": draft_id, "candidate": {"private": "not-a-browser-history-field"},
        }},
        {"name": "propose_experiment_draft", "output": {
            "contract_version": "experiment_draft_proposal.v1", "proposal_id": experiment_id,
            "artifact_identity": "not-a-uuid",
        }},
        {"name": "propose_paper_reference_draft", "output": {
            "contract_version": "paper_reference_proposal.v1", "proposal_id": "invalid",
        }},
    ]}
    response = client.get(f"/api/v1/benchmarks/assistant/sessions/{record.session_id}")
    assert response.status_code == 200
    assert response.json()["messages"][0]["proposal_ids"] == [proposal_id]
    assert response.json()["messages"][0]["experiment_proposals"] == [
        {"proposal_id": experiment_id, "draft_id": draft_id},
    ]
    assert "not-a-browser-history-field" not in response.text


@pytest.mark.parametrize("kind", [None, "agent_studio", "assistant_chat"])
def test_missing_foreign_or_other_kind_is_not_found(setup, kind):
    client, _, store, _, record, _ = setup
    store.get_session.return_value = None if kind is None else SimpleNamespace(chat_kind=kind)
    response = client.get(f"/api/v1/benchmarks/assistant/sessions/{record.session_id}")
    assert response.status_code == 404
    store.list_messages.assert_not_called()


def test_paging_roundtrip_and_configured_limits(setup, monkeypatch):
    client, _, store, _, record, _ = setup
    store.list_sessions.return_value.next_cursor = ChatSessionCursor(record.created_at, record.session_id)
    cursor = client.get("/api/v1/benchmarks/assistant/sessions").json()["next_cursor"]
    assert client.get("/api/v1/benchmarks/assistant/sessions", params={"cursor": cursor}).status_code == 200
    assert store.list_sessions.call_args.kwargs["cursor"].session_id == record.session_id
    monkeypatch.setenv("CHAT_SESSION_PAGE_SIZE_MAX", "1")
    assert client.get("/api/v1/benchmarks/assistant/sessions?limit=2").status_code == 422
    assert client.get("/api/v1/benchmarks/assistant/sessions?cursor=private-invalid").status_code == 422
    response = client.get("/api/v1/benchmarks/assistant/sessions/private-invalid")
    assert response.status_code == 422 and "private-invalid" not in response.text


def test_disabled_api_and_denied_assist_do_not_touch_database(setup, monkeypatch):
    client, app, _, _, _, sessions = setup
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "false")
    assert client.post("/api/v1/benchmarks/assistant/sessions").status_code == 404
    sessions.assert_not_called()
    monkeypatch.setenv("BENCHMARK_API_ENABLED", "true")
    # Use the real curator dependency graph: capability denial precedes token/DB checks.
    del app.dependency_overrides[require_benchmark_assistant_curator]

    def denied():
        raise HTTPException(403, "Benchmark capability required")

    app.dependency_overrides[require_benchmark_assist] = denied
    assert client.post("/api/v1/benchmarks/assistant/sessions").status_code == 403
    sessions.assert_not_called()


def test_database_failure_sanitized_and_large_create_not_committed(setup, monkeypatch):
    client, _, store, db, _, _ = setup
    monkeypatch.setattr("src.lib.http_errors.report_runtime_exception", lambda *_a, **_k: True)
    store.list_sessions.side_effect = RuntimeError("private-sql-parameters")
    response = client.get("/api/v1/benchmarks/assistant/sessions")
    assert response.status_code == 503 and "private-sql-parameters" not in response.text
    monkeypatch.setenv("BENCHMARK_CATALOG_MAX_RESPONSE_BYTES", "1")
    assert client.post("/api/v1/benchmarks/assistant/sessions").status_code == 413
    db.commit.assert_not_called()


def test_stream_start_and_completed_replay_do_not_repeat_provider_work(setup, monkeypatch):
    client, _, _, _, record, _ = setup
    manager = ExecutableRunManager()
    observe = MagicMock(wraps=manager.observe)
    monkeypatch.setattr(manager, "observe", observe)
    monkeypatch.setattr(api, "get_chat_sse_keepalive_interval_seconds", lambda: 7.0)
    monkeypatch.setattr(api, "executable_run_manager", manager)
    monkeypatch.setattr(api.assistant_turns, "executable_run_manager", manager)
    saved = []
    monkeypatch.setattr(api.assistant_turns, "_persist", lambda **values: saved.append(values))
    calls = []

    async def model(**values):
        calls.append(values["user_id"])
        values["state"].assistant_text_parts.append("Saved answer")
        yield {"type": "TEXT_DELTA", "delta": "Saved answer"}

    monkeypatch.setattr(api.assistant_turns, "stream_benchmark_assistant", model)
    prepared = SimpleNamespace(created=True, replay=None)
    monkeypatch.setattr(api, "_prepare", lambda *_args: (prepared, []))
    payload = {"turn_id": str(uuid4()), "message": "Help with paper", "context": {}}
    path = f"/api/v1/benchmarks/assistant/sessions/{record.session_id}/turns"
    response = client.post(path, json=payload)
    assert response.status_code == 200
    assert '"DONE"' in response.text and "Saved answer" in response.text
    assert observe.call_args.kwargs["keepalive_interval_seconds"] == 7.0
    assert calls == ["human"] and len(saved) == 1
    prepared.created = False
    proposal_id, draft_id = str(uuid4()), str(uuid4())
    prepared.replay = SimpleNamespace(content="Saved answer", payload_json={
        "status": "completed", "tool_calls": [{"name": "propose_experiment_draft", "output": {
            "contract_version": "experiment_draft_proposal.v1", "proposal_id": proposal_id,
            "artifact_identity": draft_id, "candidate": {"private": "do-not-replay"},
        }}],
    })
    response = client.post(path, json=payload)
    assert response.status_code == 200 and '"replayed":true' in response.text
    assert proposal_id in response.text and draft_id in response.text
    assert '"tool_name":"propose_experiment_draft"' in response.text
    assert "do-not-replay" not in response.text
    assert calls == ["human"]
    prepared.replay = None
    # An accepted request without its live producer cannot start another paid run.
    assert client.post(path, json=payload).status_code == 409


def test_turn_and_tool_reply_request_schema_cannot_supply_identity_or_tool_catalog(setup):
    client, _, _, _, record, _ = setup
    path = f"/api/v1/benchmarks/assistant/sessions/{record.session_id}/turns"
    response = client.post(path, json={"turn_id": str(uuid4()), "message": "x", "owner": "other"})
    assert response.status_code == 422
    response = client.post(path, json={"turn_id": str(uuid4()), "message": "x", "tools": ["run_benchmark"]})
    assert response.status_code == 422
    response = client.post(path + f"/{uuid4()}/tool-results", json={
        "tool_request_id": str(uuid4()), "output": {},
    })
    assert response.status_code == 409
