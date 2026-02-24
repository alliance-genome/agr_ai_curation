"""Tool policy cache service for Agent Workshop Tool Library."""

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from src.models.sql.tool_policy import ToolPolicy

logger = logging.getLogger(__name__)


@dataclass
class ToolPolicyEntry:
    """Serializable tool policy entry for UI/API responses."""

    tool_key: str
    display_name: str
    description: str
    category: str
    curator_visible: bool
    allow_attach: bool
    allow_execute: bool
    config: Dict[str, Any]


def _policy_to_entry(policy: ToolPolicy) -> ToolPolicyEntry:
    return ToolPolicyEntry(
        tool_key=policy.tool_key,
        display_name=policy.display_name,
        description=policy.description or "",
        category=policy.category or "General",
        curator_visible=bool(policy.curator_visible),
        allow_attach=bool(policy.allow_attach),
        allow_execute=bool(policy.allow_execute),
        config=dict(policy.config or {}),
    )


class ToolPolicyCacheService:
    """Caches tool policy rows for low-latency library endpoints."""

    def __init__(self) -> None:
        self._entries: Optional[List[ToolPolicyEntry]] = None
        self._loaded_at_monotonic: float = 0.0
        raw_ttl = os.getenv("TOOL_POLICY_CACHE_TTL_SECONDS", "30").strip() or "30"
        try:
            self._ttl_seconds = max(0.0, float(raw_ttl))
        except ValueError:
            logger.warning(
                "Invalid TOOL_POLICY_CACHE_TTL_SECONDS='%s'; defaulting to 30s",
                raw_ttl,
            )
            self._ttl_seconds = 30.0

    def _is_stale(self) -> bool:
        if self._entries is None:
            return True
        if self._ttl_seconds <= 0:
            return True
        return (time.monotonic() - self._loaded_at_monotonic) >= self._ttl_seconds

    def _load(self, db: Session) -> List[ToolPolicyEntry]:
        rows = (
            db.query(ToolPolicy)
            .order_by(ToolPolicy.category.asc(), ToolPolicy.display_name.asc(), ToolPolicy.tool_key.asc())
            .all()
        )
        entries = [_policy_to_entry(row) for row in rows]
        logger.info("Loaded %s tool policies", len(entries))
        self._loaded_at_monotonic = time.monotonic()
        return entries

    def list_all(self, db: Session) -> List[ToolPolicyEntry]:
        if self._is_stale():
            self._entries = self._load(db)
        return list(self._entries)

    def list_curator_visible(self, db: Session) -> List[ToolPolicyEntry]:
        return [entry for entry in self.list_all(db) if entry.curator_visible]

    def refresh(self, db: Session) -> List[ToolPolicyEntry]:
        self._entries = self._load(db)
        return list(self._entries)


_tool_policy_service: Optional[ToolPolicyCacheService] = None


def get_tool_policy_cache() -> ToolPolicyCacheService:
    """Get singleton tool policy cache service."""
    global _tool_policy_service
    if _tool_policy_service is None:
        _tool_policy_service = ToolPolicyCacheService()
    return _tool_policy_service
