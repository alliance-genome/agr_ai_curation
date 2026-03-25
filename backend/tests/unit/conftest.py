"""Shared unit-test fixtures."""

import httpx
import pytest


class _FakeAsyncClient:
    def __init__(self, *, response=None, exc=None, capture=None, timeout=None):
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
        if self._exc is not None:
            raise self._exc
        return self._response


@pytest.fixture
def loki_response():
    def _build(target_module, payload, *, status_code=200):
        request = httpx.Request(
            "GET",
            f"{target_module.DEFAULT_LOKI_URL}{target_module.LOKI_QUERY_RANGE_PATH}",
        )
        return httpx.Response(status_code, json=payload, request=request)

    return _build


@pytest.fixture
def patch_loki_async_client(monkeypatch):
    def _patch(target_module, *, response=None, exc=None, capture=None):
        monkeypatch.setattr(
            target_module.httpx,
            "AsyncClient",
            lambda timeout=None: _FakeAsyncClient(
                response=response,
                exc=exc,
                capture=capture,
                timeout=timeout,
            ),
        )

    return _patch
