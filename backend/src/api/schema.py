"""Schema API endpoints for Weaviate schema management."""

from fastapi import APIRouter, HTTPException, Body
from typing import Dict, Any
import logging

from ..lib.weaviate_client.settings import update_schema, get_collection_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")


@router.get("/schema")
async def get_schema_endpoint() -> Dict[str, Any]:
    """
    Get current Weaviate collection schema.

    Returns the complete schema definition including properties,
    vectorizers, and index configuration.
    """
    try:
        settings = await get_collection_settings()

        schema = {
            "collection": settings["collection_name"],
            "version": settings["schema_version"],
            "properties": [
                {
                    "name": "document_id",
                    "dataType": ["text"],
                    "description": "Unique identifier for the document",
                    "indexInverted": True
                },
                {
                    "name": "filename",
                    "dataType": ["text"],
                    "description": "Original PDF filename",
                    "indexInverted": True,
                    "tokenization": "field"
                },
                {
                    "name": "content",
                    "dataType": ["text"],
                    "description": "Chunk content text",
                    "indexInverted": True,
                    "tokenization": "word"
                },
                {
                    "name": "chunk_index",
                    "dataType": ["int"],
                    "description": "Sequential index of chunk within document"
                },
                {
                    "name": "page_number",
                    "dataType": ["int"],
                    "description": "PDF page number"
                },
                {
                    "name": "element_type",
                    "dataType": ["text"],
                    "description": "Type of content element",
                    "indexInverted": True
                },
                {
                    "name": "metadata",
                    "dataType": ["object"],
                    "description": "Additional chunk metadata",
                    "nestedProperties": [
                        {
                            "name": "section_title",
                            "dataType": ["text"]
                        },
                        {
                            "name": "doc_items",
                            "dataType": ["object"],
                            "nestedProperties": [
                                {"name": "element_id", "dataType": ["text"]},
                                {"name": "doc_item_label", "dataType": ["text"]},
                                {"name": "page", "dataType": ["int"]},
                                {
                                    "name": "bbox",
                                    "dataType": ["object"],
                                    "nestedProperties": [
                                        {"name": "left", "dataType": ["number"]},
                                        {"name": "top", "dataType": ["number"]},
                                        {"name": "right", "dataType": ["number"]},
                                        {"name": "bottom", "dataType": ["number"]},
                                        {"name": "coord_origin", "dataType": ["text"]}
                                    ]
                                }
                            ]
                        },
                        {
                            "name": "confidence_score",
                            "dataType": ["number"]
                        }
                    ]
                },
                {
                    "name": "embedding_status",
                    "dataType": ["text"],
                    "description": "Current embedding status"
                },
                {
                    "name": "vector_dimensions",
                    "dataType": ["int"],
                    "description": "Dimension of the vector embedding"
                },
                {
                    "name": "created_at",
                    "dataType": ["date"],
                    "description": "Timestamp of creation"
                },
                {
                    "name": "updated_at",
                    "dataType": ["date"],
                    "description": "Timestamp of last update"
                }
            ],
            "vectorizer": {
                "type": settings.get("vectorizer", "none"),
                "model": settings.get("embedding_model", "text-embedding-ada-002")
            },
            "vectorIndexConfig": {
                "distance": "cosine",
                "ef": 200,
                "efConstruction": 128,
                "maxConnections": 64,
                "dynamicEfMin": 100,
                "dynamicEfMax": 500,
                "dynamicEfFactor": 8,
                "vectorCacheMaxObjects": 1000000,
                "flatSearchCutoff": 40000
            },
            "invertedIndexConfig": {
                "cleanupIntervalSeconds": 60,
                "stopwords": {
                    "preset": "en"
                }
            },
            "replicationConfig": {
                "factor": settings.get("replication_factor", 1)
            }
        }

        return schema

    except Exception as e:
        logger.error('Error retrieving schema: %s', e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve schema: {str(e)}"
        )


@router.put("/schema")
async def update_schema_endpoint(
    schema_update: Dict[str, Any] = Body(...)
) -> Dict[str, Any]:
    """
    Update Weaviate collection schema.

    WARNING: Schema changes can be destructive and may require data migration.
    Some changes (like removing properties) may result in data loss.
    """
    try:
        if "properties" in schema_update:
            for prop in schema_update["properties"]:
                if prop.get("dataType") not in [
                    ["text"], ["int"], ["number"], ["boolean"],
                    ["date"], ["object"], ["text[]"], ["int[]"]
                ]:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid dataType for property {prop.get('name')}"
                    )

        if "vectorIndexConfig" in schema_update:
            vec_config = schema_update["vectorIndexConfig"]
            if vec_config.get("distance") not in ["cosine", "euclidean", "manhattan", "hamming"]:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid distance metric"
                )

        result = await update_schema(schema_update)

        return {
            "success": True,
            "message": "Schema updated successfully",
            "warnings": [
                "Schema changes may require re-indexing of existing data",
                "Some changes may be applied asynchronously"
            ],
            "applied_changes": result.get("applied_changes", [])
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Error updating schema: %s', e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update schema: {str(e)}"
        )
