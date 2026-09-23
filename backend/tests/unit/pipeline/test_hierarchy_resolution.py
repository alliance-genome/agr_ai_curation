"""Unit tests for hierarchy resolution helpers."""

import sys
import types
from types import SimpleNamespace

import pytest

from src.lib.observability.cost_context import cost_scope
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

# Expected classification: every section keeps its own title, and each
# subsection's parent is the paper's own top-level heading.
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
    "parent_chain_loops": {
        "sections": [
            {"idx": i, "is_top_level": i not in (3, 4), "parent_idx": {3: 4, 4: 3}.get(i)}
            for i in range(8)
        ]
    },
    "parent_is_self": {
        "sections": [
            {"idx": i, "is_top_level": i != 3, "parent_idx": 3 if i == 3 else None}
            for i in range(8)
        ]
    },
    "parent_chain_ends_without_top_level": {
        "sections": [
            {"idx": i, "is_top_level": i not in (3, 4), "parent_idx": 3 if i == 4 else None}
            for i in range(8)
        ]
    },
    "parent_chain_ends_out_of_range": {
        "sections": [
            {"idx": i, "is_top_level": i not in (3, 4), "parent_idx": {3: 12, 4: 3}.get(i)}
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


def _contract_report_recorder(monkeypatch):
    reports = []

    def _record(violation, **kwargs):
        reports.append((violation, kwargs))
        return True

    monkeypatch.setattr(hierarchy, "report_payload_contract_violation", _record)
    return reports


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(_INVALID_SECTION_INDEX_OUTPUTS))
async def test_invalid_section_indexes_fail_explicitly_after_contract_retries(
    monkeypatch, hierarchy_env, case
):
    invalid = _paper_output(**_INVALID_SECTION_INDEX_OUTPUTS[case])
    _captured, calls = _install_sequenced_runner(monkeypatch, [invalid, invalid])
    sentry_calls = _sentry_recorder(monkeypatch)
    reports = _contract_report_recorder(monkeypatch)

    with cost_scope({"document_id": "doc-1", "job_id": "job-1"}):
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
    assert ("data", "ai_curation.validation.retry_count", 1) in sentry_calls

    assert len(reports) == 1
    violation, kwargs = reports[0]
    assert violation.category == "contract_serialization_failure"
    assert violation.component == "hierarchy_resolution"
    assert violation.setting == "HIERARCHY_RESOLUTION_CONTRACT_RETRIES"
    assert (violation.measured, violation.limit) == (1, 1)
    assert kwargs["level"] == "error"
    assert kwargs["agent"] == "hierarchy_classifier"
    assert kwargs["correlation"] == {
        "outcome": "failed",
        "contract_retries": 1,
        "section_count": len(_PAPER_SECTIONS),
        "document_id": "doc-1",
        "job_id": "job-1",
        "run_id": None,
    }
    for info in _PAPER_SECTIONS:
        assert info["title"] not in violation.message


@pytest.mark.asyncio
async def test_section_index_contract_correction_recovers(monkeypatch, hierarchy_env):
    invalid = _paper_output(**_INVALID_SECTION_INDEX_OUTPUTS["missing"])
    _captured, calls = _install_sequenced_runner(
        monkeypatch, [invalid, _paper_output()]
    )
    sentry_calls = _sentry_recorder(monkeypatch)
    reports = _contract_report_recorder(monkeypatch)

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
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
    # The stored raw response and the span keep the correction visible.
    assert raw["contract_retries"] == 1
    assert ("data", "ai_curation.validation.retry_count", 1) in sentry_calls
    assert len(reports) == 1
    violation, kwargs = reports[0]
    assert violation.component == "hierarchy_resolution.recovered"
    assert kwargs["level"] == "warning"
    assert kwargs["correlation"]["outcome"] == "recovered"
    assert kwargs["correlation"]["contract_retries"] == 1


@pytest.mark.asyncio
async def test_first_try_success_records_zero_retries_and_reports_nothing(
    monkeypatch, hierarchy_env
):
    _install_sequenced_runner(monkeypatch, [_paper_output()])
    sentry_calls = _sentry_recorder(monkeypatch)
    reports = _contract_report_recorder(monkeypatch)

    _sections, _abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        _PAPER_SECTIONS
    )

    assert raw["contract_retries"] == 0
    assert ("data", "ai_curation.validation.retry_count", 0) in sentry_calls
    assert reports == []


# Three-level numbered headings, in document order.
_NESTED_SECTIONS = [
    {"title": "Wingless patterns the wing disc", "preview": "Jane Doe"},
    {"title": "1. Introduction", "preview": "Wingless is a morphogen."},
    {"title": "2. Materials and Methods", "preview": ""},
    {"title": "2.1. Fly genetics", "preview": ""},
    {"title": "2.1.1. Fly strains", "preview": "w1118 flies were used."},
    {"title": "2.1.2. Clone induction", "preview": "Clones were induced by heat shock."},
    {"title": "2.2. Imaging", "preview": "Discs were imaged."},
    {"title": "3. Results", "preview": ""},
    {"title": "3.1. wg controls growth", "preview": ""},
    {"title": "3.1.1. Clone size", "preview": "Clones lacking wg were small."},
]


@pytest.mark.asyncio
async def test_nested_subsections_resolve_to_top_level_ancestor(
    monkeypatch, hierarchy_env
):
    # 2.1.1 names its direct parent 2.1; 2.1.2 names the top-level section
    # directly; 3.1.1 names 3.1. All must resolve to their top-level heading.
    output = hierarchy.HierarchyOutput.model_validate({
        "sections": [
            {"idx": 0, "is_top_level": True, "parent_idx": None},
            {"idx": 1, "is_top_level": True, "parent_idx": None},
            {"idx": 2, "is_top_level": True, "parent_idx": None},
            {"idx": 3, "is_top_level": False, "parent_idx": 2},
            {"idx": 4, "is_top_level": False, "parent_idx": 3},
            {"idx": 5, "is_top_level": False, "parent_idx": 2},
            {"idx": 6, "is_top_level": False, "parent_idx": 2},
            {"idx": 7, "is_top_level": True, "parent_idx": None},
            {"idx": 8, "is_top_level": False, "parent_idx": 7},
            {"idx": 9, "is_top_level": False, "parent_idx": 8},
        ],
        "abstract_idx": None,
    })
    _captured, calls = _install_sequenced_runner(monkeypatch, [output])
    _sentry_recorder(monkeypatch)
    reports = _contract_report_recorder(monkeypatch)

    sections, _abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        _NESTED_SECTIONS
    )

    methods = "2. Materials and Methods"
    results = "3. Results"
    assert [
        (item.header, item.parent_section, item.subsection, item.is_top_level)
        for item in sections
    ] == [
        ("Wingless patterns the wing disc", "Wingless patterns the wing disc", None, True),
        ("1. Introduction", "1. Introduction", None, True),
        (methods, methods, None, True),
        ("2.1. Fly genetics", methods, "2.1. Fly genetics", False),
        ("2.1.1. Fly strains", methods, "2.1.1. Fly strains", False),
        ("2.1.2. Clone induction", methods, "2.1.2. Clone induction", False),
        ("2.2. Imaging", methods, "2.2. Imaging", False),
        (results, results, None, True),
        ("3.1. wg controls growth", results, "3.1. wg controls growth", False),
        ("3.1.1. Clone size", results, "3.1.1. Clone size", False),
    ]
    assert len(calls) == 1
    assert raw["contract_retries"] == 0
    assert reports == []


# PDFX shape: every heading is its own "Title" element whose section_title is
# itself, and the first body element under it often repeats the heading line.
_PDFX_ELEMENTS = [
    {"type": "Title", "text": "Results", "metadata": {"section_title": "Results"}},
    {
        "type": "NarrativeText",
        "text": "Results\n\nClones lacking   wg were\nsmall.",
        "metadata": {"section_title": "Results"},
    },
    {"type": "Title", "text": "Methods", "metadata": {"section_title": "Methods"}},
    {"type": "Title", "text": "Fly strains", "metadata": {"section_title": "Fly strains"}},
    {
        "type": "NarrativeText",
        "text": "Fly strains",
        "metadata": {"section_title": "Fly strains"},
    },
    {
        "type": "NarrativeText",
        "text": "Flies were raised at 25 C.",
        "metadata": {"section_title": "Fly strains"},
    },
    {
        "type": "NarrativeText",
        "text": "Results obtained with this second paragraph stay out of the preview.",
        "metadata": {"section_title": "Results"},
    },
]


def _capture_section_info(monkeypatch):
    llm_inputs = []

    async def _fake_llm(section_info):
        llm_inputs.append(section_info)
        return ([], None, None)

    monkeypatch.setattr(hierarchy, "_call_llm_for_hierarchy", _fake_llm)
    return llm_inputs


@pytest.mark.asyncio
async def test_section_previews_use_first_body_text_not_the_heading(monkeypatch):
    monkeypatch.delenv("HIERARCHY_RESOLUTION_PREVIEW_MAX_CHARS", raising=False)
    llm_inputs = _capture_section_info(monkeypatch)

    await hierarchy.resolve_document_hierarchy(_PDFX_ELEMENTS)

    assert llm_inputs == [[
        {"title": "Results", "preview": "Clones lacking wg were small."},
        {"title": "Methods", "preview": ""},
        {"title": "Fly strains", "preview": "Flies were raised at 25 C."},
    ]]


@pytest.mark.asyncio
async def test_section_previews_are_bounded_with_an_explicit_cut_marker(monkeypatch):
    monkeypatch.setenv("HIERARCHY_RESOLUTION_PREVIEW_MAX_CHARS", "14")
    llm_inputs = _capture_section_info(monkeypatch)

    await hierarchy.resolve_document_hierarchy(_PDFX_ELEMENTS)

    previews = {info["title"]: info["preview"] for info in llm_inputs[0]}
    assert previews["Results"] == "Clones lacking..."
    # Text that fits the bound is sent whole, without a cut marker.
    monkeypatch.setenv("HIERARCHY_RESOLUTION_PREVIEW_MAX_CHARS", "26")
    await hierarchy.resolve_document_hierarchy(_PDFX_ELEMENTS)
    previews = {info["title"]: info["preview"] for info in llm_inputs[1]}
    assert previews["Fly strains"] == "Flies were raised at 25 C."


@pytest.mark.asyncio
async def test_section_previews_can_be_disabled(monkeypatch):
    monkeypatch.setenv("HIERARCHY_RESOLUTION_PREVIEW_MAX_CHARS", "0")
    llm_inputs = _capture_section_info(monkeypatch)

    await hierarchy.resolve_document_hierarchy(_PDFX_ELEMENTS)

    assert [info["preview"] for info in llm_inputs[0]] == ["", "", ""]


def test_preview_max_chars_setting_default_and_override(monkeypatch):
    from src.lib.openai_agents.config import get_hierarchy_resolution_preview_max_chars

    monkeypatch.delenv("HIERARCHY_RESOLUTION_PREVIEW_MAX_CHARS", raising=False)
    assert get_hierarchy_resolution_preview_max_chars() == 100
    monkeypatch.setenv("HIERARCHY_RESOLUTION_PREVIEW_MAX_CHARS", "240")
    assert get_hierarchy_resolution_preview_max_chars() == 240
    monkeypatch.setenv("HIERARCHY_RESOLUTION_PREVIEW_MAX_CHARS", "-5")
    assert get_hierarchy_resolution_preview_max_chars() == 0


@pytest.mark.asyncio
async def test_prompt_marks_only_cut_previews_and_omits_empty_ones(
    monkeypatch, hierarchy_env
):
    _captured, calls = _install_sequenced_runner(monkeypatch, [_paper_output()])
    _sentry_recorder(monkeypatch)
    sections = [dict(info) for info in _PAPER_SECTIONS]
    sections[1]["preview"] = "Wingless signaling controls..."

    await hierarchy._call_llm_for_hierarchy(sections)

    prompt = calls[0]["prompt"]
    assert '[1] "Abstract" → "Wingless signaling controls..."' in prompt
    assert '[3] "2.1. Fly strains" → "Flies were raised at 25 C."\n' in prompt
    assert '[2] "Materials and Methods"\n' in prompt
    assert "(~100 characters)" not in calls[0]["instructions"]
    # Parent chains may name a direct parent subsection, so the prompt must not
    # restrict subsections to pointing at top-level sections.
    assert "can only point to a section in the list" in calls[0]["instructions"]
    assert "can only point to a top-level section" not in calls[0]["instructions"]


@pytest.mark.asyncio
async def test_pdfx_fixture_previews_never_repeat_their_heading(monkeypatch):
    import json
    from pathlib import Path

    monkeypatch.delenv("HIERARCHY_RESOLUTION_PREVIEW_MAX_CHARS", raising=False)
    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "micropub-biology-001725_pdfx.json"
    )
    elements = json.loads(fixture.read_text(encoding="utf-8"))
    llm_inputs = _capture_section_info(monkeypatch)

    await hierarchy.resolve_document_hierarchy(elements)

    previews = {info["title"]: info["preview"] for info in llm_inputs[0]}
    assert previews["Abstract"].startswith("The Drosophila ovary serves as")
    assert previews["Methods"].startswith("The Drosophila melanogaster ovarian")
    assert previews["References"].startswith("Cetera M, Horne-Badovinac S.")
    for title, preview in previews.items():
        assert preview, title
        assert not preview.startswith(title), title
        assert len(preview) <= 100 + len("..."), title


def _sentry_capture_recorder(monkeypatch):
    from src.lib.observability import payload_contracts

    captures = []

    def _capture(exc, **kwargs):
        captures.append({"exc": exc, **kwargs})
        return True

    monkeypatch.setattr(payload_contracts, "report_runtime_exception", _capture)
    return captures


@pytest.mark.asyncio
async def test_failed_and_recovered_contract_reports_group_separately(
    monkeypatch, hierarchy_env
):
    # Sentry groups payload-contract events by this fingerprint. A recovered
    # retry (warning) must not share an issue with a real failure (error), or
    # the real failure joins the warning issue and new-issue alerts never fire.
    invalid = _paper_output(**_INVALID_SECTION_INDEX_OUTPUTS["missing"])
    _sentry_recorder(monkeypatch)
    captures = _sentry_capture_recorder(monkeypatch)

    _install_sequenced_runner(monkeypatch, [invalid, invalid])
    await hierarchy._call_llm_for_hierarchy(_PAPER_SECTIONS)
    _install_sequenced_runner(monkeypatch, [invalid, _paper_output()])
    await hierarchy._call_llm_for_hierarchy(_PAPER_SECTIONS)

    assert [(c["fingerprint"], c["level"]) for c in captures] == [
        (
            ["payload_contract", "contract_serialization_failure", "hierarchy_resolution"],
            "error",
        ),
        (
            [
                "payload_contract",
                "contract_serialization_failure",
                "hierarchy_resolution.recovered",
            ],
            "warning",
        ),
    ]


@pytest.mark.asyncio
async def test_contract_retry_without_output_reports_failure_once(
    monkeypatch, hierarchy_env
):
    invalid = _paper_output(**_INVALID_SECTION_INDEX_OUTPUTS["missing"])
    _captured, calls = _install_sequenced_runner(monkeypatch, [invalid, None])
    _sentry_recorder(monkeypatch)
    reports = _contract_report_recorder(monkeypatch)

    sections, abstract_title, raw = await hierarchy._call_llm_for_hierarchy(
        _PAPER_SECTIONS
    )

    assert (sections, abstract_title) == ([], None)
    assert raw["contract_retries"] == 1
    assert len(calls) == 2
    assert len(reports) == 1
    violation, kwargs = reports[0]
    assert violation.component == "hierarchy_resolution"
    assert kwargs["level"] == "error"
    assert kwargs["correlation"]["outcome"] == "failed"
    assert "the correction retry returned no output" in violation.message


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_reported_contract_failure_marks_the_logged_error_as_captured(
    monkeypatch, hierarchy_env, caplog, accepted
):
    # The outer logger.error(exc_info=True) event is dropped by Sentry's
    # before_send only when the reporter marked the raised error as captured.
    invalid = _paper_output(**_INVALID_SECTION_INDEX_OUTPUTS["missing"])
    _install_sequenced_runner(monkeypatch, [invalid, invalid])
    _sentry_recorder(monkeypatch)
    monkeypatch.setattr(
        hierarchy,
        "report_payload_contract_violation",
        lambda violation, **kwargs: accepted,
    )

    with caplog.at_level("ERROR", logger=hierarchy.logger.name):
        await hierarchy._call_llm_for_hierarchy(_PAPER_SECTIONS)

    record = next(
        r for r in caplog.records if "LLM hierarchy resolution failed" in r.getMessage()
    )
    error = record.exc_info[1]
    assert isinstance(error, ValueError)
    assert "section index contract" in str(error)
    assert getattr(error, "_ai_curation_sentry_captured", False) is accepted
