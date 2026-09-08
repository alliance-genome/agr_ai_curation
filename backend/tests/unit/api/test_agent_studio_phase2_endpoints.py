"""Tests for Phase 2 Agent Workshop endpoints."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.lib import http_errors


def test_get_models_endpoint_returns_sorted_models(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setattr(
        api_module,
        "list_model_definitions",
        lambda: [
            SimpleNamespace(
                model_id="gpt-5.4-mini",
                name="GPT-5.4 Mini",
                provider="openai",
                description="Fast",
                guidance="Fast guidance",
                default=False,
                supports_reasoning=True,
                supports_temperature=False,
                reasoning_options=["low", "medium", "high", "xhigh"],
                default_reasoning="high",
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
    assert response.models[0].model_id == "gpt-5.4-mini"
    assert response.models[0].default_reasoning == "high"


def test_get_tool_library_endpoint_returns_curator_visible_policy_rows(monkeypatch):
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
            ),
            SimpleNamespace(
                tool_key="stale_seeded_tool",
                display_name="Stale Tool",
                description="No installed binding",
                category="External API",
                curator_visible=True,
                allow_attach=True,
                allow_execute=True,
                config={},
            ),
        ],
    )

    monkeypatch.setattr(api_module, "get_tool_policy_cache", lambda: fake_service)

    response = asyncio.run(
        api_module.get_tool_library_endpoint(
            user={"sub": "test"},
            db=SimpleNamespace(),
        )
    )

    assert [tool.tool_key for tool in response.tools] == [
        "search_document",
        "stale_seeded_tool",
    ]
    assert [tool.config.requires_document for tool in response.tools] == [True, False]


def test_get_tool_library_endpoint_marks_document_tools_from_one_definition(monkeypatch):
    import src.api.agent_studio as api_module
    from src.lib.agent_studio.catalog_service import DOCUMENT_TOOL_IDS

    def _entry(tool_key, config):
        return SimpleNamespace(
            tool_key=tool_key,
            display_name=tool_key,
            description="",
            category="Document",
            curator_visible=True,
            allow_attach=True,
            allow_execute=True,
            config=config,
        )

    fake_service = SimpleNamespace(
        list_curator_visible=lambda _db: [
            *[_entry(tool_id, {"mode": "auto"}) for tool_id in sorted(DOCUMENT_TOOL_IDS)],
            _entry("chebi_lookup", {"requires_document": True}),
        ],
    )
    monkeypatch.setattr(api_module, "get_tool_policy_cache", lambda: fake_service)

    response = asyncio.run(
        api_module.get_tool_library_endpoint(user={"sub": "test"}, db=SimpleNamespace())
    )

    by_key = {tool.tool_key: tool for tool in response.tools}
    for tool_id in DOCUMENT_TOOL_IDS:
        assert by_key[tool_id].config.requires_document is True
        # Policy config keys other than the derived flag pass through untouched.
        assert by_key[tool_id].config.model_dump()["mode"] == "auto"
    # A stored policy value never overrides the tool registry definition.
    assert by_key["chebi_lookup"].config.requires_document is False
    payload = response.model_dump()["tools"]
    assert all("requires_document" in tool["config"] for tool in payload)


def test_get_agent_templates_endpoint_returns_system_templates_and_canonical_groups(monkeypatch):
    import src.api.agent_studio as api_module

    monkeypatch.setattr(
        api_module,
        "list_groups",
        lambda: [
            SimpleNamespace(group_id="RGD", name="Rat Genome Database"),
            SimpleNamespace(group_id="FB", name="FlyBase"),
        ],
    )

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
                    allowed_group_ids=[],
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
    assert response.templates[0].allowed_group_ids == []
    assert [(group.group_id, group.name) for group in response.group_options] == [
        ("FB", "FlyBase"),
        ("RGD", "Rat Genome Database"),
    ]


def test_get_models_endpoint_returns_500_on_loader_error(monkeypatch):
    import src.api.agent_studio as api_module

    calls = []

    def _raise():
        raise RuntimeError("models loader failed")

    def _fake_report_runtime_exception(exc, **kwargs):
        calls.append((exc, kwargs))
        return True

    monkeypatch.setattr(api_module, "list_model_definitions", _raise)
    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api_module.get_models_endpoint(user={"sub": "test"}))

    assert exc_info.value.status_code == 500
    assert "Failed to load model options" in str(exc_info.value.detail)
    assert len(calls) == 1
    assert calls[0][1]["operation"] == "sanitized_http_exception"


def test_get_tool_library_endpoint_returns_500_on_service_error(monkeypatch):
    import src.api.agent_studio as api_module

    calls = []

    def _fake_report_runtime_exception(exc, **kwargs):
        calls.append((exc, kwargs))
        return True

    fake_service = SimpleNamespace(
        list_curator_visible=lambda _db: (_ for _ in ()).throw(RuntimeError("tool policy cache unavailable"))
    )
    monkeypatch.setattr(api_module, "get_tool_policy_cache", lambda: fake_service)
    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.get_tool_library_endpoint(
                user={"sub": "test"},
                db=SimpleNamespace(),
            )
        )

    assert exc_info.value.status_code == 500
    assert "Failed to load tool library" in str(exc_info.value.detail)
    assert len(calls) == 1
    assert calls[0][1]["operation"] == "sanitized_http_exception"


def test_get_agent_templates_endpoint_returns_500_on_db_error(monkeypatch):
    import src.api.agent_studio as api_module

    calls = []

    def _fake_report_runtime_exception(exc, **kwargs):
        calls.append((exc, kwargs))
        return True

    fake_db = SimpleNamespace(
        query=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable"))
    )
    monkeypatch.setattr(http_errors, "report_runtime_exception", _fake_report_runtime_exception)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            api_module.get_agent_templates_endpoint(
                user={"sub": "test"},
                db=fake_db,
            )
        )

    assert exc_info.value.status_code == 500
    assert "Failed to load agent templates" in str(exc_info.value.detail)
    assert len(calls) == 1
    assert calls[0][1]["operation"] == "sanitized_http_exception"
