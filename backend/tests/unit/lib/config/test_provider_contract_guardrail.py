"""CI guardrail tests for provider/model contract drift."""

from __future__ import annotations

import json

import pytest

from src.lib.config import (
    reset_models_cache,
    reset_providers_cache,
    validate_provider_runtime_contracts,
)


@pytest.fixture(autouse=True)
def reset_config_caches():
    """Keep loader cache state isolated for contract validation assertions."""
    reset_models_cache()
    reset_providers_cache()
    yield
    reset_models_cache()
    reset_providers_cache()


def test_repo_provider_model_contracts_have_no_structural_errors(monkeypatch):
    """Real config guardrail: drift between models/providers must fail CI."""
    # Guard against external path overrides influencing CI/runtime checks.
    monkeypatch.delenv("MODELS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PROVIDERS_CONFIG_PATH", raising=False)

    is_valid, report = validate_provider_runtime_contracts(strict_mode=False)
    assert is_valid, (
        "LLM provider/model contract drift detected. "
        f"Errors: {json.dumps(report.get('errors', []), indent=2)}"
    )
    assert report.get("errors", []) == []
    assert report.get("summary", {}).get("model_count", 0) > 0
    assert report.get("summary", {}).get("provider_count", 0) > 0
