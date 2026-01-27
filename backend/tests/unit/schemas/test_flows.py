"""Unit tests for FlowDefinition schema validation.

Tests the task_input node requirement and related validation.
"""
import pytest
from pydantic import ValidationError

from src.schemas.flows import FlowDefinition, FlowNode, FlowNodeData, FlowNodePosition


def make_task_input_node(node_id: str = "task_input_1", task_instructions: str = "Test task") -> dict:
    """Helper to create a valid task_input node dict."""
    return {
        "id": node_id,
        "type": "task_input",
        "position": {"x": 0, "y": 0},
        "data": {
            "agent_id": "task_input",
            "agent_display_name": "Initial Instructions",
            "task_instructions": task_instructions,
            "input_source": "user_query",
            "output_key": "task_input",
        }
    }


def make_agent_node(node_id: str, agent_id: str = "pdf", output_key: str = None) -> dict:
    """Helper to create a valid agent node dict."""
    return {
        "id": node_id,
        "type": "agent",
        "position": {"x": 100, "y": 100},
        "data": {
            "agent_id": agent_id,
            "agent_display_name": agent_id.replace("_", " ").title(),
            "input_source": "previous_output",
            "output_key": output_key or f"{agent_id}_output",
        }
    }


class TestFlowDefinitionTaskInputRequirement:
    """Tests for task_input node requirement in FlowDefinition."""

    def test_flow_definition_requires_task_input(self):
        """Flow without task_input node should raise ValidationError."""
        # Create flow with only agent nodes (no task_input)
        flow_data = {
            "version": "1.0",
            "nodes": [make_agent_node("n1", "pdf")],
            "edges": [],
            "entry_node_id": "n1",
        }

        with pytest.raises(ValidationError) as exc_info:
            FlowDefinition(**flow_data)

        # Check that the error message is user-friendly
        errors = exc_info.value.errors()
        assert len(errors) >= 1
        error_msg = errors[0]["msg"]
        assert "Task Input" in error_msg or "task_input" in error_msg

    def test_flow_definition_with_task_input_passes(self):
        """Flow with valid task_input node should pass validation."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("task_1", "Extract gene mentions"),
                make_agent_node("n1", "pdf", "pdf_output"),
            ],
            "edges": [{"id": "e1", "source": "task_1", "target": "n1"}],
            "entry_node_id": "task_1",
        }

        flow = FlowDefinition(**flow_data)
        assert len(flow.nodes) == 2
        assert flow.entry_node_id == "task_1"

    def test_task_input_must_have_instructions(self):
        """task_input node without instructions should fail validation."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("task_1", ""),  # Empty instructions
                make_agent_node("n1", "pdf", "pdf_output"),
            ],
            "edges": [{"id": "e1", "source": "task_1", "target": "n1"}],
            "entry_node_id": "task_1",
        }

        with pytest.raises(ValidationError) as exc_info:
            FlowDefinition(**flow_data)

        errors = exc_info.value.errors()
        assert len(errors) >= 1
        # The error should be about empty task_instructions
        error_msg = str(errors[0]["msg"]).lower()
        assert "task_instructions" in error_msg or "non-empty" in error_msg

    def test_task_input_whitespace_only_fails(self):
        """task_input node with whitespace-only instructions should fail."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("task_1", "   "),  # Whitespace only
                make_agent_node("n1", "pdf", "pdf_output"),
            ],
            "edges": [{"id": "e1", "source": "task_1", "target": "n1"}],
            "entry_node_id": "task_1",
        }

        with pytest.raises(ValidationError):
            FlowDefinition(**flow_data)

    def test_multiple_task_inputs_fails(self):
        """Flow with multiple task_input nodes should fail."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("task_1", "First task"),
                make_task_input_node("task_2", "Second task"),
            ],
            "edges": [],
            "entry_node_id": "task_1",
        }

        with pytest.raises(ValidationError) as exc_info:
            FlowDefinition(**flow_data)

        errors = exc_info.value.errors()
        # Should have error about unique output keys (both have same output_key)
        # or about multiple task_input nodes
        assert len(errors) >= 1

    def test_task_input_must_be_entry_node(self):
        """task_input node must be the entry_node_id."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("task_1", "Test task"),
                make_agent_node("n1", "pdf", "pdf_output"),
            ],
            "edges": [{"id": "e1", "source": "task_1", "target": "n1"}],
            "entry_node_id": "n1",  # Wrong - should be task_1
        }

        with pytest.raises(ValidationError) as exc_info:
            FlowDefinition(**flow_data)

        errors = exc_info.value.errors()
        assert len(errors) >= 1
        error_msg = str(errors[0]["msg"]).lower()
        assert "entry" in error_msg or "task_input" in error_msg

    def test_task_input_cannot_have_incoming_edges(self):
        """task_input node cannot have incoming edges."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("task_1", "Test task"),
                make_agent_node("n1", "pdf", "pdf_output"),
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "task_1"}],  # Wrong direction
            "entry_node_id": "task_1",
        }

        with pytest.raises(ValidationError) as exc_info:
            FlowDefinition(**flow_data)

        errors = exc_info.value.errors()
        assert len(errors) >= 1

    def test_task_input_none_instructions_fails(self):
        """task_input node with None instructions should fail."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                {
                    "id": "task_1",
                    "type": "task_input",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "agent_id": "task_input",
                        "agent_display_name": "Initial Instructions",
                        "task_instructions": None,  # Explicitly None
                        "input_source": "user_query",
                        "output_key": "task_input",
                    }
                },
                make_agent_node("n1", "pdf", "pdf_output"),
            ],
            "edges": [{"id": "e1", "source": "task_1", "target": "n1"}],
            "entry_node_id": "task_1",
        }

        with pytest.raises(ValidationError):
            FlowDefinition(**flow_data)

    def test_task_input_type_requires_matching_agent_id(self):
        """task_input type node must have agent_id='task_input'."""
        flow_data = {
            "version": "1.0",
            "nodes": [{
                "id": "task_1",
                "type": "task_input",
                "position": {"x": 0, "y": 0},
                "data": {
                    "agent_id": "pdf",  # Wrong agent_id for task_input type
                    "agent_display_name": "Wrong Agent",
                    "task_instructions": "Test task",
                    "input_source": "user_query",
                    "output_key": "task_input",
                }
            }],
            "edges": [],
            "entry_node_id": "task_1",
        }

        with pytest.raises(ValidationError) as exc_info:
            FlowDefinition(**flow_data)

        errors = exc_info.value.errors()
        assert len(errors) >= 1


class TestFlowDefinitionOtherValidations:
    """Tests for other FlowDefinition validations (to ensure they still work)."""

    def test_unique_node_ids(self):
        """Node IDs must be unique."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("duplicate_id", "Test task"),
                {
                    "id": "duplicate_id",  # Duplicate!
                    "type": "agent",
                    "position": {"x": 100, "y": 100},
                    "data": {
                        "agent_id": "pdf",
                        "agent_display_name": "PDF",
                        "input_source": "previous_output",
                        "output_key": "pdf_output",
                    }
                },
            ],
            "edges": [],
            "entry_node_id": "duplicate_id",
        }

        with pytest.raises(ValidationError):
            FlowDefinition(**flow_data)

    def test_unique_output_keys(self):
        """Output keys must be unique."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("task_1", "Test task"),
                {
                    "id": "n1",
                    "type": "agent",
                    "position": {"x": 100, "y": 100},
                    "data": {
                        "agent_id": "pdf",
                        "agent_display_name": "PDF",
                        "input_source": "previous_output",
                        "output_key": "task_input",  # Duplicate of task_input's output_key!
                    }
                },
            ],
            "edges": [],
            "entry_node_id": "task_1",
        }

        with pytest.raises(ValidationError):
            FlowDefinition(**flow_data)

    def test_entry_node_must_exist(self):
        """entry_node_id must reference an existing node."""
        flow_data = {
            "version": "1.0",
            "nodes": [make_task_input_node("task_1", "Test task")],
            "edges": [],
            "entry_node_id": "nonexistent",
        }

        with pytest.raises(ValidationError):
            FlowDefinition(**flow_data)

    def test_edge_nodes_must_exist(self):
        """Edge source and target must reference existing nodes."""
        flow_data = {
            "version": "1.0",
            "nodes": [
                make_task_input_node("task_1", "Test task"),
                make_agent_node("n1", "pdf", "pdf_output"),
            ],
            "edges": [{"id": "e1", "source": "task_1", "target": "nonexistent"}],
            "entry_node_id": "task_1",
        }

        with pytest.raises(ValidationError):
            FlowDefinition(**flow_data)
