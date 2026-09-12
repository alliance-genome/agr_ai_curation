"""Request-local authorization compiler for Agent Studio callable tools."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from src.lib.agent_access import is_resource_access_allowed
from src.lib.agent_studio.tool_policy_service import get_tool_policy_cache
from src.lib.openai_agents.config import get_agent_studio_tool_search_max_candidates
from src.models.sql.tool_policy import ToolPolicy


class ToolSearchAuthorizationError(RuntimeError):
    """The callable universe could not be compiled safely."""

    def __init__(self, message: str, *, candidate_count: int, bound: int) -> None:
        super().__init__(message)
        self.candidate_count = candidate_count
        self.bound = bound

    def sanitized_context(self) -> dict[str, Any]:
        return {
            "authorization_phase": "tool_declaration",
            "candidate_count": self.candidate_count,
            "bound": self.bound,
            "bound_exceeded": self.candidate_count > self.bound,
        }


@dataclass(frozen=True)
class AuthorizedToolUniverse:
    definitions: tuple[dict[str, Any], ...]
    authorized_names: frozenset[str]
    fingerprint: str
    candidate_count: int
    filtered_count: int


def _allowed_groups(config: Mapping[str, Any]) -> list[str]:
    value = config.get("allowed_group_ids", [])
    return [str(group_id) for group_id in value] if isinstance(value, list) else []


def _policy_allows(entry: Any, active_group_ids: Sequence[str]) -> bool:
    return bool(
        entry.curator_visible
        and entry.allow_execute
        and is_resource_access_allowed(
            visibility_allowed=True,
            allowed_group_ids=_allowed_groups(dict(entry.config or {})),
            active_group_ids=list(active_group_ids),
            resource_kind="agent_studio_callable_tool",
        )
    )


def compile_authorized_tool_universe(
    *,
    db: Session,
    definitions: Sequence[Mapping[str, Any]],
    user_id: int,
    active_group_ids: Sequence[str],
) -> AuthorizedToolUniverse:
    """Validate, policy-filter, bound, and fingerprint callable definitions."""

    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise ToolSearchAuthorizationError(
            "Authenticated database identity is required for tool declaration",
            candidate_count=len(definitions),
            bound=get_agent_studio_tool_search_max_candidates(),
        )
    maximum = get_agent_studio_tool_search_max_candidates()
    if len(definitions) > maximum:
        raise ToolSearchAuthorizationError(
            "Agent Studio authorized tool catalog exceeds its configured bound",
            candidate_count=len(definitions),
            bound=maximum,
        )
    policies = {
        entry.tool_key: entry for entry in get_tool_policy_cache().refresh(db)
    }
    authorized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in definitions:
        definition = dict(raw)
        name = str(definition.get("name") or "").strip()
        schema = definition.get("input_schema")
        if not name or name in seen or not isinstance(schema, Mapping):
            raise ToolSearchAuthorizationError(
                "Agent Studio callable catalog contains an invalid definition",
                candidate_count=len(definitions),
                bound=maximum,
            )
        seen.add(name)
        policy = policies.get(name)
        if policy is not None and not _policy_allows(policy, active_group_ids):
            continue
        authorized.append(definition)
    authorized.sort(key=lambda item: str(item["name"]))
    canonical = json.dumps(
        authorized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return AuthorizedToolUniverse(
        definitions=tuple(authorized),
        authorized_names=frozenset(str(item["name"]) for item in authorized),
        fingerprint=fingerprint,
        candidate_count=len(definitions),
        filtered_count=len(definitions) - len(authorized),
    )


def is_tool_authorized_at_invocation(
    *,
    db: Session,
    tool_name: str,
    declared_names: frozenset[str],
    active_group_ids: Sequence[str],
) -> bool:
    """Recheck durable policy immediately before invoking a declared tool."""

    if tool_name not in declared_names:
        return False
    policy = db.query(ToolPolicy).filter(ToolPolicy.tool_key == tool_name).first()
    if policy is None:
        return True
    return _policy_allows(policy, active_group_ids)


__all__ = [
    "AuthorizedToolUniverse",
    "ToolSearchAuthorizationError",
    "compile_authorized_tool_universe",
    "is_tool_authorized_at_invocation",
]
