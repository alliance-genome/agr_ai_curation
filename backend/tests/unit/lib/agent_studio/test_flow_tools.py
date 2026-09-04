"""Unit tests for Agent Studio flow tools."""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

import src.lib.agent_studio.catalog_service as catalog_service
import src.lib.agent_studio.flow_tools as flow_tools
from src.api import agent_studio as api_module
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
        "AGENT_STUDIO_FLOW_INSPECTION_PAGE_LIMIT",
        "AGENT_STUDIO_FLOW_INSPECTION_CHUNK_MAX_CHARS",
    ):
        monkeypatch.delenv(environment_name, raising=False)
    flow_tools.clear_workflow_user_context()
    flow_tools.clear_current_flow_context()

    def _available_agents(*, db_user_id=None, authenticated_groups=None):
        assert db_user_id is not None
        available = []
        for agent_id in flow_tools.FLOW_AGENT_IDS:
            if agent_id not in flow_tools.AGENT_REGISTRY:
                continue
            entry = flow_tools.AGENT_REGISTRY.get(agent_id, {})
            if not is_resource_access_allowed(
                visibility_allowed=True,
                allowed_group_ids=list(entry.get("allowed_group_ids") or []),
                active_group_ids=list(authenticated_groups or []),
            ):
                continue
            visible_entry = {
                "agent_id": agent_id,
                "display_name": entry.get("name", agent_id),
                **entry,
            }
            if entry.get("category") == "Validation":
                visible_entry["supervisor"] = {"enabled": True}
            available.append(visible_entry)
        return available

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
    context = flow_tools.get_current_flow_context()
    assert context is not None
    assert context["flow_name"] == "My Flow"


def test_flow_context_definition_is_v1_1_only():
    assert FlowContextDefinition().version == "1.1"

    with pytest.raises(ValidationError, match="1.1"):
        FlowContextDefinition(version="1.0")
    flow_tools.clear_current_flow_context()
    assert flow_tools.get_current_flow_context() is None


def test_flow_context_definition_preserves_node_verification_fields():
    definition = FlowContextDefinition(
        nodes=[
            {
                "id": "extract",
                "agent_id": "gene_extractor",
                "agent_display_name": "Gene",
                "position": {"x": 200, "y": 100},
                "output_key": "genes",
                "step_goal": "Extract genes",
                "prompt_version": 9,
                "validation_groups": [
                    {
                        "group_id": "replacement",
                        "state": "replaced",
                        "validator_node_id": "custom-validator",
                    },
                    {
                        "group_id": "supplemental",
                        "state": "supplemental",
                        "validator_node_id": "supplemental-validator",
                    },
                ],
            }
        ]
    )

    node = definition.model_dump()["nodes"][0]
    assert node["step_goal"] == "Extract genes"
    assert node["prompt_version"] == 9
    assert node["position"] == {"x": 200.0, "y": 100.0}
    assert [group["state"] for group in node["validation_groups"]] == [
        "replaced",
        "supplemental",
    ]


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


def _exact_validation_flow() -> dict[str, Any]:
    return {
        "version": "1.1",
        "nodes": [
            {
                "id": "task",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Initial Instructions",
                    "task_instructions": "Extract facts",
                    "output_key": "task_input",
                },
            },
            {
                "id": "extract",
                "type": "agent",
                "position": {"x": 100, "y": 100},
                "data": {
                    "agent_id": "extractor",
                    "agent_display_name": "Extractor",
                    "prompt_version": 2,
                    "custom_instructions": "Preserve evidence.",
                    "output_key": "facts",
                },
            },
        ],
        "edges": [
            {
                "id": "control",
                "source": "task",
                "target": "extract",
                "role": "control_flow",
            }
        ],
        "entry_node_id": "task",
    }


def test_validate_flow_handler_accepts_exact_full_draft(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "resolve_live_flow_agent",
        lambda agent_id, _context: {
            "category": "Extraction",
            "is_active": True,
            "supervisor": {"enabled": True},
            "produces_flow_artifacts": True,
        }
        if agent_id == "extractor"
        else None,
    )
    monkeypatch.setattr(
        flow_tools,
        "apply_flow_validation_attachment_defaults",
        lambda candidate, **_kwargs: candidate,
    )

    result = flow_tools._validate_flow_handler()(
        flow_definition=_exact_validation_flow(),
        name="Exact flow",
        phase="pre_apply",
    )

    assert result == {
        "artifact_kind": "flow",
        "phase": "pre_apply",
        "valid": True,
        "findings": [],
        "node_count": 2,
        "edge_count": 1,
    }


def test_validate_flow_handler_returns_structured_safe_reference_finding(monkeypatch):
    monkeypatch.setattr(flow_tools, "resolve_live_flow_agent", lambda *_args: None)
    monkeypatch.setattr(
        flow_tools,
        "apply_flow_validation_attachment_defaults",
        lambda candidate, **_kwargs: candidate,
    )
    draft = _exact_validation_flow()
    draft["nodes"][1]["data"]["agent_id"] = "private_other_user_agent"

    result = flow_tools._validate_flow_handler()(
        flow_definition=draft,
        name="Exact flow",
    )

    assert result["valid"] is False
    finding = next(item for item in result["findings"] if item["code"] == "unavailable_agent")
    assert finding["path"] == "flow_definition.nodes.extract.data.agent_id"
    assert "private_other_user_agent" not in finding["message"]



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
    for name in (
        "Gene Curation",
        "Disease Annotation",
        "Allele Annotation",
        "GO Annotation Pipeline",
    ):
        definition = flow_tools._build_simplified_flow_definition(
            steps=templates[name]["steps"],
            task_instructions=name,
            flow_agent_ids=sorted(installed_agent_ids),
            agent_registry=flow_tools.AGENT_REGISTRY,
        )
        assert definition.version == "1.1"


def test_all_twelve_alliance_recipes_appear_when_required_agents_are_flow_eligible(
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
        "RGD GO and Disease Paper Review",
        "RGD GO Paper Review",
    ]


def test_rgd_recipe_discovery_compiles_without_persistence_and_denies_non_rgd_access(
    monkeypatch,
):
    available_agent_ids = {
        "rgd_go_paper_curator",
        "disease_extractor",
        "chat_output",
    }
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", sorted(available_agent_ids))
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "rgd_go_paper_curator": {
                "name": "RGD GO Paper Curator",
                "description": "RGD-only GO paper review",
                "category": "Extraction",
                "allowed_group_ids": ["RGD"],
            },
            "disease_extractor": {
                "name": "Disease Extraction Agent",
                "description": "Disease paper review",
                "category": "Extraction",
            },
            "chat_output": {
                "name": "Chat Output Agent",
                "description": "Display review results",
                "category": "Output",
            },
        },
    )

    flow_tools.set_workflow_user_context(42, active_group_ids=["MGI"])
    denied_templates = flow_tools._get_flow_templates_handler()()["templates"]
    assert not any(template["name"].startswith("RGD ") for template in denied_templates)

    flow_tools.set_workflow_user_context(42, active_group_ids=["RGD"])
    templates = {
        template["name"]: template
        for template in flow_tools._get_flow_templates_handler()()["templates"]
    }
    assert set(templates) == {
        "RGD GO and Disease Paper Review",
        "RGD GO Paper Review",
    }
    combined = templates["RGD GO and Disease Paper Review"]

    definition = flow_tools.build_flow_definition_from_recipe(
        steps=combined["steps"],
        task_instructions=combined["description"],
    )
    assert [
        node.data.agent_id
        for node in definition.nodes
        if node.data.agent_id != "task_input"
    ] == ["rgd_go_paper_curator", "disease_extractor", "chat_output"]


def test_advertised_alliance_recipes_pass_the_create_compiler_contract():
    templates = flow_tools._get_flow_templates_handler()()["templates"]

    assert templates
    for template in templates:
        definition = flow_tools.build_flow_definition_from_recipe(
            steps=template["steps"],
            task_instructions=template["name"],
        )
        assert definition.version == "1.1"


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


def test_startup_validation_rejects_invalid_group_restricted_recipe(
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
                                "access": {"allowed_group_ids": ["RGD"]},
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
        flow_tools.validate_installed_flow_recipe_catalog(catalog)

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


def test_flow_catalog_accepts_visible_custom_agent_without_static_enum(monkeypatch):
    custom_id = "ca_1234567890abcdef"
    assert custom_id not in flow_tools.FLOW_AGENT_IDS
    monkeypatch.setattr(
        catalog_service,
        "list_available_agents",
        lambda **_kwargs: [
            {
                "agent_id": custom_id,
                "display_name": "My extraction agent",
                "description": "A saved custom agent",
                "category": "Extraction",
                "requires_document": True,
                "frontend": {"show_in_palette": True},
                "supervisor": {"enabled": False},
            }
        ],
    )

    result = flow_tools._get_available_agents_handler()()
    assert result["extraction_agents"] == [custom_id]
    assert result["categories"]["Extraction"][0]["agent_id"] == custom_id
    assert "enum" not in flow_tools._simplified_flow_steps_schema()["items"][
        "properties"
    ]["agent_id"]


def test_flow_catalog_excludes_hidden_and_runtime_unsupported_custom_output(
    monkeypatch,
):
    monkeypatch.setattr(
        catalog_service,
        "list_available_agents",
        lambda **_kwargs: [
            {
                "agent_id": "ca_hidden",
                "display_name": "Hidden extraction",
                "category": "Extraction",
                "frontend": {"show_in_palette": False},
                "supervisor": {"enabled": False},
            },
            {
                "agent_id": "ca_custom_output",
                "display_name": "Custom output",
                "category": "Output",
                "frontend": {"show_in_palette": True},
                "supervisor": {"enabled": False},
            },
        ],
    )

    result = flow_tools._get_available_agents_handler()()
    assert result["total_agents"] == 0
    assert result["output_agents"] == []


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


def _inspection_flow() -> dict[str, Any]:
    return {
        "flow_name": "Inspection Flow",
        "version": "1.1",
        "entry_node_id": "task",
        "nodes": [
            {
                "id": "task",
                "type": "task_input",
                "data": {
                    "agent_id": "task_input",
                    "agent_display_name": "Task",
                    "task_instructions": "abcdefghij",
                    "output_key": "task_input",
                },
            },
            {
                "id": "extract",
                "type": "agent",
                "data": {
                    "agent_id": "gene_extractor",
                    "agent_display_name": "Extract",
                    "step_goal": "Extract genes",
                    "custom_instructions": "klmnopqrst",
                    "prompt_version": 7,
                    "output_key": "genes",
                    "validation_attachments": [],
                    "validation_groups": [],
                },
            },
            {
                "id": "csv",
                "type": "output",
                "data": {
                    "agent_id": "csv_formatter",
                    "agent_display_name": "CSV",
                    "output_key": "csv",
                    "projection_plan": {
                        "columns": [{"field": "gene.symbol"}, {"field": "gene.id"}],
                        "format": "csv",
                    },
                },
            },
            {
                "id": "next",
                "type": "agent",
                "data": {
                    "agent_id": "disease_extractor",
                    "agent_display_name": "Continue",
                    "output_key": "diseases",
                },
            },
            {
                "id": "json",
                "type": "output",
                "data": {
                    "agent_id": "json_formatter",
                    "agent_display_name": "JSON",
                    "output_key": "json",
                },
            },
        ],
        "edges": [
            {"id": "c1", "source": "task", "target": "extract", "role": "control_flow"},
            {"id": "o1", "source": "extract", "target": "csv", "role": "output_attachment"},
            {"id": "c2", "source": "extract", "target": "next", "role": "control_flow"},
            {"id": "o2", "source": "next", "target": "json", "role": "output_attachment"},
        ],
    }


def test_get_current_flow_returns_minimal_manifest_for_empty_flow():
    handler = flow_tools._get_current_flow_handler()

    no_context = handler()
    assert no_context["success"] is False
    assert no_context["complete"] is True

    flow_tools.set_current_flow_context(
        {"flow_name": "Untitled", "version": "1.1", "nodes": [], "edges": []}
    )
    manifest = handler()

    assert manifest["success"] is True
    assert manifest["contract"] == "current_flow_manifest_v1"
    assert manifest["counts"]["all_nodes"] == 0
    assert manifest["has_critical_issues"] is True
    assert {item["code"] for item in manifest["findings"]} >= {"missing_task_input"}
    for verbose_key in (
        "steps",
        "edges",
        "execution_order_markdown",
        "executable_graph",
        "domain_envelope_analysis",
        "projection_plan",
        "validation_attachments",
    ):
        assert verbose_key not in manifest


def test_manifest_classifies_continuing_multi_output_control_path_and_duplicates():
    flow = _inspection_flow()
    flow.update({
        "flow_id": "flow-123",
        "flow_description": "Current exact description",
        "flow_updated_at": "2026-09-04T12:00:00Z",
        "flow_is_dirty": True,
        "flow_draft_fingerprint": f"sha256:{'b' * 64}",
        "task_instructions_default_only": False,
    })
    flow["nodes"][1]["position"] = {"x": 123.5, "y": -9.25}
    flow["edges"][0]["condition"] = {"type": "not_empty"}
    flow["nodes"][4]["data"]["output_key"] = "diseases"
    flow_tools.set_current_flow_context(flow)

    manifest = flow_tools._get_current_flow_handler()()

    assert manifest["topology_valid"] is True
    assert manifest["ordered_control_node_ids"] == ["task", "extract", "next"]
    assert manifest["executable_agent_node_ids"] == ["extract", "next"]
    assert manifest["output_node_ids"] == ["csv", "json"]
    assert manifest["counts"] == {
        "all_nodes": 5,
        "control_nodes": 3,
        "ordered_control_nodes": 3,
        "executable_agents": 2,
        "output_nodes": 2,
        "validation_sidecars": 0,
        "disconnected_nodes": 0,
    }
    duplicate = next(
        item for item in manifest["findings"] if item["code"] == "duplicate_output_key"
    )
    assert duplicate["severity"] == "HIGH"
    assert duplicate["duplicate_count"] == 2
    assert manifest["high_issue_count"] == 1
    assert manifest["has_critical_issues"] is False
    assert manifest["authoring"] == {
        "flow_id": "flow-123",
        "description": "Current exact description",
        "baseline_updated_at": "2026-09-04T12:00:00Z",
        "draft_is_dirty": True,
        "draft_fingerprint": f"sha256:{'b' * 64}",
        "task_instructions_default_only": False,
    }

    node = flow_tools._get_current_flow_node_handler()(node_id="extract")
    assert node["position"] == {"x": 123.5, "y": -9.25}

    control_edges = flow_tools._get_current_flow_topology_handler()(
        section="control_edges"
    )
    assert control_edges["items"][0]["condition"] == {"type": "not_empty"}

    bindings = flow_tools._get_current_flow_topology_handler()(
        section="output_bindings"
    )
    assert [item["output_node_id"] for item in bindings["items"]] == ["csv", "json"]
    assert bindings["complete"] is True


def test_manifest_keeps_critical_topology_and_task_findings_in_first_call():
    flow = _inspection_flow()
    flow["nodes"][0]["data"]["task_instructions"] = ""
    flow["edges"].append(
        {"id": "branch", "source": "task", "target": "next", "role": "control_flow"}
    )
    flow_tools.set_current_flow_context(flow)

    manifest = flow_tools._get_current_flow_handler()()

    assert manifest["has_critical_issues"] is True
    assert {item["code"] for item in manifest["findings"]} >= {
        "empty_task_input",
        "branch",
    }
    topology = flow_tools._get_current_flow_topology_handler()(
        section="issues", limit=1
    )
    assert topology["truncated"] is True
    topology_next = flow_tools._get_current_flow_topology_handler()(
        **topology["next_call"]["arguments"]
    )
    assert topology_next["cursor"] == "1"
    first = flow_tools._get_current_flow_validation_warnings_handler()(limit=1)
    assert first["truncated"] is True
    assert first["next_call"]["tool"] == "get_current_flow_validation_warnings"
    second = flow_tools._get_current_flow_validation_warnings_handler()(
        **first["next_call"]["arguments"]
    )
    assert second["cursor"] == "1"


def test_instruction_chunks_reconstruct_exact_text(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_FLOW_INSPECTION_CHUNK_MAX_CHARS", "4")
    flow_tools.set_current_flow_context(_inspection_flow())
    handler = flow_tools._get_current_flow_instructions_handler()

    response = handler(node_id="extract", field="custom_instructions")
    chunks = [response["content"]]
    assert response["content_sha256"]
    while response["next_call"] is not None:
        response = handler(**response["next_call"]["arguments"])
        chunks.append(response["content"])

    assert "".join(chunks) == "klmnopqrst"
    assert response["complete"] is True
    assert response["truncated"] is False


def test_escape_heavy_flow_details_fit_provider_envelope_and_reconstruct(monkeypatch):
    provider_limit = 700
    monkeypatch.setenv(
        "AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", str(provider_limit)
    )
    monkeypatch.setenv("AGENT_STUDIO_FLOW_INSPECTION_CHUNK_MAX_CHARS", "4000")
    flow = _inspection_flow()
    instruction = ('🧬é"\\\n\t\x01' * 600) + "tail"
    projection_value = {
        "escaped": instruction,
        "nested": [{"control": "\b\f\r", "unicode": "値🧬" * 300}],
    }
    flow["nodes"][1]["data"]["custom_instructions"] = instruction
    flow["nodes"][2]["data"]["projection_plan"]["escape_fixture"] = projection_value
    flow_tools.set_current_flow_context(flow)

    cases = (
        (
            "get_current_flow_instructions",
            flow_tools._get_current_flow_instructions_handler(),
            {"node_id": "extract", "field": "custom_instructions"},
            instruction,
        ),
        (
            "get_current_flow_projection_plan",
            flow_tools._get_current_flow_projection_plan_handler(),
            {"node_id": "csv", "field": "escape_fixture", "section": ""},
            json.dumps(
                projection_value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        ),
    )

    for tool_name, handler, initial_arguments, expected in cases:
        arguments: dict[str, Any] = initial_arguments
        chunks = []
        previous_end = -1
        while True:
            result = handler(**arguments)
            serialized = api_module._provider_tool_result_content(
                tool_name=tool_name,
                tool_input=arguments,
                tool_result=result,
                session_id="session-1",
                turn_id="turn-1",
            )
            assert len(serialized) <= provider_limit
            assert json.loads(serialized).get("status") != "compacted_tool_result"
            assert result["end_char"] > previous_end
            expected_start = previous_end if previous_end >= 0 else 0
            assert result["start_char"] == expected_start
            chunks.append(result["content"])
            previous_end = result["end_char"]
            if result["complete"]:
                expected_hash = result["content_sha256"]
                break
            arguments = result["next_call"]["arguments"]

        reconstructed = "".join(chunks)
        assert reconstructed == expected
        assert hashlib.sha256(reconstructed.encode("utf-8")).hexdigest() == expected_hash


def test_flow_detail_reports_provider_safe_configuration_error(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "240")
    flow_tools.set_current_flow_context(_inspection_flow())

    result = flow_tools._get_current_flow_instructions_handler()(
        node_id="extract", field="custom_instructions"
    )

    assert result["success"] is False
    assert result["error"] == "provider_limit_too_small"
    assert len(json.dumps(result, default=str)) <= 240


def test_projection_plan_inventory_and_chunks_are_bounded_and_reconstructable(
    monkeypatch,
):
    monkeypatch.setenv("AGENT_STUDIO_FLOW_INSPECTION_CHUNK_MAX_CHARS", "8")
    flow_tools.set_current_flow_context(_inspection_flow())

    node = flow_tools._get_current_flow_node_handler()(node_id="csv")
    assert node["scalar_configuration"]["agent_id"] == "csv_formatter"
    assert "projection_plan" not in node["scalar_configuration"]
    assert node["detail_availability"]["projection_plan"] is True

    handler = flow_tools._get_current_flow_projection_plan_handler()
    inventory = handler(node_id="csv", limit=1)
    assert inventory["items"] == [{"field": "columns", "value_type": "list"}]
    assert inventory["next_call"]["arguments"]["cursor"] == "1"

    response = handler(node_id="csv", field="columns")
    chunks = [response["content"]]
    while response["next_call"] is not None:
        response = handler(**response["next_call"]["arguments"])
        chunks.append(response["content"])

    assert "".join(chunks) == '[{"field":"gene.symbol"},{"field":"gene.id"}]'
    assert response["encoding"] == "canonical_json"
    assert response["complete"] is True


def test_projection_plan_sections_follow_json_pointer_semantics():
    flow = _inspection_flow()
    flow["nodes"][2]["data"]["projection_plan"]["details"] = {
        "": "empty-key",
        "a/b": {"til~de": "escaped-key"},
        "rows": [{"name": "first"}, {"name": "second"}],
    }
    flow_tools.set_current_flow_context(flow)
    handler = flow_tools._get_current_flow_projection_plan_handler()

    root = handler(node_id="csv", field="details", section="")
    assert root["success"] is True
    assert json.loads(root["content"])["rows"][1]["name"] == "second"

    empty_key = handler(node_id="csv", field="details", section="/")
    assert empty_key["success"] is True
    assert empty_key["content"] == '"empty-key"'

    nested_array = handler(
        node_id="csv", field="details", section="/rows/1/name"
    )
    assert nested_array["success"] is True
    assert nested_array["content"] == '"second"'

    escaped_keys = handler(
        node_id="csv", field="details", section="/a~1b/til~0de"
    )
    assert escaped_keys["success"] is True
    assert escaped_keys["content"] == '"escaped-key"'

    for invalid_index in ("-1", "+1", "01", "1.0", "-", "2"):
        invalid = handler(
            node_id="csv",
            field="details",
            section=f"/rows/{invalid_index}",
        )
        assert invalid["success"] is False

    invalid_escape = handler(node_id="csv", field="details", section="/a~2b")
    assert invalid_escape["success"] is False


def test_validation_schedule_details_preserve_defaults_opt_outs_and_sidecars():
    flow = _inspection_flow()
    extract = flow["nodes"][1]["data"]
    extract["validation_attachments"] = [
        {
            "attachment_id": "default",
            "validator_id": "default-validator",
            "validator_binding_id": "default-binding",
            "state": "active",
            "enabled": True,
            "default_enabled": True,
            "allow_opt_out": False,
        },
        {
            "attachment_id": "optional",
            "validator_id": "optional-validator",
            "validator_binding_id": "optional-binding",
            "state": "active",
            "enabled": False,
            "default_enabled": True,
            "allow_opt_out": True,
        },
        {
            "attachment_id": "replaced",
            "validator_id": "old-validator",
            "validator_binding_id": "old-binding",
            "state": "active",
            "enabled": True,
            "default_enabled": True,
        },
        {
            "attachment_id": "future",
            "validator_id": "future-validator",
            "state": "under_development",
            "enabled": False,
            "default_enabled": False,
        },
    ]
    extract["validation_groups"] = [
        {
            "group_id": "replacement",
            "state": "replaced",
            "attachment_id": "replaced",
            "binding_id": "new-binding",
            "validator_node_id": "custom-validator",
            "replaces_attachment_id": "replaced",
        },
        {
            "group_id": "supplement",
            "state": "supplemental",
            "binding_id": "supplemental-binding",
            "validator_node_id": "supplemental-validator",
        },
    ]
    flow["nodes"].extend(
        [
            {
                "id": "custom-validator",
                "type": "agent",
                "data": {
                    "agent_id": "custom_validator",
                    "agent_display_name": "Replacement",
                    "output_key": "replacement",
                },
            },
            {
                "id": "supplemental-validator",
                "type": "agent",
                "data": {
                    "agent_id": "supplemental_validator",
                    "agent_display_name": "Supplemental",
                    "output_key": "supplemental",
                },
            },
        ]
    )
    flow["edges"].extend(
        [
            {
                "id": "v1",
                "source": "extract",
                "target": "custom-validator",
                "role": "validation_attachment",
                "satisfies_binding_id": "new-binding",
                "replaces_attachment_id": "replaced",
            },
            {
                "id": "v2",
                "source": "extract",
                "target": "supplemental-validator",
                "role": "validation_attachment",
                "satisfies_binding_id": "supplemental-binding",
            },
        ]
    )
    flow_tools.set_current_flow_context(flow)
    handler = flow_tools._get_current_flow_validation_schedule_handler()

    selections = handler(node_id="extract", section="selections")
    assert selections["items"][0]["default_enabled"] is True
    assert selections["section_counts"] == {
        "selections": 4,
        "scheduled_validators": 1,
        "opt_outs": 1,
        "replacement_validators": 1,
        "supplemental_validators": 1,
        "inactive_metadata": 1,
    }
    assert handler(node_id="extract", section="replacement_validators")["items"][0][
        "validator_node_id"
    ] == "custom-validator"
    assert handler(node_id="extract", section="supplemental_validators")["items"][0][
        "validator_binding_id"
    ] == "supplemental-binding"


def test_manifest_exposes_compact_domain_pack_link_without_aggregate_analysis(
    monkeypatch,
):
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "gene_extractor": {
                "name": "Gene Extractor",
                "category": "Extraction",
                "curation": {"domain_pack_id": "alliance_gene"},
            },
            "disease_extractor": {
                "name": "Disease Extractor",
                "category": "Extraction",
            },
        },
    )
    flow_tools.set_current_flow_context(_inspection_flow())

    manifest = flow_tools._get_current_flow_handler()()

    extract = next(node for node in manifest["nodes"] if node["node_id"] == "extract")
    assert extract["domain_pack_id"] == "alliance_gene"
    assert "domain_envelope_analysis" not in manifest
def test_flow_proposal_compiles_semantic_operations_without_database_writes(
    monkeypatch,
):
    base_fingerprint = f"sha256:{'a' * 64}"
    flow_tools.set_workflow_user_context(123)
    flow_tools.set_current_flow_context(
        {
            "flow_name": "New Flow",
            "flow_description": "",
            "flow_draft_fingerprint": base_fingerprint,
            "version": "1.1",
            "entry_node_id": "node_0",
            "nodes": [
                {
                    "id": "node_0",
                    "type": "task_input",
                    "position": {"x": 250, "y": 100},
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": "",
                        "output_key": "task_input",
                        "validation_groups": [],
                    },
                }
            ],
            "edges": [],
        }
    )
    accessible = {
        "gene_extractor": {
            "name": "Gene Extractor",
            "description": "Extract genes",
            "category": "Extraction",
            "produces_flow_artifacts": True,
        },
        "chat_output": {
            "name": "Chat Output",
            "description": "Display results",
            "category": "Output",
        },
    }
    monkeypatch.setattr(flow_tools, "_accessible_flow_agents", lambda: accessible)
    monkeypatch.setattr(
        flow_tools,
        "resolve_live_flow_agent",
        lambda agent_id, _context: accessible.get(agent_id),
    )

    propose = flow_tools._propose_flow_draft_update_handler()
    result = propose(
        base_draft_fingerprint=base_fingerprint,
        change_summary="Build a gene extraction flow.",
        operations=[
            {
                "operation": "update_flow",
                "name": "Gene flow",
                "description": "Extract genes",
                "task_instructions": "Extract every gene mentioned in the paper.",
            },
            {
                "operation": "add_agent_step",
                "agent_id": "gene_extractor",
                "step_ref": "extractor",
                "step_goal": "Extract genes",
            },
            {
                "operation": "add_agent_step",
                "agent_id": "gene_extractor",
                "step_ref": "reviewer",
                "step_goal": "Review extracted genes",
            },
        ],
    )

    assert result["success"] is True, result
    assert result["pending_user_approval"] is True
    assert result["base_draft_fingerprint"] == base_fingerprint
    assert result["candidate_draft_fingerprint"].startswith("sha256:")
    assert result["candidate"]["name"] == "Gene flow"
    assert "null" not in json.dumps(result["candidate"])
    assert "task_instructions_default_only" not in result["candidate"][
        "flow_definition"
    ]
    assert all(
        "validation_groups" not in node["data"]
        for node in result["candidate"]["flow_definition"]["nodes"]
    )
    assert result["candidate"]["flow_definition"]["nodes"][1]["id"] == "node_1"
    assert result["candidate"]["flow_definition"]["edges"][0] == {
        "id": "edge_1",
        "source": "node_0",
        "target": "node_1",
        "role": "control_flow",
    }
    assert result["candidate"]["flow_definition"]["edges"][1]["source"] == "node_1"
    assert result["candidate"]["flow_definition"]["edges"][1]["target"] == "node_2"
    assert result["diff"]
    assert not any(
        entry["path"].endswith("task_instructions_default_only")
        or entry["path"].endswith("validation_groups")
        for entry in result["diff"]
    )

    follow_up = propose(
        base_draft_fingerprint=base_fingerprint,
        change_summary="Refine the proposed extraction step.",
        operations=[
            {
                "operation": "update_step",
                "node_ref": "extractor",
                "custom_instructions": "Keep exact evidence references.",
            },
            {
                "operation": "reorder_control_steps",
                "ordered_refs": ["reviewer", "extractor"],
            },
            {
                "operation": "add_agent_step",
                "agent_id": "chat_output",
                "step_ref": "result",
                "source_refs": ["extractor", "reviewer"],
            },
        ],
    )
    assert follow_up["success"] is True, follow_up
    assert len(follow_up["candidate"]["flow_definition"]["nodes"]) == 4
    assert follow_up["candidate"]["flow_definition"]["nodes"][1]["data"][
        "custom_instructions"
    ] == "Keep exact evidence references."
    assert follow_up["candidate"]["flow_definition"]["edges"][0]["source"] == "node_0"
    assert follow_up["candidate"]["flow_definition"]["edges"][0]["target"] == "node_2"
    assert follow_up["candidate"]["flow_definition"]["edges"][1]["source"] == "node_2"
    assert follow_up["candidate"]["flow_definition"]["edges"][1]["target"] == "node_1"
    assert follow_up["candidate"]["flow_definition"]["edges"][-2]["source"] == "node_1"
    assert follow_up["candidate"]["flow_definition"]["edges"][-2]["target"] == "node_3"
    assert follow_up["candidate"]["flow_definition"]["edges"][-1]["source"] == "node_2"
    assert follow_up["candidate"]["flow_definition"]["edges"][-1]["target"] == "node_3"


def test_flow_proposal_rejects_stale_or_unavailable_references(monkeypatch):
    current = f"sha256:{'b' * 64}"
    flow_tools.set_current_flow_context(
        {
            "flow_name": "Flow",
            "flow_draft_fingerprint": current,
            "version": "1.1",
            "entry_node_id": "task",
            "nodes": [],
            "edges": [],
        }
    )
    handler = flow_tools._propose_flow_draft_update_handler()
    stale = handler(
        base_draft_fingerprint=f"sha256:{'c' * 64}",
        operations=[{"operation": "update_flow", "name": "Other"}],
        change_summary="Rename",
    )
    assert stale["code"] == "stale_draft_fingerprint"

    monkeypatch.setenv("AGENT_STUDIO_FLOW_DESCRIPTION_MAX_CHARS", "4")
    oversized_description = handler(
        base_draft_fingerprint=current,
        operations=[
            {"operation": "update_flow", "description": "too long"}
        ],
        change_summary="Update description",
    )
    assert oversized_description["success"] is False
    assert "description exceeds 4" in oversized_description["error"]

    monkeypatch.setattr(flow_tools, "_accessible_flow_agents", lambda: {})
    unavailable = handler(
        base_draft_fingerprint=current,
        operations=[{"operation": "add_agent_step", "agent_id": "private_agent"}],
        change_summary="Add an unavailable step",
    )
    assert unavailable["success"] is False
    assert "not available" in unavailable["error"]


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
    assert first["complete"] is False
    assert first["next_call"] == {
        "tool": "get_available_agents",
        "arguments": {
            "limit": 2,
            "cursor": "2",
        },
    }

    second = handler(**first["next_call"]["arguments"])
    assert second["returned_count"] == 2
    assert second["truncated"] is False
    assert second["next_cursor"] is None
    assert second["complete"] is True
    assert second["next_call"] is None

    first_ids = {a["agent_id"] for ag in first["categories"].values() for a in ag}
    second_ids = {a["agent_id"] for ag in second["categories"].values() for a in ag}
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == {
        "gene_extractor",
        "gene_validation",
        "disease_extractor",
        "chat_output",
    }


def test_get_available_agents_recovers_oversized_escape_heavy_record(monkeypatch):
    description = '🧬値"\\\n\t\x01' * 1_000
    expected = {
        "agent_id": "escape_agent",
        "name": "Escape 🧬 agent",
        "description": description,
        "category": "Extraction",
        "requires_document": False,
    }
    normal_agents = {
        "first_agent": {
            "name": "First agent",
            "description": "Fits before the oversized record",
            "category": "Extraction",
        },
        "last_agent": {
            "name": "Last agent",
            "description": "Fits after the oversized record",
            "category": "Extraction",
        },
    }
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["first_agent", "escape_agent", "last_agent"],
    )
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "first_agent": normal_agents["first_agent"],
            "escape_agent": {
                "name": expected["name"],
                "description": description,
                "category": "Extraction",
            },
            "last_agent": normal_agents["last_agent"],
        },
    )
    monkeypatch.setattr(flow_tools, "_FLOW_CATALOG_RESULT_MAX_CHARS", 700)
    monkeypatch.setattr(flow_tools, "_FLOW_CATALOG_CHUNK_MAX_CHARS", 4_000)
    monkeypatch.setenv("AGENT_STUDIO_PROVIDER_TOOL_RESULT_INLINE_MAX_CHARS", "700")
    handler = flow_tools._get_available_agents_handler()

    page = handler(limit=2)
    page_serialized = api_module._provider_tool_result_content(
        tool_name="get_available_agents",
        tool_input={"limit": 2},
        tool_result=page,
        session_id="session-1",
        turn_id="turn-1",
    )
    assert len(page_serialized) <= 700
    assert json.loads(page_serialized).get("status") != "compacted_tool_result"
    assert [
        agent["agent_id"]
        for agents in page["categories"].values()
        for agent in agents
    ] == ["first_agent"]
    assert page["next_call"]["arguments"]["cursor"] == "1"

    chunks = []
    expected_hash = None
    previous_end = 0
    returned_agent_ids = ["first_agent"]
    next_call = page["next_call"]
    saw_detail = False
    while next_call is not None:
        arguments = next_call["arguments"]
        result = handler(**arguments)
        serialized = api_module._provider_tool_result_content(
            tool_name="get_available_agents",
            tool_input=arguments,
            tool_result=result,
            session_id="session-1",
            turn_id="turn-1",
        )
        assert len(serialized) <= 700
        assert json.loads(serialized).get("status") != "compacted_tool_result"

        if result.get("detail_mode") == "agent_record":
            saw_detail = True
            assert result["detail_index"] == 1
            assert result["range"]["start"] == previous_end
            assert result["range"]["end"] > previous_end
            previous_end = result["range"]["end"]
            chunks.append(result["content"])
            expected_hash = result["sha256"]
        else:
            returned_agent_ids.extend(
                agent["agent_id"]
                for agents in result["categories"].values()
                for agent in agents
            )
        next_call = result["next_call"]

    assert saw_detail is True
    reconstructed = "".join(chunks)
    assert hashlib.sha256(reconstructed.encode("utf-8")).hexdigest() == expected_hash
    assert json.loads(reconstructed) == expected
    assert returned_agent_ids == ["first_agent", "last_agent"]


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


def test_get_flow_templates_pages_templates_independently_without_agent_repetition(monkeypatch):
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS",
                        ["gene_extractor", "gene_validation", "disease_extractor"])
    monkeypatch.setattr(flow_tools, "AGENT_REGISTRY", _multi_agent_registry())
    templates = [{"name": f"Gene template {index}",
                  "description": f"Example {index} " + ("bounded recipe " * 30),
                  "steps": [{"agent_id": "gene_extractor"}, {"agent_id": "gene_validation"}]}
                 for index in range(37)]
    monkeypatch.setattr(flow_tools, "_filter_flow_templates", lambda *args, **kwargs: templates)
    handler = flow_tools._get_flow_templates_handler()
    first = handler(limit=1, template_limit=2, template_query="gene")
    content = api_module._provider_tool_result_content(
        tool_name="get_flow_templates",
        tool_input={"limit": 1, "template_limit": 2, "template_query": "gene"},
        tool_result=first, session_id="session-1", turn_id="turn-1")
    assert json.loads(content).get("status") != "compacted_tool_result"
    assert first["returned_count"] == 1
    assert first["template_returned_count"] == 2
    assert first["template_total_count"] == 37
    assert first["template_next_call"]["arguments"]["section"] == "templates"
    assert first["agent_next_call"]["arguments"]["section"] == "agents"
    assert first["complete"] is False
    assert first["next_call"] == first["agent_next_call"]
    assert first["next_call"]["arguments"]["template_query"] == "gene"
    assert first["next_call"]["arguments"]["template_limit"] == 2
    agent_page = handler(**first["agent_next_call"]["arguments"])
    assert agent_page["templates"] == []
    template_page = handler(**first["template_next_call"]["arguments"])
    assert template_page["available_agents"] == []
    assert template_page["templates"][0]["name"] == "Gene template 2"
    assert template_page["complete"] is False
    assert template_page["template_next_call"]["arguments"]["template_cursor"] == "4"
    assert template_page["template_next_call"]["arguments"]["pending_agent_cursor"] == "1"
    assert template_page["next_call"] == template_page["agent_next_call"]


def test_get_flow_templates_top_level_continuation_reconstructs_both_collections(
    monkeypatch,
):
    agent_ids = ["gene_extractor", "gene_validation", "disease_extractor"]
    templates = [
        {
            "name": f"Gene template {index}",
            "description": f"Example {index}",
            "steps": [{"agent_id": "gene_extractor"}],
        }
        for index in range(7)
    ]
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", agent_ids)
    monkeypatch.setattr(flow_tools, "AGENT_REGISTRY", _multi_agent_registry())
    monkeypatch.setattr(
        flow_tools,
        "_filter_flow_templates",
        lambda *args, **kwargs: [
            *templates,
            {
                "name": "Unrelated workflow",
                "description": "Does not match the template filter",
                "steps": [{"agent_id": "disease_extractor"}],
            },
        ],
    )
    handler = flow_tools._get_flow_templates_handler()

    response = handler(limit=1, template_limit=2, template_query="gene")
    returned_agent_ids = []
    returned_template_names = []
    while True:
        returned_agent_ids.extend(
            agent["agent_id"] for agent in response["available_agents"]
        )
        returned_template_names.extend(
            template["name"] for template in response["templates"]
        )
        if response["complete"]:
            assert response["next_call"] is None
            break
        assert response["next_call"] is not None
        response = handler(**response["next_call"]["arguments"])

    assert returned_agent_ids == agent_ids
    assert returned_template_names == [template["name"] for template in templates]


def test_get_flow_templates_recovers_oversized_unicode_records_without_provider_compaction(
    monkeypatch,
):
    template = {
        "name": "Large 🧬 template",
        "description": "🧬値" * 2_000,
        "steps": [
            {"agent_id": "gene_extractor", "custom_instructions": "🧬" * 2_000}
            for _ in range(30)
        ],
    }
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["gene_extractor"])
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "gene_extractor": {
                "name": "Gene 🧬 extractor",
                "description": "🧬値" * 2_000,
                "category": "Extraction",
            }
        },
    )
    monkeypatch.setattr(
        flow_tools,
        "_filter_flow_templates",
        lambda *args, **kwargs: [template],
    )
    monkeypatch.setattr(flow_tools, "_FLOW_CATALOG_RESULT_MAX_CHARS", 1_400)
    monkeypatch.setattr(flow_tools, "_FLOW_CATALOG_CHUNK_MAX_CHARS", 1_000)
    handler = flow_tools._get_flow_templates_handler()

    for kind, initial_arguments, expected in (
        ("template", {"section": "templates"}, template),
        (
            "agent",
            {"section": "agents"},
            {
                "agent_id": "gene_extractor",
                "display_name": "Gene 🧬 extractor",
                "description": "🧬値" * 2_000,
                "category": "Extraction",
                "requires_document": False,
            },
        ),
    ):
        page = handler(**cast(dict[str, Any], initial_arguments))
        assert len(json.dumps(page, default=str)) <= 1_400
        page_content = api_module._provider_tool_result_content(
            tool_name="get_flow_templates",
            tool_input=initial_arguments,
            tool_result=page,
            session_id="session-1",
            turn_id="turn-1",
        )
        assert json.loads(page_content).get("status") != "compacted_tool_result"
        next_call = page[f"{kind}_next_call"]
        assert next_call["arguments"]["detail_kind"] == kind
        chunks = []
        expected_hash = None
        while next_call is not None and "detail_kind" in next_call["arguments"]:
            detail = handler(**next_call["arguments"])
            assert len(json.dumps(detail, default=str)) <= 1_400
            content = api_module._provider_tool_result_content(
                tool_name="get_flow_templates",
                tool_input=next_call["arguments"],
                tool_result=detail,
                session_id="session-1",
                turn_id="turn-1",
            )
            assert json.loads(content).get("status") != "compacted_tool_result"
            chunks.append(detail["content"])
            expected_hash = detail["sha256"]
            next_call = detail["next_call"]
        reconstructed = "".join(chunks)
        assert hashlib.sha256(reconstructed.encode()).hexdigest() == expected_hash
        assert json.loads(reconstructed) == expected


def test_register_flow_tools_registers_manifest_and_bounded_detail_tools(monkeypatch):
    registrations = []

    class _Registry:
        def unregister(self, _name):
            return False

        def register(self, **kwargs):
            registrations.append(kwargs)

    monkeypatch.setattr(
        flow_tools, "get_diagnostic_tools_registry", lambda: _Registry()
    )
    monkeypatch.setattr(
        flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction", "gene", "chat_output"]
    )
    monkeypatch.delenv("AGENT_STUDIO_FLOW_STEP_GOAL_MAX_CHARS", raising=False)

    flow_tools.register_flow_tools()

    names = [entry["name"] for entry in registrations]
    assert names == [
        "propose_flow_draft_update",
        "validate_flow",
        "get_flow_templates",
        "get_current_flow",
        "get_current_flow_topology",
        "get_current_flow_node",
        "get_current_flow_instructions",
        "get_current_flow_projection_plan",
        "get_current_flow_validation_warnings",
        "get_current_flow_validation_schedule",
        "get_available_agents",
    ]
    assert all(entry["category"] == "flows" for entry in registrations)
    assert all(callable(entry["handler"]) for entry in registrations)
    flow_catalog_schema = registrations[2]["input_schema"]["properties"]
    assert {
        "detail_kind",
        "detail_index",
        "detail_cursor",
        "detail_max_chars",
    }.issubset(flow_catalog_schema)
    assert flow_catalog_schema["detail_max_chars"]["maximum"] == 6_000
    proposal_schema = registrations[0]["input_schema"]
    assert proposal_schema["required"] == [
        "base_draft_fingerprint",
        "operations",
        "change_summary",
    ]
    assert proposal_schema["properties"]["operations"]["maxItems"] == 30
    assert (
        "add_agent_step"
        in proposal_schema["properties"]["operations"]["items"]["properties"][
            "operation"
        ]["enum"]
    )
    validate_flow_schema = registrations[1]["input_schema"]
    assert "steps" not in validate_flow_schema["properties"]
    assert validate_flow_schema["required"] == ["flow_definition"]
    exact_schema = validate_flow_schema["properties"]["flow_definition"]
    assert {"nodes", "edges", "entry_node_id"}.issubset(exact_schema["properties"])


def test_register_flow_tools_propagates_configured_limits(monkeypatch):
    registrations = []

    class _Registry:
        def unregister(self, _name):
            return False

        def register(self, **kwargs):
            registrations.append(kwargs)

    monkeypatch.setattr(
        flow_tools, "get_diagnostic_tools_registry", lambda: _Registry()
    )
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
    monkeypatch.setenv("AGENT_STUDIO_FLOW_INSPECTION_PAGE_LIMIT", "6")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_INSPECTION_CHUNK_MAX_CHARS", "900")
    monkeypatch.setenv("AGENT_STUDIO_FLOW_PROPOSAL_MAX_OPERATIONS", "7")
    monkeypatch.setenv("TOOL_PAGE_DEFAULT_LIMIT", "17")
    monkeypatch.setenv("TOOL_PAGE_MAX_LIMIT", "13")

    flow_tools.register_flow_tools()

    proposal_schema = registrations[0]["input_schema"]
    validate_schema = registrations[1]["input_schema"]
    assert "steps" not in validate_schema["properties"]
    assert validate_schema["required"] == ["flow_definition"]
    assert proposal_schema["properties"]["operations"]["maxItems"] == 7
    assert validate_schema["properties"]["name"]["maxLength"] == 40
    by_name = {registration["name"]: registration for registration in registrations}
    assert (
        by_name["get_current_flow_topology"]["input_schema"]["properties"]["limit"][
            "maximum"
        ]
        == 6
    )
    assert (
        by_name["get_current_flow_instructions"]["input_schema"]["properties"]["limit"][
            "maximum"
        ]
        == 900
    )
    available_agents_description = by_name["get_available_agents"]["description"]
    available_agents_properties = by_name["get_available_agents"]["input_schema"][
        "properties"
    ]
    assert available_agents_properties["limit"]["default"] == 13
    assert available_agents_properties["limit"]["maximum"] == 13
    assert {
        "detail_index",
        "detail_cursor",
        "detail_max_chars",
    }.issubset(available_agents_properties)
    assert by_name["get_available_agents"]["handler"]()["limit"] == 13
    assert "complete focused\nOutput catalog" in available_agents_description
    assert "next_call through ordinary pages and exact record chunks" in (
        available_agents_description
    )
    assert "terminal control nodes" in available_agents_description
    assert (
        "flow ends with an appropriate output agent" not in available_agents_description
    )
