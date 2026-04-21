"""Shared chat history configuration sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


def _read_bool_env(env: Mapping[str, str], key: str, default: str) -> bool:
    return str(env.get(key, default)).strip().lower() == "true"


def _read_int_env(env: Mapping[str, str], key: str, default: str) -> int:
    return int(str(env.get(key, default)).strip())


@dataclass(frozen=True)
class ChatHistoryConfig:
    """Runtime chat history settings shared across API and agent code."""

    history_enabled: bool
    max_exchanges: int
    include_in_routing: bool
    include_in_response: bool
    max_sessions_per_user: int

    def as_history_dict(self) -> dict[str, int | bool]:
        """Return the public chat config payload consumed by `/api/chat/config`."""

        return {
            "enabled": self.history_enabled,
            "max_exchanges": self.max_exchanges,
            "include_in_routing": self.include_in_routing,
            "include_in_response": self.include_in_response,
            "max_sessions_per_user": self.max_sessions_per_user,
        }


def load_chat_history_config(env: Mapping[str, str] | None = None) -> ChatHistoryConfig:
    """Load chat history configuration from the current environment."""

    source_env = env or os.environ
    return ChatHistoryConfig(
        history_enabled=_read_bool_env(source_env, "CHAT_HISTORY_ENABLED", "true"),
        max_exchanges=_read_int_env(source_env, "CHAT_MAX_HISTORY_EXCHANGES", "20"),
        include_in_routing=_read_bool_env(source_env, "CHAT_HISTORY_IN_ROUTING", "true"),
        include_in_response=_read_bool_env(source_env, "CHAT_HISTORY_IN_RESPONSE", "true"),
        max_sessions_per_user=_read_int_env(source_env, "CHAT_MAX_SESSIONS_PER_USER", "50"),
    )


chat_history_config = load_chat_history_config()


__all__ = [
    "ChatHistoryConfig",
    "chat_history_config",
    "load_chat_history_config",
]
