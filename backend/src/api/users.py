"""Users API router for user profile management.

Implements: FR-004, FR-005, FR-022

This router is separate from auth router to satisfy contract requirement
that /users/me is at root path, not under /auth prefix.
"""

import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .auth import get_auth_dependency
from src.lib.group_rules import get_groups_from_provider_groups
from src.models.sql.database import get_db
from src.schemas.chat_route_preferences import (
    ChatRoutePickerResponse,
    ChatRoutePickerTarget,
    ChatRoutePreferenceResponse,
    ChatRoutePreferenceUpdate,
)
from src.services.chat_route_preference_service import (
    ChatRoutePreferenceState,
    ChatRouteTargetUnavailableError,
    clear_chat_route_preference,
    get_chat_route_preference,
    list_chat_route_picker_targets,
    update_chat_route_preference,
)
from src.services.user_service import set_global_user_from_cognito


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


def _authenticated_groups(user: Dict[str, Any]) -> list[str]:
    provider_groups = user.get("groups")
    if provider_groups is None:
        provider_groups = user.get("cognito:groups", [])
    if not isinstance(provider_groups, list):
        provider_groups = [str(provider_groups)] if provider_groups else []
    return get_groups_from_provider_groups(provider_groups)


def _preference_response(state: ChatRoutePreferenceState) -> ChatRoutePreferenceResponse:
    target = ChatRoutePickerTarget(**state.target.__dict__) if state.target else None
    return ChatRoutePreferenceResponse(
        mode=state.mode,
        agent_id=state.agent_id,
        flow_id=state.flow_id,
        status="available" if state.available else "unavailable",
        target=target,
    )


@router.get("/me")
async def get_current_user_info(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db)
) -> dict:
    """Get current authenticated user's information.

    Contract: GET /users/me
    Requirements: FR-004, FR-005 (user provisioning on access), FR-022

    Args:
        user: Authenticated Cognito user dict from auth dependency
        db: Database session for querying user record

    Returns:
        User information dictionary with:
        - user_id: Internal database ID
        - user_id: Cognito user identifier (sub claim, stored in user_id column)
        - email: User email address
        - display_name: User display name
        - created_at: Account creation timestamp
        - last_login: Last authentication timestamp
        - is_active: Account active status

    Raises:
        401: If authentication token is missing or invalid
        404: If user record not found in database

    Note:
        This endpoint triggers user auto-provisioning via set_global_user_from_cognito()
        in the dependency chain (implements FR-005).
    """
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated"
        )

    # Auto-provision user on first login (or update on subsequent logins)
    # This implements FR-005 (automatic user creation) and FR-006 (empty collections)
    db_user = set_global_user_from_cognito(db, user)

    response = db_user.to_dict()
    provider_groups = user.get("groups")
    if provider_groups is None:
        provider_groups = user.get("cognito:groups", [])
    if not isinstance(provider_groups, list):
        provider_groups = [str(provider_groups)] if provider_groups else []
    response["provider_groups"] = provider_groups
    response["active_groups"] = _authenticated_groups(user)
    return response


@router.get("/me/chat-route-preference", response_model=ChatRoutePreferenceResponse)
async def read_chat_route_preference(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ChatRoutePreferenceResponse:
    """Read this authenticated user's routing intent and current availability."""

    db_user = set_global_user_from_cognito(db, user)
    return _preference_response(
        get_chat_route_preference(
            db,
            user_id=db_user.id,
            active_group_ids=_authenticated_groups(user),
        )
    )


@router.put("/me/chat-route-preference", response_model=ChatRoutePreferenceResponse)
async def replace_chat_route_preference(
    request: ChatRoutePreferenceUpdate,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ChatRoutePreferenceResponse:
    """Replace this authenticated user's routing intent."""

    db_user = set_global_user_from_cognito(db, user)
    try:
        state = update_chat_route_preference(
            db,
            user_id=db_user.id,
            mode=request.mode.value,
            agent_key=request.agent_id,
            flow_id=request.flow_id,
            active_group_ids=_authenticated_groups(user),
        )
    except ChatRouteTargetUnavailableError as exc:
        raise HTTPException(
            status_code=404,
            detail="Chat route target is unavailable",
        ) from exc
    return _preference_response(state)


@router.delete("/me/chat-route-preference", response_model=ChatRoutePreferenceResponse)
async def delete_chat_route_preference(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ChatRoutePreferenceResponse:
    """Clear stored routing intent and restore the automatic default."""

    db_user = set_global_user_from_cognito(db, user)
    clear_chat_route_preference(db, user_id=db_user.id)
    return _preference_response(
        ChatRoutePreferenceState("automatic", None, None, True, None)
    )


@router.get("/me/chat-route-targets", response_model=ChatRoutePickerResponse)
async def read_chat_route_targets(
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
) -> ChatRoutePickerResponse:
    """List current authorized Agent and saved-flow picker targets."""

    db_user = set_global_user_from_cognito(db, user)
    targets = list_chat_route_picker_targets(
        db,
        user_id=db_user.id,
        active_group_ids=_authenticated_groups(user),
    )
    return ChatRoutePickerResponse(
        targets=[ChatRoutePickerTarget(**target.__dict__) for target in targets]
    )


# Export router
__all__ = ["router"]
