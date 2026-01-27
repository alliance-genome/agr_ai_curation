"""
Trace Review - FastAPI Backend
Main application entry point
"""
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .services.cache_manager import CacheManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize and cleanup application resources"""
    # Startup
    ttl_hours = int(os.getenv("CACHE_TTL_HOURS", "1"))
    app.state.cache_manager = CacheManager(ttl_hours=ttl_hours)
    logger.info(f"Cache manager initialized with TTL: {ttl_hours} hours")

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
    allow_origins=["http://localhost:3001", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Health check endpoint"""
    cache_manager = app.state.cache_manager
    return {
        "status": "ok",
        "message": "Trace Review API is running",
        "cache_stats": {
            "size": len(cache_manager.cache),
            "ttl_hours": cache_manager.ttl_hours
        }
    }


@app.get("/health/langfuse")
async def langfuse_health():
    """
    Check Langfuse connectivity for both remote and local sources.

    Use this to diagnose networking issues with Langfuse.
    """
    import requests
    from .config import (
        get_langfuse_host, get_langfuse_public_key, get_langfuse_secret_key,
        get_langfuse_local_host, get_langfuse_local_public_key, get_langfuse_local_secret_key
    )

    results = {}

    # Check remote (EC2 via VPN)
    remote_host = get_langfuse_host()
    remote_pk = get_langfuse_public_key()
    remote_sk = get_langfuse_secret_key()

    results["remote"] = {
        "host": remote_host,
        "public_key": f"{remote_pk[:20]}..." if remote_pk else "NOT_SET",
        "secret_key": "***" if remote_sk else "NOT_SET",
    }

    try:
        resp = requests.get(f"{remote_host}/api/public/health", timeout=5)
        results["remote"]["health_check"] = "OK" if resp.status_code == 200 else f"ERROR: {resp.status_code}"
        results["remote"]["reachable"] = True
    except requests.exceptions.Timeout:
        results["remote"]["health_check"] = "TIMEOUT"
        results["remote"]["reachable"] = False
    except Exception as e:
        results["remote"]["health_check"] = f"ERROR: {str(e)}"
        results["remote"]["reachable"] = False

    # Check local (localhost)
    local_host = get_langfuse_local_host()
    local_pk = get_langfuse_local_public_key()
    local_sk = get_langfuse_local_secret_key()

    results["local"] = {
        "host": local_host,
        "public_key": f"{local_pk[:20]}..." if local_pk else "NOT_SET",
        "secret_key": "***" if local_sk else "NOT_SET",
    }

    try:
        resp = requests.get(f"{local_host}/api/public/health", timeout=5)
        results["local"]["health_check"] = "OK" if resp.status_code == 200 else f"ERROR: {resp.status_code}"
        results["local"]["reachable"] = True
    except requests.exceptions.Timeout:
        results["local"]["health_check"] = "TIMEOUT"
        results["local"]["reachable"] = False
    except Exception as e:
        results["local"]["health_check"] = f"ERROR: {str(e)}"
        results["local"]["reachable"] = False

    return {
        "status": "ok",
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
