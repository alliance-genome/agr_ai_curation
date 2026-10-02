"""Tests for the ABC Literature client's development user-password service auth."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from agr_ai_curation_alliance.cognito_user_password import (
    CognitoUserPasswordAuthError,
    CognitoUserPasswordTokens,
)
from agr_ai_curation_alliance.document_sources.abc_literature import (
    ABCLiteratureDocumentSourceProvider,
)
from agr_ai_curation_alliance.document_sources.registration import (
    _build_abc_literature_client_config,
)
from agr_ai_curation_alliance.literature import client as client_module
from agr_ai_curation_alliance.literature.client import (
    ABCLiteratureAuthMode,
    ABCLiteratureClient,
    ABCLiteratureClientConfig,
    ABCLiteratureConfigError,
    ABCLiteratureHTTPError,
)
from src.lib.benchmarks.document_conversions import DocumentConversionService

BASE_URL = "https://literature.example/api"


def _synthetic(label: str) -> str:
    return f"synthetic-{label}-{uuid4().hex[:8]}"


def _config(**overrides) -> ABCLiteratureClientConfig:
    values = {
        "base_url": BASE_URL,
        "auth_mode": ABCLiteratureAuthMode.COGNITO_USER_PASSWORD,
        "cognito_region": "us-east-1",
        "cognito_user_pool_id": "pool-1",
        "cognito_client_id": "client-1",
        "cognito_username": "dev-service-user",
        "cognito_password": _synthetic("sign-in"),
        "cognito_refresh_skew_seconds": 600.0,
    }
    values.update(overrides)
    return ABCLiteratureClientConfig(**values)


class _FakeSignIn:
    """Stands in for Cognito and returns access tokens with chosen lifetimes."""

    def __init__(self, *lifetimes: float):
        self.lifetimes = list(lifetimes)
        self.settings: list = []

    def __call__(self, settings):
        self.settings.append(settings)
        lifetime = self.lifetimes.pop(0)
        return CognitoUserPasswordTokens(
            access_token=f"service-access-{len(self.settings)}",
            id_claims={"sub": "dev-sub"},
            expires_at=time.time() + lifetime,
        )


@pytest.fixture
def sign_in(monkeypatch):
    def install(*lifetimes: float) -> _FakeSignIn:
        fake = _FakeSignIn(*lifetimes)
        monkeypatch.setattr(client_module, "authenticate_user_password", fake)
        return fake

    monkeypatch.setattr(
        client_module, "_user_password_tokens", client_module._UserPasswordTokenCache()
    )
    return install


def _recording_http(handler=None):
    requests: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if handler is not None:
            return handler(request)
        return httpx.Response(200, json={"curie": "AGRKB:101"})

    return httpx.AsyncClient(transport=httpx.MockTransport(_handle)), requests


@pytest.mark.asyncio
async def test_user_password_mode_sends_the_signed_in_access_token(sign_in) -> None:
    fake = sign_in(3600)
    http_client, requests = _recording_http()
    client = ABCLiteratureClient(_config(), http_client=http_client)

    await client.show_reference("AGRKB:101")

    [request] = requests
    assert request.headers["Authorization"] == "Bearer service-access-1"
    [settings] = fake.settings
    assert settings.region == "us-east-1"
    assert settings.user_pool_id == "pool-1"
    assert settings.client_id == "client-1"
    assert settings.username == "dev-service-user"
    assert settings.client_secret is None


@pytest.mark.asyncio
async def test_user_password_token_is_cached_across_clients(sign_in) -> None:
    fake = sign_in(3600)
    first_http, first_requests = _recording_http()
    second_http, second_requests = _recording_http()

    await ABCLiteratureClient(_config(), http_client=first_http).show_reference("AGRKB:101")
    await ABCLiteratureClient(_config(), http_client=second_http).show_reference("AGRKB:102")

    assert len(fake.settings) == 1
    assert first_requests[0].headers["Authorization"] == "Bearer service-access-1"
    assert second_requests[0].headers["Authorization"] == "Bearer service-access-1"


@pytest.mark.asyncio
async def test_user_password_token_is_renewed_before_expiry(sign_in) -> None:
    # The first token expires inside the 600-second skew, so the next call renews.
    fake = sign_in(300, 3600)
    http_client, requests = _recording_http()
    client = ABCLiteratureClient(_config(), http_client=http_client)

    await client.show_reference("AGRKB:101")
    await client.show_reference("AGRKB:102")
    await client.show_reference("AGRKB:103")

    assert len(fake.settings) == 2
    assert [request.headers["Authorization"] for request in requests] == [
        "Bearer service-access-1",
        "Bearer service-access-2",
        "Bearer service-access-2",
    ]


@pytest.mark.asyncio
async def test_user_password_mode_passes_client_secret_for_secret_hash(sign_in) -> None:
    fake = sign_in(3600)
    client_value = _synthetic("client")
    http_client, _requests = _recording_http()
    client = ABCLiteratureClient(
        _config(cognito_client_secret=client_value), http_client=http_client
    )

    await client.show_reference("AGRKB:101")

    assert fake.settings[0].client_secret == client_value


@pytest.mark.asyncio
async def test_request_bearer_token_skips_user_password_sign_in(sign_in) -> None:
    fake = sign_in()
    http_client, requests = _recording_http()
    client = ABCLiteratureClient(_config(), http_client=http_client)

    await client.show_reference("AGRKB:101", request_bearer_token="curator-access")

    assert fake.settings == []
    assert requests[0].headers["Authorization"] == "Bearer curator-access"


@pytest.mark.parametrize(
    ("field_name", "env_name"),
    [
        ("cognito_region", "ABC_LITERATURE_COGNITO_REGION"),
        ("cognito_user_pool_id", "ABC_LITERATURE_COGNITO_USER_POOL_ID"),
        ("cognito_client_id", "ABC_LITERATURE_COGNITO_CLIENT_ID"),
        ("cognito_username", "ABC_LITERATURE_COGNITO_USERNAME"),
        ("cognito_password", "ABC_LITERATURE_COGNITO_PASSWORD"),
    ],
)
def test_user_password_mode_fails_closed_on_missing_settings(field_name, env_name) -> None:
    config = _config(**{field_name: "  "})

    with pytest.raises(ABCLiteratureConfigError) as exc_info:
        ABCLiteratureClient(config, http_client=_recording_http()[0])

    message = str(exc_info.value)
    assert env_name in message
    assert "cognito_user_password" in message
    assert "dev-service-user" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        CognitoUserPasswordAuthError("Cognito user-password authentication returned no token."),
        client_module.ClientError(
            {"Error": {"Code": "NotAuthorizedException", "Message": "denied"}},
            "InitiateAuth",
        ),
        client_module.jwt.InvalidIssuerError("bad issuer"),
    ],
    ids=["unusable-result", "cognito-rejected", "token-validation"],
)
async def test_user_password_sign_in_failure_is_a_sanitized_client_error(
    monkeypatch, sign_in, failure
) -> None:
    sign_in()

    def _fail(_settings):
        raise failure

    monkeypatch.setattr(client_module, "authenticate_user_password", _fail)
    http_client, requests = _recording_http()
    client = ABCLiteratureClient(_config(), http_client=http_client)

    with pytest.raises(ABCLiteratureHTTPError) as exc_info:
        await client.show_reference("AGRKB:101")

    assert exc_info.value.status_code == 502
    assert exc_info.value.endpoint == "token"
    assert requests == []


def test_config_repr_redacts_user_password_identity() -> None:
    config = _config()

    rendered = repr(config)

    assert config.cognito_password not in rendered
    assert "dev-service-user" not in rendered


# --- Benchmark source-reference conversion through the configured service auth ---

SOURCE_REFERENCE = "AGRKB:101"
MAIN_TEXT = "# Synthetic paper\n\n## Results\n\nThe synthetic marker is expressed.\n"


def _set_user_password_env(monkeypatch) -> None:
    monkeypatch.setenv("ABC_LITERATURE_API_BASE_URL", BASE_URL)
    monkeypatch.setenv("ABC_LITERATURE_AUTH_MODE", "cognito_user_password")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_USER_POOL_ID", "pool-1")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_CLIENT_ID", "client-1")
    monkeypatch.delenv("ABC_LITERATURE_COGNITO_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_USERNAME", "dev-service-user")
    monkeypatch.setenv("ABC_LITERATURE_COGNITO_PASSWORD", _synthetic("sign-in"))
    monkeypatch.delenv("ABC_LITERATURE_COGNITO_REFRESH_SKEW_SECONDS", raising=False)


def _abc_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path.removeprefix("/api")
    if path == "/reference/AGRKB:101":
        return httpx.Response(
            200, json={"reference_id": 101, "reference_curie": SOURCE_REFERENCE}
        )
    if path == "/reference/referencefile/show_all/AGRKB:101":
        return httpx.Response(
            200,
            json=[
                {
                    "referencefile_id": 10,
                    "reference_id": 101,
                    "display_name": "main",
                    "file_class": "main",
                    "file_extension": "pdf",
                    "file_publication_status": "final",
                    "pdf_type": "pdf",
                    "referencefile_mods": [{"mod_abbreviation": None}],
                },
                {
                    "referencefile_id": 11,
                    "reference_id": 101,
                    "display_name": "main_nxml",
                    "file_class": "converted_merged_main",
                    "file_extension": "md",
                    "file_publication_status": "final",
                    "referencefile_mods": [{"mod_abbreviation": None}],
                },
            ],
        )
    if path == "/reference/referencefile/download_file/11":
        return httpx.Response(200, content=MAIN_TEXT.encode("utf-8"))
    return httpx.Response(404, json={"detail": "not found"})


class _Repository:
    def __init__(self, row):
        self.row = row
        self.failed: list = []
        self.succeeded: list = []

    def mark_running(self, _db, _conversion_id):
        self.row.status = "running"
        return self.row

    def mark_succeeded(self, _db, _conversion_id, *, snapshot_id, identity):
        self.row.status = "succeeded"
        self.succeeded.append(identity)
        return self.row

    def mark_failed(self, _db, _conversion_id, *, code, message):
        self.row.status = "failed"
        self.failed.append((code, message))
        return self.row


@contextmanager
def _session():
    yield MagicMock()


@pytest.mark.asyncio
async def test_benchmark_source_conversion_uses_configured_user_password_auth(
    monkeypatch, sign_in
) -> None:
    _set_user_password_env(monkeypatch)
    fake = sign_in(3600)
    http_client, requests = _recording_http(_abc_handler)
    provider = ABCLiteratureDocumentSourceProvider(
        ABCLiteratureClient(_build_abc_literature_client_config(), http_client=http_client)
    )
    row = SimpleNamespace(
        id=uuid4(),
        owner_subject="service:portal",
        service_principal="portal-client",
        curator_subject="synthetic-curator",
        curator_db_user_id=7,
        input_kind="source_reference",
        source_digest=None,
        source_blob_reference=None,
        source_reference=SOURCE_REFERENCE,
        status="queued",
    )
    repository = _Repository(row)
    frozen: list = []
    service = DocumentConversionService(
        session_factory=_session,
        repository=repository,
        snapshot_store_factory=MagicMock,
        snapshot_repository_factory=lambda _db, _store: SimpleNamespace(
            freeze_input=lambda source, **_kw: frozen.append(source)
            or SimpleNamespace(id=uuid4())
        ),
        max_input_bytes=lambda: 1_000_000,
    )

    with patch(
        "src.lib.benchmarks.document_conversions.get_configured_document_source_provider",
        return_value=provider,
    ):
        await service.run(row.id, authorized_group_ids=("group-alpha",))

    assert repository.failed == []
    assert len(fake.settings) == 1
    assert [request.url.path.removeprefix("/api") for request in requests] == [
        "/reference/AGRKB:101",
        "/reference/referencefile/show_all/AGRKB:101",
        "/reference/referencefile/download_file/11",
    ]
    assert {request.headers["Authorization"] for request in requests} == {
        "Bearer service-access-1"
    }
    [snapshot] = frozen
    assert any("synthetic marker" in element["text"] for element in json.loads(snapshot.content))
