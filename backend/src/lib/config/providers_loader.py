"""
Provider catalog loader for LLM runtime adapters.

Loads provider definitions from config/providers.yaml.
"""

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any

import yaml

logger = logging.getLogger(__name__)


def _find_project_root() -> Optional[Path]:
    """Find project root by looking for pyproject.toml or docker-compose.yml."""
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists() or (parent / "docker-compose.yml").exists():
            return parent
    return None


def _get_default_providers_path() -> Path:
    """Resolve providers.yaml location with env override support."""
    env_path = os.environ.get("PROVIDERS_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    project_root = _find_project_root()
    if project_root:
        return project_root / "config" / "providers.yaml"

    return Path(__file__).parent.parent.parent.parent.parent / "config" / "providers.yaml"


DEFAULT_PROVIDERS_PATH = _get_default_providers_path()
_init_lock = threading.Lock()


@dataclass
class ProviderDefinition:
    """Runtime provider definition."""

    provider_id: str
    driver: str
    api_key_env: str
    base_url_env: Optional[str] = None
    default_base_url: Optional[str] = None
    litellm_prefix: Optional[str] = None
    drop_params: bool = False
    api_mode: str = "responses"
    default_for_runner: bool = False
    supports_parallel_tool_calls: bool = True

    @classmethod
    def from_yaml(cls, provider_id: str, data: Dict[str, Any]) -> "ProviderDefinition":
        if not isinstance(data, dict):
            raise ValueError(f"Provider '{provider_id}' definition must be a mapping")

        driver = str(data.get("driver", "")).strip().lower()
        if driver not in {"openai_native", "litellm"}:
            raise ValueError(
                f"Provider '{provider_id}' has invalid driver '{driver}'. "
                "Supported: openai_native, litellm"
            )

        api_key_env = str(data.get("api_key_env", "")).strip()
        if not api_key_env:
            raise ValueError(f"Provider '{provider_id}' is missing required field 'api_key_env'")

        api_mode = str(data.get("api_mode", "responses")).strip().lower() or "responses"
        if api_mode not in {"responses", "chat_completions"}:
            raise ValueError(
                f"Provider '{provider_id}' has invalid api_mode '{api_mode}'. "
                "Supported: responses, chat_completions"
            )

        supports = data.get("supports", {})
        if supports is None:
            supports = {}
        if not isinstance(supports, dict):
            raise ValueError(f"Provider '{provider_id}' field 'supports' must be a mapping")

        litellm_prefix = str(data.get("litellm_prefix", "")).strip() or None
        if driver == "litellm" and not litellm_prefix:
            raise ValueError(
                f"Provider '{provider_id}' with driver=litellm requires 'litellm_prefix'"
            )

        return cls(
            provider_id=provider_id,
            driver=driver,
            api_key_env=api_key_env,
            base_url_env=str(data.get("base_url_env", "")).strip() or None,
            default_base_url=str(data.get("default_base_url", "")).strip() or None,
            litellm_prefix=litellm_prefix,
            drop_params=bool(data.get("drop_params", driver == "litellm")),
            api_mode=api_mode,
            default_for_runner=bool(data.get("default_for_runner", False)),
            supports_parallel_tool_calls=bool(supports.get("parallel_tool_calls", True)),
        )


_provider_registry: Dict[str, ProviderDefinition] = {}
_initialized = False


def load_providers(
    providers_path: Optional[Path] = None,
    force_reload: bool = False,
) -> Dict[str, ProviderDefinition]:
    """Load provider catalog from YAML."""
    global _provider_registry, _initialized

    with _init_lock:
        if _initialized and not force_reload:
            return _provider_registry

        if providers_path is None:
            providers_path = DEFAULT_PROVIDERS_PATH

        if not providers_path.exists():
            raise FileNotFoundError(f"Providers configuration not found: {providers_path}")

        with open(providers_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        raw_providers = data.get("providers")
        if not isinstance(raw_providers, dict) or not raw_providers:
            raise ValueError("providers.yaml must define a non-empty top-level 'providers' mapping")

        registry: Dict[str, ProviderDefinition] = {}
        default_runner_count = 0
        for provider_id, raw in raw_providers.items():
            clean_id = str(provider_id or "").strip().lower()
            if not clean_id:
                raise ValueError("providers.yaml contains an empty provider key")
            provider = ProviderDefinition.from_yaml(clean_id, raw)
            registry[clean_id] = provider
            if provider.default_for_runner:
                default_runner_count += 1

        if default_runner_count != 1:
            raise ValueError(
                "providers.yaml must define exactly one provider with default_for_runner=true"
            )

        _provider_registry = registry
        _initialized = True
        logger.info("Loaded %s provider definitions", len(_provider_registry))
        return _provider_registry


def get_provider(provider_id: str) -> Optional[ProviderDefinition]:
    """Get one provider definition by key."""
    if not _initialized:
        load_providers()
    key = str(provider_id or "").strip().lower()
    if not key:
        return None
    return _provider_registry.get(key)


def get_default_runner_provider() -> ProviderDefinition:
    """Get provider flagged as default_for_runner."""
    if not _initialized:
        load_providers()
    for provider in _provider_registry.values():
        if provider.default_for_runner:
            return provider
    raise ValueError("No default runner provider configured")


def list_providers() -> List[ProviderDefinition]:
    """List all provider definitions."""
    if not _initialized:
        load_providers()
    return list(_provider_registry.values())


def is_initialized() -> bool:
    """Check if provider registry has been loaded."""
    return _initialized


def reset_cache() -> None:
    """Reset cached provider definitions (tests)."""
    global _provider_registry, _initialized
    with _init_lock:
        _provider_registry = {}
        _initialized = False
