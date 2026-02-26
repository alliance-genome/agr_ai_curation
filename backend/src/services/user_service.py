"""User provisioning and management service.

Implements:
- provision_user(): Auto-create users on first login
- provision_weaviate_tenants(): Create Weaviate tenants for new users

Pattern follows AGR Literature Service reference implementation.
Requirements: FR-005, FR-006
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session
from weaviate.classes.tenants import Tenant

from src.auth.base import AuthPrincipal
from src.models.sql.user import User
from src.lib.weaviate_helpers import get_connection, get_tenant_name


logger = logging.getLogger(__name__)


def provision_weaviate_tenants(auth_sub: str) -> bool:
    """Create Weaviate tenants for new user in multi-tenant collections.

    Creates tenants in both:
    - DocumentChunk collection (for text embeddings)
    - PDFDocument collection (for document metadata)

    Args:
        auth_sub: Auth provider subject identifier (from token 'sub' claim)

    Returns:
        True if provisioning succeeded, False if it failed (caller should retry)

    Note:
        - Idempotent: Safe to call multiple times (Weaviate ignores duplicate tenants)
        - Called automatically by provision_user() on every login
        - Returns False on failure so caller can retry on next login
    """
    tenant_name = get_tenant_name(auth_sub)

    try:
        connection = get_connection()

        with connection.session() as client:
            # Create tenant in DocumentChunk collection
            chunk_collection = client.collections.get("DocumentChunk")
            chunk_collection.tenants.create(Tenant(name=tenant_name))
            logger.info("Created tenant '%s' in DocumentChunk collection", tenant_name)

            # Create tenant in PDFDocument collection
            pdf_collection = client.collections.get("PDFDocument")
            pdf_collection.tenants.create(Tenant(name=tenant_name))
            logger.info("Created tenant '%s' in PDFDocument collection", tenant_name)

        return True

    except Exception as e:
        logger.error('Failed to provision Weaviate tenants for %s: %s', auth_sub, e)
        # Return False to signal failure - caller will retry on next login
        return False


def provision_user(db: Session, principal: AuthPrincipal) -> User:
    """Ensure user row exists for authenticated principal, create if new.

    This is the main user provisioning function, called on every authenticated request.
    Pattern follows AGR Literature Service implementation.

    Args:
        db: SQLAlchemy database session
        principal: Provider-agnostic authenticated principal

    Returns:
        User database model (either existing or newly created)

    Behavior:
        - First login: Create user record + Weaviate tenants
        - Subsequent logins: Update last_login timestamp
        - Email changes: Update email from identity provider profile

    Requirements:
        - FR-005: Automatic user creation on first login
        - FR-006: Empty Weaviate collections initialized for new users

    Example:
        >>> from fastapi import Depends
        >>> @router.get("/endpoint")
        >>> def protected_endpoint(
        ...     user: dict = get_auth_dependency(),
        ...     db: Session = Depends(get_db)
        ... ):
        ...     db_user = provision_user(db, principal)
        ...     # Now db_user is guaranteed to exist
    """
    auth_sub: str = principal.subject
    if not auth_sub:
        raise ValueError("Authenticated principal missing subject")

    # Extract email if valid (must contain @)
    user_email: Optional[str] = principal.email
    if user_email and '@' not in user_email:
        user_email = None

    # Extract display name (use 'name' claim or fallback to email)
    display_name: Optional[str] = principal.display_name or user_email

    # Check if user already exists by their auth_sub claim
    db_user = db.query(User).filter_by(auth_sub=auth_sub).one_or_none()

    if db_user is None:
        # First login - create new user
        logger.info('First login detected for principal %s - creating account', auth_sub)

        # Create user record
        db_user = User(
            auth_sub=auth_sub,
            email=user_email,
            display_name=display_name,
            created_at=datetime.now(timezone.utc),
            last_login=datetime.now(timezone.utc),
            is_active=True
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)

        logger.info('Created user account: id=%s, auth_sub=%s, email=%s', db_user.id, auth_sub, user_email)

        # Provision Weaviate tenants for new user (FR-006)
        # Returns False on failure - we'll retry on next login
        provisioned = provision_weaviate_tenants(auth_sub)
        if provisioned:
            logger.info('Provisioned Weaviate tenants for user %s', auth_sub)
        else:
            logger.warning('Failed to provision Weaviate tenants for %s - will retry on next login', auth_sub)

        return db_user

    # Existing user - update metadata and retry tenant provisioning if needed
    needs_update = False

    # Update email if changed in identity provider
    if db_user.email != user_email:
        logger.info('Updating email for user %s: %s → %s', auth_sub, db_user.email, user_email)
        db_user.email = user_email
        needs_update = True

    # Update display name if changed
    if db_user.display_name != display_name:
        logger.info('Updating display name for user %s: %s → %s', auth_sub, db_user.display_name, display_name)
        db_user.display_name = display_name
        needs_update = True

    # Update last_login timestamp
    db_user.last_login = datetime.now(timezone.utc)
    needs_update = True

    if needs_update:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)

    # Retry tenant provisioning on every login (idempotent if already exists)
    # This ensures we eventually provision tenants even if first attempt failed
    provision_weaviate_tenants(auth_sub)

    logger.debug('User %s authenticated (id=%s)', auth_sub, db_user.id)
    return db_user


def principal_from_claims(claims: Dict[str, Any], provider: str = "unknown") -> AuthPrincipal:
    """Convert raw auth claims dict into AuthPrincipal."""
    groups = claims.get("groups")
    if groups is None:
        groups = claims.get("cognito:groups", [])
    if not isinstance(groups, list):
        groups = [str(groups)] if groups else []

    return AuthPrincipal(
        subject=claims.get("sub") or "",
        email=claims.get("email"),
        display_name=claims.get("name") or claims.get("email"),
        groups=groups,
        raw_claims=dict(claims),
        provider=provider,
    )


def set_global_user_from_cognito(db: Session, cognito_user: Dict[str, Any]) -> User:
    """Deprecated compatibility wrapper around provision_user()."""
    principal = principal_from_claims(cognito_user, provider="cognito")
    return provision_user(db, principal)


__all__ = [
    "provision_user",
    "principal_from_claims",
    "set_global_user_from_cognito",
    "provision_weaviate_tenants",
]
