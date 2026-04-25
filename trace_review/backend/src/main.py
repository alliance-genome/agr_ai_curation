"""
Trace Review - FastAPI Backend
Main application entry point
"""
import logging
import os
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .logging_config import configure_logging, create_request_context_middleware
from .services.cache_manager import CacheManager
from .config import get_trace_review_preflight_diagnostics, validate_trace_source

configure_logging()

logger = logging.getLogger(__name__)


def _allowed_origins() -> list[str]:
    origins = {"http://localhost:3000", "http://localhost:3001"}
    frontend_url = os.getenv("FRONTEND_URL")
    if frontend_url:
        origins.add(frontend_url.rstrip("/"))
    return sorted(origins)


def _health_payload(app: FastAPI) -> tuple[dict, int]:
    cache_manager = getattr(app.state, "cache_manager", None)
    if cache_manager is None:
        return {
            "status": "starting",
            "message": "Trace Review API is still initializing",
            "cache_stats": None,
        }, 503

    return {
        "status": "ok",
        "message": "Trace Review API is running",
        "cache_stats": {
            "size": len(cache_manager.cache),
            "ttl_hours": cache_manager.ttl_hours
        }
    }, 200


def _preflight_payload(app: FastAPI, source: str) -> tuple[dict, int]:
    health_payload, health_status = _health_payload(app)
    diagnostics = get_trace_review_preflight_diagnostics(selected_source=source)
    source_selection = diagnostics["source_selection"]

    status = "ok"
    status_code = health_status
    next_actions = []

    if not source_selection["valid"]:
        status = "config_error"
        status_code = 400
        next_actions.append("Choose one of the supported source values: remote or local.")
    elif not source_selection["selected_ready"]:
        status = "config_error"
        status_code = 503
        selected = source_selection["selected"]
        missing = diagnostics["langfuse_sources"][selected]["missing_env"]
        next_actions.append(f"Set required Langfuse configuration for source '{selected}': {', '.join(missing)}.")

    if health_status != 200:
        if status == "ok":
            status = "starting"
        next_actions.append("Wait for TraceReview backend startup to complete, then retry the preflight.")

    return {
        "status": status,
        "service": "trace_review_backend",
        "message": "TraceReview preflight diagnostics are report-only; no services or production resources were mutated.",
        "backend": health_payload,
        "diagnostics": diagnostics,
        "next_actions": next_actions,
    }, status_code


def _health_response(app: FastAPI) -> JSONResponse:
    payload, status_code = _health_payload(app)
    return JSONResponse(content=payload, status_code=status_code)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup application resources"""
    # Startup
    ttl_hours = int(os.getenv("CACHE_TTL_HOURS", "1"))
    app.state.cache_manager = CacheManager(ttl_hours=ttl_hours)
    logger.info("Cache manager initialized with TTL: %s hours", ttl_hours)

    yield

    # Shutdown
    app.state.cache_manager.clear_all()
    logger.info("Cache cleared on shutdown")


# Create FastAPI app
app = FastAPI(
    title="Trace Review API",
    description="API for analyzing Langfuse traces",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request correlation middleware - adds request_id to all log lines.
create_request_context_middleware(app)


@app.get("/")
async def root():
    """Health check endpoint"""
    return _health_response(app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return _health_response(app)


@app.get("/health/preflight")
async def preflight_health(source: Literal["remote", "local"] = "remote"):
    """Report TraceReview preflight diagnostics without mutating services."""
    payload, status_code = _preflight_payload(app, source)
    return JSONResponse(content=payload, status_code=status_code)


@app.get("/health/langfuse")
async def langfuse_health(source: str = "remote"):
    """
    Check Langfuse connectivity for both remote and local sources.

    Use this to diagnose networking issues with Langfuse.
    """
    import requests
    from .config import get_trace_source_diagnostic, get_trace_source_runtime_config

    try:
        validate_trace_source(source)
    except ValueError as exc:
        return JSONResponse(
            content={
                "status": "config_error",
                "message": str(exc),
                "valid_sources": ["remote", "local"],
            },
            status_code=400,
        )

    results = {}

    for trace_source in ("remote", "local"):
        source_diagnostic = get_trace_source_diagnostic(trace_source)
        host = get_trace_source_runtime_config(trace_source)["host"] or ""
        results[trace_source] = {
            "host": source_diagnostic["host"],
            "credentials": source_diagnostic["credentials"],
            "ready": source_diagnostic["ready"],
            "missing_env": source_diagnostic["missing_env"],
        }

        if not host:
            results[trace_source]["health_check"] = "NOT_CONFIGURED"
            results[trace_source]["reachable"] = False
            continue

        try:
            resp = requests.get(f"{host.rstrip('/')}/api/public/health", timeout=5)
            results[trace_source]["health_check"] = "OK" if resp.status_code == 200 else f"ERROR: {resp.status_code}"
            results[trace_source]["reachable"] = True
        except requests.exceptions.Timeout:
            results[trace_source]["health_check"] = "TIMEOUT"
            results[trace_source]["reachable"] = False
        except Exception as e:
            results[trace_source]["health_check"] = f"ERROR: {str(e)}"
            results[trace_source]["reachable"] = False

    return {
        "status": "ok" if results[source]["reachable"] and results[source]["ready"] else "degraded",
        "selected_source": source,
        "langfuse": results,
        "troubleshooting": {
            "remote_unreachable": "Check VPN connection to EC2",
            "local_unreachable": "Ensure local Langfuse is running (docker compose up -d in main project)",
            "auth_error": "Verify LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env"
        }
    }


# Import and include routers
from .api import auth, traces, claude

app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(traces.router, prefix="/api/traces", tags=["Traces"])
app.include_router(claude.router, prefix="/api/claude/traces", tags=["Claude"])
