"""Bearer-only curator export: strict ID-token checks, read-only identity, signed bundles."""

import base64
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import jwt
from jwt.exceptions import PyJWKClientConnectionError
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api import flow_exports as api
from src.lib.flow_transfer import config as config_module
from src.lib.flow_transfer.config import flow_export_config, flow_export_sign_in
from src.lib.flow_transfer.export import EvaluatedFlow
from src.models.sql import get_db
from tests.unit.lib.flow_transfer.support import CURATOR_SUB, ISSUER, make_bundle

POOL = "us-east-1_synthetic"
TOKEN_ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}"
OIDC_ISSUER = "https://sign-in.example.org/realms/synthetic"
TARGET_CLIENT = "synthetic-target-client"
MAIN_CLIENT = "synthetic-main-client"
VERSION = "sha256:" + "1" * 64


class _Keys:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, _token):
        return SimpleNamespace(key=self.key)


def _cognito(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "cognito")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setenv("COGNITO_CLIENT_ID", MAIN_CLIENT)
    monkeypatch.setenv("COGNITO_DOMAIN", "https://auth.example.org")
    monkeypatch.setenv("COGNITO_REDIRECT_URI", "https://app.example.org/auth/callback")
    return TOKEN_ISSUER


def _oidc(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("OIDC_ISSUER_URL", OIDC_ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", MAIN_CLIENT)
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://app.example.org/auth/callback")
    monkeypatch.delenv("OIDC_GROUP_CLAIM", raising=False)
    monkeypatch.setattr(config_module, "get_group_claim_key", lambda: "realm_access.roles")
    return OIDC_ISSUER


def _setup(monkeypatch, sign_in):
    signer = Ed25519PrivateKey.generate()
    seed = signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    monkeypatch.setenv("FLOW_EXPORT_SIGNING_KEY", base64.b64encode(seed).decode())
    monkeypatch.setenv("FLOW_EXPORT_ISSUER", ISSUER)
    monkeypatch.setenv("FLOW_EXPORT_BEARER_CLIENT_IDS", TARGET_CLIENT)
    issuer = sign_in(monkeypatch)
    monkeypatch.setattr(api, "_provider", None)
    monkeypatch.setattr(api, "_provider_key", None)
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider = api._bearer_provider(flow_export_config(), flow_export_sign_in())
    monkeypatch.setattr(provider, "_discover_async", AsyncMock(return_value={"issuer": issuer}))
    monkeypatch.setattr(provider, "_get_jwks_client", lambda: _Keys(rsa_key.public_key()))
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(id=42)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: session
    return SimpleNamespace(client=TestClient(app), rsa_key=rsa_key, session=session, signer=signer,
                           issuer=issuer, provider=provider)


@pytest.fixture
def setup(monkeypatch):
    return _setup(monkeypatch, _cognito)


@pytest.fixture
def oidc_setup(monkeypatch):
    return _setup(monkeypatch, _oidc)


def token(setup, *, headers=None, drop=(), **changes):
    now = int(time.time())
    claims = {"sub": CURATOR_SUB, "iss": setup.issuer, "aud": TARGET_CLIENT, "token_use": "id",
              "iat": now, "exp": now + 300, "cognito:groups": []}
    for name in drop:
        claims.pop(name)
    claims |= changes
    return jwt.encode(claims, setup.rsa_key, algorithm="RS256",
                      headers={"kid": "fixture"} | (headers or {}))


def oidc_token(setup, **kwargs):
    """A generic OIDC ID token: no token_use claim, groups under a nested claim."""
    return token(setup, drop=("token_use", "cognito:groups"),
                 realm_access={"roles": ["group-alpha"]}, **kwargs)


def refusals(caplog):
    return [(record.code, getattr(record, "source_flow_id", None)) for record in caplog.records
            if record.getMessage() == "flow_export_refused"]


@pytest.fixture
def info_logs(caplog):
    caplog.set_level(logging.INFO, logger=api.logger.name)
    return caplog


def bearer(value):
    return {"Authorization": f"Bearer {value}"}


def evaluated(flow_id, reason=None, version=VERSION):
    bundle = None if reason else make_bundle(flow_id=flow_id)
    return EvaluatedFlow(flow_id=flow_id, name="Flow", description=None, owned=True,
                         version=version, reason=reason, bundle=bundle)


def test_unconfigured_routes_answer_503(setup, monkeypatch):
    monkeypatch.delenv("FLOW_EXPORT_SIGNING_KEY")
    monkeypatch.delenv("FLOW_EXPORT_ISSUER")
    monkeypatch.delenv("FLOW_EXPORT_BEARER_CLIENT_IDS")
    response = setup.client.get("/api/flow-exports", headers=bearer(token(setup)))
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "flow_export_not_configured"}


@pytest.mark.parametrize("make", [
    lambda s: {},
    lambda s: {"Cookie": "auth_token=" + token(s)},
    lambda s: bearer(token(s, aud=MAIN_CLIENT)),
    lambda s: bearer(token(s, token_use="access")),
    lambda s: bearer(token(s, drop=("token_use",))),
    lambda s: bearer(token(s, exp=int(time.time()) - 600)),
    lambda s: bearer("x" * 9000),
    lambda s: {"Authorization": "Basic " + token(s)},
], ids=["missing", "cookie-only", "main-client-audience", "access-token", "no-token-use", "expired",
        "oversized", "basic"])
def test_every_bearer_problem_is_401(setup, make, info_logs):
    headers = make(setup)
    response = setup.client.get("/api/flow-exports", headers=headers)
    assert response.status_code == 401
    assert response.json()["detail"] == {"code": "authorization_required"}
    assert refusals(info_logs) == [("authorization_required", None)]
    for value in headers.values():
        assert value.split(" ", 1)[-1].split("=", 1)[-1] not in info_logs.text


def test_an_unreachable_sign_in_provider_is_503(setup, monkeypatch, caplog):
    provider = api._bearer_provider(flow_export_config(), flow_export_sign_in())
    leaked = "-".join(("fake", "provider", "detail", uuid4().hex))

    def unreachable():
        raise PyJWKClientConnectionError(leaked)
    monkeypatch.setattr(provider, "_get_jwks_client", unreachable)
    response = setup.client.get("/api/flow-exports", headers=bearer(token(setup)))
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "authorization_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert leaked not in caplog.text and leaked not in response.text


def test_cognito_keeps_the_pool_issuer_and_cognito_groups(setup):
    assert setup.provider.issuer_url == TOKEN_ISSUER
    assert setup.provider.group_claim == "cognito:groups"
    assert "token_use" in setup.provider.required_claims
    assert setup.provider.audience == [TARGET_CLIENT]


def test_oidc_uses_the_oidc_issuer_and_the_configured_group_claim(oidc_setup):
    assert oidc_setup.provider.issuer_url == OIDC_ISSUER
    assert oidc_setup.provider.group_claim == "realm_access.roles"
    assert "token_use" not in oidc_setup.provider.required_claims
    assert oidc_setup.provider.audience == [TARGET_CLIENT]


def test_an_oidc_id_token_is_accepted_with_its_groups(oidc_setup, monkeypatch):
    seen = {}
    monkeypatch.setattr(api, "get_groups_from_provider_groups",
                        lambda groups: seen.setdefault("groups", list(groups)))
    monkeypatch.setattr(api, "exportable_flows", lambda db, user_id, offset, limit: ([], 0))
    for value in (oidc_token(oidc_setup), oidc_token(oidc_setup, typ="ID"),
                  oidc_token(oidc_setup, token_use="id")):
        response = oidc_setup.client.get("/api/flow-exports", headers=bearer(value))
        assert response.status_code == 200, response.text
    assert seen["groups"] == ["group-alpha"]


@pytest.mark.parametrize("make", [
    lambda s: oidc_token(s, scope="openid profile"),
    lambda s: oidc_token(s, scp=["flows.read"]),
    lambda s: oidc_token(s, typ="Bearer"),
    lambda s: oidc_token(s, token_use="access"),
    lambda s: oidc_token(s, headers={"typ": "at+jwt"}),
    lambda s: oidc_token(s, aud=MAIN_CLIENT),
    lambda s: oidc_token(s, iss=TOKEN_ISSUER),
], ids=["scope", "scp", "keycloak-bearer-typ", "token-use-access", "rfc9068-header",
        "main-client-audience", "other-issuer"])
def test_oidc_access_tokens_and_foreign_tokens_are_401(oidc_setup, make, info_logs):
    response = oidc_setup.client.get("/api/flow-exports", headers=bearer(make(oidc_setup)))
    assert response.status_code == 401
    assert response.json()["detail"] == {"code": "authorization_required"}
    assert refusals(info_logs) == [("authorization_required", None)]


def test_the_page_size_follows_its_env_setting(setup, monkeypatch):
    monkeypatch.setenv("FLOW_TRANSFER_EXPORT_PAGE_SIZE", "2")
    asked = []
    monkeypatch.setattr(api, "exportable_flows",
                        lambda db, user_id, offset, limit: asked.append(limit) or ([], 0))
    value = token(setup)
    assert setup.client.get("/api/flow-exports", headers=bearer(value)).status_code == 200
    assert setup.client.get("/api/flow-exports", params={"limit": 2},
                            headers=bearer(value)).status_code == 200
    over = setup.client.get("/api/flow-exports", params={"limit": 3}, headers=bearer(value))
    assert over.status_code == 422 and over.json()["detail"][0]["loc"] == ["query", "limit"]
    assert asked == [2, 2]


def test_a_curator_with_no_account_has_no_flows(setup):
    setup.session.scalar.return_value = None
    response = setup.client.get("/api/flow-exports", headers=bearer(token(setup)))
    assert response.status_code == 200
    assert response.json() == {"items": [], "total_items": 0, "next_offset": None}


def test_the_list_pages_and_says_why_a_flow_cannot_be_imported(setup, monkeypatch, info_logs):
    first, second = uuid4(), uuid4()
    monkeypatch.setattr(api, "exportable_flows", lambda db, user_id, offset, limit: (
        [SimpleNamespace(id=first), SimpleNamespace(id=second)], 3))
    results = {first: evaluated(first), second: evaluated(second, reason="flexible_output")}
    monkeypatch.setattr(api, "evaluate_flow", lambda db, flow, curator, **_: results[flow.id])
    value = token(setup)
    response = setup.client.get("/api/flow-exports", headers=bearer(value))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [(item["importable"], item["reason"]) for item in body["items"]] == [
        (True, None), (False, "flexible_output")]
    assert body["total_items"] == 3 and body["next_offset"] == 2
    assert value not in info_logs.text


def test_an_invisible_flow_is_404(setup, monkeypatch, info_logs):
    def invisible(db, flow_id, user_id):
        raise HTTPException(403, "Access denied")
    monkeypatch.setattr(api, "get_visible_flow", invisible)
    flow_id = uuid4()
    response = setup.client.get(f"/api/flow-exports/{flow_id}", params={"version": VERSION},
                                headers=bearer(token(setup)))
    assert response.status_code == 404 and response.json()["detail"] == {"code": "flow_not_found"}
    assert response.headers["cache-control"] == "no-store"
    assert refusals(info_logs) == [("flow_not_found", str(flow_id))]


def test_a_curator_with_no_account_cannot_export(setup, info_logs):
    setup.session.scalar.return_value = None
    flow_id = uuid4()
    response = setup.client.get(f"/api/flow-exports/{flow_id}", params={"version": VERSION},
                                headers=bearer(token(setup)))
    assert response.status_code == 404 and response.json()["detail"] == {"code": "flow_not_found"}
    assert refusals(info_logs) == [("flow_not_found", str(flow_id))]


def test_a_changed_or_refused_flow_answers_with_its_current_item(setup, monkeypatch, info_logs):
    flow_id = uuid4()
    monkeypatch.setattr(api, "get_visible_flow", lambda db, fid, user_id: SimpleNamespace(id=fid))
    monkeypatch.setattr(api, "evaluate_flow", lambda *a, **k: evaluated(flow_id, version="sha256:" + "2" * 64))
    stale = setup.client.get(f"/api/flow-exports/{flow_id}", params={"version": VERSION},
                             headers=bearer(token(setup)))
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "flow_changed"
    assert stale.json()["detail"]["item"]["version"] == "sha256:" + "2" * 64
    assert refusals(info_logs) == [("flow_changed", str(flow_id))]
    assert VERSION not in info_logs.text and "Flow" not in info_logs.text
    monkeypatch.setattr(api, "evaluate_flow", lambda *a, **k: evaluated(flow_id, reason="lookup_tools"))
    refused = setup.client.get(f"/api/flow-exports/{flow_id}", params={"version": VERSION},
                               headers=bearer(token(setup)))
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "lookup_tools"
    assert refused.json()["detail"]["item"]["importable"] is False


def test_an_importable_flow_is_signed_for_this_curator(setup, monkeypatch):
    flow_id = uuid4()
    monkeypatch.setattr(api, "get_visible_flow", lambda db, fid, user_id: SimpleNamespace(id=fid))
    monkeypatch.setattr(api, "evaluate_flow", lambda *a, **k: evaluated(flow_id))
    response = setup.client.get(f"/api/flow-exports/{flow_id}", params={"version": VERSION},
                                headers=bearer(token(setup)))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    claims = jwt.decode(body["signature"], setup.signer.public_key(), algorithms=["EdDSA"],
                        audience="agr-ai-curation-flow-import")
    assert claims["sub"] == CURATOR_SUB and claims["source_flow_id"] == str(flow_id)
