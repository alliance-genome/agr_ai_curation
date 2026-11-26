"""Generic Sentry capture helpers for caught runtime exceptions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import importlib
import logging
from typing import Any, Literal

from src.lib.openai_agents.config import (
    get_document_max_files_per_paper,
    get_runtime_observability_context_value_max_chars,
    get_runtime_observability_tag_value_max_chars,
)

logger = logging.getLogger(__name__)

_ALLOWED_TAG_KEYS = {
    "ai_curation.trace.id_hash",
    "ai_curation.chat.session_id_hash",
    "ai_curation.flow.id_hash",
    "flow_failure_type",
    "failure_category",
    "phase",
    "provider",
    "run_kind",
    "tool_name",
}


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


def supplement_refusal_context(
    component: object, operation: object, context: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project this one warning onto the same closed schema at capture and before_send."""
    if component != "document_files" or operation != "supplement_add_nothing_added":
        return None

    from src.lib.document_sources.file_reasons import ADD_REFUSAL_REASONS
    from src.lib.observability.sentry import SENTRY_IDENTIFIER_HASH_PATTERN, hash_sentry_identifier

    safe = {
        "component": component,
        "operation": operation,
        "document_id": hash_sentry_identifier(context.get("document_id")),
    }
    counts = context.get("reason_counts")
    entries = context.get("refusals")
    if not isinstance(counts, Mapping) or not isinstance(entries, list):
        return safe

    reason_counts = {
        reason.value: count
        for reason in ADD_REFUSAL_REASONS
        if type(count := counts.get(reason.value)) is int and count > 0
    }
    refusals = []
    for entry in entries[:get_document_max_files_per_paper()]:
        if not isinstance(entry, Mapping):
            continue
        reason = entry.get("reason_code")
        if not isinstance(reason, str) or reason not in reason_counts:
            continue
        detail: dict[str, Any] = {"reason_code": reason}
        for key in ("measured_value", "limit_value"):
            value = entry.get(key)
            if key in entry and (value is None or type(value) is int):
                detail[key] = value
        artifact_hash = entry.get("artifact_id_hash")
        if ("artifact_id_hash" in entry and artifact_hash is None) or (
            isinstance(artifact_hash, str) and SENTRY_IDENTIFIER_HASH_PATTERN.fullmatch(artifact_hash)
        ):
            detail["artifact_id_hash"] = artifact_hash
        refusals.append(detail)

    safe["refusals"] = refusals
    safe["reason_counts"] = reason_counts
    omitted = sum(reason_counts.values()) - len(refusals)
    if omitted >= 0:
        safe["omitted_count"] = omitted
    return safe


def _safe_context(
    context: Mapping[str, Any] | None, *, component: str, operation: str,
) -> dict[str, Any]:
    refusals = supplement_refusal_context(component, operation, context or {})
    if refusals is not None:
        return refusals

    context_value_max_chars = get_runtime_observability_context_value_max_chars()
    safe: dict[str, Any] = {}
    for key, value in (context or {}).items():
        if value is None or isinstance(value, (bool, int, float)):
            safe[str(key)] = value
        else:
            safe[str(key)] = _safe_text(value, max_chars=context_value_max_chars)
    return safe


def sanitized_runtime_error(message: str) -> RuntimeError:
    """Build a traceback-bearing wrapper from a fixed, content-free message.

    Callers must not interpolate raw exception details or user/provider content.
    Sever the active exception chain before passing the wrapper to reporting.
    """
    try:
        raise RuntimeError(message) from None
    except RuntimeError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def _omit_request(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Remove request metadata added by framework integrations to this capture."""
    event.pop("request", None)
    return event


def report_runtime_exception(
    exc: BaseException,
    *,
    component: str,
    operation: str,
    tags: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    level: Literal["fatal", "error", "warning", "info", "debug"] = "error",
    fingerprint: Sequence[str] | None = None,
    include_request: bool = True,
) -> bool:
    """Best-effort Sentry capture for caught runtime exceptions.

    ``fingerprint`` overrides Sentry grouping with stable, low-cardinality
    parts; never include per-run identifiers in it.
    Set ``include_request=False`` when the concrete request can identify private resources.
    """

    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if getattr(cause, "_ai_curation_sentry_captured", False):
            return False
        cause = cause.__cause__

    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
    except Exception:
        logger.warning("Sentry SDK unavailable for runtime exception capture", extra={"sentry_skip_event": True})
        return False

    tag_value_max_chars = get_runtime_observability_tag_value_max_chars()
    safe_component = _safe_text(component, max_chars=tag_value_max_chars)
    safe_operation = _safe_text(operation, max_chars=tag_value_max_chars)

    try:
        with sentry_sdk.new_scope() as scope:
            if not include_request:
                scope.add_event_processor(_omit_request)
            scope.set_level(level)
            if fingerprint:
                scope.fingerprint = [
                    _safe_text(part, max_chars=tag_value_max_chars)
                    for part in fingerprint
                ]
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
                    **_safe_context(context, component=component, operation=operation),
                },
            )
            event_id = sentry_sdk.capture_exception(exc)
        return bool(event_id)
    except Exception:
        logger.warning("Failed to capture runtime exception in Sentry", extra={"sentry_skip_event": True})
        return False
