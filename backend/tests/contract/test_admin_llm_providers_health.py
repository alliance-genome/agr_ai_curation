"""Contract tests for GET /api/admin/health/llm-providers."""

import pytest


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL_TOKEN_LIMIT", "8191")
    monkeypatch.setenv("EMBEDDING_TOKEN_SAFETY_MARGIN", "500")
    monkeypatch.setenv("CONTENT_PREVIEW_CHARS", "1600")

    from fastapi.testclient import TestClient
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from main import app

    return TestClient(app)


def test_llm_providers_health_contract_shape(client, monkeypatch):
    report = {
        "status": "degraded",
        "strict_mode": False,
        "validated_at": "2026-02-26T00:00:00+00:00",
        "errors": [],
        "warnings": ["Provider 'groq' missing GROQ_API_KEY"],
        "providers": [
            {
                "provider_id": "openai",
                "driver": "openai_native",
                "api_mode": "responses",
                "api_key_env": "OPENAI_API_KEY",
                "api_key_present": True,
                "base_url_env": "OPENAI_BASE_URL",
                "base_url_configured": False,
                "default_for_runner": True,
                "mapped_model_ids": ["gpt-5.4-mini"],
                "mapped_curator_visible_model_ids": ["gpt-5.4-mini"],
                "supports_parallel_tool_calls": True,
                "readiness": "ready",
            },
            {
                "provider_id": "groq",
                "driver": "litellm",
                "api_mode": "responses",
                "api_key_env": "GROQ_API_KEY",
                "api_key_present": False,
                "base_url_env": "GROQ_BASE_URL",
                "base_url_configured": True,
                "default_for_runner": False,
                "mapped_model_ids": ["openai/gpt-oss-120b"],
                "mapped_curator_visible_model_ids": ["openai/gpt-oss-120b"],
                "supports_parallel_tool_calls": True,
                "readiness": "missing_api_key",
            },
        ],
        "models": [
            {
                "model_id": "gpt-5.4-mini",
                "provider_id": "openai",
                "provider_exists": True,
                "curator_visible": True,
            }
        ],
        "summary": {
            "provider_count": 2,
            "model_count": 1,
            "ready_provider_count": 1,
            "missing_key_provider_count": 1,
            "mapped_model_count": 1,
        },
    }
    startup_report = {
        "status": "healthy",
        "validated_at": "2026-02-25T00:00:00+00:00",
        "errors": [],
        "warnings": [],
        "providers": [],
        "models": [],
        "summary": {},
        "strict_mode": True,
    }

    monkeypatch.setattr(
        "src.lib.config.provider_validation.build_provider_runtime_report",
        lambda: report,
    )
    monkeypatch.setattr(
        "src.lib.config.provider_validation.get_startup_provider_validation_report",
        lambda: startup_report,
    )

    response = client.get("/api/admin/health/llm-providers")
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "degraded"
    assert data["strict_mode"] is False
    assert data["summary"]["provider_count"] == 2
    assert data["summary"]["missing_key_provider_count"] == 1
    assert len(data["providers"]) == 2
    assert data["providers"][0]["provider_id"] == "openai"
    assert data["providers"][1]["provider_id"] == "groq"
    assert data["startup_report"]["status"] == "healthy"
    # Contract should expose env names/presence only; never raw secrets.
    assert "api_key" not in data["providers"][0]


def test_llm_providers_health_reports_unhealthy_when_errors_exist(client, monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.provider_validation.build_provider_runtime_report",
        lambda: {
            "status": "unhealthy",
            "strict_mode": True,
            "validated_at": "2026-02-26T00:00:00+00:00",
            "errors": ["Default provider is not configured"],
            "warnings": [],
            "providers": [],
            "models": [],
            "summary": {
                "provider_count": 0,
                "model_count": 0,
                "ready_provider_count": 0,
                "missing_key_provider_count": 0,
                "mapped_model_count": 0,
            },
        },
    )
    monkeypatch.setattr(
        "src.lib.config.provider_validation.get_startup_provider_validation_report",
        lambda: None,
    )

    response = client.get("/api/admin/health/llm-providers")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unhealthy"
    assert data["strict_mode"] is True
    assert data["errors"] == ["Default provider is not configured"]
    assert data["providers"] == []
    assert data["models"] == []
    assert data["startup_report"] is None
