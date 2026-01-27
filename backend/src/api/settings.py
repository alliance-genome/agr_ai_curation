"""Settings API endpoints for Weaviate configuration management."""

from fastapi import APIRouter, HTTPException, Body
from typing import Dict, Any
import logging

from ..models.api_schemas import (
    SettingsResponse,
    EmbeddingConfiguration,
    WeaviateSettings,
    AvailableModelsResponse,
    AvailableModel
)
from ..lib.weaviate_client.settings import (
    get_embedding_config,
    update_embedding_config,
    get_collection_settings,
    get_available_models
)
from .auth import get_auth_dependency

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")


@router.get("/settings", response_model=SettingsResponse)
async def get_settings_endpoint(user: Dict[str, Any] = get_auth_dependency()):
    """
    Get current Weaviate configuration settings.

    Returns embedding configuration, database settings, and available models.
    """
    try:
        embedding_config = await get_embedding_config()
        collection_settings = await get_collection_settings()
        models = await get_available_models()

        available_models = []
        for provider, model_list in models.items():
            available_models.append(
                AvailableModelsResponse(
                    provider=provider,
                    models=[
                        AvailableModel(name=m["name"], dimensions=m["dimensions"])
                        for m in model_list
                    ]
                )
            )

        return SettingsResponse(
            embedding=EmbeddingConfiguration(
                model_provider=embedding_config["provider"],
                model_name=embedding_config["model"],
                dimensions=embedding_config["dimensions"],
                batch_size=embedding_config.get("batch_size", 10)
            ),
            database=WeaviateSettings(
                collection_name=collection_settings["collection_name"],
                schema_version=collection_settings["schema_version"],
                replication_factor=collection_settings.get("replication_factor", 1),
                consistency=collection_settings.get("consistency", "eventual"),
                vector_index_type=collection_settings.get("vector_index_type", "hnsw")
            ),
            available_models=available_models
        )

    except Exception as e:
        logger.error(f"Error retrieving settings: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve settings: {str(e)}"
        )


@router.put("/settings")
async def update_settings_endpoint(
    embedding_config: EmbeddingConfiguration = Body(None),
    database_settings: WeaviateSettings = Body(None),
    user: Dict[str, Any] = get_auth_dependency()
) -> Dict[str, Any]:
    """
    Update Weaviate configuration settings.

    Allows updating embedding configuration and/or database settings.
    Changes may require re-embedding existing documents.
    """
    try:
        results = {
            "success": True,
            "updated": [],
            "warnings": []
        }

        if embedding_config:
            models = await get_available_models()
            provider_models = models.get(embedding_config.model_provider, [])
            model_names = [m["name"] for m in provider_models]

            if embedding_config.model_name not in model_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"Model {embedding_config.model_name} not available for provider {embedding_config.model_provider}"
                )

            await update_embedding_config({
                "provider": embedding_config.model_provider,
                "model": embedding_config.model_name,
                "dimensions": embedding_config.dimensions,
                "batch_size": embedding_config.batch_size
            })

            results["updated"].append("embedding_configuration")
            results["warnings"].append(
                "Embedding configuration updated. Existing documents may need re-embedding."
            )

        if database_settings:
            if database_settings.replication_factor != 1:
                results["warnings"].append(
                    "Replication factor changes require cluster restart to take effect."
                )

            results["updated"].append("database_settings")

        if not embedding_config and not database_settings:
            results["success"] = False
            results["warnings"].append("No settings provided to update")

        return results

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update settings: {str(e)}"
        )