"""Tests for agent batch capabilities."""
import pytest

from src.lib.agent_studio.catalog_service import AGENT_REGISTRY, get_agent_by_id


class TestBatchCapabilities:
    """Tests for agent batch capability flags."""

    def test_pdf_agent_has_pdf_extraction_flag(self):
        """PDF agent should have batch_pdf_extraction capability."""
        agent = AGENT_REGISTRY.get("pdf")
        assert agent is not None
        assert "batch_capabilities" in agent
        assert "pdf_extraction" in agent["batch_capabilities"]

    def test_gene_expression_has_pdf_extraction_flag(self):
        """Gene expression agent should have batch_pdf_extraction capability."""
        agent = AGENT_REGISTRY.get("gene_expression")
        assert agent is not None
        assert "batch_capabilities" in agent
        assert "pdf_extraction" in agent["batch_capabilities"]

    def test_csv_formatter_has_file_output_flag(self):
        """CSV formatter should have file_output capability."""
        agent = AGENT_REGISTRY.get("csv_formatter")
        assert agent is not None
        assert "batch_capabilities" in agent
        assert "file_output" in agent["batch_capabilities"]

    def test_chat_output_has_chat_output_flag(self):
        """Chat output should have chat_output capability (not file_output)."""
        agent = AGENT_REGISTRY.get("chat_output")
        assert agent is not None
        assert "batch_capabilities" in agent
        assert "chat_output" in agent["batch_capabilities"]
        assert "file_output" not in agent["batch_capabilities"]
