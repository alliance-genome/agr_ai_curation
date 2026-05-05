"""Unit tests for admin LLM provider health endpoint."""

import asyncio


def test_check_llm_providers_returns_report(monkeypatch):
    import src.api.admin.connections as api_module

    report = {
        "status": "degraded",
        "strict_mode": False,
        "validated_at": "2026-02-23T00:00:00+00:00",
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
        "validated_at": "2026-02-22T23:59:59+00:00",
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

    response = asyncio.run(api_module.check_llm_providers())

    assert response.status == "degraded"
    assert response.strict_mode is False
    assert len(response.providers) == 2
    assert response.providers[1].provider_id == "groq"
    assert response.summary.missing_key_provider_count == 1
    assert response.startup_report is not None
    assert response.startup_report["status"] == "healthy"
