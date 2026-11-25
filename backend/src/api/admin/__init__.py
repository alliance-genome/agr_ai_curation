"""Admin API module for privileged operations.

This module provides admin-only endpoints for:
- Prompt template management (CRUD operations)
- Cache management
- Connection health diagnostics

Authorization: Email allowlist via ADMIN_EMAILS environment variable.
The minimal public banner status route (`GET /api/health/connections`) is
exported separately via `public_connections_router`.
"""

from .connections import public_router as public_connections_router
from .connections import router as connections_router
from .prompts import router as prompts_router

__all__ = ["connections_router", "public_connections_router", "prompts_router"]
