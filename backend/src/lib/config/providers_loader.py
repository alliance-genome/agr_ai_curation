"""Provider catalog loader with package-default and runtime-override merging."""

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.lib.packages import ExportKind

from .package_default_sources import (
    load_optional_runtime_yaml_source,
    load_package_yaml_sources,
)

logger = logging.getLogger(__name__)
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
    source_label: Optional[str] = None

    @classmethod
    def from_yaml(
        cls,
        provider_id: str,
        data: Dict[str, Any],
        *,
        source_label: str,
    ) -> "ProviderDefinition":
        if not isinstance(data, dict):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} must be a mapping"
            )

        driver = str(data.get("driver", "")).strip().lower()
        if driver not in {"openai_native", "litellm"}:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} has invalid driver '{driver}'. "
                "Supported: openai_native, litellm"
            )

        api_key_env = str(data.get("api_key_env", "")).strip()
        if not api_key_env:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} is missing required field "
                f"'api_key_env'"
            )

        api_mode = str(data.get("api_mode", "responses")).strip().lower() or "responses"
        if api_mode not in {"responses", "chat_completions"}:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} has invalid api_mode '{api_mode}'. "
                "Supported: responses, chat_completions"
            )

        supports = data.get("supports", {})
        if supports is None:
            supports = {}
        if not isinstance(supports, dict):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} field 'supports' must be a mapping"
            )

        litellm_prefix = str(data.get("litellm_prefix", "")).strip() or None
        if driver == "litellm" and not litellm_prefix:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} with driver=litellm "
                f"requires 'litellm_prefix'"
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
            source_label=source_label,
        )


_provider_registry: Dict[str, ProviderDefinition] = {}
_initialized = False


def load_providers(
    providers_path: Optional[Path] = None,
    *,
    packages_dir: Optional[Path] = None,
    force_reload: bool = False,
) -> Dict[str, ProviderDefinition]:
    """Load provider catalog from package defaults plus runtime overrides."""
    global _provider_registry, _initialized

    with _init_lock:
        if _initialized and not force_reload:
            return _provider_registry

        sources = list(
            load_package_yaml_sources(
                export_kind=ExportKind.PROVIDER,
                packages_dir=packages_dir,
            )
        )
        runtime_source = load_optional_runtime_yaml_source(
            explicit_path=providers_path,
            env_var="PROVIDERS_CONFIG_PATH",
            filename="providers.yaml",
        )
        if runtime_source is not None:
            sources.append(runtime_source)

        if not sources:
            raise FileNotFoundError(
                "No provider defaults were found in runtime packages or runtime override config"
            )

        registry: Dict[str, ProviderDefinition] = {}
        for source in sources:
            raw_providers = source.payload.get("providers")
            if not isinstance(raw_providers, dict) or not raw_providers:
                raise ValueError(
                    f"{source.describe()} must define a non-empty top-level "
                    f"'providers' mapping"
                )

            for provider_id, raw in raw_providers.items():
                clean_id = str(provider_id or "").strip().lower()
                if not clean_id:
                    raise ValueError(f"{source.describe()} contains an empty provider key")
                provider = ProviderDefinition.from_yaml(
                    clean_id,
                    raw,
                    source_label=source.describe(),
                )
                registry[clean_id] = provider

        default_runner_providers = [
            provider
            for provider in registry.values()
            if provider.default_for_runner
        ]
        if len(default_runner_providers) != 1:
            configured_sources = ", ".join(
                f"{provider.provider_id} ({provider.source_label})"
                for provider in default_runner_providers
            ) or "none"
            raise ValueError(
                "Merged provider configuration must define exactly one provider with "
                f"default_for_runner=true; found {len(default_runner_providers)} "
                f"({configured_sources})"
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
