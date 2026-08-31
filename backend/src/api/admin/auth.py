"""Canonical authorization policy for privileged admin APIs."""

import logging
import os

from fastapi import HTTPException

from src.api.auth import get_auth_dependency

logger = logging.getLogger(__name__)


def _parse_admin_emails() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", "")
    return {email.strip().lower() for email in raw.split(",") if email.strip()}


_admin_emails_cache = _parse_admin_emails()


def get_admin_emails() -> set[str]:
    """Return the process-cached ADMIN_EMAILS allowlist."""
    return _admin_emails_cache


async def require_admin(user: dict = get_auth_dependency()) -> dict:
    """Require the authenticated user's email to be on the admin allowlist."""
    admin_emails = get_admin_emails()
    if not admin_emails:
        if os.getenv("DEV_MODE", "false").lower() == "true":
            logger.warning("ADMIN_EMAILS not set, allowing access in DEV_MODE")
            return user
        raise HTTPException(
            status_code=403,
            detail="Admin access not configured. Set ADMIN_EMAILS environment variable.",
        )
    user_email = user.get("email", "").lower()
    if user_email not in admin_emails:
        logger.warning("Admin access denied for user: %s", user_email)
        raise HTTPException(
            status_code=403,
            detail="Admin access required. Contact your administrator.",
        )
    return user
