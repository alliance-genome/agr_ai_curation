"""User provisioning and management service.

Implements:
- set_global_user_from_cognito(): Auto-create users on first login
- provision_weaviate_tenants(): Create Weaviate tenants for new users

Pattern follows AGR Literature Service reference implementation.
Requirements: FR-005, FR-006
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session
from weaviate.classes.tenants import Tenant

from src.models.sql.user import User
from src.lib.weaviate_helpers import get_connection, get_tenant_name


logger = logging.getLogger(__name__)


def provision_weaviate_tenants(cognito_sub: str) -> bool:
    """Create Weaviate tenants for new user in multi-tenant collections.

    Creates tenants in both:
    - DocumentChunk collection (for text embeddings)
    - PDFDocument collection (for document metadata)

    Args:
        cognito_sub: Cognito user identifier (UUID from 'sub' claim)

    Returns:
        True if provisioning succeeded, False if it failed (caller should retry)

    Note:
        - Idempotent: Safe to call multiple times (Weaviate ignores duplicate tenants)
        - Called automatically by set_global_user_from_cognito() on every login
        - Returns False on failure so caller can retry on next login
    """
    tenant_name = get_tenant_name(cognito_sub)

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
        logger.error('Failed to provision Weaviate tenants for %s: %s', cognito_sub, e)
        # Return False to signal failure - caller will retry on next login
        return False


def set_global_user_from_cognito(db: Session, cognito_user: Dict[str, Any]) -> User:
    """Ensure user row exists for Cognito principal, create if new.

    This is the main user provisioning function, called on every authenticated request.
    Pattern follows AGR Literature Service implementation.

    Args:
        db: SQLAlchemy database session
        cognito_user: Dict containing Cognito user claims from JWT token
            - sub: Unique user ID (UUID)
            - email: User email address
            - name: User display name
            - groups: List of Cognito group names (optional)

    Returns:
        User database model (either existing or newly created)

    Behavior:
        - First login: Create user record + Weaviate tenants
        - Subsequent logins: Update last_login timestamp
        - Email changes: Update email from Cognito profile

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
        ...     db_user = set_global_user_from_cognito(db, user)
        ...     # Now db_user is guaranteed to exist
    """
    # Extract user identifier from Cognito JWT token
    # 'sub' is the unique Cognito user ID (UUIDv4 format)
    cognito_sub: str = cognito_user.get('sub')
    if not cognito_sub:
        raise ValueError("Cognito user missing 'sub' claim")

    # Extract email if valid (must contain @)
    user_email: Optional[str] = cognito_user.get('email')
    if user_email and '@' not in user_email:
        user_email = None

    # Extract display name (use 'name' claim or fallback to email)
    display_name: Optional[str] = cognito_user.get('name') or user_email

    # Check if user already exists by their auth_sub (Cognito sub claim)
    db_user = db.query(User).filter_by(auth_sub=cognito_sub).one_or_none()

    if db_user is None:
        # First login - create new user
        logger.info('First login detected for Cognito user %s - creating account', cognito_sub)

        # Create user record
        db_user = User(
            auth_sub=cognito_sub,  # Unique identifier from Cognito JWT 'sub' claim
            email=user_email,
            display_name=display_name,
            created_at=datetime.utcnow(),
            last_login=datetime.utcnow(),
            is_active=True
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)

        logger.info('Created user account: id=%s, auth_sub=%s, email=%s', db_user.id, cognito_sub, user_email)

        # Provision Weaviate tenants for new user (FR-006)
        # Returns False on failure - we'll retry on next login
        provisioned = provision_weaviate_tenants(cognito_sub)
        if provisioned:
            logger.info('Provisioned Weaviate tenants for user %s', cognito_sub)
        else:
            logger.warning('Failed to provision Weaviate tenants for %s - will retry on next login', cognito_sub)

        return db_user

    # Existing user - update metadata and retry tenant provisioning if needed
    needs_update = False

    # Update email if changed in Cognito
    if db_user.email != user_email:
        logger.info('Updating email for user %s: %s → %s', cognito_sub, db_user.email, user_email)
        db_user.email = user_email
        needs_update = True

    # Update display name if changed
    if db_user.display_name != display_name:
        logger.info('Updating display name for user %s: %s → %s', cognito_sub, db_user.display_name, display_name)
        db_user.display_name = display_name
        needs_update = True

    # Update last_login timestamp
    db_user.last_login = datetime.utcnow()
    needs_update = True

    if needs_update:
        db.add(db_user)
        db.commit()
        db.refresh(db_user)

    # Retry tenant provisioning on every login (idempotent if already exists)
    # This ensures we eventually provision tenants even if first attempt failed
    provision_weaviate_tenants(cognito_sub)

    logger.debug('User %s authenticated (id=%s)', cognito_sub, db_user.id)
    return db_user


# Export main functions
__all__ = ["set_global_user_from_cognito", "provision_weaviate_tenants"]
