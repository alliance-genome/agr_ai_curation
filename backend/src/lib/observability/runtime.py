"""Generic Sentry capture helpers for caught runtime exceptions."""

from __future__ import annotations

from collections.abc import Mapping
import importlib
import logging
from typing import Any, Literal

from src.lib.openai_agents.config import (
    get_runtime_observability_context_value_max_chars,
    get_runtime_observability_tag_value_max_chars,
)

logger = logging.getLogger(__name__)

_ALLOWED_TAG_KEYS = {"run_kind"}


def _safe_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return text[:max_chars]


def _safe_tags(tags: Mapping[str, Any] | None) -> dict[str, str]:
    tag_value_max_chars = get_runtime_observability_tag_value_max_chars()
    return {
        str(key): _safe_text(value, max_chars=tag_value_max_chars)
        for key, value in (tags or {}).items()
        if str(key) in _ALLOWED_TAG_KEYS
    }


def _safe_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    context_value_max_chars = get_runtime_observability_context_value_max_chars()
    safe: dict[str, Any] = {}
    for key, value in (context or {}).items():
        if value is None or isinstance(value, (bool, int, float)):
            safe[str(key)] = value
        else:
            safe[str(key)] = _safe_text(value, max_chars=context_value_max_chars)
    return safe


def report_runtime_exception(
    exc: BaseException,
    *,
    component: str,
    operation: str,
    tags: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    level: Literal["fatal", "error", "warning", "info", "debug"] = "error",
) -> bool:
    """Best-effort Sentry capture for caught runtime exceptions."""

    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
    except Exception as import_exc:
        logger.warning("Sentry SDK unavailable for runtime exception capture: %s", import_exc)
        return False

    tag_value_max_chars = get_runtime_observability_tag_value_max_chars()
    safe_component = _safe_text(component, max_chars=tag_value_max_chars)
    safe_operation = _safe_text(operation, max_chars=tag_value_max_chars)

    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_level(level)
            scope.set_tag("alert_type", "runtime_exception")
            scope.set_tag("runtime_component", safe_component)
            scope.set_tag("operation", safe_operation)
            for key, value in _safe_tags(tags).items():
                scope.set_tag(key, value)
            scope.set_context(
                "runtime_exception",
                {
                    "component": safe_component,
                    "operation": safe_operation,
                    **_safe_context(context),
                },
            )
            sentry_sdk.capture_exception(exc)
        return True
    except Exception as capture_exc:
        logger.warning("Failed to capture runtime exception in Sentry: %s", capture_exc)
        return False
