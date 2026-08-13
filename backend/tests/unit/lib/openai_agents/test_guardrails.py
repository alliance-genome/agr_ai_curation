"""Unit tests for openai_agents guardrail helpers."""

from types import SimpleNamespace

import pytest

from src.lib.openai_agents import guardrails


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


def test_check_for_pii_detects_email_and_returns_none_when_clean():
    assert guardrails.check_for_pii("contact me at curator@example.org") == "email"
    assert guardrails.check_for_pii("gene daf-16 regulates stress response") is None


def test_contains_negative_claim_matches_expected_phrases():
    assert guardrails._contains_negative_claim("This gene was not found in the document.") is True
    assert guardrails._contains_negative_claim("Found strong evidence in section 4.") is False


def test_enforce_uncited_negative_guardrail_requires_search_tool():
    answer = SimpleNamespace(answer="No results found for this query.")
    message = guardrails.enforce_uncited_negative_guardrail(answer, tools_called=["read_section"])

    assert message is not None
    assert "without using a search tool" in message


def test_enforce_uncited_negative_guardrail_allows_negative_with_search_tool():
    answer = SimpleNamespace(answer="No relevant evidence was found.")
    message = guardrails.enforce_uncited_negative_guardrail(
        answer,
        tools_called=["search_document", "read_section"],
    )
    assert message is None


def test_enforce_uncited_negative_guardrail_skips_positive_answers():
    answer = SimpleNamespace(answer="The paper reports expression in neurons.")
    message = guardrails.enforce_uncited_negative_guardrail(answer, tools_called=[])
    assert message is None


def test_tool_call_tracker_records_and_resets_calls():
    tracker = guardrails.ToolCallTracker()
    assert tracker.has_tool_calls() is False
    assert tracker.get_call_count() == 0

    tracker.record_call("search_document")
    tracker.record_call("read_section")

    assert tracker.has_tool_calls() is True
    assert tracker.get_call_count() == 2
    assert tracker.get_tool_names() == ["search_document", "read_section"]

    tracker.reset()
    assert tracker.has_tool_calls() is False
    assert tracker.get_call_count() == 0
    assert tracker.get_tool_names() == []


@pytest.mark.asyncio
async def test_pii_pattern_guardrail_trips_on_detected_pii():
    output = await guardrails.pii_pattern_guardrail.guardrail_function(
        ctx=SimpleNamespace(context={}),
        agent=SimpleNamespace(name="guarded-agent"),
        input_data="Please email me at user@example.com",
    )

    assert output.tripwire_triggered is True
    assert output.output_info.is_safe is False
    assert output.output_info.category == "pii"


@pytest.mark.asyncio
async def test_pii_pattern_guardrail_passes_clean_user_message_list():
    output = await guardrails.pii_pattern_guardrail.guardrail_function(
        ctx=SimpleNamespace(context={}),
        agent=SimpleNamespace(name="guarded-agent"),
        input_data=[
            {"role": "system", "content": "context"},
            {"role": "user", "content": "Find gene ontology annotations for pax6"},
        ],
    )

    assert output.tripwire_triggered is False
    assert output.output_info.is_safe is True


@pytest.mark.asyncio
async def test_llm_safety_guardrail_maps_runner_result(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_SINGLE_SHOT_MAX_TURNS", "3")
    sentry_calls = []

    class FakeSentrySpan:
        active = False

        def set_data(self, key, value):
            assert self.active, "Sentry span data must be written before span exit"
            sentry_calls.append(("data", key, value))

    def _fake_sentry_span(**kwargs):
        sentry_calls.append(("span", kwargs))
        return _FakeContextManager(FakeSentrySpan())

    async def _fake_run(_guardrail_agent, _input_data, context, max_turns):
        assert context == {"trace_id": "trace-1"}
        assert max_turns == 3
        return SimpleNamespace(
            final_output=guardrails.SafetyCheckOutput(
                is_safe=False,
                reasoning="PII detected by LLM",
                category="pii",
            )
        )

    monkeypatch.setattr(guardrails.Runner, "run", _fake_run)
    monkeypatch.setattr(guardrails, "gen_ai_invoke_agent_span", _fake_sentry_span)

    output = await guardrails.llm_safety_guardrail.guardrail_function(
        ctx=SimpleNamespace(context={"trace_id": "trace-1"}),
        agent=SimpleNamespace(name="guarded-agent"),
        input_data="my ssn is 123-45-6789",
    )

    assert output.tripwire_triggered is True
    assert output.output_info.reasoning == "PII detected by LLM"
    span_call = next(call for call in sentry_calls if call[0] == "span")
    assert span_call[1]["workflow"] == "guardrail"
    assert span_call[1]["agent_key"] == "safety_guardrail"
    assert ("data", "ai_curation.validation.status", "rejected") in sentry_calls


@pytest.mark.asyncio
async def test_llm_safety_guardrail_records_sentry_error_before_reraising(monkeypatch):
    sentry_calls = []

    class FakeSentrySpan:
        active = False

        def set_data(self, key, value):
            assert self.active, "Sentry span data must be written before span exit"
            sentry_calls.append(("data", key, value))

    def _fake_sentry_span(**kwargs):
        sentry_calls.append(("span", kwargs))
        return _FakeContextManager(FakeSentrySpan())

    async def _fake_run(*_args, **_kwargs):
        raise TimeoutError("guardrail timed out")

    monkeypatch.setattr(guardrails.Runner, "run", _fake_run)
    monkeypatch.setattr(guardrails, "gen_ai_invoke_agent_span", _fake_sentry_span)

    with pytest.raises(TimeoutError, match="guardrail timed out"):
        await guardrails.llm_safety_guardrail.guardrail_function(
            ctx=SimpleNamespace(context={"trace_id": "trace-err"}),
            agent=SimpleNamespace(name="guarded-agent"),
            input_data="hello",
        )

    assert ("data", "ai_curation.validation.status", "error") in sentry_calls
    assert (
        "data",
        "ai_curation.error.detail",
        {
            "message": "guardrail timed out",
            "error_type": "TimeoutError",
            "phase": "guardrail_safety",
        },
    ) in sentry_calls


@pytest.mark.asyncio
async def test_create_topic_guardrail_trips_for_off_topic_query(monkeypatch):
    monkeypatch.setenv("GUARDRAIL_SINGLE_SHOT_MAX_TURNS", "4")
    sentry_calls = []

    class FakeSentrySpan:
        active = False

        def set_data(self, key, value):
            assert self.active, "Sentry span data must be written before span exit"
            sentry_calls.append(("data", key, value))

    def _fake_sentry_span(**kwargs):
        sentry_calls.append(("span", kwargs))
        return _FakeContextManager(FakeSentrySpan())

    async def _fake_run(_topic_agent, _input_data, context, max_turns):
        assert context == {"trace_id": "trace-2"}
        assert max_turns == 4
        return SimpleNamespace(
            final_output=guardrails.TopicCheckOutput(
                is_on_topic=False,
                reasoning="Unrelated programming question",
                detected_topic="software",
            )
        )

    monkeypatch.setattr(guardrails.Runner, "run", _fake_run)
    monkeypatch.setattr(guardrails, "gen_ai_invoke_agent_span", _fake_sentry_span)
    topic_guardrail = guardrails.create_topic_guardrail(["biology"], guardrail_name="Bio Check")

    output = await topic_guardrail.guardrail_function(
        ctx=SimpleNamespace(context={"trace_id": "trace-2"}),
        agent=SimpleNamespace(name="guarded-agent"),
        input_data="Help me debug JavaScript",
    )

    assert output.tripwire_triggered is True
    span_call = next(call for call in sentry_calls if call[0] == "span")
    assert span_call[1]["workflow"] == "guardrail"
    assert span_call[1]["agent_key"] == "topic_guardrail"
    assert span_call[1]["span_data"]["ai_curation.validation.detail"] == {
        "allowed_topics": ["biology"]
    }
    assert ("data", "ai_curation.validation.status", "rejected") in sentry_calls
    assert output.output_info.is_on_topic is False


@pytest.mark.asyncio
async def test_tool_required_output_guardrail_enforces_minimum_calls():
    tracker = guardrails.ToolCallTracker()
    output_guardrail = guardrails.create_tool_required_output_guardrail(tracker, minimum_calls=1)

    blocked = await output_guardrail.guardrail_function(
        ctx=SimpleNamespace(context={}),
        agent=SimpleNamespace(name="tool-agent"),
        output=SimpleNamespace(answer="response"),
    )
    assert blocked.tripwire_triggered is True
    assert blocked.output_info["calls_made"] == 0

    tracker.record_call("search_document")

    allowed = await output_guardrail.guardrail_function(
        ctx=SimpleNamespace(context={}),
        agent=SimpleNamespace(name="tool-agent"),
        output=SimpleNamespace(answer="response"),
    )
    assert allowed.tripwire_triggered is False
    assert allowed.output_info["calls_made"] == 1
    assert allowed.output_info["tools_called"] == ["search_document"]
