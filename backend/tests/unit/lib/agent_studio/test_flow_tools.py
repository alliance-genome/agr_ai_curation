"""Unit tests for Agent Studio flow tools."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

import src.lib.agent_studio.catalog_service as catalog_service
import src.lib.agent_studio.flow_tools as flow_tools
from src.lib.agent_access import is_resource_access_allowed
from src.lib.agent_studio.models import FlowContextDefinition
from src.lib.flow_edge_roles import SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS
from src.lib.packages.flow_recipes import (
    FlowRecipeCatalog,
    FlowRecipeLoadError,
    FlowRecipeManifest,
    LoadedFlowRecipeManifest,
)


@pytest.fixture(autouse=True)
def _isolate_flow_tool_state(monkeypatch):
    for environment_name in (
        "FLOW_DEFINITION_MAX_NODES",
        "AGENT_STUDIO_FLOW_MAX_STEPS",
        "AGENT_STUDIO_FLOW_NAME_MAX_CHARS",
        "AGENT_STUDIO_FLOW_DESCRIPTION_MAX_CHARS",
        "AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS",
        "AGENT_STUDIO_FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS",
        "AGENT_STUDIO_FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS",
    ):
        monkeypatch.delenv(environment_name, raising=False)
    flow_tools.clear_workflow_user_context()
    flow_tools.clear_current_flow_context()

    def _available_agents(*, db_user_id=None, authenticated_groups=None):
        assert db_user_id is not None
        return [
            {"agent_id": agent_id}
            for agent_id, entry in flow_tools.AGENT_REGISTRY.items()
            if is_resource_access_allowed(
                visibility_allowed=True,
                allowed_group_ids=list(entry.get("allowed_group_ids") or []),
                active_group_ids=list(authenticated_groups or []),
            )
        ]

    monkeypatch.setattr(catalog_service, "list_available_agents", _available_agents)
    # Flow-tool tests run with explicit server-derived user/group context unless
    # an authentication or denial case overrides it.
    flow_tools.set_workflow_user_context(42, active_group_ids=["RGD"])
    yield
    flow_tools.clear_workflow_user_context()
    flow_tools.clear_current_flow_context()


def test_workflow_user_context_set_get_clear():
    flow_tools.clear_workflow_user_context()
    assert flow_tools.get_current_user_id() is None
    assert flow_tools.get_current_user_email() is None

    flow_tools.set_workflow_user_context(42, "curator@example.org")
    assert flow_tools.get_current_user_id() == 42
    assert flow_tools.get_current_user_email() == "curator@example.org"

    flow_tools.clear_workflow_user_context()
    assert flow_tools.get_current_user_id() is None
    assert flow_tools.get_current_user_email() is None


def test_flow_context_set_get_clear():
    assert flow_tools.get_current_flow_context() is None
    flow_tools.set_current_flow_context({"flow_name": "My Flow", "nodes": []})
    assert flow_tools.get_current_flow_context()["flow_name"] == "My Flow"


def test_flow_context_definition_is_v1_1_only():
    assert FlowContextDefinition().version == "1.1"

    with pytest.raises(ValidationError, match="1.1"):
        FlowContextDefinition(version="1.0")
    flow_tools.clear_current_flow_context()
    assert flow_tools.get_current_flow_context() is None


def test_get_flow_agent_ids_excludes_supervisor_task_input_and_attachment_only_validators(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "supervisor": {},
            "task_input": {},
            "pdf_extraction": {"category": "Extraction"},
            "chat_output": {"category": "Output"},
            "allele_validation": {
                "category": "Validation",
                "supervisor": {"enabled": False},
            },
            "ontology_term_validation": {
                "category": "Validation",
                "supervisor": {"enabled": True},
            },
        },
    )
    assert flow_tools._get_flow_agent_ids() == [
        "chat_output",
        "ontology_term_validation",
        "pdf_extraction",
    ]


def test_validate_flow_handler_reports_errors_warnings_and_suggestions(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "gene_expression", "chat_output", "gene_validation"],
    )
    validate = flow_tools._validate_flow_handler()

    result = validate(
        steps=[
            {"agent_id": "pdf_extraction"},
            {"agent_id": "pdf_extraction"},  # duplicate -> warning
            {"agent_id": "gene_expression", "custom_instructions": "x" * 2001},
            {"agent_id": "unknown"},
            {"agent_id": "chat_output", "step_goal": "y" * 501},
        ],
        name=" " * 2,
    )

    assert result["valid"] is False
    assert any("unknown agent_id 'unknown'" in e for e in result["errors"])
    assert any("custom_instructions exceeds 2000" in e for e in result["errors"])
    assert any("step_goal exceeds 500" in e for e in result["errors"])
    assert any("Flow name cannot be empty" in e for e in result["errors"])
    assert any("used multiple times" in w for w in result["warnings"])
    assert any("Consider adding 'gene_validation' step" in s for s in result["suggestions"])


def test_validate_flow_handler_suggests_pdf_and_output(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene_validation", "disease_validation", "pdf_extraction", "chat_output"],
    )
    validate = flow_tools._validate_flow_handler()
    result = validate(
        steps=[{"agent_id": "gene_validation"}, {"agent_id": "disease_validation"}],
        name="Flow Name",
    )

    assert result["valid"] is True
    assert any("Consider adding 'pdf_extraction'" in s for s in result["suggestions"])
    output_suggestion = next(
        suggestion
        for suggestion in result["suggestions"]
        if "Consider attaching 'chat_output'" in suggestion
    )
    assert (
        "via ordered source_steps to one or more earlier Extraction or typed "
        "Validation steps"
    ) in output_suggestion


def test_validate_flow_handler_only_mentions_installed_agent_ids(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene_expression_extraction", "gene_validation"],
    )
    validate = flow_tools._validate_flow_handler()

    result = validate(
        steps=[{"agent_id": "gene_expression_extraction"}],
        name="Expression Flow",
    )

    assert result["valid"] is True
    assert result["suggestions"] == [
        "Consider adding 'gene_validation' step after 'gene_expression_extraction' to validate gene identifiers"
    ]
    assert not any("pdf_extraction" in suggestion for suggestion in result["suggestions"])
    assert not any("chat_output" in suggestion for suggestion in result["suggestions"])


def test_validate_flow_handler_accepts_gene_expression_alias_pair(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene_expression", "gene_expression_extraction", "gene_validation"],
    )
    validate = flow_tools._validate_flow_handler()

    flow_alias_result = validate(
        steps=[{"agent_id": "gene_expression"}],
        name="Expression Flow",
    )
    package_agent_result = validate(
        steps=[{"agent_id": "gene_expression_extraction"}],
        name="Expression Flow",
    )

    assert flow_alias_result["valid"] is True
    assert package_agent_result["valid"] is True
    assert flow_alias_result["errors"] == []
    assert package_agent_result["errors"] == []
    assert any(
        "Consider adding 'gene_validation' step after 'gene_expression'" in suggestion
        for suggestion in flow_alias_result["suggestions"]
    )
    assert any(
        "Consider adding 'gene_validation' step after 'gene_expression'" in suggestion
        for suggestion in package_agent_result["suggestions"]
    )


def test_validate_flow_handler_accepts_ordered_extraction_and_typed_validator_sources(
    monkeypatch,
):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "gene_validation", "chat_output"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "gene_validation": {
                "category": "Validation",
                "output_schema_key": "GeneResultEnvelope",
            },
            "chat_output": {"category": "Output"},
        },
    )

    result = flow_tools._validate_flow_handler()(
        steps=[
            {"agent_id": "pdf_extraction"},
            {"agent_id": "gene_validation"},
            {"agent_id": "chat_output", "source_steps": [1, 2]},
        ],
        name="Grouped Output",
    )

    assert result["valid"] is True
    assert result["errors"] == []


def test_validate_flow_handler_rejects_removed_singular_source_step(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "chat_output"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "chat_output": {"category": "Output"},
        },
    )

    result = flow_tools._validate_flow_handler()(
        steps=[
            {"agent_id": "pdf_extraction"},
            {"agent_id": "chat_output", "source_step": 1},
        ],
    )

    assert result["valid"] is False
    assert result["errors"] == [
        "Step 2: output formatter requires non-empty source_steps"
    ]
    assert result["help"].startswith("Bind formatter source_steps")


def test_validate_flow_handler_checks_every_grouped_source_with_shared_policy(
    monkeypatch,
):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "untyped_validator", "chat_output"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "untyped_validator": {"category": "Validation"},
            "chat_output": {"category": "Output"},
        },
    )

    result = flow_tools._validate_flow_handler()(
        steps=[
            {"agent_id": "pdf_extraction"},
            {"agent_id": "untyped_validator"},
            {"agent_id": "chat_output", "source_steps": [1, 2]},
        ],
    )

    assert result["valid"] is False
    assert result["errors"] == [
        "Step 3: source_steps entry 2 ('untyped_validator') is not an extraction "
        "agent or a typed validation agent"
    ]


def test_validate_flow_uses_canonical_output_filename_template_validation(
    monkeypatch,
):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "csv_formatter"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "csv_formatter": {"category": "Output"},
        },
    )
    validate = flow_tools._validate_flow_handler()

    valid = validate(
        steps=[
            {"agent_id": "pdf_extraction"},
            {
                "agent_id": "csv_formatter",
                "source_steps": [1],
                "output_filename_template": "{{input_filename_stem}}.csv",
            },
        ]
    )
    invalid = validate(
        steps=[
            {"agent_id": "pdf_extraction"},
            {
                "agent_id": "csv_formatter",
                "source_steps": [1],
                "output_filename_template": "{{unsupported_variable}}.csv",
            },
        ]
    )

    assert valid["valid"] is True
    assert invalid["valid"] is False
    assert any("unsupported_variable" in error for error in invalid["errors"])


@pytest.mark.parametrize(
    "unsupported_variable",
    ["agent_id", "source_steps", "exceeds"],
)
def test_validate_and_create_share_pre_persistence_rejection(
    monkeypatch,
    unsupported_variable,
):
    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 7)
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "csv_formatter"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "csv_formatter": {"category": "Output"},
        },
    )

    import src.models.sql as sql_module

    def _unexpected_db_access():
        raise AssertionError("invalid preflight must not access the database")

    monkeypatch.setattr(sql_module, "get_db", _unexpected_db_access)
    steps = [
        {"agent_id": "pdf_extraction"},
        {
            "agent_id": "csv_formatter",
            "source_steps": [1],
            "output_filename_template": (
                "{{" + unsupported_variable + "}}.csv"
            ),
        },
    ]

    validation = flow_tools._validate_flow_handler()(steps=steps)
    creation = flow_tools._create_flow_handler()(
        name="Template flow",
        description="Validate the filename template before persistence",
        steps=steps,
    )

    assert validation["valid"] is False
    assert "supported filename variables" in validation["help"]
    assert creation["success"] is False
    assert creation["error"] == validation["errors"][0]
    assert "supported filename variables" in creation["help"]
    assert "Valid agent IDs" not in creation["help"]


def test_validate_and_create_share_configured_step_goal_limit(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS", "4")
    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 7)
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction"])
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {"pdf_extraction": {"category": "Extraction"}},
    )
    steps = [{"agent_id": "pdf_extraction", "step_goal": "12345"}]

    validation = flow_tools._validate_flow_handler()(steps=steps)
    creation = flow_tools._create_flow_handler()(
        name="Short goal flow",
        description="Exercise the configured admission limit",
        steps=steps,
    )

    assert validation["errors"] == [
        "Step 1: step_goal exceeds 4 characters"
    ]
    assert creation["error"] == validation["errors"][0]
    assert creation["help"] == (
        "Shorten the named field to the configured maximum"
    )


def test_overlong_filename_template_returns_length_help(monkeypatch):
    monkeypatch.setenv(
        "AGENT_STUDIO_FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS",
        "4",
    )
    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 7)
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "csv_formatter"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "csv_formatter": {"category": "Output"},
        },
    )
    steps = [
        {"agent_id": "pdf_extraction"},
        {
            "agent_id": "csv_formatter",
            "source_steps": [1],
            "output_filename_template": "a.csv",
        },
    ]

    validation = flow_tools._validate_flow_handler()(steps=steps)
    creation = flow_tools._create_flow_handler()(
        name="Filename length",
        description="Exercise filename length recovery",
        steps=steps,
    )

    assert validation["help"] == (
        "Shorten the named field to the configured maximum"
    )
    assert creation["help"] == validation["help"]


def test_limit_clamp_warnings_are_not_repeated_per_step(monkeypatch, caplog):
    monkeypatch.setenv(
        "AGENT_STUDIO_FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS",
        "2001",
    )
    monkeypatch.setenv("AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS", "501")
    monkeypatch.setenv(
        "AGENT_STUDIO_FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS",
        "256",
    )
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction"])
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {"pdf_extraction": {"category": "Extraction"}},
    )

    result = flow_tools._validate_flow_handler()(
        steps=[{"agent_id": "pdf_extraction"}] * 3
    )

    assert result["valid"] is True
    for environment_name in (
        "AGENT_STUDIO_FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS",
        "AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS",
        "AGENT_STUDIO_FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS",
    ):
        assert sum(environment_name in message for message in caplog.messages) == 1


def test_validate_collects_field_limits_before_unknown_agent_id(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS", "4")
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction"])

    result = flow_tools._validate_flow_handler()(
        steps=[{"agent_id": "not_available", "step_goal": "12345"}]
    )

    assert result["errors"] == [
        "Step 1: step_goal exceeds 4 characters",
        "Step 1: unknown agent_id 'not_available'",
    ]


@pytest.mark.parametrize("malformed_steps", [None, {"agent_id": "pdf_extraction"}])
def test_validate_and_create_structurally_reject_non_array_steps(
    monkeypatch,
    malformed_steps,
):
    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 7)
    invalid_steps = cast(Any, malformed_steps)

    validation = flow_tools._validate_flow_handler()(steps=invalid_steps)
    creation = flow_tools._create_flow_handler()(
        name="Malformed steps",
        description="Reject a non-array step payload",
        steps=invalid_steps,
    )

    assert validation == {
        "valid": False,
        "errors": ["Flow steps must be an array"],
        "warnings": [],
        "suggestions": [],
        "step_count": 0,
        "unique_agents": [],
        "help": "Provide a non-empty steps array within the configured step limit",
    }
    assert creation["success"] is False
    assert creation["error"] == validation["errors"][0]


def test_effective_step_limit_fits_required_task_input_node(monkeypatch):
    monkeypatch.delenv("FLOW_DEFINITION_MAX_NODES", raising=False)
    monkeypatch.setenv("AGENT_STUDIO_FLOW_MAX_STEPS", "100")
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction"])
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {"pdf_extraction": {"category": "Extraction"}},
    )
    validate = flow_tools._validate_flow_handler()

    for authored_step_count in (29, 30):
        result = validate(
            steps=[{"agent_id": "pdf_extraction"}] * authored_step_count
        )
        assert result["valid"] is True, result

    too_many = validate(steps=[{"agent_id": "pdf_extraction"}] * 31)
    assert too_many["valid"] is False
    assert too_many["errors"] == ["Flow has 31 steps; maximum is 30"]

    assert flow_tools._simplified_flow_steps_schema()["maxItems"] == 30
    assert (
        flow_tools._simplified_flow_steps_schema()["items"]["properties"]
        ["source_steps"]["maxItems"]
        == 29
    )


def test_get_flow_templates_handler_uses_registry(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "gene_validation"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {
                "name": "PDF Specialist",
                "description": "Extract entities",
                "category": "Extraction",
                "requires_document": True,
            },
            "gene_validation": {
                "name": "Gene Specialist",
                "description": "Validate genes",
                "category": "Validation",
                "requires_document": False,
            },
        },
    )
    handler = flow_tools._get_flow_templates_handler()
    result = handler()

    assert len(result["templates"]) >= 1
    assert len(result["available_agents"]) == 2
    assert result["available_agents"][0]["agent_id"] in {
        "pdf_extraction",
        "gene_validation",
    }
    assert "Found" in result["message"]


def test_search_flow_agents_hides_attachment_only_validators(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {
                "name": "PDF Specialist",
                "description": "Extract entities",
                "category": "Extraction",
                "requires_document": True,
            },
            "allele_validation": {
                "name": "Allele Validation",
                "description": "Validate alleles",
                "category": "Validation",
                "requires_document": False,
                "supervisor": {"enabled": False},
            },
            "ontology_term_validation": {
                "name": "Ontology Term Validation",
                "description": "Validate ontology terms",
                "category": "Validation",
                "requires_document": False,
                "supervisor": {"enabled": True},
            },
        },
    )

    result = flow_tools._get_available_agents_handler()(category="Validation")

    assert result["validation_agents"] == ["ontology_term_validation"]
    assert "allele_validation" not in {
        agent["agent_id"]
        for agents in result["categories"].values()
        for agent in agents
    }


def test_get_flow_templates_handler_filters_missing_steps_and_resolves_installed_aliases(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "gene_validation", "gene_ontology_lookup"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {
                "name": "PDF Specialist",
                "description": "Extract entities",
                "category": "Extraction",
                "requires_document": True,
            },
            "gene_validation": {
                "name": "Gene Specialist",
                "description": "Validate genes",
                "category": "Validation",
                "requires_document": False,
            },
            "gene_ontology_lookup": {
                "name": "GO Specialist",
                "description": "Validate GO terms",
                "category": "Validation",
                "requires_document": False,
            },
        },
    )

    handler = flow_tools._get_flow_templates_handler()
    result = handler()

    assert {template["name"] for template in result["templates"]} == {
        "Gene Curation",
        "GO Annotation Pipeline",
    }
    assert result["templates"][0]["steps"][0]["agent_id"] == "pdf_extraction"
    assert all(
        step["agent_id"] not in {"chat_output", "gene", "gene_ontology"}
        for template in result["templates"]
        for step in template["steps"]
    )
    assert "compatible templates" in result["message"]


def test_flow_templates_bind_outputs_to_canonical_validator_sources(monkeypatch):
    installed_agent_ids = {
        "pdf_extraction",
        "gene_validation",
        "disease_validation",
        "allele_validation",
        "gene_ontology_lookup",
        "chat_output",
    }
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", sorted(installed_agent_ids))
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "gene_validation": {
                "category": "Validation",
                "output_schema_key": "GeneResultEnvelope",
            },
            "disease_validation": {
                "category": "Validation",
                "output_schema_key": "DiseaseValidationResult",
            },
            "allele_validation": {
                "category": "Validation",
                "output_schema_key": "AlleleResultEnvelope",
            },
            "gene_ontology_lookup": {
                "category": "Validation",
                "output_schema_key": "GOTermResultEnvelope",
            },
            "chat_output": {"category": "Output"},
        },
    )
    templates = {
        template["name"]: template
        for template in flow_tools._filter_flow_templates(installed_agent_ids)
    }

    assert templates["Gene Curation"]["steps"][-1]["source_steps"] == [2]
    assert templates["Disease Annotation"]["steps"][-1]["source_steps"] == [2]
    assert templates["Allele Annotation"]["steps"][-1]["source_steps"] == [2]
    assert templates["GO Annotation Pipeline"]["steps"][-1]["source_steps"] == [
        2,
        3,
    ]
    validate = flow_tools._validate_flow_handler()
    for name in (
        "Gene Curation",
        "Disease Annotation",
        "Allele Annotation",
        "GO Annotation Pipeline",
    ):
        assert validate(steps=templates[name]["steps"], name=name)["valid"] is True


def test_all_ten_alliance_recipes_appear_when_required_agents_are_flow_eligible(
    monkeypatch,
):
    catalog = flow_tools.load_flow_recipe_catalog()
    available_agent_ids = {
        step.agent_id
        for recipe in catalog.recipes
        for step in recipe.steps
    }
    validation_agent_ids = {
        "gene",
        "allele",
        "disease",
        "chemical",
        "gene_ontology",
    }
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", sorted(available_agent_ids))
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            agent_id: (
                {
                    "category": "Validation",
                    "output_schema_key": f"{agent_id.title()}Result",
                }
                if agent_id in validation_agent_ids
                else {
                    "category": (
                        "Output"
                        if agent_id in SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS
                        else "Extraction"
                    )
                }
            )
            for agent_id in available_agent_ids
        },
    )

    templates = flow_tools._filter_flow_templates(
        available_agent_ids,
        catalog,
        active_group_ids=["RGD"],
    )

    assert [template["name"] for template in templates] == [
        "Gene Curation",
        "Gene Extraction",
        "Disease Annotation",
        "Disease Extraction",
        "Chemical Entity Extraction",
        "Gene Expression Analysis",
        "Phenotype Extraction",
        "Allele/Variant Extraction",
        "Allele Annotation",
        "GO Annotation Pipeline",
    ]


def test_advertised_alliance_recipes_pass_the_public_validation_contract():
    templates = flow_tools._get_flow_templates_handler()()["templates"]
    validate = flow_tools._validate_flow_handler()

    assert templates
    for template in templates:
        result = validate(steps=template["steps"], name=template["name"])
        assert result["valid"] is True, {
            "recipe": template["name"],
            "errors": result["errors"],
        }


def test_flow_templates_do_not_advertise_rejected_output_bindings(monkeypatch):
    installed_agent_ids = {
        "pdf_extraction",
        "gene_validation",
        "chat_output",
    }
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", sorted(installed_agent_ids))
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "gene_validation": {"category": "Validation"},
            "chat_output": {"category": "Output"},
        },
    )

    templates = flow_tools._filter_flow_templates(installed_agent_ids)

    assert templates == []


def test_flow_templates_renumber_sources_after_optional_formatter_is_removed(
    monkeypatch,
    tmp_path,
):
    available_agent_ids = {
        "pdf_extraction",
        "record_extractor",
        "secondary_extractor",
        "chat_output",
    }
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", sorted(available_agent_ids))
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "record_extractor": {"category": "Extraction"},
            "secondary_extractor": {"category": "Extraction"},
            "chat_output": {"category": "Output"},
        },
    )
    catalog = FlowRecipeCatalog(
        contributions=(
            LoadedFlowRecipeManifest(
                package_id="org.custom",
                export_name="default",
                source_path=tmp_path / "flow_recipes.yaml",
                manifest=FlowRecipeManifest.model_validate(
                    {
                        "flow_recipes_api_version": "1.0.0",
                        "recipes": [
                            {
                                "name": "Optional Mid-flow Output",
                                "description": "Exercise canonical source remapping",
                                "access": {"allowed_group_ids": ["RGD"]},
                                "steps": [
                                    {"agent_id": "pdf_extraction"},
                                    {"agent_id": "record_extractor"},
                                    {
                                        "agent_id": "csv_formatter",
                                        "source_steps": [2],
                                    },
                                    {"agent_id": "secondary_extractor"},
                                    {
                                        "agent_id": "chat_output",
                                        "source_steps": [4],
                                    },
                                ],
                            }
                        ],
                    }
                ),
            ),
        )
    )

    templates = flow_tools._filter_flow_templates(
        available_agent_ids,
        catalog,
        active_group_ids=["RGD"],
    )

    assert templates[0]["allowed_group_ids"] == ["RGD"]
    assert templates[0]["steps"] == [
        {"agent_id": "pdf_extraction"},
        {"agent_id": "record_extractor"},
        {"agent_id": "secondary_extractor"},
        {"agent_id": "chat_output", "source_steps": [3]},
    ]

    assert flow_tools._filter_flow_templates(
        available_agent_ids,
        catalog,
        active_group_ids=["MGI"],
    ) == []


def test_flow_template_shared_validation_failure_reports_package_recipe_context(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "chat_output"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {"category": "Extraction"},
            "chat_output": {"category": "Output"},
        },
    )
    source_path = tmp_path / "flow_recipes.yaml"
    catalog = FlowRecipeCatalog(
        contributions=(
            LoadedFlowRecipeManifest(
                package_id="org.invalid",
                export_name="default",
                source_path=source_path,
                manifest=FlowRecipeManifest.model_validate(
                    {
                        "flow_recipes_api_version": "1.0.0",
                        "recipes": [
                            {
                                "name": "Broken Output",
                                "description": "Invalid ordered source binding",
                                "steps": [
                                    {"agent_id": "pdf_extraction"},
                                    {
                                        "agent_id": "chat_output",
                                        "source_steps": [2],
                                    },
                                ],
                            }
                        ],
                    }
                ),
            ),
        )
    )

    with pytest.raises(FlowRecipeLoadError) as exc_info:
        flow_tools._filter_flow_templates(
            {"pdf_extraction", "chat_output"},
            catalog,
        )

    message = str(exc_info.value)
    assert "Broken Output" in message
    assert "org.invalid" in message
    assert str(source_path) in message
    assert "source_steps must reference earlier steps" in message




def test_get_flow_templates_handler_reports_core_only_install(monkeypatch):
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", [])
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "supervisor": {"name": "Supervisor", "category": "Routing"},
            "task_input": {"name": "Initial Instructions", "category": "Input"},
        },
    )

    handler = flow_tools._get_flow_templates_handler()
    result = handler()

    assert result["templates"] == []
    assert result["available_agents"] == []
    assert "No flow-capable agents are currently installed" in result["message"]


def test_get_available_agents_handler_groups_categories(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "gene", "chat_output"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "supervisor": {"category": "Routing"},
            "task_input": {"category": "Input"},
            "pdf_extraction": {"name": "PDF", "description": "Extract", "category": "Extraction", "requires_document": True},
            "gene": {
                "name": "Gene",
                "description": "Validate",
                "category": "Validation",
                "requires_document": False,
                "supervisor": {"enabled": True},
            },
            "chat_output": {
                "name": "Chat Output",
                "description": "Render",
                "category": "Output",
                "requires_document": False,
            },
        },
    )
    handler = flow_tools._get_available_agents_handler()
    result = handler()

    assert result["total_agents"] == 3
    assert "Extraction" in result["categories"]
    assert "Validation" in result["categories"]
    assert "Output" in result["categories"]
    assert "chat_output" in result["output_agents"]
    assert "pdf_extraction" in result["extraction_agents"]
    assert "gene" in result["validation_agents"]


def test_get_available_agents_filters_restricted_agents_by_authenticated_groups(monkeypatch):
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["open", "rgd_only"])
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "open": {"name": "Open", "category": "Extraction"},
            "rgd_only": {
                "name": "RGD only",
                "category": "Extraction",
                "allowed_group_ids": ["RGD"],
            },
        },
    )
    flow_tools._current_active_group_ids.set(("MGI",))
    denied = flow_tools._get_available_agents_handler()()
    assert denied["total_agents"] == 1
    assert denied["extraction_agents"] == ["open"]

    flow_tools._current_active_group_ids.set(("WB", "RGD"))
    allowed = flow_tools._get_available_agents_handler()()
    assert set(allowed["extraction_agents"]) == {"open", "rgd_only"}


def test_get_available_agents_handler_reports_core_only_install(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "supervisor": {"category": "Routing"},
            "task_input": {"category": "Input"},
        },
    )

    handler = flow_tools._get_available_agents_handler()
    result = handler()

    assert result["total_agents"] == 0
    assert result["output_agents"] == []
    assert result["extraction_agents"] == []
    assert result["validation_agents"] == []
    assert "No flow-capable agents are currently installed" in result["message"]


def test_get_current_flow_handler_no_context_and_empty_flow():
    handler = flow_tools._get_current_flow_handler()

    no_context = handler()
    assert no_context["success"] is False
    assert "No flow is currently being edited" in no_context["error"]

    flow_tools.set_current_flow_context({"flow_name": "Untitled", "nodes": [], "edges": []})
    empty = handler()
    assert empty["success"] is True
    assert empty["step_count"] == 0
    assert empty["steps"] == []


def test_get_current_flow_handler_detects_parallel_and_disconnected_nodes():
    handler = flow_tools._get_current_flow_handler()
    flow_tools.set_current_flow_context(
        {
            "flow_name": "Branchy Flow",
            "entry_node_id": "task_input_0",
            "nodes": [
                {
                    "id": "task_input_0",
                    "type": "task_input",
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "",
                        "output_key": "task_input",
                    },
                },
                {
                    "id": "step_1",
                    "type": "agent",
                    "data": {"agent_id": "pdf_extraction", "agent_display_name": "PDF", "output_key": "step_1_output"},
                },
                {
                    "id": "step_2",
                    "type": "agent",
                    "data": {"agent_id": "gene", "agent_display_name": "Gene", "output_key": "step_2_output"},
                },
                {
                    "id": "step_3",
                    "type": "agent",
                    "data": {"agent_id": "chat_output", "agent_display_name": "Output", "output_key": "out"},
                },
            ],
            "edges": [
                {"source": "task_input_0", "target": "step_1"},
                {"source": "task_input_0", "target": "step_2"},  # parallel branch
            ],
        }
    )

    result = handler()

    assert result["success"] is True
    # Invalid branches do not get flattened into a pretend sequential order.
    assert result["step_count"] == 0
    assert result["disconnected_count"] == 3
    assert result["has_critical_issues"] is True
    assert result["critical_issue_count"] >= 2  # empty task instructions + parallel branching
    assert any(w["type"] == "CRITICAL" for w in result["validation_warnings"])
    assert {issue["code"] for issue in result["executable_graph"]["issues"]} >= {
        "branch",
        "ambiguous_terminal",
        "disconnected",
    }
    assert "Invalid executable flow topology" in result["execution_order_markdown"]


def test_get_current_flow_handler_ignores_validation_attachment_sidecar_edges():
    handler = flow_tools._get_current_flow_handler()
    flow_tools.set_current_flow_context(
        {
            "flow_name": "Validator Sidecar Flow",
            "version": "1.1",
            "entry_node_id": "task_input_0",
            "nodes": [
                {
                    "id": "task_input_0",
                    "type": "task_input",
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "Extract genes.",
                        "output_key": "task_input",
                    },
                },
                {
                    "id": "extract_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "gene_extractor",
                        "agent_display_name": "Gene Extraction",
                        "output_key": "genes",
                    },
                },
                {
                    "id": "custom_validator_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "custom_gene_validator",
                        "agent_display_name": "Custom Gene Validator",
                        "output_key": "gene_validation",
                    },
                },
                {
                    "id": "output_1",
                    "type": "output",
                    "data": {
                        "agent_id": "chat_output",
                        "agent_display_name": "Output",
                        "output_key": "out",
                    },
                },
            ],
            "edges": [
                {"source": "task_input_0", "target": "extract_1"},
                {
                    "source": "extract_1",
                    "target": "output_1",
                    "role": "output_attachment",
                },
                {
                    "source": "extract_1",
                    "target": "custom_validator_1",
                    "role": "validation_attachment",
                    "satisfies_binding_id": "alliance.gene.identity",
                },
            ],
        }
    )

    result = handler()

    assert result["success"] is True
    assert result["step_count"] == 2
    assert result["disconnected_count"] == 0
    assert result["has_critical_issues"] is False
    assert [step["node_id"] for step in result["steps"]] == [
        "extract_1",
        "output_1",
    ]
    assert "Parallel flows not yet supported" not in result["execution_order_markdown"]
    assert not any(
        warning["node_id"] == "custom_validator_1"
        for warning in result["validation_warnings"]
    )


def test_get_current_flow_handler_explains_output_attachment_binding():
    handler = flow_tools._get_current_flow_handler()
    flow_tools.set_current_flow_context(
        {
            "flow_name": "Multiple Export Flow",
            "version": "1.1",
            "entry_node_id": "task_input_0",
            "nodes": [
                {
                    "id": "task_input_0",
                    "type": "task_input",
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "Extract alleles and genes.",
                        "output_key": "task_input",
                    },
                },
                {
                    "id": "allele_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "allele_extractor",
                        "agent_display_name": "Allele Extraction",
                        "output_key": "alleles",
                    },
                },
                {
                    "id": "allele_csv",
                    "type": "output",
                    "data": {
                        "agent_id": "csv_formatter",
                        "agent_display_name": "Allele CSV",
                        "output_key": "allele_csv",
                    },
                },
                {
                    "id": "gene_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "gene_extractor",
                        "agent_display_name": "Gene Extraction",
                        "output_key": "genes",
                    },
                },
            ],
            "edges": [
                {
                    "id": "control_1",
                    "source": "task_input_0",
                    "target": "allele_1",
                    "role": "control_flow",
                },
                {
                    "id": "output_1",
                    "source": "allele_1",
                    "target": "allele_csv",
                    "role": "output_attachment",
                },
                {
                    "id": "control_2",
                    "source": "allele_1",
                    "target": "gene_1",
                    "role": "control_flow",
                },
                {
                    "id": "output_2",
                    "source": "gene_1",
                    "target": "allele_csv",
                    "role": "output_attachment",
                },
            ],
        }
    )

    result = handler()

    assert result["success"] is True
    assert [step["node_id"] for step in result["steps"]] == [
        "allele_1",
        "gene_1",
        "allele_csv",
    ]
    formatter_step = result["steps"][2]
    assert "source_node_id" not in formatter_step["output_attachment"]
    assert "edge_id" not in formatter_step["output_attachment"]
    assert [
        source["source_node_id"]
        for source in formatter_step["output_attachment"]["sources"]
    ] == ["allele_1", "gene_1"]
    assert [
        source["source_agent_id"]
        for source in formatter_step["output_attachment"]["sources"]
    ] == ["allele_extractor", "gene_extractor"]
    assert result["edges"][1]["role"] == "output_attachment"
    assert result["has_critical_issues"] is False
    assert not any(
        issue["code"] == "entry_mismatch"
        for issue in result["executable_graph"]["issues"]
    )
    markdown = result["execution_order_markdown"]
    assert "Formatter output branches" in markdown
    assert "Multiple formatter branches create multiple independent" in markdown
    assert "Projects the ordered results from Allele Extraction" in markdown
    assert "Gene Extraction (`gene_1`)" in markdown
    assert "ordered results of Allele Extraction, Gene Extraction" in markdown


def test_get_current_flow_handler_flags_attachment_only_validator_step(monkeypatch):
    handler = flow_tools._get_current_flow_handler()
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "allele_validation": {
                "name": "Allele Validation",
                "category": "Validation",
                "supervisor": {"enabled": False},
            }
        },
    )
    flow_tools.set_current_flow_context(
        {
            "flow_name": "Standalone Validator Flow",
            "entry_node_id": "task_input_0",
            "nodes": [
                {
                    "id": "task_input_0",
                    "type": "task_input",
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "Validate alleles.",
                        "output_key": "task_input",
                    },
                },
                {
                    "id": "validator_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "allele_validation",
                        "agent_display_name": "Allele Validation",
                        "output_key": "allele_validation_output",
                    },
                },
            ],
            "edges": [
                {"id": "e1", "source": "task_input_0", "target": "validator_1"},
            ],
        }
    )

    result = handler()

    assert result["success"] is True
    assert result["has_critical_issues"] is True
    assert result["steps"][0]["flow_step_policy_warning"].startswith(
        "Allele Validation is an attachment-only validator"
    )
    assert any(
        warning["node_id"] == "validator_1"
        and "attachment-only validator" in warning["message"]
        for warning in result["validation_warnings"]
    )


def test_get_current_flow_handler_only_adds_preview_ellipsis_when_truncated():
    handler = flow_tools._get_current_flow_handler()
    flow_tools.set_current_flow_context(
        {
            "flow_name": "Preview Flow",
            "entry_node_id": "task_input_0",
            "nodes": [
                {
                    "id": "task_input_0",
                    "type": "task_input",
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "Read the paper.",
                        "output_key": "task_input",
                    },
                },
                {
                    "id": "formatter_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "chat_output_formatter",
                        "agent_display_name": "Formatter",
                        "output_filename_template": "{{input_filename_stem}}.tsv",
                        "output_key": "formatted_output",
                    },
                },
            ],
            "edges": [
                {"source": "task_input_0", "target": "formatter_1"},
            ],
        }
    )

    result = handler()
    markdown = result["execution_order_markdown"]

    assert result["success"] is True
    assert "- **Input:** Flow task and loaded document context" in markdown
    assert "- **Output Filename Template:** {{input_filename_stem}}.tsv" in markdown
    assert (
        "- **Output Filename Template:** {{input_filename_stem}}.tsv..."
        not in markdown
    )


def test_get_current_flow_handler_includes_domain_envelope_analysis(monkeypatch):
    handler = flow_tools._get_current_flow_handler()
    monkeypatch.setattr(
        flow_tools,
        "current_flow_domain_envelope_analysis",
        lambda **_kwargs: {
            "semantic_source": "domain_envelope.extracted_objects",
            "envelope_node_count": 1,
            "nodes": [
                {
                    "node_id": "extract_1",
                    "agent_id": "allele_extractor",
                    "agent_display_name": "Allele Extraction",
                    "domain_pack_id": "alliance_allele",
                    "domain_pack_version": "0.7.0",
                    "object_definitions": [
                        {
                            "object_type": "allele",
                            "display_name": "Allele",
                            "field_paths": ["gene.symbol", "allele.symbol"],
                        }
                    ],
                    "validation_schedule": {
                        "scheduled_validators": [
                            {"validator_binding_id": "allele-symbol-binding"}
                        ],
                        "opt_outs": [
                            {"validator_binding_id": "optional-note-binding"}
                        ],
                        "inactive_metadata": [
                            {"validator_binding_id": "future-ontology-binding"}
                        ],
                        "replacement_validators": [
                            {"validator_binding_id": "allele-custom-binding"}
                        ],
                        "supplemental_validators": [
                            {"validator_binding_id": "allele-supplemental-binding"}
                        ],
                    },
                }
            ],
        },
    )
    flow_tools.set_current_flow_context(
        {
            "flow_name": "Allele Envelope Flow",
            "entry_node_id": "task_input_0",
            "nodes": [
                {
                    "id": "task_input_0",
                    "type": "task_input",
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "Extract alleles.",
                        "output_key": "task_input",
                    },
                },
                {
                    "id": "extract_1",
                    "type": "agent",
                    "data": {
                        "agent_id": "allele_extractor",
                        "agent_display_name": "Allele Extraction",
                        "output_key": "alleles",
                        "validation_attachments": [
                            {
                                "attachment_id": "allele-symbol-binding",
                                "state": "active",
                                "enabled": True,
                            },
                            {
                                "attachment_id": "optional-note-binding",
                                "state": "active",
                                "enabled": False,
                            },
                            {
                                "attachment_id": "source-reference-binding",
                                "state": "under_development",
                                "enabled": False,
                            },
                        ],
                    },
                },
            ],
            "edges": [{"source": "task_input_0", "target": "extract_1"}],
        }
    )

    result = handler()

    assert result["success"] is True
    assert result["domain_envelope_analysis"]["semantic_source"] == "domain_envelope.extracted_objects"
    assert result["domain_envelope_analysis"]["envelope_node_count"] == 1
    assert (
        result["domain_envelope_analysis"]["nodes"][0]["validation_schedule"][
            "scheduled_validators"
        ][0]["validator_binding_id"]
        == "allele-symbol-binding"
    )
    assert "Domain Envelope Metadata" in result["execution_order_markdown"]
    assert (
        "**Validation Attachments:** 1 active scheduled, 1 opted out, "
        "1 under-development metadata"
    ) in result["execution_order_markdown"]
    assert (
        "1 scheduled validators, 1 policy opt-outs, 1 replacement validators, "
        "1 supplemental validators, 1 under-development metadata"
    ) in result["execution_order_markdown"]


def test_create_flow_handler_validation_and_auth_errors(monkeypatch):
    create = flow_tools._create_flow_handler()
    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: None)
    unauth = create("Flow A", "desc", [{"agent_id": "pdf_extraction"}])
    assert unauth["success"] is False
    assert "User not authenticated" in unauth["error"]

    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 7)
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction", "gene"])

    missing_desc = create("Flow A", "   ", [{"agent_id": "pdf_extraction"}])
    assert missing_desc["success"] is False
    assert "description is required" in missing_desc["error"]

    no_steps = create("Flow A", "desc", [])
    assert no_steps["success"] is False
    assert "at least one step" in no_steps["error"]

    unknown_agent = create("Flow A", "desc", [{"agent_id": "nope"}])
    assert unknown_agent["success"] is False
    assert "unknown agent_id" in unknown_agent["error"]
    assert unknown_agent["help"].startswith("Valid agent IDs:")

    monkeypatch.setenv("AGENT_STUDIO_FLOW_NAME_MAX_CHARS", "4")
    long_name = create(
        "Flow A",
        "desc",
        [{"agent_id": "pdf_extraction"}],
    )
    assert "Flow name exceeds 4 characters" in long_name["error"]
    assert long_name["help"] == (
        "Shorten the named field to the configured maximum"
    )


def test_create_flow_handler_success_and_db_errors(monkeypatch):
    class _FakeFlow:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _FakeDB:
        def __init__(self, commit_side_effect=None):
            self._commit_side_effect = commit_side_effect
            self.added = None
            self.closed = False

        def add(self, flow):
            self.added = flow

        def commit(self):
            if self._commit_side_effect:
                raise self._commit_side_effect

        def refresh(self, _flow):
            return None

        def close(self):
            self.closed = True

    def _gen_db(db):
        def _factory():
            yield db

        return _factory

    create = flow_tools._create_flow_handler()

    monkeypatch.setattr(flow_tools, "get_current_user_id", lambda: 123)
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["pdf_extraction", "gene", "csv_formatter"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "pdf_extraction": {
                "name": "PDF Specialist",
                "category": "Extraction",
            },
            "gene": {
                "name": "Gene Specialist",
                "category": "Validation",
                "output_schema_key": "GeneResultEnvelope",
            },
            "csv_formatter": {
                "name": "CSV Formatter",
                "category": "Output",
            },
        },
    )

    import src.models.sql as sql_module

    success_db = _FakeDB()
    monkeypatch.setattr(sql_module, "get_db", _gen_db(success_db))
    monkeypatch.setattr(sql_module, "CurationFlow", _FakeFlow)

    result = create(
        name="Good Flow",
        description="Extract then validate",
        steps=[
            {"agent_id": "pdf_extraction", "step_goal": "extract"},
            {"agent_id": "gene", "step_goal": "validate"},
        ],
    )
    assert result["success"] is True
    assert "flow_id" in result
    assert success_db.closed is True
    assert success_db.added is not None
    assert success_db.added.flow_definition["version"] == "1.1"

    branch_db = _FakeDB()
    monkeypatch.setattr(sql_module, "get_db", _gen_db(branch_db))
    branch_result = create(
        name="Branched Output Flow",
        description="Extract and export while retaining the control chain",
        steps=[
            {"agent_id": "pdf_extraction", "step_goal": "extract"},
            {"agent_id": "gene", "step_goal": "validate"},
            {
                "agent_id": "csv_formatter",
                "source_steps": [1, 2],
                "output_filename_template": (
                    "{{input_filename_stem}}-{{timestamp}}.csv"
                ),
            },
        ],
    )
    assert branch_result["success"] is True, branch_result
    assert branch_db.added is not None
    assert branch_db.added.flow_definition["version"] == "1.1"
    assert [node["type"] for node in branch_db.added.flow_definition["nodes"]] == [
        "task_input",
        "agent",
        "agent",
        "output",
    ]
    assert branch_db.added.flow_definition["edges"][1]["source"] == "step_1"
    assert branch_db.added.flow_definition["edges"][1]["target"] == "step_2"
    assert branch_db.added.flow_definition["edges"][2] == {
        "id": "output_edge_3_1",
        "source": "step_1",
        "target": "step_3",
        "role": "output_attachment",
        "satisfies_binding_id": None,
        "replaces_attachment_id": None,
        "condition": None,
    }
    assert branch_db.added.flow_definition["edges"][3] == {
        "id": "output_edge_3_2",
        "source": "step_2",
        "target": "step_3",
        "role": "output_attachment",
        "satisfies_binding_id": None,
        "replaces_attachment_id": None,
        "condition": None,
    }
    assert branch_db.added.flow_definition["nodes"][3]["data"][
        "output_filename_template"
    ] == "{{input_filename_stem}}-{{timestamp}}.csv"

    dup_db = _FakeDB(commit_side_effect=Exception("uq_user_flow_name_active"))
    monkeypatch.setattr(sql_module, "get_db", _gen_db(dup_db))
    dup = create(
        name="Good Flow",
        description="Extract then validate",
        steps=[{"agent_id": "pdf_extraction"}],
    )
    assert dup["success"] is False
    assert "already exists" in dup["error"]
    assert dup_db.closed is True

    generic_db = _FakeDB(commit_side_effect=Exception("db timeout"))
    monkeypatch.setattr(sql_module, "get_db", _gen_db(generic_db))
    generic = create(
        name="Good Flow",
        description="Extract then validate",
        steps=[{"agent_id": "pdf_extraction"}],
    )
    assert generic["success"] is False
    assert "database error" in generic["error"]


def _multi_agent_registry():
    return {
        "supervisor": {"category": "Routing"},
        "task_input": {"category": "Input"},
        "gene_extractor": {
            "name": "Gene Specialist",
            "description": "Extract gene mentions",
            "category": "Extraction",
            "requires_document": True,
        },
        "gene_validation": {
            "name": "Gene Validator",
            "description": "Validate gene identifiers",
            "category": "Validation",
            "requires_document": False,
            "supervisor": {"enabled": True},
        },
        "disease_extractor": {
            "name": "Disease Specialist",
            "description": "Extract disease mentions",
            "category": "Extraction",
            "requires_document": True,
        },
        "chat_output": {
            "name": "Chat Output",
            "description": "Render results",
            "category": "Output",
            "requires_document": False,
        },
    }


def test_get_available_agents_handler_filters_by_query(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene_extractor", "gene_validation", "disease_extractor", "chat_output"],
    )
    monkeypatch.setattr(flow_tools, "AGENT_REGISTRY", _multi_agent_registry())
    handler = flow_tools._get_available_agents_handler()

    result = handler(query="gene")

    returned_ids = {
        agent["agent_id"]
        for agents in result["categories"].values()
        for agent in agents
    }
    assert returned_ids == {"gene_extractor", "gene_validation"}
    assert result["total_count"] == 2
    assert result["returned_count"] == 2
    assert result["query"] == "gene"
    assert result["truncated"] is False


def test_get_available_agents_handler_filters_by_category(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene_extractor", "gene_validation", "disease_extractor", "chat_output"],
    )
    monkeypatch.setattr(flow_tools, "AGENT_REGISTRY", _multi_agent_registry())
    handler = flow_tools._get_available_agents_handler()

    result = handler(category="Extraction")

    assert set(result["categories"].keys()) == {"Extraction"}
    assert set(result["extraction_agents"]) == {"gene_extractor", "disease_extractor"}
    assert result["total_count"] == 2


def test_get_available_agents_handler_pages_with_cursor(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene_extractor", "gene_validation", "disease_extractor", "chat_output"],
    )
    monkeypatch.setattr(flow_tools, "AGENT_REGISTRY", _multi_agent_registry())
    handler = flow_tools._get_available_agents_handler()

    first = handler(limit=2)
    assert first["returned_count"] == 2
    assert first["total_count"] == 4
    assert first["truncated"] is True
    assert first["next_cursor"] == "2"

    second = handler(limit=2, cursor=first["next_cursor"])
    assert second["returned_count"] == 2
    assert second["truncated"] is False
    assert second["next_cursor"] is None

    first_ids = {a["agent_id"] for ag in first["categories"].values() for a in ag}
    second_ids = {a["agent_id"] for ag in second["categories"].values() for a in ag}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {
        "gene_extractor",
        "gene_validation",
        "disease_extractor",
        "chat_output",
    }


def test_get_flow_templates_handler_filters_by_query(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene_extractor", "gene_validation", "disease_extractor"],
    )
    monkeypatch.setattr(flow_tools, "AGENT_REGISTRY", _multi_agent_registry())
    handler = flow_tools._get_flow_templates_handler()

    result = handler(query="disease")

    assert {agent["agent_id"] for agent in result["available_agents"]} == {
        "disease_extractor"
    }
    assert result["total_count"] == 1
    assert result["query"] == "disease"
    assert result["truncated"] is False


def test_get_flow_templates_handler_pages_available_agents(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene_extractor", "gene_validation", "disease_extractor"],
    )
    monkeypatch.setattr(flow_tools, "AGENT_REGISTRY", _multi_agent_registry())
    handler = flow_tools._get_flow_templates_handler()

    first = handler(limit=2)
    assert first["returned_count"] == 2
    assert first["total_count"] == 3
    assert first["truncated"] is True
    assert first["next_cursor"] == "2"

    second = handler(limit=2, cursor=first["next_cursor"])
    assert second["returned_count"] == 1
    assert second["truncated"] is False
    assert second["next_cursor"] is None


def test_register_flow_tools_registers_five_tools(monkeypatch):
    registrations = []

    class _Registry:
        def register(self, **kwargs):
            registrations.append(kwargs)

    monkeypatch.setattr(flow_tools, "get_diagnostic_tools_registry", lambda: _Registry())
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction", "gene", "chat_output"])
    monkeypatch.delenv("AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS", raising=False)

    flow_tools.register_flow_tools()

    names = [entry["name"] for entry in registrations]
    assert names == [
        "create_flow",
        "validate_flow",
        "get_flow_templates",
        "get_current_flow",
        "get_available_agents",
    ]
    assert all(entry["category"] == "flows" for entry in registrations)
    assert all(callable(entry["handler"]) for entry in registrations)
    create_flow_schema = registrations[0]["input_schema"]
    create_steps_schema = create_flow_schema["properties"]["steps"]
    step_properties = create_steps_schema["items"][
        "properties"
    ]
    assert "source_steps" in step_properties
    assert "source_step" not in step_properties
    assert step_properties["source_steps"]["minItems"] == 1
    assert step_properties["source_steps"]["uniqueItems"] is True
    assert "output_filename_template" in step_properties
    assert step_properties["step_goal"]["maxLength"] == 500
    validate_flow_schema = registrations[1]["input_schema"]
    validate_steps_schema = validate_flow_schema["properties"]["steps"]
    assert validate_steps_schema == create_steps_schema


def test_register_flow_tools_propagates_configured_limits(monkeypatch):
    registrations = []

    class _Registry:
        def register(self, **kwargs):
            registrations.append(kwargs)

    monkeypatch.setattr(flow_tools, "get_diagnostic_tools_registry", lambda: _Registry())
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction"])
    monkeypatch.setenv("AGENT_STUDIO_FLOW_MAX_STEPS", "4")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_NAME_MAX_CHARS", "40")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_DESCRIPTION_MAX_CHARS", "400")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS", "50")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_CUSTOM_INSTRUCTIONS_MAX_CHARS", "300")
    monkeypatch.setenv(
        "AGENT_STUDIO_FLOW_OUTPUT_FILENAME_TEMPLATE_MAX_CHARS",
        "60",
    )

    flow_tools.register_flow_tools()

    create_schema = registrations[0]["input_schema"]
    validate_schema = registrations[1]["input_schema"]
    steps_schema = create_schema["properties"]["steps"]
    step_properties = steps_schema["items"]["properties"]
    assert steps_schema == validate_schema["properties"]["steps"]
    assert steps_schema["maxItems"] == 4
    assert step_properties["source_steps"]["maxItems"] == 3
    assert step_properties["source_steps"]["items"]["maximum"] == 3
    assert step_properties["step_goal"]["maxLength"] == 50
    assert step_properties["custom_instructions"]["maxLength"] == 300
    assert step_properties["output_filename_template"]["maxLength"] == 60
    assert create_schema["properties"]["name"]["maxLength"] == 40
    assert validate_schema["properties"]["name"]["maxLength"] == 40
    assert create_schema["properties"]["description"]["maxLength"] == 400
