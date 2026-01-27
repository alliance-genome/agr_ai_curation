"""Settings management library for Weaviate configuration."""

import logging
import os
from typing import Dict, Any, Optional, List

from .connection import _connection

logger = logging.getLogger(__name__)

# Default embedding configurations
EMBEDDING_CONFIGS = {
    "openai": {
        "text-embedding-3-small": {"dimensions": 1536, "max_tokens": 8191},
        "text-embedding-3-large": {"dimensions": 3072, "max_tokens": 8191},
        "text-embedding-ada-002": {"dimensions": 1536, "max_tokens": 8191}
    },
    "cohere": {
        "embed-english-v3.0": {"dimensions": 1024, "max_tokens": 512},
        "embed-multilingual-v3.0": {"dimensions": 1024, "max_tokens": 512}
    },
    "huggingface": {
        "sentence-transformers/all-MiniLM-L6-v2": {"dimensions": 384, "max_tokens": 256},
        "sentence-transformers/all-mpnet-base-v2": {"dimensions": 768, "max_tokens": 384}
    }
}

# Current configuration (in-memory store for demo)
_current_config = {
    "embedding": {
        "modelProvider": "openai",
        "modelName": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        "dimensions": 1536,
        "batchSize": 100
    },
    "database": {
        "collectionName": "PDFDocuments",
        "schemaVersion": "1.0.0",
        "replicationFactor": 1,
        "consistency": "eventual",
        "vectorIndexType": "hnsw"
    }
}


def get_embedding_config() -> Dict[str, Any]:
    """Get current embedding configuration.

    Returns:
        Dictionary with embedding configuration
    """
    return _current_config["embedding"].copy()


def update_embedding_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Update embedding configuration.

    Args:
        config: New embedding configuration

    Returns:
        Operation result dictionary
    """
    try:
        # Validate provider and model
        provider = config.get("modelProvider")
        model_name = config.get("modelName")

        if provider not in EMBEDDING_CONFIGS:
            raise ValueError(f"Unsupported provider: {provider}")

        if model_name not in EMBEDDING_CONFIGS[provider]:
            raise ValueError(f"Unsupported model: {model_name} for provider {provider}")

        # Get model specs
        model_specs = EMBEDDING_CONFIGS[provider][model_name]

        # Update configuration
        _current_config["embedding"].update({
            "modelProvider": provider,
            "modelName": model_name,
            "dimensions": model_specs["dimensions"],
            "batchSize": config.get("batchSize", 100)
        })

        logger.info(f"Updated embedding config: {_current_config['embedding']}")

        return {
            "success": True,
            "message": "Embedding configuration updated successfully",
            "config": _current_config["embedding"]
        }

    except Exception as e:
        logger.error(f"Failed to update embedding config: {e}")
        return {
            "success": False,
            "message": f"Failed to update embedding config: {e}",
            "error": {
                "code": "CONFIG_UPDATE_FAILED",
                "details": str(e)
            }
        }


def get_collection_settings() -> Dict[str, Any]:
    """Get current collection settings.

    Returns:
        Dictionary with collection settings
    """
    return _current_config["database"].copy()


def update_schema(schema_config: Dict[str, Any]) -> Dict[str, Any]:
    """Update Weaviate schema configuration.

    Args:
        schema_config: New schema configuration

    Returns:
        Operation result dictionary
    """
    if not _connection:
        raise RuntimeError("No Weaviate connection established")

    with _connection.session() as client:
        try:
            collection_name = schema_config.get("collectionName", _current_config["database"]["collectionName"])

            # Check if collection exists
            existing_schema = client.schema.get()
            collection_exists = any(
                cls.get("class") == collection_name
                for cls in existing_schema.get("classes", [])
            )

            if not collection_exists:
                # Create new collection with schema
                schema = {
                    "class": collection_name,
                    "vectorizer": "text2vec-openai",
                    "moduleConfig": {
                        "text2vec-openai": {
                            "model": _current_config["embedding"]["modelName"],
                            "type": "text"
                        }
                    },
                    "properties": [
                        {
                            "name": "filename",
                            "dataType": ["text"],
                            "description": "Original PDF filename"
                        },
                        {
                            "name": "fileSize",
                            "dataType": ["int"],
                            "description": "File size in bytes"
                        },
                        {
                            "name": "creationDate",
                            "dataType": ["date"],
                            "description": "When document was added"
                        },
                        {
                            "name": "lastAccessedDate",
                            "dataType": ["date"],
                            "description": "Last access time"
                        },
                        {
                            "name": "processingStatus",
                            "dataType": ["text"],
                            "description": "Processing pipeline status"
                        },
                        {
                            "name": "embeddingStatus",
                            "dataType": ["text"],
                            "description": "Embedding completion status"
                        },
                        {
                            "name": "chunkCount",
                            "dataType": ["int"],
                            "description": "Number of chunks"
                        },
                        {
                            "name": "vectorCount",
                            "dataType": ["int"],
                            "description": "Number of vectors"
                        },
                        {
                            "name": "metadata",
                            "dataType": ["object"],
                            "description": "Additional metadata"
                        }
                    ]
                }

                client.schema.create_class(schema)
                logger.info(f"Created collection: {collection_name}")

                # Create DocumentChunk collection
                chunk_schema = {
                    "class": "DocumentChunk",
                    "vectorizer": "text2vec-openai",
                    "moduleConfig": {
                        "text2vec-openai": {
                            "model": _current_config["embedding"]["modelName"],
                            "type": "text"
                        }
                    },
                    "properties": [
                        {
                            "name": "documentId",
                            "dataType": ["text"],
                            "description": "Parent document ID"
                        },
                        {
                            "name": "chunkIndex",
                            "dataType": ["int"],
                            "description": "Order within document"
                        },
                        {
                            "name": "content",
                            "dataType": ["text"],
                            "description": "Chunk text content"
                        },
                        {
                            "name": "elementType",
                            "dataType": ["text"],
                            "description": "Unstructured element type"
                        },
                        {
                            "name": "pageNumber",
                            "dataType": ["int"],
                            "description": "Source page number"
                        },
                        {
                            "name": "sectionTitle",
                            "dataType": ["text"],
                            "description": "Section heading (concatenated path for backward compatibility)"
                        },
                        {
                            "name": "parentSection",
                            "dataType": ["text"],
                            "description": "Top-level section name (e.g., Methods, Results, TITLE)"
                        },
                        {
                            "name": "subsection",
                            "dataType": ["text"],
                            "description": "Subsection name if applicable (null for top-level sections)"
                        },
                        {
                            "name": "isTopLevel",
                            "dataType": ["boolean"],
                            "description": "True if this is a major top-level section, False if subsection"
                        },
                        {
                            "name": "docItemProvenance",
                            "dataType": ["text"],
                            "description": "Docling provenance entries (JSON string) - matches main.py schema"
                        },
                        {
                            "name": "metadata",
                            "dataType": ["text"],
                            "description": "Chunk metadata (JSON string) - matches main.py schema"
                        }
                    ]
                }

                client.schema.create_class(chunk_schema)
                logger.info("Created DocumentChunk collection")

            # Update current config
            _current_config["database"].update(schema_config)

            return {
                "success": True,
                "message": "Schema updated successfully",
                "config": _current_config["database"]
            }

        except Exception as e:
            logger.error(f"Failed to update schema: {e}")
            return {
                "success": False,
                "message": f"Failed to update schema: {e}",
                "error": {
                    "code": "SCHEMA_UPDATE_FAILED",
                    "details": str(e)
                }
            }


def get_available_models() -> List[Dict[str, Any]]:
    """Get list of available embedding models.

    Returns:
        List of available models with their specifications
    """
    models = []
    for provider, provider_models in EMBEDDING_CONFIGS.items():
        for model_name, specs in provider_models.items():
            models.append({
                "provider": provider,
                "modelName": model_name,
                "dimensions": specs["dimensions"],
                "maxTokens": specs.get("max_tokens", "unknown")
            })
    return models

# Async wrapper functions for FastAPI endpoints
async def get_embedding_config() -> Dict[str, Any]:
    """Async wrapper for get_embedding_config."""
    # Mock response for now
    return {
        "provider": "openai",
        "model": "text-embedding-ada-002",
        "dimensions": 1536,
        "batch_size": 10
    }


async def update_embedding_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Async wrapper for update_embedding_config."""
    # Mock response for now
    return {
        "success": True,
        "message": "Configuration updated"
    }


async def get_collection_settings() -> Dict[str, Any]:
    """Async wrapper for get_collection_settings."""
    # Mock response for now
    return {
        "collection_name": "PDFDocuments",
        "schema_version": "1.0.0",
        "replication_factor": 1,
        "consistency": "eventual",
        "vector_index_type": "hnsw"
    }


async def update_schema(schema_config: Dict[str, Any]) -> Dict[str, Any]:
    """Async wrapper for update_schema."""
    # Mock response for now
    return {
        "success": True,
        "applied_changes": []
    }


async def get_available_models() -> Dict[str, List[Dict[str, Any]]]:
    """Async wrapper for get_available_models."""
    # Mock response for now
    return {
        "openai": [
            {"name": "text-embedding-ada-002", "dimensions": 1536},
            {"name": "text-embedding-3-small", "dimensions": 512},
            {"name": "text-embedding-3-large", "dimensions": 3072}
        ]
    }
