"""Admin API module for privileged operations.

This module provides admin-only endpoints for:
- Prompt template management (CRUD operations)
- Cache management
- Connection health monitoring

Authorization: Email allowlist via ADMIN_EMAILS environment variable.
Note: Health endpoints are public for monitoring systems.
"""

from .connections import router as connections_router
from .benchmarks import router as benchmarks_router
from .prompts import router as prompts_router

__all__ = ["benchmarks_router", "connections_router", "prompts_router"]
