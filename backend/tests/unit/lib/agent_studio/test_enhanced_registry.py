# backend/tests/unit/lib/agent_studio/test_enhanced_registry.py
"""Tests for enhanced AGENT_REGISTRY with frontend and batching fields."""


TARGET_VALIDATION_AGENT_ICONS = {
    "agm_validation": "🧬",
    "controlled_vocabulary_validation": "🏷️",
    "data_provider_validation": "🏢",
    "experimental_condition_validation": "🧪",
    "ontology_term_validation": "🔎",
    "reference_validation": "📚",
    "subject_entity_validation": "🎯",
}


def _documentation_strings(value):
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for child in value.values():
            yield from _documentation_strings(child)
        return
    if isinstance(value, list):
        for child in value:
            yield from _documentation_strings(child)


class TestEnhancedRegistry:
    """Tests for AGENT_REGISTRY frontend and batching enhancements."""

    def test_all_agents_have_frontend_metadata(self):
        """Every agent should have frontend metadata with icon."""
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        for agent_id, config in AGENT_REGISTRY.items():
            frontend = config.get("frontend")
            assert frontend is not None, f"{agent_id}: missing 'frontend' key"
            assert "icon" in frontend, f"{agent_id}: frontend missing 'icon'"
            assert "show_in_palette" in frontend, f"{agent_id}: frontend missing 'show_in_palette'"

    def test_frontend_icons_are_strings(self):
        """Frontend icons should be non-empty strings (emoji or icon name)."""
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        for agent_id, config in AGENT_REGISTRY.items():
            frontend = config.get("frontend", {})
            icon = frontend.get("icon")
            assert isinstance(icon, str), f"{agent_id}: icon should be string, got {type(icon)}"
            assert len(icon) > 0, f"{agent_id}: icon should not be empty"

    def test_routing_agents_hidden_from_palette(self):
        """Routing agents (supervisor) should not appear in palette."""
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        # Supervisor is the main routing agent
        supervisor = AGENT_REGISTRY.get("supervisor")
        if supervisor:
            frontend = supervisor.get("frontend", {})
            assert frontend.get("show_in_palette") is False, \
                "Supervisor should be hidden from palette"

    def test_input_nodes_hidden_from_palette(self):
        """Input nodes (task_input) should not appear in palette."""
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        task_input = AGENT_REGISTRY.get("task_input")
        if task_input:
            frontend = task_input.get("frontend", {})
            assert frontend.get("show_in_palette") is False, \
                "task_input should be hidden from palette"

    def test_validation_agents_visible_in_palette(self):
        """Validation agents should appear in palette."""
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        validation_agents = ["gene", "allele", "disease", "chemical"]
        for agent_id in validation_agents:
            if agent_id in AGENT_REGISTRY:
                frontend = AGENT_REGISTRY[agent_id].get("frontend", {})
                assert frontend.get("show_in_palette") is True, \
                    f"{agent_id} should be visible in palette"

    def test_curation_prep_hidden_from_palette(self):
        """Curation prep is the internal handoff engine, not a palette choice.

        As of 0.7.1, curation_handoff is the single curator-facing terminal for
        getting data to the curation system; it runs curation_prep internally,
        so curation_prep is hidden from the Flow Builder palette.
        """
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        curation_prep = AGENT_REGISTRY.get("curation_prep")
        assert curation_prep is not None
        frontend = curation_prep.get("frontend", {})
        assert frontend.get("show_in_palette") is False

    def test_newer_validation_agents_have_browser_metadata(self):
        """Newer validation agents should render useful Agent Browser tabs."""
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        for agent_id, expected_icon in TARGET_VALIDATION_AGENT_ICONS.items():
            config = AGENT_REGISTRY[agent_id]
            assert config["frontend"]["icon"] == expected_icon

            documentation = config.get("documentation")
            assert documentation is not None, f"{agent_id}: missing documentation"
            assert documentation.get("summary"), f"{agent_id}: missing summary"
            assert len(documentation.get("capabilities", [])) >= 3, (
                f"{agent_id}: expected at least three capabilities"
            )
            for capability in documentation["capabilities"]:
                assert capability.get("example_query"), (
                    f"{agent_id}: capability missing query language"
                )
                assert capability.get("example_result"), (
                    f"{agent_id}: capability missing result language"
                )
            assert documentation.get("data_sources"), (
                f"{agent_id}: missing data sources"
            )
            assert len(documentation.get("limitations", [])) >= 4, (
                f"{agent_id}: expected at least four guidance limitations"
            )

    def test_agent_browser_documentation_omits_retired_ontology_mapping_copy(self):
        """Agent Browser docs should reference typed ontology resolution only."""
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        browser_documentation = {
            agent_id: AGENT_REGISTRY[agent_id]["documentation"]
            for agent_id in [
                *TARGET_VALIDATION_AGENT_ICONS,
                "gene_expression_extraction",
                "phenotype_extractor",
            ]
        }

        documentation_text = "\n".join(
            _documentation_strings(browser_documentation)
        ).lower()
        assert "ontology mapping agent" not in documentation_text
        assert "ontology mapping" not in documentation_text
        assert "mapping route" not in documentation_text
        assert "old mapping" not in documentation_text
