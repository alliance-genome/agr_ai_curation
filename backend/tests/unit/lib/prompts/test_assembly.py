"""Unit tests for deterministic prompt layer assembly."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.lib.config.agent_loader import AgentDefinition
from src.lib.prompts import assembly
from src.lib.prompts.cache import PromptNotFoundError
from src.models.sql.prompts import PromptTemplate


def _agent(
    *,
    folder_name: str = "gene",
    agent_id: str = "gene_validation",
    output_schema: str | None = "GeneResultEnvelope",
) -> AgentDefinition:
    return AgentDefinition(
        folder_name=folder_name,
        agent_id=agent_id,
        name="Gene Validation Agent",
        output_schema=output_schema,
    )


def _prompt(
    agent_name: str,
    prompt_type: str,
    content: str,
    *,
    group_id: str | None = None,
    version: int = 1,
) -> PromptTemplate:
    return PromptTemplate(
        id=uuid.uuid4(),
        agent_name=agent_name,
        prompt_type=prompt_type,
        group_id=group_id,
        content=content,
        version=version,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        created_by="tester@example.org",
        source_file=f"packages/test/agents/{agent_name}/{prompt_type}.yaml",
    )


@pytest.fixture
def prompt_cache(monkeypatch):
    prompts = {
        "gene:system:base": _prompt("gene", "system", "Base prompt"),
        "gene:group_rules:FB": _prompt(
            "gene",
            "group_rules",
            "FlyBase rules",
            group_id="FB",
        ),
        "gene:group_rules:WB": _prompt(
            "gene",
            "group_rules",
            "WormBase rules",
            group_id="WB",
        ),
    }
    monkeypatch.setattr(assembly, "get_all_active_prompts", lambda: prompts)
    return prompts


@pytest.fixture(autouse=True)
def agent_registry(monkeypatch):
    monkeypatch.setattr(
        assembly,
        "load_agent_definitions",
        lambda: {"gene_validation": _agent()},
    )
    monkeypatch.setattr(assembly, "resolve_output_schema", lambda _schema_key: None)


def test_core_prompt_layers_are_locked_and_do_not_use_prompt_templates(prompt_cache):
    bundle = assembly.build_agent_core_prompt("gene")

    assert bundle.agent_id == "gene"
    assert bundle.layer_order == ("core_static", "core_generated")
    assert [layer.editable for layer in bundle.layers] == [False, False]
    assert [layer.locked for layer in bundle.layers] == [True, True]
    assert all("prompt_templates:" not in layer.source_ref for layer in bundle.layers)
    assert "GeneResultEnvelope structured output" in bundle.layers[1].content
    assert "Base prompt" not in bundle.render()
    assert "FlyBase rules" not in bundle.render()


def test_prompt_layers_keep_expected_order_and_editability(prompt_cache):
    bundle = assembly.build_agent_prompt_layers(
        "gene_validation",
        group_id=["fb", "WB", "fb"],
        overlay="Curator emphasis",
        runtime_context={"document": "paper.pdf", "active_groups": ["WB", "FB"]},
    )

    assert bundle.layer_order == (
        "core_static",
        "core_generated",
        "base_prompt",
        "group_rules",
        "curator_overlay",
        "runtime_context",
    )

    by_kind = {layer.kind: layer for layer in bundle.layers}
    assert by_kind["base_prompt"].editable is True
    assert by_kind["base_prompt"].locked is False
    assert by_kind["base_prompt"].provenance == "prompt_template:system"
    assert "prompt_templates:" in by_kind["base_prompt"].source_ref

    assert by_kind["group_rules"].editable is True
    assert by_kind["group_rules"].locked is False
    assert by_kind["group_rules"].content.index("## FB") < by_kind[
        "group_rules"
    ].content.index("## WB")
    assert "FlyBase rules" in by_kind["group_rules"].content
    assert "WormBase rules" in by_kind["group_rules"].content

    assert by_kind["curator_overlay"].editable is True
    assert by_kind["curator_overlay"].locked is False
    assert by_kind["runtime_context"].editable is False
    assert by_kind["runtime_context"].locked is True
    assert by_kind["runtime_context"].content == (
        '{"active_groups":["WB","FB"],"document":"paper.pdf"}'
    )


def test_hashes_are_stable_for_same_inputs(prompt_cache):
    first = assembly.build_agent_prompt_layers(
        "gene",
        group_id="FB",
        overlay="Curator emphasis",
        runtime_context={"document": "paper.pdf"},
    )
    second = assembly.build_agent_prompt_layers(
        "gene",
        group_id="FB",
        overlay="Curator emphasis",
        runtime_context={"document": "paper.pdf"},
    )

    assert second.hash == first.hash
    assert [layer.hash for layer in second.layers] == [
        layer.hash for layer in first.layers
    ]
    assert first.to_manifest() == second.to_manifest()


def test_base_prompt_is_required(prompt_cache):
    prompt_cache.pop("gene:system:base")

    with pytest.raises(PromptNotFoundError):
        assembly.build_agent_prompt_layers("gene")
