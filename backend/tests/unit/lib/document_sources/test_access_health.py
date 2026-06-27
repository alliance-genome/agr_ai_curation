"""Tests for document-source access and health helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.lib.document_sources.access import build_document_source_request_context
from src.lib.document_sources.health import check_configured_document_source_health
from src.lib.document_sources.models import (
    DocumentSourceConfigError,
    DocumentSourceHealth,
)


class FakeProvider:
    provider_id = "fake_provider"

    def __init__(self, health: DocumentSourceHealth):
        self.health_payload = health
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.aclose()

    async def aclose(self):
        self.closed = True

    async def health(self) -> DocumentSourceHealth:
        return self.health_payload


def request_with_cookies(cookies: dict[str, str]):
    return SimpleNamespace(cookies=cookies)


def test_build_document_source_request_context_maps_groups_and_token() -> None:
    request = request_with_cookies({"auth_token": "curator-token"})

    context = build_document_source_request_context(
        request=request,  # type: ignore[arg-type]
        user_claims={
            "cognito:groups": ["MGIStaff", "unknown-group"],
            "groups": ["MGICurator"],
        },
    )

    assert context.provider_groups == ("MGIStaff", "unknown-group", "MGICurator")
    assert context.authorized_group_ids == ("MGI",)
    assert context.curator_token == "curator-token"
    assert context.has_curator_token is True
    assert "curator-token" not in repr(context)


def test_build_document_source_request_context_ignores_cookie_for_api_key_claims() -> None:
    request = request_with_cookies({"auth_token": "unvalidated-cookie-token"})

    context = build_document_source_request_context(
        request=request,  # type: ignore[arg-type]
        user_claims={
            "sub": "api-key-test-user",
            "groups": ["MGICurator"],
        },
    )

    assert context.authorized_group_ids == ("MGI",)
    assert context.curator_token is None
    assert context.has_curator_token is False


def test_build_document_source_request_context_ignores_cookie_in_dev_mode(
    monkeypatch,
) -> None:
    request = request_with_cookies({"auth_token": "dev-cookie-token"})
    monkeypatch.setattr("src.lib.document_sources.access.is_dev_mode", lambda: True)

    context = build_document_source_request_context(
        request=request,  # type: ignore[arg-type]
        user_claims={
            "sub": "dev-user-123",
            "groups": ["MGICurator"],
        },
    )

    assert context.authorized_group_ids == ("MGI",)
    assert context.curator_token is None


def test_build_document_source_request_context_accepts_comma_group_string() -> None:
    context = build_document_source_request_context(
        request=None,
        user_claims={"groups": "flybase-curators, wormbase-curators"},
    )

    assert context.provider_groups == ("flybase-curators", "wormbase-curators")
    assert context.authorized_group_ids == ("FB", "WB")
    assert context.curator_token is None


@pytest.mark.asyncio
async def test_document_source_health_disabled_does_not_construct_provider(monkeypatch):
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_abc_literature_import_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_document_source_provider",
        lambda: "abc_literature",
    )

    def fail_provider(_provider_id):
        raise AssertionError("provider should not be constructed when disabled")

    monkeypatch.setattr(
        "src.lib.document_sources.health.get_configured_document_source_provider",
        fail_provider,
    )

    result = await check_configured_document_source_health()

    assert result.ok is True
    assert result.provider == "abc_literature"
    assert result.metadata == {"enabled": False}


@pytest.mark.asyncio
async def test_document_source_health_local_pdf_enabled_is_healthy(monkeypatch):
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_abc_literature_import_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_document_source_provider",
        lambda: "local_pdf",
    )

    result = await check_configured_document_source_health()

    assert result.ok is True
    assert result.provider == "local_pdf"
    assert result.metadata == {"enabled": True}


@pytest.mark.asyncio
async def test_document_source_health_local_pdf_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_abc_literature_import_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_document_source_provider",
        lambda: "LOCAL_PDF",
    )

    result = await check_configured_document_source_health()

    assert result.ok is True
    assert result.provider == "local_pdf"
    assert result.metadata == {"enabled": True}


@pytest.mark.asyncio
async def test_document_source_health_wraps_provider_config_error(monkeypatch):
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_abc_literature_import_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_document_source_provider",
        lambda: "abc_literature",
    )

    def raise_config_error(_provider_id):
        raise DocumentSourceConfigError("ABC_LITERATURE_API_BASE_URL is required")

    monkeypatch.setattr(
        "src.lib.document_sources.health.get_configured_document_source_provider",
        raise_config_error,
    )

    result = await check_configured_document_source_health()

    assert result.ok is False
    assert result.provider == "abc_literature"
    assert result.message == "Document-source provider misconfigured"
    assert "ABC_LITERATURE_API_BASE_URL" not in result.message
    assert result.metadata == {"enabled": True, "reason": "configuration"}


@pytest.mark.asyncio
async def test_document_source_health_closes_provider(monkeypatch):
    fake_provider = FakeProvider(
        DocumentSourceHealth(
            provider="fake_provider",
            ok=True,
            message="ok",
            metadata={"endpoint": "search"},
        )
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_abc_literature_import_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_document_source_provider",
        lambda: "fake_provider",
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_configured_document_source_provider",
        lambda _provider_id: fake_provider,
    )

    result = await check_configured_document_source_health()

    assert result.ok is True
    assert result.provider == "fake_provider"
    assert result.message == "Document-source provider ready"
    assert result.metadata == {"endpoint": "search", "enabled": True}
    assert fake_provider.closed is True


@pytest.mark.asyncio
async def test_document_source_health_sanitizes_unhealthy_provider_message(monkeypatch):
    fake_provider = FakeProvider(
        DocumentSourceHealth(
            provider="fake_provider",
            ok=False,
            message="raw provider failure with endpoint details",
            metadata={},
        )
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_abc_literature_import_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_document_source_provider",
        lambda: "fake_provider",
    )
    monkeypatch.setattr(
        "src.lib.document_sources.health.get_configured_document_source_provider",
        lambda _provider_id: fake_provider,
    )

    result = await check_configured_document_source_health()

    assert result.ok is False
    assert result.message == "Document-source provider unavailable"
    assert "raw provider failure" not in result.message
