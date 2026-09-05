"""Trusted human admission, separate from orchestration and source credentials."""

import logging
from typing import Any, NoReturn

from anyio.to_thread import run_sync
from fastapi import Depends, Header, HTTPException, Request
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select

from src.api import auth as browser_auth
from src.api.benchmark_auth import require_benchmark_run
from src.lib.benchmarks.curator_authorization import authorize_benchmark_curator
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext, capture_curator_context
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.openai_agents.config import get_benchmark_curator_auth_max_bytes
from src.lib.security.redaction import active_secret_redaction
from src.models.sql.database import SessionLocal
from src.models.sql.user import User

logger = logging.getLogger(__name__)


def _unavailable(exc: Exception, operation: str) -> NoReturn:
    raise_sanitized_http_exception(
        logger, status_code=503,
        detail=_failure(503, "curator_authorization_unavailable").detail,
        log_message="Benchmark curator authorization unavailable",
        exc=sanitized_benchmark_error(operation, type(exc).__name__),
    )


def _failure(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={
        "code": code, "message": "Verified current AI Curation curator authorization required",
    })


async def require_benchmark_curator(
    request: Request,
    orchestration: dict[str, Any] = Depends(require_benchmark_run),
    curator_authorization: str | None = Header(
        default=None, alias="X-Benchmark-Curator-Authorization",
        description="Ephemeral Bearer token for the execution target's configured AI Curation audience; not an ABC source token.",
    ),
) -> BenchmarkCuratorContext:
    """Return only a token-free receipt after provider and active-account checks.

    A remote portal must obtain a target-appropriate human token; its own login
    audience is not implicitly trusted. No development/API-key bypass applies.
    """
    cookie = request.cookies.get("auth_token") or request.cookies.get("cognito_token")
    if curator_authorization is not None:
        if cookie or len(curator_authorization.encode("utf-8")) > get_benchmark_curator_auth_max_bytes():
            raise _failure(401, "invalid_curator_authorization")
        scheme, separator, token = curator_authorization.partition(" ")
        if scheme != "Bearer" or not separator or not token or any(c.isspace() for c in token):
            raise _failure(401, "invalid_curator_authorization")
    else:
        token = cookie
    if not token or len(token.encode("utf-8")) > get_benchmark_curator_auth_max_bytes():
        raise _failure(401, "curator_authorization_required")

    try:
        with active_secret_redaction(token):
            provider = browser_auth._get_provider_or_503()
            principal = provider.extract_principal(await provider.validate_token(token))
    except InvalidTokenError:
        raise _failure(401, "invalid_curator_authorization") from None
    except Exception as exc:
        _unavailable(exc, "curator_token_validation")
    finally:
        del token, cookie, curator_authorization

    client_id = orchestration.get("client_id")
    is_service = (
        orchestration.get("token_use") == "access"
        and isinstance(client_id, str) and bool(client_id)
        and orchestration.get("sub") == f"service:{client_id}"
    )
    if not is_service and principal.subject != orchestration.get("sub"):
        raise _failure(403, "curator_identity_mismatch")

    def capture() -> BenchmarkCuratorContext:
        with SessionLocal() as session:
            user = session.scalar(select(User).where(User.auth_sub == principal.subject))
            if user is None:
                raise PermissionError("Active curator account required")
            return capture_curator_context(principal, user=user)

    try:
        context = await run_sync(capture)
        return await authorize_benchmark_curator(context)
    except PermissionError:
        raise _failure(403, "curator_authorization_required") from None
    except Exception as exc:
        _unavailable(exc, "curator_current_authorization")
