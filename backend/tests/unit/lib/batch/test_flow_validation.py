"""Tests for flow batch validation."""

from src.lib.batch.validation import validate_flow_for_batch


def _flow_ending_in(exit_agent_id: str) -> dict:
    is_formatter = exit_agent_id.endswith("_formatter")
    return {
        "version": "1.1",
        "entry_node_id": "1",
        "nodes": [
            {"id": "1", "type": "agent", "data": {"agent_id": "pdf_extraction"}},
            {"id": "2", "type": "agent", "data": {"agent_id": "gene"}},
            {
                "id": "3",
                "type": "output" if is_formatter else "agent",
                "data": {"agent_id": exit_agent_id},
            },
        ],
        "edges": [
            {"id": "e1", "source": "1", "target": "2"},
            {
                "id": "e2",
                "source": "2",
                "target": "3",
                "role": "output_attachment" if is_formatter else "control_flow",
            },
        ],
    }


class TestFlowValidation:
    """Tests for flow batch compatibility validation."""

    def test_valid_flow_passes(self):
        """Flow with PDF input and file output is valid."""
        flow_definition = {
            "version": "1.1",
            "entry_node_id": "1",
            "nodes": [
                {"id": "1", "type": "agent", "data": {"agent_id": "pdf_extraction", "output_key": "pdf_out"}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "agent", "data": {"agent_id": "gene", "output_key": "gene_out"}, "position": {"x": 0, "y": 100}},
                {"id": "3", "type": "output", "data": {"agent_id": "csv_formatter", "output_key": "csv_out"}, "position": {"x": 0, "y": 200}},
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2"},
                {"id": "e2", "source": "2", "target": "3", "role": "output_attachment"},
            ],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is True
        assert result.errors == []

    def test_valid_flow_with_initial_instructions(self):
        """Flow starting with initial instructions but containing PDF agent is valid."""
        flow_definition = {
            "version": "1.1",
            "entry_node_id": "1",
            "nodes": [
                {"id": "1", "type": "agent", "data": {"agent_id": "supervisor", "output_key": "init_out"}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "agent", "data": {"agent_id": "pdf_extraction", "output_key": "pdf_out"}, "position": {"x": 0, "y": 100}},
                {"id": "3", "type": "agent", "data": {"agent_id": "gene", "output_key": "gene_out"}, "position": {"x": 0, "y": 200}},
                {"id": "4", "type": "output", "data": {"agent_id": "csv_formatter", "output_key": "csv_out"}, "position": {"x": 0, "y": 300}},
            ],
            "edges": [
                {"id": "e1", "source": "1", "target": "2"},
                {"id": "e2", "source": "2", "target": "3"},
                {"id": "e3", "source": "3", "target": "4", "role": "output_attachment"},
            ],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is True
        assert result.errors == []

    def test_invalid_no_pdf_agent(self):
        """Flow without any PDF extraction agent is invalid."""
        flow_definition = {
            "version": "1.1",
            "entry_node_id": "1",
            "nodes": [
                {"id": "1", "type": "agent", "data": {"agent_id": "gene", "output_key": "gene_out"}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "output", "data": {"agent_id": "csv_formatter", "output_key": "csv_out"}, "position": {"x": 0, "y": 100}},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "1",
                    "target": "2",
                    "role": "output_attachment",
                }
            ],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is False
        assert any("PDF extraction" in e for e in result.errors)

    def test_invalid_chat_output(self):
        """Flow ending with chat output is invalid for batch."""
        flow_definition = {
            "version": "1.1",
            "entry_node_id": "1",
            "nodes": [
                {"id": "1", "type": "agent", "data": {"agent_id": "pdf_extraction", "output_key": "pdf_out"}, "position": {"x": 0, "y": 0}},
                {"id": "2", "type": "output", "data": {"agent_id": "chat_output", "output_key": "chat_out"}, "position": {"x": 0, "y": 100}},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "1",
                    "target": "2",
                    "role": "output_attachment",
                }
            ],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is False
        assert any("file output" in e.lower() for e in result.errors)

    def test_flow_ending_in_curation_handoff_is_valid_for_batch(self):
        result = validate_flow_for_batch(_flow_ending_in("curation_handoff"))

        assert result.valid is True
        assert result.errors == []

    def test_flow_ending_in_chat_output_still_rejected(self):
        result = validate_flow_for_batch(_flow_ending_in("chat_output_formatter"))

        assert result.valid is False
        assert any("Chat Output" in e for e in result.errors)

    def test_v11_mixed_file_and_chat_output_attachments_are_batch_valid(self):
        flow_definition = {
            "version": "1.1",
            "entry_node_id": "pdf",
            "nodes": [
                {"id": "pdf", "type": "agent", "data": {"agent_id": "pdf_extraction"}},
                {"id": "csv", "type": "output", "data": {"agent_id": "csv_formatter"}},
                {
                    "id": "chat",
                    "type": "output",
                    "data": {"agent_id": "chat_output_formatter"},
                },
            ],
            "edges": [
                {
                    "id": "csv-output",
                    "source": "pdf",
                    "target": "csv",
                    "role": "output_attachment",
                },
                {
                    "id": "chat-output",
                    "source": "pdf",
                    "target": "chat",
                    "role": "output_attachment",
                },
            ],
        }

        result = validate_flow_for_batch(flow_definition)

        assert result.valid is True
        assert result.errors == []
