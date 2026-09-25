"""ALL-1284: one stable prompt cache key per agent and static prompt.

Production (2026-09-16..22) sent no application prompt_cache_key, so the Agents
SDK keyed each request by chat session or by a fresh per-run uuid: 256 Allele
Validation runs used 256 keys and 81 identical Figure Locator calls never hit
the cache. Every native OpenAI runtime now sends
``<agent key>:p<digest of agent key, model and static prompt layers>t<digest of
the tool surface the request sends>``; the tool-surface binding happens per
request in ``MeasuredModel`` (see test_model_request_measurement).
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from agents import ModelSettings

from src.lib.openai_agents import config as agent_config
from src.lib.openai_agents.config import (
    PROMPT_CACHE_KEY_MAX_CHARS,
    PromptCacheIdentity,
    build_model_settings,
    build_prompt_cache_key,
    prompt_cache_extra_args,
    tool_surface_digest,
)
from src.lib.prompts import assembly

OPENAI_MODEL = "gpt-6-sol"
COMPATIBLE_MODEL = "deepseek/deepseek-v4-pro-0813"  # openrouter, openai_compatible


def _key(settings) -> str:
    return settings.extra_args["prompt_cache_key"]


def _bundle(agent_id: str, *, base: str, runtime: str | None = None, group: str | None = None):
    layers = [
        assembly._make_layer(
            layer_id=f"{agent_id}:core_static",
            kind="core_static",
            title="Platform runtime contract",
            content=assembly.CORE_STATIC_PROMPT,
            provenance="backend_static",
            editable=False,
            locked=True,
            source_ref="src.lib.prompts.assembly:CORE_STATIC_PROMPT",
        ),
        assembly._make_layer(
            layer_id=f"{agent_id}:base_prompt",
            kind="base_prompt",
            title="Editable base prompt",
            content=base,
            provenance="prompt_template:system",
            editable=True,
            locked=False,
            source_ref=f"prompt_templates:{agent_id}:v1",
        ),
    ]
    if group:
        layers.append(
            assembly._make_layer(
                layer_id=f"{agent_id}:group_rules:GROUP_A",
                kind="group_rules",
                title="Group rules",
                content=group,
                provenance="prompt_template:group_rules",
                editable=True,
                locked=False,
                source_ref=f"prompt_templates:{agent_id}-group-a:v1",
            )
        )
    if runtime:
        layers.append(
            assembly._make_layer(
                layer_id=f"{agent_id}:runtime_context",
                kind="runtime_context",
                title="Runtime context",
                content=runtime,
                provenance="runtime_context",
                editable=False,
                locked=True,
                source_ref="request:runtime_context",
            )
        )
    return assembly._bundle(agent_id, layers)


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def test_same_agent_model_and_static_prompt_yield_one_key():
    identity = PromptCacheIdentity(agent_key="allele_validation", static_prompt="Validate alleles.")

    first = build_prompt_cache_key(identity, model=OPENAI_MODEL)
    second = build_prompt_cache_key(
        PromptCacheIdentity(agent_key="allele_validation", static_prompt="Validate alleles."),
        model=OPENAI_MODEL,
    )

    assert first == second
    assert first.startswith("allele_validation:")
    assert not first.startswith(agent_config.SDK_GENERATED_PROMPT_CACHE_KEY_PREFIX)


@pytest.mark.parametrize(
    "agent_key,static_prompt,model",
    [
        ("gene_validation", "Validate alleles.", OPENAI_MODEL),
        ("allele_validation", "Validate alleles. v2", OPENAI_MODEL),
        ("allele_validation", "Validate alleles.", "gpt-6-astra"),
    ],
)
def test_agent_prompt_version_or_model_change_changes_key(agent_key, static_prompt, model):
    baseline = build_prompt_cache_key(
        PromptCacheIdentity(agent_key="allele_validation", static_prompt="Validate alleles."),
        model=OPENAI_MODEL,
    )

    assert build_prompt_cache_key(
        PromptCacheIdentity(agent_key=agent_key, static_prompt=static_prompt), model=model
    ) != baseline


def test_long_agent_keys_stay_bounded_and_distinct():
    shared_prefix = "ca_" + "x" * 80
    first = build_prompt_cache_key(
        PromptCacheIdentity(agent_key=shared_prefix + "_one", static_prompt="p"), model=OPENAI_MODEL
    )
    second = build_prompt_cache_key(
        PromptCacheIdentity(agent_key=shared_prefix + "_two", static_prompt="p"), model=OPENAI_MODEL
    )

    bound = [
        agent_config.bind_prompt_cache_key_to_tool_surface(key, tool_surface_digest([]))
        for key in (first, second)
    ]

    assert first != second
    assert len(bound[0]) == len(bound[1]) == PROMPT_CACHE_KEY_MAX_CHARS
    assert all(agent_config.is_application_prompt_cache_key(key) for key in [first, second, *bound])


def test_tool_surface_binding_is_order_independent_and_rejects_foreign_keys():
    key = build_prompt_cache_key(
        PromptCacheIdentity(agent_key="allele_validation", static_prompt="Validate."),
        model=OPENAI_MODEL,
    )
    search = {"type": "function", "name": "search", "parameters": {"type": "object"}}
    lookup = {"type": "function", "name": "lookup", "parameters": {"type": "object"}}

    assert tool_surface_digest([search, lookup]) == tool_surface_digest([lookup, search])
    assert tool_surface_digest([search]) != tool_surface_digest([search, lookup])
    bound = agent_config.bind_prompt_cache_key_to_tool_surface(key, tool_surface_digest([search]))
    rebound = agent_config.bind_prompt_cache_key_to_tool_surface(
        bound, tool_surface_digest([search, lookup])
    )
    assert bound.startswith(key) and rebound.startswith(key) and bound != rebound
    for foreign in ("agents-sdk:group:abc", "allele_validation:abc"):
        assert not agent_config.is_application_prompt_cache_key(foreign)
        with pytest.raises(ValueError):
            agent_config.bind_prompt_cache_key_to_tool_surface(foreign, "0" * 8)


@pytest.mark.parametrize("agent_key,static_prompt", [("", "p"), ("agent", " ")])
def test_identity_requires_agent_and_static_prompt(agent_key, static_prompt):
    with pytest.raises(ValueError):
        PromptCacheIdentity(agent_key=agent_key, static_prompt=static_prompt)


# ---------------------------------------------------------------------------
# Shared builder: native OpenAI only
# ---------------------------------------------------------------------------


def test_build_model_settings_sets_stable_key_for_native_openai():
    identity = PromptCacheIdentity(agent_key="gene_expression", static_prompt="Curate.")

    settings = build_model_settings(model=OPENAI_MODEL, prompt_cache=identity)

    assert settings.extra_args == {
        "prompt_cache_key": build_prompt_cache_key(identity, model=OPENAI_MODEL)
    }


def test_build_model_settings_leaves_compatible_providers_untouched():
    identity = PromptCacheIdentity(agent_key="gene_expression", static_prompt="Curate.")

    assert build_model_settings(model=COMPATIBLE_MODEL, prompt_cache=identity).extra_args is None
    assert (
        build_model_settings(
            model=OPENAI_MODEL, provider_override="groq", prompt_cache=identity
        ).extra_args
        is None
    )
    assert prompt_cache_extra_args(identity, model=COMPATIBLE_MODEL) is None


def test_build_model_settings_requires_a_prompt_cache_identity():
    with pytest.raises(TypeError):
        build_model_settings(model=OPENAI_MODEL)  # type: ignore[call-arg]


def test_sdk_does_not_generate_a_key_when_ours_is_present():
    from agents.models.openai_responses import OpenAIResponsesModel
    from agents.run_internal.prompt_cache_key import (
        PromptCacheKeyResolver,
        model_settings_with_prompt_cache_key,
    )
    from openai import AsyncOpenAI

    official = OpenAIResponsesModel(model=OPENAI_MODEL, openai_client=AsyncOpenAI(api_key="k"))
    settings = build_model_settings(
        model=OPENAI_MODEL,
        prompt_cache=PromptCacheIdentity(agent_key="figure_locator_classifier", static_prompt="p"),
    )
    resolver = PromptCacheKeyResolver()

    generated = resolver.resolve(
        settings, model=official, conversation_id=None, session=None, group_id="session-1"
    )

    assert generated is None
    assert model_settings_with_prompt_cache_key(settings, generated) is settings
    # Without our key the SDK would have derived one from the grouping.
    assert resolver.resolve(
        ModelSettings(), model=official, conversation_id=None, session=None, group_id="session-1"
    ).startswith("agents-sdk:")


# ---------------------------------------------------------------------------
# Static prompt layers
# ---------------------------------------------------------------------------


def test_static_prefix_excludes_runtime_context_and_keeps_static_first():
    first = _bundle("allele_validation", base="Validate.", runtime="Document doc-1, run 1")
    second = _bundle("allele_validation", base="Validate.", runtime="Document doc-2, run 2")

    assert first.hash != second.hash
    assert first.static_prefix() == second.static_prefix()
    assert "doc-1" not in first.static_prefix()
    assert first.render().startswith(first.static_prefix())


def test_static_prefix_includes_group_layer():
    plain = _bundle("allele_validation", base="Validate.")
    grouped = _bundle("allele_validation", base="Validate.", group="Group A rules")

    assert grouped.static_prefix() != plain.static_prefix()
    assert grouped.static_prefix().endswith("Group A rules")


def test_static_prefix_rejects_static_layer_after_runtime_context():
    bundle = _bundle("allele_validation", base="Validate.", runtime="per run")
    reordered = assembly._bundle(
        bundle.agent_id, [bundle.layers[0], bundle.layers[2], bundle.layers[1]]
    )

    with pytest.raises(ValueError, match="after runtime context"):
        reordered.static_prefix()


# ---------------------------------------------------------------------------
# Runtime paths
# ---------------------------------------------------------------------------


def _catalog_agent(monkeypatch, *, agent_key="allele_validation", base="Validate alleles."):
    from src.lib.agent_studio import catalog_service

    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda model, **_kwargs: model)
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda *, db_agent, runtime_kwargs, canonical_tool_ids: _bundle(
            db_agent.agent_key,
            base=db_agent.instructions,
            runtime=f"Document {runtime_kwargs.get('document_id')}",
        ),
    )

    def build(document_id: str):
        row = SimpleNamespace(
            id="agent-id",
            agent_key=agent_key,
            visibility="system",
            instructions=base,
            group_prompt_overrides={},
            group_rules_enabled=False,
            template_source=None,
            model_id=OPENAI_MODEL,
            model_temperature=None,
            model_reasoning="low",
            output_schema_key=None,
            tool_ids=[],
            name="Allele Validation",
        )
        return catalog_service._create_db_agent(row, document_id=document_id)

    return build


def test_specialist_validator_and_formatter_key_is_stable_across_documents(monkeypatch):
    build = _catalog_agent(monkeypatch)

    first = build("doc-1")
    second = build("doc-2")

    assert first.instructions != second.instructions
    assert _key(first.model_settings) == _key(second.model_settings)
    assert _key(first.model_settings).startswith("allele_validation:")


def test_specialist_key_differs_per_agent_and_prompt_version(monkeypatch):
    baseline = _key(_catalog_agent(monkeypatch)("doc-1").model_settings)
    other_agent = _key(_catalog_agent(monkeypatch, agent_key="chat_output")("doc-1").model_settings)
    new_prompt = _key(
        _catalog_agent(monkeypatch, base="Validate alleles. Revised.")("doc-1").model_settings
    )

    assert len({baseline, other_agent, new_prompt}) == 3


def test_supervisor_key_ignores_per_turn_runtime_context(monkeypatch):
    from src.lib.openai_agents.agents import supervisor_agent

    monkeypatch.setattr(
        agent_config,
        "get_agent_config",
        lambda _name: SimpleNamespace(model=OPENAI_MODEL, temperature=None, reasoning="low"),
    )
    monkeypatch.setattr(agent_config, "log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda model, **_kwargs: model)
    monkeypatch.setattr(supervisor_agent, "_get_supervisor_specialist_specs", lambda *_a, **_k: [])
    monkeypatch.setattr(supervisor_agent, "_create_dynamic_specialist_tools", lambda **_k: [])
    monkeypatch.setattr(
        supervisor_agent,
        "_build_runtime_tool_availability_note",
        lambda **kwargs: f"Document loaded: {kwargs['document_loaded']}",
    )
    monkeypatch.setattr(
        supervisor_agent,
        "build_agent_prompt_layers",
        lambda agent_id, group_id=None, runtime_context=None: _bundle(
            agent_id, base="Route curation requests.", runtime=runtime_context
        ),
    )
    monkeypatch.setattr(supervisor_agent, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(supervisor_agent, "set_pending_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.log_agent_config", lambda **_kwargs: None
    )
    monkeypatch.setattr(supervisor_agent, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    with_document = supervisor_agent.create_supervisor_agent(document_id="doc-1", user_id="u-1")
    without_document = supervisor_agent.create_supervisor_agent(document_id=None, user_id=None)

    assert with_document.instructions != without_document.instructions
    assert _key(with_document.model_settings) == _key(without_document.model_settings)
    assert _key(with_document.model_settings).startswith("supervisor:")


def test_agent_studio_settings_carry_stable_key():
    from src.lib.agent_studio import openai_runtime as studio

    identity = PromptCacheIdentity(agent_key="agent_studio_authoring", static_prompt="Studio")

    first = studio.build_agent_studio_model_settings(max_output_tokens=100, prompt_cache=identity)
    second = studio.build_agent_studio_model_settings(
        max_output_tokens=4096, tool_choice="save_flow", prompt_cache=identity
    )

    assert _key(first) == _key(second)
    assert _key(first).startswith("agent_studio_authoring:")


@pytest.mark.asyncio
async def test_figure_locator_classifier_key_is_stable_across_batches(monkeypatch):
    from src.lib.pipeline import figure_locator_resolution as locator

    agents_built = []
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda model, **_kwargs: model)
    monkeypatch.setattr(
        "agents.Agent", lambda **kwargs: agents_built.append(kwargs) or SimpleNamespace(**kwargs)
    )
    monkeypatch.setattr(locator, "gen_ai_invoke_agent_span", lambda **_kwargs: nullcontext(None))

    async def run(agent, prompt, **_kwargs):
        raise RuntimeError("stop after construction")

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", run)
    for chunk_id in ("chunk-a", "chunk-b"):
        candidate = (SimpleNamespace(id=chunk_id, content=f"Figure {chunk_id}"), f"Figure {chunk_id}")
        with pytest.raises(RuntimeError, match="stop after construction"):
            await locator._call_figure_locator_classifier(
                [candidate], model_name=OPENAI_MODEL, reasoning_effort="low"
            )

    keys = {_key(kwargs["model_settings"]) for kwargs in agents_built}
    assert len(agents_built) == 2
    assert len(keys) == 1
    assert next(iter(keys)).startswith("figure_locator_classifier:")


def test_structured_retry_agent_gets_its_own_stable_key_only_for_native_openai():
    from src.lib.openai_agents import streaming_tools

    keyed_specialist = SimpleNamespace(
        model=OPENAI_MODEL,
        model_settings=ModelSettings(extra_args={"prompt_cache_key": "allele_validation:abc"}),
    )
    compatible_specialist = SimpleNamespace(model=object(), model_settings=ModelSettings())

    first = streaming_tools._structured_retry_model_settings(
        keyed_specialist, agent_key="allele_validation", instructions="Synthesize."
    )
    second = streaming_tools._structured_retry_model_settings(
        keyed_specialist, agent_key="allele_validation", instructions="Synthesize."
    )

    assert _key(first) == _key(second)
    assert _key(first).startswith("allele_validation.structured_retry:")
    assert (
        streaming_tools._structured_retry_model_settings(
            compatible_specialist, agent_key="allele_validation", instructions="Synthesize."
        ).extra_args
        is None
    )
