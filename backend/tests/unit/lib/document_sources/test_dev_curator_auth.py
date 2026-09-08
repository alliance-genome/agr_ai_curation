"""Tests for provider-neutral renewable development credentials."""

import asyncio

import pytest

from src.lib.document_sources import dev_curator_auth as auth
from src.lib.packages.document_source_provider_models import DevCuratorCredentials


def _credentials(*, token: str = "provider-token", expires_at: float = 5000):
    return DevCuratorCredentials(
        token=token,
        claims={"sub": "curator", "groups": ["staff"]},
        expires_at=expires_at,
    )


@pytest.mark.parametrize(
    ("dev_mode", "enabled", "provider", "expected"),
    [
        (True, True, "abc_literature", True),
        (False, True, "abc_literature", False),
        (True, False, "abc_literature", False),
        (True, True, "local_pdf", False),
        (True, True, "LOCAL_PDF", False),
    ],
)
def test_renewable_auth_gating(
    monkeypatch, dev_mode, enabled, provider, expected
) -> None:
    monkeypatch.setattr(auth, "is_dev_mode", lambda: dev_mode)
    monkeypatch.setattr(auth, "get_document_source_import_enabled", lambda: enabled)
    monkeypatch.setattr(auth, "get_document_source_provider", lambda: provider)

    assert auth.renewable_dev_curator_auth_required() is expected


@pytest.mark.asyncio
async def test_cache_reuse_refresh_and_concurrent_callers(monkeypatch) -> None:
    service = auth.DevCuratorCredentialService()
    monkeypatch.setattr(auth, "renewable_dev_curator_auth_required", lambda: True)
    monkeypatch.setattr(
        auth, "get_document_source_dev_curator_refresh_skew_seconds", lambda: 600
    )
    monkeypatch.setattr(
        auth, "get_document_source_import_timeout_seconds", lambda: 300.0
    )
    now = {"value": 1000.0}
    monkeypatch.setattr(auth.time, "time", lambda: now["value"])
    calls = 0

    async def authenticate():
        nonlocal calls
        calls += 1
        return _credentials(token=f"token-{calls}", expires_at=now["value"] + 1000)

    monkeypatch.setattr(
        auth,
        "get_document_source_development_credential_resolver",
        lambda _: authenticate,
    )

    first = await asyncio.gather(*(service.get_credentials() for _ in range(8)))
    assert calls == 1
    assert {item.token for item in first} == {"token-1"}

    again = await service.get_credentials()
    assert again.token == "token-1"
    assert calls == 1

    now["value"] = 1450.0
    refreshed = await service.get_credentials()
    assert refreshed.token == "token-2"
    assert calls == 2


@pytest.mark.asyncio
async def test_service_sanitizes_provider_failures(monkeypatch) -> None:
    service = auth.DevCuratorCredentialService()
    monkeypatch.setattr(auth, "renewable_dev_curator_auth_required", lambda: True)

    async def fail():
        raise RuntimeError("fake-curator password-value client-secret-value")

    monkeypatch.setattr(
        auth, "get_document_source_development_credential_resolver", lambda _: fail
    )

    with pytest.raises(auth.DevCuratorCredentialUnavailable) as exc_info:
        await service.get_credentials()
    message = str(exc_info.value)
    assert "fake-curator" not in message
    assert "password-value" not in message
    assert "client-secret-value" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        "static-token",
        _credentials(token=""),
        _credentials(expires_at=0),
        _credentials(expires_at=float("nan")),
    ],
)
async def test_invalid_credentials_fail_closed(monkeypatch, result):
    monkeypatch.setattr(auth, "renewable_dev_curator_auth_required", lambda: True)

    async def resolve():
        return result

    monkeypatch.setattr(
        auth, "get_document_source_development_credential_resolver", lambda _: resolve
    )
    with pytest.raises(
        auth.DevCuratorCredentialUnavailable, match="invalid credentials"
    ):
        await auth.DevCuratorCredentialService().get_credentials()


@pytest.mark.asyncio
async def test_resolver_timeout_is_sanitized(monkeypatch):
    monkeypatch.setattr(auth, "renewable_dev_curator_auth_required", lambda: True)
    monkeypatch.setattr(
        auth, "get_document_source_request_timeout_seconds", lambda: 0.001
    )

    async def resolve():
        await asyncio.Event().wait()

    monkeypatch.setattr(
        auth, "get_document_source_development_credential_resolver", lambda _: resolve
    )
    with pytest.raises(auth.DevCuratorCredentialUnavailable, match="unavailable"):
        await auth.DevCuratorCredentialService().get_credentials()


@pytest.mark.asyncio
async def test_cached_credentials_are_scoped_to_provider_and_resolver(monkeypatch):
    monkeypatch.setattr(auth, "renewable_dev_curator_auth_required", lambda: True)

    async def first():
        return _credentials(token="first", expires_at=auth.time.time() + 10000)

    async def second():
        return _credentials(token="second", expires_at=auth.time.time() + 10000)

    selected = {"provider": "first", "resolver": first}
    monkeypatch.setattr(
        auth, "get_document_source_provider", lambda: selected["provider"]
    )
    monkeypatch.setattr(
        auth,
        "get_document_source_development_credential_resolver",
        lambda _: selected["resolver"],
    )
    service = auth.DevCuratorCredentialService()
    assert (await service.get_credentials()).token == "first"
    selected.update(provider="second", resolver=second)
    assert (await service.get_credentials()).token == "second"
    selected.update(provider="first", resolver=second)
    assert (await service.get_credentials()).token == "second"
