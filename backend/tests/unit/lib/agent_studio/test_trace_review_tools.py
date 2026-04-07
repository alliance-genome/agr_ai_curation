"""Unit tests for trace review helper tools."""

from __future__ import annotations

import httpx
import pytest

import src.lib.agent_studio.tools as tools


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response=None, exc=None, capture=None, timeout=None):
        self._response = response
        self._exc = exc
        self._capture = capture if capture is not None else {}
        self._capture["timeout"] = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        self._capture["url"] = url
        self._capture["params"] = params
        if self._exc:
            raise self._exc
        return self._response


def _patch_async_client(monkeypatch, response=None, exc=None, capture=None):
    monkeypatch.setattr(
        tools.httpx,
        "AsyncClient",
        lambda timeout=None: _FakeAsyncClient(response=response, exc=exc, capture=capture, timeout=timeout),
    )


def test_env_helpers(monkeypatch):
    monkeypatch.delenv("TRACE_REVIEW_SOURCE", raising=False)
    monkeypatch.delenv("TRACE_REVIEW_URL", raising=False)
    assert tools.get_trace_source() == "local"
    assert tools.get_trace_review_url() == "http://trace_review_backend:8001"

    monkeypatch.setenv("TRACE_REVIEW_SOURCE", "prod")
    monkeypatch.setenv("TRACE_REVIEW_URL", "http://trace-review:9000")
    assert tools.get_trace_source() == "prod"
    assert tools.get_trace_review_url() == "http://trace-review:9000"
    assert tools._get_claude_api_url() == "http://trace-review:9000/api/claude/traces"


def test_validate_helpers():
    tools.validate_trace_id("01784cd8-7512-4830-b5f5-a427502ab923")
    tools.validate_trace_id("856df16f1752cb53ee43dcb2f5ecfd16")
    with pytest.raises(ValueError):
        tools.validate_trace_id("not-a-trace-id")

    tools.validate_view("summary")
    with pytest.raises(ValueError):
        tools.validate_view("not-a-view")


@pytest.mark.asyncio
async def test_get_trace_summary_success(monkeypatch):
    capture = {}
    _patch_async_client(
        monkeypatch,
        response=_FakeResponse(200, {"data": {"trace_id": "t1"}, "token_info": {"estimated_tokens": 50}}),
        capture=capture,
    )
    result = await tools.get_trace_summary("856df16f1752cb53ee43dcb2f5ecfd16")

    assert result["status"] == "success"
    assert result["data"]["trace_id"] == "t1"
    assert result["token_info"]["estimated_tokens"] == 50
    assert capture["url"].endswith("/api/claude/traces/856df16f1752cb53ee43dcb2f5ecfd16/summary")
    assert "source" in capture["params"]


@pytest.mark.asyncio
async def test_get_trace_summary_404_and_timeout(monkeypatch):
    _patch_async_client(monkeypatch, response=_FakeResponse(404, {}))
    not_found = await tools.get_trace_summary("856df16f1752cb53ee43dcb2f5ecfd16")
    assert not_found["status"] == "error"
    assert "not found" in not_found["error"]

    _patch_async_client(monkeypatch, exc=httpx.TimeoutException("timeout"))
    timeout = await tools.get_trace_summary("856df16f1752cb53ee43dcb2f5ecfd16")
    assert timeout["status"] == "error"
    assert "timeout" in timeout["error"].lower()


@pytest.mark.asyncio
async def test_get_tool_calls_summary_success_and_404(monkeypatch):
    _patch_async_client(
        monkeypatch,
        response=_FakeResponse(200, {"data": {"total_count": 2}, "token_info": {"estimated_tokens": 100}}),
    )
    success = await tools.get_tool_calls_summary("856df16f1752cb53ee43dcb2f5ecfd16")
    assert success["status"] == "success"
    assert success["data"]["total_count"] == 2

    _patch_async_client(monkeypatch, response=_FakeResponse(404, {}))
    missing = await tools.get_tool_calls_summary("856df16f1752cb53ee43dcb2f5ecfd16")
    assert missing["status"] == "error"
    assert "not found" in missing["error"]


@pytest.mark.asyncio
async def test_get_tool_calls_page_clamps_page_size_and_filters(monkeypatch):
    capture = {}
    _patch_async_client(
        monkeypatch,
        response=_FakeResponse(200, {"tool_calls": [], "pagination": {"page": 1}, "token_info": {}, "filter_applied": "search_document"}),
        capture=capture,
    )
    result = await tools.get_tool_calls_page(
        "856df16f1752cb53ee43dcb2f5ecfd16",
        page=2,
        page_size=999,
        tool_name="search_document",
    )
    assert result["status"] == "success"
    assert capture["params"]["page"] == 2
    assert capture["params"]["page_size"] == 20
    assert capture["params"]["tool_name"] == "search_document"


@pytest.mark.asyncio
async def test_get_tool_calls_page_400(monkeypatch):
    _patch_async_client(monkeypatch, response=_FakeResponse(400, {"detail": "bad page"}))
    result = await tools.get_tool_calls_page("856df16f1752cb53ee43dcb2f5ecfd16", page=-1)
    assert result["status"] == "error"
    assert "Invalid request" in result["error"]


@pytest.mark.asyncio
async def test_get_tool_call_detail_404(monkeypatch):
    _patch_async_client(monkeypatch, response=_FakeResponse(404, {}))
    result = await tools.get_tool_call_detail("856df16f1752cb53ee43dcb2f5ecfd16", "call_123")
    assert result["status"] == "error"
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_get_trace_conversation_success(monkeypatch):
    _patch_async_client(
        monkeypatch,
        response=_FakeResponse(200, {"data": {"user_query": "q", "assistant_response": "a"}, "token_info": {"estimated_tokens": 10}}),
    )
    result = await tools.get_trace_conversation("856df16f1752cb53ee43dcb2f5ecfd16")
    assert result["status"] == "success"
    assert result["data"]["user_query"] == "q"


@pytest.mark.asyncio
async def test_get_trace_view_invalid_and_400(monkeypatch):
    invalid = await tools.get_trace_view("856df16f1752cb53ee43dcb2f5ecfd16", "bogus_view")
    assert invalid["status"] == "error"
    assert "Invalid view" in invalid["error"]

    _patch_async_client(monkeypatch, response=_FakeResponse(400, {"detail": "invalid view detail"}))
    bad = await tools.get_trace_view("856df16f1752cb53ee43dcb2f5ecfd16", "token_analysis")
    assert bad["status"] == "error"
    assert "invalid view detail" in bad["error"]


@pytest.mark.asyncio
async def test_get_service_logs_success_and_error_branches(monkeypatch):
    capture = {}
    _patch_async_client(
        monkeypatch,
        response=_FakeResponse(200, {"container": "backend", "lines_returned": 5, "logs": "line1\nline2"}),
        capture=capture,
    )
    success = await tools.get_service_logs(
        container="backend",
        lines=50,
        level="fatal",
        since=15,
    )
    assert success["status"] == "success"
    assert success["data"]["lines_requested"] == 100  # clamped minimum
    assert capture["params"]["lines"] == 100
    assert capture["params"]["level"] == "FATAL"
    assert capture["params"]["since"] == 15

    _patch_async_client(monkeypatch, response=_FakeResponse(400, {"detail": "bad container"}))
    bad_container = await tools.get_service_logs(container="unknown", lines=200)
    assert bad_container["status"] == "error"
    assert "bad container" in bad_container["error"]

    _patch_async_client(monkeypatch, exc=httpx.TimeoutException("timeout"))
    timeout = await tools.get_service_logs(container="backend", lines=200)
    assert timeout["status"] == "error"
    assert "Timeout retrieving logs" in timeout["error"]

    request = httpx.Request("GET", "http://localhost:8000/api/logs/backend")
    _patch_async_client(monkeypatch, exc=httpx.ConnectError("connect failed", request=request))
    connect = await tools.get_service_logs(container="backend", lines=200)
    assert connect["status"] == "error"
    assert "Cannot connect" in connect["error"]


@pytest.mark.asyncio
async def test_get_service_logs_rejects_non_integer_since(monkeypatch):
    _patch_async_client(
        monkeypatch,
        response=_FakeResponse(200, {"container": "backend", "lines_returned": 5, "logs": "line1\nline2"}),
    )

    invalid_since = await tools.get_service_logs(container="backend", since="15")  # type: ignore[arg-type]

    assert invalid_since["status"] == "error"
    assert invalid_since["error"] == "Time filter must be an integer number of minutes"
