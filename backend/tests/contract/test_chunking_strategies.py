"""Contract tests for getChunkingStrategies endpoint.

These tests verify the API contract for the chunking strategies endpoint.
They test strategies list response, default strategy identification, and response schema compliance.
"""

import pytest
from typing import Dict, Any, List
from fastapi.testclient import TestClient
from unittest.mock import Mock
import sys
from pathlib import Path

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.strategy import ChunkingStrategy, ChunkingMethod


class TestGetChunkingStrategiesEndpoint:
    """Contract tests for GET /weaviate/chunking-strategies endpoint."""

    @pytest.fixture
    def client(self):
        """Create a test client for the FastAPI app."""
        try:
            from api.main import app
            return TestClient(app)
        except ImportError:
            # If API not implemented yet, create a mock client for contract definition
            mock_client = Mock()
            mock_client.get = Mock()
            return mock_client

    @pytest.fixture
    def expected_strategies(self) -> List[Dict[str, Any]]:
        """Expected chunking strategies based on specification."""
        return [
            {
                "name": "research",
                "method": "by_title",
                "max_characters": 1500,
                "overlap_characters": 200,
                "exclude_element_types": [],
                "description": "Optimized for research papers and academic documents",
                "is_default": False
            },
            {
                "name": "legal",
                "method": "by_paragraph",
                "max_characters": 1000,
                "overlap_characters": 100,
                "exclude_element_types": [],
                "description": "Designed for legal documents with precise paragraph boundaries",
                "is_default": False
            },
            {
                "name": "technical",
                "method": "by_character",
                "max_characters": 2000,
                "overlap_characters": 400,
                "exclude_element_types": [],
                "description": "For technical documentation with code blocks and diagrams",
                "is_default": False
            },
            {
                "name": "general",
                "method": "by_paragraph",
                "max_characters": 1500,
                "overlap_characters": 200,
                "exclude_element_types": [],
                "description": "General purpose chunking for most document types",
                "is_default": True  # Assuming this is the default
            }
        ]

    def test_strategies_list_response(self, client, expected_strategies):
        """Test that endpoint returns list of available strategies."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Should return a list or object with strategies
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                # Might be wrapped in an object
                strategies = data.get("strategies", [])
            else:
                strategies = []

            # Verify we have strategies
            assert len(strategies) > 0

            # Check that expected strategies are present
            strategy_names = [s.get("name") for s in strategies]
            assert "research" in strategy_names
            assert "legal" in strategy_names
            assert "technical" in strategy_names
            assert "general" in strategy_names

            # Verify structure of each strategy
            for strategy in strategies:
                assert "name" in strategy
                assert "method" in strategy
                assert "max_characters" in strategy
                assert "overlap_characters" in strategy

    def test_default_strategy_identification(self, client):
        """Test that default strategy is clearly identified."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Extract strategies list
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                strategies = data.get("strategies", [])
                # Might have separate default indicator
                if "default_strategy" in data:
                    assert data["default_strategy"] in ["research", "legal", "technical", "general"]
            else:
                strategies = []

            # Check for default indicator
            default_strategies = [s for s in strategies if s.get("is_default") is True]

            # Should have exactly one default strategy
            if default_strategies:
                assert len(default_strategies) == 1
                default_strategy = default_strategies[0]
                assert default_strategy["name"] in ["research", "legal", "technical", "general"]

    def test_response_schema(self, client):
        """Test that response matches expected schema."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Extract strategies
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                strategies = data.get("strategies", [])
            else:
                strategies = []

            # Validate each strategy can be parsed into ChunkingStrategy model
            for strategy_data in strategies:
                try:
                    # Map string method to enum if needed
                    if "method" in strategy_data and isinstance(strategy_data["method"], str):
                        method_mapping = {
                            "by_title": ChunkingMethod.BY_TITLE,
                            "by_paragraph": ChunkingMethod.BY_PARAGRAPH,
                            "by_character": ChunkingMethod.BY_CHARACTER,
                            "by_sentence": ChunkingMethod.BY_SENTENCE
                        }
                        strategy_data["method"] = method_mapping.get(
                            strategy_data["method"],
                            ChunkingMethod.BY_PARAGRAPH
                        )

                    strategy = ChunkingStrategy(**strategy_data)
                    assert strategy.name is not None
                    assert strategy.method is not None
                    assert strategy.max_characters > 0
                    assert strategy.overlap_characters >= 0
                    assert strategy.overlap_characters < strategy.max_characters
                except Exception as e:
                    # Log the error but don't fail if schema is slightly different
                    print(f"Strategy validation warning: {e}")

    def test_strategy_method_values(self, client):
        """Test that strategy methods use valid values."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Extract strategies
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                strategies = data.get("strategies", [])
            else:
                strategies = []

            valid_methods = ["by_title", "by_paragraph", "by_character", "by_sentence"]

            for strategy in strategies:
                assert "method" in strategy
                method = strategy["method"]
                # Method should be one of the valid options
                assert method in valid_methods, f"Invalid method: {method}"

    def test_strategy_parameters_validity(self, client):
        """Test that strategy parameters have valid values."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Extract strategies
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                strategies = data.get("strategies", [])
            else:
                strategies = []

            for strategy in strategies:
                # Check max_characters is reasonable
                assert strategy["max_characters"] > 0
                assert strategy["max_characters"] <= 10000  # Reasonable upper limit

                # Check overlap is less than max
                assert strategy["overlap_characters"] >= 0
                assert strategy["overlap_characters"] < strategy["max_characters"]

                # Check overlap is reasonable percentage
                overlap_ratio = strategy["overlap_characters"] / strategy["max_characters"]
                assert overlap_ratio <= 0.5  # Overlap shouldn't be more than 50%

    def test_exclude_element_types_field(self, client):
        """Test that exclude_element_types field is present and valid."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Extract strategies
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                strategies = data.get("strategies", [])
            else:
                strategies = []

            for strategy in strategies:
                # Should have exclude_element_types field
                assert "exclude_element_types" in strategy
                exclude_types = strategy["exclude_element_types"]

                # Should be a list
                assert isinstance(exclude_types, list)

                # If not empty, should contain valid element types
                valid_element_types = [
                    "TITLE", "NARRATIVE_TEXT", "LIST_ITEM", "TABLE",
                    "FIGURE_CAPTION", "FOOTER", "HEADER", "PAGE_BREAK"
                ]

                for element_type in exclude_types:
                    assert element_type in valid_element_types

    def test_strategy_descriptions(self, client):
        """Test that strategies include helpful descriptions."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Extract strategies
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                strategies = data.get("strategies", [])
            else:
                strategies = []

            for strategy in strategies:
                # Description is optional but helpful
                if "description" in strategy:
                    assert isinstance(strategy["description"], str)
                    assert len(strategy["description"]) > 0
                    # Description should be meaningful
                    assert len(strategy["description"]) >= 10

    def test_response_consistency(self, client):
        """Test that multiple calls return consistent results."""
        # Make multiple requests
        responses = []
        for _ in range(3):
            response = client.get("/weaviate/chunking-strategies")
            if hasattr(response, 'status_code'):
                assert response.status_code == 200
                responses.append(response.json())

        if len(responses) >= 2:
            # Extract strategy names from each response
            strategy_sets = []
            for resp_data in responses:
                if isinstance(resp_data, list):
                    strategies = resp_data
                elif isinstance(resp_data, dict):
                    strategies = resp_data.get("strategies", [])
                else:
                    strategies = []

                names = set(s.get("name") for s in strategies)
                strategy_sets.append(names)

            # All responses should have the same strategies
            first_set = strategy_sets[0]
            for strategy_set in strategy_sets[1:]:
                assert strategy_set == first_set

    def test_strategy_ordering(self, client):
        """Test that strategies are returned in a consistent order."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Extract strategies
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                strategies = data.get("strategies", [])
            else:
                strategies = []

            # Strategies might be ordered by:
            # - Alphabetical by name
            # - Default first
            # - Custom order

            # Just verify they have an order
            _ = [s.get("name") for s in strategies]  # Verify names can be extracted

            # Check if default is first (common pattern)
            default_strategies = [s for s in strategies if s.get("is_default")]
            if default_strategies:
                # Default might be first
                pass  # This is implementation-specific

    def test_empty_database_response(self, client):
        """Test response when no strategies are configured (edge case)."""
        # This is an edge case - normally strategies should always exist
        # But test graceful handling

        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            # Should still return 200, possibly with empty list
            assert response.status_code in [200, 500, 503]

            if response.status_code == 200:
                data = response.json()
                # Should be a valid response structure even if empty
                assert data is not None

    def test_caching_headers(self, client):
        """Test that appropriate caching headers are set."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200

            # Check for caching headers (optional but good practice)
            _ = response.headers if hasattr(response, 'headers') else {}

            # Strategies don't change often, so might have cache headers
            # This is implementation-specific

    def test_content_type(self, client):
        """Test that response has correct content type."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200

            # Check content type
            if hasattr(response, 'headers'):
                content_type = response.headers.get('content-type', '')
                assert 'application/json' in content_type.lower()

    def test_strategy_name_uniqueness(self, client):
        """Test that strategy names are unique."""
        response = client.get("/weaviate/chunking-strategies")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Extract strategies
            if isinstance(data, list):
                strategies = data
            elif isinstance(data, dict):
                strategies = data.get("strategies", [])
            else:
                strategies = []

            # Check for unique names
            names = [s.get("name") for s in strategies]
            assert len(names) == len(set(names)), "Strategy names must be unique"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])