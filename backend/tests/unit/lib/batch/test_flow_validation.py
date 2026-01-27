"""Tests for flow batch validation."""
import pytest

from src.lib.batch.validation import validate_flow_for_batch
from src.schemas.batch import BatchValidationResponse


class TestFlowValidation:
    """Tests for flow batch compatibility validation."""

    def test_valid_flow_passes(self):
        """Flow with PDF input and file output is valid."""
        flow_definition = {
            "version": "1.0",
            "entry_node_id": "1",
            "nodes": [
                {"id": "1", "type": "agent", "data": {"agent_id": "pdf", "output_key": "pdf_out"}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "agent", "data": {"agent_id": "gene", "output_key": "gene_out"}, "position": {"x": 0, "y": 100}},
                {"id": "3", "type": "agent", "data": {"agent_id": "csv_formatter", "output_key": "csv_out"}, "position": {"x": 0, "y": 200}},
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2"},
                {"id": "e2", "source": "2", "target": "3"},
            ],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is True
        assert result.errors == []

    def test_valid_flow_with_initial_instructions(self):
        """Flow starting with initial instructions but containing PDF agent is valid."""
        flow_definition = {
            "version": "1.0",
            "entry_node_id": "1",
            "nodes": [
                {"id": "1", "type": "agent", "data": {"agent_id": "supervisor", "output_key": "init_out"}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "agent", "data": {"agent_id": "pdf", "output_key": "pdf_out"}, "position": {"x": 0, "y": 100}},
                {"id": "3", "type": "agent", "data": {"agent_id": "gene", "output_key": "gene_out"}, "position": {"x": 0, "y": 200}},
                {"id": "4", "type": "agent", "data": {"agent_id": "csv_formatter", "output_key": "csv_out"}, "position": {"x": 0, "y": 300}},
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2"},
                {"id": "e2", "source": "2", "target": "3"},
                {"id": "e3", "source": "3", "target": "4"},
            ],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is True
        assert result.errors == []

    def test_invalid_no_pdf_agent(self):
        """Flow without any PDF extraction agent is invalid."""
        flow_definition = {
            "version": "1.0",
            "entry_node_id": "1",
            "nodes": [
                {"id": "1", "type": "agent", "data": {"agent_id": "gene", "output_key": "gene_out"}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "agent", "data": {"agent_id": "csv_formatter", "output_key": "csv_out"}, "position": {"x": 0, "y": 100}},
            ],
            "edges": [{"id": "e1", "source": "1", "target": "2"}],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is False
        assert any("PDF extraction" in e for e in result.errors)

    def test_invalid_chat_output(self):
        """Flow ending with chat output is invalid for batch."""
        flow_definition = {
            "version": "1.0",
            "entry_node_id": "1",
            "nodes": [
                {"id": "1", "type": "agent", "data": {"agent_id": "pdf", "output_key": "pdf_out"}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "agent", "data": {"agent_id": "chat_output", "output_key": "chat_out"}, "position": {"x": 0, "y": 100}},
            ],
            "edges": [{"id": "e1", "source": "1", "target": "2"}],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is False
        assert any("file output" in e.lower() for e in result.errors)
