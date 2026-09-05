"""Token-free curator execution receipts for durable benchmark jobs.

Only authenticated backend code may capture these receipts. Deserializing one
from a request does not authenticate it or authorize benchmark execution.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from src.auth.base import AuthPrincipal
from src.lib.group_rules import get_groups_from_provider_groups
from src.models.sql.user import User

from .models import FrozenStrictModel


class BenchmarkCuratorContext(FrozenStrictModel):
    """The human context used by every comparison arm, not the job's M2M owner."""

    schema_version: Literal[1] = 1
    subject: str = Field(min_length=1)
    auth_provider: str = Field(min_length=1)
    auth_issuer: str | None = None
    provider_username: str | None = None
    db_user_id: int = Field(gt=0)
    active_groups: tuple[str, ...]

    @field_validator("auth_issuer", "provider_username")
    @classmethod
    def require_optional_locator(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError("Curator provider locator must be normalized")
        return value

    @field_validator("subject", "auth_provider")
    @classmethod
    def require_identity(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Curator execution identity must be nonempty and normalized")
        return value

    @field_validator("active_groups")
    @classmethod
    def canonical_groups(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or value != value.strip() for value in values):
            raise ValueError("Curator execution groups must be normalized identities")
        # These are internal execution group IDs, not raw provider role names.
        return tuple(sorted(set(values)))


def capture_curator_context(principal: AuthPrincipal, *, user: User) -> BenchmarkCuratorContext:
    """Capture an already-verified human principal and its current local user row.

    Benchmark run capability must be checked separately by the admission layer.
    Raw claims, email, cookies, and tokens are intentionally not copied.
    """

    if (
        not isinstance(principal, AuthPrincipal)
        or principal.subject.startswith("service:")
        or principal.provider == "unknown"
        or user.is_active is not True
        or user.auth_sub != principal.subject
    ):
        raise PermissionError("Verified active curator identity is required")
    return BenchmarkCuratorContext(
        subject=principal.subject,
        auth_provider=principal.provider,
        auth_issuer=principal.raw_claims.get("iss"),
        provider_username=principal.raw_claims.get("cognito:username"),
        db_user_id=user.id,
        active_groups=tuple(get_groups_from_provider_groups(principal.groups)),
    )


def require_current_curator_authorization(
    frozen: BenchmarkCuratorContext,
    *,
    current_principal: AuthPrincipal | None,
    current_user: User | None,
) -> BenchmarkCuratorContext:
    """Check fresh trusted authorization without changing the experiment's context.

    Callers must obtain current membership from the configured authoritative
    auth boundary, not reconstruct a principal from this frozen receipt. An
    unavailable lookup is represented by None and fails closed. Additional
    newly granted groups do not silently change tools in an accepted experiment.
    """

    if current_principal is None or current_user is None:
        raise PermissionError("Current curator authorization is unavailable")
    current = capture_curator_context(current_principal, user=current_user)
    if (
        current.subject != frozen.subject
        or current.auth_provider != frozen.auth_provider
        or current.auth_issuer != frozen.auth_issuer
        or current.provider_username != frozen.provider_username
        or current.db_user_id != frozen.db_user_id
        or not set(frozen.active_groups).issubset(current.active_groups)
    ):
        raise PermissionError("Curator execution authorization no longer matches")
    return frozen
