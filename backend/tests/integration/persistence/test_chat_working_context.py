"""Real PostgreSQL session isolation, atomic replacement, leases and cancellation."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.lib.chat_history_repository import ChatHistorySessionNotFoundError
from src.lib.openai_agents.chat_compaction_session import DurableChatHistorySession
from src.lib.openai_agents.model_context_view import ContextRecoveryError
from src.models.sql.chat_session import ChatSession
from src.models.sql.database import SessionLocal


@pytest.fixture
def contexts():
    owner = "context-fixture-" + uuid4().hex
    sessions = {}
    with SessionLocal() as db, db.begin():
        for kind in ("assistant_chat", "agent_studio"):
            session_id = uuid4().hex
            db.add(ChatSession(session_id=session_id, user_auth_sub=owner, chat_kind=kind))
            sessions[kind] = DurableChatHistorySession(session_id=session_id, user_id=owner, chat_kind=kind)
    try:
        yield sessions
    finally:
        for session in sessions.values():
            asyncio.run(session.release_run())
        with SessionLocal() as db, db.begin():
            db.execute(delete(ChatSession).where(ChatSession.user_auth_sub == owner))


@pytest.mark.asyncio
async def test_separate_chats_restore_complete_sdk_protocol_and_duplicate_user_occurrences(contexts):
    call = {"type": "function_call", "call_id": "saved", "name": "save", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "saved", "output": "saved once"}
    for kind, session in contexts.items():
        opaque = {"type": "compaction", "id": "cmp-" + kind, "encrypted_content": kind}
        await session.add_items([{"role": "user", "content": "earlier"}, opaque, call, output])
        await session.add_items([{"role": "user", "content": "Again"}])
        await session.add_items([{"role": "user", "content": "Again"}])
        restarted = DurableChatHistorySession(session_id=session.session_id, user_id=session.user_id, chat_kind=kind)
        assert await restarted.get_items() == [opaque, call, output,
            {"role": "user", "content": "Again"}, {"role": "user", "content": "Again"}]
        wrong_kind = "agent_studio" if kind == "assistant_chat" else "assistant_chat"
        with pytest.raises(ChatHistorySessionNotFoundError):
            await DurableChatHistorySession(session_id=session.session_id, user_id=session.user_id, chat_kind=wrong_kind).get_items()
        with pytest.raises(ChatHistorySessionNotFoundError):
            await DurableChatHistorySession(session_id=session.session_id, user_id="different-owner", chat_kind=kind).get_items()
        await restarted.clear_session()
        assert await restarted.get_items() == []
        await restarted.add_items([{"role": "user", "content": "fresh"}])
        assert await restarted.pop_item() == {"role": "user", "content": "fresh"}
        assert await restarted.get_items() == []


@pytest.mark.asyncio
async def test_concurrent_chat_invocations_rejected_before_tools_and_independent_chats_continue(contexts):
    session = contexts["assistant_chat"]
    contender = DurableChatHistorySession(session_id=session.session_id, user_id=session.user_id)
    await session.acquire_run()
    with pytest.raises(ContextRecoveryError, match="already has a running request"):
        await contender.acquire_run()
    await contexts["agent_studio"].acquire_run()
    await contexts["agent_studio"].release_run()
    await session.release_run()
    await contender.acquire_run()
    await contender.release_run()


@pytest.mark.asyncio
async def test_failed_projection_write_keeps_prior_window_atomically(contexts, monkeypatch):
    session = contexts["assistant_chat"]
    original = [{"role": "user", "content": "NOT observed α😀"}]
    await session.add_items(original)
    from src.lib.openai_agents import chat_compaction_session as module
    def failed_append(*args, **kwargs):
        raise RuntimeError("injected write failure after delete")
    monkeypatch.setattr(module.ChatHistoryRepository, "append_message", failed_append)
    with pytest.raises(RuntimeError, match="injected"):
        await session.add_items([{"role": "assistant", "content": "new"}])
    assert await session.get_items() == original


def test_concurrent_session_appends_keep_both_committed_deltas(contexts):
    session = contexts["assistant_chat"]
    def append(value):
        independent = DurableChatHistorySession(session_id=session.session_id, user_id=session.user_id)
        independent._add_items_sync([{"role": "user", "content": value}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(append, ["one", "two"]))
    assert sorted(item["content"] for item in asyncio.run(session.get_items())) == ["one", "two"]


@pytest.mark.asyncio
async def test_cancellation_drains_owned_work_before_releasing_lease(contexts):
    session = contexts["assistant_chat"]
    await session.acquire_run()
    stopped = asyncio.Event()
    async def owned_run():
        try:
            await asyncio.Event().wait()
        finally:
            await session.add_items([{"role": "assistant", "content": "cancelled checkpoint"}])
            stopped.set()
    task = asyncio.create_task(owned_run())
    await asyncio.sleep(0)
    class Result:
        run_loop_task = task
        def cancel(self):
            task.cancel()
    await session.release_run(Result())
    assert stopped.is_set()
    contender = DurableChatHistorySession(session_id=session.session_id, user_id=session.user_id)
    await contender.acquire_run()
    await contender.release_run()
    assert await session.get_items() == [{"role": "assistant", "content": "cancelled checkpoint"}]


@pytest.mark.asyncio
async def test_capacity_restore_matches_sdk_persistence_and_reconciles_delivered_page(contexts, monkeypatch):
    from agents import Agent, ModelSettings
    from agents.items import ToolCallItem, ItemHelpers
    from agents.run_internal.items import run_item_to_input_item
    from openai.types.responses import ResponseFunctionToolCall, ResponseCompactionItem
    from src.lib.openai_agents.context_capacity import ContextCapacityPolicy
    from src.lib.openai_agents.run_history import HistoryScope, RunHistory, record_model_history
    from src.models.sql.agent_run_history import AgentHistoryEvent
    from tests.unit.lib.openai_agents.test_context_capacity import FakeRequest
    session = contexts["agent_studio"]
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: session.user_id)
    monkeypatch.setattr("src.lib.context.get_current_session_id", lambda: session.session_id)
    trace = uuid4().hex
    monkeypatch.setattr("src.lib.context.get_current_trace_id", lambda: trace)
    def policy():
        invocation = str(uuid4())
        history = RunHistory(HistoryScope(session.user_id, trace, invocation, "agent_studio_authoring",
            invocation + "/", session_id=session.session_id, session_reader=True))
        history.context_policy = ContextCapacityPolicy(history)
        return history.context_policy
    first = policy()
    source = [{"role": "user", "content": "Exact NOT observed α😀. " * 600}]
    first.canonical_input = source
    try:
        await first.prepare(FakeRequest(3000), source, ModelSettings())
        raw_call = ResponseFunctionToolCall(type="function_call", call_id="pending-1", name="read_pending_context", arguments="{}")
        call = run_item_to_input_item(ToolCallItem(agent=Agent(name="fixture"), raw_item=raw_call))
        await record_model_history(first.history, "model_output", [raw_call])
        assert first.last_output == [call]  # Real SDK excludes unset namespace/status.
        page = await first.next_page("pending-1")
        output = {"type": "function_call_output", "call_id": "pending-1", "output": ItemHelpers._convert_tool_output(page)}
        # Cancellation between SDK page persistence and the next model prepare.
        await session.add_items([*source, call, output])
        restored_items = await session.get_items()
        second = policy()
        second.canonical_input = restored_items
        await second.prepare(FakeRequest(3000), restored_items, ModelSettings())
        assert second.pending[0].cursor == page["returned_range"]["end"]
        assert second.pending[0].body == source[0]["content"]
        # A native boundary is committed next; its new session prefix must match.
        native = ResponseCompactionItem(type="compaction", id="cmp-reconnect", encrypted_content="fixture-native-state")
        await record_model_history(second.history, "model_output", [native])
        await session.add_items(second.last_output)
        compacted_items = await session.get_items()
        assert compacted_items[0]["type"] == "compaction"
        third = policy()
        await third.restore(compacted_items)
        assert third.pending[0].cursor == second.pending[0].cursor
        assert third.pending[0].body == source[0]["content"]
        # An uncommitted future checkpoint must not override the matching one.
        third.canonical_input = compacted_items
        third.last_input = compacted_items
        third.observe_output([ResponseFunctionToolCall(type="function_call", call_id="uncommitted", name="read_pending_context", arguments="{}")])
        await third.checkpoint_output(native_compaction=True)
        fourth = policy()
        await fourth.restore(compacted_items)
        assert fourth.pending[0].cursor == third.pending[0].cursor
        assert all(item.get("call_id") != "uncommitted" for item in fourth.apply(compacted_items))
    finally:
        with SessionLocal() as db, db.begin():
            db.execute(delete(AgentHistoryEvent).where(AgentHistoryEvent.owner_id == session.user_id))


@pytest.mark.asyncio
async def test_lease_does_not_reserve_application_pool_connection(contexts):
    from src.models.sql.database import engine
    before = engine.pool.checkedout()
    await contexts["assistant_chat"].acquire_run()
    await contexts["agent_studio"].acquire_run()
    assert engine.pool.checkedout() == before
    with SessionLocal() as db:
        assert db.get(ChatSession, contexts["assistant_chat"].session_id) is not None
