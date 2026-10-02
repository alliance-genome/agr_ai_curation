"""Tests for the Alliance machine reader used by login-free development imports."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from agr_ai_curation_alliance.document_sources import dev_reader_auth as auth
from agr_ai_curation_alliance.literature import client as client_module


def _set_reader_env(monkeypatch, *, mode: str = "cognito_client_credentials") -> str:
    reader_value = "synthetic-" + uuid4().hex
    monkeypatch.setenv("ABC_LITERATURE_API_BASE_URL", "https://literature.example/api")
    monkeypatch.setenv("ABC_LITERATURE_AUTH_MODE", mode)
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_TOKEN_URL", "https://auth.example/oauth2/token")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_CLIENT_ID", "synthetic-reader-client")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_CLIENT_SECRET", reader_value)
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_SCOPE", "abc-literature/read")
    return reader_value


def _record_clients(monkeypatch, issue):
    clients: list = []
    original_init = client_module.ABCLiteratureClient.__init__

    def tracking_init(self, config, **kwargs):
        original_init(self, config, **kwargs)
        clients.append(self)

    async def fake_token(self):
        return await issue(self)

    closed: list = []

    async def tracking_close(self):
        closed.append(self)

    monkeypatch.setattr(client_module.ABCLiteratureClient, "__init__", tracking_init)
    monkeypatch.setattr(
        client_module.ABCLiteratureClient, "client_credentials_token", fake_token
    )
    monkeypatch.setattr(client_module.ABCLiteratureClient, "aclose", tracking_close)
    return clients, closed


@pytest.mark.asyncio
async def test_reader_issues_machine_bearer_from_abc_client_credentials(monkeypatch):
    _set_reader_env(monkeypatch)
    expires_at = time.time() + 3600

    async def issue(client):
        assert client.config.auth_mode is client_module.ABCLiteratureAuthMode.COGNITO_CLIENT_CREDENTIALS
        assert client.config.cognito_scope == "abc-literature/read"
        return "reader-access-token", expires_at

    clients, closed = _record_clients(monkeypatch, issue)

    credentials = await auth.resolve_development_reader()

    assert credentials.token == "reader-access-token"
    assert credentials.expires_at == expires_at
    assert "reader-access-token" not in repr(credentials)
    assert closed == clients and len(clients) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["none", "static_bearer"])
async def test_reader_requires_client_credentials_mode(monkeypatch, mode):
    _set_reader_env(monkeypatch, mode=mode)

    async def issue(_client):
        pytest.fail("no token may be requested outside client-credentials mode")

    _record_clients(monkeypatch, issue)

    with pytest.raises(
        auth.DevelopmentReaderUnavailable, match="cognito_client_credentials"
    ):
        await auth.resolve_development_reader()


@pytest.mark.asyncio
async def test_reader_fails_closed_without_base_url(monkeypatch):
    _set_reader_env(monkeypatch)
    monkeypatch.delenv("ABC_LITERATURE_API_BASE_URL")

    with pytest.raises(auth.DevelopmentReaderUnavailable, match="misconfigured"):
        await auth.resolve_development_reader()


@pytest.mark.asyncio
async def test_reader_fails_closed_with_incomplete_client_settings(monkeypatch):
    reader_value = _set_reader_env(monkeypatch)
    monkeypatch.delenv("ABC_LITERATURE_COGNITO_SCOPE")

    with pytest.raises(auth.DevelopmentReaderUnavailable) as exc_info:
        await auth.resolve_development_reader()

    assert "misconfigured" in str(exc_info.value)
    assert reader_value not in str(exc_info.value)


@pytest.mark.asyncio
async def test_reader_token_failure_is_sanitized(monkeypatch):
    reader_value = _set_reader_env(monkeypatch)

    async def issue(_client):
        raise client_module.ABCLiteratureHTTPError(
            f"token endpoint rejected {reader_value}", status_code=400, endpoint="token"
        )

    clients, closed = _record_clients(monkeypatch, issue)

    with pytest.raises(auth.DevelopmentReaderUnavailable) as exc_info:
        await auth.resolve_development_reader()

    assert reader_value not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert closed == clients


@pytest.mark.asyncio
async def test_static_provider_bearer_cannot_substitute_for_the_reader(monkeypatch):
    _set_reader_env(monkeypatch, mode="static_bearer")
    monkeypatch.setenv("ABC_LITERATURE_BEARER_TOKEN", "stale-provider-token")

    with pytest.raises(auth.DevelopmentReaderUnavailable):
        await auth.resolve_development_reader()
