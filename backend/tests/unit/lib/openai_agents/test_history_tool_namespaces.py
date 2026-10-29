"""Historical forced/default calls remain valid when hosted discovery resumes."""

from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest
from agents import (
    FunctionTool,
    ModelResponse,
    ModelSettings,
    ToolSearchTool,
    Usage,
    tool_namespace,
)
from agents.models.openai_responses import OpenAIResponsesModel, OpenAIResponsesWSModel
from openai import AsyncOpenAI

from src.lib.openai_agents.context_capacity import (
    ContextCapacityPolicy,
    NativeCapacityRequest,
    item_keys,
)
from src.lib.openai_agents.model_request_measurement import MeasuredModel
from src.lib.openai_agents.tool_surface import align_history_tool_namespaces


def tool(name="refresh_workshop_prompt"):
    async def invoke(*args):
        raise AssertionError("History projection must not execute tools")

    return FunctionTool(
        name=name,
        description="Read the current draft",
        params_json_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        on_invoke_tool=invoke,
    )


def surface(namespace):
    function = tool()
    if namespace is None:
        return [function]
    function.defer_loading = True
    return [
        ToolSearchTool(execution="server"),
        *tool_namespace(name=namespace, description="Workshop tools", tools=[function]),
    ]


def history():
    return [
        {"role": "user", "content": "Inspect my draft"},
        {
            "type": "function_call",
            "call_id": "namespaced",
            "name": "refresh_workshop_prompt",
            "namespace": "workshop_authoring",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "namespaced",
            "output": "Original draft",
        },
        {
            "type": "function_call",
            "call_id": "forced",
            "name": "refresh_workshop_prompt",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "forced",
            "output": "Updated draft",
        },
        {"role": "user", "content": "What next?"},
    ]


def test_deferred_forced_deferred_checkpoint_projection_is_lossless():
    checkpoint = json.dumps(history())
    raw = json.loads(checkpoint)
    for namespace in ["workshop_authoring", None, "workshop_authoring"]:
        projected = align_history_tool_namespaces(raw, surface(namespace))
        calls = [i for i in projected if i.get("type") == "function_call"]
        assert [i.get("namespace") for i in calls] == [namespace, namespace]
        for original, current in zip(raw, projected):
            assert {k: v for k, v in original.items() if k != "namespace"} == {
                k: v for k, v in current.items() if k != "namespace"
            }
        assert json.dumps(raw) == checkpoint
        assert align_history_tool_namespaces(projected, surface(namespace)) == projected


def test_absent_renamed_or_ambiguous_tools_are_not_invented_or_guessed():
    raw = history() + [
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "tool_search_output", "id": "search", "tools": []},
    ]
    for tools in [
        [],
        [tool("renamed_refresh")],
        [tool(), *tool_namespace(name="other", description="Other", tools=[tool()])],
    ]:
        assert align_history_tool_namespaces(raw, tools) == raw
    qualified = [
        {
            "type": "function_call",
            "name": "unknown.refresh_workshop_prompt",
            "call_id": "unknown",
            "arguments": "{}",
        }
    ]
    assert (
        align_history_tool_namespaces(qualified, surface("workshop_authoring"))
        == qualified
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", [None, "workshop_authoring"])
async def test_sdk_count_and_compact_use_same_projection_without_changing_source(
    namespace,
):
    sent = []

    async def transport(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200, json={"object": "response.input_tokens", "input_tokens": 100}
        )

    client = AsyncOpenAI(
        api_key="fixture",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )

    async def compact(items, **kwargs):
        assert items == sent[-1]["input"]
        return SimpleNamespace(output=items)

    request = NativeCapacityRequest(
        OpenAIResponsesModel(model="gpt-6-astra", openai_client=client),
        ModelSettings(max_tokens=1000),
        instructions="Inspect the draft",
        tools=surface(namespace),
        output_schema=None,
        handoffs=[],
        compact_call=compact,
    )
    raw = history()
    before = deepcopy(raw)
    assert await request.count(raw) == 100
    assert sent[-1]["input"] == request.prepare_input(raw)
    assert await request.compact(raw) == request.prepare_input(raw)
    assert raw == before
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("model_class", [OpenAIResponsesModel, OpenAIResponsesWSModel])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("namespace", [None, "workshop_authoring"])
async def test_measured_generation_matches_real_sdk_count_after_checkpoint_restore(
    monkeypatch, model_class, stream, namespace
):
    counted = []
    generated = []

    async def transport(request):
        counted.append(json.loads(request.content))
        return httpx.Response(
            200, json={"object": "response.input_tokens", "input_tokens": 100}
        )

    client = AsyncOpenAI(
        api_key="fixture",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    inner = model_class(model="gpt-6-astra", openai_client=client)

    def record(instructions, input, settings, tools, schema, handoffs):
        wire = inner._build_response_create_kwargs(
            system_instructions=instructions,
            input=input,
            model_settings=settings,
            tools=tools,
            output_schema=schema,
            handoffs=handoffs,
        )
        generated.append(wire)

    async def response(
        instructions, input, settings, tools, schema, handoffs, tracing, **kwargs
    ):
        record(instructions, input, settings, tools, schema, handoffs)
        return ModelResponse(output=[], usage=Usage(), response_id="fixture")

    async def events(
        instructions, input, settings, tools, schema, handoffs, tracing, **kwargs
    ):
        record(instructions, input, settings, tools, schema, handoffs)
        if False:
            yield None

    monkeypatch.setattr(inner, "get_response", response)
    monkeypatch.setattr(inner, "stream_response", events)
    policy = ContextCapacityPolicy()
    saved = history()
    before = deepcopy(saved)

    async def restore(items):
        # The persisted projection contains the old mixed routing, not the
        # caller's incoming list. Final generation must align AFTER restore.
        policy.replacement_keys = item_keys(items)
        policy.replacement_items = deepcopy(saved)

    monkeypatch.setattr(policy, "restore", restore)
    model = MeasuredModel(inner, agent=None, context_policy=policy)
    args = (
        "Inspect the draft",
        [{"role": "user", "content": "Resume checkpoint"}],
        ModelSettings(max_tokens=1000),
        surface(namespace),
        None,
        [],
        None,
    )
    if stream:
        async for _ in model.stream_response(*args):
            pass
    else:
        await model.get_response(*args)
    assert len(counted) == len(generated) == 1
    assert (
        counted[0]["input"]
        == generated[0]["input"]
        == align_history_tool_namespaces(saved, args[3])
    )
    assert counted[0]["tools"] == generated[0]["tools"]
    assert (
        saved == before
        and policy.replacement_items == before
        and policy.last_input == before
    )
    await client.close()


@pytest.mark.asyncio
async def test_native_request_without_context_policy_still_projects_history():
    client = AsyncOpenAI(api_key="fixture")
    inner = OpenAIResponsesModel(model="gpt-6-astra", openai_client=client)
    model = MeasuredModel(inner, agent=None)
    raw = history()
    before = deepcopy(raw)
    tools = surface("workshop_authoring")
    projected, _ = await model._prepare_context(
        "Inspect", raw, ModelSettings(), tools, None, [], None, None, None
    )
    assert projected == align_history_tool_namespaces(raw, tools)
    assert raw == before
    await client.close()
