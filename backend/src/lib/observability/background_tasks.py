"""Sentry-aware helpers for FastAPI background task failures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import functools
import importlib
import inspect
import logging
from typing import Any

from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)

def _task_name(func: Callable[..., Any], explicit_name: str | None) -> str:
    if explicit_name:
        return explicit_name
    return getattr(func, "__qualname__", getattr(func, "__name__", "background_task"))


def _safe_tag_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    return text[:200]


def _safe_tags(tags: Mapping[str, Any] | None) -> dict[str, str]:
    return {str(key): _safe_tag_value(value) for key, value in (tags or {}).items()}


def _safe_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (context or {}).items():
        if value is None or isinstance(value, (bool, int, float)):
            safe[str(key)] = value
        else:
            safe[str(key)] = _safe_tag_value(value)
    return safe


def report_background_task_exception(
    exc: BaseException,
    *,
    task_name: str,
    tags: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> bool:
    """Best-effort Sentry capture for caught or wrapped background failures."""

    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
    except Exception as import_exc:
        logger.warning("Sentry SDK unavailable for background task capture: %s", import_exc)
        return False

    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_level("error")
            scope.set_tag("alert_type", "background_task_failure")
            scope.set_tag("task_name", task_name)
            for key, value in _safe_tags(tags).items():
                scope.set_tag(key, value)
            scope.set_context(
                "background_task",
                {
                    "task_name": task_name,
                    **_safe_context(context),
                },
            )
            sentry_sdk.capture_exception(exc)
        return True
    except Exception as capture_exc:
        logger.warning("Failed to capture background task exception in Sentry: %s", capture_exc)
        return False


def observed_background_task(
    func: Callable[..., Any],
    *,
    task_name: str | None = None,
    tags: Mapping[str, Any] | None = None,
    observability_context: Mapping[str, Any] | None = None,
) -> Callable[..., Any]:
    """Wrap a background task so uncaught exceptions are reported then re-raised."""

    name = _task_name(func, task_name)
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                report_background_task_exception(
                    exc,
                    task_name=name,
                    tags=tags,
                    context=observability_context,
                )
                raise

        wrapper: Callable[..., Any] = _async_wrapper
    else:

        @functools.wraps(func)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                report_background_task_exception(
                    exc,
                    task_name=name,
                    tags=tags,
                    context=observability_context,
                )
                raise

        wrapper = _sync_wrapper

    setattr(wrapper, "__observability_original_task__", func)
    setattr(wrapper, "__observability_task_name__", name)
    return wrapper


def add_observed_background_task(
    background_tasks: BackgroundTasks,
    func: Callable[..., Any],
    *args: Any,
    task_name: str | None = None,
    tags: Mapping[str, Any] | None = None,
    observability_context: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Queue a FastAPI background task with best-effort Sentry failure capture."""

    background_tasks.add_task(
        observed_background_task(
            func,
            task_name=task_name,
            tags=tags,
            observability_context=observability_context,
        ),
        *args,
        **kwargs,
    )
