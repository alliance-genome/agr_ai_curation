"""HTTP profile contracts, caller identity and recoverable errors."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api import generic_profiles as api
from src.api.auth import get_auth_dependency


@pytest.fixture
def client(monkeypatch):
    db = MagicMock()
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_auth_dependency().dependency] = lambda: {
        "sub": "authenticated-curator"
    }
    app.dependency_overrides[api.get_db] = lambda: db
    monkeypatch.setattr(
        api, "set_global_user_from_cognito", lambda *_: SimpleNamespace(id=7)
    )
    with TestClient(app) as client:
        yield client, db, app


def contract():
    return {
        "name": "Example",
        "semantic_class": "example",
        "fields": [
            {
                "key": "source_status",
                "required": True,
                "nullable": False,
                "value_schema": {"kind": "enum", "values": ["known", "not_stated"]},
            },
        ],
    }


def rows():
    now = datetime.now(timezone.utc)
    profile_id = uuid4()
    parsed = api.GenericProfileContract.model_validate(contract())
    profile = SimpleNamespace(
        id=profile_id,
        owner_id=7,
        project_id=None,
        visibility="private",
        name=parsed.name,
        description="",
        semantic_class="example",
        head_revision=1,
        archived=False,
        created_at=now,
        updated_at=now,
    )
    revision = SimpleNamespace(
        id=uuid4(),
        profile_id=profile_id,
        revision=1,
        fingerprint=parsed.fingerprint(),
        contract=parsed.model_dump(mode="json"),
        creator_id=7,
        created_at=now,
    )
    return profile, revision


def test_create_uses_authenticated_identity_and_returns_canonical_contract(
    client, monkeypatch
):
    client, db, _ = client
    create = MagicMock(return_value=rows())
    monkeypatch.setattr(api.service, "create_profile", create)
    response = client.post(
        "/api/agent-studio/generic-profiles", json={"contract": contract()}
    )
    assert response.status_code == 201
    assert create.call_args.args[1] == 7
    assert response.json()["revision"]["contract"]["fields"][0]["required"] is True
    assert response.json()["revision"]["fingerprint"].startswith("sha256:")
    db.commit.assert_called_once()


def test_invalid_field_is_addressed_and_no_service_is_called(client, monkeypatch):
    client, db, _ = client
    create = MagicMock()
    monkeypatch.setattr(api.service, "create_profile", create)
    body = contract()
    body["fields"][0]["key"] = "label"
    response = client.post(
        "/api/agent-studio/generic-profiles", json={"contract": body}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == [
        "body",
        "contract",
        "fields",
        0,
        "key",
    ]
    create.assert_not_called()
    db.commit.assert_not_called()


def test_validation_is_a_non_persisting_draft_operation(client):
    client, db, _ = client
    response = client.post(
        "/api/agent-studio/generic-profiles/validate", json=contract()
    )
    assert response.status_code == 200
    assert response.json()["fingerprint"].startswith("sha256:")
    db.commit.assert_not_called()
    db.add.assert_not_called()


def test_stale_save_reports_conflict_and_rolls_back(client, monkeypatch):
    client, db, _ = client
    monkeypatch.setattr(
        api.service,
        "revise_profile",
        MagicMock(
            side_effect=api.service.ProfileConflictError("Compare before saving")
        ),
    )
    response = client.post(
        f"/api/agent-studio/generic-profiles/{uuid4()}/revisions",
        json={"contract": contract(), "expected_revision": 1},
    )
    assert response.status_code == 409
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_inaccessible_profile_returns_non_disclosing_404(client, monkeypatch):
    client, _, _ = client
    monkeypatch.setattr(
        api.service,
        "get_profile",
        MagicMock(side_effect=api.service.ProfileNotFoundError("Private resource")),
    )
    response = client.get(f"/api/agent-studio/generic-profiles/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found"


def test_every_route_is_authenticated_and_openapi_is_closed(client):
    _, _, app = client
    auth = get_auth_dependency().dependency
    assert all(
        any(dependency.call is auth for dependency in route.dependant.dependencies)
        for route in api.router.routes
    )
    schema = app.openapi()
    for model_name in ("GenericProfileContract", "ObjectValueSchema"):
        # FastAPI distinguishes request defaults from fully serialized responses.
        variants = [
            definition
            for name, definition in schema["components"]["schemas"].items()
            if name == model_name or name.startswith(model_name + "-")
        ]
        assert variants
        assert all(
            definition["additionalProperties"] is False for definition in variants
        )
    assert (
        "/api/agent-studio/generic-profiles/{profile_id}/revisions/{revision}"
        in schema["paths"]
    )
