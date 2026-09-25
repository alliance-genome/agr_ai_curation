"""Contract tests for the shared hosted tool-search surface compiler (ALL-1280)."""

from __future__ import annotations

import copy
import importlib.util
import json
import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import Agent, FunctionTool, ToolSearchTool, WebSearchTool, function_tool

from src.lib.config.tool_loading_loader import (
    TOOL_LOADING_RUNTIMES,
    ToolLoadingConfigError,
    ToolLoadingPolicy,
    load_tool_loading_policies,
    load_tool_namespaces,
    parse_tool_loading_policy,
)
from src.lib.openai_agents import tool_surface
from src.lib.openai_agents.tool_surface import (
    MODE_DEFERRED,
    MODE_EAGER_POLICY,
    MODE_EAGER_PROVIDER_UNSUPPORTED,
    ToolSurface,
    ToolSurfaceConfigurationError,
    ToolSurfaceError,
    apply_tool_surface,
    canonical_tool_name,
    compile_tool_surface,
    validate_tool_surface_configuration,
)

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "tool_surface_golden_cases", _HERE / "tool_surface_golden_cases.py"
)
golden_cases = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(golden_cases)

DEFERRED = ToolLoadingPolicy(mode="deferred")
EAGER = ToolLoadingPolicy(mode="eager")
NAMESPACES = {
    "search_document": ("document_reading", "Search and read the paper."),
    "read_chunk": ("document_reading", "Search and read the paper."),
    "record_evidence": ("evidence_maintenance", "Record and maintain evidence."),
    "discard_recorded_evidence": ("evidence_maintenance", "Record and maintain evidence."),
    "finalize_gene_extraction": ("evidence_maintenance", "Record and maintain evidence."),
}


def _tool(name: str) -> FunctionTool:
    async def invoke(_ctx, _args):
        return name

    return FunctionTool(
        name=name,
        description=f"Run {name}",
        params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
        on_invoke_tool=invoke,
    )


def _payload(tools: list) -> str:
    return golden_cases.provider_payload(tools)


def _compile(tools, policy=DEFERRED, **kwargs):
    kwargs.setdefault("supports_tool_search", True)
    kwargs.setdefault("namespace_resolver", NAMESPACES.get)
    return compile_tool_surface(
        tools, runtime="extractor", agent_key="gene_extractor", policy=policy, **kwargs
    )


# ---------------------------------------------------------------------------
# Studio byte-identical golden
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", sorted(golden_cases.CASES))
def test_agent_studio_surface_is_byte_identical_to_pre_compiler_golden(case):
    from src.lib.agent_studio import openai_runtime as runtime

    golden = json.loads(
        (_HERE / "fixtures" / "agent_studio_tool_surface_golden.json").read_text()
    )[case]

    async def execute(*_args):
        raise AssertionError("no tool executes while building the surface")

    state = runtime.AgentStudioRunState(trace_id="golden")
    tools, counts = runtime.build_agent_studio_tools(
        golden_cases.studio_definitions(),
        executor=execute,
        state=state,
        namespace_for_tool=golden_cases.namespace_for_tool,
        **golden_cases.CASES[case],
    )

    assert json.loads(_payload(tools)) == golden["payload"]
    assert _payload(tools) == json.dumps(golden["payload"], sort_keys=True, default=str)
    assert counts == golden["counts"]
    assert state.tool_surface.mode == MODE_DEFERRED
    assert state.tool_surface.runtime == "agent_studio"


# ---------------------------------------------------------------------------
# Eager runtimes: zero diff
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "runtime",
    [
        runtime
        for runtime in TOOL_LOADING_RUNTIMES
        if runtime not in {"agent_studio", "extractor"}
    ],
)
def test_every_non_studio_runtime_policy_sends_an_unchanged_tool_payload(runtime):
    tools = [_tool("search_document"), _tool("record_evidence"), WebSearchTool(), _tool("finalize_x")]
    before = _payload(tools)
    agent = Agent(name="Runtime agent", instructions="x", tools=list(tools))
    agent.tool_surface_runtime = runtime

    surface = apply_tool_surface(agent)

    assert surface.mode == MODE_EAGER_POLICY
    assert [id(tool) for tool in agent.tools] == [id(tool) for tool in tools]
    assert _payload(agent.tools) == before
    assert not any(isinstance(tool, ToolSearchTool) for tool in agent.tools)
    assert agent.tool_surface is surface


def test_repository_policies_defer_only_agent_studio_and_extractors():
    policies = load_tool_loading_policies()

    assert set(policies) == set(TOOL_LOADING_RUNTIMES)
    assert policies["agent_studio"].mode == "deferred"
    assert policies["agent_studio"].eager_tools == (
        "search_studio_capabilities",
        "read_studio_guide",
    )
    assert policies["extractor"].mode == "deferred"
    assert set(policies["extractor"].deferred_namespaces) == {
        "staged_object_corrections",
        "evidence_maintenance",
        "ontology_term_lookup",
        "reference_data_lookup",
    }
    assert {runtime for runtime, policy in policies.items() if policy.mode == "eager"} == (
        set(TOOL_LOADING_RUNTIMES) - {"agent_studio", "extractor"}
    )


def test_studio_eager_policy_tool_is_a_registered_studio_capability():
    from src.api.agent_studio_opus_tools import SEARCH_STUDIO_CAPABILITIES_TOOL

    policy = load_tool_loading_policies()["agent_studio"]
    assert SEARCH_STUDIO_CAPABILITIES_TOOL["name"] in policy.eager_tools


# ---------------------------------------------------------------------------
# Deferred compiler invariants
# ---------------------------------------------------------------------------


def test_deferred_surface_partitions_without_changing_declared_tools():
    tools = [
        _tool("search_document"),
        _tool("get_agent_contract"),
        _tool("record_evidence"),
        _tool("read_chunk"),
        _tool("finalize_gene_extraction"),
        _tool("discard_recorded_evidence"),
    ]

    surface = _compile(tools, forced_tool_names=["record_evidence"])

    assert isinstance(surface.tools[0], ToolSearchTool)
    assert surface.tools[0].execution == "server"
    assert surface.mode == MODE_DEFERRED
    assert surface.eager_names == ("get_agent_contract", "record_evidence", "finalize_gene_extraction")
    assert surface.deferred_names == ("search_document", "read_chunk", "discard_recorded_evidence")
    assert surface.namespace_names == ("document_reading", "evidence_maintenance")
    assert sorted(surface.declared_names) == sorted(tool.name for tool in tools)
    deferred = [tool for tool in surface.tools[1:] if isinstance(tool, FunctionTool) and tool.defer_loading]
    assert [tool._tool_namespace for tool in deferred] == [
        "document_reading", "document_reading", "evidence_maintenance",
    ]
    # Inputs are never mutated; the compiler works on copies.
    assert not any(tool.defer_loading for tool in tools)


def test_terminal_and_required_tools_are_never_deferred():
    surface = _compile(
        [_tool("finalize_gene_extraction"), _tool("search_document")],
        required_tool_names=["search_document"],
    )

    assert surface.deferred_names == ()
    assert surface.mode == MODE_EAGER_POLICY
    assert not any(isinstance(tool, ToolSearchTool) for tool in surface.tools)


def test_deferred_namespaces_limit_what_is_deferred():
    surface = _compile(
        [_tool("search_document"), _tool("record_evidence")],
        policy=ToolLoadingPolicy(mode="deferred", deferred_namespaces=("evidence_maintenance",)),
    )

    assert surface.eager_names == ("search_document",)
    assert surface.deferred_names == ("record_evidence",)


def test_compilation_is_deterministic():
    tools = [_tool("read_chunk"), _tool("record_evidence"), _tool("search_document")]

    first = _compile(tools)
    second = _compile(tools)

    assert first.fingerprint == second.fingerprint
    assert _payload(first.tools) == _payload(second.tools)


def test_namespace_above_the_function_cap_fails(monkeypatch):
    monkeypatch.setenv("TOOL_SURFACE_NAMESPACE_MAX_FUNCTIONS", "1")

    with pytest.raises(tool_surface.ToolGroupCapError) as excinfo:
        _compile([_tool("search_document"), _tool("read_chunk")])

    # Curator-facing: only a saved custom agent can reach the run-time cap.
    assert str(excinfo.value) == (
        "This agent has 2 tools from the 'document reading' group, and custom agents "
        "can use at most 1 tools from one group. Please contact the AI Curation "
        "developers for help setting up this agent."
    )
    assert excinfo.value.oversized == {"document_reading": ["search_document", "read_chunk"]}
    assert excinfo.value.agent_key == "gene_extractor"


def test_namespace_with_two_descriptions_fails():
    with pytest.raises(ToolSurfaceError, match="more than one description"):
        _compile(
            [_tool("search_document"), _tool("read_chunk")],
            namespace_resolver=lambda name: ("document_reading", f"About {name}"),
        )


def test_duplicate_or_already_compiled_tools_fail():
    with pytest.raises(ToolSurfaceError, match="duplicate"):
        _compile([_tool("read_chunk"), _tool("read_chunk")])
    compiled = _compile([_tool("read_chunk")]).tools
    with pytest.raises(ToolSurfaceError, match="already compiled"):
        _compile(compiled)


def test_unsupported_provider_is_explicit_measured_eager_and_logged_once(caplog):
    tool_surface._unsupported_logged.clear()
    tools = [_tool("search_document")]

    with caplog.at_level(logging.WARNING, logger=tool_surface.__name__):
        first = _compile(tools, supports_tool_search=False, provider="openrouter", model="m")
        second = _compile(tools, supports_tool_search=False, provider="openrouter", model="m")

    assert first.mode == second.mode == MODE_EAGER_PROVIDER_UNSUPPORTED
    assert first.tools == tools
    assert len([r for r in caplog.records if MODE_EAGER_PROVIDER_UNSUPPORTED in r.getMessage()]) == 1


def test_unsupported_provider_fails_when_policy_says_fail():
    with pytest.raises(ToolSurfaceError, match="on_unsupported_provider: fail"):
        _compile(
            [_tool("search_document")],
            policy=ToolLoadingPolicy(mode="deferred", on_unsupported_provider="fail"),
            supports_tool_search=False,
        )


def test_deferral_survives_run_state_rebinding_when_compiled_last(monkeypatch):
    from src.lib.openai_agents import streaming_tools

    @function_tool(name_override="search_document")
    def search_document(query: str) -> str:
        return query

    agent = Agent(name="Extractor", instructions="x", tools=[search_document])
    monkeypatch.setattr(
        streaming_tools, "_run_state_tool_impls", lambda: {"search_document": "x:y"}
    )
    monkeypatch.setattr(streaming_tools, "_import_callable", lambda _path: lambda query: query)
    streaming_tools._bind_run_state_into_tools(
        agent,
        evidence_records=[],
        builder_workspace=SimpleNamespace(run_id="run-1"),
    )
    assert agent.tools[0] is not search_document  # rebuilt per run

    surface = _compile(agent.tools)

    assert surface.deferred_names == ("search_document",)
    assert surface.tools[-1].defer_loading is True


def test_apply_tool_surface_recompiles_an_already_applied_agent_from_source(monkeypatch):
    agent = Agent(name="Runtime agent", instructions="x", tools=[_tool("read_chunk")])
    agent.tool_surface_runtime = "extractor"
    monkeypatch.setattr(
        tool_surface, "resolve_tool_loading_policy", lambda *_args: DEFERRED
    )
    monkeypatch.setattr(tool_surface, "model_supports_tool_search", lambda *_args: True)
    monkeypatch.setattr(tool_surface, "declarative_namespace_resolver", lambda: NAMESPACES.get)

    first = apply_tool_surface(agent)
    second = apply_tool_surface(agent)

    assert first.fingerprint == second.fingerprint
    assert second.deferred_names == ("read_chunk",)


# ---------------------------------------------------------------------------
# Qualified wire names and run observations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("document_reading.read_chunk", "read_chunk"),
        ("read_chunk", "read_chunk"),
        (" evidence_maintenance.record_evidence ", "record_evidence"),
        (None, ""),
    ],
)
def test_canonical_tool_name(wire, expected):
    assert canonical_tool_name(wire) == expected


def test_qualified_names_normalize_in_tracker_and_supervisor_ledger():
    from src.lib.openai_agents.agents.supervisor_agent import SupervisorCallLedger
    from src.lib.openai_agents.guardrails import ToolCallTracker

    tracker = ToolCallTracker()
    tracker.record_call("document_reading.search_document")
    assert tracker.get_tool_names() == ["search_document"]

    ledger = SupervisorCallLedger(max_total_calls=10, max_calls_per_tool=10)
    handoff = SimpleNamespace(result_ref="ref-1")
    ledger.record_extraction_handoff("specialists.ask_gene_specialist", "q", handoff)
    ledger.record_extraction_handoff("ask_gene_specialist", "q", handoff)
    assert ledger.latest_extraction_handoffs() == [handoff]


def test_run_observations_track_loaded_called_and_searches():
    surface = _compile([_tool("search_document"), _tool("read_chunk"), _tool("record_evidence")])

    surface.observe_response_output(
        [
            {"type": "tool_search_call", "execution": "server"},
            {
                "type": "tool_search_output",
                "tools": [
                    {
                        "type": "namespace",
                        "name": "document_reading",
                        "tools": [{"name": "search_document"}, {"name": "read_chunk"}],
                    }
                ],
            },
            {"type": "function_call", "name": "document_reading.search_document"},
        ]
    )
    summary = surface.summary()

    assert summary["searches"] == 1
    assert summary["loaded_names"] == ["read_chunk", "search_document"]
    assert summary["called_names"] == ["search_document"]
    assert summary["loaded_not_called"] == ["read_chunk"]
    assert summary["mode"] == MODE_DEFERRED
    assert summary["deferred_count"] == 3
    assert json.dumps(summary)


def test_request_measurement_counts_namespace_headers_and_loaded_names():
    from src.lib.openai_agents.model_request_measurement import build_measurement

    surface = _compile([_tool("search_document"), _tool("read_chunk"), _tool("record_evidence")])
    measurement = build_measurement(
        runtime="agents_sdk",
        provider="openai",
        api="responses",
        transport="http",
        model="gpt-6-sol",
        instructions="x",
        input_value=[
            {"type": "tool_search_call", "call_id": "s1", "execution": "server"},
            {
                "type": "tool_search_output",
                "tools": [{"type": "namespace", "name": "document_reading", "tools": [{"name": "read_chunk"}]}],
            },
            {"type": "function_call", "call_id": "c1", "name": "document_reading.read_chunk"},
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        ],
        tools=surface.tools,
    )
    tools = measurement["outbound"]["tools"]
    input_measure = measurement["outbound"]["input"]

    assert tools["namespace_headers"]["count"] == 2
    assert tools["namespace_headers"]["chars"] > 0
    assert tools["deferred"]["count"] == 3
    assert input_measure["loaded_deferred_tool_definitions"]["names"] == ["read_chunk"]
    assert input_measure["tool_calls"]["tool_search_calls"] == 1
    assert input_measure["tool_results"]["largest"]["tool_name"] == "read_chunk"


def test_prompt_cache_key_binds_to_the_compiled_visible_and_deferred_surface():
    """ALL-1284: the key's tool-surface digest reflects deferral, not just names."""

    from agents import ModelSettings

    from src.lib.openai_agents.config import (
        PROMPT_CACHE_KEY_FIELD,
        PromptCacheIdentity,
        build_prompt_cache_key,
    )
    from src.lib.openai_agents.model_request_measurement import (
        bind_prompt_cache_tool_surface,
    )

    key = build_prompt_cache_key(
        PromptCacheIdentity(agent_key="gene_extractor", static_prompt="Extract."),
        model="gpt-6-sol",
    )
    settings = ModelSettings(extra_args={PROMPT_CACHE_KEY_FIELD: key})
    tools = [_tool("search_document"), _tool("read_chunk"), _tool("record_evidence")]
    all_deferred = _compile(tools)
    reading_eager = _compile(
        tools,
        policy=ToolLoadingPolicy(mode="deferred", deferred_namespaces=("evidence_maintenance",)),
    )
    eager = _compile(tools, policy=EAGER)

    digests = [
        bind_prompt_cache_tool_surface(settings, surface.tools, [])[1]
        for surface in (all_deferred, reading_eager, eager)
    ]

    assert len(set(digests)) == 3
    assert bind_prompt_cache_tool_surface(settings, _compile(tools).tools, [])[1] == digests[0]


# ---------------------------------------------------------------------------
# Deferred runs: bare calls, tool-less replay, prompt audit, trace tags
# ---------------------------------------------------------------------------


def _evidence_deferred_surface() -> ToolSurface:
    return _compile(
        [_tool("search_document"), _tool("read_chunk"), _tool("record_evidence")],
        policy=ToolLoadingPolicy(mode="deferred", deferred_namespaces=("evidence_maintenance",)),
    )


def test_bare_call_to_a_deferred_tool_returns_a_load_hint_instead_of_ending_the_run():
    import asyncio

    from agents import Model, ModelResponse, RunConfig, Runner, Usage
    from agents.exceptions import ModelBehaviorError
    from openai.types.responses import (
        ResponseFunctionToolCall,
        ResponseOutputMessage,
        ResponseOutputText,
    )

    class BareCallModel(Model):
        def __init__(self):
            self.inputs = []

        async def get_response(self, system_instructions, input, *args, **kwargs):
            self.inputs.append(list(input))
            if len(self.inputs) == 1:
                # The model calls a deferred tool by its bare name before loading it.
                output = [ResponseFunctionToolCall(
                    id="fc_1", call_id="call_1", name="record_evidence",
                    arguments="{}", type="function_call",
                )]
            else:
                output = [ResponseOutputMessage(
                    id="msg_1", type="message", role="assistant", status="completed",
                    content=[ResponseOutputText(type="output_text", text="done", annotations=[])],
                )]
            return ModelResponse(output=output, usage=Usage(), response_id=f"r{len(self.inputs)}")

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("non-streaming run")
            yield

    surface = _evidence_deferred_surface()
    assert surface.deferred_names == ("record_evidence",)
    base_config = RunConfig(tracing_disabled=True)

    model = BareCallModel()
    agent = Agent(name="Extractor", instructions="x", model=model, tools=surface.tools)
    result = asyncio.run(
        Runner.run(agent, "go", run_config=tool_surface.run_config_for_tool_surface(base_config, surface))
    )

    assert result.final_output == "done"
    [error_output] = [
        item for item in model.inputs[1] if item.get("type") == "function_call_output"
    ]
    assert error_output["call_id"] == "call_1"
    assert error_output["output"] == (
        "Tool 'record_evidence' is not loaded yet. It is in the 'evidence_maintenance' "
        "tool group: load the 'evidence_maintenance' group with tool search, then call "
        "'record_evidence' again."
    )
    # Without the deferred-run config the SDK ends the run on the same call.
    with pytest.raises(ModelBehaviorError, match="record_evidence not found"):
        asyncio.run(Runner.run(
            Agent(name="Extractor", instructions="x", model=BareCallModel(), tools=surface.tools),
            "go",
            run_config=base_config,
        ))


def test_eager_surface_keeps_its_run_config_and_formatter_conflicts_fail():
    from agents import RunConfig

    config = RunConfig(tracing_disabled=True)
    eager = _compile([_tool("search_document")], policy=EAGER)

    assert tool_surface.run_config_for_tool_surface(config, eager) is config
    with pytest.raises(ToolSurfaceError, match="tool_error_formatter"):
        tool_surface.run_config_for_tool_surface(
            RunConfig(tool_error_formatter=lambda _args: None), _evidence_deferred_surface()
        )


def test_replay_without_tool_search_keeps_every_call_and_result():
    history = [
        {"role": "user", "content": "extract"},
        # Reasoning tied to the search (the next non-reasoning item): removed.
        {"type": "reasoning", "id": "rs_search_a", "summary": []},
        {"type": "reasoning", "id": "rs_search_b", "summary": []},
        {"type": "tool_search_call", "id": "ts_1", "call_id": None, "execution": "server",
         "arguments": {"paths": ["evidence_maintenance"]}, "status": "completed"},
        {"type": "tool_search_output", "id": "tso_1", "call_id": None, "execution": "server",
         "status": "completed", "tools": [{"type": "namespace", "name": "evidence_maintenance"}]},
        # Reasoning tied to a kept function call: kept.
        {"type": "reasoning", "id": "rs_call", "summary": []},
        {"type": "function_call", "call_id": "c1", "name": "record_evidence",
         "namespace": "evidence_maintenance", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "evidence e1"},
        {"type": "function_call", "call_id": "c2", "name": "read_chunk", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c2", "output": "chunk text"},
    ]

    replay, changes = tool_surface.replay_input_without_tool_search(history)

    assert changes == {
        "tool_search_items_removed": 2,
        "reasoning_items_removed": 2,
        "function_call_namespaces_removed": 1,
    }
    assert replay == [
        history[0],
        history[5],
        {"type": "function_call", "call_id": "c1", "name": "record_evidence", "arguments": "{}"},
        history[7],
        history[8],
        history[9],
    ]
    assert history[6]["namespace"] == "evidence_maintenance"


def test_replay_of_an_eager_run_is_unchanged():
    history = [
        {"role": "user", "content": "extract"},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "call_id": "c1", "name": "read_chunk", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "chunk text"},
    ]

    replay, changes = tool_surface.replay_input_without_tool_search(history)

    assert replay == history
    assert changes == {
        "tool_search_items_removed": 0,
        "reasoning_items_removed": 0,
        "function_call_namespaces_removed": 0,
    }


def test_recorded_prompt_matches_the_instructions_the_deferred_run_sends():
    from src.lib.prompts import context as prompt_context

    prompt_context.clear_prompt_context()
    agent = Agent(name="Gene Extractor", instructions="Extract genes.", tools=[
        _tool("search_document"), _tool("read_chunk"), _tool("record_evidence"),
    ])
    agent.tool_surface_runtime = "extractor"
    run_id = prompt_context.set_pending_prompts(
        "Gene Extractor",
        [],
        effective_prompt_hash="h0",
        layer_manifest={"agent_id": "gene_extractor", "layers": [], "hash": "h0"},
    )
    prompt_context.bind_prompt_run(agent, run_id)
    policy = ToolLoadingPolicy(mode="deferred", deferred_namespaces=("evidence_maintenance",))

    runtime_agent = copy.copy(agent)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tool_surface, "resolve_tool_loading_policy", lambda *_args: policy)
        patch.setattr(tool_surface, "model_supports_tool_search", lambda *_args: True)
        patch.setattr(tool_surface, "declarative_namespace_resolver", lambda: NAMESPACES.get)
        surface = apply_tool_surface(runtime_agent)
    tool_surface.record_tool_surface_prompt(agent, surface, target_agent=runtime_agent)
    prompt_context.commit_pending_prompts(runtime_agent)

    [prompt_run] = prompt_context.get_used_prompt_runs()
    layers = prompt_run.assembly.layer_manifest["layers"]
    assert surface.mode == MODE_DEFERRED
    assert layers[-1]["id"] == "gene_extractor:runtime_context:tool_surface_on_demand"
    assert layers[-1]["content"] == tool_surface.deferred_tools_note(surface)
    assert runtime_agent.instructions == (
        f"Extract genes.\n\n{tool_surface.deferred_tools_note(surface)}"
    )
    assert prompt_run.assembly.effective_prompt_hash != "h0"
    prompt_context.clear_prompt_context()


def test_eager_surface_adds_no_prompt_layer():
    from src.lib.prompts import context as prompt_context

    prompt_context.clear_prompt_context()
    agent = Agent(name="Gene Validator", instructions="Validate.", tools=[_tool("search_document")])
    run_id = prompt_context.set_pending_prompts(
        "Gene Validator", [], effective_prompt_hash="h0",
        layer_manifest={"agent_id": "gene_validation", "layers": [], "hash": "h0"},
    )
    prompt_context.bind_prompt_run(agent, run_id)

    tool_surface.record_tool_surface_prompt(
        agent, _compile(agent.tools, policy=EAGER), target_agent=agent
    )
    prompt_context.commit_pending_prompts(agent)

    [prompt_run] = prompt_context.get_used_prompt_runs()
    assert prompt_run.assembly.layer_manifest["layers"] == []
    prompt_context.clear_prompt_context()


def test_trace_attributes_tag_the_run_with_its_tool_surface_mode(monkeypatch):
    import langfuse

    from src.lib.openai_agents import langfuse_client

    captured = []

    @contextmanager
    def fake_propagate_attributes(**kwargs):
        captured.append(kwargs)
        yield

    surface = _evidence_deferred_surface()
    monkeypatch.setattr(langfuse, "propagate_attributes", fake_propagate_attributes)
    monkeypatch.setattr(langfuse_client, "is_openai_agents_tracing_enabled", lambda: True)
    monkeypatch.setattr(langfuse_client, "get_langfuse", lambda: None)
    with tool_surface.tool_surface_trace_attributes(surface):
        pass
    assert captured == []

    monkeypatch.setattr(langfuse_client, "get_langfuse", lambda: object())
    with tool_surface.tool_surface_trace_attributes(surface):
        pass
    assert captured == [{
        "tags": ["tool_surface:deferred"],
        "metadata": {
            "tool_surface_mode": "deferred",
            "tool_surface_runtime": "extractor",
            "tool_surface_fingerprint": surface.fingerprint,
        },
    }]


# ---------------------------------------------------------------------------
# Declarative configuration and startup validation
# ---------------------------------------------------------------------------


def test_repository_namespaces_and_bindings_validate():
    namespaces = load_tool_namespaces()
    report = validate_tool_surface_configuration()

    assert set(namespaces) >= {
        "document_reading",
        "evidence_maintenance",
        "staged_object_corrections",
        "ontology_term_lookup",
        "reference_data_lookup",
    }
    assert all(len(namespace.description) <= 160 for namespace in namespaces.values())
    assert report["namespaced_tool_count"] > 0
    assert report["policies"]["agent_studio"] == "deferred"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"mode": "lazy"}, "mode"),
        ({"mode": "eager", "eager_tools": ["read_chunk"]}, "mode is 'eager'"),
        ({"mode": "deferred", "surprise": True}, "unknown fields"),
        ({"mode": "deferred", "on_unsupported_provider": "maybe"}, "on_unsupported_provider"),
        ({"mode": "deferred", "eager_tools": ["a", "a"]}, "duplicates"),
    ],
)
def test_policy_parsing_is_strict(raw, message):
    with pytest.raises(ToolLoadingConfigError, match=message):
        parse_tool_loading_policy(raw, label="test policy")


def _fake_registry(bindings):
    return SimpleNamespace(
        bindings=tuple(
            SimpleNamespace(tool_id=tool_id, metadata=metadata)
            for tool_id, metadata in bindings.items()
        ),
        bindings_by_tool_id={tool_id: object() for tool_id in bindings},
    )


def _patch_validation_sources(monkeypatch, *, bindings, agents=None, policies=None):
    from src.lib.config import agent_loader, tool_loading_loader, tool_policy_defaults_loader
    from src.lib.packages import tool_registry

    monkeypatch.setattr(tool_registry, "load_tool_registry", lambda: _fake_registry(bindings))
    monkeypatch.setattr(tool_policy_defaults_loader, "load_tool_policy_defaults", lambda: {})
    monkeypatch.setattr(agent_loader, "load_agent_definitions", lambda: agents or {})
    monkeypatch.setattr(
        tool_loading_loader,
        "get_tool_loading_policies",
        lambda: policies or {runtime: EAGER for runtime in TOOL_LOADING_RUNTIMES},
    )


def test_startup_rejects_unknown_namespace(monkeypatch):
    _patch_validation_sources(monkeypatch, bindings={"read_chunk": {"namespace": "nowhere"}})

    with pytest.raises(ToolSurfaceConfigurationError, match="unknown namespace 'nowhere'"):
        validate_tool_surface_configuration()


def test_startup_rejects_deferrable_terminal_tool(monkeypatch):
    _patch_validation_sources(
        monkeypatch,
        bindings={
            "finalize_gene_extraction": {"namespace": "evidence_maintenance"},
            "stage_x": {"namespace": "evidence_maintenance", "builder_finalization": True},
        },
    )

    with pytest.raises(ToolSurfaceConfigurationError) as excinfo:
        validate_tool_surface_configuration()
    assert "finalize_gene_extraction" in str(excinfo.value)
    assert "stage_x" in str(excinfo.value)


def test_startup_rejects_oversized_agent_namespace(monkeypatch):
    monkeypatch.setenv("TOOL_SURFACE_NAMESPACE_MAX_FUNCTIONS", "1")
    _patch_validation_sources(
        monkeypatch,
        bindings={
            "search_document": {"namespace": "document_reading"},
            "read_chunk": {"namespace": "document_reading"},
        },
        agents={
            "reader": SimpleNamespace(tools=["search_document", "read_chunk"], tool_loading=None)
        },
    )

    with pytest.raises(ToolSurfaceConfigurationError, match="Agent 'reader' places 2 tools"):
        validate_tool_surface_configuration()


def test_startup_rejects_unknown_eager_tool_and_namespace(monkeypatch):
    policies = {runtime: EAGER for runtime in TOOL_LOADING_RUNTIMES}
    policies["validator"] = ToolLoadingPolicy(
        mode="deferred",
        deferred_namespaces=("ghost_namespace",),
        eager_tools=("ghost_tool",),
        source_label="validator policy",
    )
    _patch_validation_sources(
        monkeypatch,
        bindings={"read_chunk": {"namespace": "document_reading"}},
        agents={
            "reader": SimpleNamespace(
                tools=["read_chunk"],
                category="Extraction",
                tool_loading=ToolLoadingPolicy(mode="deferred", eager_tools=("missing_tool",)),
            )
        },
        policies=policies,
    )

    with pytest.raises(ToolSurfaceConfigurationError) as excinfo:
        validate_tool_surface_configuration()
    message = str(excinfo.value)
    assert "ghost_namespace" in message
    assert "ghost_tool" in message
    assert "missing_tool" in message


def test_startup_rejects_deferral_for_validators_and_supervisors(monkeypatch):
    policies = {runtime: EAGER for runtime in TOOL_LOADING_RUNTIMES}
    for runtime in ("validator", "chat_supervisor", "flow_supervisor"):
        policies[runtime] = ToolLoadingPolicy(mode="deferred", source_label=f"{runtime} policy")
    _patch_validation_sources(
        monkeypatch,
        bindings={"read_chunk": {"namespace": "document_reading"}},
        agents={
            f"{category.lower()}_agent": SimpleNamespace(
                tools=["read_chunk"],
                category=category,
                tool_loading=ToolLoadingPolicy(mode="deferred"),
            )
            for category in ("Validation", "Routing", "Extraction")
        },
        policies=policies,
    )

    with pytest.raises(ToolSurfaceConfigurationError) as excinfo:
        validate_tool_surface_configuration()
    message = str(excinfo.value)
    for runtime in ("validator", "chat_supervisor", "flow_supervisor"):
        assert f"defers tools for runtime '{runtime}'" in message
    assert "Agent 'validation_agent' (Validation) tool_loading defers tools" in message
    assert "Agent 'routing_agent' (Routing) tool_loading defers tools" in message
    assert "extraction_agent" not in message


def test_agent_yaml_tool_loading_override_is_parsed():
    from src.lib.config.agent_loader import AgentDefinition

    definition = AgentDefinition.from_yaml(
        "reader",
        {
            "agent_id": "reader",
            "tools": ["read_chunk"],
            "tool_loading": {"mode": "deferred", "deferred_namespaces": ["document_reading"]},
        },
    )

    assert definition.tool_loading.mode == "deferred"
    assert definition.tool_loading.deferred_namespaces == ("document_reading",)


# ---------------------------------------------------------------------------
# Provider/model capability flags
# ---------------------------------------------------------------------------


def test_openai_catalog_models_declare_tool_search_and_others_do_not():
    from src.lib.config.models_loader import list_models, load_models
    from src.lib.config.providers_loader import get_provider, load_providers

    load_providers(force_reload=True)
    load_models(force_reload=True)
    assert get_provider("openai").supports_tool_search is True
    assert get_provider("openrouter").supports_tool_search is False
    for model in list_models():
        assert model.supports_tool_search is (model.provider == "openai")
        assert tool_surface.model_supports_tool_search(model.model_id, model.provider) is (
            model.provider == "openai"
        )


def test_tool_search_provider_must_use_responses_api():
    from src.lib.config.providers_loader import ProviderDefinition

    with pytest.raises(ValueError, match="requires the Responses API"):
        ProviderDefinition.from_yaml(
            "compat",
            {
                "driver": "openai_compatible",
                "api_key_env": "X",
                "default_base_url": "https://example.invalid",
                "api_mode": "chat_completions",
                "supports": {"tool_search": True},
            },
            source_label="test",
        )


def test_provider_validation_rejects_model_tool_search_on_unsupported_provider(monkeypatch):
    from src.lib.config import provider_validation
    from src.lib.config.models_loader import ModelDefinition
    from src.lib.config.providers_loader import ProviderDefinition

    provider = ProviderDefinition(
        provider_id="compat",
        driver="openai_compatible",
        api_key_env="COMPAT_KEY",
        default_base_url="https://example.invalid",
        api_mode="chat_completions",
        default_for_runner=True,
    )
    model = ModelDefinition(model_id="m", name="M", provider="compat", supports_tool_search=True)
    monkeypatch.setattr(provider_validation, "load_providers", lambda: None)
    monkeypatch.setattr(provider_validation, "list_providers", lambda: [provider])
    monkeypatch.setattr(provider_validation, "load_models", lambda: None)
    monkeypatch.setattr(provider_validation, "list_models", lambda: [model])

    report = provider_validation.build_provider_runtime_report(strict_mode=False)

    assert any("supports_tool_search" in error for error in report["errors"])


def test_surface_summary_is_attached_to_measured_requests():
    from src.lib.openai_agents.model_request_measurement import MeasuredModel

    surface = _compile([_tool("read_chunk")], policy=EAGER)
    measurement: dict = {}
    measured = MeasuredModel(SimpleNamespace(), agent=SimpleNamespace(tool_surface=surface))

    measured._observe_tool_surface(
        measurement,
        SimpleNamespace(output=[{"type": "function_call", "name": "read_chunk"}]),
    )

    assert measurement["tool_surface"]["mode"] == MODE_EAGER_POLICY
    assert measurement["tool_surface"]["called_names"] == ["read_chunk"]


# ---------------------------------------------------------------------------
# Phase 2: extractor deferral
# ---------------------------------------------------------------------------


def _packaged_extractors():
    from src.lib.config.agent_loader import load_agent_definitions

    return sorted(
        (
            (agent_id, definition)
            for agent_id, definition in load_agent_definitions().items()
            if definition.category == "Extraction"
        ),
        key=lambda item: item[0],
    )


def _extractor_agent(definition, *, model="gpt-6-sol"):
    agent = Agent(
        name=definition.name,
        instructions="Extract curatable objects.",
        model=model,
        tools=[_tool(name) for name in definition.tools],
    )
    agent.agent_key = definition.agent_id
    agent.cost_identity = {"agent_id": definition.agent_id, "agent_role": "extraction"}
    return agent


def test_packaged_extractors_are_covered():
    assert {agent_id for agent_id, _ in _packaged_extractors()} >= {
        "gene_extractor",
        "allele_extractor",
        "disease_extractor",
        "phenotype_extractor",
        "pdf_extraction",
        "gene_expression_extraction",
        "rgd_go_paper_curator",
    }


@pytest.mark.parametrize(
    "agent_id",
    [agent_id for agent_id, _ in _packaged_extractors()],
)
def test_packaged_extractor_surface_keeps_everyday_tools_visible(agent_id):
    from src.lib.config.agent_loader import load_agent_definitions

    definition = load_agent_definitions()[agent_id]
    agent = _extractor_agent(definition)

    surface = apply_tool_surface(agent)

    visible_functions = [
        tool for tool in agent.tools if isinstance(tool, FunctionTool) and not tool.defer_loading
    ]
    assert surface.runtime == "extractor"
    assert surface.mode == MODE_DEFERRED
    assert isinstance(agent.tools[0], ToolSearchTool)
    assert len(visible_functions) < 20
    assert sorted(surface.declared_names) == sorted(definition.tools)
    visible = {tool.name for tool in visible_functions}
    for name in definition.tools:
        if (
            name.startswith(("stage_", "list_staged_", "finalize_"))
            or name in {
                "search_document", "read_chunk", "read_section", "read_subsection",
                "record_evidence", "list_recorded_evidence", "get_agent_contract",
                "search_domain_field_terms", "resolve_domain_field_term", "quickgo_api_call",
            }
        ):
            assert name in visible, name
    deferred = set(surface.deferred_names)
    for name in definition.tools:
        if name.startswith(("patch_", "find_staged_")) or name in {
            "get_recorded_evidence", "attach_evidence_to_object", "detach_evidence_from_object",
            "discard_recorded_evidence", "update_recorded_evidence_metadata",
            "inspect_ontology_term", "agr_species_context_lookup", "agr_literature_reference_lookup",
        }:
            assert name in deferred, name
    assert set(surface.namespace_names) <= {
        "staged_object_corrections", "evidence_maintenance",
        "ontology_term_lookup", "reference_data_lookup",
    }
    note = agent.instructions.split("## Tools loaded on demand", 1)[1]
    for namespace in surface.namespace_names:
        assert f"- {namespace}:" in note


def test_gene_expression_extractor_visible_surface_golden():
    from src.lib.config.agent_loader import load_agent_definitions

    agent = _extractor_agent(load_agent_definitions()["gene_expression_extraction"])

    surface = apply_tool_surface(agent)

    assert surface.eager_names == (
        "search_document", "read_chunk", "read_section", "read_subsection",
        "record_evidence", "list_recorded_evidence", "get_agent_contract",
        "stage_gene_expression_observation", "list_staged_gene_expression_observations",
        "finalize_gene_expression_extraction",
    )
    assert surface.namespace_names == (
        "evidence_maintenance", "reference_data_lookup", "staged_object_corrections",
    )
    assert surface.deferred_names == (
        "get_recorded_evidence", "attach_evidence_to_object", "detach_evidence_from_object",
        "discard_recorded_evidence", "update_recorded_evidence_metadata",
        "agr_species_context_lookup",
        "patch_gene_expression_observation", "discard_gene_expression_observation",
        "find_staged_gene_expression_observations",
    )


def test_extractor_on_provider_without_tool_search_runs_eagerly_and_unchanged():
    from src.lib.config.agent_loader import load_agent_definitions

    tool_surface._unsupported_logged.clear()
    agent = _extractor_agent(
        load_agent_definitions()["gene_extractor"],
        model="deepseek/deepseek-v4-pro-0813",
    )
    tools = list(agent.tools)
    before = _payload(tools)

    surface = apply_tool_surface(agent)

    assert surface.mode == MODE_EAGER_PROVIDER_UNSUPPORTED
    assert _payload(agent.tools) == before
    assert agent.instructions == "Extract curatable objects."


def test_reapplying_an_extractor_surface_does_not_repeat_the_note():
    from src.lib.config.agent_loader import load_agent_definitions

    agent = _extractor_agent(load_agent_definitions()["gene_extractor"])

    first = apply_tool_surface(agent)
    instructions = agent.instructions
    second = apply_tool_surface(agent)

    assert agent.instructions == instructions
    assert instructions.count("## Tools loaded on demand") == 1
    assert first.fingerprint == second.fingerprint


def test_custom_extractor_uses_the_extractor_policy():
    agent = Agent(
        name="Custom extractor",
        instructions="Extract.",
        model="gpt-6-sol",
        tools=[_tool("read_chunk"), _tool("patch_gene_mention_evidence"), _tool("finalize_gene_extraction")],
    )
    agent.agent_key = "ca_custom_extractor"
    agent.cost_identity = {"agent_id": "ca_custom_extractor", "agent_role": "extraction"}

    surface = apply_tool_surface(agent)

    assert surface.runtime == "extractor"
    assert surface.deferred_names == ("patch_gene_mention_evidence",)
    assert surface.eager_names == ("read_chunk", "finalize_gene_extraction")


def test_named_tool_choice_stays_eager():
    from agents import ModelSettings

    agent = Agent(
        name="Custom extractor",
        instructions="Extract.",
        model="gpt-6-sol",
        model_settings=ModelSettings(tool_choice="patch_gene_mention_evidence"),
        tools=[_tool("read_chunk"), _tool("patch_gene_mention_evidence"), _tool("find_staged_gene_mention_evidence")],
    )
    agent.cost_identity = {"agent_id": "ca_named", "agent_role": "extraction"}

    surface = apply_tool_surface(agent)

    assert "patch_gene_mention_evidence" in surface.eager_names
    assert surface.deferred_names == ("find_staged_gene_mention_evidence",)
