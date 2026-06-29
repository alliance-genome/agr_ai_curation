"""Sentry application error reporting setup and event redaction."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import json
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
    "data",
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
_SPAN_NUMERIC_KEYS = {"client_sample_rate", "exclusive_time"}
_SPAN_TIMESTAMP_KEYS = {"start_timestamp", "timestamp"}
_SENTRY_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?$")
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
    ai_content_capture_tier: int
    ai_content_preview_max_chars: int
    ai_content_tier1_preview_max_chars: int
    transaction_retained_spans_max: int


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


def _get_env_int(
    key: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer value for %s: %s, using default %s", key, raw, default)
        return default

    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


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
        ai_content_capture_tier=_get_env_int(
            "SENTRY_AI_CONTENT_CAPTURE_TIER",
            2,
            minimum=0,
            maximum=2,
        ),
        ai_content_preview_max_chars=_get_env_int(
            "SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS",
            2000,
            minimum=256,
            maximum=200000,
        ),
        ai_content_tier1_preview_max_chars=_get_env_int(
            "SENTRY_AI_CONTENT_TIER1_PREVIEW_MAX_CHARS",
            2000,
            minimum=256,
            maximum=20000,
        ),
        transaction_retained_spans_max=_get_env_int(
            "SENTRY_TRANSACTION_RETAINED_SPANS_MAX",
            50,
            minimum=50,
            maximum=1000,
        ),
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

_AI_CURATION_IDENTIFIER_DATA_KEYS = {
    "ai_curation.chat.session_id",
    "ai_curation.document.id",
    "ai_curation.flow.id",
    "ai_curation.flow.run_id",
    "ai_curation.trace.id",
    "ai_curation.validator.request_id",
}

_AI_CURATION_SAFE_TEXT_DATA_KEYS = {
    "ai_curation.adapter.key",
    "ai_curation.agent.key",
    "ai_curation.agent.output_type",
    "ai_curation.agent.source",
    "ai_curation.chat.session_id_hash",
    "ai_curation.domain_pack.id",
    "ai_curation.document.id_hash",
    "ai_curation.finalization.status",
    "ai_curation.finalization.tool",
    "ai_curation.flow.id_hash",
    "ai_curation.flow.name",
    "ai_curation.flow.run_id_hash",
    "ai_curation.specialist.name",
    "ai_curation.tool.kind",
    "ai_curation.tool.name",
    "ai_curation.trace.id_hash",
    "ai_curation.validation.status",
    "ai_curation.validator.agent_id",
    "ai_curation.validator.binding_id",
    "ai_curation.validator.package_id",
    "ai_curation.workflow",
}

_AI_CURATION_CONTENT_DATA_KEYS = {
    "ai_curation.agent.input",
    "ai_curation.agent.output",
    "ai_curation.error.detail",
    "ai_curation.finalization.detail",
    "ai_curation.tool.input",
    "ai_curation.tool.output",
    "ai_curation.validation.detail",
}

_AI_CURATION_NUMERIC_DATA_KEYS = {
    "ai_curation.agent.events_collected",
    "ai_curation.candidate.count",
    "ai_curation.content.capture_tier",
    "ai_curation.error.event_count",
    "ai_curation.finalization.attempt",
    "ai_curation.finalization.max_attempts",
    "ai_curation.flow.total_steps",
    "ai_curation.validator.batch_size",
    "ai_curation.tool_call.count",
    "ai_curation.validation.error_count",
    "ai_curation.sentry.spans.dropped_by_redactor",
    "ai_curation.sentry.spans.retained_by_redactor",
    "ai_curation.sentry.spans.total_before_redactor",
}

_AI_CURATION_BOOLEAN_DATA_KEYS = {
    "ai_curation.document.present",
    "ai_curation.finalization.required",
}


def _ai_content_capture_tier() -> int:
    return get_sentry_settings().ai_content_capture_tier


def _ai_content_preview_max_chars() -> int:
    return get_sentry_settings().ai_content_preview_max_chars


def _gen_ai_content_capture_enabled() -> bool:
    return _get_env_bool("SENTRY_OPENAI_INCLUDE_PROMPTS", False)


def _truncate_ai_content(value: Any, *, max_chars: int, depth: int = 0) -> Any:
    if depth > _MAX_REDACTION_DEPTH:
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _truncate_ai_content(
                child_value,
                max_chars=max_chars,
                depth=depth + 1,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            _truncate_ai_content(item, max_chars=max_chars, depth=depth + 1)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _truncate_ai_content(item, max_chars=max_chars, depth=depth + 1)
            for item in value
        )
    if isinstance(value, str):
        scrubbed = _scrub_string(value)
        if len(scrubbed) <= max_chars:
            return scrubbed
        return scrubbed[:max_chars] + f"...[truncated {len(scrubbed) - max_chars} chars]"
    return value


def _scrub_ai_content(value: Any, *, max_chars: int | None = None) -> Any:
    limit = max_chars if max_chars is not None else _ai_content_preview_max_chars()
    scrubbed = _scrub_value(value, allow_content=True)
    truncated = _truncate_ai_content(scrubbed, max_chars=limit)
    if isinstance(truncated, (Mapping, list, tuple)):
        try:
            serialized = json.dumps(truncated, default=str, sort_keys=True)
        except Exception:
            serialized = str(truncated)
        if len(serialized) > limit:
            return serialized[:limit] + f"...[truncated {len(serialized) - limit} chars]"
    return truncated


def _redact_ai_curation_span_data(key: str, value: Any, *, content_tier: int) -> Any:
    if key in _AI_CURATION_IDENTIFIER_DATA_KEYS:
        hashed = _hash_identifier(value)
        return hashed if hashed is not None else None

    if key in _AI_CURATION_SAFE_TEXT_DATA_KEYS:
        if isinstance(value, list):
            return [
                safe_text
                for item in value
                if (safe_text := _safe_gen_ai_text(item)) is not None
            ]
        return _safe_gen_ai_text(value)

    if key in _AI_CURATION_NUMERIC_DATA_KEYS and _is_real_number(value):
        return value

    if key in _AI_CURATION_BOOLEAN_DATA_KEYS and isinstance(value, bool):
        return value

    if key in _AI_CURATION_CONTENT_DATA_KEYS:
        if content_tier >= 2:
            return _scrub_ai_content(value)
        if content_tier == 1:
            return _scrub_ai_content(
                value,
                max_chars=get_sentry_settings().ai_content_tier1_preview_max_chars,
            )
        return _redact_untrusted_strings(value)

    if key.startswith("ai_curation."):
        return None

    return None


def _redact_single_span_data(key: str, value: Any) -> Any:
    redacted = _redact_span_data({key: value})
    if isinstance(redacted, Mapping):
        return redacted.get(key)
    return None


def _set_redacted_span_data(set_data: Any, key: str, value: Any) -> None:
    redacted_value = _redact_single_span_data(key, value)
    if redacted_value is not None:
        set_data(key, redacted_value)


def set_redacted_ai_span_data(span: Any, key: str, value: Any) -> None:
    """Set AI span data through the same redaction path used at span creation."""

    set_data = getattr(span, "set_data", None)
    if callable(set_data):
        _set_redacted_span_data(set_data, key, value)


def hash_sentry_identifier(value: Any) -> str | None:
    """Return the stable Sentry-safe hash used for runtime identifiers."""

    hashed = _hash_identifier(value)
    if hashed in {None, _REDACTED}:
        return None
    return hashed


def set_sentry_span_status(span: Any, status: str) -> None:
    """Best-effort status setter for Sentry spans/transactions."""

    safe_status = _safe_trace_text(status)
    if safe_status is None:
        return

    set_status = getattr(span, "set_status", None)
    if callable(set_status):
        try:
            set_status(safe_status)
        except Exception as exc:
            logger.debug("Sentry span status update failed: %s", exc)


class _SentryParentOnlySpan:
    """Delegate status to an active parent while keeping transaction data compact."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_data(self, key: str, value: Any) -> None:
        return None

    def set_status(self, status: str) -> None:
        set_sentry_span_status(self._span, status)


def _redact_span_data(data: Any) -> Any:
    """Preserve AI monitoring metadata while keeping content capture opt-in."""

    if not isinstance(data, Mapping):
        return _redact_untrusted_strings(data)

    redacted: dict[str, Any] = {}
    include_content = _gen_ai_content_capture_enabled()
    content_tier = _ai_content_capture_tier()
    for raw_key, value in data.items():
        key = str(raw_key)

        if key in _GEN_AI_CONTENT_DATA_KEYS:
            redacted[key] = (
                _scrub_ai_content(value) if include_content else _REDACTED
            )
            continue

        if key.startswith("ai_curation."):
            redacted_value = _redact_ai_curation_span_data(
                key,
                value,
                content_tier=content_tier,
            )
            if redacted_value is not None:
                redacted[key] = redacted_value
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


def _safe_sentry_timestamp(value: Any) -> int | float | str | None:
    if _is_real_number(value):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _SENTRY_TIMESTAMP_PATTERN.fullmatch(text):
            return text
    return None


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
            continue

        if key == "data" and isinstance(child_value, Mapping):
            redacted[key] = _redact_span_data(child_value)

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


@contextmanager
def gen_ai_invoke_agent_span(
    *,
    agent_name: str | None,
    model: str | None,
    conversation_id: str | None,
    workflow: str | None = None,
    tool_name: str | None = None,
    agent_key: str | None = None,
    agent_source: str | None = None,
    specialist_name: str | None = None,
    trace_id: str | None = None,
    flow_run_id: str | None = None,
    document_id: str | None = None,
    document_present: bool | None = None,
    finalization_required: bool | None = None,
    finalization_tool: str | None = None,
    finalization_attempt: int | None = None,
    finalization_status: str | None = None,
    validation_status: str | None = None,
    validation_error_count: int | None = None,
    tool_call_count: int | None = None,
    candidate_count: int | None = None,
    input_preview: Any | None = None,
    output_preview: Any | None = None,
    span_data: Mapping[str, Any] | None = None,
):
    """Create a minimal manual Sentry AI span for an agent invocation."""

    settings = get_sentry_settings()
    if not settings.ai_agents_monitoring_enabled:
        yield None
        return

    safe_agent_name = _safe_gen_ai_text(agent_name) or "agent"
    safe_model = _safe_gen_ai_text(model) if model else None
    hashed_conversation_id = _hash_identifier(conversation_id)
    if hashed_conversation_id == _REDACTED:
        hashed_conversation_id = None

    span_context: Any | None = None
    span = None
    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
        start_span = getattr(sentry_sdk, "start_span", None)
        if not callable(start_span):
            yield None
            return

        span_context = start_span(
            op="gen_ai.invoke_agent",
            name=f"invoke_agent {safe_agent_name}",
        )
        span = span_context.__enter__()
        set_data = getattr(span, "set_data", None)
        if callable(set_data):
            def safe_set(key: str, value: Any) -> None:
                _set_redacted_span_data(set_data, key, value)

            safe_set("gen_ai.operation.name", "invoke_agent")
            safe_set("gen_ai.agent.name", safe_agent_name)
            safe_set("gen_ai.provider.name", "openai")
            safe_set("gen_ai.response.streaming", True)
            safe_set("ai_curation.content.capture_tier", settings.ai_content_capture_tier)
            if safe_model:
                safe_set("gen_ai.request.model", safe_model)
            if hashed_conversation_id:
                safe_set("gen_ai.conversation.id", hashed_conversation_id)
                safe_set("ai_curation.chat.session_id_hash", hashed_conversation_id)
            if workflow:
                safe_set("ai_curation.workflow", workflow)
            if tool_name:
                safe_set("ai_curation.tool.name", tool_name)
            if agent_key:
                safe_set("ai_curation.agent.key", agent_key)
            if agent_source:
                safe_set("ai_curation.agent.source", agent_source)
            if specialist_name:
                safe_set("ai_curation.specialist.name", specialist_name)
            if trace_id:
                hashed_trace_id = _hash_identifier(trace_id)
                if hashed_trace_id not in {None, _REDACTED}:
                    safe_set("ai_curation.trace.id_hash", hashed_trace_id)
            if flow_run_id:
                hashed_flow_run_id = _hash_identifier(flow_run_id)
                if hashed_flow_run_id not in {None, _REDACTED}:
                    safe_set("ai_curation.flow.run_id_hash", hashed_flow_run_id)
            if document_id:
                hashed_document_id = _hash_identifier(document_id)
                if hashed_document_id not in {None, _REDACTED}:
                    safe_set("ai_curation.document.id_hash", hashed_document_id)
            if document_present is not None:
                safe_set("ai_curation.document.present", document_present)
            if finalization_required is not None:
                safe_set("ai_curation.finalization.required", finalization_required)
            if finalization_tool:
                safe_set("ai_curation.finalization.tool", finalization_tool)
            if finalization_attempt is not None:
                safe_set("ai_curation.finalization.attempt", finalization_attempt)
            if finalization_status:
                safe_set("ai_curation.finalization.status", finalization_status)
            if validation_status:
                safe_set("ai_curation.validation.status", validation_status)
            if validation_error_count is not None:
                safe_set("ai_curation.validation.error_count", validation_error_count)
            if tool_call_count is not None:
                safe_set("ai_curation.tool_call.count", tool_call_count)
            if candidate_count is not None:
                safe_set("ai_curation.candidate.count", candidate_count)
            if input_preview is not None:
                safe_set("ai_curation.agent.input", input_preview)
            if output_preview is not None:
                safe_set("ai_curation.agent.output", output_preview)
            if span_data:
                for key, value in span_data.items():
                    if str(key).startswith(("ai_curation.", "gen_ai.")):
                        safe_set(str(key), value)
    except Exception as exc:
        logger.debug("Sentry AI invoke-agent span unavailable: %s", exc)
        span_context = None

    try:
        yield span
    finally:
        if span_context is not None:
            try:
                span_context.__exit__(None, None, None)
            except Exception as exc:
                logger.debug("Sentry AI invoke-agent span cleanup failed: %s", exc)


@contextmanager
def gen_ai_workflow_transaction(
    *,
    name: str,
    workflow: str,
    conversation_id: str | None,
    operation: str = "http.server",
    document_id: str | None = None,
    document_present: bool | None = None,
    trace_id: str | None = None,
    input_preview: Any | None = None,
    span_data: Mapping[str, Any] | None = None,
):
    """Create a transaction that can parent GenAI spans beyond framework request scope."""

    settings = get_sentry_settings()
    if not settings.ai_agents_monitoring_enabled:
        yield None
        return

    safe_name = _safe_trace_text(name) or "ai_curation.chat"
    safe_operation = _safe_trace_text(operation) or "http.server"
    safe_workflow = _safe_gen_ai_text(workflow)
    hashed_conversation_id = _hash_identifier(conversation_id)
    if hashed_conversation_id == _REDACTED:
        hashed_conversation_id = None

    transaction_context: Any | None = None
    transaction = None
    transaction_handle = None
    capture_transaction_data = True
    try:
        sentry_sdk = importlib.import_module("sentry_sdk")
        get_current_scope = getattr(sentry_sdk, "get_current_scope", None)
        if callable(get_current_scope):
            scope = get_current_scope()
            transaction = getattr(scope, "span", None)
            if transaction is not None:
                capture_transaction_data = False

        if transaction is None:
            start_transaction = getattr(sentry_sdk, "start_transaction", None)
            if not callable(start_transaction):
                yield None
                return

            transaction_context = start_transaction(op=safe_operation, name=safe_name)
            transaction = transaction_context.__enter__()

        transaction_handle = (
            transaction if capture_transaction_data else _SentryParentOnlySpan(transaction)
        )
        set_data = getattr(transaction_handle, "set_data", None)
        if callable(set_data):
            def safe_set(key: str, value: Any) -> None:
                _set_redacted_span_data(set_data, key, value)

            safe_set("gen_ai.operation.name", "chat")
            safe_set("gen_ai.provider.name", "openai")
            safe_set("gen_ai.response.streaming", True)
            safe_set("ai_curation.content.capture_tier", settings.ai_content_capture_tier)
            if safe_workflow:
                safe_set("ai_curation.workflow", safe_workflow)
            if hashed_conversation_id:
                safe_set("gen_ai.conversation.id", hashed_conversation_id)
                safe_set("ai_curation.chat.session_id_hash", hashed_conversation_id)
            if trace_id:
                hashed_trace_id = hash_sentry_identifier(trace_id)
                if hashed_trace_id:
                    safe_set("ai_curation.trace.id_hash", hashed_trace_id)
            if document_id:
                hashed_document_id = hash_sentry_identifier(document_id)
                if hashed_document_id:
                    safe_set("ai_curation.document.id_hash", hashed_document_id)
            if document_present is not None:
                safe_set("ai_curation.document.present", document_present)
            if input_preview is not None:
                safe_set("ai_curation.agent.input", input_preview)
            if span_data:
                for key, value in span_data.items():
                    if str(key).startswith(("ai_curation.", "gen_ai.")):
                        safe_set(str(key), value)
    except Exception as exc:
        logger.debug("Sentry AI workflow transaction unavailable: %s", exc)
        transaction_context = None

    try:
        yield transaction_handle
    except BaseException as exc:
        if transaction_handle is not None:
            set_sentry_span_status(
                transaction_handle,
                "cancelled" if type(exc).__name__ == "CancelledError" else "internal_error",
            )
            set_redacted_ai_span_data(
                transaction_handle,
                "ai_curation.error.detail",
                {
                    "message": str(exc),
                    "error_type": type(exc).__name__,
                    "phase": "workflow_transaction",
                },
            )
        raise
    finally:
        if transaction_context is not None:
            try:
                transaction_context.__exit__(None, None, None)
            except Exception as exc:
                logger.debug("Sentry AI workflow transaction cleanup failed: %s", exc)


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
            elif normalized_key in _SPAN_TIMESTAMP_KEYS:
                safe_timestamp = _safe_sentry_timestamp(value)
                if safe_timestamp is not None:
                    safe_span[normalized_key] = safe_timestamp
            elif normalized_key == "same_process_as_parent" and isinstance(value, bool):
                safe_span[normalized_key] = value
            elif normalized_key == "data":
                safe_span[normalized_key] = _redact_span_data(value)
            elif (
                normalized_key == "description"
                and str(span.get("op", "")).startswith("gen_ai.")
                and (safe_text := _safe_gen_ai_text(value)) is not None
            ):
                safe_span[normalized_key] = safe_text
            elif normalized_key in {"description", "tags"}:
                safe_span[normalized_key] = _redact_untrusted_strings(value)

        redacted.append(safe_span)

    return redacted


def _span_priority(span: Mapping[str, Any]) -> int:
    op = str(span.get("op") or "")
    status = str(span.get("status") or "")
    if op.startswith("gen_ai."):
        return 0
    if status and status not in {"ok", "unknown", "unset"}:
        return 1
    return 2


def _limit_transaction_spans(
    spans: list[Any],
    *,
    max_spans: int | None = None,
) -> tuple[list[Any], int]:
    """Keep transaction events under Sentry ingest limits while preserving GenAI spans."""

    if max_spans is None:
        max_spans = get_sentry_settings().transaction_retained_spans_max
    if len(spans) <= max_spans:
        return spans, 0

    indexed_spans = [
        (index, span)
        for index, span in enumerate(spans)
        if isinstance(span, Mapping)
    ]
    selected_indexes: set[int] = set()
    for priority in (0, 1, 2):
        for index, span in indexed_spans:
            if len(selected_indexes) >= max_spans:
                break
            if index in selected_indexes:
                continue
            if _span_priority(span) == priority:
                selected_indexes.add(index)
        if len(selected_indexes) >= max_spans:
            break

    retained = [
        span
        for index, span in enumerate(spans)
        if index in selected_indexes
    ]
    return retained, len(spans) - len(retained)


def _record_span_redaction_counts(
    event: dict[str, Any],
    *,
    total_before: int,
    retained: int,
    dropped: int,
) -> None:
    if dropped <= 0:
        return

    contexts = event.setdefault("contexts", {})
    if not isinstance(contexts, dict):
        return
    trace_context = contexts.setdefault("trace", {})
    if not isinstance(trace_context, dict):
        return
    trace_data = trace_context.setdefault("data", {})
    if not isinstance(trace_data, dict):
        return

    trace_data["ai_curation.sentry.spans.total_before_redactor"] = total_before
    trace_data["ai_curation.sentry.spans.retained_by_redactor"] = retained
    trace_data["ai_curation.sentry.spans.dropped_by_redactor"] = dropped


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
        redacted_spans = _redact_spans(raw_spans)
        limited_spans, dropped_spans = _limit_transaction_spans(redacted_spans)
        scrubbed["spans"] = limited_spans
        _record_span_redaction_counts(
            scrubbed,
            total_before=len(redacted_spans),
            retained=len(limited_spans),
            dropped=dropped_spans,
        )

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
