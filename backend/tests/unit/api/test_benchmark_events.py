import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import HTTPException
import pytest
from starlette.requests import ClientDisconnect

from src.api import benchmark_events as events


def test_connection_limit_is_per_principal_and_released(monkeypatch):
    monkeypatch.setenv("BENCHMARK_MAX_EVENT_CONNECTIONS_PER_PRINCIPAL", "1")
    subject = str(uuid4())
    other = str(uuid4())
    events._reserve(subject)
    try:
        with pytest.raises(HTTPException) as error:
            events._reserve(subject)
        assert error.value.status_code == 429
        events._reserve(other)
        events._release(other)
    finally:
        events._release(subject)
    assert subject not in events._connections


@pytest.mark.asyncio
async def test_failed_preflight_releases_connection(monkeypatch):
    subject = str(uuid4())
    request = SimpleNamespace(headers={})
    def missing(*args):
        raise HTTPException(404, "missing")
    monkeypatch.setattr(events, "read_event_batch", missing)
    with pytest.raises(HTTPException):
        await events.create_event_response(request, uuid4(), subject)
    assert subject not in events._connections


@pytest.mark.asyncio
async def test_disconnect_releases_slot_even_if_send_fails():
    subject = str(uuid4())
    events._reserve(subject)
    async def content():
        yield "data: test\n\n"
        await asyncio.sleep(0)
    response = events.BenchmarkEventResponse(content(), subject)
    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("disconnected")
    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), send)
    assert subject not in events._connections


def test_data_cannot_inject_sse_control_fields():
    frame = events._frame("benchmark.event", {"payload": "text\nid: forged\nevent: forged"}, "safe:1")
    assert frame.count("\nid: ") == 0
    assert frame.count("\nevent: ") == 1
    assert frame.endswith("\n\n")


@pytest.mark.asyncio
async def test_reauthorization_failure_blocks_next_private_batch_and_releases_slot(monkeypatch):
    subject = str(uuid4())
    request = SimpleNamespace(headers={}, is_disconnected=AsyncMock(return_value=False))
    batch = events.EventBatch(
        summary=None,
        events=({"sequence": 1, "payload": {"value": "first-authorized"}},),
        latest_sequence=2,
    )
    reader = Mock(return_value=batch)
    monkeypatch.setattr(events, "read_event_batch", reader)
    authorize = AsyncMock(side_effect=HTTPException(403, {"code": "authorization_required"}))
    monkeypatch.setattr(events, "require_benchmark_read", authorize)
    response = await events.create_event_response(request, uuid4(), subject)
    sent = []
    async def send(message):
        sent.append(message)
    await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), send)
    body = b"".join(message.get("body", b"") for message in sent).decode()
    assert "first-authorized" in body
    assert "event: stream.error" in body
    assert "authorization_required" in body
    reader.assert_called_once()
    authorize.assert_awaited_once()
    assert subject not in events._connections
