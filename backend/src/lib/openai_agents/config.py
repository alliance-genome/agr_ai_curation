"""
Agent Configuration Module.

Centralizes all agent settings from environment variables.
Each agent can be configured individually via .env file.

Environment variable naming convention:
  AGENT_{AGENT_NAME}_{SETTING}

Example:
  AGENT_SUPERVISOR_MODEL=gpt-4o
  AGENT_PDF_MODEL=gpt-5.4-nano
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
        model_name: The model name (e.g., "gpt-5.4-nano", "gemini-3-pro-preview")

    Returns:
        Model name string for OpenAI, or LitellmModel instance for Gemini
    """
    provider_id = resolve_model_provider(model_name, provider_override)
    provider = _get_provider_definition(provider_id)

    if provider.driver == "openai_native":
        return model_name

    if provider.driver == "litellm":
        from agents.extensions.models.litellm_model import LitellmModel
        import litellm

        litellm.drop_params = bool(provider.drop_params)

        api_key = get_api_key(provider.provider_id)
        if not api_key:
            raise ValueError(f"{provider.api_key_env} environment variable not set")

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
    - GPT-5 series (gpt-5, gpt-5.4-nano) - OpenAI reasoning
    - Gemini 3 Pro Preview (gemini-3-pro-preview) - "low"/"high" thinking levels

    For Gemini 3 models, the OpenAI SDK's reasoning_effort parameter maps to:
    - minimal/low -> "low" thinking level
    - medium/high -> "high" thinking level

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
ReasoningEffort = Literal["minimal", "low", "medium", "high"]


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
    - GPT-5 family models (gpt-5, gpt-5.4-nano)
    - Gemini 3 Pro Preview (gemini-3-pro-preview) - uses "low"/"high" thinking levels

    For Gemini 3, the OpenAI SDK's reasoning_effort parameter maps to:
    - minimal/low -> "low" thinking level
    - medium/high -> "high" thinking level

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

    # Build reasoning config for models that support it
    # Note: summary="auto" enables reasoning summary streaming for GPT-5 models
    reasoning = None
    if reasoning_effort and supports_reasoning(model):
        reasoning = Reasoning(effort=reasoning_effort, summary="auto")

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
    if val in ("minimal", "low", "medium", "high"):
        return val  # type: ignore
    logger.warning('Invalid reasoning value for %s: %s, using default %s', key, val, default)
    return default


# =============================================================================
# Default values (read from .env, with minimal hardcoded fallbacks)
# =============================================================================

def get_default_model() -> str:
    """Get default model ID, preferring explicit env override if valid."""
    from src.lib.config.models_loader import get_default_model as get_catalog_default_model

    explicit = str(os.getenv("DEFAULT_AGENT_MODEL", "")).strip()
    if explicit:
        _get_model_definition(explicit)
        return explicit

    default_model = get_catalog_default_model()
    if default_model is None:
        raise ValueError("No default model configured in models.yaml")
    return default_model.model_id


def get_default_temperature() -> float:
    """Get the default temperature from DEFAULT_AGENT_TEMPERATURE env var."""
    val = os.getenv("DEFAULT_AGENT_TEMPERATURE", "0.2")
    try:
        return float(val)
    except ValueError:
        return 0.2


def get_default_reasoning() -> ReasoningEffort:
    """Get the default reasoning effort from DEFAULT_AGENT_REASONING env var."""
    val = os.getenv("DEFAULT_AGENT_REASONING", "low")
    if val in ("minimal", "low", "medium", "high"):
        return val  # type: ignore
    return "low"


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
        agent_id: The agent ID (e.g., "gene", "pdf_extraction", "allele")

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

    # Resolve each setting with priority: env > registry > fallback
    model = _get_env(
        f"{prefix}MODEL",
        defaults.get("model", get_default_model())
    )

    # Temperature can be None for models that don't support it
    temperature_str = os.getenv(f"{prefix}TEMPERATURE")
    if temperature_str is not None:
        try:
            temperature = float(temperature_str)
        except ValueError:
            temperature = defaults.get("temperature", get_default_temperature())
    else:
        temperature = defaults.get("temperature", get_default_temperature())

    reasoning = _get_env_reasoning(
        f"{prefix}REASONING",
        defaults.get("reasoning", get_default_reasoning())
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
    val = os.getenv("AGENT_MAX_TURNS", "20")
    try:
        return int(val)
    except ValueError:
        logger.warning('Invalid AGENT_MAX_TURNS value: %s, using default 20', val)
        return 20


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
