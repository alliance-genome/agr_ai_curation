"""Read-only current curator authorization, without stored human credentials."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import boto3
from botocore.config import Config

from src.auth.base import AuthPrincipal
from src.config import (
    get_auth_provider,
    get_cognito_region,
    get_cognito_user_pool_id,
    is_dev_mode,
)
from src.lib.benchmarks.execution_context import (
    BenchmarkCuratorContext,
    require_current_curator_authorization,
)
from src.lib.openai_agents.config import (
    get_benchmark_curator_auth_max_attempts,
    get_benchmark_curator_auth_timeout_seconds,
)
from src.models.sql.database import SessionLocal
from src.models.sql.user import User


def _lookup_cognito_principal(
    frozen: BenchmarkCuratorContext, *, client: Any, pool_id: str, issuer: str,
) -> AuthPrincipal:
    """Resolve a verified provider username and verify its stable subject.

    IAM authorizes these reads. Neither a source bearer nor the historical
    curator token is accepted. All group pages are required before success.
    """
    if (
        frozen.auth_provider != "oidc"
        or frozen.auth_issuer != issuer
        or not frozen.provider_username
    ):
        raise PermissionError("Curator identity does not match the configured provider")
    account = client.admin_get_user(UserPoolId=pool_id, Username=frozen.provider_username)
    subjects = [
        attribute.get("Value") for attribute in account.get("UserAttributes", [])
        if attribute.get("Name") == "sub"
    ]
    if (
        account.get("Enabled") is not True
        or account.get("Username") != frozen.provider_username
        or subjects != [frozen.subject]
    ):
        raise PermissionError("Current curator account is unavailable or mismatched")
    groups: list[str] = []
    request = {"UserPoolId": pool_id, "Username": frozen.provider_username}
    seen_tokens: set[str] = set()
    while True:
        page = client.admin_list_groups_for_user(**request)
        for group in page["Groups"]:
            name = group["GroupName"]
            if not isinstance(name, str) or not name or name != name.strip():
                raise PermissionError("Current curator group response is invalid")
            groups.append(name)
        token = page.get("NextToken")
        if token is None:
            break
        if not isinstance(token, str) or not token or token in seen_tokens:
            raise PermissionError("Current curator group pagination is invalid")
        seen_tokens.add(token)
        request["NextToken"] = token
    return AuthPrincipal(
        subject=subjects[0], provider="oidc", groups=groups,
        raw_claims={"iss": issuer, "cognito:username": account["Username"]},
    )


def _configured_current_principal(frozen: BenchmarkCuratorContext) -> AuthPrincipal:
    # Generic OIDC does not define a token-free administrative membership API.
    # Do not reinterpret frozen claims or use development bypass as a lookup.
    if is_dev_mode() or get_auth_provider() != "cognito":
        raise PermissionError("Current curator lookup is not configured for this provider")
    region = get_cognito_region()
    pool_id = get_cognito_user_pool_id()
    if not region or not pool_id:
        raise PermissionError("Current curator lookup requires a configured user pool")
    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    timeout = get_benchmark_curator_auth_timeout_seconds()
    client = boto3.client(
        "cognito-idp", region_name=region,
        config=Config(
            connect_timeout=timeout, read_timeout=timeout,
            retries={"mode": "standard", "total_max_attempts": get_benchmark_curator_auth_max_attempts()},
        ),
    )
    try:
        return _lookup_cognito_principal(frozen, client=client, pool_id=pool_id, issuer=issuer)
    finally:
        client.close()


async def authorize_benchmark_curator(
    frozen: BenchmarkCuratorContext,
    *,
    session_factory: Callable[..., Any] = SessionLocal,
) -> BenchmarkCuratorContext:
    """Recheck the provider and local active account without changing frozen groups."""
    try:
        principal = await asyncio.to_thread(_configured_current_principal, frozen)
        with session_factory() as session:
            return require_current_curator_authorization(
                frozen, current_principal=principal,
                current_user=session.get(User, frozen.db_user_id),
            )
    except Exception:
        # SDK exception text may contain account/provider details; do not put
        # it in persisted benchmark failure metadata or an exception chain.
        raise PermissionError("Current benchmark curator authorization failed") from None
