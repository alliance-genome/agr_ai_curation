"""Hard-budget admission without cost-ledger reporting or live provider calls."""

from unittest.mock import AsyncMock, Mock

import pytest
from agents import ModelSettings
from src.lib.validation_review import provider as p
from src.lib.validation_review.admission import ReviewStopped
from src.lib.validation_history.store import HistoryScope


@pytest.fixture
def setup(monkeypatch):
    budget = p.ExecutionBudget(
        HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        "episode",
        "reviewer_tokens",
        True,
        reviewer_agent_id="reviewer",
    )
    request = Mock()
    request.effective_model = "model"
    request._wire.return_value = {"model": "model", "max_output_tokens": 128000}
    request.count = AsyncMock(return_value=230000)
    monkeypatch.setattr(
        "src.lib.openai_agents.context_capacity.NativeCapacityRequest",
        Mock(return_value=request),
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.model_request_measurement.describe_model",
        lambda *a, **kw: ("openai", "responses", "websocket"),
    )
    monkeypatch.setattr(p, "remaining_seconds", lambda _: 30)
    monkeypatch.setattr(
        "src.lib.observability.cost_context.get_agent_cost_identity",
        lambda agent: {"agent_id": "reviewer"},
    )
    reserve, reconcile = Mock(return_value="reservation"), Mock()
    monkeypatch.setattr(p.admission, "reserve", reserve)
    monkeypatch.setattr(p.admission, "reconcile", reconcile)
    return budget, request, reserve, reconcile


async def admit(settings=None):
    return await p.admit_model(
        Mock(),
        settings or ModelSettings(max_tokens=128000),
        instructions="policy",
        input="full input",
        tools=[],
        output_schema=None,
        handoffs=[],
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
        provider_hint="openai",
        agent=Mock(),
    )


@pytest.mark.asyncio
async def test_exact_input_plus_existing_output_ceiling_is_reserved_before_call(setup):
    budget, request, reserve, reconcile = setup
    with p.budget_scope(budget):
        settings, reservation = await admit()
    assert settings.max_tokens == 128000
    assert reserve.call_args.kwargs["charges"] == {
        "reviewer_tokens": 358000,
        "reviewer_turns": 1,
    }
    p.settle(
        reservation,
        {"status": "reported", "input_tokens": 230000, "output_tokens": 1000},
    )
    assert reconcile.call_args.kwargs["actual"] == {
        "reviewer_tokens": 231000,
        "reviewer_turns": 1,
    }


@pytest.mark.asyncio
async def test_missing_usage_does_not_release_uncertain_reservation(setup):
    budget, _, _, reconcile = setup
    with p.budget_scope(budget):
        _, reservation = await admit()
    p.settle(reservation, {"status": "not_reported"})
    assert reconcile.call_args.kwargs["actual"] is None


@pytest.mark.asyncio
async def test_hidden_retry_mode_stops_before_count_or_admission(setup, monkeypatch):
    budget, request, reserve, _ = setup
    monkeypatch.setattr(
        "src.lib.openai_agents.model_request_measurement.describe_model",
        lambda *a, **kw: ("openai", "responses", "http"),
    )
    with (
        p.budget_scope(budget),
        pytest.raises(ReviewStopped, match="hidden_http_retries"),
    ):
        await admit()
    request.count.assert_not_awaited()
    reserve.assert_not_called()


@pytest.mark.asyncio
async def test_nested_scope_restores_parent_and_direct_compaction_is_explicit_stop(
    setup,
):
    budget, _, _, _ = setup
    with p.budget_scope(budget):
        with pytest.raises(ReviewStopped, match="direct_request"):
            p.guard_direct_request()
        assert p.ACTIVE_BUDGET.get() is budget
    assert p.ACTIVE_BUDGET.get() is None
    p.guard_direct_request()


def test_reviewer_identity_is_mandatory():
    with pytest.raises(ValueError, match="explicit reviewer"):
        p.ExecutionBudget(HistoryScope("o", "d", "s"), "e", "reviewer_tokens", True)


@pytest.mark.asyncio
async def test_stream_waits_read_fixed_deadline_only_once(setup, monkeypatch):
    import asyncio

    budget, _, _, _ = setup
    read = Mock(return_value=30)
    monkeypatch.setattr(p, "remaining_seconds", read)
    with p.budget_scope(budget):
        for _ in range(200):
            await p.guarded_await(asyncio.sleep(0))
    read.assert_called_once_with(budget)


@pytest.mark.asyncio
async def test_nested_agent_tokens_count_without_spending_reviewer_turns(
    setup, monkeypatch
):
    budget, _, reserve, _ = setup
    monkeypatch.setattr(
        "src.lib.observability.cost_context.get_agent_cost_identity",
        lambda agent: {"agent_id": "paper_reader"},
    )
    with p.budget_scope(budget):
        await admit()
    assert reserve.call_args.kwargs["charges"] == {"reviewer_tokens": 358000}


@pytest.mark.asyncio
async def test_capacity_incompatibility_is_expected_before_send(setup, monkeypatch):
    from src.lib.openai_agents.model_context_view import ContextRecoveryError

    budget, _, reserve, _ = setup
    monkeypatch.setattr(
        "src.lib.openai_agents.context_capacity.NativeCapacityRequest",
        Mock(side_effect=ContextRecoveryError("unsupported shape")),
    )
    with (
        p.budget_scope(budget),
        pytest.raises(ReviewStopped, match="unsupported_exact_context"),
    ):
        await admit()
    reserve.assert_not_called()
