"""Health check API endpoint for Weaviate Control Panel."""

from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from datetime import datetime
import logging
import os

from ..lib.weaviate_helpers import get_connection
from ..config import is_cognito_configured

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/weaviate")


@router.get("/health")
async def health_check_endpoint() -> Dict[str, Any]:
    """
    Check health status of Weaviate connection and service.

    Returns connection status, version information, and basic metrics.
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "Weaviate Control Panel API",
        "version": "0.1.0",
        "cognito_configured": is_cognito_configured(),
        "checks": {
            "api": "healthy",
            "weaviate": "unknown"
        },
        "details": {}
    }

    try:
        connection = get_connection()
        weaviate_health = await connection.health_check()

        if weaviate_health and weaviate_health.get("status") == "healthy":
            health_status["checks"]["weaviate"] = "healthy"
            health_status["details"]["weaviate"] = {
                "version": weaviate_health.get("version", "unknown"),
                "nodes": weaviate_health.get("nodes", 1),
                "collections": weaviate_health.get("collections", 0)
            }
        else:
            health_status["checks"]["weaviate"] = "unhealthy"
            health_status["status"] = "degraded"
            health_status["details"]["weaviate"] = {
                "error": "Failed to connect to Weaviate",
                "message": weaviate_health.get("message", "Connection failed")
            }

    except Exception as e:
        logger.error('Error checking Weaviate health: %s', e)
        health_status["checks"]["weaviate"] = "unhealthy"
        health_status["status"] = "degraded"
        health_status["details"]["weaviate"] = {
            "error": str(e)
        }

    health_status["details"]["environment"] = {
        "python_version": os.getenv("PYTHON_VERSION", "3.11+"),
        "docker": os.path.exists("/.dockerenv"),
        "debug_mode": os.getenv("DEBUG", "false").lower() == "true"
    }

    overall_unhealthy = any(
        status in ["unhealthy", "error"]
        for status in health_status["checks"].values()
    )

    if overall_unhealthy:
        health_status["status"] = "unhealthy"
        raise HTTPException(
            status_code=503,
            detail=health_status
        )

    return health_status


@router.get("/readiness")
async def readiness_check_endpoint() -> Dict[str, Any]:
    """
    Check if the service is ready to accept requests.

    More lightweight than health check, used by orchestrators.
    """
    try:
        connection = get_connection()
        is_connected = await connection.health_check()

        if not is_connected or is_connected.get("status") != "healthy":
            raise HTTPException(
                status_code=503,
                detail={
                    "ready": False,
                    "reason": "Weaviate connection not ready"
                }
            )

        return {
            "ready": True,
            "timestamp": datetime.utcnow().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error('Readiness check failed: %s', e)
        raise HTTPException(
            status_code=503,
            detail={
                "ready": False,
                "reason": str(e)
            }
        )