"""Unit tests for Agent Studio flow tools."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_get_flow_agent_ids_excludes_supervisor_and_task_input(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {"supervisor": {}, "task_input": {}, "pdf_extraction": {}, "gene": {}, "chat_output": {}},
    )
    assert flow_tools._get_flow_agent_ids() == ["chat_output", "gene", "pdf_extraction"]


def test_validate_flow_handler_reports_errors_warnings_and_suggestions(monkeypatch):
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["pdf_extraction", "gene_expression", "chat_output"])
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
    monkeypatch.setattr(flow_tools, "FLOW_AGENT_IDS", ["gene", "disease"])
    validate = flow_tools._validate_flow_handler()
    result = validate(
        steps=[{"agent_id": "gene"}, {"agent_id": "disease"}],
        name="Flow Name",
    )

    assert result["valid"] is True
    assert any("Consider adding 'pdf_extraction'" in s for s in result["suggestions"])
    assert any("Consider adding 'chat_output'" in s for s in result["suggestions"])


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


def test_get_available_agents_handler_groups_categories(monkeypatch):
    monkeypatch.setattr(
        flow_tools,
        "AGENT_REGISTRY",
        {
            "supervisor": {"category": "Routing"},
            "task_input": {"category": "Input"},
            "pdf_extraction": {"name": "PDF", "description": "Extract", "category": "Extraction", "requires_document": True},
            "gene": {"name": "Gene", "description": "Validate", "category": "Validation", "requires_document": False},
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
    assert result["step_count"] == 3
    assert result["disconnected_count"] == 1
    assert result["has_critical_issues"] is True
    assert result["critical_issue_count"] >= 2  # empty task instructions + parallel branching
    assert any(w["type"] == "CRITICAL" for w in result["validation_warnings"])
    assert any(w["type"] == "WARNING" for w in result["validation_warnings"])
    assert "Parallel flows not yet supported" in result["execution_order_markdown"]


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
