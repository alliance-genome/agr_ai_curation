"""Tests for config.providers_loader."""

from pathlib import Path

import pytest


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
    driver: litellm
    api_key_env: GROQ_API_KEY
    base_url_env: GROQ_BASE_URL
    default_base_url: https://api.groq.com/openai/v1
    litellm_prefix: groq
    drop_params: true
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
    assert loaded["groq"].driver == "litellm"
    assert loaded["groq"].litellm_prefix == "groq"
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
    driver: litellm
    api_key_env: GROQ_API_KEY
    litellm_prefix: groq
    default_for_runner: false
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exactly one provider with default_for_runner=true"):
        providers_loader_module.load_providers(providers_path=config_path, force_reload=True)


def test_litellm_provider_requires_prefix(tmp_path: Path):
    import src.lib.config.providers_loader as providers_loader_module

    config_path = tmp_path / "providers.yaml"
    config_path.write_text(
        """
providers:
  openai:
    driver: openai_native
    api_key_env: OPENAI_API_KEY
    default_for_runner: true
  bad_litellm:
    driver: litellm
    api_key_env: BAD_KEY
        """.strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires 'litellm_prefix'"):
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
    driver: litellm
    api_key_env: ORG_KEY
    litellm_prefix: org
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

    assert loaded["shared"].driver == "litellm"
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
    driver: litellm
    api_key_env: GROQ_API_KEY
    litellm_prefix: groq
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
    assert loaded["groq"].driver == "litellm"


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
