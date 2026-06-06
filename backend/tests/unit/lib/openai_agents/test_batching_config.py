"""Tests for registry-driven batching config."""
from src.lib.openai_agents.streaming_tools import get_batching_config


class TestGetBatchingConfig:
    """Tests for get_batching_config function."""

    def test_get_batching_config_returns_dict(self):
        """get_batching_config should return a dict."""
        config = get_batching_config()
        assert isinstance(config, dict)

    def test_get_batching_config_has_gene_specialist(self):
        """Config should include gene specialist."""
        config = get_batching_config()
        assert "ask_gene_specialist" in config
        assert "entity" in config["ask_gene_specialist"]
        assert "example" in config["ask_gene_specialist"]

    def test_get_batching_config_excludes_non_batching_agents(self):
        """Agents without batching config should not appear."""
        config = get_batching_config()
        # PDF agent doesn't have batching
        assert "ask_pdf_extraction_specialist" not in config

    def test_get_batching_config_entries_are_registry_derived(self):
        """Generated config entries should expose the registry batching shape."""
        config = get_batching_config()

        for tool_name, entry in config.items():
            assert tool_name.startswith("ask_")
            assert entry["entity"]
            assert entry["example"]

    def test_get_batching_config_has_all_expected_tools(self):
        """Config should have all expected batching tools."""
        config = get_batching_config()
        expected_tools = [
            "ask_gene_specialist",
            "ask_allele_specialist",
            "ask_disease_specialist",
            "ask_chemical_specialist",
            "ask_ontology_term_validation_specialist",
            "ask_gene_ontology_specialist",
            "ask_go_annotations_specialist",
        ]
        for tool in expected_tools:
            assert tool in config, f"Missing {tool} in batching config"
