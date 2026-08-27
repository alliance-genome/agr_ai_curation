"""Alliance package contract for authenticated group-scoped agent tools."""

from pathlib import Path

import pytest

from src.lib.config.agent_loader import AgentDefinition, load_agent_definitions
from src.lib.config.groups_loader import load_groups


REPO_ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture(autouse=True)
def _canonical_groups():
    load_groups(REPO_ROOT / "config" / "groups.yaml", force_reload=True)


def test_package_agent_group_tool_policy_is_strict_and_field_scoped():
    agent = AgentDefinition.from_yaml(
        "gene_expression",
        {
            "agent_id": "gene_expression_extraction",
            "name": "Gene Expression Extractor",
            "tools": ["search_document", "restricted_context_helper"],
            "group_tool_policy": {
                "rules": [
                    {
                        "tool_id": "zfin_genotype_context_helper",
                        "allowed_group_ids": ["ZFIN"],
                        "field_paths": ["expression_experiment.specimen_genomic_model"],
                    },
                    {
                        "tool_id": "restricted_context_helper",
                        "allowed_group_ids": ["RGD"],
                        "field_paths": ["annotation.subject"],
                    },
                ]
            },
        },
        package_id="agr.alliance",
    )

    assert agent.group_tool_policy.to_dict() == {
        "rules": [
            {
                "tool_id": "zfin_genotype_context_helper",
                "allowed_group_ids": ["ZFIN"],
                "field_paths": ["expression_experiment.specimen_genomic_model"],
            },
            {
                "tool_id": "restricted_context_helper",
                "allowed_group_ids": ["RGD"],
                "field_paths": ["annotation.subject"],
            },
        ]
    }


def test_alliance_gene_expression_does_not_implicitly_expose_broad_database_tool():
    agents = load_agent_definitions(
        REPO_ROOT / "packages" / "alliance" / "agents",
        force_reload=True,
    )
    gene_expression = agents["gene_expression_extraction"]

    assert "agr_curation_query" not in gene_expression.tools
    assert all(
        rule.tool_id != "agr_curation_query"
        for rule in gene_expression.group_tool_policy.rules
    )


def test_rgd_gene_product_resolver_uses_authenticated_group_tool_policy():
    agents = load_agent_definitions(
        REPO_ROOT / "packages" / "alliance" / "agents",
        force_reload=True,
    )
    go_annotations = agents["go_annotations_lookup"]

    assert "resolve_gene_product" not in go_annotations.tools
    assert [rule.to_dict() for rule in go_annotations.group_tool_policy.rules] == [
        {
            "tool_id": "resolve_gene_product",
            "allowed_group_ids": ["RGD"],
            "field_paths": ["gene_id"],
        }
    ]


def test_rgd_go_paper_curator_scopes_identity_and_annotation_sources():
    agents = load_agent_definitions(
        REPO_ROOT / "packages" / "alliance" / "agents",
        force_reload=True,
    )
    curator = agents["rgd_go_paper_curator"]

    assert curator.access.allowed_group_ids == ["RGD"]
    assert "resolve_gene_product" not in curator.tools
    assert "go_api_call" not in curator.tools
    assert [rule.to_dict() for rule in curator.group_tool_policy.rules] == [
        {
            "tool_id": "resolve_gene_product",
            "allowed_group_ids": ["RGD"],
            "field_paths": ["gene_product"],
        },
        {
            "tool_id": "go_api_call",
            "allowed_group_ids": ["RGD"],
            "field_paths": ["provider_context.existing_annotation_context"],
        },
    ]
