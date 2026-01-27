"""Contract tests for schema management endpoints."""

import pytest
from unittest.mock import patch, AsyncMock
from typing import Dict, Any


@pytest.fixture
def client(monkeypatch):
    """Create test client with mocked dependencies."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from fastapi.testclient import TestClient
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from main import app
    return TestClient(app)


@pytest.fixture
def mock_settings_functions():
    """Mock settings functions."""
    with patch("src.api.schema.get_collection_settings") as mock_get, \
         patch("src.api.schema.update_schema") as mock_update:
        yield mock_get, mock_update


@pytest.fixture
def sample_collection_settings():
    """Sample collection settings for testing."""
    return {
        "collection_name": "PDFDocuments",
        "schema_version": "1.0.0",
        "replication_factor": 1,
        "vectorizer": "none",
        "embedding_model": "text-embedding-ada-002"
    }


class TestGetSchemaEndpoint:
    """Tests for GET /weaviate/schema endpoint."""

    def test_get_schema_success(self, client, mock_settings_functions, sample_collection_settings):
        """Test successful schema retrieval."""
        mock_get, _ = mock_settings_functions
        mock_get.return_value = sample_collection_settings

        response = client.get("/weaviate/schema")
        assert response.status_code == 200

        data = response.json()
        assert data["collection"] == "PDFDocuments"
        assert data["version"] == "1.0.0"
        assert "properties" in data
        assert "vectorizer" in data
        assert "vectorIndexConfig" in data
        assert "invertedIndexConfig" in data
        assert "replicationConfig" in data

    def test_get_schema_properties_structure(self, client, mock_settings_functions, sample_collection_settings):
        """Test schema properties have correct structure."""
        mock_get, _ = mock_settings_functions
        mock_get.return_value = sample_collection_settings

        response = client.get("/weaviate/schema")
        assert response.status_code == 200

        properties = response.json()["properties"]
        assert isinstance(properties, list)
        assert len(properties) > 0

        # Check required properties exist
        property_names = {prop["name"] for prop in properties}
        required_props = {
            "document_id", "filename", "content", "chunk_index",
            "page_number", "element_type", "metadata", "embedding_status",
            "vector_dimensions", "created_at", "updated_at"
        }
        assert required_props.issubset(property_names)

        # Check property structure
        for prop in properties:
            assert "name" in prop
            assert "dataType" in prop
            assert "description" in prop
            assert isinstance(prop["dataType"], list)

    def test_get_schema_metadata_nested_properties(self, client, mock_settings_functions, sample_collection_settings):
        """Test metadata property has nested properties."""
        mock_get, _ = mock_settings_functions
        mock_get.return_value = sample_collection_settings

        response = client.get("/weaviate/schema")
        assert response.status_code == 200

        properties = response.json()["properties"]
        metadata_prop = next(p for p in properties if p["name"] == "metadata")

        assert metadata_prop["dataType"] == ["object"]
        assert "nestedProperties" in metadata_prop
        assert isinstance(metadata_prop["nestedProperties"], list)

        nested_names = {np["name"] for np in metadata_prop["nestedProperties"]}
        assert "section_title" in nested_names
        assert "doc_items" in nested_names
        assert "confidence_score" in nested_names

    def test_get_schema_vector_index_config(self, client, mock_settings_functions, sample_collection_settings):
        """Test vector index configuration structure."""
        mock_get, _ = mock_settings_functions
        mock_get.return_value = sample_collection_settings

        response = client.get("/weaviate/schema")
        assert response.status_code == 200

        vector_config = response.json()["vectorIndexConfig"]
        assert vector_config["distance"] == "cosine"
        assert "ef" in vector_config
        assert "efConstruction" in vector_config
        assert "maxConnections" in vector_config
        assert "dynamicEfMin" in vector_config
        assert "dynamicEfMax" in vector_config
        assert "dynamicEfFactor" in vector_config
        assert "vectorCacheMaxObjects" in vector_config
        assert "flatSearchCutoff" in vector_config

    def test_get_schema_inverted_index_config(self, client, mock_settings_functions, sample_collection_settings):
        """Test inverted index configuration."""
        mock_get, _ = mock_settings_functions
        mock_get.return_value = sample_collection_settings

        response = client.get("/weaviate/schema")
        assert response.status_code == 200

        inverted_config = response.json()["invertedIndexConfig"]
        assert "cleanupIntervalSeconds" in inverted_config
        assert inverted_config["cleanupIntervalSeconds"] == 60
        assert "stopwords" in inverted_config
        assert inverted_config["stopwords"]["preset"] == "en"

    def test_get_schema_replication_config(self, client, mock_settings_functions, sample_collection_settings):
        """Test replication configuration."""
        mock_get, _ = mock_settings_functions
        mock_get.return_value = sample_collection_settings

        response = client.get("/weaviate/schema")
        assert response.status_code == 200

        replication_config = response.json()["replicationConfig"]
        assert replication_config["factor"] == 1

    def test_get_schema_error_handling(self, client, mock_settings_functions):
        """Test error handling in get schema."""
        mock_get, _ = mock_settings_functions
        mock_get.side_effect = Exception("Database connection failed")

        response = client.get("/weaviate/schema")
        assert response.status_code == 500
        assert "Failed to retrieve schema" in response.json()["detail"]


class TestUpdateSchemaEndpoint:
    """Tests for PUT /weaviate/schema endpoint."""

    def test_update_schema_success(self, client, mock_settings_functions):
        """Test successful schema update."""
        _, mock_update = mock_settings_functions
        mock_update.return_value = {
            "applied_changes": ["Added property 'new_field'", "Updated vector index config"]
        }

        schema_update = {
            "properties": [
                {
                    "name": "new_field",
                    "dataType": ["text"],
                    "description": "New field for testing"
                }
            ]
        }

        response = client.put("/weaviate/schema", json=schema_update)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] == True
        assert "Schema updated successfully" in data["message"]
        assert len(data["warnings"]) > 0
        assert len(data["applied_changes"]) == 2

    def test_update_schema_invalid_datatype(self, client, mock_settings_functions):
        """Test schema update with invalid data type."""
        schema_update = {
            "properties": [
                {
                    "name": "invalid_field",
                    "dataType": ["invalid_type"],
                    "description": "Field with invalid type"
                }
            ]
        }

        response = client.put("/weaviate/schema", json=schema_update)
        assert response.status_code == 400
        assert "Invalid dataType" in response.json()["detail"]

    def test_update_schema_valid_datatypes(self, client, mock_settings_functions):
        """Test schema update with all valid data types."""
        _, mock_update = mock_settings_functions
        mock_update.return_value = {"applied_changes": []}

        valid_datatypes = [
            ["text"], ["int"], ["number"], ["boolean"],
            ["date"], ["object"], ["text[]"], ["int[]"]
        ]

        for datatype in valid_datatypes:
            schema_update = {
                "properties": [
                    {
                        "name": f"field_{datatype[0]}",
                        "dataType": datatype,
                        "description": f"Field of type {datatype}"
                    }
                ]
            }

            response = client.put("/weaviate/schema", json=schema_update)
            assert response.status_code == 200

    def test_update_schema_invalid_distance_metric(self, client, mock_settings_functions):
        """Test schema update with invalid distance metric."""
        schema_update = {
            "vectorIndexConfig": {
                "distance": "invalid_metric"
            }
        }

        response = client.put("/weaviate/schema", json=schema_update)
        assert response.status_code == 400
        assert "Invalid distance metric" in response.json()["detail"]

    def test_update_schema_valid_distance_metrics(self, client, mock_settings_functions):
        """Test schema update with valid distance metrics."""
        _, mock_update = mock_settings_functions
        mock_update.return_value = {"applied_changes": []}

        valid_metrics = ["cosine", "euclidean", "manhattan", "hamming"]

        for metric in valid_metrics:
            schema_update = {
                "vectorIndexConfig": {
                    "distance": metric
                }
            }

            response = client.put("/weaviate/schema", json=schema_update)
            assert response.status_code == 200

    def test_update_schema_warnings(self, client, mock_settings_functions):
        """Test that schema update includes appropriate warnings."""
        _, mock_update = mock_settings_functions
        mock_update.return_value = {"applied_changes": []}

        schema_update = {
            "properties": [
                {
                    "name": "test_field",
                    "dataType": ["text"],
                    "description": "Test field"
                }
            ]
        }

        response = client.put("/weaviate/schema", json=schema_update)
        assert response.status_code == 200

        data = response.json()
        warnings = data["warnings"]
        assert len(warnings) > 0
        assert any("re-indexing" in w for w in warnings)
        assert any("asynchronously" in w for w in warnings)

    def test_update_schema_complex_update(self, client, mock_settings_functions):
        """Test complex schema update with multiple changes."""
        _, mock_update = mock_settings_functions
        mock_update.return_value = {
            "applied_changes": [
                "Added property 'field1'",
                "Added property 'field2'",
                "Updated vector index config",
                "Updated inverted index config"
            ]
        }

        schema_update = {
            "properties": [
                {
                    "name": "field1",
                    "dataType": ["text"],
                    "description": "First new field",
                    "indexInverted": True,
                    "tokenization": "word"
                },
                {
                    "name": "field2",
                    "dataType": ["int[]"],
                    "description": "Second new field"
                }
            ],
            "vectorIndexConfig": {
                "distance": "euclidean",
                "ef": 300
            },
            "invertedIndexConfig": {
                "cleanupIntervalSeconds": 120
            }
        }

        response = client.put("/weaviate/schema", json=schema_update)
        assert response.status_code == 200

        data = response.json()
        assert data["success"] == True
        assert len(data["applied_changes"]) == 4

    def test_update_schema_error_handling(self, client, mock_settings_functions):
        """Test error handling in update schema."""
        _, mock_update = mock_settings_functions
        mock_update.side_effect = Exception("Schema migration failed")

        schema_update = {
            "properties": [
                {
                    "name": "test_field",
                    "dataType": ["text"],
                    "description": "Test field"
                }
            ]
        }

        response = client.put("/weaviate/schema", json=schema_update)
        assert response.status_code == 500
        assert "Failed to update schema" in response.json()["detail"]


class TestSchemaEndpointEdgeCases:
    """Edge case tests for schema endpoints."""

    def test_get_schema_custom_vectorizer(self, client, mock_settings_functions):
        """Test schema with custom vectorizer configuration."""
        mock_get, _ = mock_settings_functions
        mock_get.return_value = {
            "collection_name": "PDFDocuments",
            "schema_version": "1.0.0",
            "vectorizer": "text2vec-openai",
            "embedding_model": "text-embedding-3-large"
        }

        response = client.get("/weaviate/schema")
        assert response.status_code == 200

        data = response.json()
        assert data["vectorizer"]["type"] == "text2vec-openai"
        assert data["vectorizer"]["model"] == "text-embedding-3-large"

    def test_update_schema_empty_update(self, client, mock_settings_functions):
        """Test schema update with empty update object."""
        _, mock_update = mock_settings_functions
        mock_update.return_value = {"applied_changes": []}

        response = client.put("/weaviate/schema", json={})
        assert response.status_code == 200

        data = response.json()
        assert data["success"] == True
        assert len(data["applied_changes"]) == 0

    def test_update_schema_property_with_nested_objects(self, client, mock_settings_functions):
        """Test adding property with nested object structure."""
        _, mock_update = mock_settings_functions
        mock_update.return_value = {"applied_changes": ["Added complex property"]}

        schema_update = {
            "properties": [
                {
                    "name": "complex_metadata",
                    "dataType": ["object"],
                    "description": "Complex nested metadata",
                    "nestedProperties": [
                        {
                            "name": "level1",
                            "dataType": ["object"],
                            "nestedProperties": [
                                {
                                    "name": "level2",
                                    "dataType": ["text"]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        response = client.put("/weaviate/schema", json=schema_update)
        assert response.status_code == 200
