"""Tests for Groq JSON+tools compatibility helpers in streaming_tools."""

from types import SimpleNamespace

from pydantic import BaseModel

from src.lib.openai_agents.streaming_tools import (
    _adapt_tools_for_groq_schema_constraints,
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
