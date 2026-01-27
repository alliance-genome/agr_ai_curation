"""Weaviate helper functions for multi-tenancy operations.

Task: T025 - Create get_tenant_name() and get_user_collections() helpers
Provides tenant-scoped collection access with SDK-enforced isolation.

Requirements: FR-011, FR-012, FR-013 (user-specific data isolation)
"""

import logging
from typing import Tuple

from weaviate import WeaviateClient
from weaviate.collections.collection import Collection


logger = logging.getLogger(__name__)


# Re-export get_connection for test mocking
# Use late import to avoid circular dependencies during module initialization
def get_connection():
    """Get Weaviate connection (wrapper for test patching).

    This wrapper allows tests to patch 'src.lib.weaviate_helpers.get_connection'
    without causing circular import issues during module initialization.

    Tests should patch both:
    - src.services.user_service.get_connection (imported reference)
    - src.lib.weaviate_helpers.get_connection (this wrapper)
    """
    from src.lib.weaviate_client.connection import get_connection as _get_connection
    return _get_connection()


def get_tenant_name(user_id: str) -> str:
    """Convert Cognito sub to valid Weaviate tenant name.

    Weaviate tenant names:
    - Must start with letter or underscore
    - Can contain letters, numbers, underscores, hyphens
    - Case insensitive

    Args:
        user_id: Cognito user identifier (sub claim from JWT token, stored in user_id column)

    Returns:
        Valid Weaviate tenant name (replaces hyphens with underscores)

    Example:
        >>> get_tenant_name("00u1abc2-def3-ghi4-jkl5")
        "00u1abc2_def3_ghi4_jkl5"
    """
    # user IDs often contain hyphens (e.g., "00u1abc2-def3-ghi4-jkl5-mno6pqr7stu8")
    # Replace hyphens with underscores for valid tenant name
    return user_id.replace('-', '_')


def get_user_collections(
    client: WeaviateClient,
    user_id: str
) -> Tuple[Collection, Collection]:
    """Get tenant-scoped collections for authenticated user.

    This is the recommended pattern for ALL document/chunk operations.
    Returns collections with .with_tenant() already applied, enforcing
    SDK-level data isolation (prevents accidental cross-user data access).

    Args:
        client: Weaviate client instance from connection.session()
        user_id: Okta user identifier from authenticated request

    Returns:
        Tuple of (document_chunk_collection, pdf_document_collection)
        Both collections are scoped to user's tenant

    Raises:
        weaviate.exceptions.MissingTenantError: If .with_tenant() is omitted
            in downstream queries (fail-safe protection)

    Usage:
        >>> from src.lib.weaviate_client.connection import get_connection
        >>> from fastapi import Security
        >>> from src.api.auth import auth
        >>>
        >>> @router.get("/documents")
        >>> def list_documents(user: OktaUser = Security(auth.get_user)):
        >>>     connection = get_connection()
        >>>     with connection.session() as client:
        >>>         chunk_col, pdf_col = get_user_collections(client, user.uid)
        >>>
        >>>         # All queries automatically scoped to user's tenant
        >>>         results = chunk_col.query.fetch_objects(limit=100)
        >>>         # Only returns this user's data - SDK enforces isolation

    Key Benefits:
        - **Fail-safe**: Omitting .with_tenant() raises MissingTenantError
        - **Single schema**: One DocumentChunk/PDFDocument definition for all users
        - **Performance**: Native tenant isolation (not query-time filtering)
        - **Auditability**: Tenant is explicit in returned collections
    """
    tenant_name = get_tenant_name(user_id)

    # Get collections with tenant scope applied
    document_chunk_collection = client.collections.get("DocumentChunk").with_tenant(tenant_name)
    pdf_document_collection = client.collections.get("PDFDocument").with_tenant(tenant_name)

    logger.debug(f"Retrieved tenant-scoped collections for user {user_id} (tenant: {tenant_name})")

    return document_chunk_collection, pdf_document_collection


# Export main functions
__all__ = ["get_connection", "get_tenant_name", "get_user_collections"]
