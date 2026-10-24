from unittest.mock import Mock

import pytest
from src.lib.validation_review.policy import ReviewLimits
from src.lib.validation_review.tools import create_tools
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_history.store import HistoryScope


def test_pooled_32_target_defaults_and_nondefault_warnings(monkeypatch):
    policy = ReviewLimits.configured()
    pools = policy.allowances(targets=32, episodes=1, extras=16)
    assert [
        pools[key]
        for key in ("initial_tokens", "reconsideration_tokens", "reviewer_tokens")
    ] == [4800000, 2400000, 1000000]
    monkeypatch.setenv("VALIDATION_REVIEW_TOKENS_PER_TARGET", "200000")
    monkeypatch.setenv("VALIDATION_REVIEW_WARNING_PERCENT", "65")
    changed = ReviewLimits.configured()
    assert (
        changed.allowances(targets=32, episodes=1, extras=16)["initial_tokens"]
        == 6400000
    )
    assert changed.warning_percent == 65
    monkeypatch.setenv("VALIDATION_REVIEW_CRITICAL_PERCENT", "60")
    with pytest.raises(ValueError):
        ReviewLimits.configured()


def test_tool_schema_cannot_choose_actor_episode_or_validator():
    context = ReviewContext(
        HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        "episode",
        "reviewer",
    )
    tools = create_tools(context, checkpoint_provider=Mock())
    assert len(tools) == 5
    for tool in tools:
        fields = tool.params_json_schema["properties"]
        assert not {
            "actor_id",
            "owner_id",
            "episode_id",
            "validator_agent",
            "receipt",
            "scope_id",
        } & set(fields)
        assert tool.params_json_schema["additionalProperties"] is False


def test_review_settings_are_forwarded_to_backend_and_worker():
    import yaml
    from tests.unit.test_docker_compose_production import WORKSPACE_ROOT

    settings = ReviewLimits.configured().snapshot()
    for filename, services in (
        ("docker-compose.yml", ("backend", "benchmark_worker")),
        ("docker-compose.production.yml", ("backend", "benchmark_worker")),
        (
            "docker-compose.test.yml",
            (
                "backend-unit-tests",
                "backend-persistence-tests",
                "benchmark-worker-test",
            ),
        ),
    ):
        spec = yaml.safe_load((WORKSPACE_ROOT / filename).read_text())
        for name in services:
            env = spec["services"][name]["environment"]
            if isinstance(env, list):
                env = dict(item.split("=", 1) for item in env)
            for field, value in settings.items():
                key = "VALIDATION_REVIEW_" + field.upper()
                assert env[key] == "${" + key + ":-" + str(value) + "}"
    example = (WORKSPACE_ROOT / ".env.example").read_text()
    for field, value in settings.items():
        assert (
            "\nVALIDATION_REVIEW_" + field.upper() + "=" + str(value) + "\n" in example
        )


def test_locked_review_protocol_is_separate_from_editable_policy():
    from src.lib.config.agent_loader import get_agent_definition
    from src.lib.prompts.assembly import _build_core_generated_content
    from src.lib.validation_review.protocol import REVIEW_PROTOCOL

    definition = get_agent_definition("validation_reviewer")
    assert definition is not None
    assert REVIEW_PROTOCOL.strip() in _build_core_generated_content(definition)


def test_sentry_reports_only_sanitized_error_once(monkeypatch):
    from src.lib.validation_review.audit import report_operational_failure

    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda error, **kwargs: reports.append(error) or True,
    )
    original = RuntimeError("PRIVATE_PAPER_PROVIDER_MESSAGE")
    report_operational_failure(original)
    report_operational_failure(original)
    assert len(reports) == 1
    assert "PRIVATE" not in str(reports[0])
    assert reports[0].__cause__ is None and reports[0].__context__ is None


def test_review_budget_counts_are_not_redacted_as_secrets():
    from src.lib.openai_agents.extraction_trace_events import _is_secret_key

    for name in (
        "reviewer_tokens",
        "initial_tokens",
        "reconsideration_tokens",
        "tokens_per_target",
        "tokens_per_extra",
    ):
        assert not _is_secret_key(name)
    assert _is_secret_key("api_token") and _is_secret_key("lease_token")


@pytest.mark.asyncio
async def test_tool_conflicts_and_target_limits_are_recoverable(monkeypatch):
    import json
    from agents.tool_context import ToolContext
    from src.lib.validation_review import reads, scheduling
    from src.lib.validation_review.admission import ReviewStopped
    from src.lib.validation_history import store

    context = ReviewContext(
        HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        "episode",
        "reviewer",
    )
    tools = {t.name: t for t in create_tools(context, checkpoint_provider=Mock())}
    monkeypatch.setattr(
        reads, "read", Mock(side_effect=store.HistoryConflict("stale SHA"))
    )
    result = await tools["read_validation_history"].on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="review_tool",
            tool_call_id="test",
            tool_arguments="{}",
        ),
        json.dumps({"fact_id": "f", "sha256": "s", "offset": 0}),
    )
    assert result["state"] == "conflict" and not result["applied"]
    monkeypatch.setattr(
        scheduling,
        "reconsider",
        Mock(side_effect=ReviewStopped("scientific_attempts_exhausted", resource="t")),
    )
    args = json.dumps(
        {
            "operation_id": "op",
            "parent_decision_id": "d",
            "target_ids": ["t"],
            "instructions": "retry",
            "evidence_refs": [],
        }
    )
    result = await tools["request_validation_reconsideration"].on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="review_tool",
            tool_call_id="test",
            tool_arguments="{}",
        ),
        args,
    )
    assert result["state"] == "not_applied" and result["targets"] == ["t"]
    monkeypatch.setattr(
        scheduling, "reconsider", Mock(side_effect=ReviewStopped("expired"))
    )
    with pytest.raises(ReviewStopped, match="expired"):
        await tools["request_validation_reconsideration"].on_invoke_tool(
            ToolContext(
                context=None,
                tool_name="review_tool",
                tool_call_id="test",
                tool_arguments="{}",
            ),
            args,
        )


@pytest.mark.asyncio
async def test_executor_renews_until_work_finishes_and_cancels_on_lost_ownership(
    monkeypatch,
):
    import asyncio
    from src.lib.validation_review.execution import _while_owned
    from src.lib.validation_review import work
    from src.lib.validation_review.admission import ReviewStopped

    pulses = []

    def heartbeat(*args):
        pulses.append(args)
        if len(pulses) == 2:
            raise ReviewStopped("cancelled")

    monkeypatch.setattr(work, "heartbeat", heartbeat)
    cancelled = asyncio.Event()

    async def long_call():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(ReviewStopped, match="cancelled"):
        await _while_owned(long_call(), "scope", "work", "fence", 0.01)
    assert cancelled.is_set() and len(pulses) == 2


def test_only_sdk_wrappers_are_transparent_to_expected_stops():
    from agents import UserError
    from src.lib.validation_review.admission import ReviewStopped
    from src.lib.validation_review.stops import review_stop

    stop = ReviewStopped("expired")
    wrapper = UserError("tool failed")
    wrapper.__cause__ = stop
    assert review_stop(wrapper) is stop
    bug = RuntimeError("database failed while handling stop")
    bug.__context__ = stop
    assert review_stop(bug) is None
    wrapper.__cause__ = wrapper
    assert review_stop(wrapper) is None
