"""Admin API module for privileged operations.

This module provides admin-only endpoints for:
- Prompt template management (CRUD operations)
- Cache management

Authorization: Email allowlist via ADMIN_EMAILS environment variable.
"""

from .prompts import router as prompts_router

__all__ = ["prompts_router"]
