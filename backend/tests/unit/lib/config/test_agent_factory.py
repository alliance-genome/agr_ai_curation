"""
Tests for agent_factory.py - convention-based factory discovery.

Tests the factory discovery, caching, and agent creation functions.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.lib.config.agent_factory import (
    get_agent_factory,
    get_factory_by_agent_id,
    create_agent,
    create_agent_by_id,
    list_available_factories,
    clear_factory_cache,
)


class TestGetAgentFactory:
    """Tests for get_agent_factory function."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_factory_cache()

    def test_returns_callable_for_known_agent(self):
        """Should return a callable factory for known agents."""
        factory = get_agent_factory("gene")
        assert factory is not None
        assert callable(factory)

    def test_returns_none_for_unknown_agent(self):
        """Should return None for agents that don't exist."""
        factory = get_agent_factory("nonexistent_agent_xyz")
        assert factory is None

    def test_returns_none_for_empty_string(self):
        """Should return None for empty folder name."""
        factory = get_agent_factory("")
        assert factory is None

    def test_caches_factory_on_first_call(self):
        """Should cache factory after first successful lookup."""
        # First call - should import module
        factory1 = get_agent_factory("gene")
        assert factory1 is not None

        # Second call - should use cache (same object)
        factory2 = get_agent_factory("gene")
        assert factory2 is factory1

    def test_cache_is_per_folder_name(self):
        """Different folder names should have separate cache entries."""
        factory_gene = get_agent_factory("gene")
        factory_allele = get_agent_factory("allele")

        assert factory_gene is not None
        assert factory_allele is not None
        assert factory_gene is not factory_allele

    def test_handles_import_error_gracefully(self):
        """Should return None when module import fails."""
        with patch("src.lib.config.agent_factory.importlib.import_module") as mock_import:
            mock_import.side_effect = ImportError("Module not found")
            clear_factory_cache()

            factory = get_agent_factory("fake_agent")
            assert factory is None

    def test_handles_generic_exception_gracefully(self):
        """Should return None and log warning on unexpected errors."""
        with patch("src.lib.config.agent_factory.importlib.import_module") as mock_import:
            mock_import.side_effect = RuntimeError("Unexpected error")
            clear_factory_cache()

            factory = get_agent_factory("broken_agent")
            assert factory is None


class TestGetFactoryByAgentId:
    """Tests for get_factory_by_agent_id function."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_factory_cache()

    def test_returns_factory_for_valid_agent_id(self):
        """Should return factory when agent_id exists in YAML."""
        factory = get_factory_by_agent_id("gene_validation")
        assert factory is not None
        assert callable(factory)

    def test_returns_none_for_invalid_agent_id(self):
        """Should return None for unknown agent_id."""
        factory = get_factory_by_agent_id("nonexistent_agent_id")
        assert factory is None

    def test_returns_none_for_empty_agent_id(self):
        """Should return None for empty agent_id."""
        factory = get_factory_by_agent_id("")
        assert factory is None

    def test_uses_folder_name_from_definition(self):
        """Should look up folder_name from agent definition."""
        # gene_validation has folder_name "gene"
        factory = get_factory_by_agent_id("gene_validation")
        direct_factory = get_agent_factory("gene")

        # Both should return the same factory
        assert factory is direct_factory


class TestCreateAgent:
    """Tests for create_agent function."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_factory_cache()

    def test_returns_none_for_unknown_folder(self):
        """Should return None when folder doesn't exist."""
        agent = create_agent("nonexistent_folder")
        assert agent is None

    def test_returns_none_when_factory_raises_exception(self):
        """Should return None and log error when factory fails."""
        with patch("src.lib.config.agent_factory.get_agent_factory") as mock_get:
            mock_factory = MagicMock(side_effect=ValueError("Factory error"))
            mock_get.return_value = mock_factory

            agent = create_agent("test_agent", some_param="value")
            assert agent is None

    def test_passes_kwargs_to_factory(self):
        """Should pass kwargs through to factory function."""
        with patch("src.lib.config.agent_factory.get_agent_factory") as mock_get:
            mock_factory = MagicMock(return_value="mock_agent")
            mock_get.return_value = mock_factory

            result = create_agent("test", param1="a", param2="b")

            mock_factory.assert_called_once_with(param1="a", param2="b")
            assert result == "mock_agent"


class TestCreateAgentById:
    """Tests for create_agent_by_id function."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_factory_cache()

    def test_returns_none_for_unknown_agent_id(self):
        """Should return None when agent_id doesn't exist."""
        agent = create_agent_by_id("nonexistent_id")
        assert agent is None

    def test_returns_none_when_factory_raises_exception(self):
        """Should return None and log error when factory fails."""
        with patch("src.lib.config.agent_factory.get_factory_by_agent_id") as mock_get:
            mock_factory = MagicMock(side_effect=TypeError("Bad args"))
            mock_get.return_value = mock_factory

            agent = create_agent_by_id("test_id", bad_param=True)
            assert agent is None


class TestListAvailableFactories:
    """Tests for list_available_factories function."""

    def setup_method(self):
        """Clear cache before each test."""
        clear_factory_cache()

    def test_returns_list(self):
        """Should return a list of folder names."""
        factories = list_available_factories()
        assert isinstance(factories, list)

    def test_includes_known_agents(self):
        """Should include agents that have factories."""
        factories = list_available_factories()

        # These agents should have factories
        assert "gene" in factories
        assert "allele" in factories
        assert "pdf" in factories

    def test_excludes_supervisor(self):
        """Should not include supervisor in the list."""
        factories = list_available_factories()
        assert "supervisor" not in factories

    def test_returns_sorted_list(self):
        """Should return alphabetically sorted list."""
        factories = list_available_factories()
        assert factories == sorted(factories)


class TestClearFactoryCache:
    """Tests for clear_factory_cache function."""

    def test_clears_cached_factories(self):
        """Should clear all cached factories."""
        # Populate cache
        factory1 = get_agent_factory("gene")
        assert factory1 is not None

        # Clear cache
        clear_factory_cache()

        # After clearing, a new import should happen
        # We can verify by checking that subsequent calls work
        factory2 = get_agent_factory("gene")
        assert factory2 is not None

    def test_cache_empty_after_clear(self):
        """Cache should be empty after clearing."""
        get_agent_factory("gene")
        get_agent_factory("allele")

        clear_factory_cache()

        # Access the cache directly to verify it's empty
        from src.lib.config import agent_factory
        assert len(agent_factory._factory_cache) == 0
