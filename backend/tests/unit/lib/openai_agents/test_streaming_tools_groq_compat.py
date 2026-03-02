"""Tests for Groq JSON+tools compatibility helpers in streaming_tools."""

from types import SimpleNamespace

from pydantic import BaseModel

from src.lib.openai_agents.streaming_tools import (
    _adapt_tools_for_groq_schema_constraints,
    _compute_adaptive_specialist_max_turns,
    _estimate_bulk_entity_count,
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


def test_required_tool_names_prefers_document_tools_over_agr():
    agent = SimpleNamespace(
        tools=[
            SimpleNamespace(name="agr_curation_query"),
            SimpleNamespace(name="search_document"),
        ]
    )

    assert _required_tool_names_for_agent(agent) == {
        "search_document",
        "read_section",
        "read_subsection",
    }


def test_required_tool_failure_message_for_missing_agr_call():
    agent = SimpleNamespace(
        tools=[SimpleNamespace(name="agr_curation_query")]
    )

    message = _required_tool_failure_message(
        agent=agent,
        specialist_name="Gene Specialist",
        tool_calls=[],
    )

    assert message is not None
    assert "required AGR DB tools" in message
    assert "agr_curation_query" in message


def test_required_tool_failure_message_is_none_when_required_tool_called():
    agent = SimpleNamespace(
        tools=[SimpleNamespace(name="agr_curation_query")]
    )

    message = _required_tool_failure_message(
        agent=agent,
        specialist_name="Gene Specialist",
        tool_calls=[SimpleNamespace(tool_name="agr_curation_query")],
    )

    assert message is None


def test_adapt_tools_for_groq_replaces_agr_tool(monkeypatch):
    replacement = SimpleNamespace(name="agr_curation_query")
    monkeypatch.setattr(
        "src.lib.openai_agents.tools.agr_curation.create_groq_agr_curation_query_tool",
        lambda: replacement,
    )
    tools = [
        SimpleNamespace(name="agr_curation_query"),
        SimpleNamespace(name="search_document"),
    ]

    adapted = _adapt_tools_for_groq_schema_constraints(tools)

    assert adapted[0] is replacement
    assert getattr(adapted[1], "name", None) == "search_document"


def test_estimate_bulk_entity_count_detects_list_payload():
    query = (
        "Validate this list: crb, ninaE, Rh1, norpA, trp, Arr1, Arr2, ninaC, "
        "Act5C, Act87E, Act57B, patj, stardust"
    )

    assert _estimate_bulk_entity_count(query) >= 12


def test_compute_adaptive_specialist_max_turns_scales_for_large_agr_lists():
    agent = SimpleNamespace(tools=[SimpleNamespace(name="agr_curation_query")])
    query = "List: " + ", ".join(f"gene_{idx}" for idx in range(30))

    adaptive = _compute_adaptive_specialist_max_turns(
        agent=agent,
        input_text=query,
        base_max_turns=20,
    )

    assert adaptive > 20
    assert adaptive <= 120


def test_compute_adaptive_specialist_max_turns_keeps_default_for_non_agr_agents():
    agent = SimpleNamespace(tools=[SimpleNamespace(name="search_document")])

    adaptive = _compute_adaptive_specialist_max_turns(
        agent=agent,
        input_text="List: a, b, c, d, e, f, g, h, i, j",
        base_max_turns=20,
    )

    assert adaptive == 20
