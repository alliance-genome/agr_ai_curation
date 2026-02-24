"""Tests for Phase 2 Agent Workshop endpoints."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def test_get_models_endpoint_returns_sorted_models(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setattr(
        api_module,
        "list_model_definitions",
        lambda: [
            SimpleNamespace(
                model_id="gpt-5-mini",
                name="GPT-5 Mini",
                provider="openai",
                description="Fast",
                guidance="Fast guidance",
                default=False,
                supports_reasoning=True,
                supports_temperature=False,
                reasoning_options=["low", "medium", "high"],
                default_reasoning="medium",
                reasoning_descriptions={"medium": "Balanced"},
                recommended_for=["Quick checks"],
                avoid_for=["Deep adjudication"],
            ),
            SimpleNamespace(
                model_id="gpt-4o",
                name="GPT-4o",
                provider="openai",
                description="Default",
                guidance="Default guidance",
                default=True,
                curator_visible=False,
                supports_reasoning=True,
                supports_temperature=True,
                reasoning_options=[],
                default_reasoning=None,
                reasoning_descriptions={},
                recommended_for=[],
                avoid_for=[],
            ),
        ],
    )

    response = asyncio.run(api_module.get_models_endpoint(user={"sub": "test"}))

    assert len(response.models) == 1
    assert response.models[0].model_id == "gpt-5-mini"
    assert response.models[0].default_reasoning == "medium"


def test_get_tool_library_endpoint_returns_curator_visible_tools():
    import src.api.agent_studio as api_module

    fake_service = SimpleNamespace(
        list_curator_visible=lambda _db: [
            SimpleNamespace(
                tool_key="search_document",
                display_name="Search Document",
                description="Search",
                category="Document",
                curator_visible=True,
                allow_attach=True,
                allow_execute=True,
                config={},
            )
        ]
    )

    api_module.get_tool_policy_cache = lambda: fake_service  # type: ignore

    response = asyncio.run(
        api_module.get_tool_library_endpoint(
            user={"sub": "test"},
            db=SimpleNamespace(),
        )
    )

    assert len(response.tools) == 1
    assert response.tools[0].tool_key == "search_document"


def test_get_agent_templates_endpoint_returns_system_templates():
    import src.api.agent_studio as api_module

    class _Query:
        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return [
                SimpleNamespace(
                    agent_key="gene",
                    name="Gene Specialist",
                    description="Gene helper",
                    icon="🧬",
                    category="Validation",
                    model_id="gpt-4o",
                    tool_ids=["agr_curation_query"],
                    output_schema_key=None,
                )
            ]

    fake_db = SimpleNamespace(query=lambda *_args, **_kwargs: _Query())

    response = asyncio.run(
        api_module.get_agent_templates_endpoint(
            user={"sub": "test"},
            db=fake_db,
        )
    )

    assert len(response.templates) == 1
    assert response.templates[0].agent_id == "gene"
    assert response.templates[0].model_id == "gpt-4o"


def test_get_models_endpoint_returns_500_on_loader_error(monkeypatch):
    import src.api.agent_studio as api_module

    def _raise():
        raise RuntimeError("models loader failed")

    monkeypatch.setattr(api_module, "list_model_definitions", _raise)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api_module.get_models_endpoint(user={"sub": "test"}))

    assert exc_info.value.status_code == 500
    assert "Failed to load model options" in str(exc_info.value.detail)


def test_get_tool_library_endpoint_returns_500_on_service_error(monkeypatch):
    import src.api.agent_studio as api_module

    fake_service = SimpleNamespace(
        list_curator_visible=lambda _db: (_ for _ in ()).throw(RuntimeError("tool policy cache unavailable"))
    )
    monkeypatch.setattr(api_module, "get_tool_policy_cache", lambda: fake_service)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.get_tool_library_endpoint(
                user={"sub": "test"},
                db=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 500
    assert "Failed to load tool library" in str(exc_info.value.detail)


def test_get_agent_templates_endpoint_returns_500_on_db_error():
    import src.api.agent_studio as api_module

    fake_db = SimpleNamespace(
        query=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable"))
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.get_agent_templates_endpoint(
                user={"sub": "test"},
                db=fake_db,
            )
        )

    assert exc_info.value.status_code == 500
    assert "Failed to load agent templates" in str(exc_info.value.detail)
