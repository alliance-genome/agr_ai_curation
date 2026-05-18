"""Parity tests for shared effective-prompt assembly call sites."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.lib.agent_studio import catalog_service
from src.lib.agent_studio.diagnostic_tools import tool_definitions
from src.lib.agent_studio.models import AgentPrompts, PromptCatalog, PromptInfo
from src.lib.config.agent_loader import AgentDefinition
from src.lib.prompts import assembly
from src.models.sql.prompts import PromptTemplate


def _agent_definition() -> AgentDefinition:
    return AgentDefinition(
        folder_name="demo_agent",
        agent_id="demo_agent_validation",
        name="Demo Agent",
        output_schema=None,
    )


def _prompt(
    prompt_type: str,
    content: str,
    *,
    group_id: str | None = None,
) -> PromptTemplate:
    return PromptTemplate(
        id=uuid.uuid4(),
        agent_name="demo_agent",
        prompt_type=prompt_type,
        group_id=group_id,
        content=content,
        version=1,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        created_by="tester@example.org",
        source_file=f"packages/test/demo_agent/{prompt_type}.yaml",
    )


@pytest.fixture
def prompt_parity_service(monkeypatch):
    base_prompt = _prompt("system", "Editable base prompt")
    group_prompt = _prompt("group_rules", "WB group rules", group_id="WB")
    prompt_cache = {
        "demo_agent:system:base": base_prompt,
        "demo_agent:group_rules:WB": group_prompt,
    }
    definition = _agent_definition()

    monkeypatch.setattr(assembly, "load_agent_definitions", lambda: {"demo": definition})
    monkeypatch.setattr(assembly, "get_all_active_prompts", lambda: prompt_cache)
    monkeypatch.setattr(catalog_service, "get_agent_by_folder", lambda key: definition if key == "demo_agent" else None)
    monkeypatch.setattr(
        catalog_service,
        "get_agent_definition",
        lambda key: definition if key == "demo_agent_validation" else None,
    )

    service = catalog_service.PromptCatalogService()
    service._catalog = PromptCatalog(
        categories=[
            AgentPrompts(
                category="Validation",
                agents=[
                    PromptInfo(
                        agent_id="demo_agent",
                        agent_name="Demo Agent",
                        description="Demo validation agent",
                        base_prompt="catalog base should not be used",
                        source_file="database",
                        has_group_rules=True,
                        group_rules={},
                    )
                ],
            )
        ],
        total_agents=1,
        available_groups=["WB"],
    )
    return service


def test_catalog_preview_diagnostic_and_runtime_share_prompt_bundle(
    monkeypatch,
    prompt_parity_service,
):
    import src.api.agent_studio as api_module

    monkeypatch.setattr(api_module, "get_prompt_catalog", lambda: prompt_parity_service)
    monkeypatch.setattr(catalog_service, "get_prompt_catalog", lambda: prompt_parity_service)

    catalog_bundle = prompt_parity_service.get_effective_prompt_bundle(
        "demo_agent",
        group_id="WB",
    )
    assert catalog_bundle is not None

    combined = asyncio.run(
        api_module.get_combined_prompt(
            request=api_module.CombinedPromptRequest(agent_id="demo_agent", group_id="WB"),
            user={"sub": "auth-sub"},
        )
    )
    preview = asyncio.run(
        api_module.get_prompt_preview(
            agent_id="demo_agent",
            group_id="WB",
            user={"sub": "auth-sub"},
            db=SimpleNamespace(),
        )
    )
    diagnostic = tool_definitions._create_get_prompt_handler()(
        agent_id="demo_agent",
        group_id="WB",
    )
    runtime_bundle = catalog_service._build_runtime_instructions(
        SimpleNamespace(
            agent_key="demo_agent",
            visibility="system",
            group_rules_enabled=True,
        ),
        {"active_groups": ["WB"]},
        canonical_tool_ids=[],
    )

    assert combined.combined_prompt == catalog_bundle.render()
    assert preview.prompt == catalog_bundle.render()
    assert diagnostic["prompt"] == catalog_bundle.render()
    assert runtime_bundle.render() == catalog_bundle.render()

    assert combined.effective_prompt_hash == catalog_bundle.hash
    assert preview.effective_prompt_hash == catalog_bundle.hash
    assert diagnostic["effective_prompt_hash"] == catalog_bundle.hash
    assert runtime_bundle.hash == catalog_bundle.hash

    assert combined.layer_manifest == catalog_bundle.to_manifest()
    assert preview.layer_manifest == catalog_bundle.to_manifest()
    assert diagnostic["layer_manifest"] == catalog_bundle.to_manifest()
    assert runtime_bundle.to_manifest() == catalog_bundle.to_manifest()


def test_custom_agent_preview_treats_custom_prompt_as_overlay(
    monkeypatch,
    prompt_parity_service,
):
    import src.api.agent_studio as api_module
    from src.lib.agent_studio import custom_agent_service

    custom_uuid = uuid.uuid4()
    monkeypatch.setattr(custom_agent_service, "parse_custom_agent_id", lambda _agent_id: custom_uuid)
    monkeypatch.setattr(api_module, "set_global_user_from_cognito", lambda _db, _user: SimpleNamespace(id=7))
    monkeypatch.setattr(
        custom_agent_service,
        "get_custom_agent_for_user",
        lambda _db, _uuid, _user_id: SimpleNamespace(
            custom_prompt="Curator overlay instructions",
            parent_agent_key="demo_agent",
            group_rules_enabled=True,
            group_prompt_overrides={"WB": "Curator WB overlay"},
        ),
    )

    response = asyncio.run(
        api_module.get_prompt_preview(
            agent_id=f"ca_{custom_uuid}",
            group_id="WB",
            user={"sub": "auth-sub"},
            db=SimpleNamespace(),
        )
    )

    layer_kinds = [layer["kind"] for layer in response.layer_manifest["layers"]]
    assert layer_kinds == [
        "core_static",
        "base_prompt",
        "group_rules",
        "curator_overlay",
    ]
    assert "Editable base prompt" in response.prompt
    assert "WB group rules" in response.prompt
    assert "Curator overlay instructions" in response.prompt
    assert "Curator WB overlay" in response.prompt


def test_runtime_additional_context_is_final_assembler_layer(prompt_parity_service):
    runtime_bundle = catalog_service._build_runtime_instructions(
        SimpleNamespace(
            agent_key="demo_agent",
            visibility="system",
            group_rules_enabled=True,
        ),
        {
            "active_groups": ["WB"],
            "additional_runtime_context": [
                "## FLOW STEP INSTRUCTIONS\n\nUse the step-local output contract."
            ],
        },
        canonical_tool_ids=[],
    )

    assert "Editable base prompt" in runtime_bundle.render()
    assert "WB group rules" in runtime_bundle.render()
    assert "Use the step-local output contract." in runtime_bundle.render()
    assert runtime_bundle.layer_order == (
        "core_static",
        "base_prompt",
        "group_rules",
        "runtime_context",
    )
    assert runtime_bundle.to_manifest()["layers"][-1]["content"] == (
        "## FLOW STEP INSTRUCTIONS\n\nUse the step-local output contract."
    )
