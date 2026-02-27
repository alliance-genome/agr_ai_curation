"""Unit tests for logs API endpoint."""

import asyncio

import pytest
from fastapi import HTTPException

from src.api import logs as logs_api


class _FakeProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_get_container_logs_rejects_invalid_container():
    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("not-allowed", lines=2000)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_container_logs_success(monkeypatch):
    captured = {}

    async def _fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProcess(returncode=0, stdout=b"line1\nline2\n", stderr=b"")

    async def _fake_wait_for(coro, timeout):
        assert timeout == 10.0
        return await coro

    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "myproj")
    monkeypatch.setattr(logs_api.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(logs_api.asyncio, "wait_for", _fake_wait_for)

    payload = await logs_api.get_container_logs("backend", lines=120)
    assert payload.container == "backend"
    assert payload.lines_returned == 2
    assert payload.logs == "line1\nline2\n"
    assert captured["cmd"] == ("docker", "logs", "--tail", "120", "myproj-backend-1")
    assert captured["kwargs"]["shell"] is False


@pytest.mark.asyncio
async def test_get_container_logs_nonzero_returncode_maps_to_500(monkeypatch):
    async def _fake_create_subprocess_exec(*_cmd, **_kwargs):
        return _FakeProcess(returncode=1, stdout=b"", stderr=b"not running")

    async def _fake_wait_for(coro, timeout):
        return await coro

    monkeypatch.setattr(logs_api.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(logs_api.asyncio, "wait_for", _fake_wait_for)

    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("backend", lines=200)
    assert exc.value.status_code == 500
    assert "Unexpected error" in exc.value.detail


@pytest.mark.asyncio
async def test_get_container_logs_handles_timeout(monkeypatch):
    async def _fake_create_subprocess_exec(*_cmd, **_kwargs):
        return _FakeProcess(returncode=0, stdout=b"", stderr=b"")

    async def _timeout(coro, timeout):
        coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(logs_api.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(logs_api.asyncio, "wait_for", _timeout)

    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("backend", lines=200)
    assert exc.value.status_code == 500
    assert "Timeout retrieving logs" in exc.value.detail


@pytest.mark.asyncio
async def test_get_container_logs_handles_missing_docker_cli(monkeypatch):
    async def _missing_docker(*_cmd, **_kwargs):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(logs_api.asyncio, "create_subprocess_exec", _missing_docker)

    with pytest.raises(HTTPException) as exc:
        await logs_api.get_container_logs("backend", lines=200)
    assert exc.value.status_code == 500
    assert "Docker CLI not found" in exc.value.detail
