"""Provider catalog loader with package-default and runtime-override merging."""

import logging
import threading
from dataclasses import dataclass, field
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
    api_mode: str = "responses"
    default_for_runner: bool = False
    optional_for_runtime: bool = False
    supports_parallel_tool_calls: bool = True
    request_extra_body: Dict[str, Any] = field(default_factory=dict)
    request_headers: Dict[str, str] = field(default_factory=dict)
    forbidden_request_fields: tuple[str, ...] = ()
    omit_usage_request: bool = False
    telemetry_adapter: Optional[str] = None
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
        if driver not in {"openai_native", "openai_compatible"}:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} has invalid driver '{driver}'. "
                "Supported: openai_native, openai_compatible"
            )

        api_key_env = str(data.get("api_key_env", "")).strip()
        if not api_key_env:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} is missing required field "
                f"'api_key_env'"
            )

        configured_api_mode = str(data.get("api_mode", "")).strip().lower()
        if driver == "openai_compatible" and not configured_api_mode:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} with "
                "driver=openai_compatible requires 'api_mode'"
            )
        api_mode = configured_api_mode or "responses"
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

        base_url_env = str(data.get("base_url_env", "")).strip() or None
        default_base_url = str(data.get("default_base_url", "")).strip() or None
        if driver == "openai_compatible" and not (base_url_env or default_base_url):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} with "
                "driver=openai_compatible requires 'base_url_env' or "
                "'default_base_url'"
            )

        request = data.get("request", {})
        if request is None:
            request = {}
        if not isinstance(request, dict):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} field 'request' must be a mapping"
            )
        request_extra_body = request.get("extra_body", {})
        if request_extra_body is None:
            request_extra_body = {}
        if not isinstance(request_extra_body, dict):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} field "
                "'request.extra_body' must be a mapping"
            )
        request_headers = request.get("headers", {})
        if request_headers is None:
            request_headers = {}
        if not isinstance(request_headers, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in request_headers.items()
        ):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} field "
                "'request.headers' must map strings to strings"
            )
        raw_forbidden_fields = request.get("forbidden_fields", [])
        if raw_forbidden_fields is None:
            raw_forbidden_fields = []
        if not isinstance(raw_forbidden_fields, list) or any(
            not isinstance(value, str) or not value.strip()
            for value in raw_forbidden_fields
        ):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} field "
                "'request.forbidden_fields' must be a list of non-empty strings"
            )
        omit_usage_request = bool(request.get("omit_usage_request", False))

        telemetry = data.get("telemetry", {})
        if telemetry is None:
            telemetry = {}
        if not isinstance(telemetry, dict):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} field 'telemetry' must be a mapping"
            )
        telemetry_adapter = str(telemetry.get("adapter", "")).strip().lower() or None
        if telemetry_adapter not in {None, "openrouter"}:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} has unsupported telemetry "
                f"adapter '{telemetry_adapter}'"
            )
        if telemetry_adapter == "openrouter":
            provider_policy = request_extra_body.get("provider")
            required_forbidden = {"models", "fallbacks"}
            if (
                not isinstance(provider_policy, dict)
                or provider_policy.get("allow_fallbacks") is not False
                or provider_policy.get("require_parameters") is not True
                or request_headers.get("X-OpenRouter-Metadata") != "enabled"
                or not required_forbidden.issubset(set(raw_forbidden_fields))
                or not omit_usage_request
            ):
                raise ValueError(
                    f"Provider '{provider_id}' in {source_label} uses the OpenRouter "
                    "telemetry adapter but is missing required no-fallback, "
                    "required-parameter, metadata, forbidden-fallback, or automatic-usage policy"
                )
        if api_mode != "chat_completions" and (
            request_extra_body or request_headers or telemetry_adapter
        ):
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} configures request policy or "
                "telemetry that requires api_mode=chat_completions"
            )

        optional_for_runtime = bool(data.get("optional_for_runtime", False))
        default_for_runner = bool(data.get("default_for_runner", False))
        if optional_for_runtime and default_for_runner:
            raise ValueError(
                f"Provider '{provider_id}' in {source_label} cannot be both optional_for_runtime "
                "and default_for_runner"
            )

        return cls(
            provider_id=provider_id,
            driver=driver,
            api_key_env=api_key_env,
            base_url_env=base_url_env,
            default_base_url=default_base_url,
            api_mode=api_mode,
            default_for_runner=default_for_runner,
            optional_for_runtime=optional_for_runtime,
            supports_parallel_tool_calls=bool(supports.get("parallel_tool_calls", True)),
            request_extra_body=dict(request_extra_body),
            request_headers=dict(request_headers),
            forbidden_request_fields=tuple(
                value.strip() for value in raw_forbidden_fields
            ),
            omit_usage_request=omit_usage_request,
            telemetry_adapter=telemetry_adapter,
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
