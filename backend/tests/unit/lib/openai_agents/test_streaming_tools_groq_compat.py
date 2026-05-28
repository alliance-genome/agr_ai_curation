"""Tests for Groq JSON+tools compatibility helpers in streaming_tools."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.lib.openai_agents.streaming_tools import (
    _adapt_tools_with_provider_adapter,
    _adapt_tools_for_groq_schema_constraints,
    _build_tool_efficiency_instruction,
    _compute_adaptive_specialist_max_turns,
    _estimate_bulk_entity_count,
    _tool_provider_adapter_factories,
    _try_parse_markdown_field_table,
    _required_tool_failure_message,
    _required_tool_names_for_agent,
    _should_use_groq_tool_json_compat,
    _try_validate_json_output,
)


class _Envelope(BaseModel):
    value: str


def test_should_use_groq_tool_json_compat_when_structured_output_and_tools_present():
    agent = SimpleNamespace(
        output_type=_Envelope,
        tools=[object()],
        model=SimpleNamespace(model="groq/openai/gpt-oss-120b"),
    )

    assert _should_use_groq_tool_json_compat(agent) is True


def test_should_not_use_groq_tool_json_compat_without_tools():
    agent = SimpleNamespace(
        output_type=_Envelope,
        tools=[],
        model=SimpleNamespace(model="groq/openai/gpt-oss-120b"),
    )

    assert _should_use_groq_tool_json_compat(agent) is False


def test_try_validate_json_output_extracts_and_validates_embedded_json():
    raw = "Here is the result:\\n```json\\n{\"value\":\"ok\"}\\n```"
    validated = _try_validate_json_output(raw, _Envelope)

    assert validated is not None
    assert validated == '{"value": "ok"}'


def test_try_validate_json_output_returns_none_for_invalid_shape():
    raw = '{"unexpected":"field"}'

    assert _try_validate_json_output(raw, _Envelope) is None


def test_try_validate_json_output_recovers_markdown_field_table():
    class _AnswerEnvelope(BaseModel):
        answer: str
        citations: list[dict] = []
        sources: list[str] = []

    raw = """| Field | Type | Content |
|---|---|---|
| **answer** | string | The title is Alliance AI curation test document. |
| **citations** | array | [{"section_title":"TITLE","page_number":1}] |
| **sources** | array | ["read_section"] |
"""
    validated = _try_validate_json_output(raw, _AnswerEnvelope)

    assert validated is not None
    assert '"answer": "The title is Alliance AI curation test document."' in validated
    assert '"sources": ["read_section"]' in validated


def test_try_parse_markdown_field_table_extracts_expected_fields():
    raw = """| Field | Type | Content |
|---|---|---|
| **answer** | string | Concise summary |
| **citations** | array | [{"section_title":"TITLE","page_number":1}] |
| **sources** | array | ["read_section"] |
"""
    parsed = _try_parse_markdown_field_table(raw)

    assert parsed is not None
    assert parsed["answer"] == "Concise summary"
    assert parsed["citations"][0]["section_title"] == "TITLE"
    assert parsed["sources"] == ["read_section"]


def test_required_tool_names_prefers_document_tools_over_package_required_tools(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_metadata_by_name",
        lambda: {
            "artifact_lookup": {
                "required_tool_call": {
                    "enforce": True,
                }
            }
        },
    )
    agent = SimpleNamespace(
        tools=[
            SimpleNamespace(name="artifact_lookup"),
            SimpleNamespace(name="search_document"),
        ]
    )

    assert _required_tool_names_for_agent(agent) == {
        "search_document",
        "read_chunk",
        "read_section",
        "read_subsection",
    }


def test_required_tool_failure_message_for_missing_package_required_call(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_metadata_by_name",
        lambda: {
            "artifact_lookup": {
                "required_tool_call": {
                    "enforce": True,
                    "failure_message": "did not call artifact lookup before answering",
                }
            }
        },
    )
    agent = SimpleNamespace(
        tools=[SimpleNamespace(name="artifact_lookup")]
    )

    message = _required_tool_failure_message(
        agent=agent,
        specialist_name="Catalog Specialist",
        tool_calls=[],
    )

    assert message is not None
    assert "did not call artifact lookup before answering" in message
    assert "artifact_lookup" in message


def test_required_tool_failure_message_requires_package_declared_failure_message(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_metadata_by_name",
        lambda: {
            "artifact_lookup": {
                "required_tool_call": {
                    "enforce": True,
                }
            }
        },
    )
    agent = SimpleNamespace(tools=[SimpleNamespace(name="artifact_lookup")])

    with pytest.raises(ValueError, match="must declare failure_message"):
        _required_tool_failure_message(
            agent=agent,
            specialist_name="Catalog Specialist",
            tool_calls=[],
        )


def test_required_tool_failure_message_is_none_when_required_tool_called(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_metadata_by_name",
        lambda: {
            "artifact_lookup": {
                "required_tool_call": {
                    "enforce": True,
                }
            }
        },
    )
    agent = SimpleNamespace(
        tools=[SimpleNamespace(name="artifact_lookup")]
    )

    message = _required_tool_failure_message(
        agent=agent,
        specialist_name="Gene Specialist",
        tool_calls=[SimpleNamespace(tool_name="artifact_lookup")],
    )

    assert message is None


def test_adapt_tools_for_groq_uses_package_declared_adapter(monkeypatch):
    replacement = SimpleNamespace(name="demo_lookup")
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_provider_adapter_factories",
        lambda adapter_key: {"demo_lookup": lambda: replacement},
    )
    tools = [
        SimpleNamespace(name="demo_lookup"),
        SimpleNamespace(name="search_document"),
    ]

    adapted = _adapt_tools_for_groq_schema_constraints(tools)

    assert adapted[0] is replacement
    assert getattr(adapted[1], "name", None) == "search_document"


def test_adapt_tools_with_provider_adapter_is_tool_name_agnostic(monkeypatch):
    replacement = SimpleNamespace(name="museum_catalog_lookup")
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_provider_adapter_factories",
        lambda adapter_key: {"museum_catalog_lookup": lambda: replacement},
    )

    adapted = _adapt_tools_with_provider_adapter(
        [SimpleNamespace(name="museum_catalog_lookup")],
        "demo_provider_schema",
    )

    assert adapted == [replacement]


def test_provider_adapter_factories_load_shipped_package_registry_by_default(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(tmp_path / "missing-packages"))
    _tool_provider_adapter_factories.cache_clear()

    try:
        factories = _tool_provider_adapter_factories("groq_schema_constraints")
        tools = [SimpleNamespace(name="agr_curation_query")]

        adapted = _adapt_tools_for_groq_schema_constraints(tools)

        assert "agr_curation_query" in factories
        assert adapted[0] is not tools[0]
        assert getattr(adapted[0], "name", None) == "agr_curation_query"
    finally:
        _tool_provider_adapter_factories.cache_clear()


def test_estimate_bulk_entity_count_detects_list_payload():
    query = (
        "Validate this list: crb, ninaE, Rh1, norpA, trp, Arr1, Arr2, ninaC, "
        "Act5C, Act87E, Act57B, patj, stardust"
    )

    assert _estimate_bulk_entity_count(query) >= 12


def test_compute_adaptive_specialist_max_turns_scales_for_large_package_bulk_lists(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_metadata_by_name",
        lambda: {
            "artifact_lookup": {
                "bulk_list_optimization": {
                    "enabled": True,
                    "minimum_entities": 8,
                    "min_turns": 40,
                    "max_turns": 120,
                }
            }
        },
    )
    agent = SimpleNamespace(tools=[SimpleNamespace(name="artifact_lookup")])
    query = "List: " + ", ".join(f"gene_{idx}" for idx in range(30))

    adaptive = _compute_adaptive_specialist_max_turns(
        agent=agent,
        input_text=query,
        base_max_turns=20,
    )

    assert adaptive > 20
    assert adaptive <= 120


def test_compute_adaptive_specialist_max_turns_honors_zero_minimum_entities(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_metadata_by_name",
        lambda: {
            "artifact_lookup": {
                "bulk_list_optimization": {
                    "enabled": True,
                    "minimum_entities": 0,
                    "min_turns": 40,
                    "max_turns": 120,
                }
            }
        },
    )
    agent = SimpleNamespace(tools=[SimpleNamespace(name="artifact_lookup")])

    adaptive = _compute_adaptive_specialist_max_turns(
        agent=agent,
        input_text="Lookup gene_1",
        base_max_turns=20,
    )

    assert adaptive == 40


def test_compute_adaptive_specialist_max_turns_requires_numeric_package_metadata(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_metadata_by_name",
        lambda: {
            "artifact_lookup": {
                "bulk_list_optimization": {
                    "enabled": True,
                    "minimum_entities": 8,
                    "max_turns": 120,
                }
            }
        },
    )
    agent = SimpleNamespace(tools=[SimpleNamespace(name="artifact_lookup")])
    query = "List: " + ", ".join(f"gene_{idx}" for idx in range(30))

    with pytest.raises(ValueError, match="min_turns is not declared"):
        _compute_adaptive_specialist_max_turns(
            agent=agent,
            input_text=query,
            base_max_turns=20,
        )


def test_compute_adaptive_specialist_max_turns_keeps_default_for_non_bulk_agents():
    agent = SimpleNamespace(tools=[SimpleNamespace(name="search_document")])

    adaptive = _compute_adaptive_specialist_max_turns(
        agent=agent,
        input_text="List: a, b, c, d, e, f, g, h, i, j",
        base_max_turns=20,
    )

    assert adaptive == 20


def test_tool_efficiency_instruction_requires_package_declared_text(monkeypatch):
    monkeypatch.setattr(
        "src.lib.openai_agents.streaming_tools._tool_metadata_by_name",
        lambda: {
            "artifact_lookup": {
                "bulk_list_optimization": {
                    "enabled": True,
                    "minimum_entities": 8,
                    "min_turns": 40,
                    "max_turns": 120,
                }
            }
        },
    )
    agent = SimpleNamespace(tools=[SimpleNamespace(name="artifact_lookup")])
    query = "List: " + ", ".join(f"gene_{idx}" for idx in range(12))

    with pytest.raises(ValueError, match="no instruction is declared"):
        _build_tool_efficiency_instruction(agent, query)
