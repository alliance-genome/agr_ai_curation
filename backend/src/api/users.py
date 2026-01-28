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
from src.models.sql.database import get_db
from src.services.user_service import set_global_user_from_cognito


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


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

    # Return user information
    return db_user.to_dict()


# Export router
__all__ = ["router"]
