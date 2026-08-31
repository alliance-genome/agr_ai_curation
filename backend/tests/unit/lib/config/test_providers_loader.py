"""Tests for config.providers_loader."""

from pathlib import Path

import pytest
import yaml


@pytest.fixture(autouse=True)
def reset_provider_cache():
    import src.lib.config.providers_loader as providers_loader_module

    providers_loader_module.reset_cache()
    yield
    providers_loader_module.reset_cache()


def _write_provider_package(
    packages_dir: Path,
    *,
    directory_name: str,
    package_id: str,
    providers_text: str,
) -> None:
    package_dir = packages_dir / directory_name
    (package_dir / "config").mkdir(parents=True)
    (package_dir / "requirements").mkdir(parents=True)
    (package_dir / "requirements" / "runtime.txt").write_text("", encoding="utf-8")
    (package_dir / "package.yaml").write_text(
        f"""package_id: {package_id}
display_name: {package_id} package
version: 1.0.0
package_api_version: 1.0.0
min_runtime_version: 1.0.0
max_runtime_version: 2.0.0
python_package_root: src/{package_id.replace('.', '_')}
requirements_file: requirements/runtime.txt
exports:
  - kind: provider
    name: default_providers
    path: config/providers.yaml
    description: Default providers
""",
        encoding="utf-8",
    )
    (package_dir / "config" / "providers.yaml").write_text(
        providers_text.strip() + "\n",
        encoding="utf-8",
    )


def test_shipped_provider_catalogs_match_direct_driver_contract():
    from src.lib.config.providers_loader import ProviderDefinition

    repo_root = Path(__file__).resolve().parents[5]
    deployment_catalog = yaml.safe_load(
        (repo_root / "config" / "providers.yaml").read_text(encoding="utf-8")
    )
    package_catalog = yaml.safe_load(
        (repo_root / "packages" / "core" / "config" / "providers.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert deployment_catalog == package_catalog
    providers = deployment_catalog["providers"]
    assert providers["openai"]["driver"] == "openai_native"
    assert providers["openai"]["api_mode"] == "responses"
    assert providers["openai"]["default_for_runner"] is True
    for provider_id in ("gemini", "groq", "openrouter"):
        provider = ProviderDefinition.from_yaml(
            provider_id,
            providers[provider_id],
            source_label="shipped provider catalog",
        )
        assert provider.driver == "openai_compatible"
        assert provider.api_mode == "chat_completions"
        assert provider.default_base_url
        assert provider.supports_parallel_tool_calls is True
    openrouter = ProviderDefinition.from_yaml(
        "openrouter",
        providers["openrouter"],
        source_label="shipped provider catalog",
    )
    assert openrouter.optional_for_runtime is True
    assert openrouter.request_extra_body == {
        "provider": {"allow_fallbacks": False, "require_parameters": True}
    }
    assert openrouter.request_headers == {"X-OpenRouter-Metadata": "enabled"}
    assert openrouter.forbidden_request_fields == ("models", "fallbacks")
    assert openrouter.omit_usage_request is True
    assert openrouter.telemetry_adapter == "openrouter"


def test_openrouter_adapter_requires_strict_routing_and_metadata_policy(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  openai:
    driver: openai_native
    api_key_env: OPENAI_API_KEY
    default_for_runner: true
  openrouter:
    driver: openai_compatible
    api_key_env: OPENROUTER_API_KEY
    default_base_url: https://openrouter.ai/api/v1
    api_mode: chat_completions
    optional_for_runtime: true
    telemetry:
      adapter: openrouter
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing required no-fallback"):
        providers_loader_module.load_providers(
            providers_path=config_path,
            packages_dir=tmp_path / "missing-packages",
            force_reload=True,
        )


def test_load_providers_reads_yaml(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  openai:
    driver: openai_native
    api_key_env: OPENAI_API_KEY
    api_mode: responses
    default_for_runner: true
    supports:
      parallel_tool_calls: true
  groq:
    driver: openai_compatible
    api_key_env: GROQ_API_KEY
    base_url_env: GROQ_BASE_URL
    default_base_url: https://api.groq.com/openai/v1
    api_mode: chat_completions
    supports:
      parallel_tool_calls: false
        """.strip(),
        encoding="utf-8",
    )

    loaded = providers_loader_module.load_providers(
        providers_path=config_path,
        packages_dir=tmp_path / "missing-packages",
        force_reload=True,
    )

    assert "openai" in loaded
    assert "groq" in loaded
    assert loaded["groq"].driver == "openai_compatible"
    assert loaded["groq"].api_mode == "chat_completions"
    assert loaded["groq"].supports_parallel_tool_calls is False
    assert loaded["openai"].source_label == f"runtime override 'providers.yaml' at {config_path}"
    assert providers_loader_module.get_default_runner_provider().provider_id == "openai"


def test_load_providers_requires_exactly_one_default(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  openai:
    driver: openai_native
    api_key_env: OPENAI_API_KEY
    default_for_runner: false
  groq:
    driver: openai_compatible
    api_key_env: GROQ_API_KEY
    default_base_url: https://api.groq.com/openai/v1
    api_mode: chat_completions
    default_for_runner: false
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one provider with default_for_runner=true"):
        providers_loader_module.load_providers(providers_path=config_path, force_reload=True)


def test_openai_compatible_provider_requires_base_url_config(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  openai:
    driver: openai_native
    api_key_env: OPENAI_API_KEY
    default_for_runner: true
  incomplete:
    driver: openai_compatible
    api_key_env: BAD_KEY
    api_mode: chat_completions
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires 'base_url_env' or 'default_base_url'"):
        providers_loader_module.load_providers(providers_path=config_path, force_reload=True)


def test_openai_compatible_provider_requires_explicit_api_mode(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  openai:
    driver: openai_native
    api_key_env: OPENAI_API_KEY
    default_for_runner: true
  incomplete:
    driver: openai_compatible
    api_key_env: COMPATIBLE_KEY
    default_base_url: https://compatible.example/v1
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires 'api_mode'"):
        providers_loader_module.load_providers(providers_path=config_path, force_reload=True)


def test_load_providers_uses_sorted_package_order_for_collisions(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    packages_dir = tmp_path / "packages"
    override_path = tmp_path / "providers.yaml"
    _write_provider_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        providers_text="""
providers:
  shared:
    driver: openai_native
    api_key_env: BASE_KEY
    default_for_runner: true
""",
    )
    _write_provider_package(
        packages_dir,
        directory_name="org-custom",
        package_id="org.custom",
        providers_text="""
providers:
  shared:
    driver: openai_compatible
    api_key_env: ORG_KEY
    default_base_url: https://org.example/v1
    api_mode: chat_completions
    default_for_runner: true
""",
    )
    override_path.write_text(
        """
providers:
  runtime-shadow:
    driver: openai_native
    api_key_env: RUNTIME_SHADOW_KEY
    default_for_runner: false
""".strip(),
        encoding="utf-8",
    )

    loaded = providers_loader_module.load_providers(
        providers_path=override_path,
        packages_dir=packages_dir,
        force_reload=True,
    )

    assert loaded["shared"].driver == "openai_compatible"
    assert loaded["shared"].api_key_env == "ORG_KEY"
    assert loaded["shared"].source_label is not None
    assert "package default 'org.custom'" in loaded["shared"].source_label


def test_load_providers_runtime_override_wins_over_package_defaults(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    packages_dir = tmp_path / "packages"
    override_path = tmp_path / "providers.yaml"
    _write_provider_package(
        packages_dir,
        directory_name="agr-base",
        package_id="agr.base",
        providers_text="""
providers:
  openai:
    driver: openai_native
    api_key_env: BASE_OPENAI_KEY
    default_for_runner: true
""",
    )
    override_path.write_text(
        """
providers:
  openai:
    driver: openai_native
    api_key_env: RUNTIME_OPENAI_KEY
    default_for_runner: true
  groq:
    driver: openai_compatible
    api_key_env: GROQ_API_KEY
    default_base_url: https://api.groq.com/openai/v1
    api_mode: chat_completions
    default_for_runner: false
""".strip(),
        encoding="utf-8",
    )

    loaded = providers_loader_module.load_providers(
        providers_path=override_path,
        packages_dir=packages_dir,
        force_reload=True,
    )

    assert loaded["openai"].api_key_env == "RUNTIME_OPENAI_KEY"
    assert loaded["openai"].source_label == (
        f"runtime override 'providers.yaml' at {override_path}"
    )
    assert loaded["groq"].driver == "openai_compatible"


def test_load_providers_reports_runtime_override_source_on_invalid_entry(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  openai:
    driver: openai_native
    default_for_runner: true
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime override 'providers.yaml'") as exc_info:
        providers_loader_module.load_providers(providers_path=config_path, force_reload=True)

    assert str(config_path) in str(exc_info.value)
