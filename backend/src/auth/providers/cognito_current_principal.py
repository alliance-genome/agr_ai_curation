"""Authoritative token-free Cognito account and membership adapter."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from src.auth.base import AuthPrincipal, CurrentPrincipalDenied, PrincipalLookupIdentity
from src.config import get_cognito_region, get_cognito_user_pool_id
from src.lib.openai_agents.config import (
    get_benchmark_curator_auth_max_attempts,
    get_benchmark_curator_auth_timeout_seconds,
)


def _lookup_cognito_principal(
    frozen: PrincipalLookupIdentity, *, client: Any, pool_id: str, issuer: str,
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
        raise CurrentPrincipalDenied("Curator identity does not match the configured provider")
    account = client.admin_get_user(UserPoolId=pool_id, Username=frozen.provider_username)
    if (
        not isinstance(account["Enabled"], bool)
        or not isinstance(account["Username"], str)
        or not account["Username"]
        or not isinstance(account["UserAttributes"], list)
    ):
        raise ValueError("Current curator account response is invalid")
    subjects = [
        attribute.get("Value") for attribute in account["UserAttributes"]
        if attribute.get("Name") == "sub"
    ]
    if len(subjects) != 1 or not isinstance(subjects[0], str) or not subjects[0]:
        raise ValueError("Current curator account subject response is invalid")
    if (
        account["Enabled"] is not True
        or account["Username"] != frozen.provider_username
        or subjects != [frozen.subject]
    ):
        raise CurrentPrincipalDenied("Current curator account is unavailable or mismatched")
    groups: list[str] = []
    request = {"UserPoolId": pool_id, "Username": frozen.provider_username}
    seen_tokens: set[str] = set()
    while True:
        page = client.admin_list_groups_for_user(**request)
        if not isinstance(page["Groups"], list):
            raise ValueError("Current curator group response is invalid")
        for group in page["Groups"]:
            name = group["GroupName"]
            if not isinstance(name, str) or not name or name != name.strip():
                raise ValueError("Current curator group response is invalid")
            groups.append(name)
        token = page.get("NextToken")
        if token is None:
            break
        if not isinstance(token, str) or not token or token in seen_tokens:
            raise ValueError("Current curator group pagination is invalid")
        seen_tokens.add(token)
        request["NextToken"] = token
    return AuthPrincipal(
        subject=subjects[0], provider="oidc", groups=groups,
        raw_claims={"iss": issuer, "cognito:username": account["Username"]},
    )


def resolve_current_principal(frozen: PrincipalLookupIdentity) -> AuthPrincipal:
    region = get_cognito_region()
    pool_id = get_cognito_user_pool_id()
    if not region or not pool_id:
        raise ValueError("Current curator lookup requires a configured user pool")
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
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "UserNotFoundException":
            raise CurrentPrincipalDenied("Current curator account no longer exists") from None
        raise
    finally:
        client.close()

