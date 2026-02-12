"""
Agent Configuration Module.

Centralizes all agent settings from environment variables.
Each agent can be configured individually via .env file.

Environment variable naming convention:
  AGENT_{AGENT_NAME}_{SETTING}

Example:
  AGENT_SUPERVISOR_MODEL=gpt-4o
  AGENT_PDF_MODEL=gpt-5-mini
  AGENT_PDF_TEMPERATURE=0.3
  AGENT_GENE_REASONING=medium

Provider Configuration:
  LLM_PROVIDER=openai|gemini  (default: openai)
  GEMINI_API_KEY=...  (required if LLM_PROVIDER=gemini)

  When using Gemini, use:
  - gemini-3-pro-preview (supports reasoning with "low" or "high" levels)

  Gemini 3 requires LiteLLM for proper thought_signature handling during
  function calling. The get_model_for_agent() function automatically
  returns a LitellmModel when using Gemini.

  Future: Anthropic Claude models may be added as a third provider option.
"""

import os
import logging
from typing import Optional, Literal, Union
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# =============================================================================
# LLM Provider Configuration
# =============================================================================

# Gemini API endpoint for OpenAI compatibility mode
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def get_llm_provider() -> str:
    """Get the configured LLM provider (openai or gemini)."""
    return os.getenv("LLM_PROVIDER", "openai").lower()


def is_gemini_provider() -> bool:
    """Check if Gemini is the configured provider."""
    return get_llm_provider() == "gemini"


def get_api_key() -> Optional[str]:
    """Get the appropriate API key based on provider."""
    if is_gemini_provider():
        return os.getenv("GEMINI_API_KEY")
    return os.getenv("OPENAI_API_KEY")


def get_base_url() -> Optional[str]:
    """Get the base URL for the LLM API. Returns None for OpenAI (uses default)."""
    if is_gemini_provider():
        return GEMINI_BASE_URL
    return None  # OpenAI uses default URL


def get_model_for_agent(model_name: str) -> Union[str, "LitellmModel"]:
    """Get the appropriate model object for an agent.

    For OpenAI provider: returns model name string (SDK handles it directly)
    For Gemini provider: returns LitellmModel instance (handles thought_signature)

    Gemini 3 requires thought_signature handling for function calling, which
    LiteLLM handles automatically. This function abstracts that complexity.

    Args:
        model_name: The model name (e.g., "gpt-5-mini", "gemini-3-pro-preview")

    Returns:
        Model name string for OpenAI, or LitellmModel instance for Gemini
    """
    if is_gemini_provider():
        # Import here to avoid circular imports and only when needed
        from agents.extensions.models.litellm_model import LitellmModel
        import litellm

        # Drop unsupported params like parallel_tool_calls for Gemini
        # See: https://docs.litellm.ai/docs/completion/drop_params
        litellm.drop_params = True

        api_key = get_api_key()
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        # LiteLLM uses "gemini/" prefix for Google AI Studio models
        litellm_model_name = f"gemini/{model_name}"

        logger.info('[LiteLLM] Creating model for Gemini: %s (drop_params=True)', litellm_model_name)
        return LitellmModel(model=litellm_model_name, api_key=api_key)

    # OpenAI - just return the model name string
    return model_name


def is_gemini_model(model: str) -> bool:
    """Check if a model name is a Gemini model."""
    return model.startswith("gemini-")


def is_gpt5_model(model: str) -> bool:
    """Check if a model supports GPT-5 style reasoning."""
    return model.startswith("gpt-5")


def supports_reasoning(model: str) -> bool:
    """Check if a model supports reasoning/thinking mode.

    All supported models use reasoning:
    - GPT-5 series (gpt-5, gpt-5-mini) - OpenAI reasoning
    - Gemini 3 Pro Preview (gemini-3-pro-preview) - "low"/"high" thinking levels

    For Gemini 3 models, the OpenAI SDK's reasoning_effort parameter maps to:
    - minimal/low -> "low" thinking level
    - medium/high -> "high" thinking level

    Future: Anthropic Claude models may be added here.
    """
    # All our supported models use reasoning
    # GPT-5 series
    if model.startswith("gpt-5"):
        return True

    # Gemini 3 Pro Preview
    if model.startswith("gemini-3"):
        return True

    # Fallback for any unknown models - assume they support reasoning
    # since we only use reasoning-capable models
    return True


def supports_temperature(model: str) -> bool:
    """Check if a model supports temperature parameter.

    GPT-5 models don't support temperature when reasoning is enabled.
    Gemini 3 models and most other models support temperature.
    """
    # GPT-5 doesn't support temperature with reasoning
    if model.startswith("gpt-5"):
        return False

    # Most models (including Gemini 3, GPT-4o, etc.) support temperature
    return True


# Type alias for reasoning effort levels
ReasoningEffort = Literal["minimal", "low", "medium", "high"]


def build_model_settings(
    model: str,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[ReasoningEffort] = None,
    tool_choice: Optional[str] = None,
    parallel_tool_calls: bool = True,
    verbosity: Optional[str] = None,
):
    """
    Build ModelSettings with appropriate reasoning and temperature for the model.

    This is a shared helper function for all agents to ensure consistent
    behavior across OpenAI and Gemini models (and potentially Anthropic in future).

    Reasoning is supported on:
    - GPT-5 family models (gpt-5, gpt-5-mini)
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

    # Gemini doesn't support parallel tool calls - always disable for Gemini
    effective_parallel_tool_calls = False if is_gemini_provider() else parallel_tool_calls

    return ModelSettings(
        temperature=effective_temperature,
        reasoning=reasoning,
        tool_choice=tool_choice,
        parallel_tool_calls=effective_parallel_tool_calls,
        verbosity=effective_verbosity,
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
    """Get the default model from DEFAULT_AGENT_MODEL env var.

    This is the single source of truth for the default model.
    All agents fall back to this if their specific model is not set.
    """
    return os.getenv("DEFAULT_AGENT_MODEL", "gpt-5-mini")


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
        agent_id: The agent ID (e.g., "gene", "pdf", "allele")

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
