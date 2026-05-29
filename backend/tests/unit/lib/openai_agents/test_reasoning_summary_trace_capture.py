from types import SimpleNamespace

import pytest

from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents.config import (
    build_model_settings,
    reasoning_summary_request_settings,
)


def _provider(provider_id: str, *, driver: str = "openai_native"):
    return SimpleNamespace(
        provider_id=provider_id,
        driver=driver,
        supports_parallel_tool_calls=True,
        drop_params=False,
        api_key_env="OPENAI_API_KEY",
        base_url_env=None,
        default_base_url=None,
        litellm_prefix=None,
    )


def _model(provider: str, *, supports_reasoning: bool):
    return SimpleNamespace(
        provider=provider,
        supports_reasoning=supports_reasoning,
        supports_temperature=not supports_reasoning,
    )


class _FakeRunResult:
    def __init__(self, events=None, final_output="done"):
        self._events = events or []
        self.final_output = final_output

    async def stream_events(self):
        for event in self._events:
            yield event


def _reasoning_event(*, summary=None, raw_summary=None, raw=None):
    if raw is None:
        raw = SimpleNamespace(summary=raw_summary)
        raw.model_dump = lambda: {"encrypted_content": "not a summary"}
    return SimpleNamespace(
        type="run_item_stream_event",
        item=SimpleNamespace(type="reasoning_item", summary=summary, raw_item=raw),
    )


def test_reasoning_summary_settings_request_detailed_for_openai_reasoning_model(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: _model("openai", supports_reasoning=True),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: _provider(provider_id, driver="openai_native"),
    )

    settings = reasoning_summary_request_settings(
        model="gpt-5.4-mini",
        reasoning_effort="medium",
    )

    assert settings["availability"] == "present"
    assert settings["requested_summary"] == "detailed"
    assert settings["reasoning_effort"] == "medium"

    model_settings = build_model_settings(model="gpt-5.4-mini", reasoning_effort="medium")
    assert model_settings.reasoning.effort == "medium"
    assert model_settings.reasoning.summary == "detailed"


def test_reasoning_summary_settings_report_not_supported_for_non_reasoning_model(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: _model("openai", supports_reasoning=False),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: _provider(provider_id, driver="openai_native"),
    )

    settings = reasoning_summary_request_settings(
        model="gpt-4o",
        reasoning_effort="medium",
    )

    assert settings["availability"] == "not_supported"
    assert settings["requested_summary"] is None


def test_reasoning_summary_settings_report_not_supported_for_litellm_provider(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: _model("gemini", supports_reasoning=True),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: _provider(provider_id, driver="litellm"),
    )

    settings = reasoning_summary_request_settings(
        model="gemini-3-pro-preview",
        reasoning_effort="medium",
    )

    assert settings["availability"] == "not_supported"
    assert settings["requested_summary"] is None


def test_reasoning_summary_settings_report_not_requested_without_reasoning_effort(monkeypatch):
    monkeypatch.setattr(
        "src.lib.config.models_loader.get_model",
        lambda _model_id: _model("openai", supports_reasoning=True),
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda provider_id: _provider(provider_id, driver="openai_native"),
    )

    settings = reasoning_summary_request_settings(
        model="gpt-5.4-mini",
        reasoning_effort=None,
    )

    assert settings["availability"] == "not_requested"
    assert settings["requested_summary"] is None


@pytest.mark.asyncio
async def test_specialist_reasoning_item_persists_only_first_class_summary_text(monkeypatch):
    captured_trace_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", lambda _event: None)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(
        streaming_tools,
        "RunConfig",
        lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            events=[_reasoning_event(summary=[SimpleNamespace(text="Used resolver evidence.")])]
        ),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_reasoning_request_metadata",
        lambda _agent: {"availability": "present"},
    )
    monkeypatch.setattr(
        streaming_tools,
        "write_extraction_trace_event",
        lambda **event: captured_trace_events.append(event),
    )

    agent = SimpleNamespace(
        name="Reasoning Specialist",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-5.4-mini",
    )

    await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Reasoning Specialist",
        max_turns=3,
        tool_name="ask_gene_expression_specialist",
    )

    summary_events = [
        event
        for event in captured_trace_events
        if event["event_type"] == "model.reasoning_summary.output"
    ]
    assert summary_events == [
        {
            "event_type": "model.reasoning_summary.output",
            "output_summary": {"summary_text": "Used resolver evidence."},
            "metadata": {
                "agent": "Reasoning Specialist",
                "tool_name": "ask_gene_expression_specialist",
                "availability": "present",
            },
        }
    ]


@pytest.mark.asyncio
async def test_specialist_reasoning_item_does_not_persist_raw_item_dump_as_summary(monkeypatch):
    captured_trace_events = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", lambda _event: None)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent_name: None)
    monkeypatch.setattr(
        streaming_tools,
        "RunConfig",
        lambda *args, **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(events=[_reasoning_event()]),
    )
    monkeypatch.setattr(
        streaming_tools,
        "_reasoning_request_metadata",
        lambda _agent: {"availability": "present"},
    )
    monkeypatch.setattr(
        streaming_tools,
        "write_extraction_trace_event",
        lambda **event: captured_trace_events.append(event),
    )

    agent = SimpleNamespace(
        name="Reasoning Specialist",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-5.4-mini",
    )

    await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="extract findings",
        specialist_name="Reasoning Specialist",
        max_turns=3,
        tool_name="ask_gene_expression_specialist",
    )

    assert [
        event["event_type"]
        for event in captured_trace_events
        if event["event_type"] == "model.reasoning_summary.output"
    ] == []
