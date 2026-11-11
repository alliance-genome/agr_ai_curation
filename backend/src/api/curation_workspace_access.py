"""Route guards that keep each review session private to its curator (ALL-1547).

Every route in ``src.api.curation_workspace`` declares one of the guards in
``SESSION_OWNERSHIP_GUARDS`` or is listed, with its reason, in that router's
exempt table; ``tests/unit/api/test_curation_workspace_ownership.py`` fails
when a route does neither. A guard resolves the identifier the route was
given (session, candidate, envelope, submission, operation, snapshot or
document) to its owner and answers 404 when the caller is not the owner, with
the same message used for an identifier that does not exist.

This module deliberately avoids ``from __future__ import annotations``: the
body guards are built by a factory and FastAPI must see the concrete request
model in their signatures.
"""

from typing import Any, Callable
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.auth import get_auth_dependency
from src.lib.curation_workspace.session_ownership import (
    OwnedResourceNotFound,
    require_owned_candidate,
    require_owned_document,
    require_owned_operation,
    require_owned_session,
    require_owned_snapshot,
    require_owned_submission,
    require_visible_envelope,
)
from src.models.sql.database import get_db
from src.schemas.curation_workspace import (
    CurationCandidateDecisionRequest,
    CurationEvidenceRecomputeRequest,
    CurationEvidenceResolveRequest,
    CurationManualEvidenceCreateRequest,
    CurationSessionCreateRequest,
)

_SNAPSHOT_NOT_FOUND = {"error": "snapshot_not_found", "message": "Snapshot was not found"}


def current_user_id(user: dict) -> str | None:
    return user.get("sub") or user.get("uid")


def require_current_user_id(user: dict) -> str:
    user_id = current_user_id(user)
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")
    return user_id


def _checked(db: Session, check: Callable[[], Any]) -> None:
    """Run an identifier-only ownership query, then end its read.

    ``get_db`` gives each request its own session and the guard runs before
    the handler, so the only open work is this read. Ending it means a route
    that awaits other work does not hold a transaction; the handler starts
    its own. The guard loads no rows.
    """

    try:
        check()
    finally:
        db.rollback()


def owned_session_in_path(
    session_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> None:
    owner_sub = require_current_user_id(user)
    _checked(db, lambda: require_owned_session(db, session_id, owner_sub))


def owned_candidate_in_path(
    candidate_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> None:
    owner_sub = require_current_user_id(user)
    _checked(db, lambda: require_owned_candidate(db, candidate_id, owner_sub))


def visible_envelope_in_path(
    envelope_id: str,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> None:
    owner_sub = require_current_user_id(user)
    _checked(db, lambda: require_visible_envelope(db, envelope_id, owner_sub))


def owned_submission_in_path(
    session_id: UUID,
    submission_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> None:
    owner_sub = require_current_user_id(user)
    _checked(db, lambda: require_owned_submission(db, session_id, submission_id, owner_sub))


def owned_operation_in_path(
    operation_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> None:
    owner_sub = require_current_user_id(user)
    _checked(db, lambda: require_owned_operation(db, operation_id, owner_sub))


def owned_document_in_path(
    document_id: str,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> None:
    owner_sub = require_current_user_id(user)
    _checked(db, lambda: require_owned_document(db, document_id, owner_sub))


def owned_benchmark_session_in_path(
    session_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> None:
    """Session guard for benchmark routes, which answer in their own error shape."""

    owner_sub = require_current_user_id(user)
    try:
        _checked(db, lambda: require_owned_session(db, session_id, owner_sub))
    except OwnedResourceNotFound:
        raise HTTPException(status_code=404, detail=_SNAPSHOT_NOT_FOUND) from None


def owned_snapshot_in_path(
    snapshot_id: UUID,
    user: dict = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> None:
    owner_sub = require_current_user_id(user)
    try:
        _checked(db, lambda: require_owned_snapshot(db, snapshot_id, owner_sub))
    except OwnedResourceNotFound:
        raise HTTPException(status_code=404, detail=_SNAPSHOT_NOT_FOUND) from None


def _body_guard(
    model: type,
    check: Callable[[Session, Any, str], Any],
    name: str,
) -> Callable[..., None]:
    # The parameter is named ``request`` like the route's own body parameter,
    # so FastAPI reads one request body and validates it for both.
    def guard(
        request: model,  # type: ignore[valid-type]
        user: dict = get_auth_dependency(),
        db: Session = Depends(get_db),
    ) -> None:
        owner_sub = require_current_user_id(user)
        _checked(db, lambda: check(db, request, owner_sub))

    guard.__name__ = guard.__qualname__ = name
    return guard


def _check_session_in_body(db: Session, request: Any, owner_sub: str) -> None:
    require_owned_session(db, request.session_id, owner_sub)


def _check_document_in_body(db: Session, request: Any, owner_sub: str) -> None:
    require_owned_document(db, request.document_id, owner_sub)


owned_session_in_evidence_recompute = _body_guard(
    CurationEvidenceRecomputeRequest, _check_session_in_body, "owned_session_in_evidence_recompute"
)
owned_session_in_manual_evidence = _body_guard(
    CurationManualEvidenceCreateRequest, _check_session_in_body, "owned_session_in_manual_evidence"
)
owned_session_in_evidence_resolve = _body_guard(
    CurationEvidenceResolveRequest, _check_session_in_body, "owned_session_in_evidence_resolve"
)
owned_session_in_candidate_decision = _body_guard(
    CurationCandidateDecisionRequest, _check_session_in_body, "owned_session_in_candidate_decision"
)
owned_document_in_session_create = _body_guard(
    CurationSessionCreateRequest, _check_document_in_body, "owned_document_in_session_create"
)


SESSION_OWNERSHIP_GUARDS: frozenset[Callable[..., None]] = frozenset(
    {
        owned_session_in_path,
        owned_candidate_in_path,
        visible_envelope_in_path,
        owned_submission_in_path,
        owned_operation_in_path,
        owned_document_in_path,
        owned_benchmark_session_in_path,
        owned_snapshot_in_path,
        owned_session_in_evidence_recompute,
        owned_session_in_manual_evidence,
        owned_session_in_evidence_resolve,
        owned_session_in_candidate_decision,
        owned_document_in_session_create,
    }
)


__all__ = [
    "SESSION_OWNERSHIP_GUARDS",
    "current_user_id",
    "owned_benchmark_session_in_path",
    "owned_candidate_in_path",
    "owned_document_in_path",
    "owned_document_in_session_create",
    "owned_operation_in_path",
    "owned_session_in_candidate_decision",
    "owned_session_in_evidence_recompute",
    "owned_session_in_evidence_resolve",
    "owned_session_in_manual_evidence",
    "owned_session_in_path",
    "owned_snapshot_in_path",
    "owned_submission_in_path",
    "require_current_user_id",
    "visible_envelope_in_path",
]
