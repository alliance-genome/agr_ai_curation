"""Unit tests for hierarchy resolution helpers."""

import sys
import types
from types import SimpleNamespace

import pytest

from src.lib.pipeline import hierarchy_resolution as hierarchy


@pytest.mark.asyncio
async def test_resolve_document_hierarchy_returns_none_when_no_section_titles():
    elements = [
        {"metadata": {}, "text": "No section title here"},
        {"text": "Also missing metadata"},
    ]

    updated, metadata = await hierarchy.resolve_document_hierarchy(elements)

    assert updated == elements
    assert metadata is None


@pytest.mark.asyncio
async def test_resolve_document_hierarchy_applies_classification_and_metadata(monkeypatch):
    async def _fake_llm(_section_info):
        return (
            [
                hierarchy.SectionItem(
                    header="Intro",
                    parent_section="Introduction",
                    subsection=None,
                    is_top_level=True,
                ),
                hierarchy.SectionItem(
                    header="Fly Strains",
                    parent_section="Methods",
                    subsection="Fly Strains",
                    is_top_level=False,
                ),
            ],
            "Intro",
            {"model": "stub"},
        )

    monkeypatch.setattr(hierarchy, "_call_llm_for_hierarchy", _fake_llm)
    monkeypatch.setenv("HIERARCHY_LLM_MODEL", "gpt-5.4-mini")

    elements = [
        {"metadata": {"section_title": "Intro"}, "text": "Overview"},
        {"metadata": {"section_title": "Fly Strains"}, "text": "Methods details"},
    ]

    updated, metadata = await hierarchy.resolve_document_hierarchy(elements, store_metadata=True)

    assert updated[0]["metadata"]["parent_section"] == "Introduction"
    assert updated[0]["metadata"]["subsection"] is None
    assert updated[0]["section_path"] == ["Introduction"]

    assert updated[1]["metadata"]["parent_section"] == "Methods"
    assert updated[1]["metadata"]["subsection"] == "Fly Strains"
    assert updated[1]["section_title"] == "Methods > Fly Strains"
    assert updated[1]["section_path"] == ["Methods", "Fly Strains"]

    assert metadata is not None
    assert metadata.top_level_sections == ["Introduction"]
    assert metadata.abstract_section_title == "Intro"
    assert metadata.llm_raw_response == {"model": "stub"}
    assert metadata.model_used == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_resolve_document_hierarchy_falls_back_on_empty_llm_result(monkeypatch):
    async def _fake_llm(_section_info):
        return ([], None, None)

    monkeypatch.setattr(hierarchy, "_call_llm_for_hierarchy", _fake_llm)

    elements = [{"metadata": {"section_title": "Intro"}, "text": "x"}]
    updated, metadata = await hierarchy.resolve_document_hierarchy(elements)

    assert updated == elements
    assert metadata is None


@pytest.mark.asyncio
async def test_resolve_document_hierarchy_can_skip_metadata_storage(monkeypatch):
    async def _fake_llm(_section_info):
        return (
            [
                hierarchy.SectionItem(
                    header="Intro",
                    parent_section="Introduction",
                    subsection=None,
                    is_top_level=True,
                )
            ],
            None,
            {"raw": True},
        )

    monkeypatch.setattr(hierarchy, "_call_llm_for_hierarchy", _fake_llm)
    elements = [{"metadata": {"section_title": "Intro"}, "text": "x"}]

    _updated, metadata = await hierarchy.resolve_document_hierarchy(elements, store_metadata=False)
    assert metadata is None


@pytest.mark.asyncio
async def test_call_llm_for_hierarchy_returns_empty_when_api_key_missing(monkeypatch):
    _install_fake_agent_modules(monkeypatch, final_output=None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        [{"title": "Intro", "preview": "hello"}]
    )

    assert sections == []
    assert abstract_title is None
    assert raw is None


def _install_fake_agent_modules(monkeypatch, final_output, raise_error=False):
    captured = {}
    agents_module = types.ModuleType("agents")

    class FakeModelSettings:
        def __init__(self, temperature=None, reasoning=None):
            captured["temperature"] = temperature
            captured["reasoning"] = reasoning

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs

    class FakeRunner:
        @staticmethod
        async def run(agent, user_prompt):
            captured["run_agent"] = agent
            captured["user_prompt"] = user_prompt
            if raise_error:
                raise RuntimeError("llm failed")
            return SimpleNamespace(final_output=final_output)

    agents_module.Agent = FakeAgent
    agents_module.Runner = FakeRunner
    agents_module.ModelSettings = FakeModelSettings

    shared_module = types.ModuleType("openai.types.shared")

    class FakeReasoning:
        def __init__(self, effort):
            self.effort = effort

    shared_module.Reasoning = FakeReasoning

    monkeypatch.setitem(sys.modules, "agents", agents_module)
    monkeypatch.setitem(sys.modules, "openai.types.shared", shared_module)
    return captured, FakeReasoning


@pytest.mark.asyncio
async def test_call_llm_for_hierarchy_success_with_structured_output(monkeypatch):
    output = hierarchy.HierarchyOutput(
        sections=[
            hierarchy.SectionItem(
                header="Intro",
                parent_section="Introduction",
                subsection=None,
                is_top_level=True,
            )
        ],
        abstract_section_title="Intro",
    )
    captured, fake_reasoning_cls = _install_fake_agent_modules(monkeypatch, final_output=output)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("HIERARCHY_LLM_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("HIERARCHY_LLM_REASONING", "medium")

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        [{"title": "Intro", "preview": "overview"}]
    )

    assert len(sections) == 1
    assert sections[0].header == "Intro"
    assert abstract_title == "Intro"
    assert raw["sections_count"] == 1
    assert raw["abstract_section_title"] == "Intro"
    assert captured["temperature"] is None
    assert isinstance(captured["reasoning"], fake_reasoning_cls)
    assert captured["reasoning"].effort == "medium"
    assert "Intro" in captured["user_prompt"]


@pytest.mark.asyncio
async def test_call_llm_for_hierarchy_handles_empty_final_output(monkeypatch):
    captured, _ = _install_fake_agent_modules(monkeypatch, final_output=None)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("HIERARCHY_LLM_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("HIERARCHY_LLM_REASONING", "low")

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        [{"title": "Intro", "preview": "overview"}]
    )

    assert sections == []
    assert abstract_title is None
    assert raw is not None
    assert raw["model"] == "gpt-5.4-mini"
    assert captured["temperature"] is None


@pytest.mark.asyncio
async def test_call_llm_for_hierarchy_handles_runtime_exception(monkeypatch):
    _install_fake_agent_modules(monkeypatch, final_output=None, raise_error=True)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        [{"title": "Intro", "preview": "overview"}]
    )

    assert sections == []
    assert abstract_title is None
    assert raw is None
