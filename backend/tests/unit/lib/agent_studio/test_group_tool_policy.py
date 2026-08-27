"""Group-scoped tool policy resolution and runtime construction tests."""

from types import SimpleNamespace

import pytest

from src.lib.agent_studio import catalog_service
from src.lib.group_tool_policy import parse_group_tool_policy, resolve_group_tool_policy


POLICY = {
    "rules": [
        {
            "tool_id": "zfin_genotype_context_helper",
            "allowed_group_ids": ["ZFIN"],
            "field_paths": ["expression_experiment.specimen_genomic_model"],
        },
        {
            "tool_id": "restricted_base_helper",
            "allowed_group_ids": ["RGD"],
            "field_paths": ["annotation.subject"],
        },
    ]
}


@pytest.mark.parametrize(
    ("active_groups", "expected_tools", "expected_added", "expected_denied"),
    [
        (
            ["ZFIN"],
            ["base_helper", "zfin_genotype_context_helper"],
            ["zfin_genotype_context_helper"],
            ["restricted_base_helper"],
        ),
        (
            ["RGD"],
            ["base_helper", "restricted_base_helper"],
            [],
            ["zfin_genotype_context_helper"],
        ),
        (
            ["WB"],
            ["base_helper"],
            [],
            ["zfin_genotype_context_helper", "restricted_base_helper"],
        ),
        (
            ["RGD", "ZFIN"],
            [
                "base_helper",
                "restricted_base_helper",
                "zfin_genotype_context_helper",
            ],
            ["zfin_genotype_context_helper"],
            [],
        ),
        (
            [],
            ["base_helper"],
            [],
            ["zfin_genotype_context_helper", "restricted_base_helper"],
        ),
    ],
)
def test_resolve_group_tool_policy_adds_restricts_and_audits(
    active_groups,
    expected_tools,
    expected_added,
    expected_denied,
):
    resolution = resolve_group_tool_policy(
        ["base_helper", "restricted_base_helper"],
        POLICY,
        active_groups,
    )

    assert resolution.tool_ids == expected_tools
    assert resolution.added_tool_ids == expected_added
    assert resolution.denied_tool_ids == expected_denied


def test_policy_free_agent_preserves_base_tools_and_never_infers_broad_tool():
    resolution = resolve_group_tool_policy(
        ["search_document"],
        None,
        ["ZFIN", "RGD"],
    )

    assert resolution.tool_ids == ["search_document"]
    assert "agr_curation_query" not in resolution.tool_ids
    assert resolution.audit_metadata() == {
        "active_group_ids": ["ZFIN", "RGD"],
        "base_tool_ids": ["search_document"],
        "added_tool_ids": [],
        "denied_tool_ids": [],
    }


def test_policy_validation_requires_field_scope_and_unique_tools():
    with pytest.raises(ValueError, match="field_paths must be a non-empty list"):
        parse_group_tool_policy(
            {
                "rules": [
                    {
                        "tool_id": "zfin_helper",
                        "allowed_group_ids": ["ZFIN"],
                    }
                ]
            }
        )

    with pytest.raises(ValueError, match="duplicate tool_id 'zfin_helper'"):
        parse_group_tool_policy(
            {
                "rules": [
                    {
                        "tool_id": "zfin_helper",
                        "allowed_group_ids": ["ZFIN"],
                        "field_paths": ["one"],
                    },
                    {
                        "tool_id": "zfin_helper",
                        "allowed_group_ids": ["ZFIN"],
                        "field_paths": ["two"],
                    },
                ]
            }
        )


def test_resolution_rejects_noncanonical_authenticated_group_context():
    with pytest.raises(ValueError, match="Unknown group ID 'unknown-group'"):
        resolve_group_tool_policy(
            ["base_helper"],
            POLICY,
            ["unknown-group"],
        )


@pytest.mark.parametrize(
    ("authenticated_groups", "expected_tool_ids"),
    [([], ["base_helper"]), (["ZFIN"], ["base_helper", "restricted_base_helper"])],
)
def test_create_custom_db_agent_enforces_inherited_group_tool_policy(
    monkeypatch,
    authenticated_groups,
    expected_tool_ids,
):
    row = SimpleNamespace(
        agent_key="ca_gene_expression",
        visibility="private",
        template_source="gene_expression",
        tool_ids=["base_helper", "restricted_base_helper"],
        group_tool_policy={
            "rules": [
                {
                    "tool_id": "restricted_base_helper",
                    "allowed_group_ids": ["ZFIN"],
                    "field_paths": ["expression_experiment.specimen_genomic_model"],
                }
            ]
        },
        output_schema_key=None,
        model_id="test-model",
        model_temperature=0.1,
        model_reasoning="medium",
        name="Gene Expression",
    )
    captured = {}
    monkeypatch.setattr(
        catalog_service,
        "resolve_tools",
        lambda tool_ids, _context: captured.setdefault("tool_ids", list(tool_ids)),
    )
    monkeypatch.setattr(
        catalog_service,
        "_build_runtime_instructions",
        lambda **_kwargs: SimpleNamespace(
            render=lambda: "instructions",
            hash="hash",
            to_manifest=lambda: {},
        ),
    )
    monkeypatch.setattr(catalog_service, "prompt_templates_for_bundle", lambda _bundle: [])
    monkeypatch.setattr(catalog_service, "set_pending_prompts", lambda *_args, **_kwargs: "run")
    monkeypatch.setattr(catalog_service, "bind_prompt_run", lambda *_args: None)
    monkeypatch.setattr(catalog_service, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    logged_config = {}
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.log_agent_config",
        lambda **kwargs: logged_config.update(kwargs),
    )

    from src.lib.openai_agents import config as agent_config

    monkeypatch.setattr(agent_config, "resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(agent_config, "get_model_for_agent", lambda *_args, **_kwargs: "model")
    monkeypatch.setattr(agent_config, "build_model_settings", lambda **kwargs: kwargs)

    built = catalog_service._create_db_agent(
        row,
        active_groups=["ZFIN"],
        authenticated_groups=authenticated_groups,
    )

    assert built is not None
    assert captured["tool_ids"] == expected_tool_ids
    assert built.group_tool_exposure["active_group_ids"] == authenticated_groups
    assert built.group_tool_exposure["added_tool_ids"] == []
    assert built.group_tool_exposure["denied_tool_ids"] == (
        [] if authenticated_groups else ["restricted_base_helper"]
    )
    assert logged_config["tools"] == expected_tool_ids
    assert logged_config["metadata"]["group_tool_exposure"] == built.group_tool_exposure
