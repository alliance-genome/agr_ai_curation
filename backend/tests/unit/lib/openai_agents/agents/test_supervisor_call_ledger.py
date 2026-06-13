"""Unit tests for the supervisor chat no-progress brake (SupervisorCallLedger).

These cover the per-chat-turn ledger that collapses identical concurrent
specialist calls, short-circuits sequential repeats, and enforces a per-turn
invocation budget. The ledger is the runaway/no-progress brake for standard
chat (flows enforce strict step order via flows/ and bypass the ledger).
"""
import asyncio

import pytest

from src.lib.openai_agents.agents.supervisor_agent import (
    _LEDGER_BUDGET_EXCEEDED_MESSAGE,
    _LEDGER_REPLAY_INSTRUCTION,
    SupervisorCallLedger,
)
from src.lib.openai_agents.streaming_tools import SupervisorExtractionHandoff


def _make_ledger(*, max_total_calls: int = 25, max_calls_per_tool: int = 8) -> SupervisorCallLedger:
    return SupervisorCallLedger(
        max_total_calls=max_total_calls,
        max_calls_per_tool=max_calls_per_tool,
    )


class _CountingFactory:
    """Factory whose returned coroutine increments a call counter once invoked."""

    def __init__(self, result: str = "specialist result"):
        self.calls = 0
        self.result = result

    def __call__(self):
        async def _run() -> str:
            self.calls += 1
            return self.result

        return _run()


def _make_handoff(
    *,
    result_ref: str = "extraction-result:00000000-0000-4000-8000-000000000001",
    result_status: str = "non_empty_extraction_ready",
    object_count: int = 3,
) -> SupervisorExtractionHandoff:
    return SupervisorExtractionHandoff(
        tool_name="ask_allele_specialist",
        specialist_name="Allele/Variant Extraction",
        result_ref=result_ref,
        extraction_result_id=result_ref.removeprefix("extraction-result:"),
        result_status=result_status,
        object_count=object_count,
        domain_pack_id="fixture.pack.allele",
        adapter_key="ALLELE",
        agent_key="allele_extraction",
        created_new=True,
    )


@pytest.mark.asyncio
async def test_sequential_repeat_runs_once_and_replays_with_instruction():
    """A repeated (tool, query) runs the specialist once; the repeat replays cached text."""
    ledger = _make_ledger()
    factory = _CountingFactory(result="allele unresolved")

    first = await ledger.run_or_replay("ask_allele_specialist", "find allele X", factory)
    second = await ledger.run_or_replay("ask_allele_specialist", "find allele X", factory)

    # Underlying run invoked exactly once.
    assert factory.calls == 1
    # First call returns the raw specialist result, no instruction prefix.
    assert first == "allele unresolved"
    assert _LEDGER_REPLAY_INSTRUCTION not in first
    # Second call replays the cached text prefixed with the terminal instruction.
    assert second.startswith(_LEDGER_REPLAY_INSTRUCTION)
    assert "allele unresolved" in second


@pytest.mark.asyncio
async def test_cached_extraction_replay_points_to_existing_result_ref_without_blocking_new_work():
    """A repeated extraction query gets result-ref guidance; distinct work still runs."""
    ledger = _make_ledger()
    factory = _CountingFactory(
        result=(
            "Extraction result ready: fixture.pack.allele\n"
            "Result ref: extraction-result:00000000-0000-4000-8000-000000000001"
        )
    )

    first = await ledger.run_or_replay("ask_allele_specialist", "find allele X", factory)
    ledger.record_extraction_handoff(
        "ask_allele_specialist",
        "find allele X",
        _make_handoff(),
    )
    second = await ledger.run_or_replay("ask_allele_specialist", "find allele X", factory)

    assert factory.calls == 1
    assert first.startswith("Extraction result ready")
    assert second.startswith(_LEDGER_REPLAY_INSTRUCTION)
    assert 'inspect_results(result_ref="extraction-result:00000000-0000-4000-8000-000000000001"' in second
    assert "3 retained objects" in second
    assert "Rerun a specialist only when the curator changes scope" in second
    assert ledger.latest_extraction_handoffs()[0].result_ref.endswith("000000000001")

    # The guidance is not a specialist ban. A materially different request still
    # invokes its own specialist under the normal per-turn budget.
    generic_factory = _CountingFactory(result="broader PDF extraction ran")
    generic = await ledger.run_or_replay(
        "ask_generic_pdf_extraction_specialist",
        "search the full PDF for anything else relevant",
        generic_factory,
    )
    assert generic == "broader PDF extraction ran"
    assert generic_factory.calls == 1


@pytest.mark.asyncio
async def test_cached_empty_extraction_replay_guides_report_or_clarify_path():
    """Empty extraction replays should steer away from silent retry loops."""
    ledger = _make_ledger()
    factory = _CountingFactory(result="Extraction result ready: empty")

    await ledger.run_or_replay("ask_allele_specialist", "find allele X", factory)
    ledger.record_extraction_handoff(
        "ask_allele_specialist",
        "find allele X",
        _make_handoff(result_status="empty_extraction", object_count=0),
    )
    replay = await ledger.run_or_replay("ask_allele_specialist", "find allele X", factory)

    assert factory.calls == 1
    assert replay.startswith(_LEDGER_REPLAY_INSTRUCTION)
    assert "already produced an empty extraction result" in replay
    assert "ask for clarification" in replay
    assert "materially different retry" in replay


@pytest.mark.asyncio
async def test_query_normalization_treats_whitespace_and_case_as_same_key():
    """Normalized keying collapses case/whitespace variants of the same query."""
    ledger = _make_ledger()
    factory = _CountingFactory()

    await ledger.run_or_replay("ask_allele_specialist", "Find  Allele X", factory)
    replay = await ledger.run_or_replay("ask_allele_specialist", "find allele x", factory)

    assert factory.calls == 1
    assert replay.startswith(_LEDGER_REPLAY_INSTRUCTION)


@pytest.mark.asyncio
async def test_concurrent_identical_calls_run_underlying_once():
    """Four simultaneous identical calls (Sue's storm) collapse to one underlying run."""
    ledger = _make_ledger()
    started = 0
    release = asyncio.Event()

    def factory():
        async def _run() -> str:
            nonlocal started
            started += 1
            # Hold the owner's run open so the other callers arrive while in-flight.
            await release.wait()
            return "unresolved"

        return _run()

    async def _call():
        return await ledger.run_or_replay("ask_allele_specialist", "same lookup", factory)

    tasks = [asyncio.create_task(_call()) for _ in range(4)]
    # Let all four register against the key before the owner completes.
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)

    # Underlying specialist ran exactly once despite four concurrent callers.
    assert started == 1
    # All callers receive the result.
    assert all("unresolved" in r for r in results)


@pytest.mark.asyncio
async def test_different_queries_run_underlying_twice():
    """Two DIFFERENT queries each run -- parallelism for distinct work is preserved."""
    ledger = _make_ledger()
    factory = _CountingFactory()

    await ledger.run_or_replay("ask_allele_specialist", "find allele X", factory)
    await ledger.run_or_replay("ask_allele_specialist", "find allele Y", factory)

    assert factory.calls == 2


@pytest.mark.asyncio
async def test_different_tools_same_query_run_underlying_twice():
    """Same query against different specialists are distinct keys and both run."""
    ledger = _make_ledger()
    factory = _CountingFactory()

    await ledger.run_or_replay("ask_allele_specialist", "lookup", factory)
    await ledger.run_or_replay("ask_gene_specialist", "lookup", factory)

    assert factory.calls == 2


@pytest.mark.asyncio
async def test_per_specialist_budget_short_circuits_without_running():
    """After the per-specialist cap, further distinct calls short-circuit with the summarize message."""
    ledger = _make_ledger(max_calls_per_tool=3, max_total_calls=25)
    factory = _CountingFactory()

    for i in range(3):
        await ledger.run_or_replay("ask_allele_specialist", f"query {i}", factory)
    assert factory.calls == 3

    blocked = await ledger.run_or_replay("ask_allele_specialist", "query 4", factory)
    assert blocked == _LEDGER_BUDGET_EXCEEDED_MESSAGE
    # Underlying run was NOT invoked for the blocked call.
    assert factory.calls == 3


@pytest.mark.asyncio
async def test_total_budget_short_circuits_across_specialists():
    """After the total cap, further distinct calls to any specialist short-circuit."""
    ledger = _make_ledger(max_total_calls=2, max_calls_per_tool=8)
    factory = _CountingFactory()

    await ledger.run_or_replay("ask_allele_specialist", "q1", factory)
    await ledger.run_or_replay("ask_gene_specialist", "q2", factory)
    assert factory.calls == 2

    blocked = await ledger.run_or_replay("ask_disease_specialist", "q3", factory)
    assert blocked == _LEDGER_BUDGET_EXCEEDED_MESSAGE
    assert factory.calls == 2


@pytest.mark.asyncio
async def test_cached_replay_does_not_count_against_budget():
    """Sequential repeats replay cache and never consume budget."""
    ledger = _make_ledger(max_calls_per_tool=2, max_total_calls=25)
    factory = _CountingFactory()

    await ledger.run_or_replay("ask_allele_specialist", "q1", factory)
    # Many repeats of the SAME key must not exhaust the per-tool budget.
    for _ in range(10):
        replay = await ledger.run_or_replay("ask_allele_specialist", "q1", factory)
        assert replay.startswith(_LEDGER_REPLAY_INSTRUCTION)
    assert factory.calls == 1

    # A second distinct query still fits within the per-tool budget of 2.
    await ledger.run_or_replay("ask_allele_specialist", "q2", factory)
    assert factory.calls == 2


@pytest.mark.asyncio
async def test_exception_propagates_and_clears_key_for_retry():
    """A raising run propagates, then clears the key so a later legitimate retry runs."""
    ledger = _make_ledger()
    attempts = 0

    def failing_factory():
        async def _run() -> str:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("specialist boom")

        return _run()

    with pytest.raises(RuntimeError, match="specialist boom"):
        await ledger.run_or_replay("ask_allele_specialist", "q", failing_factory)

    # Key was cleared; a subsequent call with the same key re-runs (not cached).
    ok_factory = _CountingFactory(result="recovered")
    result = await ledger.run_or_replay("ask_allele_specialist", "q", ok_factory)
    assert result == "recovered"
    assert ok_factory.calls == 1
    assert attempts == 1


@pytest.mark.asyncio
async def test_concurrent_identical_failure_propagates_to_all_awaiters():
    """An in-flight failure propagates to all concurrent awaiters of the same key."""
    ledger = _make_ledger()
    release = asyncio.Event()
    started = 0

    def failing_factory():
        async def _run() -> str:
            nonlocal started
            started += 1
            await release.wait()
            raise RuntimeError("boom")

        return _run()

    async def _call():
        return await ledger.run_or_replay("ask_allele_specialist", "same", failing_factory)

    tasks = [asyncio.create_task(_call()) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert started == 1
    assert all(isinstance(r, RuntimeError) for r in results)

    # After failure the key is cleared, so a fresh call re-runs.
    ok_factory = _CountingFactory(result="ok")
    assert await ledger.run_or_replay("ask_allele_specialist", "same", ok_factory) == "ok"
    assert ok_factory.calls == 1
