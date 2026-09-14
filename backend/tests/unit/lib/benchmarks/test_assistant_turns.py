import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.lib.benchmarks import assistant_turns as turns


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "cancel", "failure"])
async def test_model_tool_roundtrip_persists_then_finishes_and_cleans_up(monkeypatch, outcome):
    saved = []
    monkeypatch.setattr(turns, "_persist", lambda **values: saved.append(values))
    manager = SimpleNamespace(set_outcome_status=AsyncMock())
    monkeypatch.setattr(turns, "executable_run_manager", manager)

    async def model(**values):
        result = await values["executor"](
            "read_paper_reference_draft", {"draft_id": str(uuid4())}, "call",
        )
        assert result.full_output == {"revision": 2}
        if outcome == "failure":
            raise RuntimeError("private-provider-credential")
        values["state"].assistant_text_parts.append("Draft inspected")
        values["state"].usage_observed = True
        values["state"].input_tokens = 7
        yield {"type": "TEXT_DELTA", "delta": "Draft inspected"}

    monkeypatch.setattr(turns, "stream_benchmark_assistant", model)
    session, turn = str(uuid4()), str(uuid4())
    cancellation = asyncio.Event()
    events = []
    async for raw in turns.produce_turn(
        owner="human", session_id=session, turn_id=turn,
        input_items=[{"role": "user", "content": "Check draft"}], cancel_event=cancellation,
    ):
        item = json.loads(raw.removeprefix("data: "))
        events.append(item)
        if item["type"] == "TOOL_REQUEST":
            bridge = turns.active_bridges[turns.run_id(session, turn)]
            if outcome == "cancel":
                cancellation.set()
            else:
                bridge.reply(owner="human", request_id=UUID(item["tool_request_id"]), output={"revision": 2})
        if item["type"] in {"DONE", "CANCELLED"}:
            assert len(saved) == 1
    assert saved[0]["status"] == {"success": "completed", "cancel": "cancelled", "failure": "failed"}[outcome]
    assert saved[0]["owner"] == "human"
    assert turns.run_id(session, turn) not in turns.active_bridges
    assert "private-provider-credential" not in str(events)
    assert events[-1]["type"] == ("CANCELLED" if outcome == "cancel" else "DONE")
    if outcome == "failure":
        manager.set_outcome_status.assert_awaited_once_with(turns.run_id(session, turn), "failed")


@pytest.mark.asyncio
async def test_history_failure_does_not_report_done_or_leak_sql_context(monkeypatch):
    async def model(**_values):
        yield {"type": "TEXT_DELTA", "delta": "Answer"}

    def fail(**_values):
        raise RuntimeError("private-sql-conversation-parameters")

    monkeypatch.setattr(turns, "stream_benchmark_assistant", model)
    monkeypatch.setattr(turns, "_persist", fail)
    events = []
    with pytest.raises(RuntimeError, match="Assistant history save failed") as error:
        async for item in turns.produce_turn(
            owner="human", session_id="session", turn_id="turn", input_items=[],
            cancel_event=asyncio.Event(),
        ):
            events.append(item)
    assert error.value.__context__ is None
    assert not any('"DONE"' in item for item in events)
    assert not turns.active_bridges
