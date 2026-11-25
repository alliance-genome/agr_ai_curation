"""Admin and public APIs for connection health monitoring.

Provides endpoints for checking external service and runtime health:
- GET /api/health/connections - Public minimal status for the curator banner
- GET /api/admin/health/connections - Admin-only cached health of all configured services
- GET /api/admin/health/connections/{service_id} - Admin-only single service from that cache
- GET /api/admin/health/llm-providers - LLM provider and model configuration
- GET /api/admin/health/packages - Runtime package discovery and compatibility

Authorization:
- GET /api/health/connections is public and returns only non-sensitive overall
  status and plain unhealthy service descriptions for the curator banner.
- GET /api/admin/health/connections and /connections/{service_id} expose service
  URLs, internal service IDs, and sanitized error text, so they require admin
  authorization via ADMIN_EMAILS (`require_admin`).
- All three connection routes answer from the same bounded-age, per-worker
  snapshot; none starts an uncached probe per request.
- The llm-providers and packages routes describe how the system is configured
  and require login like the neighbouring protected routes.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.api.admin.auth import require_admin
from src.api.auth import get_auth_dependency

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/health", tags=["Admin - Health"])
public_router = APIRouter(prefix="/api/health", tags=["Health"])


# =============================================================================
# Response Models
# =============================================================================


class PublicUnhealthyServiceStatus(BaseModel):
    """Minimal non-sensitive status for one unhealthy service."""

    description: str
    required: bool


class PublicConnectionsHealthResponse(BaseModel):
    """Minimal public connection health status for the curator banner."""

    observed_at: str  # UTC sweep start; unchanged on cache hits
    status: str  # "healthy", "degraded", "unhealthy"
    required_healthy: bool
    unhealthy_services: List[PublicUnhealthyServiceStatus]


class ServiceHealthResponse(BaseModel):
    """Health status for a single service (admin diagnostics).

    Security Notes:
    - url field contains redacted URL (credentials replaced with ***)
    - last_error field is sanitized (URLs redacted, truncated if too long)
    """

    service_id: str
    description: str
    url: str  # This is the redacted display_url, NOT the actual URL
    required: bool
    is_healthy: Optional[bool]
    last_error: Optional[str]  # Sanitized - URLs redacted, length limited


class ConnectionsHealthResponse(BaseModel):
    """Health status for all services."""

    observed_at: str  # UTC sweep start; unchanged on cache hits
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
    optional_for_runtime: bool = False
    required_for_runtime: bool = False
    route_configured: bool = False
    route_available: bool = False
    mapped_model_ids: List[str]
    mapped_curator_visible_model_ids: List[str]
    supports_parallel_tool_calls: bool
    # "ready" | "missing_api_key" | "missing_base_url" |
    # "missing_api_key_and_base_url" | "unused"
    readiness: str


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


class PackageHealthItem(BaseModel):
    """Health status for one discovered runtime package."""

    package_id: str
    display_name: Optional[str] = None
    version: Optional[str] = None
    status: str  # "loaded" | "failed"
    package_path: str
    manifest_path: str
    reason: Optional[str] = None


class PackageHealthSummary(BaseModel):
    """Summary counts for runtime package diagnostics."""

    total_discovered: int
    loaded_count: int
    failed_count: int
    validation_error_count: int


class PackagesHealthResponse(BaseModel):
    """Health/diagnostics report for runtime packages."""

    status: str  # "healthy" | "degraded" | "unhealthy"
    validated_at: str
    packages_dir: str
    runtime_version: str
    supported_package_api_version: str
    validation_errors: List[str]
    summary: PackageHealthSummary
    loaded_packages: List[PackageHealthItem]
    failed_packages: List[PackageHealthItem]


# =============================================================================
# Shared Snapshot Helpers
# =============================================================================


async def _get_connections_snapshot(request: Request):
    """Return the shared cached connections health snapshot or raise 503."""
    from src.lib.config.connections_loader import is_initialized

    if not is_initialized():
        raise HTTPException(
            status_code=503,
            detail="Connections not loaded. Service may still be starting up.",
        )

    from src.lib.config.connections_monitor import SnapshotUnavailable

    try:
        return await request.app.state.connections_health_monitor.get()
    except SnapshotUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


def _summarize_connections_status(
    status_dict: Dict[str, Dict[str, Any]],
) -> tuple[str, bool]:
    """Compute overall health status and whether all required services are healthy."""
    # Required services must be explicitly healthy. Optional services that are
    # intentionally unconfigured report None and should not degrade overall health.
    required_healthy = all(
        s.get("is_healthy") is True
        for s in status_dict.values()
        if s.get("required")
    )
    optional_unhealthy = any(
        s.get("required") is False and s.get("is_healthy") is False
        for s in status_dict.values()
    )

    if not required_healthy:
        overall_status = "unhealthy"  # Required services are down or unconfigured
    elif optional_unhealthy:
        overall_status = "degraded"  # Optional configured services are down
    else:
        overall_status = "healthy"

    return overall_status, required_healthy


# =============================================================================
# Endpoints
# =============================================================================


@public_router.get(
    "/connections",
    response_model=PublicConnectionsHealthResponse,
    summary="Public minimal connection status for the curator banner",
    description="""
    Returns minimal, non-sensitive connection health status from the shared
    bounded-age, per-worker cached observation. Never starts an uncached probe
    and never exposes URLs, hostnames, ports, raw errors, or internal service IDs.
    """,
)
async def check_public_connections_status(
    request: Request,
) -> PublicConnectionsHealthResponse:
    """Return minimal public connection health status for the curator banner."""
    snapshot = await _get_connections_snapshot(request)
    status_dict = snapshot.services
    overall_status, required_healthy = _summarize_connections_status(status_dict)

    unhealthy_services = [
        PublicUnhealthyServiceStatus(
            description=str(service.get("description") or "").strip(),
            required=bool(service.get("required")),
        )
        for service in status_dict.values()
        if service.get("is_healthy") is False
        or (bool(service.get("required")) and service.get("is_healthy") is not True)
    ]

    return PublicConnectionsHealthResponse(
        observed_at=snapshot.observed_at,
        status=overall_status,
        required_healthy=required_healthy,
        unhealthy_services=unhealthy_services,
    )


@router.get(
    "/connections",
    response_model=ConnectionsHealthResponse,
    summary="Check health of all configured services",
    description="""
    Shares a bounded-age, per-worker observation of configured services.
    observed_at is the UTC sweep start, preserved on cache hits.
    The single-service route and public status route read the same observation.

    Requires admin authorization (ADMIN_EMAILS allowlist).

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
async def check_all_connections(
    request: Request,
    _admin: dict = Depends(require_admin),
) -> ConnectionsHealthResponse:
    """Check health of all configured external services (admin only)."""
    snapshot = await _get_connections_snapshot(request)
    status_dict = snapshot.services

    # Calculate summary statistics
    total = len(status_dict)
    healthy = sum(1 for s in status_dict.values() if s.get("is_healthy") is True)
    unhealthy = sum(1 for s in status_dict.values() if s.get("is_healthy") is False)
    unknown = sum(1 for s in status_dict.values() if s.get("is_healthy") is None)
    overall_status, required_healthy = _summarize_connections_status(status_dict)

    # Build response
    services = {
        sid: ServiceHealthResponse(**data) for sid, data in status_dict.items()
    }

    return ConnectionsHealthResponse(
        observed_at=snapshot.observed_at,
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
    Returns one service's entry from the same bounded-age, per-worker
    observation the summary route shares. It never starts a probe of its own;
    an expired observation is refreshed by one shared sweep, exactly as for
    the summary route.

    Requires admin authorization (ADMIN_EMAILS allowlist).
    """,
)
async def check_single_connection(
    service_id: str,
    request: Request,
    _admin: dict = Depends(require_admin),
) -> ServiceHealthResponse:
    """Return the cached health observation for one configured service (admin only)."""
    snapshot = await _get_connections_snapshot(request)

    service = snapshot.services.get(service_id)
    if service is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown service: {service_id}",
        )
    return ServiceHealthResponse(**service)


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
async def check_llm_providers(
    user: dict = get_auth_dependency(),
) -> LLMProvidersHealthResponse:
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


@router.get(
    "/packages",
    response_model=PackagesHealthResponse,
    summary="Check runtime package health",
    description="""
    Discovers installed runtime packages from the configured packages directory,
    validates package manifests and compatibility, and reports which packages loaded
    or failed with explicit reasons.
    """,
)
async def check_runtime_packages(
    user: dict = get_auth_dependency(),
) -> PackagesHealthResponse:
    """Check runtime package discovery and compatibility status."""
    from src.lib.packages.health import build_package_health_report

    report = build_package_health_report()
    return PackagesHealthResponse(
        status=report["status"],
        validated_at=report["validated_at"],
        packages_dir=report["packages_dir"],
        runtime_version=report["runtime_version"],
        supported_package_api_version=report["supported_package_api_version"],
        validation_errors=report["validation_errors"],
        summary=PackageHealthSummary(**report["summary"]),
        loaded_packages=[PackageHealthItem(**item) for item in report["loaded_packages"]],
        failed_packages=[PackageHealthItem(**item) for item in report["failed_packages"]],
    )
