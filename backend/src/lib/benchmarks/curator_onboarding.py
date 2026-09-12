"""Explicit first-login provisioning; discovery never calls this operation."""

from typing import Callable, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from src.auth.base import AuthPrincipal, PrincipalLookupIdentity
from src.auth.current_principal import get_current_principal_resolver
from src.lib.benchmarks.models import FrozenStrictModel
from src.lib.group_rules import get_groups_from_provider_groups
from src.lib.openai_agents.config import get_benchmark_environment_id
from src.models.sql.database import SessionLocal
from src.models.sql.user import User
from src.services.user_service import provision_user


class BenchmarkCuratorOnboardingReceipt(FrozenStrictModel):
    schema_version: Literal[1] = 1
    subject: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)


def _identity(principal: AuthPrincipal) -> PrincipalLookupIdentity:
    return PrincipalLookupIdentity(
        subject=principal.subject,
        auth_provider=principal.provider,
        auth_issuer=principal.raw_claims.get("iss"),
        provider_username=principal.raw_claims.get("cognito:username"),
    )


def _active_user(session: Session, subject: str) -> User | None:
    user = session.scalar(
        select(User).where(User.auth_sub == subject).with_for_update()
    )
    if user is not None and user.is_active is not True:
        raise PermissionError("Active curator account required")
    return user


def onboard_benchmark_curator(
    principal: AuthPrincipal,
    *,
    session_factory: Callable[..., Session] = SessionLocal,
) -> BenchmarkCuratorOnboardingReceipt:
    """Run off-thread, after service capability and human token verification.

    Current provider checks precede all SQL/tenant writes. A uniqueness collision
    reconciles one concurrent first login, not a retry of arbitrary DB failures.
    """
    if (
        not isinstance(principal, AuthPrincipal)
        or not principal.subject.strip()
        or principal.subject != principal.subject.strip()
        or principal.subject.startswith("service:")
        or principal.provider == "unknown"
    ):
        raise PermissionError("Verified curator identity required")
    identity = _identity(principal)
    if not isinstance(identity.auth_issuer, str) or not identity.auth_issuer.strip():
        raise PermissionError("Verified curator issuer required")
    current = get_current_principal_resolver()(identity)
    if not isinstance(current, AuthPrincipal):
        raise TypeError("Current principal resolver must return AuthPrincipal")
    if _identity(current) != identity or not set(
        get_groups_from_provider_groups(principal.groups)
    ).issubset(get_groups_from_provider_groups(current.groups)):
        raise PermissionError("Current curator identity or groups changed")
    # Validate the acknowledgement before committing anything. Preserve profile
    # metadata from the verified token: current lookup need not return a profile.
    receipt = BenchmarkCuratorOnboardingReceipt(
        subject=principal.subject,
        issuer=identity.auth_issuer,
        environment_id=get_benchmark_environment_id(),
    )
    with session_factory() as session:
        _active_user(session, principal.subject)
        try:
            user = provision_user(session, principal)
        except IntegrityError as error:
            original = error.orig
            diagnostic = getattr(original, "diag", None)
            if (
                getattr(original, "pgcode", None) != "23505"
                or getattr(diagnostic, "constraint_name", None) != "uq_users_auth_sub"
                or getattr(diagnostic, "table_name", None) != "users"
            ):
                raise
            session.rollback()
            if _active_user(session, principal.subject) is None:
                raise
            user = provision_user(session, principal)
        if user.is_active is not True or user.auth_sub != principal.subject:
            raise PermissionError("Active curator account required")
    return receipt
