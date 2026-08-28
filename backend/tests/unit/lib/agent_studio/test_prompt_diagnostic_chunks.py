"""Unit coverage for compact and chunk-addressable prompt inspection."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.lib.agent_studio.diagnostic_tools import tool_definitions
from src.lib.prompts.assembly import PromptLayer, PromptLayerBundle


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bundle(*, group_id: str | None = None) -> PromptLayerBundle:
    layers = [
        PromptLayer(
            id="demo_agent:core_static",
            kind="core_static",
            title="Core",
            content="Locked core",
            provenance="backend_static",
            editable=False,
            locked=True,
            source_ref="src.lib.prompts.assembly:CORE_STATIC_PROMPT",
            hash=_hash("Locked core"),
        ),
        PromptLayer(
            id="demo_agent:base_prompt",
            kind="base_prompt",
            title="Base",
            content="Exact editable base prompt",
            provenance="prompt_template:system",
            editable=True,
            locked=False,
            source_ref="prompt_templates:base-id:version:1",
            hash=_hash("Exact editable base prompt"),
        ),
    ]
    if group_id == "test_group":
        layers.append(
            PromptLayer(
                id="demo_agent:group_rules:test_group",
                kind="group_rules",
                title="Test group rules",
                content="Exact test group rules",
                provenance="prompt_template:group_rules",
                editable=True,
                locked=False,
                source_ref="prompt_templates:group-id:version:1",
                hash=_hash("Exact test group rules"),
            )
        )
    bundle = PromptLayerBundle(agent_id="demo_agent", layers=tuple(layers), hash="")
    return PromptLayerBundle(
        agent_id=bundle.agent_id,
        layers=bundle.layers,
        hash=_hash(bundle.render()),
    )


@pytest.fixture
def prompt_handler(monkeypatch):
    from src.lib.agent_studio import catalog_service

    agent = SimpleNamespace(
        agent_name="Demo Agent",
        description="Demo prompt",
        source_file="packages/demo/agents/demo_agent/prompt.yaml",
        has_group_rules=True,
        group_rules={"test_group": SimpleNamespace()},
    )
    service = SimpleNamespace(
        get_agent=lambda agent_id: agent if agent_id == "demo_agent" else None,
        get_effective_prompt_bundle=lambda agent_id, group_id=None: (
            _bundle(group_id=group_id) if agent_id == "demo_agent" else None
        ),
        catalog=SimpleNamespace(
            categories=[SimpleNamespace(agents=[SimpleNamespace(agent_id="demo_agent")])]
        ),
    )
    monkeypatch.setattr(catalog_service, "get_prompt_catalog", lambda: service)
    return tool_definitions._create_get_prompt_handler()


def test_default_prompt_view_is_content_free_manifest(prompt_handler):
    result = prompt_handler(agent_id="demo_agent")

    assert result["status"] == "ok"
    assert result["view"] == "summary"
    assert result["effective_prompt_hash"] == _bundle().hash
    assert result["effective_prompt_total_length"] == len(_bundle().render())
    assert result["group_id_requested"] is None
    assert result["group_id_applied"] is None
    assert [layer["index"] for layer in result["layers"]] == [0, 1]
    assert [layer["id"] for layer in result["layers"]] == [
        "demo_agent:core_static",
        "demo_agent:base_prompt",
    ]
    assert all("content" not in layer for layer in result["layers"])
    assert "prompt" not in result
    assert "layer_manifest" not in result


def test_effective_prompt_chunks_reconstruct_exact_group_render(monkeypatch, prompt_handler):
    monkeypatch.setenv("AGENT_STUDIO_PROMPT_INSPECTION_CHUNK_MAX_CHARS", "7")
    expected = _bundle(group_id="test_group")
    summary = prompt_handler(agent_id="demo_agent", group_id="test_group")
    chunks = []
    cursor = 0

    while True:
        result = prompt_handler(
            agent_id="demo_agent",
            group_id="test_group",
            view="effective_prompt",
            cursor=cursor,
            max_chars=100,
        )
        chunks.append(result["content"])
        assert result["hash"] == summary["effective_prompt_hash"] == expected.hash
        assert result["total_length"] == summary["effective_prompt_total_length"]
        assert result["returned_range"]["start"] == cursor
        if result["complete"]:
            assert result["next_cursor"] is None
            break
        cursor = result["next_cursor"]

    assert summary["group_id_applied"] == "test_group"
    assert "Exact test group rules" in "".join(chunks)
    assert "".join(chunks) == expected.render()


def test_layer_chunks_support_stable_id_and_index(prompt_handler):
    expected_layer = _bundle().layers[1]

    by_id = prompt_handler(
        agent_id="demo_agent",
        view="layer",
        layer_id=expected_layer.id,
        max_chars=10,
    )
    by_index = prompt_handler(
        agent_id="demo_agent",
        view="layer",
        layer_index=1,
        max_chars=10,
    )

    assert by_id["content"] == by_index["content"] == expected_layer.content[:10]
    assert by_id["hash"] == by_index["hash"] == expected_layer.hash
    assert by_id["layer"]["id"] == expected_layer.id
    assert "content" not in by_id["layer"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"view": "layer"}, "exactly one"),
        ({"view": "layer", "layer_id": "missing"}, "not found"),
        ({"view": "layer", "layer_index": 20}, "out of range"),
        (
            {"view": "layer", "layer_id": "demo_agent:base_prompt", "layer_index": 1},
            "exactly one",
        ),
        ({"view": "effective_prompt", "cursor": 10_000}, "exceeds target length"),
    ],
)
def test_prompt_detail_rejects_invalid_addresses(prompt_handler, kwargs, message):
    result = prompt_handler(agent_id="demo_agent", **kwargs)

    assert result["status"] == "error"
    assert message in result["message"]


def test_prompt_schema_advertises_only_summary_and_bounded_detail_views(
    monkeypatch,
):
    from src.lib.prompts import cache as prompt_cache

    monkeypatch.setattr(prompt_cache, "is_initialized", lambda: False)
    monkeypatch.setenv("AGENT_STUDIO_PROMPT_INSPECTION_CHUNK_MAX_CHARS", "4321")

    description, schema = tool_definitions._get_prompt_diagnostic_contract()

    properties = schema["properties"]
    assert properties["view"]["enum"] == ["summary", "effective_prompt", "layer"]
    assert properties["max_chars"]["maximum"] == 4321
    assert properties["cursor"]["minimum"] == 0
    assert "next_cursor" in description
    assert "prompt" not in properties
    assert "layer_manifest" not in properties


def test_get_prompt_remains_available_in_all_supported_agent_studio_contexts():
    from src.api import agent_studio as api_module
    from src.lib.agent_studio.models import AgentWorkshopContext, ChatContext

    contexts = [
        ChatContext(active_tab="agents"),
        ChatContext(active_tab="flows"),
        ChatContext(
            active_tab="agent_workshop",
            agent_workshop=AgentWorkshopContext(prompt_draft="Review this prompt"),
        ),
    ]

    assert all(
        api_module._is_tool_allowed_for_context("get_prompt", context)
        for context in contexts
    )


def test_installed_disease_prompt_and_document_tool_results_stay_provider_visible(
    monkeypatch,
):
    import yaml

    from src.api import agent_studio as api_module
    from src.lib.agent_studio import catalog_service
    from src.lib.config.agent_loader import load_agent_definitions
    from src.lib.prompts import assembly
    from src.models.sql.prompts import PromptTemplate

    definition = load_agent_definitions()["disease_extractor"]
    assert definition.package_path is not None
    agent_dir = definition.package_path / "agents" / definition.folder_name
    prompt_content = yaml.safe_load(
        (agent_dir / "prompt.yaml").read_text(encoding="utf-8")
    )["content"]
    group_content = yaml.safe_load(
        (agent_dir / "group_rules" / "fb.yaml").read_text(encoding="utf-8")
    )["content"]
    now = datetime.now(timezone.utc)
    prompt_cache = {
        "disease_extractor:system:base": PromptTemplate(
            id=uuid.uuid4(),
            agent_name="disease_extractor",
            prompt_type="system",
            group_id=None,
            content=prompt_content,
            version=1,
            is_active=True,
            created_at=now,
            created_by="test@example.org",
            source_file=str(agent_dir / "prompt.yaml"),
        ),
        "disease_extractor:group_rules:test_group": PromptTemplate(
            id=uuid.uuid4(),
            agent_name="disease_extractor",
            prompt_type="group_rules",
            group_id="test_group",
            content=group_content,
            version=1,
            is_active=True,
            created_at=now,
            created_by="test@example.org",
            source_file=str(agent_dir / "group_rules" / "fb.yaml"),
        ),
    }
    monkeypatch.setattr(assembly, "get_all_active_prompts", lambda: prompt_cache)
    bundle = assembly.build_agent_prompt_layers(
        "disease_extractor", group_id="test_group"
    )
    agent = SimpleNamespace(
        agent_name=definition.name,
        description=definition.description,
        source_file=str(agent_dir / "prompt.yaml"),
        has_group_rules=True,
        group_rules={"test_group": SimpleNamespace()},
    )
    service = SimpleNamespace(
        get_agent=lambda agent_id: agent if agent_id == "disease_extractor" else None,
        get_effective_prompt_bundle=lambda agent_id, group_id=None: (
            bundle
            if agent_id == "disease_extractor" and group_id == "test_group"
            else None
        ),
        catalog=SimpleNamespace(categories=[]),
    )
    monkeypatch.setattr(catalog_service, "get_prompt_catalog", lambda: service)
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "12000")
    monkeypatch.setenv("AGENT_STUDIO_PROMPT_INSPECTION_CHUNK_MAX_CHARS", "8000")
    prompt_handler = tool_definitions._create_get_prompt_handler()

    prompt_summary = prompt_handler(
        agent_id="disease_extractor", group_id="test_group"
    )
    prompt_detail_results = []
    cursor = 0
    while True:
        chunk = prompt_handler(
            agent_id="disease_extractor",
            group_id="test_group",
            view="effective_prompt",
            cursor=cursor,
        )
        prompt_detail_results.append(chunk)
        if chunk["complete"]:
            break
        cursor = chunk["next_cursor"]

    reconstructed_layers: dict[str, str] = {}
    for layer in bundle.layers:
        layer_chunks = []
        cursor = 0
        while True:
            chunk = prompt_handler(
                agent_id="disease_extractor",
                group_id="test_group",
                view="layer",
                layer_id=layer.id,
                cursor=cursor,
            )
            prompt_detail_results.append(chunk)
            layer_chunks.append(chunk["content"])
            if chunk["complete"]:
                break
            cursor = chunk["next_cursor"]
        reconstructed_layers[layer.id] = "".join(layer_chunks)

    inventory = tool_definitions._create_get_tool_inventory_handler()(
        agent_id="disease_extractor"
    )
    details = tool_definitions._create_get_tool_details_handler()(
        tool_id="search_document",
        agent_id="disease_extractor",
    )
    boundary_results = [
        ("get_prompt", result)
        for result in [prompt_summary, *prompt_detail_results]
    ] + [
        ("get_tool_inventory", inventory),
        ("get_tool_details", details),
    ]

    for tool_name, result in boundary_results:
        content = api_module._provider_tool_result_content(
            tool_name=tool_name,
            tool_input={"agent_id": "disease_extractor"},
            tool_result=result,
            session_id="session-1",
            turn_id="turn-1",
        )
        provider_result = json.loads(content)
        assert provider_result.get("status") != "compacted_tool_result"
        assert len(content) <= 12_000

    reconstructed = "".join(
        result["content"]
        for result in prompt_detail_results
        if result["view"] == "effective_prompt"
    )
    assert reconstructed == bundle.render()
    assert prompt_summary["effective_prompt_hash"] == bundle.hash
    assert reconstructed_layers == {
        layer.id: layer.content for layer in bundle.layers
    }
