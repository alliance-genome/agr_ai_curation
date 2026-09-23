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
async def test_resolve_document_hierarchy_handles_provider_figure_metadata_deterministically(
    monkeypatch,
) -> None:
    llm_inputs = []

    async def _fake_llm(section_info):
        llm_inputs.append(section_info)
        return (
            [
                hierarchy.SectionItem(
                    header="Results",
                    parent_section="Results",
                    subsection=None,
                    is_top_level=True,
                ),
            ],
            None,
            {"model": "stub"},
        )

    monkeypatch.setattr(hierarchy, "_call_llm_for_hierarchy", _fake_llm)
    monkeypatch.setenv("HIERARCHY_LLM_MODEL", "gpt-5.4-mini")

    elements = [
        {"metadata": {"section_title": "Results"}, "text": "Native result"},
        {
            "metadata": {"section_title": "Provider Figure Metadata"},
            "text": "Provider Figure Metadata",
        },
        {
            "metadata": {"section_title": "Provider Figure: Figure 1"},
            "text": "Fig. 1A shows wg expression.",
        },
    ]

    updated, metadata = await hierarchy.resolve_document_hierarchy(elements)

    assert llm_inputs == [[{"title": "Results", "preview": "Native result"}]]
    assert updated[1]["section_title"] == "Provider Figure Metadata"
    assert updated[1]["parent_section"] == "Provider Figure Metadata"
    assert updated[1]["subsection"] is None
    assert updated[2]["section_title"] == (
        "Provider Figure Metadata > Provider Figure: Figure 1"
    )
    assert updated[2]["parent_section"] == "Provider Figure Metadata"
    assert updated[2]["subsection"] == "Provider Figure: Figure 1"
    assert metadata is not None
    assert metadata.top_level_sections == ["Results", "Provider Figure Metadata"]
    assert "Provider Figure Metadata" in {
        item["parent_section"] for item in metadata.sections
    }


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
    from agents import AgentHooks
    # Resolve the real wrapper before substituting the deliberately small SDK
    # module, so this fixture also works when run without prior runner tests.
    from src.lib.openai_agents import runner as _runner  # noqa: F401
    captured = {}
    agents_module = types.ModuleType("agents")

    class FakeModelSettings:
        def __init__(self, temperature=None, reasoning=None):
            captured["temperature"] = temperature
            captured["reasoning"] = reasoning

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent_kwargs"] = kwargs
            self.name = kwargs.get("name")
            self.model = kwargs.get("model")
            self.instructions = kwargs.get("instructions")

    class FakeRunner:
        @staticmethod
        async def run(agent, user_prompt, max_turns, **_kwargs):
            captured["run_agent"] = agent
            captured["user_prompt"] = user_prompt
            captured["max_turns"] = max_turns
            if raise_error:
                raise RuntimeError("llm failed")
            return SimpleNamespace(final_output=final_output)

    agents_module.Agent = FakeAgent
    agents_module.AgentHooks = AgentHooks
    agents_module.Runner = FakeRunner
    agents_module.ModelSettings = FakeModelSettings

    shared_module = types.ModuleType("openai.types.shared")

    class FakeReasoning:
        def __init__(self, effort):
            self.effort = effort

    shared_module.Reasoning = FakeReasoning

    monkeypatch.setitem(sys.modules, "agents", agents_module)
    monkeypatch.setitem(sys.modules, "openai.types.shared", shared_module)
    monkeypatch.setattr(
        "src.lib.openai_agents.runner.run_agent_with_owned_openai_resources",
        FakeRunner.run,
    )
    return captured, FakeReasoning


class _FakeContextManager:
    def __init__(self, value=None):
        self.value = value

    def __enter__(self):
        if hasattr(self.value, "active"):
            self.value.active = True
        return self.value

    def __exit__(self, exc_type, exc, tb):
        if hasattr(self.value, "active"):
            self.value.active = False
        return None


@pytest.mark.asyncio
async def test_call_llm_for_hierarchy_success_with_structured_output(monkeypatch):
    output = hierarchy.HierarchyOutput(
        sections=[
            hierarchy.SectionClassification(idx=0, is_top_level=True),
        ],
        abstract_idx=0,
    )
    captured, fake_reasoning_cls = _install_fake_agent_modules(monkeypatch, final_output=output)
    sentry_calls = []

    class FakeSentrySpan:
        active = False

        def set_data(self, key, value):
            assert self.active, "Sentry span data must be written before span exit"
            sentry_calls.append(("data", key, value))

    def _fake_sentry_span(**kwargs):
        sentry_calls.append(("span", kwargs))
        return _FakeContextManager(FakeSentrySpan())

    monkeypatch.setattr(hierarchy, "gen_ai_invoke_agent_span", _fake_sentry_span)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("HIERARCHY_LLM_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("HIERARCHY_LLM_REASONING", "medium")
    monkeypatch.setenv("HIERARCHY_RESOLUTION_MAX_TURNS", "6")

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        [{"title": "Intro", "preview": "overview"}]
    )

    assert len(sections) == 1
    assert sections[0].header == "Intro"
    assert abstract_title == "Intro"
    assert raw["sections_count"] == 1
    assert raw["abstract_section_title"] == "Intro"
    assert captured["temperature"] is None
    assert captured["max_turns"] == 6
    assert isinstance(captured["reasoning"], fake_reasoning_cls)
    assert captured["reasoning"].effort == "medium"
    assert "Intro" in captured["user_prompt"]
    span_call = next(call for call in sentry_calls if call[0] == "span")
    assert span_call[1]["workflow"] == "hierarchy_resolution"
    assert span_call[1]["agent_key"] == "hierarchy_classifier"
    assert span_call[1]["input_preview"]["section_count"] == 1
    assert ("data", "ai_curation.validation.status", "accepted") in sentry_calls


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
    sentry_calls = []

    class FakeSentrySpan:
        active = False

        def set_data(self, key, value):
            assert self.active, "Sentry span data must be written before span exit"
            sentry_calls.append(("data", key, value))

    def _fake_sentry_span(**kwargs):
        sentry_calls.append(("span", kwargs))
        return _FakeContextManager(FakeSentrySpan())

    monkeypatch.setattr(hierarchy, "gen_ai_invoke_agent_span", _fake_sentry_span)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        [{"title": "Intro", "preview": "overview"}]
    )

    assert sections == []
    assert abstract_title is None
    assert raw is None
    assert ("data", "ai_curation.validation.status", "error") in sentry_calls
    assert (
        "data",
        "ai_curation.error.detail",
        {
            "message": "llm failed",
            "error_type": "RuntimeError",
            "phase": "hierarchy_resolution",
        },
    ) in sentry_calls


# Titles from one paper, in document order, as the application sends them.
_PAPER_SECTIONS = [
    {"title": "Loss of wg disrupts wing growth", "preview": "Jane Doe, John Roe"},
    {"title": "Abstract", "preview": "Wingless signaling controls growth."},
    {"title": "Materials and Methods", "preview": ""},
    {"title": "2.1. Fly strains", "preview": "Flies were raised at 25 C."},
    {"title": "2.2. Immunostaining", "preview": "Wing discs were fixed."},
    {"title": "Results", "preview": ""},
    {"title": "wg is required for growth", "preview": "Clones lacking wg were small."},
    {"title": "Discussion", "preview": "Our data show that wg is required."},
]

# The same classification the header-echo contract produced on this paper.
_EXPECTED_PAPER_ITEMS = [
    ("Loss of wg disrupts wing growth", "Loss of wg disrupts wing growth", None, True),
    ("Abstract", "Abstract", None, True),
    ("Materials and Methods", "Materials and Methods", None, True),
    ("2.1. Fly strains", "Materials and Methods", "2.1. Fly strains", False),
    ("2.2. Immunostaining", "Materials and Methods", "2.2. Immunostaining", False),
    ("Results", "Results", None, True),
    ("wg is required for growth", "Results", "wg is required for growth", False),
    ("Discussion", "Discussion", None, True),
]


def _paper_output(**overrides):
    sections = [
        {"idx": 0, "is_top_level": True, "parent_idx": None},
        {"idx": 1, "is_top_level": True, "parent_idx": None},
        {"idx": 2, "is_top_level": True, "parent_idx": None},
        {"idx": 3, "is_top_level": False, "parent_idx": 2},
        {"idx": 4, "is_top_level": False, "parent_idx": 2},
        {"idx": 5, "is_top_level": True, "parent_idx": None},
        {"idx": 6, "is_top_level": False, "parent_idx": 5},
        {"idx": 7, "is_top_level": True, "parent_idx": None},
    ]
    payload = {"sections": sections, "abstract_idx": 1}
    payload.update(overrides)
    return hierarchy.HierarchyOutput.model_validate(payload)


def _install_sequenced_runner(monkeypatch, outputs):
    captured, _ = _install_fake_agent_modules(monkeypatch, final_output=None)
    calls = []

    async def run(agent, user_prompt, max_turns, **_kwargs):
        calls.append(
            {"instructions": agent.instructions, "prompt": user_prompt}
        )
        return SimpleNamespace(final_output=outputs[len(calls) - 1])

    monkeypatch.setattr(
        "src.lib.openai_agents.runner.run_agent_with_owned_openai_resources",
        run,
    )
    return captured, calls


def _sentry_recorder(monkeypatch):
    sentry_calls = []

    class FakeSentrySpan:
        active = False

        def set_data(self, key, value):
            sentry_calls.append(("data", key, value))

    monkeypatch.setattr(
        hierarchy,
        "gen_ai_invoke_agent_span",
        lambda **kwargs: _FakeContextManager(FakeSentrySpan()),
    )
    return sentry_calls


@pytest.fixture
def hierarchy_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("HIERARCHY_LLM_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("HIERARCHY_LLM_REASONING", "low")
    monkeypatch.setenv("HIERARCHY_RESOLUTION_CONTRACT_RETRIES", "1")


def test_hierarchy_output_schema_has_indexes_not_echoed_titles():
    schema = hierarchy.HierarchyOutput.model_json_schema()
    item_schema = schema["$defs"][next(iter(schema["$defs"]))]

    assert set(schema["properties"]) == {"sections", "abstract_idx"}
    assert set(item_schema["properties"]) == {"idx", "is_top_level", "parent_idx"}
    property_names = set(schema["properties"]) | {
        name for definition in schema["$defs"].values()
        for name in definition.get("properties", {})
    }
    assert property_names.isdisjoint(
        {"header", "parent_section", "subsection", "abstract_section_title"}
    )


@pytest.mark.asyncio
async def test_call_llm_for_hierarchy_numbers_titles_and_maps_indexes_back(
    monkeypatch, hierarchy_env
):
    _captured, calls = _install_sequenced_runner(monkeypatch, [_paper_output()])
    _sentry_recorder(monkeypatch)

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        _PAPER_SECTIONS
    )

    assert [
        (item.header, item.parent_section, item.subsection, item.is_top_level)
        for item in sections
    ] == _EXPECTED_PAPER_ITEMS
    assert abstract_title == "Abstract"
    assert raw["sections_count"] == len(_PAPER_SECTIONS)
    assert raw["abstract_section_title"] == "Abstract"
    assert len(calls) == 1
    assert '[0] "Loss of wg disrupts wing growth"' in calls[0]["prompt"]
    assert '[7] "Discussion"' in calls[0]["prompt"]


@pytest.mark.asyncio
async def test_resolve_document_hierarchy_applies_index_output_to_elements(
    monkeypatch, hierarchy_env
):
    _install_sequenced_runner(monkeypatch, [_paper_output()])
    _sentry_recorder(monkeypatch)
    elements = [
        {"metadata": {"section_title": info["title"]}, "text": info["preview"]}
        for info in _PAPER_SECTIONS
    ]

    updated, metadata = await hierarchy.resolve_document_hierarchy(elements)

    assert updated[3]["section_title"] == "Materials and Methods > 2.1. Fly strains"
    assert updated[3]["section_path"] == ["Materials and Methods", "2.1. Fly strains"]
    assert updated[6]["parent_section"] == "Results"
    assert updated[6]["subsection"] == "wg is required for growth"
    assert updated[7]["section_path"] == ["Discussion"]
    assert metadata is not None
    assert metadata.abstract_section_title == "Abstract"
    assert metadata.top_level_sections == [
        "Loss of wg disrupts wing growth",
        "Abstract",
        "Materials and Methods",
        "Results",
        "Discussion",
    ]


_INVALID_SECTION_INDEX_OUTPUTS = {
    "missing": {"sections": [{"idx": 0, "is_top_level": True, "parent_idx": None}]},
    "duplicate": {
        "sections": [
            {"idx": i if i < 7 else 6, "is_top_level": True, "parent_idx": None}
            for i in range(8)
        ]
    },
    "out_of_range": {
        "sections": [
            {"idx": i + 1, "is_top_level": True, "parent_idx": None} for i in range(8)
        ]
    },
    "negative": {
        "sections": [
            {"idx": i - 1, "is_top_level": True, "parent_idx": None} for i in range(8)
        ]
    },
    "parent_is_subsection": {
        "sections": [
            {"idx": i, "is_top_level": i != 3 and i != 4, "parent_idx": 3 if i == 4 else (2 if i == 3 else None)}
            for i in range(8)
        ]
    },
    "parent_out_of_range": {
        "sections": [
            {"idx": i, "is_top_level": i != 3, "parent_idx": 9 if i == 3 else None}
            for i in range(8)
        ]
    },
    "subsection_without_parent": {
        "sections": [
            {"idx": i, "is_top_level": i != 3, "parent_idx": None} for i in range(8)
        ]
    },
    "top_level_with_parent": {
        "sections": [
            {"idx": i, "is_top_level": True, "parent_idx": 0 if i == 1 else None}
            for i in range(8)
        ]
    },
    "abstract_out_of_range": {"abstract_idx": 8},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_INVALID_SECTION_INDEX_OUTPUTS))
async def test_invalid_section_indexes_fail_explicitly_after_contract_retries(
    monkeypatch, hierarchy_env, case
):
    invalid = _paper_output(**_INVALID_SECTION_INDEX_OUTPUTS[case])
    _captured, calls = _install_sequenced_runner(monkeypatch, [invalid, invalid])
    sentry_calls = _sentry_recorder(monkeypatch)

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        _PAPER_SECTIONS
    )

    assert (sections, abstract_title, raw) == ([], None, None)
    assert len(calls) == 2
    assert "Correction required" not in calls[0]["instructions"]
    assert "Correction required" in calls[1]["instructions"]
    statuses = [
        call[2] for call in sentry_calls if call[1] == "ai_curation.validation.status"
    ]
    assert statuses == ["retrying", "error"]
    detail = next(
        call[2] for call in sentry_calls if call[1] == "ai_curation.error.detail"
    )
    assert "section index contract" in detail["message"]


@pytest.mark.asyncio
async def test_section_index_contract_correction_recovers(monkeypatch, hierarchy_env):
    invalid = _paper_output(**_INVALID_SECTION_INDEX_OUTPUTS["missing"])
    _captured, calls = _install_sequenced_runner(
        monkeypatch, [invalid, _paper_output()]
    )
    sentry_calls = _sentry_recorder(monkeypatch)

    sections, abstract_title, _raw = await hierarchy._call_llm_for_hierarchy(
        _PAPER_SECTIONS
    )

    assert len(sections) == len(_PAPER_SECTIONS)
    assert abstract_title == "Abstract"
    assert len(calls) == 2
    assert calls[0]["prompt"] == calls[1]["prompt"]
    statuses = [
        call[2] for call in sentry_calls if call[1] == "ai_curation.validation.status"
    ]
    assert statuses == ["retrying", "accepted"]
