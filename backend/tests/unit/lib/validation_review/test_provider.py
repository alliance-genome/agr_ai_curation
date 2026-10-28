"""Review usage metering without speculative output reservations or live calls."""

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
async def test_output_settings_preserved_and_only_actual_tokens_are_charged(setup):
    budget, request, reserve, reconcile = setup
    with p.budget_scope(budget):
        settings, reservation = await admit()
    assert settings.max_tokens == 128000
    assert reserve.call_args.kwargs["charges"] == {
        "reviewer_tokens": 0,
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
async def test_nested_scope_restores_parent_and_compaction_is_metered(setup):
    budget, _, reserve, reconcile = setup
    with p.budget_scope(budget):
        p.guard_direct_request(provider="openai", api="responses.compact")
        reservation = await p.register_provider_call(operation="responses.compact", model="model")
        assert p.ACTIVE_BUDGET.get() is budget
    assert p.ACTIVE_BUDGET.get() is None
    assert reserve.call_args.kwargs["charges"] == {"reviewer_tokens": 0}
    assert reserve.call_args.kwargs["request"]["operation"] == "responses.compact"
    p.settle(reservation, {"status": "reported", "input_tokens": 300000, "output_tokens": 4000})
    assert reconcile.call_args.kwargs["actual"] == {"reviewer_tokens": 304000}


def test_reviewer_identity_is_mandatory():
    with pytest.raises(ValueError, match="explicit reviewer"):
        p.ExecutionBudget(
            HistoryScope("o", "00000000-0000-0000-0000-000000000001", "s"),
            "e",
            "reviewer_tokens",
            True,
        )


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
    assert reserve.call_args.kwargs["charges"] == {"reviewer_tokens": 0}


@pytest.mark.asyncio
async def test_native_compaction_and_unset_output_cap_are_preserved(setup):
    budget, request, reserve, _ = setup
    settings = ModelSettings(extra_body={"context_management": [{"type": "compaction", "compact_threshold": 250000}]})
    with p.budget_scope(budget):
        returned, _ = await admit(settings)
    assert returned is settings
    assert returned.max_tokens is None
    request.count.assert_not_awaited()  # Capacity checking belongs to the shared context path.
    assert reserve.call_args.kwargs["request"]["usage_metered"] is True


@pytest.mark.asyncio
async def test_original_reservation_cliff_does_not_block_next_call(setup):
    budget, _, reserve, reconcile = setup
    with p.budget_scope(budget):
        for input_tokens, output_tokens in [(8331, 48), (8700, 44), (9000, 50)]:
            settings, reservation = await admit()
            assert settings.max_tokens == 128000
            p.settle(reservation, {"status": "reported", "input_tokens": input_tokens, "output_tokens": output_tokens})
    assert reserve.call_count == 3
    assert all(c.kwargs["charges"]["reviewer_tokens"] == 0 for c in reserve.call_args_list)
    assert sum(c.kwargs["actual"]["reviewer_tokens"] for c in reconcile.call_args_list) == 26173


@pytest.mark.asyncio
async def test_structural_stop_still_refuses_provider_dispatch(setup):
    budget, _, reserve, _ = setup
    reserve.side_effect = ReviewStopped("cancelled")
    with p.budget_scope(budget), pytest.raises(ReviewStopped, match="cancelled"):
        await admit()
    assert not budget.execution.provider_admitted


@pytest.mark.asyncio
async def test_unrecognized_direct_requests_remain_explicit(setup):
    budget, _, reserve, _ = setup
    with p.budget_scope(budget), pytest.raises(ReviewStopped, match="unsupported_review_direct"):
        p.guard_direct_request(provider="openai", api="chat.completions")
    reserve.assert_not_called()


@pytest.mark.asyncio
async def test_uncapped_clock_is_initialized_once_and_remains_cancellable(setup, monkeypatch):
    import asyncio
    budget, _, _, _ = setup
    read = Mock(return_value=None)
    monkeypatch.setattr(p, "remaining_seconds", read)
    with p.budget_scope(budget):
        for _ in range(300):
            await p.guarded_await(asyncio.sleep(0))
        task = asyncio.create_task(p.guarded_await(asyncio.Event().wait()))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert budget.execution.clock_initialized
    assert budget.execution.deadline is None
    read.assert_called_once_with(budget)


@pytest.mark.asyncio
async def test_explicit_deadline_still_interrupts(setup, monkeypatch):
    import asyncio
    budget, _, _, _ = setup
    monkeypatch.setattr(p, "remaining_seconds", lambda _: 0)
    with p.budget_scope(budget), pytest.raises(TimeoutError) as caught:
        await p.guarded_await(asyncio.Event().wait())
    assert caught.value._review_deadline_expired


@pytest.mark.asyncio
async def test_cancelled_admission_drains_and_records_unsent_usage(setup, monkeypatch):
    import asyncio
    from threading import Event

    budget, _, _, reconcile = setup
    entered, release = Event(), Event()

    def reserve(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return "reserved-before-cancellation"

    monkeypatch.setattr(p.admission, "reserve", reserve)
    with p.budget_scope(budget):
        task = asyncio.create_task(p.register_provider_call(operation="responses", model="fixture"))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            delivered = asyncio.get_running_loop().create_future()
            asyncio.get_running_loop().call_soon(delivered.set_result, None)
            await delivered
            assert not task.done()
            task.cancel()  # repeated cancellation cannot detach the reservation
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert budget.execution.provider_admitted
            assert budget.execution.owner_stopped
            reconcile.assert_called_once_with(budget.scope, budget.episode_id,
                "reserved-before-cancellation", actual={"reviewer_tokens": 0})
            with pytest.raises(p.admission.ReviewStopped, match="cancelled"):
                await p.register_provider_call(operation="responses", model="fixture")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_owner_stop_on_another_thread_prevents_reserved_call_from_being_sent(setup, monkeypatch):
    budget, _, _, reconcile = setup

    def reserve(*args, **kwargs):
        # Simulates the owning event loop stopping a validator while its own
        # event loop is awaiting SQL, without cancelling that worker loop.
        budget.execution.owner_stopped = True
        return "unsent"

    monkeypatch.setattr(p.admission, "reserve", reserve)
    with p.budget_scope(budget):
        with pytest.raises(p.admission.ReviewStopped, match="cancelled"):
            await p.register_provider_call(operation="responses", model="fixture")
    assert budget.execution.provider_admitted
    reconcile.assert_called_once_with(budget.scope, budget.episode_id, "unsent",
                                      actual={"reviewer_tokens": 0})
