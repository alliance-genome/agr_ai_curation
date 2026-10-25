"""Reauthorize queued work from current provider membership, never saved claims."""

from auth_runtime.base import AuthPrincipal, CurrentPrincipalDenied
from sqlalchemy import select
from src.auth.current_principal import get_current_subject_resolver
from src.lib.group_rules import get_groups_from_provider_groups
from src.lib.validation_history import store
from src.models.sql.user import User


def require_current_access(owner_id, frozen_groups):
    # Called off-thread, before opening any dispatch transaction. The resolver
    # performs authoritative account and complete membership checks.
    try:
        principal = get_current_subject_resolver()(owner_id)
    except CurrentPrincipalDenied:
        raise store.HistoryAccessError(
            "Current workspace authorization denied"
        ) from None
    if not isinstance(principal, AuthPrincipal):
        raise TypeError("Current subject resolver must return AuthPrincipal")
    with store.SessionLocal() as db:
        user = db.scalar(select(User).where(User.auth_sub == owner_id))
        if (
            user is None
            or user.is_active is not True
            or principal.subject != owner_id
            or not set(frozen_groups).issubset(
                get_groups_from_provider_groups(principal.groups)
            )
        ):
            raise store.HistoryAccessError("Current workspace authorization denied")
    # Added groups do not widen the immutable command's capability grant.
    return tuple(frozen_groups)
