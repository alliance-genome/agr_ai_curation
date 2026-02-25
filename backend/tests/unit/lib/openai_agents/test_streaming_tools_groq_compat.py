"""Tests for Groq JSON+tools compatibility helpers in streaming_tools."""

from types import SimpleNamespace

from pydantic import BaseModel

from src.lib.openai_agents.streaming_tools import (
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
