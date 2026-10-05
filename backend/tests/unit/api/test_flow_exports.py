"""Bearer-only curator export: strict ID-token checks, read-only identity, signed bundles."""

import base64
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
from src.lib.flow_transfer.config import flow_export_config
from src.lib.flow_transfer.export import EvaluatedFlow
from src.models.sql import get_db
from tests.unit.lib.flow_transfer.support import CURATOR_SUB, ISSUER, make_bundle

POOL = "us-east-1_synthetic"
TOKEN_ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}"
TARGET_CLIENT = "synthetic-target-client"
MAIN_CLIENT = "synthetic-main-client"
VERSION = "sha256:" + "1" * 64


class _Keys:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, _token):
        return SimpleNamespace(key=self.key)


@pytest.fixture
def setup(monkeypatch):
    signer = Ed25519PrivateKey.generate()
    seed = signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    monkeypatch.setenv("FLOW_EXPORT_SIGNING_KEY", base64.b64encode(seed).decode())
    monkeypatch.setenv("FLOW_EXPORT_ISSUER", ISSUER)
    monkeypatch.setenv("FLOW_EXPORT_BEARER_CLIENT_IDS", TARGET_CLIENT)
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL)
    monkeypatch.setattr(api, "_provider", None)
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider = api._bearer_provider(flow_export_config())
    monkeypatch.setattr(provider, "_discover_async", AsyncMock(return_value={"issuer": TOKEN_ISSUER}))
    monkeypatch.setattr(provider, "_get_jwks_client", lambda: _Keys(rsa_key.public_key()))
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(id=42)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: session
    return SimpleNamespace(client=TestClient(app), rsa_key=rsa_key, session=session, signer=signer)


def token(setup, **changes):
    now = int(time.time())
    claims = {"sub": CURATOR_SUB, "iss": TOKEN_ISSUER, "aud": TARGET_CLIENT, "token_use": "id",
              "iat": now, "exp": now + 300, "cognito:groups": []} | changes
    return jwt.encode(claims, setup.rsa_key, algorithm="RS256", headers={"kid": "fixture"})


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
    lambda s: bearer(token(s, exp=int(time.time()) - 600)),
    lambda s: bearer("x" * 9000),
    lambda s: {"Authorization": "Basic " + token(s)},
], ids=["missing", "cookie-only", "main-client-audience", "access-token", "expired", "oversized", "basic"])
def test_every_bearer_problem_is_401(setup, make):
    response = setup.client.get("/api/flow-exports", headers=make(setup))
    assert response.status_code == 401
    assert response.json()["detail"] == {"code": "authorization_required"}


def test_an_unreachable_sign_in_provider_is_503(setup, monkeypatch):
    provider = api._bearer_provider(flow_export_config())

    def unreachable():
        raise PyJWKClientConnectionError("keys unreachable")
    monkeypatch.setattr(provider, "_get_jwks_client", unreachable)
    response = setup.client.get("/api/flow-exports", headers=bearer(token(setup)))
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "authorization_unavailable"}
    assert response.headers["cache-control"] == "no-store"


def test_a_curator_with_no_account_has_no_flows(setup):
    setup.session.scalar.return_value = None
    response = setup.client.get("/api/flow-exports", headers=bearer(token(setup)))
    assert response.status_code == 200
    assert response.json() == {"items": [], "total_items": 0, "next_offset": None}


def test_the_list_pages_and_says_why_a_flow_cannot_be_imported(setup, monkeypatch, caplog):
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
    assert value not in caplog.text


def test_an_invisible_flow_is_404(setup, monkeypatch):
    def invisible(db, flow_id, user_id):
        raise HTTPException(403, "Access denied")
    monkeypatch.setattr(api, "get_visible_flow", invisible)
    response = setup.client.get(f"/api/flow-exports/{uuid4()}", params={"version": VERSION},
                                headers=bearer(token(setup)))
    assert response.status_code == 404 and response.json()["detail"] == {"code": "flow_not_found"}
    assert response.headers["cache-control"] == "no-store"


def test_a_changed_or_refused_flow_answers_with_its_current_item(setup, monkeypatch):
    flow_id = uuid4()
    monkeypatch.setattr(api, "get_visible_flow", lambda db, fid, user_id: SimpleNamespace(id=fid))
    monkeypatch.setattr(api, "evaluate_flow", lambda *a, **k: evaluated(flow_id, version="sha256:" + "2" * 64))
    stale = setup.client.get(f"/api/flow-exports/{flow_id}", params={"version": VERSION},
                             headers=bearer(token(setup)))
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "flow_changed"
    assert stale.json()["detail"]["item"]["version"] == "sha256:" + "2" * 64
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
