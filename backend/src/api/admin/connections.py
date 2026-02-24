"""Admin API for connection health monitoring.

Provides endpoints for checking external service health:
- GET /admin/health/connections - Check health of all configured services
- GET /admin/health/connections/{service_id} - Check health of a specific service

Authorization: None required (health endpoints are public for monitoring).
This allows load balancers and monitoring systems to check service health.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/health", tags=["Admin - Health"])


# =============================================================================
# Response Models
# =============================================================================


class ServiceHealthResponse(BaseModel):
    """Health status for a single service.

    Security Notes:
    - url field contains redacted URL (credentials replaced with ***)
    - last_error field is sanitized (URLs redacted, truncated if too long)
    This is intentional to prevent information exposure on public endpoints.
    """

    service_id: str
    description: str
    url: str  # This is the redacted display_url, NOT the actual URL
    required: bool
    is_healthy: Optional[bool]
    last_error: Optional[str]  # Sanitized - URLs redacted, length limited


class ConnectionsHealthResponse(BaseModel):
    """Health status for all services."""

    status: str  # "healthy", "degraded", "unhealthy"
    total_services: int
    healthy_count: int
    unhealthy_count: int
    unknown_count: int
    required_healthy: bool
    services: Dict[str, ServiceHealthResponse]


class LLMProviderHealthItem(BaseModel):
    """Runtime readiness for one configured LLM provider."""

    provider_id: str
    driver: str
    api_mode: str
    api_key_env: str
    api_key_present: bool
    base_url_env: Optional[str]
    base_url_configured: bool
    default_for_runner: bool
    mapped_model_ids: List[str]
    mapped_curator_visible_model_ids: List[str]
    supports_parallel_tool_calls: bool
    readiness: str  # "ready" | "missing_api_key" | "unused"


class LLMModelHealthItem(BaseModel):
    """Model-to-provider mapping status for one configured model."""

    model_id: str
    provider_id: str
    provider_exists: bool
    curator_visible: bool


class LLMProviderHealthSummary(BaseModel):
    """Summary counts for LLM provider diagnostics."""

    provider_count: int
    model_count: int
    ready_provider_count: int
    missing_key_provider_count: int
    mapped_model_count: int


class LLMProvidersHealthResponse(BaseModel):
    """Health/diagnostics report for LLM provider + model configuration."""

    status: str  # "healthy" | "degraded" | "unhealthy"
    strict_mode: bool
    validated_at: str
    errors: List[str]
    warnings: List[str]
    providers: List[LLMProviderHealthItem]
    models: List[LLMModelHealthItem]
    summary: LLMProviderHealthSummary
    startup_report: Optional[Dict[str, Any]] = None


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "/connections",
    response_model=ConnectionsHealthResponse,
    summary="Check health of all configured services",
    description="""
    Performs health checks on all services defined in config/connections.yaml.

    Health checks are performed based on each service's configuration:
    - HTTP endpoints: Makes request to configured endpoint
    - Redis: Sends PING command
    - Postgres: Tests database connection

    The response includes:
    - Overall status (healthy/degraded/unhealthy)
    - Count of healthy/unhealthy services
    - Whether all required services are healthy
    - Detailed status for each service
    """,
)
async def check_all_connections() -> ConnectionsHealthResponse:
    """Check health of all configured external services."""
    from src.lib.config.connections_loader import (
        check_all_health,
        is_initialized,
    )

    if not is_initialized():
        raise HTTPException(
            status_code=503,
            detail="Connections not loaded. Service may still be starting up.",
        )

    # Perform health checks
    status_dict = await check_all_health()

    # Calculate summary statistics
    total = len(status_dict)
    healthy = sum(1 for s in status_dict.values() if s.get("is_healthy") is True)
    unhealthy = sum(1 for s in status_dict.values() if s.get("is_healthy") is False)
    unknown = sum(1 for s in status_dict.values() if s.get("is_healthy") is None)

    # Check if all required services are healthy
    required_healthy = all(
        s.get("is_healthy") is True
        for s in status_dict.values()
        if s.get("required")
    )

    # Determine overall status
    if unhealthy == 0 and unknown == 0:
        overall_status = "healthy"
    elif required_healthy:
        overall_status = "degraded"  # Some optional services unhealthy
    else:
        overall_status = "unhealthy"  # Required services are down

    # Build response
    services = {
        sid: ServiceHealthResponse(**data) for sid, data in status_dict.items()
    }

    return ConnectionsHealthResponse(
        status=overall_status,
        total_services=total,
        healthy_count=healthy,
        unhealthy_count=unhealthy,
        unknown_count=unknown,
        required_healthy=required_healthy,
        services=services,
    )


@router.get(
    "/connections/{service_id}",
    response_model=ServiceHealthResponse,
    summary="Check health of a specific service",
    description="""
    Performs a health check on a single service by its service_id.

    Returns detailed status including:
    - Current health status
    - Last error message (if any)
    - Service configuration
    """,
)
async def check_single_connection(service_id: str) -> ServiceHealthResponse:
    """Check health of a specific service."""
    from src.lib.config.connections_loader import (
        check_service_health,
        get_connection,
        is_initialized,
        sanitize_error_message,
    )

    if not is_initialized():
        raise HTTPException(
            status_code=503,
            detail="Connections not loaded. Service may still be starting up.",
        )

    conn = get_connection(service_id)
    if not conn:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown service: {service_id}",
        )

    # Perform health check
    await check_service_health(service_id)

    return ServiceHealthResponse(
        service_id=conn.service_id,
        description=conn.description,
        url=conn.display_url,  # Use display_url to prevent credential exposure
        required=conn.required,
        is_healthy=conn.is_healthy,
        last_error=sanitize_error_message(conn.last_error),  # Sanitize error messages
    )


@router.get(
    "/llm-providers",
    response_model=LLMProvidersHealthResponse,
    summary="Check LLM provider/model health",
    description="""
    Validates `config/providers.yaml` and `config/models.yaml` runtime contracts and
    reports provider readiness (API key/env availability, model mappings).

    This endpoint is read-only and safe for diagnostics:
    - It never returns secret values.
    - It reports env var names and presence booleans only.
    """,
)
async def check_llm_providers() -> LLMProvidersHealthResponse:
    """Check health and configuration status for LLM providers/models."""
    from src.lib.config.provider_validation import (
        build_provider_runtime_report,
        get_startup_provider_validation_report,
    )

    report = build_provider_runtime_report()
    startup_report = get_startup_provider_validation_report()

    return LLMProvidersHealthResponse(
        status=report["status"],
        strict_mode=report["strict_mode"],
        validated_at=report["validated_at"],
        errors=report["errors"],
        warnings=report["warnings"],
        providers=[LLMProviderHealthItem(**row) for row in report["providers"]],
        models=[LLMModelHealthItem(**row) for row in report["models"]],
        summary=LLMProviderHealthSummary(**report["summary"]),
        startup_report=startup_report,
    )
