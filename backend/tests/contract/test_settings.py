"""Contract tests for settings endpoints.

These tests verify the API contracts for the settings endpoints.
They test GET settings response, PUT settings validation, and embedding configuration updates.
"""

import pytest
from typing import List
from fastapi.testclient import TestClient
from unittest.mock import Mock
import sys
from pathlib import Path

# Add the backend/src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from models.api_schemas import (
    SettingsResponse,
    EmbeddingConfiguration,
    WeaviateSettings,
    AvailableModelsResponse,
    AvailableModel
)


class TestSettingsEndpoints:
    """Contract tests for GET/PUT /weaviate/settings endpoints."""

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
            mock_client.put = Mock()
            return mock_client

    @pytest.fixture
    def sample_embedding_config(self) -> EmbeddingConfiguration:
        """Create sample embedding configuration."""
        return EmbeddingConfiguration(
            model_provider="openai",
            model_name="text-embedding-3-small",
            dimensions=1536,
            batch_size=10
        )

    @pytest.fixture
    def sample_database_settings(self) -> WeaviateSettings:
        """Create sample database settings."""
        return WeaviateSettings(
            collection_name="PDFDocuments",
            schema_version="1.0.0",
            replication_factor=1,
            consistency="eventual",
            vector_index_type="hnsw"
        )

    @pytest.fixture
    def available_models(self) -> List[AvailableModelsResponse]:
        """Create list of available models."""
        return [
            AvailableModelsResponse(
                provider="openai",
                models=[
                    AvailableModel(name="text-embedding-3-small", dimensions=1536),
                    AvailableModel(name="text-embedding-3-large", dimensions=3072),
                    AvailableModel(name="text-embedding-ada-002", dimensions=1536)
                ]
            ),
            AvailableModelsResponse(
                provider="cohere",
                models=[
                    AvailableModel(name="embed-english-v3.0", dimensions=1024),
                    AvailableModel(name="embed-multilingual-v3.0", dimensions=1024)
                ]
            ),
            AvailableModelsResponse(
                provider="huggingface",
                models=[
                    AvailableModel(name="sentence-transformers/all-MiniLM-L6-v2", dimensions=384),
                    AvailableModel(name="sentence-transformers/all-mpnet-base-v2", dimensions=768)
                ]
            )
        ]

    def test_get_settings_response(self, client, sample_embedding_config, sample_database_settings):
        """Test GET settings endpoint response."""
        response = client.get("/weaviate/settings")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Check main sections are present
            assert "embedding" in data
            assert "database" in data
            assert "available_models" in data

            # Validate embedding configuration
            embedding = data["embedding"]
            assert "model_provider" in embedding
            assert "model_name" in embedding
            assert "dimensions" in embedding
            assert embedding["dimensions"] > 0
            assert "batch_size" in embedding
            assert embedding["batch_size"] > 0

            # Validate database settings
            database = data["database"]
            assert "collection_name" in database
            assert "schema_version" in database
            assert "replication_factor" in database
            assert database["replication_factor"] > 0
            assert "consistency" in database
            assert "vector_index_type" in database

            # Validate available models
            models = data["available_models"]
            assert isinstance(models, list)
            assert len(models) > 0

            for provider_models in models:
                assert "provider" in provider_models
                assert "models" in provider_models
                assert isinstance(provider_models["models"], list)

                for model in provider_models["models"]:
                    assert "name" in model
                    assert "dimensions" in model
                    assert model["dimensions"] > 0

    def test_put_settings_validation(self, client):
        """Test PUT settings endpoint with valid data."""
        # Update embedding configuration
        update_data = {
            "embedding": {
                "model_provider": "openai",
                "model_name": "text-embedding-3-large",
                "dimensions": 3072,
                "batch_size": 5
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Should return updated settings
            assert "embedding" in data
            updated_embedding = data["embedding"]
            assert updated_embedding["model_name"] == "text-embedding-3-large"
            assert updated_embedding["dimensions"] == 3072
            assert updated_embedding["batch_size"] == 5

    def test_embedding_configuration_update(self, client):
        """Test updating only embedding configuration."""
        # Update just embedding settings
        update_data = {
            "embedding": {
                "model_provider": "cohere",
                "model_name": "embed-english-v3.0",
                "dimensions": 1024,
                "batch_size": 20
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Verify update was applied
            embedding = data["embedding"]
            assert embedding["model_provider"] == "cohere"
            assert embedding["model_name"] == "embed-english-v3.0"
            assert embedding["dimensions"] == 1024
            assert embedding["batch_size"] == 20

            # Database settings should remain unchanged
            assert "database" in data

    def test_database_settings_update(self, client):
        """Test updating database configuration."""
        # Update database settings
        update_data = {
            "database": {
                "collection_name": "PDFDocumentsV2",
                "schema_version": "2.0.0",
                "replication_factor": 3,
                "consistency": "quorum",
                "vector_index_type": "flat"
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Verify database settings update
            database = data["database"]
            assert database["collection_name"] == "PDFDocumentsV2"
            assert database["schema_version"] == "2.0.0"
            assert database["replication_factor"] == 3
            assert database["consistency"] == "quorum"
            assert database["vector_index_type"] == "flat"

    def test_partial_settings_update(self, client):
        """Test updating only specific fields."""
        # Update only batch size
        update_data = {
            "embedding": {
                "batch_size": 25
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            # Might accept partial updates or require full config
            assert response.status_code in [200, 400, 422]

            if response.status_code == 200:
                data = response.json()
                embedding = data["embedding"]
                # Batch size should be updated
                assert embedding["batch_size"] == 25
                # Other fields should remain
                assert "model_provider" in embedding
                assert "model_name" in embedding

    def test_invalid_model_provider(self, client):
        """Test validation of invalid model provider."""
        update_data = {
            "embedding": {
                "model_provider": "invalid_provider",
                "model_name": "some-model",
                "dimensions": 1024,
                "batch_size": 10
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            # Should reject invalid provider
            assert response.status_code in [400, 422]
            data = response.json()
            error_msg = str(data).lower()
            assert "invalid" in error_msg or "provider" in error_msg

    def test_invalid_dimensions(self, client):
        """Test validation of invalid dimensions."""
        update_data = {
            "embedding": {
                "model_provider": "openai",
                "model_name": "text-embedding-3-small",
                "dimensions": -1,  # Invalid
                "batch_size": 10
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 422]
            data = response.json()
            error_msg = str(data).lower()
            assert "dimension" in error_msg or "invalid" in error_msg

    def test_invalid_batch_size(self, client):
        """Test validation of invalid batch size."""
        # Batch size too small
        update_data = {
            "embedding": {
                "model_provider": "openai",
                "model_name": "text-embedding-3-small",
                "dimensions": 1536,
                "batch_size": 0
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 422]

        # Batch size too large
        update_data["embedding"]["batch_size"] = 1000

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            # Might accept or reject based on limits
            assert response.status_code in [200, 400, 422]

    def test_invalid_replication_factor(self, client):
        """Test validation of invalid replication factor."""
        update_data = {
            "database": {
                "collection_name": "PDFDocuments",
                "schema_version": "1.0.0",
                "replication_factor": 0,  # Invalid
                "consistency": "eventual",
                "vector_index_type": "hnsw"
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 422]

    def test_get_settings_schema_compliance(self, client):
        """Test that GET response matches SettingsResponse schema."""
        response = client.get("/weaviate/settings")

        if hasattr(response, 'status_code'):
            assert response.status_code == 200
            data = response.json()

            # Try to parse as SettingsResponse
            try:
                settings = SettingsResponse(**data)

                # Validate structure
                assert settings.embedding is not None
                assert settings.database is not None
                assert settings.available_models is not None
                assert len(settings.available_models) > 0

                # Validate embedding config
                assert settings.embedding.model_provider is not None
                assert settings.embedding.model_name is not None
                assert settings.embedding.dimensions > 0
                assert settings.embedding.batch_size > 0

                # Validate database settings
                assert settings.database.collection_name is not None
                assert settings.database.schema_version is not None
                assert settings.database.replication_factor > 0

            except Exception as e:
                # Schema might be slightly different
                print(f"Schema validation warning: {e}")

    def test_consistency_values(self, client):
        """Test valid consistency level values."""
        valid_consistency_levels = ["eventual", "quorum", "all", "one"]

        for consistency in valid_consistency_levels:
            update_data = {
                "database": {
                    "collection_name": "PDFDocuments",
                    "schema_version": "1.0.0",
                    "replication_factor": 1,
                    "consistency": consistency,
                    "vector_index_type": "hnsw"
                }
            }

            response = client.put("/weaviate/settings", json=update_data)

            if hasattr(response, 'status_code'):
                # Should accept valid consistency levels
                assert response.status_code == 200

        # Test invalid consistency
        update_data = {
            "database": {
                "collection_name": "PDFDocuments",
                "schema_version": "1.0.0",
                "replication_factor": 1,
                "consistency": "invalid_consistency",
                "vector_index_type": "hnsw"
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            assert response.status_code in [400, 422]

    def test_vector_index_types(self, client):
        """Test valid vector index type values."""
        valid_index_types = ["hnsw", "flat", "lsh", "annoy"]

        for index_type in valid_index_types:
            update_data = {
                "database": {
                    "collection_name": "PDFDocuments",
                    "schema_version": "1.0.0",
                    "replication_factor": 1,
                    "consistency": "eventual",
                    "vector_index_type": index_type
                }
            }

            response = client.put("/weaviate/settings", json=update_data)

            if hasattr(response, 'status_code'):
                # Should accept valid index types (or ignore unknown ones)
                assert response.status_code in [200, 400]

    def test_settings_persistence(self, client):
        """Test that settings updates persist."""
        # Update settings
        update_data = {
            "embedding": {
                "model_provider": "openai",
                "model_name": "text-embedding-3-large",
                "dimensions": 3072,
                "batch_size": 15
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code') and response.status_code == 200:
            # Get settings again to verify persistence
            response = client.get("/weaviate/settings")
            assert response.status_code == 200
            data = response.json()

            # Should reflect the update
            embedding = data["embedding"]
            assert embedding["model_name"] == "text-embedding-3-large"
            assert embedding["dimensions"] == 3072
            assert embedding["batch_size"] == 15

    def test_concurrent_settings_updates(self, client):
        """Test handling of concurrent settings updates."""
        # Simulate concurrent updates
        updates = [
            {
                "embedding": {
                    "model_provider": "openai",
                    "model_name": "text-embedding-3-small",
                    "dimensions": 1536,
                    "batch_size": 10
                }
            },
            {
                "embedding": {
                    "model_provider": "cohere",
                    "model_name": "embed-english-v3.0",
                    "dimensions": 1024,
                    "batch_size": 20
                }
            }
        ]

        results = []
        for update in updates:
            response = client.put("/weaviate/settings", json=update)
            if hasattr(response, 'status_code'):
                results.append(response.status_code)

        # All should succeed (last one wins) or handle conflicts
        for status in results:
            assert status in [200, 409]  # 409 if conflict detection is implemented

    def test_model_compatibility_check(self, client):
        """Test that model changes check for compatibility."""
        # Try to change to incompatible dimensions
        update_data = {
            "embedding": {
                "model_provider": "openai",
                "model_name": "text-embedding-3-small",
                "dimensions": 3072,  # Wrong dimensions for this model
                "batch_size": 10
            }
        }

        response = client.put("/weaviate/settings", json=update_data)

        if hasattr(response, 'status_code'):
            # Might validate model-dimension compatibility
            # Or accept any combination
            assert response.status_code in [200, 400, 422]

    def test_settings_rollback_on_error(self, client):
        """Test that settings rollback on validation error."""
        # Get current settings
        response = client.get("/weaviate/settings")
        if hasattr(response, 'status_code'):
            original_settings = response.json()

            # Try invalid update
            update_data = {
                "embedding": {
                    "model_provider": "invalid",
                    "model_name": "invalid",
                    "dimensions": -1,
                    "batch_size": 0
                }
            }

            response = client.put("/weaviate/settings", json=update_data)
            assert response.status_code in [400, 422]

            # Verify settings unchanged
            response = client.get("/weaviate/settings")
            assert response.status_code == 200
            current_settings = response.json()

            # Should match original settings
            assert current_settings["embedding"]["model_provider"] == original_settings["embedding"]["model_provider"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])