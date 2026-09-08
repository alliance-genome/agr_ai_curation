"""Read-only current curator authorization, without stored human credentials."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from src.auth.base import AuthPrincipal, CurrentPrincipalDenied, PrincipalLookupIdentity
from src.auth.current_principal import get_current_principal_resolver
from src.lib.benchmarks.execution_context import (
    BenchmarkCuratorContext,
    require_current_curator_authorization,
)
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.models.sql.database import SessionLocal
from src.models.sql.user import User


def _configured_current_principal(frozen: BenchmarkCuratorContext) -> AuthPrincipal:
    resolver = get_current_principal_resolver()
    principal = resolver(PrincipalLookupIdentity(
        subject=frozen.subject, auth_provider=frozen.auth_provider,
        auth_issuer=frozen.auth_issuer, provider_username=frozen.provider_username,
    ))
    if not isinstance(principal, AuthPrincipal):
        raise TypeError("Current principal resolver must return AuthPrincipal")
    return principal


async def authorize_benchmark_curator(
    frozen: BenchmarkCuratorContext,
    *,
    session_factory: Callable[..., Any] = SessionLocal,
) -> BenchmarkCuratorContext:
    """Recheck the provider and local active account without changing frozen groups."""
    def check_current() -> BenchmarkCuratorContext:
        # Both provider I/O and synchronous SQL belong off the event loop.
        # The session is created, used and closed by this one worker thread.
        principal = _configured_current_principal(frozen)
        with session_factory() as session:
            current_user = session.get(User, frozen.db_user_id)
            try:
                return require_current_curator_authorization(
                    frozen, current_principal=principal, current_user=current_user,
                )
            except PermissionError:
                raise CurrentPrincipalDenied("Current curator authorization denied") from None

    try:
        return await asyncio.to_thread(check_current)
    except CurrentPrincipalDenied:
        failure = PermissionError("Current benchmark curator authorization denied")
    except Exception as exc:
        failure = sanitized_benchmark_error("curator_current_authorization", type(exc).__name__)
    # Raise outside the handler so raw provider/SQL details are not retained
    # even as a suppressed exception context.
    raise failure from None
