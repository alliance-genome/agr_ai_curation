"""Every curation-workspace route keeps review sessions private (ALL-1547)."""

from __future__ import annotations

from typing import Iterator
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.utils import get_flat_dependant
from fastapi.routing import APIRoute
from pydantic import BaseModel
from fastapi.testclient import TestClient

from src.api import curation_workspace as module
from src.api import curation_workspace_access as access
from src.api.auth import _get_user_from_cookie_impl
from src.models.sql.database import get_db
from tests.curation_ownership_support import (
    add_candidate,
    add_document,
    add_envelope,
    add_operation,
    add_session,
    add_snapshot,
    add_submission,
    add_user,
    ownership_session_factory as ownership_session_factory,
)

OWNER = "curator-owner"
OTHER = "curator-other"

# Routes that carry no session-scoped identifier. Each one either serves the
# caller's own records through a query that filters by the caller, or holds
# no session data at all.
EXEMPT_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/curation-workspace/benchmark-destinations"): "static destination configuration, no session data",
    ("GET", "/api/curation-workspace/sessions"): "inventory query always filters by the caller's sessions",
    ("GET", "/api/curation-workspace/sessions/stats"): "inventory query always filters by the caller's sessions",
    ("GET", "/api/curation-workspace/sessions/next"): "queue query always filters by the caller's sessions",
    ("GET", "/api/curation-workspace/flow-runs"): "flow-run summaries are built from the caller's sessions only",
    ("GET", "/api/curation-workspace/flow-runs/{run_id}/sessions"): "flow-run members are the caller's sessions only",
    ("GET", "/api/curation-workspace/views"): "saved views are filtered by their creator",
    ("POST", "/api/curation-workspace/views"): "creates a saved view owned by the caller",
    ("DELETE", "/api/curation-workspace/views/{view_id}"): "saved-view deletion is filtered by its creator",
    ("GET", "/api/curation-workspace/documents/{document_id}/bootstrap-availability"): (
        "read-only; considers only the caller's own prep results and chat sessions"
    ),
    ("GET", "/api/curation-workspace/prep/preview"): "previews the caller's own chat session",
    ("POST", "/api/curation-workspace/prep"): "prepares the caller's own chat session; reuse is owner-filtered",
}


def _dependency_calls(dependant) -> Iterator[object]:
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


def _routes() -> list[APIRoute]:
    return [route for route in module.router.routes if isinstance(route, APIRoute)]


def test_every_route_is_owner_guarded_or_explicitly_exempt():
    unguarded = []
    guarded_and_exempt = []
    for route in _routes():
        guarded = bool(access.SESSION_OWNERSHIP_GUARDS.intersection(_dependency_calls(route.dependant)))
        for method in route.methods:
            key = (method, route.path)
            if guarded and key in EXEMPT_ROUTES:
                guarded_and_exempt.append(key)
            if not guarded and key not in EXEMPT_ROUTES:
                unguarded.append(key)

    assert unguarded == []
    assert guarded_and_exempt == []


def _guard_dependants(dependant, guards) -> Iterator[object]:
    for dependency in dependant.dependencies:
        if dependency.call in guards:
            yield dependency
        yield from _guard_dependants(dependency, guards)


def _guard_binding_problems(route: APIRoute, guards) -> list[str]:
    """A guard must bind the identifier the route was given, not a stray input.

    A guard parameter that is not in the path silently becomes a query
    parameter, and a body guard whose parameter name differs from the
    handler's makes FastAPI expect an embedded body instead of sharing one.
    """

    problems = []
    route_path_params = set(route.param_convertors)
    guard_dependants = list(_guard_dependants(route.dependant, guards))
    for guard in guard_dependants:
        name = getattr(guard.call, "__name__", repr(guard.call))
        if guard.query_params:
            problems.append(f"{name} reads query params {[p.name for p in guard.query_params]}")
        guard_path_params = {p.name for p in guard.path_params}
        if not guard_path_params <= route_path_params:
            problems.append(f"{name} path params {sorted(guard_path_params - route_path_params)} not in path")
    if any(guard.body_params for guard in guard_dependants):
        body_names = {p.name for p in get_flat_dependant(route.dependant).body_params}
        if len(body_names) != 1:
            problems.append(f"body guard does not share one body: {sorted(body_names)}")
    return problems


def test_every_guard_binds_the_routes_own_identifier():
    problems = {
        f"{sorted(route.methods)} {route.path}": found
        for route in _routes()
        if (found := _guard_binding_problems(route, access.SESSION_OWNERSHIP_GUARDS))
    }
    assert problems == {}


class _ThrowawayBody(BaseModel):
    session_id: str


def _misdeclared_path_guard(session_uuid: str) -> None:  # not the route's {session_id}
    return None


def _misdeclared_body_guard(payload: _ThrowawayBody) -> None:  # handler names it ``request``
    return None


def test_binding_check_catches_a_guard_reading_a_query_param():
    router = APIRouter()

    @router.get("/sessions/{session_id}", dependencies=[Depends(_misdeclared_path_guard)])
    def handler(session_id: str) -> None:
        return None

    route = next(item for item in router.routes if isinstance(item, APIRoute))
    problems = _guard_binding_problems(route, {_misdeclared_path_guard})
    assert problems == ["_misdeclared_path_guard reads query params ['session_uuid']"]


def test_binding_check_catches_a_body_guard_not_sharing_the_body():
    router = APIRouter()

    @router.post("/evidence", dependencies=[Depends(_misdeclared_body_guard)])
    def handler(request: _ThrowawayBody) -> None:
        return None

    route = next(item for item in router.routes if isinstance(item, APIRoute))
    problems = _guard_binding_problems(route, {_misdeclared_body_guard})
    assert problems == ["body guard does not share one body: ['payload', 'request']"]


def test_binding_check_catches_a_guard_path_param_missing_from_the_route():
    from fastapi.dependencies.models import Dependant
    from fastapi.dependencies.utils import get_dependant

    router = APIRouter()

    @router.get("/sessions/{session_id}")
    def handler(session_id: str) -> None:
        return None

    route = next(item for item in router.routes if isinstance(item, APIRoute))
    # A dependant resolved against another path keeps a path param the route lacks.
    foreign = get_dependant(path="/other/{session_uuid}", call=_misdeclared_path_guard)
    route.dependant.dependencies.append(foreign)
    assert isinstance(foreign, Dependant)
    problems = _guard_binding_problems(route, {_misdeclared_path_guard})
    assert problems == ["_misdeclared_path_guard path params ['session_uuid'] not in path"]


def test_exempt_list_names_only_existing_routes():
    existing = {(method, route.path) for route in _routes() for method in route.methods}
    assert set(EXEMPT_ROUTES) <= existing


def test_route_inventory_is_complete():
    # A new route must be classified above; this count makes the change visible.
    assert sum(len(route.methods) for route in _routes()) == 49


@pytest.fixture
def world(ownership_session_factory, monkeypatch):
    db = ownership_session_factory()
    owner = add_user(db, OWNER)
    other = add_user(db, OTHER)
    own_document = add_document(db, owner=owner)
    other_document = add_document(db, owner=other)
    mine = add_session(db, document_id=own_document.id, created_by_id=OWNER)
    # Assigned to the caller, created by someone else: still not the caller's.
    theirs = add_session(db, document_id=other_document.id, created_by_id=OTHER, assigned_curator_id=OWNER)
    add_envelope(db, envelope_id="env-theirs", document_id=other_document.id, session_id=theirs.id)
    their_candidate = add_candidate(db, session_id=theirs.id, envelope_id="env-theirs")
    their_submission = add_submission(db, session_id=theirs.id)
    their_operation = add_operation(db, session_id=theirs.id, owner_id=OTHER)
    their_snapshot = add_snapshot(db, session_id=theirs.id, envelope_id="env-theirs", created_by_id=OTHER)
    db.commit()

    app = FastAPI()
    app.include_router(module.router)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[_get_user_from_cookie_impl] = lambda: {"sub": OWNER}
    monkeypatch.setattr(module, "provision_user_async", AsyncMock(return_value=None))
    try:
        yield {
            "client": TestClient(app),
            "mine": mine,
            "theirs": theirs,
            "own_document": own_document,
            "other_document": other_document,
            "their_candidate": their_candidate,
            "their_submission": their_submission,
            "their_operation": their_operation,
            "their_snapshot": their_snapshot,
        }
    finally:
        db.close()


def _assert_hidden(response):
    assert response.status_code == 404, response.text
    assert OTHER not in response.text
    assert OWNER not in response.text


def test_session_in_path_hides_another_curators_session(world, monkeypatch):
    monkeypatch.setattr(module, "get_session_detail", lambda _db, session_id: {"session_id": str(session_id)})
    client = world["client"]

    _assert_hidden(client.get(f"/api/curation-workspace/sessions/{world['theirs'].id}"))
    missing = client.get(f"/api/curation-workspace/sessions/{uuid4()}")
    assert missing.status_code == 404
    owned = client.get(f"/api/curation-workspace/sessions/{world['mine'].id}")
    assert owned.status_code == 200
    assert owned.json() == {"session_id": str(world["mine"].id)}


def test_session_in_body_hides_another_curators_session(world):
    candidate_id = str(world["their_candidate"].id)
    response = world["client"].post(
        f"/api/curation-workspace/candidates/{candidate_id}/decision",
        json={"session_id": str(world["theirs"].id), "candidate_id": candidate_id, "action": "accept"},
    )
    _assert_hidden(response)
    recompute = world["client"].post(
        "/api/curation-workspace/evidence/recompute",
        json={"session_id": str(world["theirs"].id), "candidate_ids": [candidate_id]},
    )
    _assert_hidden(recompute)


def test_candidate_in_path_hides_another_curators_candidate(world):
    _assert_hidden(
        world["client"].get(
            f"/api/curation-workspace/candidates/{world['their_candidate'].id}/validation-history"
        )
    )


def test_envelope_in_path_hides_another_curators_envelope(world):
    _assert_hidden(world["client"].get("/api/curation-workspace/domain-envelopes/env-theirs/review-rows"))


def test_submission_in_path_hides_another_curators_submission(world):
    client = world["client"]
    submission_id = world["their_submission"].id

    _assert_hidden(
        client.get(f"/api/curation-workspace/sessions/{world['theirs'].id}/submissions/{submission_id}")
    )
    # An owned session id does not open someone else's submission.
    _assert_hidden(
        client.get(f"/api/curation-workspace/sessions/{world['mine'].id}/submissions/{submission_id}")
    )


def test_operation_in_path_hides_another_curators_operation(world):
    _assert_hidden(
        world["client"].get(f"/api/curation-workspace/review-operations/{world['their_operation'].id}")
    )


def test_snapshot_in_path_hides_another_curators_snapshot_in_benchmark_shape(world):
    response = world["client"].get(
        f"/api/curation-workspace/benchmark-snapshots/{world['their_snapshot'].id}/download"
    )
    _assert_hidden(response)
    assert response.json() == {"detail": {"error": "snapshot_not_found", "message": "Snapshot was not found"}}


def test_document_routes_require_the_callers_document(world, monkeypatch):
    client = world["client"]
    created = []

    def fake_create(request, **kwargs):
        created.append(request.document_id)
        return {"created": True, "session": None}

    monkeypatch.setattr(module, "create_manual_session", fake_create)

    _assert_hidden(
        client.post(
            "/api/curation-workspace/sessions",
            json={"document_id": str(world["other_document"].id), "adapter_key": "fixture"},
        )
    )
    _assert_hidden(
        client.post(
            f"/api/curation-workspace/documents/{world['other_document'].id}/bootstrap",
            json={"adapter_key": "fixture"},
        )
    )
    assert created == []
