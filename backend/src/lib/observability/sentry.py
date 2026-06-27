"""Sentry application error reporting setup and event redaction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import importlib
import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_INITIALIZED = False
_REDACTED = "[Filtered]"
_MAX_REDACTION_DEPTH = 8

_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "cookie",
    "csrf",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "session",
    "credential",
    "dsn",
)

_CONTENT_KEY_MARKERS = (
    "abstract",
    "body",
    "chunk",
    "content",
    "document_text",
    "messages",
    "pdf",
    "prompt",
    "raw_text",
    "response",
    "transcript",
    "verified_quote",
)

_TRACE_CONTEXT_KEYS = {
    "client_sample_rate",
    "exclusive_time",
    "op",
    "origin",
    "parent_span_id",
    "sampled",
    "span_id",
    "status",
    "trace_id",
    "type",
}

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"pk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
)


@dataclass(frozen=True)
class SentrySettings:
    """Environment-derived Sentry SDK settings."""

    dsn: str | None
    environment: str
    release: str | None
    traces_sample_rate: float | None
    profiles_sample_rate: float | None
    allow_insecure_dsn: bool


def _get_env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    logger.warning("Invalid boolean value for %s: %s, using default %s", key, raw, default)
    return default


def _get_env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default

    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid float value for %s: %s, using default %s", key, raw, default)
        return default

    return min(max(value, 0.0), 1.0)


def get_sentry_settings() -> SentrySettings:
    """Read Sentry SDK settings from the environment."""

    dsn = os.getenv("SENTRY_DSN", "").strip() or None
    environment = (
        os.getenv("SENTRY_ENVIRONMENT")
        or os.getenv("APP_ENV")
        or os.getenv("ENVIRONMENT")
        or "local"
    ).strip()
    release = (os.getenv("SENTRY_RELEASE") or os.getenv("GIT_SHA") or "").strip() or None
    traces_raw = os.getenv("SENTRY_TRACES_SAMPLE_RATE", "").strip()
    profiles_raw = os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "").strip()

    return SentrySettings(
        dsn=dsn,
        environment=environment or "local",
        release=release,
        traces_sample_rate=_get_env_float("SENTRY_TRACES_SAMPLE_RATE", 0.0)
        if traces_raw
        else None,
        profiles_sample_rate=_get_env_float("SENTRY_PROFILES_SAMPLE_RATE", 0.0)
        if profiles_raw
        else None,
        allow_insecure_dsn=_get_env_bool("SENTRY_ALLOW_INSECURE_DSN", False),
    )


def _dsn_is_insecure(dsn: str) -> bool:
    return urlsplit(dsn).scheme.lower() == "http"


def _key_matches(markers: tuple[str, ...], key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(marker in normalized for marker in markers)


def _scrub_string(value: str) -> str:
    scrubbed = value
    for pattern in _SECRET_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed


def _scrub_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if depth > _MAX_REDACTION_DEPTH:
        return _REDACTED

    if key and _key_matches(_SENSITIVE_KEY_MARKERS, key):
        return _REDACTED
    if key and _key_matches(_CONTENT_KEY_MARKERS, key):
        return _REDACTED

    if isinstance(value, Mapping):
        return {
            str(child_key): _scrub_value(
                child_value,
                key=str(child_key),
                depth=depth + 1,
            )
            for child_key, child_value in value.items()
        }

    if isinstance(value, list):
        return [_scrub_value(item, depth=depth + 1) for item in value]

    if isinstance(value, tuple):
        return tuple(_scrub_value(item, depth=depth + 1) for item in value)

    if isinstance(value, str):
        return _scrub_string(value)

    return value


def _strip_query_string(url: str) -> str:
    if not url:
        return url
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _redact_untrusted_strings(value: Any, *, depth: int = 0) -> Any:
    """Redact arbitrary string values from untrusted event containers."""

    if depth > _MAX_REDACTION_DEPTH:
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _redact_untrusted_strings(child_value, depth=depth + 1)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_untrusted_strings(item, depth=depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_untrusted_strings(item, depth=depth + 1) for item in value)
    if isinstance(value, str):
        return _REDACTED
    return value


def _redact_contexts(contexts: dict[str, Any]) -> dict[str, Any]:
    """Redact custom contexts while preserving Sentry trace bookkeeping."""

    redacted: dict[str, Any] = {}
    for context_key, context_value in contexts.items():
        normalized_key = str(context_key)
        if normalized_key == "trace" and isinstance(context_value, Mapping):
            redacted[normalized_key] = {
                str(child_key): _scrub_value(child_value, key=str(child_key))
                for child_key, child_value in context_value.items()
                if str(child_key) in _TRACE_CONTEXT_KEYS
            }
            continue

        redacted[normalized_key] = _redact_untrusted_strings(context_value)

    return redacted


def _remove_stack_frame_vars(container: dict[str, Any]) -> None:
    """Drop stack-frame locals if an event already contains them."""

    values = container.get("values")
    if not isinstance(values, list):
        return

    for value in values:
        if not isinstance(value, dict):
            continue
        stacktrace = value.get("stacktrace")
        if not isinstance(stacktrace, dict):
            continue
        frames = stacktrace.get("frames")
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if isinstance(frame, dict):
                frame.pop("vars", None)


def _redact_event(event: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive or curator/document-derived data before Sentry upload."""

    scrubbed = _scrub_value(event)
    if not isinstance(scrubbed, dict):
        return {}

    request = scrubbed.get("request")
    if isinstance(request, dict):
        request.pop("query_string", None)
        request.pop("data", None)
        request.pop("cookies", None)
        if isinstance(request.get("url"), str):
            request["url"] = _strip_query_string(request["url"])

    for key in ("message", "logentry"):
        if key in scrubbed:
            scrubbed[key] = _REDACTED

    if isinstance(scrubbed.get("extra"), dict):
        scrubbed["extra"] = _redact_untrusted_strings(scrubbed["extra"])
    if isinstance(scrubbed.get("contexts"), dict):
        scrubbed["contexts"] = _redact_contexts(scrubbed["contexts"])

    breadcrumbs = scrubbed.get("breadcrumbs")
    if isinstance(breadcrumbs, dict) and isinstance(breadcrumbs.get("values"), list):
        for breadcrumb in breadcrumbs["values"]:
            if isinstance(breadcrumb, dict):
                breadcrumb.pop("message", None)
                breadcrumb.pop("data", None)

    exception = scrubbed.get("exception")
    if isinstance(exception, dict) and isinstance(exception.get("values"), list):
        for value in exception["values"]:
            if isinstance(value, dict) and "value" in value:
                value["value"] = _REDACTED
        _remove_stack_frame_vars(exception)

    threads = scrubbed.get("threads")
    if isinstance(threads, dict):
        _remove_stack_frame_vars(threads)

    return scrubbed


def before_send(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Redact error events before Sentry upload."""

    return _redact_event(event)


def before_send_transaction(
    event: dict[str, Any],
    hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Redact transaction events before Sentry upload."""

    return _redact_event(event)


def initialize_sentry_if_configured() -> bool:
    """Initialize the Sentry SDK once when a safe DSN is configured."""

    global _INITIALIZED
    if _INITIALIZED:
        return True

    settings = get_sentry_settings()
    if not settings.dsn:
        logger.info("Sentry not configured - running without application error reporting")
        return False

    if _dsn_is_insecure(settings.dsn) and not settings.allow_insecure_dsn:
        logger.warning(
            "Sentry DSN uses http://; refusing to initialize unless "
            "SENTRY_ALLOW_INSECURE_DSN=true"
        )
        return False

    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
        fastapi_integration = importlib.import_module("sentry_sdk.integrations.fastapi")
        starlette_integration = importlib.import_module("sentry_sdk.integrations.starlette")
    except Exception as exc:
        logger.warning("Sentry SDK is not available: %s", exc)
        return False

    init_kwargs: dict[str, Any] = {
        "dsn": settings.dsn,
        "environment": settings.environment,
        "release": settings.release,
        "before_send": before_send,
        "before_send_transaction": before_send_transaction,
        "include_local_variables": False,
        "send_default_pii": False,
        "integrations": [
            starlette_integration.StarletteIntegration(),
            fastapi_integration.FastApiIntegration(),
        ],
        "default_integrations": True,
    }
    if settings.traces_sample_rate is not None:
        init_kwargs["traces_sample_rate"] = settings.traces_sample_rate
    if settings.profiles_sample_rate is not None:
        init_kwargs["profiles_sample_rate"] = settings.profiles_sample_rate

    try:
        sentry_sdk.init(**init_kwargs)
        sentry_sdk.set_tag("app", "ai-curation")
        sentry_sdk.set_tag("component", "backend")
    except Exception as exc:
        logger.warning("Sentry initialization failed (non-fatal): %s", exc)
        return False

    _INITIALIZED = True
    logger.info(
        "Sentry application error reporting initialized (environment=%s release=%s traces_sample_rate=%s)",
        settings.environment,
        settings.release,
        settings.traces_sample_rate,
    )
    return True


def _reset_sentry_for_tests() -> None:
    """Reset module state for unit tests."""

    global _INITIALIZED
    _INITIALIZED = False
