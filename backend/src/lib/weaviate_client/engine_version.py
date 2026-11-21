"""Search index (Weaviate) engine version: read it, and refuse a server that is not the pinned one."""

from __future__ import annotations

from typing import Any, Optional

from ..openai_agents.config import get_weaviate_expected_version


class WeaviateEngineVersionMismatch(RuntimeError):
    """The running search index engine is not the version this deployment pins."""


# The version accepted by the last successful check in this process. Health
# payloads reuse it instead of calling /v1/meta on every request.
_recorded_version: Optional[str] = None


def server_engine_version(client: Any) -> str:
    """Engine version the server reports in ``/v1/meta``."""
    return str(client.get_meta()["version"])


def recorded_engine_version() -> Optional[str]:
    """Engine version accepted at connection time, or None before any check."""
    return _recorded_version


def require_expected_engine_version(client: Any) -> str:
    """Return the server's engine version, or raise when it differs from the pin."""
    global _recorded_version
    expected = get_weaviate_expected_version()
    actual = server_engine_version(client)
    if actual != expected:
        raise WeaviateEngineVersionMismatch(
            f"Search index (Weaviate) engine version mismatch: the server reports {actual}, "
            f"but WEAVIATE_EXPECTED_VERSION is {expected}. The backend and the benchmark "
            "worker will not start against an engine version they were not pinned to. "
            "Run the pinned image "
            f"(semitechnologies/weaviate:{expected}), or change the pin in the compose "
            "file and WEAVIATE_EXPECTED_VERSION together as a planned engine upgrade."
        )
    _recorded_version = actual
    return actual
