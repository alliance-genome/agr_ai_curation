"""Capacity recovery uses real provider accounting, with no paid requests."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from agents import ModelSettings

from src.lib.openai_agents.context_capacity import ContextCapacityPolicy, NativeCapacityRequest, closed_boundaries
from src.lib.openai_agents.run_history import exact_json


class FakeRequest:
    # Synthetic accounting units; these are NOT a model tokenizer or production
    # size estimate. Wire accounting is tested separately with the installed SDK.
    def __init__(self, capacity=2000):
        self.capacity = capacity
        self.has_context_tool = True
        self.count_calls = 0
        self.count_duration_ms = 0
        self.compactions = []
    async def count(self, items):
        self.count_calls += 1
        return len(exact_json(items))
    def set_settings(self, settings):
        self.settings = settings
    async def compact(self, items):
        self.compactions.append(deepcopy(items))
        return [{"type": "compaction", "encrypted_content": "exact-provider-fixture"}]


class MemoryHistory:
    healthy = True
    scope = SimpleNamespace(session_reader=False)
    native_compaction_keys = set()
    def __init__(self):
        self.rows = []
    async def record(self, kind, value):
        self.rows.append((kind, deepcopy(value)))
        return str(len(self.rows))
    def assert_current_scope(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["user", "tool", "validation", "specialist", "restored", "parallel"])
async def test_crossing_soft_threshold_preserves_complete_input(kind):
    request = FakeRequest(10000)
    policy = ContextCapacityPolicy(MemoryHistory())
    items = [{"role": "user", "content": f"{kind}: " + "NOT observed α😀 " * 100}]
    original = deepcopy(items)
    result, settings = await policy.prepare(request, items, ModelSettings())
    assert result == original == items
    assert request.compactions == [] and policy.pending == []


@pytest.mark.asyncio
async def test_overflow_compacts_completed_prefix_preserving_pending_parallel_group():
    request = FakeRequest()
    policy = ContextCapacityPolicy(MemoryHistory())
    prior = [{"role": "user", "content": "Earlier constraints " * 60}]
    pending = [
        {"type": "reasoning", "encrypted_content": "reasoning-for-calls"},
        {"type": "function_call", "call_id": "a", "name": "write", "arguments": "{}"},
        {"type": "function_call", "call_id": "b", "name": "validate", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "a", "output": "saved exactly once " * 25},
        {"type": "function_call_output", "call_id": "b", "output": "NOT accepted " * 25},
    ]
    result, _ = await policy.prepare(request, prior + pending, ModelSettings())
    assert request.compactions == [prior]
    assert result[1:] == pending
    assert not policy.pending
    assert policy.apply(prior + pending + [{"role": "assistant", "content": "continue"}]) == result + [{"role": "assistant", "content": "continue"}]


@pytest.mark.asyncio
async def test_single_oversized_payload_has_exact_complete_coverage_and_no_tool_reexecution():
    history = MemoryHistory()
    policy = ContextCapacityPolicy(history)
    request = FakeRequest(3000)
    body = 'NOT present α😀. "Quoted" \\ exact\n' * 250
    canonical = [{"role": "user", "content": body}]
    policy.canonical_input = canonical
    result, settings = await policy.prepare(request, canonical, ModelSettings())
    assert settings.tool_choice == "read_pending_context"
    assert settings.parallel_tool_calls is False
    assert history.rows[0][1]["content"] == body
    parts = []
    index = 0
    while policy.pending:
        index += 1
        call = {"type": "function_call", "name": "read_pending_context", "call_id": str(index), "arguments": "{}"}
        policy.observe_output([call])
        page = await policy.next_page(str(index))
        assert page["returned_range"]["start"] == len("".join(parts))
        parts.append(page["content"])
        canonical = [*canonical, call, {"type": "function_call_output", "call_id": str(index), "output": str(page)}]
        policy.canonical_input = canonical
        result, settings = await policy.prepare(request, policy.apply(canonical), ModelSettings())
        assert await request.count(result) <= request.capacity
        if policy.pending:
            assert settings.tool_choice == "read_pending_context"
    assert index > 1
    assert "".join(parts) == body
    assert settings.tool_choice is None
    assert page["complete"] and page["pending_payloads"] == 0
    coverage = [value for kind, value in history.rows if kind == "context_ingress_coverage"]
    assert coverage[-1]["range"]["end"] == len(body)


def test_prefix_boundaries_do_not_orphan_calls():
    items = [{"role": "user", "content": "task"},
        {"type": "function_call", "call_id": "a"},
        {"type": "function_call", "call_id": "b"},
        {"type": "function_call_output", "call_id": "a"},
        {"type": "function_call_output", "call_id": "b"}]
    assert closed_boundaries(items, 5) == [1, 5]


@pytest.mark.asyncio
async def test_count_endpoint_receives_sdk_wire_tools_schema_instructions_and_opaque_state(monkeypatch):
    import httpx
    import json
    from openai import AsyncOpenAI
    from agents import function_tool
    from agents.models.openai_responses import OpenAIResponsesModel
    @function_tool
    def lookup(value: str) -> str:
        return value
    sent = []
    def transport(request):
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 123})
    client = AsyncOpenAI(api_key="fixture", http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    model = OpenAIResponsesModel(model="gpt-6-astra", openai_client=client)
    request = NativeCapacityRequest(model, ModelSettings(max_tokens=1000), instructions="Keep NOT observed.",
        tools=[lookup], output_schema=None, handoffs=[])
    items = [{"type": "compaction", "encrypted_content": "opaque"}, {"role": "user", "content": "Task α😀"}]
    assert await request.count(items) == 123
    path, wire = sent[0]
    assert path == "/v1/responses/input_tokens"
    assert wire["instructions"] == "Keep NOT observed."
    assert wire["input"] == items
    assert wire["tools"][0]["parameters"]["properties"]["value"]["type"] == "string"
    assert request.capacity == 922000
    assert request.count_calls == 1
    await client.close()


@pytest.mark.asyncio
async def test_history_larger_than_one_window_compacts_multiple_closed_prefixes():
    request = FakeRequest(2000)
    policy = ContextCapacityPolicy(MemoryHistory())
    history = [{"role": "assistant", "content": f"part {i}: " + "evidence " * 100} for i in range(8)]
    incoming = {"role": "user", "content": "Compare every prior finding."}
    result, _ = await policy.prepare(request, [*history, incoming], ModelSettings())
    assert len(request.compactions) > 1
    for prefix in request.compactions:
        assert await request.count(prefix) <= request.capacity
    assert result[-1] == incoming
    assert await request.count(result) <= request.capacity


@pytest.mark.asyncio
async def test_no_progress_compaction_is_not_retried(monkeypatch):
    from src.lib.openai_agents.model_context_view import ContextRecoveryError
    request = FakeRequest(2000)
    request.has_context_tool = False
    async def no_progress(items):
        request.compactions.append(deepcopy(items))
        return items
    request.compact = no_progress
    policy = ContextCapacityPolicy(MemoryHistory())
    with pytest.raises(ContextRecoveryError, match="no exact context-ingestion"):
        await policy.prepare(request, [{"role": "user", "content": "A" * 1500},
            {"role": "user", "content": "B" * 1500}], ModelSettings())
    assert len(request.compactions) == 1


@pytest.mark.asyncio
async def test_pending_source_survives_new_policy_without_reexecuting_original_tool():
    history = MemoryHistory()
    history.scope = SimpleNamespace(session_reader=True)
    def latest(scope, items):
        return next((deepcopy(value) for kind, value in reversed(history.rows)
                     if kind == "context_capacity_projection"), None)
    def read(scope, event_id):
        return {"payload": deepcopy(history.rows[int(event_id) - 1][1])}
    history.store = SimpleNamespace(latest_context_projection=latest, read=read)
    original = [{"role": "user", "content": "Exact NOT accepted α😀 " * 300}]
    first = ContextCapacityPolicy(history)
    await first.prepare(FakeRequest(3000), original, ModelSettings())
    second = ContextCapacityPolicy(history)
    result, settings = await second.prepare(FakeRequest(3000), original, ModelSettings())
    assert second.pending[0].body == original[0]["content"]
    assert second.pending[0].cursor == 0
    assert settings.tool_choice == "read_pending_context"
    assert len([row for row in history.rows if row[0] == "context_ingress"]) == 1
    assert "context_ingress_event" in result[0]["content"]


@pytest.mark.asyncio
async def test_single_oversized_historical_text_block_uses_shared_ingestion():
    history = MemoryHistory()
    policy = ContextCapacityPolicy(history)
    body = [{"type": "input_text", "text": "Earlier NOT observed α😀 " * 400}]
    latest = {"role": "user", "content": "Compare the earlier evidence."}
    result, settings = await policy.prepare(FakeRequest(3000), [
        {"role": "user", "content": body}, latest], ModelSettings())
    assert result[-1] == latest
    assert len(policy.pending) == 1
    assert policy.pending[0].body == exact_json(body)
    assert policy.pending[0].page(10)["encoding"] == "json"
    assert settings.tool_choice == "read_pending_context"


def test_compaction_boundaries_keep_reasoning_with_following_output():
    items = [{"role": "user", "content": "task"},
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "function_call", "call_id": "a"},
        {"type": "function_call_output", "call_id": "a"},
        {"type": "reasoning", "encrypted_content": "next"},
        {"role": "assistant", "content": "answer"}]
    assert closed_boundaries(items, len(items)) == [1, 4, 6]


@pytest.mark.asyncio
async def test_capacity_recovery_requires_storage_before_paid_compaction():
    from src.lib.openai_agents.model_context_view import HistoryStorageUnavailable
    request = FakeRequest(2000)
    with pytest.raises(HistoryStorageUnavailable):
        await ContextCapacityPolicy().prepare(request, [
            {"role": "user", "content": "A" * 1500},
            {"role": "user", "content": "B" * 1500}], ModelSettings())
    assert not request.compactions


@pytest.mark.asyncio
async def test_effective_extra_body_instructions_and_output_reserve_are_counted(monkeypatch):
    import httpx
    import json
    from openai import AsyncOpenAI
    from agents.models.openai_responses import OpenAIResponsesModel
    monkeypatch.setenv("AGENT_MODEL_CAPACITY_OVERRIDES_JSON", json.dumps({"gpt-6-astra": {
        "context_window_tokens": 10000, "max_input_tokens": 10000, "max_output_tokens": 4000}}))
    sent = []
    def reply(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"input_tokens": 100})
    async with AsyncOpenAI(api_key="fixture", http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply))) as client:
        model = OpenAIResponsesModel(model="gpt-6-astra", openai_client=client)
        request = NativeCapacityRequest(model, ModelSettings(max_tokens=100,
            extra_body={"instructions": "actual instructions", "max_output_tokens": 3000}),
            instructions="overridden", tools=[], output_schema=None, handoffs=[])
        assert request.capacity == 7000
        await request.count([{"role": "user", "content": "task"}])
        assert sent[0]["instructions"] == request.effective_instructions == "actual instructions"


@pytest.mark.asyncio
async def test_ordinary_outputs_do_not_duplicate_capacity_projection_windows():
    history = MemoryHistory()
    policy = ContextCapacityPolicy(history)
    items = [{"role": "user", "content": "exact source " * 500}]
    await policy.prepare(FakeRequest(3000), items, ModelSettings())
    def projections():
        return [value for kind, value in history.rows if kind == "context_capacity_projection"]
    assert len(projections()) == 1
    assert all("items" not in anchor for anchor in projections()[0]["anchors"])
    for _ in range(20):
        policy.observe_output([{"type": "function_call", "call_id": "a", "name": "read_pending_context", "arguments": "{}"}])
        await policy.checkpoint_output()
    assert len(projections()) == 1


@pytest.mark.asyncio
async def test_oversized_assistant_output_keeps_valid_message_blocks():
    from openai.types.responses import ResponseOutputMessage
    history = MemoryHistory()
    policy = ContextCapacityPolicy(history)
    message = {"type": "message", "role": "assistant", "id": "msg-old", "status": "completed",
        "content": [{"type": "output_text", "text": "Exact source α😀 " * 500, "annotations": []}]}
    original = deepcopy(message)
    result, _ = await policy.prepare(FakeRequest(3000), [message], ModelSettings())
    validated = ResponseOutputMessage.model_validate(result[0])
    assert "context_ingress_event" in validated.content[0].text
    assert message == original
    assert history.rows[0][1]["content"] == exact_json(original["content"])


@pytest.mark.asyncio
async def test_page_compaction_only_dispatches_a_fitting_closed_prefix():
    from src.lib.openai_agents.context_capacity import PendingPayload
    from src.lib.openai_agents.model_context_view import ContextRecoveryError
    request = FakeRequest(2000)
    original_compact = request.compact
    async def checked_compact(items):
        assert await request.count(items) <= request.capacity
        return await original_compact(items)
    request.compact = checked_compact
    policy = ContextCapacityPolicy(MemoryHistory())
    policy.request = request
    policy.pending = [PendingPayload("source", "exact body", {"role": "user"})]
    policy.last_input = [{"role": "user", "content": "old " * 100}]
    # A single oversized newly generated message cannot be sent to /compact.
    policy.last_output = [
        {"role": "assistant", "content": "new " * 1000},
        {"type": "function_call", "call_id": "read", "name": "read_pending_context", "arguments": "{}"}]
    with pytest.raises(ContextRecoveryError, match="No source content fits"):
        await policy.next_page("read")
    assert request.compactions == [policy.last_input]
