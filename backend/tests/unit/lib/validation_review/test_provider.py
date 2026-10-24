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
    with p.budget_scope(budget), pytest.raises(
        ReviewStopped, match="hidden_http_retries"
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
