"""Extraction agents never carry identity-lookup tools (ALL-1276).

Tools declare ``identity_lookup: true`` in their package binding metadata. An
agent extracts when it carries a builder finalize tool or saves a structured
extraction output that is not a validator result. Save, packaged load (see
test_runtime_validation.py), runtime and revision restore all refuse the
combination; validation and lookup agents keep their tools.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.packages import tool_roles


@pytest.fixture(autouse=True)
def demo_tool_roles(monkeypatch):
    monkeypatch.setattr(tool_roles, "builder_finalization_tool_names", lambda: frozenset({"finalize_demo"}))
    monkeypatch.setattr(tool_roles, "identity_lookup_tool_names", lambda: frozenset({"lookup_demo"}))
    monkeypatch.setattr(
        tool_roles, "is_validator_output_schema", lambda key: key == "DemoValidationResult",
    )


# --- The role rule ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_ids", "output_state", "output_schema_key", "expected"),
    [
        (["finalize_demo"], None, None, True),
        (["search"], "structured_extraction", None, True),
        (["search"], "structured_extraction", "DemoEnvelope", True),
        (["search"], "structured_extraction", "DemoValidationResult", False),
        (["search"], "none", None, False),
        (["search"], None, None, False),
    ],
)
def test_an_agent_extracts_with_a_builder_finalizer_or_a_structured_extraction_output(
    tool_ids, output_state, output_schema_key, expected,
):
    assert tool_roles.is_extraction_agent(
        tool_ids, output_state=output_state, output_schema_key=output_schema_key,
    ) is expected


def test_only_extraction_agents_are_refused_their_identity_lookups():
    with pytest.raises(ValueError, match="cannot use database lookup tools \\(lookup_demo\\)"):
        tool_roles.require_no_identity_lookup_on_extraction(
            ["finalize_demo", "lookup_demo"], agent_label="Agent 'demo_extractor'",
        )
    tool_roles.require_no_identity_lookup_on_extraction(
        ["lookup_demo"],
        output_state="structured_extraction",
        output_schema_key="DemoValidationResult",
        agent_label="Agent 'demo_validator'",
    )
    tool_roles.require_no_identity_lookup_on_extraction(["lookup_demo"], agent_label="Agent 'demo_lookup'")


def test_the_registry_derives_both_roles_from_binding_metadata(monkeypatch):
    monkeypatch.undo()
    tool_roles.reset_cache()
    monkeypatch.setattr(tool_roles, "tool_metadata_by_name", lambda: {
        "finalize_demo": {"builder_finalization": True},
        "lookup_demo": {"identity_lookup": True},
        "species_demo": {"identity_lookup": False},
        "search": {},
    })
    try:
        assert tool_roles.builder_finalization_tool_names() == frozenset({"finalize_demo"})
        assert tool_roles.identity_lookup_tool_names() == frozenset({"lookup_demo"})
    finally:
        monkeypatch.undo()
        tool_roles.reset_cache()


# --- Save: an extraction agent never inherits identity lookups from its template -------------


def test_inherited_identity_lookups_are_dropped_for_an_extraction_agent_only():
    from src.lib.agent_studio.custom_agent_service import _merge_system_managed_tool_ids

    extractor = _merge_system_managed_tool_ids(["search", "finalize_demo"], ["lookup_demo", "helper"])
    lookup_agent = _merge_system_managed_tool_ids(["search"], ["lookup_demo", "helper"])
    # A lookup the curator asks for is kept, so validation reports it instead of hiding it.
    requested = _merge_system_managed_tool_ids(["finalize_demo", "lookup_demo"], ["lookup_demo"])

    assert extractor == ["search", "finalize_demo", "helper"]
    assert lookup_agent == ["search", "lookup_demo", "helper"]
    assert requested == ["finalize_demo", "lookup_demo"]


# --- Runtime: a saved or pinned extraction agent with identity lookups does not run ----------


def _patch_agent_build(monkeypatch, catalog_service, captured):
    monkeypatch.setattr(
        catalog_service, "resolve_tools",
        lambda tool_ids, _context: captured.setdefault("tool_ids", list(tool_ids)),
    )
    monkeypatch.setattr(
        catalog_service, "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "instructions", static_prefix=lambda: "instructions", hash="hash",
            to_manifest=lambda: {},
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "set_pending_prompts", lambda *_args, **_kwargs: "run")
    monkeypatch.setattr(catalog_service, "bind_prompt_run", lambda *_args: None)
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr("src.lib.openai_agents.langfuse_client.log_agent_config", lambda **_kwargs: None)
    from src.lib.openai_agents import config as agent_config

    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda *_args, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: kwargs)


def _row(tool_ids, **overrides):
    return SimpleNamespace(**{
        "agent_key": "ca_demo",
        "visibility": "private",
        "template_source": "demo_template",
        "tool_ids": tool_ids,
        "group_tool_policy": {},
        "output_schema_key": None,
        "model_id": "test-model",
        "model_temperature": 0.1,
        "model_reasoning": "medium",
        "name": "Demo agent",
        **overrides,
    })


def test_an_extraction_agent_with_identity_lookups_is_refused_before_its_tools_are_built(monkeypatch):
    from src.lib.agent_studio import catalog_service

    captured = {}
    _patch_agent_build(monkeypatch, catalog_service, captured)

    with pytest.raises(ValueError, match="extraction agents cannot use database lookup tools"):
        catalog_service._create_db_agent(_row(["finalize_demo", "lookup_demo"]), authenticated_groups=[])
    assert "tool_ids" not in captured


def test_a_group_scoped_identity_lookup_is_refused_on_an_extraction_agent(monkeypatch):
    from src.lib.agent_studio import catalog_service

    captured = {}
    _patch_agent_build(monkeypatch, catalog_service, captured)
    # The authenticated group's rule adds the lookup to the extractor's base tools.
    monkeypatch.setattr(
        catalog_service, "resolve_group_tool_policy",
        lambda base, _policy, _groups: SimpleNamespace(
            tool_ids=[*base, "lookup_demo"],
            audit_metadata=lambda: {
                "base_tool_ids": list(base), "added_tool_ids": ["lookup_demo"],
                "denied_tool_ids": [], "active_group_ids": ["TEAM_C"],
            },
        ),
    )
    row = _row(
        ["finalize_demo"],
        group_tool_policy={"rules": [{"tool_id": "lookup_demo", "allowed_group_ids": ["TEAM_C"], "field_paths": ["demo.value"]}]},
    )

    with pytest.raises(ValueError, match="lookup_demo"):
        catalog_service._create_db_agent(row, authenticated_groups=["TEAM_C"])


def test_a_lookup_agent_keeps_its_identity_lookup_tools(monkeypatch):
    from src.lib.agent_studio import catalog_service

    captured = {}
    _patch_agent_build(monkeypatch, catalog_service, captured)

    built = catalog_service._create_db_agent(_row(["lookup_demo"]), authenticated_groups=[])

    assert built is not None
    assert captured["tool_ids"] == ["lookup_demo"]


# --- Restore: a saved extraction revision with identity lookups is not restored --------------


def test_restoring_an_extraction_revision_with_identity_lookups_is_refused(monkeypatch):
    from src.lib.agent_studio import execution_revision_service as service

    head_id, revision_id = uuid4(), uuid4()
    head = SimpleNamespace(
        is_active=True, user_id=7, agent_key="ca_demo", execution_revision_id=head_id,
        inherited_allowed_group_ids=[],
    )
    db = SimpleNamespace(execute=lambda _statement: SimpleNamespace(scalar_one_or_none=lambda: head))
    saved = SimpleNamespace(
        # A catalog model, so the restore reaches the identity-lookup refusal.
        model_id="gpt-6-sol",
        inherited_allowed_group_ids=[],
        tool_ids=["stage_demo", "lookup_demo"],
        output_contract=SimpleNamespace(output_state="structured_extraction", output_schema_key=None),
    )
    monkeypatch.setattr(service, "get_execution_revision", lambda *_args, **_kwargs: (None, saved))
    monkeypatch.setattr(service, "append_execution_revision", lambda *_args, **_kwargs: pytest.fail("restored"))

    with pytest.raises(ValueError, match="This saved version is an extraction agent"):
        service.restore_execution_revision(
            db, uuid4(), revision_id, user_id=7, expected_revision_id=head_id, active_group_ids=[],
        )
