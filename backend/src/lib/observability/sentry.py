"""Sentry application error reporting setup and event redaction."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
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
_TRACE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
_SPAN_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{16}$")
_HASHED_IDENTIFIER_PATTERN = re.compile(r"^sha256:[0-9a-f]{16}$")
_SAFE_TRACE_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]{1,100}$")
_SAFE_GEN_AI_TEXT_PATTERN = re.compile(r"^[A-Za-z0-9_.:/() -]{1,160}$")
_TRACE_TEXT_KEYS = {"op", "origin", "status", "type"}
_TRACE_NUMERIC_KEYS = {"client_sample_rate", "exclusive_time"}
_TRACE_BOOLEAN_KEYS = {"sampled"}
_SPAN_NUMERIC_KEYS = {"client_sample_rate", "exclusive_time", "start_timestamp", "timestamp"}
_RUNTIME_IDENTIFIER_CONTEXT_KEYS = {
    "batch_id",
    "document_id",
    "flow_id",
    "flow_run_id",
    "job_id",
    "run_id",
    "session_id",
    "trace_id",
    "turn_id",
}
_RUNTIME_TEXT_CONTEXT_KEYS = {
    "component",
    "extraction_strategy",
    "level_name",
    "logger_name",
    "operation",
    "type",
}
_RUNTIME_TEXT_LIST_CONTEXT_KEYS = {"stages_completed"}

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
    send_default_pii: bool
    ai_agents_monitoring_enabled: bool
    openai_agents_integration_enabled: bool
    openai_integration_enabled: bool
    gen_ai_stream_spans_enabled: bool
    openai_include_prompts: bool


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
        send_default_pii=_get_env_bool("SENTRY_SEND_DEFAULT_PII", False),
        ai_agents_monitoring_enabled=_get_env_bool(
            "SENTRY_AI_AGENTS_MONITORING_ENABLED",
            False,
        ),
        openai_agents_integration_enabled=_get_env_bool(
            "SENTRY_OPENAI_AGENTS_INTEGRATION_ENABLED",
            False,
        ),
        openai_integration_enabled=_get_env_bool(
            "SENTRY_OPENAI_INTEGRATION_ENABLED",
            False,
        ),
        gen_ai_stream_spans_enabled=_get_env_bool(
            "SENTRY_GEN_AI_STREAM_SPANS_ENABLED",
            False,
        ),
        openai_include_prompts=_get_env_bool("SENTRY_OPENAI_INCLUDE_PROMPTS", False),
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


def _scrub_value(
    value: Any,
    *,
    key: str = "",
    depth: int = 0,
    allow_content: bool = False,
) -> Any:
    if depth > _MAX_REDACTION_DEPTH:
        return _REDACTED

    if key and _key_matches(_SENSITIVE_KEY_MARKERS, key):
        return _REDACTED
    if key and not allow_content and _key_matches(_CONTENT_KEY_MARKERS, key):
        return _REDACTED

    if isinstance(value, Mapping):
        return {
            str(child_key): _scrub_value(
                child_value,
                key=str(child_key),
                depth=depth + 1,
                allow_content=allow_content,
            )
            for child_key, child_value in value.items()
        }

    if isinstance(value, list):
        return [_scrub_value(item, depth=depth + 1, allow_content=allow_content) for item in value]

    if isinstance(value, tuple):
        return tuple(
            _scrub_value(item, depth=depth + 1, allow_content=allow_content)
            for item in value
        )

    if isinstance(value, str):
        return _scrub_string(value)

    return value


def _redact_request_url(url: str) -> str:
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


_GEN_AI_CONTENT_DATA_KEYS = {
    "gen_ai.embeddings.input",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.request.available_tools",
    "gen_ai.request.messages",
    "gen_ai.response.text",
    "gen_ai.response.tool_calls",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "gen_ai.tool.definitions",
    "gen_ai.tool.description",
    "gen_ai.tool.input",
    "gen_ai.tool.output",
    "gen_ai.user.message",
}

_GEN_AI_SAFE_TEXT_DATA_KEYS = {
    "gen_ai.agent.name",
    "gen_ai.operation.name",
    "gen_ai.pipeline.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.finish_reasons",
    "gen_ai.response.id",
    "gen_ai.response.model",
    "gen_ai.system",
    "gen_ai.tool.name",
}

_GEN_AI_IDENTIFIER_DATA_KEYS = {
    "gen_ai.conversation.id",
    "gen_ai.function_id",
}

_GEN_AI_NUMERIC_DATA_PREFIXES = (
    "gen_ai.request.",
    "gen_ai.response.time_to_first_token",
    "gen_ai.usage.",
)


def _gen_ai_content_capture_enabled() -> bool:
    return _get_env_bool("SENTRY_OPENAI_INCLUDE_PROMPTS", False)


def _redact_span_data(data: Any) -> Any:
    """Preserve AI monitoring metadata while keeping content capture opt-in."""

    if not isinstance(data, Mapping):
        return _redact_untrusted_strings(data)

    redacted: dict[str, Any] = {}
    include_content = _gen_ai_content_capture_enabled()
    for raw_key, value in data.items():
        key = str(raw_key)

        if key in _GEN_AI_CONTENT_DATA_KEYS:
            redacted[key] = (
                _scrub_value(value, allow_content=True) if include_content else _REDACTED
            )
            continue

        if key in _GEN_AI_SAFE_TEXT_DATA_KEYS:
            if isinstance(value, list):
                safe_items = [
                    safe_text
                    for item in value
                    if (safe_text := _safe_gen_ai_text(item)) is not None
                ]
                redacted[key] = safe_items
                continue

            safe_text = _safe_gen_ai_text(value)
            if safe_text is not None:
                redacted[key] = safe_text
            continue

        if key in _GEN_AI_IDENTIFIER_DATA_KEYS:
            hashed = _hash_identifier(value)
            if hashed is not None:
                redacted[key] = hashed
            continue

        if key == "gen_ai.response.streaming" and isinstance(value, bool):
            redacted[key] = value
            continue

        if key.startswith(_GEN_AI_NUMERIC_DATA_PREFIXES) and _is_real_number(value):
            redacted[key] = value
            continue

        redacted[key] = _redact_untrusted_strings(value)

    return redacted


def _is_real_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _safe_trace_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    scrubbed = _scrub_string(value.strip())
    if scrubbed != value.strip():
        return None
    if not _SAFE_TRACE_TEXT_PATTERN.fullmatch(scrubbed):
        return None
    return scrubbed


def _safe_gen_ai_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    scrubbed = _scrub_string(value.strip())
    if scrubbed != value.strip():
        return None
    if not _SAFE_GEN_AI_TEXT_PATTERN.fullmatch(scrubbed):
        return None
    return scrubbed


def _redact_trace_fields(trace_context: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve only schema-valid Sentry trace fields."""

    redacted: dict[str, Any] = {}
    for child_key, child_value in trace_context.items():
        key = str(child_key)
        if key not in _TRACE_CONTEXT_KEYS:
            continue

        if key == "trace_id":
            value = str(child_value)
            if _TRACE_ID_PATTERN.fullmatch(value):
                redacted[key] = value
            continue

        if key in {"span_id", "parent_span_id"}:
            value = str(child_value)
            if _SPAN_ID_PATTERN.fullmatch(value):
                redacted[key] = value
            continue

        if key in _TRACE_TEXT_KEYS:
            safe_text = _safe_trace_text(child_value)
            if safe_text is not None:
                redacted[key] = safe_text
            continue

        if key in _TRACE_BOOLEAN_KEYS:
            if isinstance(child_value, bool):
                redacted[key] = child_value
            continue

        if key in _TRACE_NUMERIC_KEYS and _is_real_number(child_value):
            redacted[key] = child_value

    return redacted


def _hash_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if _HASHED_IDENTIFIER_PATTERN.fullmatch(text):
        return text
    if _scrub_string(text) != text:
        return _REDACTED

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


@contextmanager
def gen_ai_conversation_scope(conversation_id: str | None):
    """Attach a stable Sentry AI conversation ID for spans emitted in this scope."""

    hashed_conversation_id = _hash_identifier(conversation_id)
    if hashed_conversation_id in {None, _REDACTED}:
        yield
        return

    settings = get_sentry_settings()
    if not settings.ai_agents_monitoring_enabled:
        yield
        return

    scope = None
    sentry_ai = None
    previous_conversation_id = None
    conversation_bound = False
    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
        sentry_ai = importlib.import_module("sentry_sdk.ai")
        get_current_scope = getattr(sentry_sdk, "get_current_scope", None)
        if callable(get_current_scope):
            scope = get_current_scope()
            get_conversation_id = getattr(scope, "get_conversation_id", None)
            if callable(get_conversation_id):
                previous_conversation_id = get_conversation_id()
        sentry_ai.set_conversation_id(hashed_conversation_id)
        conversation_bound = True
    except Exception as exc:
        logger.debug("Sentry AI conversation scope unavailable: %s", exc)

    try:
        yield
    finally:
        if not conversation_bound or sentry_ai is None:
            return
        try:
            if previous_conversation_id:
                setter = getattr(scope, "set_conversation_id", None) if scope is not None else None
                if callable(setter):
                    setter(previous_conversation_id)
                else:
                    sentry_ai.set_conversation_id(previous_conversation_id)
                return

            remover = getattr(scope, "remove_conversation_id", None) if scope is not None else None
            if callable(remover):
                remover()
            else:
                sentry_ai.set_conversation_id(None)
        except Exception as exc:
            logger.debug("Sentry AI conversation scope cleanup failed: %s", exc)


def _redact_runtime_exception_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve bounded runtime diagnostics without exposing raw identifiers."""

    redacted: dict[str, Any] = {}
    for child_key, child_value in context.items():
        key = str(child_key)

        if key in _RUNTIME_IDENTIFIER_CONTEXT_KEYS:
            hashed = _hash_identifier(child_value)
            if hashed is not None:
                redacted[key] = hashed
            continue

        if key in _RUNTIME_TEXT_CONTEXT_KEYS:
            safe_text = _safe_trace_text(child_value)
            if safe_text is not None:
                redacted[key] = safe_text
            continue

        if key in _RUNTIME_TEXT_LIST_CONTEXT_KEYS and isinstance(child_value, list):
            safe_items = [
                safe_text
                for item in child_value
                if (safe_text := _safe_trace_text(item)) is not None
            ]
            redacted[key] = safe_items
            continue

        if child_value is None or isinstance(child_value, (bool, int, float)):
            redacted[key] = child_value
            continue

        redacted[key] = _redact_untrusted_strings(child_value)

    return redacted


def _redact_contexts(contexts: dict[str, Any]) -> dict[str, Any]:
    """Redact custom contexts while preserving Sentry trace bookkeeping."""

    redacted: dict[str, Any] = {}
    for context_key, context_value in contexts.items():
        normalized_key = str(context_key)
        if normalized_key == "trace" and isinstance(context_value, Mapping):
            redacted[normalized_key] = _redact_trace_fields(context_value)
            continue

        if normalized_key == "runtime_exception" and isinstance(context_value, Mapping):
            redacted[normalized_key] = _redact_runtime_exception_context(context_value)
            continue

        redacted[normalized_key] = _redact_untrusted_strings(context_value)

    return redacted


def _redact_spans(spans: list[Any]) -> list[Any]:
    """Redact transaction span payloads while preserving low-risk trace fields."""

    redacted: list[Any] = []
    for span in spans:
        if not isinstance(span, Mapping):
            continue

        safe_span = _redact_trace_fields(span)
        for key, value in span.items():
            normalized_key = str(key)
            if normalized_key in safe_span:
                continue
            if normalized_key in _SPAN_NUMERIC_KEYS and _is_real_number(value):
                safe_span[normalized_key] = value
            elif normalized_key == "data":
                safe_span[normalized_key] = _redact_span_data(value)
            elif normalized_key in {"description", "tags"}:
                safe_span[normalized_key] = _redact_untrusted_strings(value)

        redacted.append(safe_span)

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

    raw_contexts = event.get("contexts")
    raw_spans = event.get("spans")
    scrubbed = _scrub_value(event)
    if not isinstance(scrubbed, dict):
        return {}

    request = scrubbed.get("request")
    if isinstance(request, dict):
        request.pop("query_string", None)
        request.pop("data", None)
        request.pop("cookies", None)
        if isinstance(request.get("url"), str):
            request["url"] = _redact_request_url(request["url"])

    for key in ("message", "logentry"):
        if key in scrubbed:
            scrubbed[key] = _REDACTED

    if isinstance(scrubbed.get("extra"), dict):
        scrubbed["extra"] = _redact_untrusted_strings(scrubbed["extra"])
    if isinstance(raw_contexts, dict):
        scrubbed["contexts"] = _redact_contexts(raw_contexts)
    if isinstance(raw_spans, list):
        scrubbed["spans"] = _redact_spans(raw_spans)

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


def _load_optional_integration(
    module_name: str,
    class_name: str,
    **kwargs: Any,
) -> Any | None:
    """Best-effort Sentry integration loader for optional AI monitoring hooks."""

    try:
        module = importlib.import_module(module_name)
        integration_class = getattr(module, class_name)
        return integration_class(**kwargs)
    except Exception as exc:
        logger.warning(
            "Optional Sentry integration %s.%s is unavailable: %s",
            module_name,
            class_name,
            exc,
        )
        return None


def _optional_ai_integrations(settings: SentrySettings) -> list[Any]:
    if not settings.ai_agents_monitoring_enabled:
        return []

    integrations: list[Any] = []

    if settings.openai_agents_integration_enabled:
        integration = _load_optional_integration(
            "sentry_sdk.integrations.openai_agents",
            "OpenAIAgentsIntegration",
        )
        if integration is not None:
            integrations.append(integration)

    if settings.openai_integration_enabled:
        integration = _load_optional_integration(
            "sentry_sdk.integrations.openai",
            "OpenAIIntegration",
            include_prompts=settings.openai_include_prompts,
        )
        if integration is not None:
            integrations.append(integration)

    return integrations


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
        logging_integration = importlib.import_module("sentry_sdk.integrations.logging")
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
        "send_default_pii": settings.send_default_pii,
        "integrations": [
            # Explicit runtime helpers report sanitized 5xx HTTP failures; avoid
            # a second handled-HTTPException event from the framework integration.
            starlette_integration.StarletteIntegration(
                failed_request_status_codes=set(),
            ),
            fastapi_integration.FastApiIntegration(
                failed_request_status_codes=set(),
            ),
            logging_integration.LoggingIntegration(
                level=logging.INFO,
                event_level=None,
            ),
            *_optional_ai_integrations(settings),
        ],
        "default_integrations": True,
    }
    if settings.ai_agents_monitoring_enabled and settings.gen_ai_stream_spans_enabled:
        init_kwargs["stream_gen_ai_spans"] = True
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
