"""Execution capture freezes selected settings and no-template agents remain valid."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.agent_studio.execution_snapshot import (
    capture_execution_snapshot,
    saved_runtime_prompt_bundle,
)
from src.schemas.agent_execution_revision import AgentOutputContract


@pytest.fixture(autouse=True)
def configured_test_groups(monkeypatch):
    from src.lib.config import groups_loader

    configured_groups = groups_loader.get_valid_group_ids()
    monkeypatch.setattr(
        groups_loader, "get_valid_group_ids",
        lambda: [*configured_groups, "TEAM_A", "TEAM_B"],
    )


def agent():
    return SimpleNamespace(
        id=uuid4(),
        agent_key="ca_test",
        name="Scratch agent",
        template_source=None,
        group_rules_component=None,
        instructions="Curator instructions",
        tool_ids=[],
        model_id="test-model",
        model_temperature=0.0,
        model_reasoning=None,
        group_tool_policy={"rules": []},
        allowed_group_ids=["TEAM_A"],
        inherited_allowed_group_ids=["TEAM_A"],
        group_rules_enabled=False,
        group_prompt_overrides={},
    )


def test_capture_scratch_none_and_render_never_reads_mutable_head(monkeypatch):
    from src.lib.agent_studio import custom_agent_service, catalog_service
    from src.lib.prompts import cache

    monkeypatch.setattr(custom_agent_service, "_system_managed_tool_ids", lambda *_: [])
    monkeypatch.setattr(
        catalog_service, "_inherited_curation_definition_for_db_agent", lambda _: None
    )
    head = agent()
    saved = capture_execution_snapshot(
        None, head, AgentOutputContract(output_state="none")
    )
    original = deepcopy(saved.model_dump(mode="json"))
    head.model_id = "changed-model"
    head.model_temperature = 0.9
    head.model_reasoning = "high"
    head.instructions = "Changed instructions"
    head.tool_ids = ["changed-tool"]
    head.allowed_group_ids = []
    head.inherited_allowed_group_ids = []
    head.group_rules_enabled = True
    head.group_rules_component = "changed-component"
    head.template_source = "changed-template"
    head.group_prompt_overrides = {"TEAM_A": "Changed"}
    head.group_tool_policy = {
        "rules": [{"group_id": "TEAM_A", "tool_ids": ["changed-tool"]}]
    }
    monkeypatch.setattr(
        cache,
        "get_all_active_prompts",
        lambda: (_ for _ in ()).throw(AssertionError("live lookup")),
    )
    rendered = saved_runtime_prompt_bundle(
        saved, runtime_context="Current document context"
    )
    assert saved.model_dump(mode="json") == original
    assert saved.model_temperature == 0.0
    assert saved.output_contract.output_state == "none"
    assert saved.curation is None and saved.structured_finalization is None
    assert "Curator instructions" in rendered.render()
    assert rendered.layers[-1].kind == "runtime_context"
    assert "Changed" not in rendered.render()


def test_group_layers_are_frozen_and_selected_per_run(monkeypatch):
    from src.lib.agent_studio import custom_agent_service, catalog_service
    from src.lib.config import groups_loader

    monkeypatch.setattr(custom_agent_service, "_system_managed_tool_ids", lambda *_: [])
    monkeypatch.setattr(
        catalog_service, "_inherited_curation_definition_for_db_agent", lambda _: None
    )
    monkeypatch.setattr(groups_loader, "get_valid_group_ids", lambda: ["TEAM_A", "TEAM_B"])
    head = agent()
    head.group_rules_enabled = True
    head.group_prompt_overrides = {"TEAM_A": "Saved TEAM_A rules", "TEAM_B": "Saved TEAM_B rules"}
    saved = capture_execution_snapshot(
        None, head, AgentOutputContract(output_state="none")
    )
    head.group_prompt_overrides["TEAM_A"] = "Today's changed rules"
    fb = saved_runtime_prompt_bundle(saved, active_groups=["TEAM_A"]).render()
    assert "Saved TEAM_A rules" in fb and "Saved TEAM_B rules" not in fb
    assert "Today's changed rules" not in fb


def test_pinned_catalog_build_uses_saved_settings_not_live_template(monkeypatch):
    from src.lib.agent_studio import catalog_service
    from src.lib.openai_agents import config, langfuse_client, streaming_tools
    from src.lib.config import agent_loader

    head = agent()
    saved = capture_execution_snapshot(None, head, AgentOutputContract(output_state="none"))
    # An intentionally tiny identity object proves the builder cannot read any
    # mutable runtime field from the current head (including the template).
    identity = SimpleNamespace(name="Display label", agent_key=head.agent_key, visibility="private")

    def unexpected(*args, **kwargs):
        raise AssertionError("Pinned execution performed a live template lookup")

    monkeypatch.setattr(catalog_service, "_build_runtime_instructions", unexpected)
    monkeypatch.setattr(catalog_service, "_attach_live_curation_metadata", unexpected)
    monkeypatch.setattr(agent_loader, "get_agent_by_tool_name", unexpected)
    monkeypatch.setattr(config, "resolve_model_provider", lambda *_args, **_kwargs: "openai")
    monkeypatch.setattr(config, "get_model_for_agent", lambda model, **_kwargs: model)
    monkeypatch.setattr(config, "build_model_settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(langfuse_client, "log_agent_config", lambda **_kwargs: None)
    built = catalog_service._create_db_agent(identity, execution_snapshot=saved)
    assert built.model == "test-model"
    assert built.model_settings["temperature"] == 0.0
    assert "Curator instructions" in built.instructions
    assert built.output_type is None
    assert built.output_contract["output_state"] == "none"
    assert built.execution_snapshot_fingerprint == saved.fingerprint()
    assert streaming_tools._agent_structured_finalization_config(
        built, tool_name="ask_changed_parent"
    ) == {}
    with pytest.raises(ValueError, match="cannot be overridden"):
        catalog_service._create_db_agent(
            identity, execution_snapshot=saved, model_id_override="new-model"
        )


def test_explicit_generic_output_captures_generic_curation_for_scratch_agent(monkeypatch):
    from src.lib.agent_studio import custom_agent_service, catalog_service

    monkeypatch.setattr(custom_agent_service, "_system_managed_tool_ids", lambda *_: [])
    monkeypatch.setattr(catalog_service, "_inherited_curation_definition_for_db_agent", lambda _: None)
    saved = capture_execution_snapshot(None, agent(), AgentOutputContract(
        output_state="structured_extraction", output_mode="unprofiled_generic",
    ))
    assert saved.curation == {"adapter_key": "generic", "domain_pack_id": "generic", "launchable": True}
    assert saved.structured_finalization is None
