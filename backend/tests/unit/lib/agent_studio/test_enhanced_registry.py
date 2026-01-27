# backend/tests/unit/lib/agent_studio/test_enhanced_registry.py
"""Tests for enhanced AGENT_REGISTRY with frontend and batching fields."""
import pytest


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
