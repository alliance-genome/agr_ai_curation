import asyncio
from uuid import UUID, uuid4

import pytest

from src.lib.benchmarks.assistant_tool_bridge import AssistantToolBridge, AssistantToolReplyError


@pytest.mark.asyncio
async def test_read_reply_is_owner_bound_and_idempotent():
    bridge = AssistantToolBridge(owner="human", cancel_event=asyncio.Event())
    draft_id = str(uuid4())
    call = asyncio.create_task(bridge.execute("read_paper_reference_draft", {"draft_id": draft_id}, "call"))
    request = await bridge.requests.get()
    assert request["arguments"] == {"draft_id": draft_id}
    reply = dict(owner="human", request_id=UUID(request["tool_request_id"]), output={"revision": 1})
    with pytest.raises(PermissionError):
        bridge.reply(**(reply | {"owner": "other"}))
    bridge.reply(**reply)
    bridge.reply(**reply)
    with pytest.raises(AssistantToolReplyError, match="changed"):
        bridge.reply(**(reply | {"output": {"revision": 2}}))
    result = await call
    reply["output"]["revision"] = 99
    assert result.full_output == {"revision": 1}
    assert result.provider_output == '{"revision":1}'
    bridge.close()
    with pytest.raises(AssistantToolReplyError):
        bridge.reply(**reply)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cancel", "close", "timeout"])
async def test_wait_ends_without_hanging_or_accepting_late_reply(monkeypatch, operation):
    monkeypatch.setenv("BENCHMARK_ASSISTANT_TOOL_TIMEOUT_SECONDS", "0.1")
    bridge = AssistantToolBridge(owner="human", cancel_event=asyncio.Event())
    call = asyncio.create_task(bridge.execute("read_paper_reference_draft", {"draft_id": str(uuid4())}, None))
    request = await bridge.requests.get()
    if operation == "cancel":
        bridge.cancel_event.set()
    elif operation == "close":
        bridge.close()
    with pytest.raises(AssistantToolReplyError if operation == "timeout" else asyncio.CancelledError):
        await call
    with pytest.raises(AssistantToolReplyError):
        bridge.reply(owner="human", request_id=UUID(request["tool_request_id"]), output={})
    assert not bridge._pending


@pytest.mark.asyncio
async def test_forbidden_tools_arguments_and_limits_fail_before_dispatch(monkeypatch):
    monkeypatch.setenv("BENCHMARK_ASSISTANT_MAX_TOOL_CALLS", "1")
    monkeypatch.setenv("BENCHMARK_ASSISTANT_TOOL_RESULT_MAX_BYTES", "8")
    bridge = AssistantToolBridge(owner="human", cancel_event=asyncio.Event())
    with pytest.raises(PermissionError):
        await bridge.execute("run_benchmark", {}, None)
    with pytest.raises(ValueError):
        await bridge.execute("read_paper_reference_draft", {"draft_id": str(uuid4()), "owner": "other"}, None)
    assert bridge.requests.empty()
    call = asyncio.create_task(bridge.execute("read_paper_reference_draft", {"draft_id": str(uuid4())}, None))
    request = await bridge.requests.get()
    with pytest.raises(AssistantToolReplyError, match="exceeds"):
        bridge.reply(owner="human", request_id=UUID(request["tool_request_id"]), output={"long": "response"})
    with pytest.raises(AssistantToolReplyError, match="budget"):
        await bridge.execute("read_paper_reference_draft", {"draft_id": str(uuid4())}, None)
    bridge.reply(owner="human", request_id=UUID(request["tool_request_id"]), output={})
    assert (await call).full_output == {}
