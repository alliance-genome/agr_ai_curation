"""
Agent Configuration Module.

Centralizes all agent settings from environment variables.
Each agent can be configured individually via .env file.

Environment variable naming convention:
  AGENT_{AGENT_NAME}_{SETTING}

Example:
  AGENT_SUPERVISOR_MODEL=gpt-5.6-sol
  AGENT_PDF_MODEL=gpt-5.6-sol
  AGENT_PDF_TEMPERATURE=0.3
  AGENT_GENE_REASONING=medium

Provider Configuration:
  Providers are loaded from config/providers.yaml.
  Models are loaded from config/models.yaml and map to provider IDs.
  Unknown providers/models fail fast (no implicit fallback behavior).
"""

import os
import logging
from typing import Optional, Literal, TYPE_CHECKING, Union
from dataclasses import dataclass

from src.lib.flow_contract_limits import (
    FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS,
    FLOW_DEFINITION_MAX_NODES_DEFAULT,
    FLOW_DESCRIPTION_MAX_CHARS,
    FLOW_NAME_MAX_CHARS,
    FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS,
    FLOW_STEP_GOAL_MAX_CHARS,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agents.extensions.models.litellm_model import LitellmModel

# =============================================================================
# LLM Provider Configuration
# =============================================================================

def get_llm_provider() -> str:
    """Get the configured default runner provider from provider catalog."""
    from src.lib.config.providers_loader import get_default_runner_provider

    return get_default_runner_provider().provider_id


def _normalize_provider_id(provider: Optional[str]) -> str:
    """Normalize provider key formatting."""
    return str(provider or "").strip().lower()


def _get_env_bool(key: str, default: bool) -> bool:
    """Parse boolean environment variable with resilient fallback."""
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


def _get_env_float_with_fallback(key: str, default: float) -> float:
    """Parse float environment variable with resilient fallback."""
    raw = os.getenv(key)
    if raw is None:
        return default

    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float value for %s: %s, using default %s", key, raw, default)
        return default


def _get_env_int_with_fallback(key: str, default: int) -> int:
    """Parse int environment variable with resilient fallback."""
    raw = os.getenv(key)
    if raw is None:
        return default

    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int value for %s: %s, using default %s", key, raw, default)
        return default


def _get_bounded_positive_env_int(
    key: str,
    default: int,
    maximum: int,
) -> int:
    """Read a positive integer and report when canonical capacity clamps it."""

    configured = _get_env_int_with_fallback(key, default)
    if configured < 1:
        logger.warning("%s=%s is below minimum 1; using 1", key, configured)
        configured = 1
    if configured > maximum:
        logger.warning(
            "%s=%s exceeds canonical maximum %s; using %s",
            key,
            configured,
            maximum,
            maximum,
        )
    return min(configured, maximum)


def get_batch_worker_lease_seconds() -> int:
    """Duration of an exclusive batch worker lease before crash recovery."""
    return max(1, _get_env_int_with_fallback("BATCH_WORKER_LEASE_SECONDS", 120))


def get_batch_worker_heartbeat_seconds() -> int:
    """Interval between durable batch worker lease heartbeats."""
    return max(1, _get_env_int_with_fallback("BATCH_WORKER_HEARTBEAT_SECONDS", 30))


def get_batch_recovery_max_concurrency() -> int:
    """Maximum persisted batches processed concurrently during startup recovery."""
    return max(1, _get_env_int_with_fallback("BATCH_RECOVERY_MAX_CONCURRENCY", 4))


def get_submission_attempt_retention_days() -> int:
    """Days to retain terminal direct-submission attempts for audit and deduplication."""
    return max(1, _get_env_int_with_fallback("SUBMISSION_ATTEMPT_RETENTION_DAYS", 90))


def get_submission_attempt_cleanup_interval_seconds() -> int:
    """Seconds between periodic submission-attempt retention cleanup passes."""
    return max(
        1,
        _get_env_int_with_fallback("SUBMISSION_ATTEMPT_CLEANUP_INTERVAL_SECONDS", 3600),
    )


def is_retryable_groq_tool_call_error(exc: Exception) -> bool:
    """Return True when an exception matches known transient Groq tool-call parse failures."""
    text = str(exc or "").lower()
    if not text:
        return False

    markers = (
        "failed to parse tool call arguments as json",
        "tool call arguments are not valid json",
        "invalid json in tool arguments",
        "tool_use_failed",
        "midstreamfallbackerror",
        "groqexception",
    )
    return any(marker in text for marker in markers)


def get_groq_tool_call_max_retries() -> int:
    """Max retries for transient Groq tool-call parse failures."""
    retries = _get_env_int_with_fallback("GROQ_TOOL_CALL_MAX_RETRIES", 2)
    return max(0, retries)


def get_groq_tool_call_retry_delay_seconds() -> float:
    """Base delay in seconds between Groq tool-call retry attempts."""
    delay = _get_env_float_with_fallback("GROQ_TOOL_CALL_RETRY_DELAY_SECONDS", 1.0)
    return max(0.0, delay)


def _apply_provider_tool_call_overrides(
    *,
    provider: str,
    temperature: Optional[float],
    parallel_tool_calls: bool,
) -> tuple[Optional[float], bool]:
    """Apply provider-specific safeguards without affecting other providers."""
    if provider != "groq":
        return temperature, parallel_tool_calls

    # Groq local tool-calling guidance favors low temperatures and retries.
    # Keep this provider-specific so OpenAI/Gemini behavior is unchanged.
    temperature_cap = _get_env_float_with_fallback("GROQ_TOOL_TEMPERATURE_MAX", 0.0)
    effective_temperature = temperature
    if effective_temperature is not None and effective_temperature > temperature_cap:
        logger.info(
            "Capping Groq temperature from %s to %s for tool-calling stability",
            effective_temperature,
            temperature_cap,
        )
        effective_temperature = temperature_cap

    parallel_enabled = _get_env_bool("GROQ_PARALLEL_TOOL_CALLS_ENABLED", False)
    effective_parallel = parallel_tool_calls and parallel_enabled

    return effective_temperature, effective_parallel


def _get_provider_definition(provider_id: str):
    """Return provider definition or raise a strict validation error."""
    from src.lib.config.providers_loader import get_provider

    provider = get_provider(provider_id)
    if provider is None:
        raise ValueError(f"Unknown provider_id: {provider_id}")
    return provider


def _resolve_provider_from_override(provider_override: Optional[str]) -> str:
    """Resolve and validate provider override or return default runner provider."""
    if provider_override:
        provider_id = _normalize_provider_id(provider_override)
        if not provider_id:
            raise ValueError("provider_override must be non-empty when provided")
        _get_provider_definition(provider_id)
        return provider_id
    return get_llm_provider()


def _get_model_definition(model_name: str):
    """Return model definition from catalog or fail fast."""
    from src.lib.config.models_loader import get_model

    model_id = str(model_name or "").strip()
    if not model_id:
        raise ValueError("model_name is required")
    model_def = get_model(model_id)
    if model_def is None:
        raise ValueError(f"Unknown model_id: {model_id}")
    return model_def


def is_gemini_provider(provider: Optional[str] = None) -> bool:
    """Check if the provider resolves to Gemini."""
    return _resolve_provider_from_override(provider) == "gemini"


def is_groq_provider(provider: Optional[str] = None) -> bool:
    """Check if the provider resolves to Groq."""
    return _resolve_provider_from_override(provider) == "groq"


def resolve_model_provider(model_name: str, provider_override: Optional[str] = None) -> str:
    """Resolve provider for model with strict catalog validation."""
    if provider_override:
        return _resolve_provider_from_override(provider_override)

    model_def = _get_model_definition(model_name)
    provider_id = _normalize_provider_id(model_def.provider)
    if not provider_id:
        raise ValueError(f"Model '{model_name}' has empty provider in models.yaml")
    _get_provider_definition(provider_id)
    return provider_id


def get_api_key(provider_override: Optional[str] = None) -> Optional[str]:
    """Get API key for a specific provider (or default runner provider)."""
    provider_id = _resolve_provider_from_override(provider_override)
    provider = _get_provider_definition(provider_id)
    return os.getenv(provider.api_key_env)


def get_base_url(provider_override: Optional[str] = None) -> Optional[str]:
    """Get base URL for a specific provider (or default runner provider)."""
    provider_id = _resolve_provider_from_override(provider_override)
    provider = _get_provider_definition(provider_id)
    if provider.base_url_env:
        env_value = os.getenv(provider.base_url_env)
        if env_value:
            return env_value
    return provider.default_base_url or None


def get_model_for_agent(
    model_name: str,
    provider_override: Optional[str] = None,
) -> Union[str, "LitellmModel"]:
    """Get the appropriate model object for an agent.

    For OpenAI provider: returns model name string (SDK handles it directly)
    For Gemini provider: returns LitellmModel instance (handles thought_signature)

    Gemini 3 requires thought_signature handling for function calling, which
    LiteLLM handles automatically. This function abstracts that complexity.

    Args:
        model_name: The model name (e.g., "gpt-5.6-terra", "gemini-3-pro-preview")

    Returns:
        Model name string for OpenAI, or LitellmModel instance for Gemini
    """
    provider_id = resolve_model_provider(model_name, provider_override)
    provider = _get_provider_definition(provider_id)
    api_key = get_api_key(provider.provider_id)
    if not str(api_key or "").strip():
        raise ValueError(f"{provider.api_key_env} environment variable not set")

    if provider.driver == "openai_native":
        return model_name

    if provider.driver == "litellm":
        from agents.extensions.models.litellm_model import LitellmModel
        import litellm

        litellm.drop_params = bool(provider.drop_params)

        litellm_model_name = model_name
        prefix = str(provider.litellm_prefix or "").strip()
        if prefix and not model_name.startswith(f"{prefix}/"):
            litellm_model_name = f"{prefix}/{model_name}"

        base_url = get_base_url(provider.provider_id)
        logger.info(
            "[LiteLLM] Creating model for %s: %s (drop_params=%s)",
            provider.provider_id,
            litellm_model_name,
            provider.drop_params,
        )
        return LitellmModel(
            model=litellm_model_name,
            base_url=base_url,
            api_key=api_key,
        )

    raise ValueError(
        f"Provider '{provider.provider_id}' has unsupported driver '{provider.driver}'"
    )


def is_gemini_model(model: str) -> bool:
    """Check if a model name is a Gemini model."""
    return model.startswith("gemini-")


def is_gpt5_model(model: str) -> bool:
    """Check if a model supports GPT-5 style reasoning."""
    return model.startswith("gpt-5")


def supports_reasoning(model: str) -> bool:
    """Check if a model supports reasoning/thinking mode.

    All supported models use reasoning:
    - GPT-5.6 Sol/Terra - OpenAI reasoning
    - Gemini 3 Pro Preview (gemini-3-pro-preview) - "low"/"high" thinking levels

    For Gemini 3 models, the OpenAI SDK's reasoning_effort parameter maps to:
    - minimal/low -> "low" thinking level
    - medium/high/xhigh -> "high" thinking level

    Future: Anthropic Claude models may be added here.
    """
    model_def = _get_model_definition(model)
    return bool(model_def.supports_reasoning)


def supports_temperature(model: str) -> bool:
    """Check if a model supports temperature parameter.

    GPT-5 models don't support temperature when reasoning is enabled.
    Gemini 3 models and most other models support temperature.
    """
    model_def = _get_model_definition(model)
    return bool(model_def.supports_temperature)


# Type alias for reasoning effort levels
ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]

# Reasoning-effort values the model layer accepts. Agent and flow-builder configs
# can carry values the model's Reasoning schema rejects (notably "disabled"/"none"
# emitted by the AI flow builder). Treat anything else as "no reasoning" and
# normalize to None rather than letting it crash Reasoning(effort=...) downstream
# (e.g. in flow terminal formatter agents). KANBAN-1346 / 0.7.2.
_VALID_REASONING_EFFORTS: frozenset = frozenset(
    {"minimal", "low", "medium", "high", "xhigh"}
)


def normalize_reasoning_effort(value: object) -> Optional[ReasoningEffort]:
    """Return a valid ReasoningEffort, or None when unset/invalid.

    Any value not in the accepted set (including "disabled", "none", "off", or an
    unknown string) means "no reasoning" and yields None, so a misconfigured
    agent/flow can never crash model-settings construction.
    """
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in _VALID_REASONING_EFFORTS:
        return normalized  # type: ignore[return-value]
    logger.debug("Dropping invalid reasoning effort %r (treated as no reasoning)", value)
    return None


def require_model_reasoning_effort(
    model: str,
    value: object,
) -> ReasoningEffort:
    """Validate a required reasoning effort against one catalog model."""

    model_def = _get_model_definition(model)
    normalized = str(value or "").strip().lower()
    allowed = tuple(model_def.reasoning_options)
    if (
        not model_def.supports_reasoning
        or normalized not in _VALID_REASONING_EFFORTS
        or normalized not in allowed
    ):
        allowed_text = ", ".join(allowed) if allowed else "none"
        raise ValueError(
            f"Reasoning effort '{normalized or value}' is not supported by model "
            f"'{model_def.model_id}'; allowed values: {allowed_text}"
        )
    return normalized  # type: ignore[return-value]


ReasoningSummaryStatus = Literal["present", "not_requested", "not_supported", "unavailable"]


def reasoning_summary_request_settings(
    *,
    model: str,
    reasoning_effort: Optional[ReasoningEffort],
    provider_override: Optional[str] = None,
) -> dict[str, Optional[str]]:
    """Return the reasoning-summary request contract for a model run."""

    provider = resolve_model_provider(model, provider_override)
    provider_def = _get_provider_definition(provider)
    model_supports_reasoning = supports_reasoning(model)

    if not model_supports_reasoning or provider_def.driver != "openai_native":
        return {
            "availability": "not_supported",
            "reasoning_effort": reasoning_effort,
            "requested_summary": None,
            "provider": provider,
            "model": model,
        }

    if not reasoning_effort:
        return {
            "availability": "not_requested",
            "reasoning_effort": None,
            "requested_summary": None,
            "provider": provider,
            "model": model,
        }

    return {
        "availability": "present",
        "reasoning_effort": reasoning_effort,
        "requested_summary": "auto",
        "provider": provider,
        "model": model,
    }


def build_default_model_retry():
    """Opt-in runner-managed retry for transient model-call failures.

    The Agents SDK retries model calls when ``ModelSettings.retry`` is set,
    classifying HTTP 5xx (e.g. a Responses WebSocket handshake ``503``),
    never-sent WebSocket errors, and typed pre-response overload frames as safe
    to retry, with exponential backoff and ``Retry-After`` support. Disable by
    setting ``OPENAI_MODEL_MAX_RETRIES=0``.
    """
    # Import here to avoid circular dependency at module load.
    from agents import retry_policies
    from agents.retry import ModelRetrySettings, ModelRetryBackoffSettings

    def retry_responses_websocket_overload(context):
        """Retry a typed pre-response overload frame without logging frame content."""
        from agents.models.openai_responses import ResponsesWebSocketError
        from agents.retry import RetryDecision

        error = context.error
        if not (
            isinstance(error, ResponsesWebSocketError)
            and error.event_type == "error"
            and error.error_type == "service_unavailable_error"
            and error.code == "server_is_overloaded"
        ):
            return False

        retry_budget_exhausted = context.attempt == context.max_retries
        log = logger.warning if retry_budget_exhausted else logger.info
        log(
            "Retrying transient Responses WebSocket overload frame "
            "(retry %s/%s; retry_budget_exhausted=%s)",
            context.attempt,
            context.max_retries,
            retry_budget_exhausted,
        )

        decision = RetryDecision(
            retry=True,
            reason="responses_websocket_server_overloaded",
        )
        # The typed `error` frame is raised by the pinned SDK before it yields a
        # model event. Marking this decision replay-safe permits stateful model
        # calls to use the shared runner loop; the runner still vetoes retries
        # after any response/tool-bearing stream event.
        setattr(decision, "_approves_replay", True)  # noqa: B010
        return decision

    try:
        max_retries = int(os.getenv("OPENAI_MODEL_MAX_RETRIES", "3"))
    except ValueError:
        max_retries = 3
    if max_retries <= 0:
        return None
    return ModelRetrySettings(
        max_retries=max_retries,
        backoff=ModelRetryBackoffSettings(
            initial_delay=_get_env_float("OPENAI_MODEL_RETRY_INITIAL_DELAY", 0.5),
            max_delay=_get_env_float("OPENAI_MODEL_RETRY_MAX_DELAY", 8.0),
            multiplier=_get_env_float("OPENAI_MODEL_RETRY_MULTIPLIER", 2.0),
            jitter=True,
        ),
        policy=retry_policies.any(
            retry_policies.provider_suggested(),
            retry_responses_websocket_overload,
            retry_policies.retry_after(),
            retry_policies.network_error(),
            retry_policies.http_status([408, 409, 429, 500, 502, 503, 504]),
        ),
    )


def build_model_settings(
    model: str,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[ReasoningEffort] = None,
    tool_choice: Optional[str] = None,
    parallel_tool_calls: bool = True,
    verbosity: Optional[str] = None,
    include_usage: Optional[bool] = None,
    provider_override: Optional[str] = None,
):
    """
    Build ModelSettings with appropriate reasoning and temperature for the model.

    This is a shared helper function for all agents to ensure consistent
    behavior across OpenAI and Gemini models (and potentially Anthropic in future).

    Reasoning is supported on:
    - GPT-5.6 Sol/Terra models
    - Gemini 3 Pro Preview (gemini-3-pro-preview) - uses "low"/"high" thinking levels

    For Gemini 3, the OpenAI SDK's reasoning_effort parameter maps to:
    - minimal/low -> "low" thinking level
    - medium/high/xhigh -> "high" thinking level

    Args:
        model: The model name (e.g., "gpt-5", "gemini-3-pro-preview")
        temperature: Optional temperature override (0.0-1.0)
        reasoning_effort: Optional reasoning effort for models that support it
        tool_choice: Optional tool choice mode ("auto", "required", etc.)
        parallel_tool_calls: Whether to allow parallel tool calls (ignored for Gemini)
        verbosity: Optional verbosity level ("low", etc.) - fixes structured output + reasoning
        include_usage: Whether to request usage accounting from the provider when supported

    Returns:
        ModelSettings instance
    """
    # Import here to avoid circular dependency
    from agents import ModelSettings
    from openai.types.shared import Reasoning

    # Build reasoning config for models that support it. Normalize first so an
    # invalid value (e.g. "disabled" from the flow builder) becomes "no reasoning"
    # instead of crashing Reasoning(effort=...) construction.
    reasoning = None
    reasoning_effort = normalize_reasoning_effort(reasoning_effort)
    if reasoning_effort and supports_reasoning(model):
        summary_settings = reasoning_summary_request_settings(
            model=model,
            reasoning_effort=reasoning_effort,
            provider_override=provider_override,
        )
        summary = summary_settings.get("requested_summary")
        reasoning_kwargs = {"effort": reasoning_effort}
        if summary:
            reasoning_kwargs["summary"] = summary
        reasoning = Reasoning(**reasoning_kwargs)

    # GPT-5 models don't support temperature parameter, others do
    effective_temperature = temperature if supports_temperature(model) else None

    # Verbosity is needed for GPT-5 + reasoning to fix structured output issues
    # See: https://github.com/langchain-ai/langchain/issues/32492
    effective_verbosity = verbosity if reasoning else None

    # Provider-level capability gates (configured in providers.yaml)
    provider = resolve_model_provider(model, provider_override)
    provider_def = _get_provider_definition(provider)
    effective_parallel_tool_calls = (
        parallel_tool_calls if provider_def.supports_parallel_tool_calls else False
    )

    effective_temperature, effective_parallel_tool_calls = _apply_provider_tool_call_overrides(
        provider=provider,
        temperature=effective_temperature,
        parallel_tool_calls=effective_parallel_tool_calls,
    )

    return ModelSettings(
        temperature=effective_temperature,
        reasoning=reasoning,
        tool_choice=tool_choice,
        parallel_tool_calls=effective_parallel_tool_calls,
        verbosity=effective_verbosity,
        include_usage=include_usage,
        retry=build_default_model_retry(),
    )


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    model: str
    temperature: Optional[float]
    reasoning: Optional[ReasoningEffort]
    tool_choice: Optional[str]


def _get_env(key: str, default: str) -> str:
    """Get environment variable with default."""
    return os.getenv(key, default)


def _get_env_float(key: str, default: Optional[float]) -> Optional[float]:
    """Get environment variable as float."""
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        logger.warning('Invalid float value for %s: %s, using default %s', key, val, default)
        return default


def _get_env_reasoning(key: str, default: Optional[ReasoningEffort]) -> Optional[ReasoningEffort]:
    """Get environment variable as reasoning effort."""
    val = os.getenv(key)
    if val is None:
        return default
    if val in ("minimal", "low", "medium", "high", "xhigh"):
        return val  # type: ignore
    logger.warning('Invalid reasoning value for %s: %s, using default %s', key, val, default)
    return default


# =============================================================================
# Default values (read from .env, with minimal hardcoded fallbacks)
# =============================================================================

def get_default_model() -> str:
    """Get the default agent model from DEFAULT_AGENT_MODEL (.env).

    Fail-fast: .env is the single source of truth. There is no catalog or code
    fallback -- a missing/unset value raises so the misconfiguration is fixed
    rather than silently running on the wrong model.
    """
    from src.lib.config.env import require_env

    model_id = require_env(
        "DEFAULT_AGENT_MODEL",
        hint="gpt-4o is retired; use a registered GPT-5.6 model such as gpt-5.6-terra or gpt-5.6-sol.",
    )
    _get_model_definition(model_id)  # validate the model is registered in models.yaml
    return model_id


def get_default_temperature() -> Optional[float]:
    """Get the optional default temperature from DEFAULT_AGENT_TEMPERATURE (.env).

    Temperature is optional and has no code default: GPT-5 ignores it, and agents
    that need it (e.g. Gemini) declare it in their package agent.yaml. Returns
    None when unset.
    """
    from src.lib.config.env import optional_env_float

    return optional_env_float("DEFAULT_AGENT_TEMPERATURE")


def get_default_reasoning() -> ReasoningEffort:
    """Get the default reasoning effort from DEFAULT_AGENT_REASONING (.env).

    Fail-fast: .env is the source of truth; there is no code default.
    """
    from src.lib.config.env import require_env_choice

    return require_env_choice(  # type: ignore[return-value]
        "DEFAULT_AGENT_REASONING", ("minimal", "low", "medium", "high", "xhigh")
    )


# NOTE: get_supervisor_reasoning() was removed as part of the registry simplification.
# Supervisor reasoning is now configured via:
#   1. AGENT_SUPERVISOR_REASONING env var (takes priority)
#   2. AGENT_REGISTRY["supervisor"]["config_defaults"]["reasoning"] (default: "medium")
# The old DEFAULT_SUPERVISOR_REASONING env var is deprecated.


# =============================================================================
# Generic Agent Configuration Function
# =============================================================================

def get_agent_config(agent_id: str) -> AgentConfig:
    """
    Get configuration for any agent with env var override support.

    Priority (highest to lowest):
    1. Environment variable: AGENT_{AGENT_ID}_SETTING
    2. Registry config_defaults
    3. Global fallback defaults

    Args:
        agent_id: The agent ID (e.g., "gene_validation", "pdf_extraction")

    Returns:
        AgentConfig instance with resolved settings
    """
    # Import registry here to avoid circular imports
    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
        registry_entry = AGENT_REGISTRY.get(agent_id, {})
        defaults = registry_entry.get("config_defaults", {})
    except ImportError:
        defaults = {}

    # Environment variable prefix (uppercase agent_id)
    prefix = f"AGENT_{agent_id.upper()}_"

    # Resolve each setting with priority: env > registry > fallback. Keep the
    # global defaults lazy so stale unused defaults do not break explicit agent
    # overrides or registry-owned defaults.
    model_default = defaults["model"] if "model" in defaults else get_default_model()
    model = _get_env(f"{prefix}MODEL", model_default)

    # Temperature can be None for models that don't support it
    temperature_str = os.getenv(f"{prefix}TEMPERATURE")
    if temperature_str is not None:
        try:
            temperature = float(temperature_str)
        except ValueError:
            temperature = (
                defaults["temperature"]
                if "temperature" in defaults
                else get_default_temperature()
            )
    else:
        temperature = (
            defaults["temperature"]
            if "temperature" in defaults
            else get_default_temperature()
        )

    reasoning_default = (
        defaults["reasoning"] if "reasoning" in defaults else get_default_reasoning()
    )
    reasoning = _get_env_reasoning(
        f"{prefix}REASONING",
        reasoning_default,
    )

    tool_choice = _get_env(
        f"{prefix}TOOL_CHOICE",
        defaults.get("tool_choice", "auto")
    )

    return AgentConfig(
        model=model,
        temperature=temperature,
        reasoning=reasoning,
        tool_choice=tool_choice,
    )


# =============================================================================
# Runner Configuration
# =============================================================================

def get_max_turns() -> int:
    """Get maximum turns for agent runs."""
    val = os.getenv("AGENT_MAX_TURNS", "60")
    try:
        return int(val)
    except ValueError:
        logger.warning('Invalid AGENT_MAX_TURNS value: %s, using default 60', val)
        return 60


def get_supervisor_max_specialist_calls_per_turn() -> int:
    """Total distinct specialist invocations the chat supervisor may run per turn.

    Backstop for runaway/no-progress loops in standard chat (the per-call dedup
    is the primary brake). Set high so legitimate multi-lookup chats are never
    truncated. Tunable via SUPERVISOR_MAX_SPECIALIST_CALLS_PER_TURN.
    """
    limit = _get_env_int_with_fallback("SUPERVISOR_MAX_SPECIALIST_CALLS_PER_TURN", 25)
    return max(1, limit)


def get_supervisor_max_calls_per_specialist() -> int:
    """Distinct invocations of any single specialist the chat supervisor may run per turn.

    Backstop that catches a storm against one specialist (e.g. repeated allele
    lookups) without serializing legitimate different-query work. Tunable via
    SUPERVISOR_MAX_CALLS_PER_SPECIALIST.
    """
    limit = _get_env_int_with_fallback("SUPERVISOR_MAX_CALLS_PER_SPECIALIST", 8)
    return max(1, limit)


def get_supervisor_specialist_deadline_seconds() -> float:
    """Wall-clock deadline for one Automatic-mode supervisor specialist run.

    The deadline bounds the complete user-facing nested specialist invocation. At
    expiry it starts the specialist/provider cancellation and close lifecycle but
    does not wait beyond the same absolute bound for blocked cleanup. Preferred
    Agent/Flow runs do not use the chat supervisor ledger and are outside this bound.
    """

    return max(
        0.1,
        _get_env_float_with_fallback(
            "SUPERVISOR_SPECIALIST_DEADLINE_SECONDS",
            900.0,
        ),
    )


def get_flow_supervisor_parallel_tool_calls_enabled() -> bool:
    """Whether flow supervisors may request parallel tool calls from the provider."""
    return _get_env_bool("FLOW_SUPERVISOR_PARALLEL_TOOL_CALLS_ENABLED", False)


# =============================================================================
# Operational limits (turns, batches, caps, timeouts)
#
# These getters surface previously-hardcoded operational constants so they are
# discoverable and tunable from .env. Each keeps the historical default as its
# fallback, so behavior is unchanged unless the env var is set. See .env.example
# for the documented rationale and consequences of each.
# =============================================================================

# --- Observability ---


def get_sentry_log_event_level() -> int | None:
    """Minimum Python log level promoted to a Sentry event.

    A blank value keeps promotion disabled. Restricting the accepted values
    prevents accidental high-volume INFO or DEBUG event ingestion.
    """

    raw = os.getenv("SENTRY_LOG_EVENT_LEVEL", "").strip()
    if not raw:
        return None

    levels = {
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    level = levels.get(raw.upper())
    if level is None:
        logger.warning(
            "Invalid log event level for SENTRY_LOG_EVENT_LEVEL: %s; expected "
            "blank or one of WARNING, ERROR, CRITICAL. Log-event promotion "
            "remains disabled.",
            raw,
        )
    return level


# --- Document sources ---


def get_go_annotations_request_timeout_seconds() -> float:
    """HTTP timeout for typed existing-GO annotation lookups."""
    return max(
        0.1,
        _get_env_float_with_fallback("GO_ANNOTATIONS_REQUEST_TIMEOUT_SECONDS", 30.0),
    )


def get_rna_gene_product_request_timeout_seconds() -> float:
    """HTTP timeout for RNAcentral and miRBase identity lookups."""
    return max(
        0.1,
        _get_env_float_with_fallback("RNA_GENE_PRODUCT_REQUEST_TIMEOUT_SECONDS", 10.0),
    )


def get_rna_gene_product_cache_ttl_seconds() -> float:
    """Lifetime of process-local RNA gene-product resolution cache entries."""
    return max(
        0.0,
        _get_env_float_with_fallback("RNA_GENE_PRODUCT_CACHE_TTL_SECONDS", 300.0),
    )


def get_rna_gene_product_cache_max_entries() -> int:
    """Maximum process-local RNA gene-product resolution cache entries."""
    return max(
        1,
        _get_env_int_with_fallback("RNA_GENE_PRODUCT_CACHE_MAX_ENTRIES", 256),
    )


def get_rna_gene_product_max_candidates() -> int:
    """Maximum candidates fetched and returned by one gene-product resolution."""
    return max(
        1,
        _get_env_int_with_fallback("RNA_GENE_PRODUCT_MAX_CANDIDATES", 25),
    )


def get_document_source_import_enabled() -> bool:
    """Feature flag for external document-source import."""
    return _get_env_bool("DOCUMENT_SOURCE_IMPORT_ENABLED", False)


def get_document_source_provider() -> str:
    """Primary document-source provider identifier (DOCUMENT_SOURCE_PROVIDER)."""
    return os.getenv("DOCUMENT_SOURCE_PROVIDER", "local_pdf").strip() or "local_pdf"


def get_document_source_request_timeout_seconds() -> float:
    """HTTP timeout for document-source provider calls."""
    return max(
        0.1,
        _get_env_float_with_fallback("DOCUMENT_SOURCE_REQUEST_TIMEOUT_SECONDS", 10.0),
    )


def get_document_source_import_batch_limit() -> int:
    """Maximum documents accepted by one source import request."""
    return max(1, _get_env_int_with_fallback("DOCUMENT_SOURCE_IMPORT_BATCH_LIMIT", 10))


def get_document_source_poll_interval_seconds() -> float:
    """Status-poll interval for provider-backed document imports."""
    return max(
        0.1,
        _get_env_float_with_fallback("DOCUMENT_SOURCE_POLL_INTERVAL_SECONDS", 2.0),
    )


def get_document_source_import_timeout_seconds() -> float:
    """Wall-clock timeout for one provider-backed import job."""
    return max(
        1.0,
        _get_env_float_with_fallback("DOCUMENT_SOURCE_IMPORT_TIMEOUT_SECONDS", 300.0),
    )


# --- Document retrieval ---


def get_weaviate_search_initial_limit() -> int:
    """Candidate pool retrieved before reranking (default 50, provider max 100)."""

    return _get_bounded_positive_env_int(
        "WEAVIATE_SEARCH_INITIAL_LIMIT",
        50,
        100,
    )


def _get_unit_interval_env_float(key: str, default: float) -> float:
    """Read a finite float in [0, 1], falling back on invalid values."""

    value = _get_env_float_with_fallback(key, default)
    if value != value or not 0.0 <= value <= 1.0:
        logger.warning("%s=%s is outside [0, 1]; using default %s", key, value, default)
        return default
    return value


def get_weaviate_search_hybrid_alpha() -> float:
    """Default semantic weight for hybrid document retrieval."""

    return _get_unit_interval_env_float("WEAVIATE_SEARCH_HYBRID_ALPHA", 0.4)


def get_weaviate_search_mmr_enabled() -> bool:
    """Whether document retrieval applies MMR after reranking."""

    return _get_env_bool("WEAVIATE_SEARCH_MMR_ENABLED", False)


def get_weaviate_search_mmr_lambda() -> float:
    """MMR relevance/diversity weight used only when MMR is enabled."""

    return _get_unit_interval_env_float("WEAVIATE_SEARCH_MMR_LAMBDA", 0.5)


# --- Agent / turn limits ---


def _get_single_shot_output_agent_max_turns(key: str) -> int:
    """Read a one-shot structured-output agent turn budget with the SDK-default bound."""
    return max(1, _get_env_int_with_fallback(key, 10))


def get_guardrail_single_shot_max_turns() -> int:
    """Turn budget for single-shot guardrail structured-output agents (GUARDRAIL_SINGLE_SHOT_MAX_TURNS).

    These agents have no tools and only emit structured safety/topic decisions.
    Default 10 matches the Agents SDK default they previously inherited while
    making the bound explicit at every Runner call site.
    """
    return _get_single_shot_output_agent_max_turns("GUARDRAIL_SINGLE_SHOT_MAX_TURNS")


def get_hierarchy_resolution_max_turns() -> int:
    """Turn budget for the one-shot document hierarchy classifier (HIERARCHY_RESOLUTION_MAX_TURNS).

    The hierarchy agent has no tools and returns structured section metadata.
    Default 10 matches the Agents SDK default it previously inherited while
    making the bound explicit at the Runner call site.
    """
    return _get_single_shot_output_agent_max_turns("HIERARCHY_RESOLUTION_MAX_TURNS")


def get_figure_locator_resolution_max_turns() -> int:
    """Turn budget for the one-shot figure locator classifier.

    FIGURE_LOCATOR_RESOLUTION_MAX_TURNS defaults to 10, matching the Agents
    SDK default previously inherited by other structured-output classifiers.
    """
    return _get_single_shot_output_agent_max_turns(
        "FIGURE_LOCATOR_RESOLUTION_MAX_TURNS"
    )


def get_figure_locator_resolution_batch_max_chars() -> int:
    """Maximum prompt chars in one figure-locator classifier batch.

    FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS defaults to 60000. Lower it for
    models with smaller input windows or to reduce the failure scope of one
    provider call; raise it only after confirming the configured model limit.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "FIGURE_LOCATOR_RESOLUTION_BATCH_MAX_CHARS",
            60_000,
        ),
    )


def get_standard_chat_context_token_budget() -> int:
    """Model-live context budget for standard assistant chat (STANDARD_CHAT_CONTEXT_TOKEN_BUDGET).

    This is the budget the standard-chat compaction trigger compares against.
    Default 400000 matches the current GPT-5 family context target used by the
    supervisor; tune lower to compact sooner or higher when moving to a larger
    context model.
    """
    return max(1, _get_env_int_with_fallback("STANDARD_CHAT_CONTEXT_TOKEN_BUDGET", 400_000))


def get_standard_chat_compaction_threshold_percent() -> int:
    """Percent of context budget that triggers standard-chat compaction.

    STANDARD_CHAT_COMPACTION_THRESHOLD_PERCENT defaults to 70 so compaction runs
    before the model-live transcript approaches the full context window.
    """
    return min(
        100,
        max(1, _get_env_int_with_fallback("STANDARD_CHAT_COMPACTION_THRESHOLD_PERCENT", 70)),
    )


def get_standard_chat_compaction_token_threshold() -> int:
    """Estimated token threshold for standard-chat compaction.

    Derived from STANDARD_CHAT_CONTEXT_TOKEN_BUDGET and
    STANDARD_CHAT_COMPACTION_THRESHOLD_PERCENT so the threshold stays tied to
    the configured context budget.
    """
    budget = get_standard_chat_context_token_budget()
    percent = get_standard_chat_compaction_threshold_percent()
    return max(1, (budget * percent) // 100)


# --- Validator dispatch ---

def get_max_parallel_validators() -> int:
    """Max validator jobs the dispatcher runs concurrently (MAX_PARALLEL_VALIDATORS).

    Caps the thread/async fan-out for parallel validator binding execution.
    Higher = more concurrent LLM/DB load; lower = slower but gentler on the
    provider and database. Default 8 (raised from 4 to speed up condition-heavy
    validation by running more batch chunks at once).
    """
    return max(1, _get_env_int_with_fallback("MAX_PARALLEL_VALIDATORS", 8))


def get_validator_batch_max_size() -> int:
    """Default cap on deduped validator jobs carried in one batch run (VALIDATOR_BATCH_MAX_SIZE).

    Applies when a binding does not configure ``batch_max_size``. Kept small
    because the per-run ``max_turns`` budget scales with the number of jobs in
    the batch; a large cap risks "Max turns exceeded" and unbounded cost.
    Default 8.
    """
    return max(1, _get_env_int_with_fallback("VALIDATOR_BATCH_MAX_SIZE", 8))


def get_validator_max_tool_calls() -> int:
    """Per-job tool-call budget for a binding without ``max_tool_calls`` (VALIDATOR_MAX_TOOL_CALLS).

    The batch ``max_turns`` is derived as ``len(jobs) * this`` so a multi-lookup
    composite validator is never starved mid-batch. Default 8.
    """
    return max(1, _get_env_int_with_fallback("VALIDATOR_MAX_TOOL_CALLS", 8))


# --- Structured finalization ---

def get_structured_finalization_max_attempts() -> int:
    """Default attempts for the structured-finalization loop (STRUCTURED_FINALIZATION_MAX_ATTEMPTS).

    Applies when a finalization config does not set ``max_attempts``. More
    attempts = more chances for a specialist to emit valid structured output at
    the cost of extra model calls. Default 6.
    """
    return max(1, _get_env_int_with_fallback("STRUCTURED_FINALIZATION_MAX_ATTEMPTS", 6))


def get_structured_finalization_hard_max_attempts() -> int:
    """Absolute ceiling on structured-finalization attempts (STRUCTURED_FINALIZATION_HARD_MAX_ATTEMPTS).

    Configured ``max_attempts`` values are clamped to this hard cap so a
    misconfiguration cannot drive unbounded retries. Default 20.
    """
    return max(1, _get_env_int_with_fallback("STRUCTURED_FINALIZATION_HARD_MAX_ATTEMPTS", 20))


def get_structured_finalization_retry_max_turns() -> int:
    """Turn budget for the simplified output-synthesis retry agent (STRUCTURED_FINALIZATION_RETRY_MAX_TURNS).

    The retry agent runs without tools/guardrails and just needs to synthesize
    output, so its turn budget is deliberately tight. Default 5.
    """
    return max(1, _get_env_int_with_fallback("STRUCTURED_FINALIZATION_RETRY_MAX_TURNS", 5))


def get_batching_nudge_threshold() -> int:
    """Consecutive same-specialist calls before the batching nudge fires (BATCHING_NUDGE_THRESHOLD).

    Lower = nudge the supervisor toward batching sooner; higher = tolerate more
    repeated single calls before reminding it. Default 3.
    """
    return max(1, _get_env_int_with_fallback("BATCHING_NUDGE_THRESHOLD", 3))


def get_layer2_force_tool_finalization_enabled() -> bool:
    """Kill-switch for Layer-2 tool-forced structured finalization (LAYER2_FORCE_TOOL_FINALIZATION_ENABLED).

    When True (default), structured finalization uses tool_choice=required +
    ToolsToFinalOutputFunction. Set to false to fall back to the prior Layer-1
    structured-finalization loop instantly without a redeploy. Default True.
    """
    return _get_env_bool("LAYER2_FORCE_TOOL_FINALIZATION_ENABLED", True)


# --- Tool list / page / section limits ---

def get_tool_page_default_limit() -> int:
    """Default page size for bounded-list tool pagination (TOOL_PAGE_DEFAULT_LIMIT).

    The page size used when a tool call omits an explicit limit. Default 20.
    """
    return max(1, _get_env_int_with_fallback("TOOL_PAGE_DEFAULT_LIMIT", 20))


def get_tool_page_max_limit() -> int:
    """Hard ceiling on bounded-list tool page size (TOOL_PAGE_MAX_LIMIT).

    Caps how large a single page a tool may return regardless of the requested
    limit, bounding payload size. Default 50.
    """
    return max(1, _get_env_int_with_fallback("TOOL_PAGE_MAX_LIMIT", 50))


def get_supervisor_manifest_page_size() -> int:
    """Default object page size for extraction-result supervisor manifests.

    Controls how many YAML-declared manifest rows are handed to the supervisor
    or returned by inspect_results objects views when no explicit limit is
    supplied. Default 100.
    """
    return max(1, _get_env_int_with_fallback("SUPERVISOR_MANIFEST_PAGE_SIZE", 100))


def get_inspect_results_evidence_page_size() -> int:
    """Default evidence page size for inspect_results evidence views.

    Bounds how many evidence records the supervisor can fetch in one
    inspect_results(action="evidence") call when no explicit limit is supplied.
    Default 20.
    """
    return max(1, _get_env_int_with_fallback("INSPECT_RESULTS_EVIDENCE_PAGE_SIZE", 20))


def get_inspect_results_list_page_size() -> int:
    """Default result-list page size for inspect_results list views.

    Bounds how many result summaries inspect_results(action="list") returns
    when no explicit limit is supplied. Default 5.
    """
    return max(1, _get_env_int_with_fallback("INSPECT_RESULTS_LIST_PAGE_SIZE", 5))


def get_inspect_results_validation_page_size() -> int:
    """Default validation finding page size for inspect_results validation views.

    Bounds how many validation findings inspect_results(action="validation")
    returns when no explicit limit is supplied. Default 5.
    """
    return max(1, _get_env_int_with_fallback("INSPECT_RESULTS_VALIDATION_PAGE_SIZE", 5))


def get_list_recorded_evidence_limit() -> int:
    """Default cap on records returned by list_recorded_evidence (LIST_RECORDED_EVIDENCE_LIMIT).

    Bounds how many evidence records the workspace listing tool returns when no
    explicit limit is given. Default 100.
    """
    return max(1, _get_env_int_with_fallback("LIST_RECORDED_EVIDENCE_LIMIT", 100))


# --- Display / truncation ---

def get_supervisor_text_preview_limit() -> int:
    """Char limit for short text previews in supervisor context tools (SUPERVISOR_TEXT_PREVIEW_LIMIT).

    Truncation budget for inline text previews surfaced to the supervisor.
    Default 220.
    """
    return max(1, _get_env_int_with_fallback("SUPERVISOR_TEXT_PREVIEW_LIMIT", 220))


def get_supervisor_field_text_limit() -> int:
    """Char limit for bounded field text in supervisor context tools (SUPERVISOR_FIELD_TEXT_LIMIT).

    Per-field truncation budget when compacting JSON for the supervisor.
    Default 500.
    """
    return max(1, _get_env_int_with_fallback("SUPERVISOR_FIELD_TEXT_LIMIT", 500))


def get_inspect_results_evidence_text_limit() -> int:
    """Char limit for one inspect_results evidence text field.

    Truncates quote/evidence text returned only by
    inspect_results(action="evidence"). Default 500.
    """
    return max(1, _get_env_int_with_fallback("INSPECT_RESULTS_EVIDENCE_TEXT_LIMIT", 500))


def get_inspect_results_validation_detail_list_limit() -> int:
    """Max list items returned inside one inspect_results validation detail value.

    Bounds nested list values inside validation finding details returned to the
    supervisor. Default 5.
    """
    return max(
        1,
        _get_env_int_with_fallback("INSPECT_RESULTS_VALIDATION_DETAIL_LIST_LIMIT", 5),
    )


def get_inspect_results_json_depth_limit() -> int:
    """Max nested JSON depth returned by inspect_results detail views.

    Bounds recursive JSON compaction for validation/evidence detail payloads.
    Default 6.
    """
    return max(1, _get_env_int_with_fallback("INSPECT_RESULTS_JSON_DEPTH_LIMIT", 6))


def get_inspect_results_json_object_item_limit() -> int:
    """Max mapping keys returned by inspect_results compact JSON views.

    Bounds object/mapping entries inside nested JSON returned to the supervisor.
    Default 25.
    """
    return max(
        1,
        _get_env_int_with_fallback("INSPECT_RESULTS_JSON_OBJECT_ITEM_LIMIT", 25),
    )


def get_supervisor_max_list_limit() -> int:
    """Max list page size for supervisor context list tools (SUPERVISOR_MAX_LIST_LIMIT).

    Upper bound applied to list-style supervisor context tools. Default 20.
    """
    return max(1, _get_env_int_with_fallback("SUPERVISOR_MAX_LIST_LIMIT", 20))


def get_supervisor_recall_chat_history_default_limit() -> int:
    """Default page size for recall_chat_history (SUPERVISOR_RECALL_CHAT_HISTORY_DEFAULT_LIMIT).

    Bounds how many exact transcript messages recall_chat_history returns when
    no explicit limit is supplied. Default 5.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "SUPERVISOR_RECALL_CHAT_HISTORY_DEFAULT_LIMIT",
            5,
        ),
    )


def get_supervisor_inspect_chat_traces_default_limit() -> int:
    """Default page size for inspect_chat_traces (SUPERVISOR_INSPECT_CHAT_TRACES_DEFAULT_LIMIT).

    Bounds how many trace inventory rows inspect_chat_traces returns when no
    explicit limit is supplied. Default 5.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "SUPERVISOR_INSPECT_CHAT_TRACES_DEFAULT_LIMIT",
            5,
        ),
    )


def get_validation_detail_string_limit() -> int:
    """Char limit per string when materializing validation detail (VALIDATION_DETAIL_STRING_LIMIT).

    Truncates long strings stored in materialized validation findings. Default
    8000.
    """
    return max(1, _get_env_int_with_fallback("VALIDATION_DETAIL_STRING_LIMIT", 8000))


def get_validation_detail_list_limit() -> int:
    """Max list items kept when materializing validation detail (VALIDATION_DETAIL_LIST_LIMIT).

    Caps list length stored in materialized validation findings. Default 25.
    """
    return max(1, _get_env_int_with_fallback("VALIDATION_DETAIL_LIST_LIMIT", 25))


def get_validation_detail_mapping_limit() -> int:
    """Max mapping keys kept when materializing validation detail (VALIDATION_DETAIL_MAPPING_LIMIT).

    Caps dict size stored in materialized validation findings. Default 50.
    """
    return max(1, _get_env_int_with_fallback("VALIDATION_DETAIL_MAPPING_LIMIT", 50))


def get_flow_step_output_preview_chars() -> int:
    """Char limit for tool-output previews in flow steps (FLOW_STEP_OUTPUT_PREVIEW_CHARS).

    Truncation budget for flow-step tool-output previews. Default 800.
    """
    return max(1, _get_env_int_with_fallback("FLOW_STEP_OUTPUT_PREVIEW_CHARS", 800))


def get_flow_step_evidence_preview_limit() -> int:
    """Max evidence records previewed per flow step (FLOW_STEP_EVIDENCE_PREVIEW_LIMIT).

    Bounds how many evidence records a flow step preview includes. Default 10.
    """
    return max(1, _get_env_int_with_fallback("FLOW_STEP_EVIDENCE_PREVIEW_LIMIT", 10))


def get_flow_output_projection_preview_limit() -> int:
    """Rows previewed by flow/formatter projection tools (FLOW_OUTPUT_PROJECTION_PREVIEW_LIMIT).

    Bounds the preview row count visible formatter tools inspect. Default 5.
    """
    return max(
        1,
        _get_env_int_with_fallback("FLOW_OUTPUT_PROJECTION_PREVIEW_LIMIT", 5),
    )


def get_flow_output_branch_descriptor_prefix_chars() -> int:
    """Readable descriptor characters retained before branch identity. Default 55."""

    return max(
        1,
        _get_env_int_with_fallback("FLOW_OUTPUT_BRANCH_DESCRIPTOR_PREFIX_CHARS", 55),
    )


def get_flow_output_branch_node_suffix_chars() -> int:
    """Formatter-node characters retained in branched output filenames. Default 25."""

    return max(
        1,
        _get_env_int_with_fallback("FLOW_OUTPUT_BRANCH_NODE_SUFFIX_CHARS", 25),
    )


def get_flow_output_branch_identity_hash_chars() -> int:
    """SHA-256 characters used to distinguish output branches. Default 12."""

    return min(
        64,
        max(
            1,
            _get_env_int_with_fallback("FLOW_OUTPUT_BRANCH_IDENTITY_HASH_CHARS", 12),
        ),
    )


def get_flow_output_branch_storage_descriptor_chars() -> int:
    """Maximum branched storage descriptor length. Default 100."""

    return max(
        1,
        _get_env_int_with_fallback("FLOW_OUTPUT_BRANCH_STORAGE_DESCRIPTOR_CHARS", 100),
    )


# --- Timeouts ---

def _get_env_optional_nonnegative_float(
    key: str,
    default: Optional[float],
) -> Optional[float]:
    """Parse optional nonnegative float environment values.

    Blank, "none", "null", or values <= 0 disable the setting.
    """
    raw = os.getenv(key)
    if raw is None:
        return default

    value = raw.strip().lower()
    if not value or value in {"0", "none", "null", "off", "disabled"}:
        return None

    try:
        parsed = float(value)
    except ValueError:
        logger.warning("Invalid float value for %s: %s, using default %s", key, raw, default)
        return default
    if parsed <= 0:
        return None
    return parsed


def get_openai_responses_websocket_ping_interval_seconds() -> Optional[float]:
    """WebSocket ping interval for OpenAI Responses transport.

    OPENAI_RESPONSES_WEBSOCKET_PING_INTERVAL_SECONDS controls how often the
    SDK sends keepalive pings. Blank/none/0 disables pinging. Default 20.
    """
    return _get_env_optional_nonnegative_float(
        "OPENAI_RESPONSES_WEBSOCKET_PING_INTERVAL_SECONDS",
        20.0,
    )


def get_openai_responses_websocket_ping_timeout_seconds() -> Optional[float]:
    """WebSocket ping timeout for OpenAI Responses transport.

    OPENAI_RESPONSES_WEBSOCKET_PING_TIMEOUT_SECONDS controls how long the SDK
    waits for a ping response before closing the websocket. Blank/none/0
    disables heartbeat timeouts. Default disabled for long reasoning turns.
    """
    return _get_env_optional_nonnegative_float(
        "OPENAI_RESPONSES_WEBSOCKET_PING_TIMEOUT_SECONDS",
        None,
    )


def get_package_runner_timeout_seconds() -> float:
    """Subprocess timeout for an isolated package tool call (PACKAGE_RUNNER_TIMEOUT_SECONDS).

    Wall-clock budget for one package tool subprocess; on expiry the call fails
    with a timeout error. Higher = tolerate slower tools; lower = fail faster.
    Default 60.
    """
    return max(1.0, _get_env_float_with_fallback("PACKAGE_RUNNER_TIMEOUT_SECONDS", 60.0))


def get_agent_studio_trace_tool_timeout_seconds() -> float:
    """HTTP timeout for heavy Agent Studio trace tool calls (AGENT_STUDIO_TRACE_TOOL_TIMEOUT_SECONDS).

    Wall-clock budget for the langfuse reconstruction/payloads trace tools, which
    fetch large payloads and need longer than the default endpoint timeout.
    Default 45.
    """
    return max(1.0, _get_env_float_with_fallback("AGENT_STUDIO_TRACE_TOOL_TIMEOUT_SECONDS", 45.0))


def get_agent_studio_endpoint_timeout_seconds() -> float:
    """Default HTTP timeout for Agent Studio Claude-endpoint calls (AGENT_STUDIO_ENDPOINT_TIMEOUT_SECONDS).

    Applies to trace tools that do not override it. Default 30.
    """
    return max(1.0, _get_env_float_with_fallback("AGENT_STUDIO_ENDPOINT_TIMEOUT_SECONDS", 30.0))


def get_agent_studio_opus_context_editing_trigger_tokens() -> int:
    """Input-token threshold for Anthropic tool context editing.

    Agent Studio Opus asks Anthropic to clear stale tool uses/results after the
    live request context crosses this threshold. Default 140000, approximately
    70% of the 200K-token Opus context budget used by Agent Studio.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_OPUS_CONTEXT_EDITING_TRIGGER_TOKENS",
            140_000,
        ),
    )


def get_agent_studio_opus_context_editing_keep_tool_uses() -> int:
    """Recent tool-use count Anthropic should keep when context editing triggers.

    Keeping a small tail preserves local tool-loop continuity while older tool
    results can be rehydrated through durable chat/TraceReview recall tools.
    Default 3.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_OPUS_CONTEXT_EDITING_KEEP_TOOL_USES",
            3,
        ),
    )


def get_agent_studio_provider_tool_result_inline_max_chars() -> int:
    """Max raw tool-result JSON chars replayed to provider continuation.

    Larger Agent Studio tool results are replaced with compact provider-only
    summaries and recall instructions while the frontend still receives the full
    result event. Default 12000.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS",
            12_000,
        ),
    )


def get_agent_studio_chat_history_page_size() -> int:
    """Maximum sessions requested by one Agent Studio list/search call.

    Provider-envelope fitting may return fewer rows with a continuation. Default 25.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_CHAT_HISTORY_PAGE_SIZE",
            25,
        ),
    )


def get_agent_studio_chat_recall_page_size() -> int:
    """Maximum durable chat rows returned by one Agent Studio recall page.

    Metadata-only pages remain bounded beneath the provider result envelope.
    Default 10.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_CHAT_RECALL_PAGE_SIZE",
            10,
        ),
    )


def get_agent_studio_chat_recall_chunk_max_chars() -> int:
    """Maximum exact durable chat field characters requested per chunk.

    Runtime envelope fitting may return fewer characters when JSON escaping
    expands a value. Default 8000.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_CHAT_RECALL_CHUNK_MAX_CHARS",
            8_000,
        ),
    )


def get_agent_studio_workshop_prompt_max_chars() -> int:
    """Max characters accepted in an Agent Workshop prompt proposal.

    This bounds both complete replacement prompts and the result of targeted
    edits before the proposal is sent to the curator approval UI. Default 40000.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_WORKSHOP_PROMPT_MAX_CHARS",
            40_000,
        ),
    )


def get_agent_studio_workshop_context_prompt_max_chars() -> int:
    """Max main-prompt characters included in Agent Workshop system context.

    Exact current prompt text remains available through the bounded Workshop
    refresh tool when this preview is clipped. Default 12000.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_WORKSHOP_CONTEXT_PROMPT_MAX_CHARS",
            12_000,
        ),
    )


def get_agent_studio_workshop_context_group_prompt_max_chars() -> int:
    """Max selected-group prompt characters included in Workshop context.

    Exact current group prompt text remains available through the bounded
    Workshop refresh tool when this preview is clipped. Default 6000.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_WORKSHOP_CONTEXT_GROUP_PROMPT_MAX_CHARS",
            6_000,
        ),
    )


def get_agent_studio_workshop_prompt_chunk_max_chars() -> int:
    """Max exact prompt characters returned by one Workshop refresh chunk.

    The default leaves room for chunk identity and freshness metadata beneath
    the provider's default inline-result boundary. Default 8000.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_WORKSHOP_PROMPT_CHUNK_MAX_CHARS",
            8_000,
        ),
    )


def get_agent_studio_prompt_inspection_chunk_max_chars() -> int:
    """Max prompt characters returned by one get_prompt detail call.

    The default leaves room for deterministic chunk metadata and JSON escaping
    beneath the provider's default 12,000-character inline-result boundary.
    Default 8000.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_PROMPT_INSPECTION_CHUNK_MAX_CHARS",
            8_000,
        ),
    )


def get_agent_studio_tool_inventory_default_items() -> int:
    """Default summaries returned by one Agent Studio tool inventory page."""
    return max(1, _get_env_int_with_fallback("AGENT_STUDIO_TOOL_INVENTORY_DEFAULT_ITEMS", 20))


def get_agent_studio_tool_inventory_max_items() -> int:
    """Maximum summaries accepted by one Agent Studio tool inventory call."""
    return max(1, _get_env_int_with_fallback("AGENT_STUDIO_TOOL_INVENTORY_MAX_ITEMS", 50))


def get_agent_studio_tool_inventory_summary_max_chars() -> int:
    """Maximum description characters retained in one inventory summary."""
    return max(1, _get_env_int_with_fallback("AGENT_STUDIO_TOOL_INVENTORY_SUMMARY_MAX_CHARS", 300))


def get_agent_studio_tool_inventory_result_max_chars() -> int:
    """Maximum serialized characters targeted by one inventory page."""
    return max(1, _get_env_int_with_fallback("AGENT_STUDIO_TOOL_INVENTORY_RESULT_MAX_CHARS", 8_000))


def get_agent_studio_tool_details_result_max_chars() -> int:
    """Maximum serialized characters for one inline tool-details result."""
    return max(1, _get_env_int_with_fallback("AGENT_STUDIO_TOOL_DETAILS_RESULT_MAX_CHARS", 8_000))


def get_agent_studio_tool_details_chunk_max_chars() -> int:
    """Maximum exact source characters requested from one metadata chunk."""
    return max(1, _get_env_int_with_fallback("AGENT_STUDIO_TOOL_DETAILS_CHUNK_MAX_CHARS", 6_000))


def get_agent_studio_flow_template_default_items() -> int:
    """Default compatible templates returned by one independent page."""
    return max(1, _get_env_int_with_fallback("AGENT_STUDIO_FLOW_TEMPLATE_DEFAULT_ITEMS", 5))


def get_agent_studio_flow_template_max_items() -> int:
    """Maximum compatible templates accepted by one page request."""
    return max(1, _get_env_int_with_fallback("AGENT_STUDIO_FLOW_TEMPLATE_MAX_ITEMS", 20))


def get_agent_studio_flow_catalog_result_max_chars() -> int:
    """Maximum provider-serialized characters for one flow catalog result."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_FLOW_CATALOG_RESULT_MAX_CHARS",
            8_000,
        ),
    )


def get_agent_studio_flow_catalog_chunk_max_chars() -> int:
    """Maximum exact characters requested from one flow catalog detail chunk."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_FLOW_CATALOG_CHUNK_MAX_CHARS",
            6_000,
        ),
    )


def get_agent_studio_trace_review_page_size() -> int:
    """Max/default page size for exact TraceReview call listings.

    The same bound is used for summary and detail-reference pages so neither
    tool can return an unbounded number of call records. Default 10.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_TRACE_REVIEW_PAGE_SIZE",
            10,
        ),
    )


def get_agent_studio_trace_review_aggregate_page_size() -> int:
    """Default/max items returned by one aggregate TraceReview page."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_TRACE_REVIEW_AGGREGATE_PAGE_SIZE",
            5,
        ),
    )


def get_agent_studio_trace_search_default_limit() -> int:
    """Default number of TraceReview search matches requested per page."""
    return max(
        1,
        _get_env_int_with_fallback("AGENT_STUDIO_TRACE_SEARCH_DEFAULT_LIMIT", 25),
    )


def get_agent_studio_trace_search_max_limit() -> int:
    """Maximum number of TraceReview search matches requested per page."""
    return max(
        get_agent_studio_trace_search_default_limit(),
        _get_env_int_with_fallback("AGENT_STUDIO_TRACE_SEARCH_MAX_LIMIT", 100),
    )


def get_agent_studio_trace_search_filter_max_chars() -> int:
    """Maximum exact characters accepted in one TraceReview search filter."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_TRACE_SEARCH_FILTER_MAX_CHARS",
            256,
        ),
    )


def get_agent_studio_service_log_default_lines() -> int:
    """Default logical log lines requested by Agent Studio."""
    return max(
        1,
        _get_env_int_with_fallback("AGENT_STUDIO_SERVICE_LOG_DEFAULT_LINES", 20),
    )


def get_agent_studio_service_log_max_lines() -> int:
    """Maximum logical log lines requested by Agent Studio."""
    return max(
        get_agent_studio_service_log_default_lines(),
        _get_env_int_with_fallback("AGENT_STUDIO_SERVICE_LOG_MAX_LINES", 100),
    )


def get_agent_studio_service_log_source_query_max_lines() -> int:
    """Maximum Loki source entries fetched to resolve one log cursor page."""
    return max(
        get_agent_studio_service_log_max_lines(),
        _get_env_int_with_fallback(
            "AGENT_STUDIO_SERVICE_LOG_SOURCE_QUERY_MAX_LINES",
            2_000,
        ),
    )


def get_agent_studio_service_log_page_max_chars() -> int:
    """Maximum JSON-encoded log content characters returned in one page."""
    return max(
        1,
        _get_env_int_with_fallback("AGENT_STUDIO_SERVICE_LOG_PAGE_MAX_CHARS", 8_000),
    )


def get_agent_studio_service_log_timeout_seconds() -> float:
    """Timeout for the internal Agent Studio service-log HTTP request."""
    return max(
        0.1,
        _get_env_float_with_fallback("AGENT_STUDIO_SERVICE_LOG_TIMEOUT_SECONDS", 15.0),
    )


def get_agent_studio_service_log_default_lookback_minutes() -> int:
    """Default Loki lookback used when Agent Studio omits ``since``."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_SERVICE_LOG_DEFAULT_LOOKBACK_MINUTES",
            1_440,
        ),
    )


def get_agent_studio_service_log_max_lookback_minutes() -> int:
    """Maximum Loki lookback accepted from Agent Studio."""
    return max(
        get_agent_studio_service_log_default_lookback_minutes(),
        _get_env_int_with_fallback(
            "AGENT_STUDIO_SERVICE_LOG_MAX_LOOKBACK_MINUTES",
            10_080,
        ),
    )


def get_agent_studio_package_diagnostic_page_default_items() -> int:
    """Default structured items returned by one package diagnostic page."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_PACKAGE_DIAGNOSTIC_PAGE_DEFAULT_ITEMS",
            10,
        ),
    )


def get_agent_studio_trace_review_summary_max_chars() -> int:
    """Max characters in each TraceReview call input/result summary.

    Exact content remains available through field chunks. Default 200.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_TRACE_REVIEW_SUMMARY_MAX_CHARS",
            200,
        ),
    )


def get_agent_studio_package_diagnostic_page_max_items() -> int:
    """Maximum structured items requested from one package diagnostic page."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_PACKAGE_DIAGNOSTIC_PAGE_MAX_ITEMS",
            25,
        ),
    )


def get_agent_studio_trace_review_chunk_max_chars() -> int:
    """Max JSON-encoded exact content characters in one TraceReview chunk.

    Source text is shortened when JSON escaping expands it. The default leaves
    envelope headroom beneath the provider's 12,000-character inline-result
    boundary. Default 8000.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_TRACE_REVIEW_CHUNK_MAX_CHARS",
            8_000,
        ),
    )


def get_agent_studio_package_diagnostic_result_max_chars() -> int:
    """Maximum serialized characters targeted by package diagnostic summaries/pages."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_PACKAGE_DIAGNOSTIC_RESULT_MAX_CHARS",
            8_000,
        ),
    )


def get_agent_studio_package_diagnostic_chunk_max_chars() -> int:
    """Maximum exact characters returned by one package diagnostic detail chunk."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_PACKAGE_DIAGNOSTIC_CHUNK_MAX_CHARS",
            6_000,
        ),
    )


def get_agent_studio_package_diagnostic_scalar_preview_max_chars() -> int:
    """Maximum inline preview characters for an oversized diagnostic scalar."""
    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_PACKAGE_DIAGNOSTIC_SCALAR_PREVIEW_MAX_CHARS",
            256,
        ),
    )


def get_flow_definition_max_nodes() -> int:
    """Max canonical flow nodes, including the required task-input node."""
    configured = _get_env_int_with_fallback(
        "FLOW_DEFINITION_MAX_NODES",
        FLOW_DEFINITION_MAX_NODES_DEFAULT,
    )
    if configured < 2:
        logger.warning(
            "FLOW_DEFINITION_MAX_NODES=%s is below minimum 2; using 2",
            configured,
        )
    return max(2, configured)


def get_agent_studio_flow_max_steps() -> int:
    """Max authored steps admitted by Agent Studio within canonical capacity."""
    configured_max = _get_env_int_with_fallback(
        "AGENT_STUDIO_FLOW_MAX_STEPS",
        FLOW_DEFINITION_MAX_NODES_DEFAULT - 1,
    )
    if configured_max < 1:
        logger.warning(
            "AGENT_STUDIO_FLOW_MAX_STEPS=%s is below minimum 1; using 1",
            configured_max,
        )
        configured_max = 1
    canonical_capacity = get_flow_definition_max_nodes() - 1
    if configured_max > canonical_capacity:
        logger.warning(
            "AGENT_STUDIO_FLOW_MAX_STEPS=%s exceeds the canonical authored-step "
            "capacity %s; using %s",
            configured_max,
            canonical_capacity,
            canonical_capacity,
        )
    return min(configured_max, canonical_capacity)


def get_agent_studio_flow_name_max_chars() -> int:
    """Max Agent Studio flow-name length, bounded by persistence."""
    return _get_bounded_positive_env_int(
        "AGENT_STUDIO_FLOW_NAME_MAX_CHARS",
        FLOW_NAME_MAX_CHARS,
        FLOW_NAME_MAX_CHARS,
    )


def get_agent_studio_flow_description_max_chars() -> int:
    """Max Agent Studio flow-description length, bounded by the API model."""
    return _get_bounded_positive_env_int(
        "AGENT_STUDIO_FLOW_DESCRIPTION_MAX_CHARS",
        FLOW_DESCRIPTION_MAX_CHARS,
        FLOW_DESCRIPTION_MAX_CHARS,
    )


def get_agent_studio_flow_step_goal_max_chars() -> int:
    """Max simplified step-goal length, bounded by ``FlowNodeData``."""
    return _get_bounded_positive_env_int(
        "AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS",
        FLOW_STEP_GOAL_MAX_CHARS,
        FLOW_STEP_GOAL_MAX_CHARS,
    )


def get_agent_studio_flow_custom_instructions_max_chars() -> int:
    """Max simplified custom-instruction length, bounded by ``FlowNodeData``."""
    return _get_bounded_positive_env_int(
        "AGENT_STUDIO_FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS",
        FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS,
        FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS,
    )


def get_agent_studio_flow_output_filename_template_max_chars() -> int:
    """Max formatter filename-template length, bounded by ``FlowNodeData``."""
    return _get_bounded_positive_env_int(
        "AGENT_STUDIO_FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS",
        FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS,
        FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS,
    )


def get_agent_studio_flow_inspection_page_limit() -> int:
    """Maximum entries returned by one current-flow inspection page."""

    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_FLOW_INSPECTION_PAGE_LIMIT",
            20,
        ),
    )


def get_agent_studio_flow_inspection_chunk_max_chars() -> int:
    """Maximum exact text characters returned by one flow detail call."""

    return max(
        1,
        _get_env_int_with_fallback(
            "AGENT_STUDIO_FLOW_INSPECTION_CHUNK_MAX_CHARS",
            4000,
        ),
    )


def get_trace_review_export_timeout_seconds() -> float:
    """HTTP timeout for the TraceReview export call (TRACE_REVIEW_EXPORT_TIMEOUT_SECONDS).

    Wall-clock budget for fetching trace context from the TraceReview service.
    Default 30.
    """
    return max(1.0, _get_env_float_with_fallback("TRACE_REVIEW_EXPORT_TIMEOUT_SECONDS", 30.0))


def get_codebase_search_timeout_seconds() -> int:
    """Timeout for the ripgrep subprocess in Agent Studio code search (CODEBASE_SEARCH_TIMEOUT_SECONDS).

    Wall-clock budget for one read-only codebase search; on expiry the search is
    aborted. Default 30.
    """
    return max(1, _get_env_int_with_fallback("CODEBASE_SEARCH_TIMEOUT_SECONDS", 30))


def get_codebase_read_max_lines() -> int:
    """Max lines one Agent Studio code-read returns (CODEBASE_READ_MAX_LINES).

    Bounds a single read-only file read. Default 400.
    """
    return max(1, _get_env_int_with_fallback("CODEBASE_READ_MAX_LINES", 400))


def get_codebase_result_max_chars() -> int:
    """Maximum serialized characters returned by one code inspection call."""
    return max(1, _get_env_int_with_fallback("CODEBASE_RESULT_MAX_CHARS", 8_000))


def get_codebase_long_line_chunk_max_chars() -> int:
    """Maximum exact source characters requested from one long-line chunk."""
    return max(1, _get_env_int_with_fallback("CODEBASE_LONG_LINE_CHUNK_MAX_CHARS", 6_000))


def get_codebase_search_max_results() -> int:
    """Max content-search hits returned by Agent Studio code search (CODEBASE_SEARCH_MAX_RESULTS).

    Bounds content-match results. Default 100.
    """
    return max(1, _get_env_int_with_fallback("CODEBASE_SEARCH_MAX_RESULTS", 100))


def get_codebase_file_list_max_results() -> int:
    """Max file-list entries returned by Agent Studio code search (CODEBASE_FILE_LIST_MAX_RESULTS).

    Bounds file-name listing results. Default 200.
    """
    return max(1, _get_env_int_with_fallback("CODEBASE_FILE_LIST_MAX_RESULTS", 200))


# --- Feedback / transcript ---

def get_transcript_page_size() -> int:
    """Page size for paginating a captured feedback transcript (TRANSCRIPT_PAGE_SIZE).

    How many chat messages are pulled per page when snapshotting a feedback
    transcript. Default 200.
    """
    return max(1, _get_env_int_with_fallback("TRANSCRIPT_PAGE_SIZE", 200))


def get_transcript_excerpt_edge_turns() -> int:
    """Turns kept at each edge of an inline transcript excerpt (TRANSCRIPT_EXCERPT_EDGE_TURNS).

    The inline excerpt keeps this many turns from the start and end (so the inline
    cap is 2x this). Default 3.
    """
    return max(1, _get_env_int_with_fallback("TRANSCRIPT_EXCERPT_EDGE_TURNS", 3))


def get_max_transcript_turn_chars() -> int:
    """Char truncation budget per transcript turn (MAX_TRANSCRIPT_TURN_CHARS).

    Caps the stored length of any single turn in a feedback transcript snapshot.
    Default 500.
    """
    return max(1, _get_env_int_with_fallback("MAX_TRANSCRIPT_TURN_CHARS", 500))


def get_feedback_trace_snapshot_traces() -> int:
    """Max traces captured in a feedback trace snapshot (FEEDBACK_TRACE_SNAPSHOT_TRACES).

    Bounds how many distinct traces a feedback report snapshots. Default 5.
    """
    return max(1, _get_env_int_with_fallback("FEEDBACK_TRACE_SNAPSHOT_TRACES", 5))


def get_feedback_trace_snapshot_items() -> int:
    """Max items per trace in a feedback trace snapshot (FEEDBACK_TRACE_SNAPSHOT_ITEMS).

    Bounds how many items per trace a feedback report snapshots. Default 20.
    """
    return max(1, _get_env_int_with_fallback("FEEDBACK_TRACE_SNAPSHOT_ITEMS", 20))


def get_feedback_trace_preview_chars() -> int:
    """Char truncation budget for a feedback trace preview (FEEDBACK_TRACE_PREVIEW_CHARS).

    Caps preview text length in feedback trace snapshots. Default 500.
    """
    return max(1, _get_env_int_with_fallback("FEEDBACK_TRACE_PREVIEW_CHARS", 500))


def get_feedback_trace_error_chars() -> int:
    """Char truncation budget for a feedback trace error string (FEEDBACK_TRACE_ERROR_CHARS).

    Caps error text length in feedback trace snapshots. Default 300.
    """
    return max(1, _get_env_int_with_fallback("FEEDBACK_TRACE_ERROR_CHARS", 300))


# --- Agent Studio domain-envelope diagnostic tools ---

def get_domain_envelope_default_limit() -> int:
    """Default page size for domain-envelope diagnostic tool listings (DOMAIN_ENVELOPE_DEFAULT_LIMIT).

    Default 10.
    """
    return max(1, _get_env_int_with_fallback("DOMAIN_ENVELOPE_DEFAULT_LIMIT", 10))


def get_domain_envelope_max_limit() -> int:
    """Max page size for domain-envelope diagnostic tool listings (DOMAIN_ENVELOPE_MAX_LIMIT).

    Caps the requested page size. Default 50.
    """
    return max(1, _get_env_int_with_fallback("DOMAIN_ENVELOPE_MAX_LIMIT", 50))


def get_domain_pack_validation_plan_default_limit() -> int:
    """Default detail page size for domain-pack validation plans.

    ``DOMAIN_PACK_VALIDATION_PLAN_DEFAULT_LIMIT`` defaults to 3 so verbose
    disease-pack records remain below the provider's inline result budget.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "DOMAIN_PACK_VALIDATION_PLAN_DEFAULT_LIMIT",
            3,
        ),
    )


def get_domain_pack_validation_plan_max_limit() -> int:
    """Maximum detail page size for domain-pack validation plans.

    ``DOMAIN_PACK_VALIDATION_PLAN_MAX_LIMIT`` defaults to 4 to keep even the
    largest installed disease-pack section pages provider-visible.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "DOMAIN_PACK_VALIDATION_PLAN_MAX_LIMIT",
            4,
        ),
    )


def get_domain_runtime_inspection_default_limit() -> int:
    """Default detail page size for domain runtime inspection tools.

    ``DOMAIN_RUNTIME_INSPECTION_DEFAULT_LIMIT`` defaults to 3 so realistic
    envelope, review-row, and readiness pages remain provider-visible.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "DOMAIN_RUNTIME_INSPECTION_DEFAULT_LIMIT",
            3,
        ),
    )


def get_domain_runtime_inspection_max_limit() -> int:
    """Maximum detail page size for domain runtime inspection tools.

    ``DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT`` defaults to 4 to bound aggregate
    provider results even when individual domain records are verbose.
    """
    return max(
        1,
        _get_env_int_with_fallback(
            "DOMAIN_RUNTIME_INSPECTION_MAX_LIMIT",
            4,
        ),
    )


def get_domain_envelope_max_json_chars() -> int:
    """Char cap on bounded JSON returned by domain-envelope tools (DOMAIN_ENVELOPE_MAX_JSON_CHARS).

    Truncates large JSON payloads surfaced to the model. Default 20000.
    """
    return max(1, _get_env_int_with_fallback("DOMAIN_ENVELOPE_MAX_JSON_CHARS", 20_000))


def get_domain_envelope_max_summary_json_chars() -> int:
    """Char cap on bounded summary JSON in domain-envelope tools (DOMAIN_ENVELOPE_MAX_SUMMARY_JSON_CHARS).

    Truncates the compact summary JSON. Default 4000.
    """
    return max(1, _get_env_int_with_fallback("DOMAIN_ENVELOPE_MAX_SUMMARY_JSON_CHARS", 4_000))


def get_domain_envelope_max_lookup_attempts() -> int:
    """Max lookup attempts retained per object in domain-envelope tools (DOMAIN_ENVELOPE_MAX_LOOKUP_ATTEMPTS).

    Bounds how many resolver lookup attempts are surfaced before truncation.
    Default 25.
    """
    return max(1, _get_env_int_with_fallback("DOMAIN_ENVELOPE_MAX_LOOKUP_ATTEMPTS", 25))


def get_domain_envelope_max_validator_lookup_attempts() -> int:
    """Max validator lookup attempts retained in domain-envelope tools (DOMAIN_ENVELOPE_MAX_VALIDATOR_LOOKUP_ATTEMPTS).

    Bounds validator lookup attempts surfaced before truncation. Default 10.
    """
    return max(
        1,
        _get_env_int_with_fallback("DOMAIN_ENVELOPE_MAX_VALIDATOR_LOOKUP_ATTEMPTS", 10),
    )


def get_domain_envelope_max_validator_summaries() -> int:
    """Max validator summaries retained in domain-envelope tools (DOMAIN_ENVELOPE_MAX_VALIDATOR_SUMMARIES).

    Bounds validator summaries surfaced before truncation. Default 25.
    """
    return max(1, _get_env_int_with_fallback("DOMAIN_ENVELOPE_MAX_VALIDATOR_SUMMARIES", 25))


def get_domain_envelope_max_field_paths() -> int:
    """Max field paths retained in domain-envelope tools (DOMAIN_ENVELOPE_MAX_FIELD_PATHS).

    Bounds field-path enumeration surfaced to the model. Default 150.
    """
    return max(1, _get_env_int_with_fallback("DOMAIN_ENVELOPE_MAX_FIELD_PATHS", 150))


def get_domain_reference_max_values() -> int:
    """Max distinct values per domain reference key in Agent Studio (DOMAIN_REFERENCE_MAX_VALUES).

    Caps how many values are surfaced for each domain reference key. Default 50.
    """
    return max(1, _get_env_int_with_fallback("DOMAIN_REFERENCE_MAX_VALUES", 50))


# --- Chat / session ---

def get_flow_memory_max_visible_output_chars() -> int:
    """Char cap on flow-memory visible output replayed into chat (FLOW_MEMORY_MAX_VISIBLE_OUTPUT_CHARS).

    Truncates the visible flow output kept in conversational memory. Default 2500.
    """
    return max(1, _get_env_int_with_fallback("FLOW_MEMORY_MAX_VISIBLE_OUTPUT_CHARS", 2500))


def get_title_backfill_message_limit() -> int:
    """Messages scanned when backfilling a chat title (TITLE_BACKFILL_MESSAGE_LIMIT).

    Bounds how many recent messages are read to synthesize a session title.
    Default 20.
    """
    return max(1, _get_env_int_with_fallback("TITLE_BACKFILL_MESSAGE_LIMIT", 20))


def get_chat_title_max_length() -> int:
    """Max character length of a generated chat title (CHAT_TITLE_MAX_LENGTH).

    Default 80.
    """
    return max(1, _get_env_int_with_fallback("CHAT_TITLE_MAX_LENGTH", 80))


def get_chat_session_page_size_max() -> int:
    """Max page size for chat session listings (CHAT_SESSION_PAGE_SIZE_MAX).

    Caps the requested session-list page size. Default 100.
    """
    return max(1, _get_env_int_with_fallback("CHAT_SESSION_PAGE_SIZE_MAX", 100))


def get_chat_message_page_size_max() -> int:
    """Max page size for chat message listings (CHAT_MESSAGE_PAGE_SIZE_MAX).

    Caps the requested message-list page size. Default 200.
    """
    return max(1, _get_env_int_with_fallback("CHAT_MESSAGE_PAGE_SIZE_MAX", 200))


def get_chat_recent_message_scan_size_max() -> int:
    """Max messages scanned for a recent-window query (CHAT_RECENT_MESSAGE_SCAN_SIZE_MAX).

    Bounds the recent-message scan window for chat history queries. Default 5000.
    """
    return max(1, _get_env_int_with_fallback("CHAT_RECENT_MESSAGE_SCAN_SIZE_MAX", 5000))


def get_executable_run_event_replay_limit() -> int:
    """Replay buffer size per active executable run (EXECUTABLE_RUN_EVENT_REPLAY_LIMIT).

    Bounds in-memory event replay for observers that detach and reattach to a
    running chat, flow, or Agent Studio turn. Default 1000.
    """
    return max(1, _get_env_int_with_fallback("EXECUTABLE_RUN_EVENT_REPLAY_LIMIT", 1000))


def get_executable_run_retention_seconds() -> int:
    """Seconds to retain terminal in-memory executable runs (EXECUTABLE_RUN_RETENTION_SECONDS).

    Retention lets a route remount replay terminal events shortly after a run
    finishes while durable chat history remains the long-lived source. Default 900.
    """
    return max(1, _get_env_int_with_fallback("EXECUTABLE_RUN_RETENTION_SECONDS", 900))


def get_chat_sse_keepalive_interval_seconds() -> float:
    """Seconds between transport-only chat/flow observer SSE comments.

    The cadence keeps otherwise silent public SSE connections active without
    adding replayable executable-run events. Default 20 seconds.
    """
    return max(
        0.1,
        _get_env_float_with_fallback("CHAT_SSE_KEEPALIVE_INTERVAL_SECONDS", 20.0),
    )


def get_runtime_observability_tag_value_max_chars() -> int:
    """Char cap for runtime observability tag values (RUNTIME_OBSERVABILITY_TAG_VALUE_MAX_CHARS).

    Bounds low-cardinality Sentry tag values emitted by caught runtime exception
    reporting. Default 200.
    """
    return max(1, _get_env_int_with_fallback("RUNTIME_OBSERVABILITY_TAG_VALUE_MAX_CHARS", 200))


def get_runtime_observability_context_value_max_chars() -> int:
    """Char cap for runtime observability context values (RUNTIME_OBSERVABILITY_CONTEXT_VALUE_MAX_CHARS).

    Bounds custom Sentry context values emitted by caught runtime exception
    reporting before the global redaction hook applies. Default 500.
    """
    return max(
        1,
        _get_env_int_with_fallback("RUNTIME_OBSERVABILITY_CONTEXT_VALUE_MAX_CHARS", 500),
    )


def get_background_task_observability_value_max_chars() -> int:
    """Char cap for background-task Sentry values (BACKGROUND_TASK_OBSERVABILITY_VALUE_MAX_CHARS).

    Bounds string tag and context values emitted by background-task failure
    reporting. Default 200.
    """
    return max(
        1,
        _get_env_int_with_fallback("BACKGROUND_TASK_OBSERVABILITY_VALUE_MAX_CHARS", 200),
    )


def get_tool_failure_alert_summary_max_chars() -> int:
    """Char cap for SNS tool-failure previews (TOOL_FAILURE_ALERT_SUMMARY_MAX_CHARS).

    Bounds error and context string previews emitted in optional SNS alert
    bodies. Default 500.
    """
    return max(
        1,
        _get_env_int_with_fallback("TOOL_FAILURE_ALERT_SUMMARY_MAX_CHARS", 500),
    )


def get_flow_list_page_size_default() -> int:
    """Default page size for flow listings (FLOW_LIST_PAGE_SIZE_DEFAULT).

    Default 50.
    """
    return max(1, _get_env_int_with_fallback("FLOW_LIST_PAGE_SIZE_DEFAULT", 50))


# --- Flow output projection tooling ---

def get_flow_projection_max_text_chars() -> int:
    """Char cap on text fields in flow output projection (FLOW_PROJECTION_MAX_TEXT_CHARS).

    Truncates per-field text visible formatter/projection tools inspect. Default 180.
    """
    return max(1, _get_env_int_with_fallback("FLOW_PROJECTION_MAX_TEXT_CHARS", 180))


def get_flow_projection_max_row_chars() -> int:
    """Char cap on row previews in flow output projection (FLOW_PROJECTION_MAX_ROW_CHARS).

    Truncates per-row preview text visible formatter/projection tools inspect. Default 2000.
    """
    return max(1, _get_env_int_with_fallback("FLOW_PROJECTION_MAX_ROW_CHARS", 2_000))


def get_flow_projection_max_rows() -> int:
    """Hard cap on rows a deterministic flow output projection emits (FLOW_PROJECTION_MAX_ROWS).

    Safety ceiling so a runaway projection cannot build an unbounded table.
    Default 10000.
    """
    return max(1, _get_env_int_with_fallback("FLOW_PROJECTION_MAX_ROWS", 10_000))


def get_flow_chat_max_rows() -> int:
    """Max rows rendered in a chat-format flow output (FLOW_CHAT_MAX_ROWS).

    Bounds chat table/section length. Default 50.
    """
    return max(1, _get_env_int_with_fallback("FLOW_CHAT_MAX_ROWS", 50))


def get_flow_projection_max_field_examples() -> int:
    """Example values shown per field to projection tools (FLOW_PROJECTION_MAX_FIELD_EXAMPLES).

    Default 3.
    """
    return max(1, _get_env_int_with_fallback("FLOW_PROJECTION_MAX_FIELD_EXAMPLES", 3))


def get_flow_projection_max_list_items() -> int:
    """List items previewed per field by projection tools (FLOW_PROJECTION_MAX_LIST_ITEMS).

    Default 5.
    """
    return max(1, _get_env_int_with_fallback("FLOW_PROJECTION_MAX_LIST_ITEMS", 5))


def get_flow_projection_max_object_items() -> int:
    """Object keys previewed per field by projection tools (FLOW_PROJECTION_MAX_OBJECT_ITEMS).

    Default 12.
    """
    return max(1, _get_env_int_with_fallback("FLOW_PROJECTION_MAX_OBJECT_ITEMS", 12))


def get_flow_output_projection_preview_max_depth() -> int:
    """Max nested depth shown in flow output projection previews (FLOW_OUTPUT_PROJECTION_PREVIEW_MAX_DEPTH).

    Bounds nested JSON/list previews returned by deterministic flow projection
    previews. Default 4.
    """
    return max(
        1,
        _get_env_int_with_fallback("FLOW_OUTPUT_PROJECTION_PREVIEW_MAX_DEPTH", 4),
    )


def get_formatter_preview_max_depth() -> int:
    """Max nested depth shown in formatter preview values (FORMATTER_PREVIEW_MAX_DEPTH).

    Bounds nested JSON/list previews returned to formatter agents. Default 4.
    """
    return max(1, _get_env_int_with_fallback("FORMATTER_PREVIEW_MAX_DEPTH", 4))


# --- Curation / pipeline ---

def get_async_candidate_threshold() -> int:
    """Candidate count above which curation persistence runs async (ASYNC_CANDIDATE_THRESHOLD).

    Below this, candidates are persisted synchronously; at or above, the pipeline
    switches to async processing. Default 25.
    """
    return max(1, _get_env_int_with_fallback("ASYNC_CANDIDATE_THRESHOLD", 25))


def get_record_evidence_preview_chars() -> int:
    """Char truncation budget for record_evidence text previews (RECORD_EVIDENCE_PREVIEW_CHARS).

    Caps preview length in the record_evidence tool. Default 300.
    """
    return max(1, _get_env_int_with_fallback("RECORD_EVIDENCE_PREVIEW_CHARS", 300))


# --- Infrastructure clients (logs, rerank) ---

def get_loki_query_timeout_seconds() -> float:
    """HTTP timeout for Loki log queries (LOKI_QUERY_TIMEOUT_SECONDS).

    Wall-clock budget for one Loki query request. Default 10.
    """
    return max(1.0, _get_env_float_with_fallback("LOKI_QUERY_TIMEOUT_SECONDS", 10.0))


def get_loki_query_limit() -> int:
    """Default max log lines returned per Loki query (LOKI_QUERY_LIMIT).

    Default 2000.
    """
    return max(1, _get_env_int_with_fallback("LOKI_QUERY_LIMIT", 2000))


# NOTE: bedrock_reranker reads BEDROCK_RERANK_MAX_SOURCES and
# LOCAL_TRANSFORMERS_RERANK_TIMEOUT_SECONDS directly via os.getenv because that
# module can load inside the isolated package subprocess. The vars are still
# documented in .env.example.


# --- Batch / queues / file sizes ---

def get_batch_event_queue_maxsize() -> int:
    """Bounded size of a per-batch event queue (BATCH_EVENT_QUEUE_MAXSIZE).

    Caps in-memory event buffering to prevent unbounded memory growth when a
    consumer is slow. Default 1000.
    """
    return max(1, _get_env_int_with_fallback("BATCH_EVENT_QUEUE_MAXSIZE", 1000))


def get_file_output_max_size_bytes() -> int:
    """Max byte size of a generated file output (FILE_OUTPUT_MAX_SIZE_BYTES).

    Rejects oversized generated content before storage. Default 104857600 (100 MB).
    """
    return max(
        1,
        _get_env_int_with_fallback("FILE_OUTPUT_MAX_SIZE_BYTES", 100 * 1024 * 1024),
    )


def get_pdf_max_file_size_bytes() -> int:
    """Max byte size of an uploaded/processed PDF (PDF_MAX_FILE_SIZE_BYTES).

    Rejects oversized PDF uploads. Default 524288000 bytes (500 MiB). The
    configured value must fit the PostgreSQL ``INTEGER`` storage column used
    for persisted file sizes.
    """
    key = "PDF_MAX_FILE_SIZE_BYTES"
    raw = os.getenv(key)
    if raw is None:
        return 500 * 1024 * 1024
    if not raw.isascii() or not raw.isdigit():
        raise ValueError(f"{key} must be a positive integer byte count")

    value = int(raw)
    if value < 1:
        raise ValueError(f"{key} must be greater than zero")

    postgres_integer_max = 2_147_483_647
    if value > postgres_integer_max:
        raise ValueError(
            f"{key} must not exceed the persisted file-size capacity of "
            f"{postgres_integer_max} bytes"
        )
    return value


def get_pdf_document_error_message_max_chars() -> int:
    """Max chars retained for persisted PDF document failure context.

    Default 1000.
    """
    return max(
        1,
        _get_env_int_with_fallback("PDF_DOCUMENT_ERROR_MESSAGE_MAX_CHARS", 1000),
    )


def get_pdf_no_job_orphan_threshold_seconds() -> int:
    """Minimum pending-document age before no-job repair. Default 86400."""
    return max(
        300,
        _get_env_int_with_fallback(
            "PDF_NO_JOB_ORPHAN_THRESHOLD_SECONDS",
            86400,
        ),
    )


def get_pdf_no_job_orphan_batch_size() -> int:
    """Maximum no-job orphan documents inspected per run. Default 100."""
    return max(
        1,
        _get_env_int_with_fallback("PDF_NO_JOB_ORPHAN_BATCH_SIZE", 100),
    )


def get_pdf_no_job_orphan_repair_timeout_seconds() -> int:
    """Database statement timeout for one orphan repair attempt. Default 30."""
    return max(
        1,
        _get_env_int_with_fallback(
            "PDF_NO_JOB_ORPHAN_REPAIR_TIMEOUT_SECONDS",
            30,
        ),
    )


def get_pdf_no_job_orphan_repair_retry_count() -> int:
    """Transient database retries for manual orphan repair. Default 2."""
    return max(
        0,
        _get_env_int_with_fallback("PDF_NO_JOB_ORPHAN_REPAIR_RETRY_COUNT", 2),
    )


def get_pdf_no_job_orphan_repair_apply() -> bool:
    """Whether manual no-job orphan repair writes by default. Default false."""
    return _get_env_bool("PDF_NO_JOB_ORPHAN_REPAIR_APPLY", False)


def get_pdf_upload_max_page_count() -> int:
    """Max page count admitted for PDF uploads (PDF_UPLOAD_MAX_PAGE_COUNT).

    Rejects oversized PDFs before durable intake side effects. Default 300.
    """
    return max(1, _get_env_int_with_fallback("PDF_UPLOAD_MAX_PAGE_COUNT", 300))


def get_evidence_page_only_degraded_ratio_threshold() -> float:
    """Ratio at/above which page-only evidence is flagged degraded (EVIDENCE_PAGE_ONLY_DEGRADED_RATIO_THRESHOLD).

    Fraction of page-only (low-precision) anchors that marks an extraction's
    evidence quality as degraded. Default 0.5.
    """
    return _get_env_float_with_fallback(
        "EVIDENCE_PAGE_ONLY_DEGRADED_RATIO_THRESHOLD", 0.5
    )


def get_pdf_stitched_context_min_chars() -> int:
    """Min chars of stitched context kept by the PDF fuzzy matcher (PDF_STITCHED_CONTEXT_MIN_CHARS).

    Lower bound on the context window the PDF viewer matcher stitches. Default 240.
    """
    return max(1, _get_env_int_with_fallback("PDF_STITCHED_CONTEXT_MIN_CHARS", 240))


def get_pdf_stitched_context_max_chars() -> int:
    """Max chars of stitched context kept by the PDF fuzzy matcher (PDF_STITCHED_CONTEXT_MAX_CHARS).

    Upper bound on the context window the PDF viewer matcher stitches. Default 1600.
    """
    return max(1, _get_env_int_with_fallback("PDF_STITCHED_CONTEXT_MAX_CHARS", 1600))


# =============================================================================
# Logging helper
# =============================================================================

def log_agent_config(agent_name: str, config: AgentConfig) -> None:
    """Log agent configuration for debugging."""
    logger.info(
        "%s config: model=%s temp=%s reasoning=%s tool_choice=%s",
        agent_name,
        config.model,
        config.temperature,
        config.reasoning,
        config.tool_choice,
    )
