"""Request-scoped document-source access helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from src.lib.config.groups_loader import (
    get_group_claim_key,
    get_groups_for_provider_groups,
)
from src.lib.document_sources.dev_curator_auth import (
    get_dev_curator_credentials,
    renewable_dev_curator_auth_required,
)


@dataclass(frozen=True, slots=True)
class DocumentSourceRequestContext:
    """Request-local authorization context for provider-backed imports."""

    provider_groups: tuple[str, ...]
    authorized_group_ids: tuple[str, ...]
    curator_token: str | None = field(default=None, repr=False)

    @property
    def has_curator_token(self) -> bool:
        """Return whether a bearer-capable curator token is available."""
        return bool(self.curator_token)


async def build_document_source_request_context(
    *,
    request: Request | None,
    user_claims: Mapping[str, Any],
) -> DocumentSourceRequestContext:
    """Build provider access context from validated user claims and cookies.

    The raw token is kept only in this in-memory request object. It must not be
    logged, serialized, stored, or returned to the browser.
    """

    if renewable_dev_curator_auth_required():
        credentials = await get_dev_curator_credentials()
        provider_groups = _extract_provider_groups(credentials.claims)
        curator_token = credentials.token
    else:
        provider_groups = _extract_provider_groups(user_claims)
        curator_token = _extract_curator_token(request, user_claims)
    authorized_group_ids = tuple(get_groups_for_provider_groups(list(provider_groups)))
    return DocumentSourceRequestContext(
        provider_groups=provider_groups,
        authorized_group_ids=authorized_group_ids,
        curator_token=curator_token,
    )


def _extract_provider_groups(user_claims: Mapping[str, Any]) -> tuple[str, ...]:
    candidate_keys = [get_group_claim_key(), "groups", "cognito:groups"]
    seen: set[str] = set()
    groups: list[str] = []
    for key in candidate_keys:
        raw_value = user_claims.get(key)
        for group in _coerce_group_values(raw_value):
            if group not in seen:
                seen.add(group)
                groups.append(group)
    return tuple(groups)


def _coerce_group_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _extract_curator_token(
    request: Request | None,
    user_claims: Mapping[str, Any],
) -> str | None:
    if request is None:
        return None
    if _claims_allow_cookie_token(user_claims):
        token = request.cookies.get("auth_token") or request.cookies.get("cognito_token")
        if token:
            return token
    return None


def _claims_allow_cookie_token(user_claims: Mapping[str, Any]) -> bool:
    subject = str(user_claims.get("sub") or user_claims.get("uid") or "")
    return not subject.startswith("api-key-")
