"""Tests for config.providers_loader."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_provider_cache():
    import src.lib.config.providers_loader as providers_loader_module

    providers_loader_module.reset_cache()
    yield
    providers_loader_module.reset_cache()


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
        force_reload=True,
    )

    assert "openai" in loaded
    assert "groq" in loaded
    assert loaded["groq"].driver == "litellm"
    assert loaded["groq"].litellm_prefix == "groq"
    assert loaded["groq"].supports_parallel_tool_calls is False
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
