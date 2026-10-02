"""Tests for the shared Cognito USER_PASSWORD_AUTH sign-in helper."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from agr_ai_curation_alliance import cognito_user_password as cognito


def _synthetic(label: str) -> str:
    return f"synthetic-{label}-{uuid4().hex[:8]}"


def _settings(*, client_value: str | None) -> cognito.CognitoUserPasswordSettings:
    return cognito.CognitoUserPasswordSettings(
        region="us-east-1",
        user_pool_id="pool-1",
        client_id="client-1",
        client_secret=client_value,
        username="dev-user",
        password=_synthetic("sign-in"),
        request_timeout_seconds=5.0,
    )


def _install_fake_cognito(monkeypatch) -> dict:
    observed: dict = {}

    class FakeClient:
        def initiate_auth(self, **kwargs):
            observed["auth"] = kwargs
            return {
                "AuthenticationResult": {
                    "IdToken": "signed-id",
                    "AccessToken": "signed-access",
                }
            }

    monkeypatch.setattr(cognito.boto3, "client", lambda *_a, **_k: FakeClient())
    monkeypatch.setattr(
        cognito,
        "PyJWKClient",
        lambda *_a, **_k: SimpleNamespace(
            get_signing_key_from_jwt=lambda _value: SimpleNamespace(key="key")
        ),
    )

    def fake_decode(value, *_args, **_kwargs):
        if value == "signed-id":
            return {"sub": "dev-sub", "exp": 4102444800, "token_use": "id"}
        return {
            "sub": "dev-sub",
            "exp": 4102444700,
            "token_use": "access",
            "client_id": "client-1",
        }

    monkeypatch.setattr(cognito.jwt, "decode", fake_decode)
    return observed


def test_sign_in_sends_secret_hash_when_client_secret_is_set(monkeypatch) -> None:
    observed = _install_fake_cognito(monkeypatch)
    settings = _settings(client_value=_synthetic("client"))

    tokens = cognito.authenticate_user_password(settings)

    parameters = observed["auth"]["AuthParameters"]
    assert observed["auth"]["AuthFlow"] == "USER_PASSWORD_AUTH"
    assert parameters["SECRET_HASH"] == cognito.secret_hash(
        username=settings.username,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
    )
    assert tokens.access_token == "signed-access"
    assert tokens.expires_at == 4102444700


def test_sign_in_omits_secret_hash_without_client_secret(monkeypatch) -> None:
    observed = _install_fake_cognito(monkeypatch)

    cognito.authenticate_user_password(_settings(client_value=None))

    assert "SECRET_HASH" not in observed["auth"]["AuthParameters"]


def test_sign_in_errors_use_the_shared_error_type(monkeypatch) -> None:
    monkeypatch.setattr(
        cognito.boto3,
        "client",
        lambda *_a, **_k: SimpleNamespace(
            initiate_auth=lambda **_kw: {"ChallengeName": "NEW_PASSWORD_REQUIRED"}
        ),
    )

    with pytest.raises(cognito.CognitoUserPasswordAuthError, match="interaction"):
        cognito.authenticate_user_password(_settings(client_value=None))


def test_settings_and_tokens_redact_sensitive_values() -> None:
    settings = _settings(client_value=_synthetic("client"))
    tokens = cognito.CognitoUserPasswordTokens(
        access_token="signed-access", id_claims={"sub": "dev-sub"}, expires_at=1.0
    )

    rendered = repr(settings) + repr(tokens)

    for value in (
        settings.password,
        settings.client_secret,
        settings.username,
        "signed-access",
        "dev-sub",
    ):
        assert value not in rendered
