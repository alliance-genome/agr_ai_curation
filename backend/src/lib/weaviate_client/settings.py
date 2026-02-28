"""Settings management library for Weaviate configuration."""

import asyncio
import logging
import os
from typing import Any, Dict, List

from . import connection as connection_module

logger = logging.getLogger(__name__)

# Default embedding configurations
EMBEDDING_CONFIGS = {
    "openai": {
        "text-embedding-3-small": {"dimensions": 1536, "max_tokens": 8191},
        "text-embedding-3-large": {"dimensions": 3072, "max_tokens": 8191},
        "text-embedding-ada-002": {"dimensions": 1536, "max_tokens": 8191},
    },
    "cohere": {
        "embed-english-v3.0": {"dimensions": 1024, "max_tokens": 512},
        "embed-multilingual-v3.0": {"dimensions": 1024, "max_tokens": 512},
    },
    "huggingface": {
        "sentence-transformers/all-MiniLM-L6-v2": {"dimensions": 384, "max_tokens": 256},
        "sentence-transformers/all-mpnet-base-v2": {"dimensions": 768, "max_tokens": 384},
    },
}

# Current configuration (in-memory store for demo)
_current_config = {
    "embedding": {
        "modelProvider": "openai",
        "modelName": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        "dimensions": 1536,
        "batchSize": 100,
    },
    "database": {
        "collectionName": "PDFDocuments",
        "schemaVersion": "1.0.0",
        "replicationFactor": 1,
        "consistency": "eventual",
        "vectorIndexType": "hnsw",
    },
}


def _parse_embedding_update(config: Dict[str, Any]) -> tuple[str | None, str | None, int]:
    """Accept snake_case and camelCase payload variants."""
    provider = config.get("provider") or config.get("modelProvider")
    model_name = config.get("model") or config.get("modelName")
    batch_size = config.get("batch_size", config.get("batchSize", 100))
    return provider, model_name, batch_size


def get_embedding_config() -> Dict[str, Any]:
    """Get current embedding configuration."""
    return _current_config["embedding"].copy()


def update_embedding_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Update embedding configuration."""
    try:
        provider, model_name, batch_size = _parse_embedding_update(config)

        if provider not in EMBEDDING_CONFIGS:
            raise ValueError(f"Unsupported provider: {provider}")

        if model_name not in EMBEDDING_CONFIGS[provider]:
            raise ValueError(f"Unsupported model: {model_name} for provider {provider}")

        model_specs = EMBEDDING_CONFIGS[provider][model_name]

        _current_config["embedding"].update(
            {
                "modelProvider": provider,
                "modelName": model_name,
                "dimensions": model_specs["dimensions"],
                "batchSize": batch_size,
            }
        )

        logger.info("Updated embedding config: %s", _current_config["embedding"])

        return {
            "success": True,
            "message": "Embedding configuration updated successfully",
            "config": _current_config["embedding"],
        }

    except Exception as e:
        logger.error("Failed to update embedding config: %s", e)
        return {
            "success": False,
            "message": f"Failed to update embedding config: {e}",
            "error": {"code": "CONFIG_UPDATE_FAILED", "details": str(e)},
        }


def get_collection_settings() -> Dict[str, Any]:
    """Get current collection settings."""
    return _current_config["database"].copy()


def update_schema(schema_config: Dict[str, Any]) -> Dict[str, Any]:
    """Update Weaviate schema configuration."""
    connection = connection_module._connection
    if not connection:
        raise RuntimeError("No Weaviate connection established")

    with connection.session() as client:
        try:
            collection_name = schema_config.get(
                "collectionName", _current_config["database"]["collectionName"]
            )

            existing_schema = client.schema.get()
            collection_exists = any(
                cls.get("class") == collection_name for cls in existing_schema.get("classes", [])
            )

            if not collection_exists:
                schema = {
                    "class": collection_name,
                    "vectorizer": "text2vec-openai",
                    "moduleConfig": {
                        "text2vec-openai": {
                            "model": _current_config["embedding"]["modelName"],
                            "type": "text",
                        }
                    },
                    "properties": [
                        {
                            "name": "filename",
                            "dataType": ["text"],
                            "description": "Original PDF filename",
                        },
                        {
                            "name": "fileSize",
                            "dataType": ["int"],
                            "description": "File size in bytes",
                        },
                        {
                            "name": "creationDate",
                            "dataType": ["date"],
                            "description": "When document was added",
                        },
                        {
                            "name": "lastAccessedDate",
                            "dataType": ["date"],
                            "description": "Last access time",
                        },
                        {
                            "name": "processingStatus",
                            "dataType": ["text"],
                            "description": "Processing pipeline status",
                        },
                        {
                            "name": "embeddingStatus",
                            "dataType": ["text"],
                            "description": "Embedding completion status",
                        },
                        {
                            "name": "chunkCount",
                            "dataType": ["int"],
                            "description": "Number of chunks",
                        },
                        {
                            "name": "vectorCount",
                            "dataType": ["int"],
                            "description": "Number of vectors",
                        },
                        {
                            "name": "metadata",
                            "dataType": ["object"],
                            "description": "Additional metadata",
                        },
                    ],
                }

                client.schema.create_class(schema)
                logger.info("Created collection: %s", collection_name)

                chunk_schema = {
                    "class": "DocumentChunk",
                    "vectorizer": "text2vec-openai",
                    "moduleConfig": {
                        "text2vec-openai": {
                            "model": _current_config["embedding"]["modelName"],
                            "type": "text",
                        }
                    },
                    "properties": [
                        {
                            "name": "documentId",
                            "dataType": ["text"],
                            "description": "Parent document ID",
                        },
                        {
                            "name": "chunkIndex",
                            "dataType": ["int"],
                            "description": "Order within document",
                        },
                        {
                            "name": "content",
                            "dataType": ["text"],
                            "description": "Chunk text content",
                        },
                        {
                            "name": "elementType",
                            "dataType": ["text"],
                            "description": "Unstructured element type",
                        },
                        {
                            "name": "pageNumber",
                            "dataType": ["int"],
                            "description": "Source page number",
                        },
                        {
                            "name": "sectionTitle",
                            "dataType": ["text"],
                            "description": "Section heading (concatenated path for backward compatibility)",
                        },
                        {
                            "name": "parentSection",
                            "dataType": ["text"],
                            "description": "Top-level section name (e.g., Methods, Results, TITLE)",
                        },
                        {
                            "name": "subsection",
                            "dataType": ["text"],
                            "description": "Subsection name if applicable (null for top-level sections)",
                        },
                        {
                            "name": "isTopLevel",
                            "dataType": ["boolean"],
                            "description": "True if this is a major top-level section, False if subsection",
                        },
                        {
                            "name": "docItemProvenance",
                            "dataType": ["text"],
                            "description": "PDFX provenance entries (JSON string) - matches main.py schema",
                        },
                        {
                            "name": "metadata",
                            "dataType": ["text"],
                            "description": "Chunk metadata (JSON string) - matches main.py schema",
                        },
                    ],
                }

                client.schema.create_class(chunk_schema)
                logger.info("Created DocumentChunk collection")

            _current_config["database"].update(schema_config)

            return {
                "success": True,
                "message": "Schema updated successfully",
                "config": _current_config["database"],
            }

        except Exception as e:
            logger.error("Failed to update schema: %s", e)
            return {
                "success": False,
                "message": f"Failed to update schema: {e}",
                "error": {"code": "SCHEMA_UPDATE_FAILED", "details": str(e)},
            }


def get_available_models() -> List[Dict[str, Any]]:
    """Get list of available embedding models."""
    models = []
    for provider, provider_models in EMBEDDING_CONFIGS.items():
        for model_name, specs in provider_models.items():
            models.append(
                {
                    "provider": provider,
                    "modelName": model_name,
                    "dimensions": specs["dimensions"],
                    "maxTokens": specs.get("max_tokens", "unknown"),
                }
            )
    return models


async def get_embedding_config_async() -> Dict[str, Any]:
    """Async adapter for API endpoints."""
    config = get_embedding_config()
    return {
        "provider": config["modelProvider"],
        "model": config["modelName"],
        "dimensions": config["dimensions"],
        "batch_size": config.get("batchSize", 10),
    }


async def update_embedding_config_async(config: Dict[str, Any]) -> Dict[str, Any]:
    """Async adapter for API endpoints."""
    result = update_embedding_config(config)
    if not result.get("success"):
        raise RuntimeError(result["message"])
    return {"success": True, "message": "Configuration updated"}


async def get_collection_settings_async() -> Dict[str, Any]:
    """Async adapter for API endpoints."""
    settings = get_collection_settings()
    return {
        "collection_name": settings["collectionName"],
        "schema_version": settings["schemaVersion"],
        "replication_factor": settings.get("replicationFactor", 1),
        "consistency": settings.get("consistency", "eventual"),
        "vector_index_type": settings.get("vectorIndexType", "hnsw"),
    }


async def update_schema_async(schema_config: Dict[str, Any]) -> Dict[str, Any]:
    """Async adapter for API endpoints."""
    result = await asyncio.to_thread(update_schema, schema_config)
    if not result.get("success"):
        raise RuntimeError(result["message"])
    return {"success": True, "applied_changes": []}


async def get_available_models_async() -> Dict[str, List[Dict[str, Any]]]:
    """Async adapter for API endpoints."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for model in get_available_models():
        provider = model["provider"]
        grouped.setdefault(provider, []).append(
            {"name": model["modelName"], "dimensions": model["dimensions"]}
        )
    return grouped
