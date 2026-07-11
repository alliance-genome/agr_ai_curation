"""Unit tests for Agent Studio flow tools."""

from __future__ import annotations

import pytest

import src.lib.agent_studio.flow_tools as flow_tools


@pytest.fixture(autouse=True)
def _clear_contextvars():
    flow_tools.clear_workflow_user_context()
    flow_tools.clear_current_flow_context()
    yield
    flow_tools.clear_workflow_user_context()
    flow_tools.clear_current_flow_context()


def test_workflow_user_context_set_get_clear():
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
        ["pdf_extraction", "gene_expression", "chat_output", "gene"],
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
    assert any("Consider adding 'gene' step" in s for s in result["suggestions"])


def test_validate_flow_handler_suggests_pdf_and_output(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "FLOW_AGENT_IDS",
        ["gene", "disease", "pdf_extraction", "chat_output"],
    )
    validate = flow_tools._validate_flow_handler()
    result = validate(
        steps=[{"agent_id": "gene"}, {"agent_id": "disease"}],
        name="Flow Name",
    )

    assert result["valid"] is True
    assert any("Consider adding 'pdf_extraction'" in s for s in result["suggestions"])
    assert any("Consider adding 'chat_output'" in s for s in result["suggestions"])


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
        ["gene_expression", "gene_expression_extraction", "gene"],
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
        "Consider adding 'gene' step after 'gene_expression'" in suggestion
        for suggestion in flow_alias_result["suggestions"]
    )
    assert any(
        "Consider adding 'gene' step after 'gene_expression'" in suggestion
        for suggestion in package_agent_result["suggestions"]
    )


def test_get_flow_templates_handler_uses_registry(monkeypatch):
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction", "gene"])
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
            "gene": {
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
    assert result["available_agents"][0]["agent_id"] in {"pdf_extraction", "gene"}
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
                    "type": "agent",
                    "data": {
                        "agent_id": "chat_output",
                        "agent_display_name": "Output",
                        "output_key": "out",
                    },
                },
            ],
            "edges": [
                {"source": "task_input_0", "target": "extract_1"},
                {"source": "extract_1", "target": "output_1"},
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
    assert "Unknown agent_id" in unknown_agent["error"]


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
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction", "gene"])
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {"pdf_extraction": {"name": "PDF Specialist"}, "gene": {"name": "Gene Specialist"}},
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
    monkeypatch.setattr(flow_tools, "AGENT_REGISTRY", _multi_agent_registry())
    handler = flow_tools._get_available_agents_handler()

    result = handler(category="Extraction")

    assert set(result["categories"].keys()) == {"Extraction"}
    assert set(result["extraction_agents"]) == {"gene_extractor", "disease_extractor"}
    assert result["total_count"] == 2


def test_get_available_agents_handler_pages_with_cursor(monkeypatch):
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
