"""Tests for renewable login-free development curator credentials."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
from types import SimpleNamespace

import pytest

from src.lib.document_sources import dev_curator_auth as auth


def _settings() -> auth._CognitoSettings:
    return auth._CognitoSettings(
        region="us-east-1",
        user_pool_id="pool-1",
        client_id="client-1",
        client_secret="client-secret-value",
        username="fake-curator",
        password="password-value",
        request_timeout_seconds=7.5,
    )


def _credentials(*, token: str = "id-token", expires_at: float = 5000) -> auth.DevCuratorCredentials:
    return auth.DevCuratorCredentials(
        token=token,
        claims={"sub": "curator-sub", "cognito:groups": ["FBStaff"]},
        expires_at=expires_at,
    )


def test_secret_hash_matches_cognito_contract() -> None:
    expected = base64.b64encode(
        hmac.new(
            b"client-secret-value",
            b"fake-curatorclient-1",
            hashlib.sha256,
        ).digest()
    ).decode("ascii")

    assert auth._secret_hash(
        username="fake-curator",
        client_id="client-1",
        client_secret="client-secret-value",
    ) == expected


def test_secret_bearing_dataclasses_redact_repr() -> None:
    settings_repr = repr(_settings())
    credential_repr = repr(_credentials(token="sensitive-id-token"))

    for secret in (
        "pool-1",
        "client-1",
        "client-secret-value",
        "fake-curator",
        "password-value",
    ):
        assert secret not in settings_repr
    assert "sensitive-id-token" not in credential_repr
    assert "curator-sub" not in credential_repr


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
def test_renewable_auth_gating(monkeypatch, dev_mode, enabled, provider, expected) -> None:
    monkeypatch.setattr(auth, "is_dev_mode", lambda: dev_mode)
    monkeypatch.setattr(auth, "get_document_source_import_enabled", lambda: enabled)
    monkeypatch.setattr(auth, "get_document_source_provider", lambda: provider)

    assert auth.renewable_dev_curator_auth_required() is expected


def test_load_settings_requires_supported_mode_and_all_values(monkeypatch) -> None:
    monkeypatch.setattr(auth, "get_document_source_dev_curator_auth_mode", lambda: "none")
    with pytest.raises(auth.DevCuratorCredentialUnavailable) as exc_info:
        auth._load_settings()
    assert "username" not in str(exc_info.value).lower()

    monkeypatch.setattr(auth, "get_document_source_dev_curator_auth_mode", lambda: "bad-mode")
    with pytest.raises(auth.DevCuratorCredentialUnavailable, match="misconfigured"):
        auth._load_settings()

    monkeypatch.setattr(
        auth,
        "get_document_source_dev_curator_auth_mode",
        lambda: auth.COGNITO_USER_PASSWORD_MODE,
    )
    for name in (
        "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_REGION",
        "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_USER_POOL_ID",
        "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_ID",
        "DOCUMENT_SOURCE_DEV_CURATOR_COGNITO_CLIENT_SECRET",
        "DOCUMENT_SOURCE_DEV_CURATOR_USERNAME",
        "DOCUMENT_SOURCE_DEV_CURATOR_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(auth.DevCuratorCredentialUnavailable) as exc_info:
        auth._load_settings()
    assert "DOCUMENT_SOURCE" not in str(exc_info.value)


def test_authenticate_uses_password_flow_and_validates_paired_tokens(monkeypatch) -> None:
    observed = {}

    class FakeClient:
        def initiate_auth(self, **kwargs):
            observed["auth"] = kwargs
            return {
                "AuthenticationResult": {
                    "IdToken": "signed-id-token",
                    "AccessToken": "signed-access-token",
                }
            }

    def fake_boto_client(service, *, region_name, config):
        observed["service"] = service
        observed["region"] = region_name
        observed["connect_timeout"] = config.connect_timeout
        observed["read_timeout"] = config.read_timeout
        return FakeClient()

    class FakeJWKClient:
        def __init__(self, url, *, timeout):
            observed["jwks_url"] = url
            observed["jwks_timeout"] = timeout

        def get_signing_key_from_jwt(self, token):
            observed.setdefault("signing_tokens", []).append(token)
            return SimpleNamespace(key=f"public-key-for-{token}")

    def fake_decode(token, key, **kwargs):
        observed.setdefault("decodes", []).append(
            {"token": token, "key": key, **kwargs}
        )
        if token == "signed-id-token":
            return {
                "sub": "curator-sub",
                "exp": 4102444800,
                "token_use": "id",
                "cognito:groups": ["FBStaff", "FlyBaseCurator"],
            }
        return {
            "sub": "curator-sub",
            "exp": 4102444700,
            "token_use": "access",
            "client_id": "client-1",
        }

    monkeypatch.setattr(auth.boto3, "client", fake_boto_client)
    monkeypatch.setattr(auth, "PyJWKClient", FakeJWKClient)
    monkeypatch.setattr(auth.jwt, "decode", fake_decode)

    result = auth._authenticate_sync(_settings())

    assert observed["service"] == "cognito-idp"
    assert observed["region"] == "us-east-1"
    assert observed["connect_timeout"] == 7.5
    assert observed["read_timeout"] == 7.5
    assert observed["auth"]["AuthFlow"] == "USER_PASSWORD_AUTH"
    assert observed["auth"]["ClientId"] == "client-1"
    parameters = observed["auth"]["AuthParameters"]
    assert parameters["USERNAME"] == "fake-curator"
    assert parameters["PASSWORD"] == "password-value"
    assert parameters["SECRET_HASH"] == auth._secret_hash(
        username="fake-curator",
        client_id="client-1",
        client_secret="client-secret-value",
    )
    assert observed["jwks_url"].endswith("/pool-1/.well-known/jwks.json")
    assert observed["jwks_timeout"] == 8
    assert observed["signing_tokens"] == ["signed-id-token", "signed-access-token"]
    id_decode, access_decode = observed["decodes"]
    assert id_decode["audience"] == "client-1"
    assert id_decode["issuer"].endswith("/pool-1")
    assert id_decode["options"]["require"] == ["exp", "sub", "token_use"]
    assert access_decode["issuer"].endswith("/pool-1")
    assert access_decode["options"] == {
        "require": ["exp", "sub", "token_use", "client_id"],
        "verify_aud": False,
    }
    assert result.token == "signed-access-token"
    assert result.claims["cognito:groups"] == ["FBStaff", "FlyBaseCurator"]
    assert result.expires_at == 4102444700


def test_authenticate_suppresses_aws_sdk_debug_secret_logging(monkeypatch, caplog) -> None:
    password_marker = "password-debug-marker"
    token_marker = "token-debug-marker"
    botocore_logger = logging.getLogger("botocore.endpoint")
    boto3_logger = logging.getLogger("boto3.resources")
    logging.getLogger("botocore").setLevel(logging.DEBUG)
    logging.getLogger("boto3").setLevel(logging.DEBUG)

    class FakeClient:
        def initiate_auth(self, **_kwargs):
            boto3_logger.debug("resource password=%s", password_marker)
            botocore_logger.debug("request password=%s", password_marker)
            botocore_logger.debug("response id_token=%s", token_marker)
            return {
                "AuthenticationResult": {
                    "IdToken": "signed-id-token",
                    "AccessToken": "signed-access-token",
                }
            }

    monkeypatch.setattr(
        auth.boto3,
        "client",
        lambda *_args, **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        auth,
        "PyJWKClient",
        lambda *_args, **_kwargs: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key="key")
        ),
    )
    monkeypatch.setattr(
        auth.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "sub": "subject",
            "exp": 4102444800,
            "token_use": (
                "access" if _args and _args[0] == "signed-access-token" else "id"
            ),
            **(
                {"client_id": "client-1"}
                if _args and _args[0] == "signed-access-token"
                else {}
            ),
        },
    )

    with caplog.at_level(logging.DEBUG):
        auth._authenticate_sync(_settings())

    rendered = caplog.text
    assert password_marker not in rendered
    assert token_marker not in rendered
    assert logging.getLogger("botocore").level == logging.WARNING
    assert logging.getLogger("boto3").level == logging.WARNING


@pytest.mark.parametrize(
    "response",
    [
        {"ChallengeName": "NEW_PASSWORD_REQUIRED"},
        {},
        {"AuthenticationResult": {}},
        {"AuthenticationResult": {"IdToken": "id-only"}},
        {"AuthenticationResult": {"AccessToken": "access-only"}},
    ],
)
def test_authenticate_rejects_challenge_and_malformed_responses(monkeypatch, response) -> None:
    monkeypatch.setattr(
        auth.boto3,
        "client",
        lambda *_args, **_kwargs: SimpleNamespace(
            initiate_auth=lambda **_auth_kwargs: response
        ),
    )

    with pytest.raises(auth.DevCuratorCredentialUnavailable):
        auth._authenticate_sync(_settings())


def test_authenticate_rejects_non_id_token(monkeypatch) -> None:
    monkeypatch.setattr(
        auth.boto3,
        "client",
        lambda *_args, **_kwargs: SimpleNamespace(
            initiate_auth=lambda **_auth_kwargs: {
                "AuthenticationResult": {
                    "IdToken": "wrong-id-token",
                    "AccessToken": "access-token",
                }
            }
        ),
    )
    monkeypatch.setattr(
        auth,
        "PyJWKClient",
        lambda *_args, **_kwargs: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key="key")
        ),
    )
    monkeypatch.setattr(
        auth.jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "sub": "subject",
            "exp": 4102444800,
            "token_use": "access",
        },
    )

    with pytest.raises(auth.DevCuratorCredentialUnavailable, match="wrong token type"):
        auth._authenticate_sync(_settings())


def test_authenticate_rejects_expired_id_token(monkeypatch) -> None:
    monkeypatch.setattr(
        auth.boto3,
        "client",
        lambda *_args, **_kwargs: SimpleNamespace(
            initiate_auth=lambda **_auth_kwargs: {
                "AuthenticationResult": {
                    "IdToken": "expired-id-token",
                    "AccessToken": "access-token",
                }
            }
        ),
    )
    monkeypatch.setattr(
        auth,
        "PyJWKClient",
        lambda *_args, **_kwargs: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key="key")
        ),
    )

    def _expired(*_args, **_kwargs):
        raise auth.jwt.ExpiredSignatureError("expired")

    monkeypatch.setattr(auth.jwt, "decode", _expired)

    with pytest.raises(auth.jwt.ExpiredSignatureError):
        auth._authenticate_sync(_settings())


@pytest.mark.parametrize(
    ("access_claims", "message"),
    [
        (
            {
                "sub": "subject",
                "exp": 4102444800,
                "token_use": "id",
                "client_id": "client-1",
            },
            "wrong bearer token type",
        ),
        (
            {
                "sub": "subject",
                "exp": 4102444800,
                "token_use": "access",
                "client_id": "other-client",
            },
            "wrong bearer client",
        ),
        (
            {
                "sub": "other-subject",
                "exp": 4102444800,
                "token_use": "access",
                "client_id": "client-1",
            },
            "mismatched token identities",
        ),
    ],
)
def test_authenticate_rejects_invalid_access_token_claims(
    monkeypatch,
    access_claims,
    message,
) -> None:
    monkeypatch.setattr(
        auth.boto3,
        "client",
        lambda *_args, **_kwargs: SimpleNamespace(
            initiate_auth=lambda **_auth_kwargs: {
                "AuthenticationResult": {
                    "IdToken": "id-token",
                    "AccessToken": "access-token",
                }
            }
        ),
    )
    monkeypatch.setattr(
        auth,
        "PyJWKClient",
        lambda *_args, **_kwargs: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key="key")
        ),
    )
    monkeypatch.setattr(
        auth.jwt,
        "decode",
        lambda token, *_args, **_kwargs: (
            {
                "sub": "subject",
                "exp": 4102444900,
                "token_use": "id",
            }
            if token == "id-token"
            else access_claims
        ),
    )

    with pytest.raises(auth.DevCuratorCredentialUnavailable, match=message):
        auth._authenticate_sync(_settings())


def test_authenticate_rejects_expired_access_token(monkeypatch) -> None:
    monkeypatch.setattr(
        auth.boto3,
        "client",
        lambda *_args, **_kwargs: SimpleNamespace(
            initiate_auth=lambda **_auth_kwargs: {
                "AuthenticationResult": {
                    "IdToken": "id-token",
                    "AccessToken": "expired-access-token",
                }
            }
        ),
    )
    monkeypatch.setattr(
        auth,
        "PyJWKClient",
        lambda *_args, **_kwargs: SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: SimpleNamespace(key="key")
        ),
    )

    def _decode(token, *_args, **_kwargs):
        if token == "expired-access-token":
            raise auth.jwt.ExpiredSignatureError("expired")
        return {
            "sub": "subject",
            "exp": 4102444900,
            "token_use": "id",
        }

    monkeypatch.setattr(auth.jwt, "decode", _decode)

    with pytest.raises(auth.jwt.ExpiredSignatureError):
        auth._authenticate_sync(_settings())


@pytest.mark.asyncio
async def test_cache_reuse_refresh_and_concurrent_callers(monkeypatch) -> None:
    service = auth.DevCuratorCredentialService()
    monkeypatch.setattr(auth, "renewable_dev_curator_auth_required", lambda: True)
    monkeypatch.setattr(auth, "_load_settings", _settings)
    monkeypatch.setattr(auth, "get_document_source_dev_curator_refresh_skew_seconds", lambda: 600)
    monkeypatch.setattr(auth, "get_document_source_import_timeout_seconds", lambda: 300.0)
    now = {"value": 1000.0}
    monkeypatch.setattr(auth.time, "time", lambda: now["value"])
    calls = 0

    def authenticate(_config):
        nonlocal calls
        calls += 1
        return _credentials(token=f"token-{calls}", expires_at=now["value"] + 1000)

    monkeypatch.setattr(auth, "_authenticate_sync", authenticate)

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
    monkeypatch.setattr(auth, "_load_settings", _settings)

    def fail(_config):
        raise RuntimeError("fake-curator password-value client-secret-value")

    monkeypatch.setattr(auth, "_authenticate_sync", fail)

    with pytest.raises(auth.DevCuratorCredentialUnavailable) as exc_info:
        await service.get_credentials()
    message = str(exc_info.value)
    assert "fake-curator" not in message
    assert "password-value" not in message
    assert "client-secret-value" not in message


@pytest.mark.asyncio
async def test_static_provider_bearer_cannot_substitute_for_dev_curator_mode(
    monkeypatch,
) -> None:
    service = auth.DevCuratorCredentialService()
    monkeypatch.setattr(auth, "renewable_dev_curator_auth_required", lambda: True)
    monkeypatch.setattr(auth, "get_document_source_dev_curator_auth_mode", lambda: "none")
    monkeypatch.setenv("ABC_LITERATURE_AUTH_MODE", "static_bearer")
    monkeypatch.setenv("ABC_LITERATURE_BEARER_TOKEN", "stale-provider-token")

    with pytest.raises(auth.DevCuratorCredentialUnavailable) as exc_info:
        await service.get_credentials()

    assert "stale-provider-token" not in str(exc_info.value)
