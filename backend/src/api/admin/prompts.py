"""Admin API for prompt template management.

Provides endpoints for managing versioned prompts:
- List all prompts with filtering
- Get prompt by ID
- Create new prompt version
- Activate a prompt version
- Refresh prompt cache

Authorization: Email allowlist via ADMIN_EMAILS environment variable.
"""

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from psycopg2.errors import CheckViolation, UniqueViolation

from src.api.auth import get_auth_dependency
from src.lib.config import get_valid_group_ids

from src.lib.prompts import cache as prompt_cache
from src.models.sql.database import get_db
from src.models.sql.prompts import PromptTemplate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/prompts", tags=["Admin - Prompts"])


# =============================================================================
# Authorization
# =============================================================================


def _parse_admin_emails() -> set:
    """Parse admin emails from environment variable (internal helper)."""
    admin_emails_str = os.getenv("ADMIN_EMAILS", "")
    if not admin_emails_str:
        return set()
    return {email.strip().lower() for email in admin_emails_str.split(",") if email.strip()}


# Cache admin emails at module load time (env vars are static at runtime)
_admin_emails_cache: set = _parse_admin_emails()


def get_admin_emails() -> set:
    """Get the set of admin emails from cached environment variable.

    ADMIN_EMAILS should be a comma-separated list of email addresses.
    Example: ADMIN_EMAILS=admin@example.com,super@example.com

    Note: Cached at module load time for efficiency. Restart backend to pick up changes.
    """
    return _admin_emails_cache


async def require_admin(user: dict = get_auth_dependency()) -> dict:
    """Dependency that requires the user to be an admin.

    Checks if the user's email is in the ADMIN_EMAILS allowlist.

    Args:
        user: Authenticated user from auth dependency

    Returns:
        The user dict if they are an admin

    Raises:
        HTTPException 403: If user is not an admin
    """
    admin_emails = get_admin_emails()

    # In DEV_MODE, allow all authenticated users if no ADMIN_EMAILS is set
    if not admin_emails:
        dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"
        if dev_mode:
            logger.warning("ADMIN_EMAILS not set, allowing access in DEV_MODE")
            return user
        raise HTTPException(
            status_code=403,
            detail="Admin access not configured. Set ADMIN_EMAILS environment variable.",
        )

    user_email = user.get("email", "").lower()
    if user_email not in admin_emails:
        logger.warning('Admin access denied for user: %s', user_email)
        raise HTTPException(
            status_code=403,
            detail="Admin access required. Contact your administrator.",
        )

    return user


# =============================================================================
# Request/Response Models
# =============================================================================


class PromptResponse(BaseModel):
    """Response model for a prompt template."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_name: str
    prompt_type: str
    group_id: Optional[str]
    content: str
    version: int
    is_active: bool
    created_at: datetime
    created_by: Optional[str]
    change_notes: Optional[str]
    source_file: Optional[str]
    description: Optional[str]


class PromptListResponse(BaseModel):
    """Response model for list of prompts."""

    prompts: List[PromptResponse]
    total: int
    page: int
    page_size: int


class CreatePromptRequest(BaseModel):
    """Request model for creating a new prompt version."""

    agent_name: str = Field(..., min_length=1, max_length=100, description="Agent ID from catalog (e.g., 'pdf', 'gene')")
    prompt_type: str = Field(..., min_length=1, max_length=50, description="Prompt type (e.g., 'system', 'group_rules')")
    group_id: Optional[str] = Field(None, max_length=20, description="Group ID for group-specific rules (e.g., 'FB', 'WB')")
    content: str = Field(..., min_length=1, description="The prompt content text")
    change_notes: Optional[str] = Field(None, description="Notes about why this version was created")
    description: Optional[str] = Field(None, description="Optional description of the prompt")
    activate: bool = Field(False, description="Whether to activate this version immediately")


class CreatePromptResponse(BaseModel):
    """Response model for created prompt."""

    id: UUID
    agent_name: str
    prompt_type: str
    group_id: Optional[str]
    version: int
    is_active: bool
    message: str


class ActivatePromptResponse(BaseModel):
    """Response model for activating a prompt."""

    id: UUID
    agent_name: str
    prompt_type: str
    group_id: Optional[str]
    version: int
    is_active: bool
    message: str
    previous_active_version: Optional[int]


class CacheRefreshResponse(BaseModel):
    """Response model for cache refresh."""

    status: str
    active_prompts: int
    total_versions: int
    refreshed_at: datetime


class CacheStatusResponse(BaseModel):
    """Response model for cache status."""

    initialized: bool
    loaded_at: Optional[str]  # ISO format datetime string or None
    active_prompts: int
    total_versions: int


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=PromptListResponse)
async def list_prompts(
    agent_name: Optional[str] = Query(None, description="Filter by agent name"),
    prompt_type: Optional[str] = Query(None, description="Filter by prompt type"),
    group_id: Optional[str] = Query(None, description="Filter by group ID (use 'base' for NULL)"),
    active_only: bool = Query(False, description="Only return active prompts"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> PromptListResponse:
    """List all prompts with optional filtering.

    Args:
        agent_name: Filter by agent name (e.g., 'pdf', 'gene')
        prompt_type: Filter by type (e.g., 'system', 'group_rules')
        group_id: Filter by group ID ('base' for NULL group_id)
        active_only: Only return active versions
        page: Page number for pagination
        page_size: Number of items per page (max 100)
        db: Database session
        _admin: Admin user (for authorization)

    Returns:
        Paginated list of prompts
    """
    query = db.query(PromptTemplate)

    # Apply filters
    if agent_name:
        query = query.filter(PromptTemplate.agent_name == agent_name)
    if prompt_type:
        query = query.filter(PromptTemplate.prompt_type == prompt_type)
    if group_id:
        if group_id.lower() == "base":
            query = query.filter(PromptTemplate.group_id.is_(None))
        else:
            query = query.filter(PromptTemplate.group_id == group_id)
    if active_only:
        query = query.filter(PromptTemplate.is_active.is_(True))

    # Get total count
    total = query.count()

    # Apply pagination and ordering
    prompts = (
        query.order_by(
            PromptTemplate.agent_name,
            PromptTemplate.prompt_type,
            PromptTemplate.group_id,
            PromptTemplate.version.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PromptListResponse(
        prompts=[PromptResponse.model_validate(p) for p in prompts],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{prompt_id}", response_model=PromptResponse)
async def get_prompt(
    prompt_id: UUID,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> PromptResponse:
    """Get a specific prompt by ID.

    Args:
        prompt_id: UUID of the prompt template
        db: Database session
        _admin: Admin user (for authorization)

    Returns:
        The prompt template

    Raises:
        HTTPException 404: If prompt not found
    """
    prompt = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=404, detail=f"Prompt {prompt_id} not found")
    return PromptResponse.model_validate(prompt)


@router.post("", response_model=CreatePromptResponse, status_code=201)
async def create_prompt(
    request: CreatePromptRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> CreatePromptResponse:
    """Create a new version of a prompt.

    Automatically increments the version number for the agent/type/group combination.
    If activate=True, this version becomes active and the previous active version
    is deactivated.

    Includes retry logic for version number collisions (rare, but possible with
    concurrent admin requests). If a collision occurs, the version number is
    recalculated and the insert is retried.

    Args:
        request: The prompt creation request
        db: Database session
        admin: Admin user (for authorization and audit trail)

    Returns:
        The created prompt metadata

    Raises:
        HTTPException 409: If version collision persists after retries
    """
    max_retries = 3

    for attempt in range(max_retries):
        try:
            # Determine the next version number
            existing = (
                db.query(PromptTemplate)
                .filter(
                    and_(
                        PromptTemplate.agent_name == request.agent_name,
                        PromptTemplate.prompt_type == request.prompt_type,
                        # Handle NULL group_id comparison
                        PromptTemplate.group_id == request.group_id
                        if request.group_id
                        else PromptTemplate.group_id.is_(None),
                    )
                )
                .order_by(PromptTemplate.version.desc())
                .first()
            )

            next_version = (existing.version + 1) if existing else 1

            # If activating, deactivate existing active version
            if request.activate and existing and existing.is_active:
                existing.is_active = False
                logger.info(
                    'Deactivated prompt %s:%s:%s v%s', request.agent_name, request.prompt_type, request.group_id or 'base', existing.version)

            # Create the new prompt version
            new_prompt = PromptTemplate(
                agent_name=request.agent_name,
                prompt_type=request.prompt_type,
                group_id=request.group_id,
                content=request.content,
                version=next_version,
                is_active=request.activate,
                created_by=admin.get("email"),
                change_notes=request.change_notes,
                description=request.description,
            )

            db.add(new_prompt)
            db.commit()
            db.refresh(new_prompt)

            group_str = request.group_id or "base"
            logger.info(
                f"Created prompt {request.agent_name}:{request.prompt_type}:{group_str} v{next_version} "
                f"(active={request.activate}) by {admin.get('email')}"
            )

            # Refresh cache if the new prompt is active
            if request.activate:
                prompt_cache.refresh(db)
                logger.info("Prompt cache refreshed after activation")

            return CreatePromptResponse(
                id=new_prompt.id,
                agent_name=new_prompt.agent_name,
                prompt_type=new_prompt.prompt_type,
                group_id=new_prompt.group_id,
                version=new_prompt.version,
                is_active=new_prompt.is_active,
                message=f"Created version {next_version}" + (" and activated" if request.activate else ""),
            )

        except IntegrityError as e:
            db.rollback()

            # Check if this is a CHECK constraint violation (invalid group_id)
            if isinstance(e.orig, CheckViolation):
                valid_ids = get_valid_group_ids()
                logger.error(
                    f"Invalid group_id '{request.group_id}' for "
                    f"{request.agent_name}:{request.prompt_type}. "
                    f"Valid values: {valid_ids} or null"
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid group_id '{request.group_id}'. Must be one of: {', '.join(valid_ids)} (or null for base prompts)",
                )

            # Version collision (UniqueViolation) - retry
            if attempt < max_retries - 1:
                logger.warning(
                    f"Version collision on attempt {attempt + 1} for "
                    f"{request.agent_name}:{request.prompt_type}:{request.group_id or 'base'}, retrying..."
                )
                continue
            else:
                logger.error(
                    'Version collision persisted after %s attempts: %s', max_retries, e)
                raise HTTPException(
                    status_code=409,
                    detail="Version collision occurred. Please try again.",
                )

    # Should never reach here, but just in case
    raise HTTPException(
        status_code=500,
        detail="Unexpected error in version creation logic",
    )


@router.post("/{prompt_id}/activate", response_model=ActivatePromptResponse)
async def activate_prompt(
    prompt_id: UUID,
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> ActivatePromptResponse:
    """Activate a specific prompt version.

    Deactivates the currently active version (if any) and activates this one.
    Automatically refreshes the prompt cache.

    Args:
        prompt_id: UUID of the prompt to activate
        db: Database session
        admin: Admin user (for authorization and audit trail)

    Returns:
        Activation status with previous version info

    Raises:
        HTTPException 404: If prompt not found
        HTTPException 400: If prompt is already active
    """
    # Get the target prompt
    prompt = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first()
    if not prompt:
        raise HTTPException(status_code=404, detail=f"Prompt {prompt_id} not found")

    if prompt.is_active:
        raise HTTPException(
            status_code=400,
            detail=f"Prompt {prompt_id} is already active",
        )

    # Find and deactivate the currently active version
    current_active = (
        db.query(PromptTemplate)
        .filter(
            and_(
                PromptTemplate.agent_name == prompt.agent_name,
                PromptTemplate.prompt_type == prompt.prompt_type,
                # Handle NULL group_id comparison
                PromptTemplate.group_id == prompt.group_id
                if prompt.group_id
                else PromptTemplate.group_id.is_(None),
                PromptTemplate.is_active.is_(True),
            )
        )
        .first()
    )

    previous_version = None
    if current_active:
        previous_version = current_active.version
        current_active.is_active = False
        logger.info(
            'Deactivated prompt %s:%s:%s v%s', prompt.agent_name, prompt.prompt_type, prompt.group_id or 'base', previous_version)

    # Activate the new version
    prompt.is_active = True
    db.commit()

    group_str = prompt.group_id or "base"
    logger.info(
        f"Activated prompt {prompt.agent_name}:{prompt.prompt_type}:{group_str} v{prompt.version} "
        f"by {admin.get('email')}"
    )

    # Refresh the cache
    prompt_cache.refresh(db)
    logger.info("Prompt cache refreshed after activation")

    return ActivatePromptResponse(
        id=prompt.id,
        agent_name=prompt.agent_name,
        prompt_type=prompt.prompt_type,
        group_id=prompt.group_id,
        version=prompt.version,
        is_active=True,
        message=f"Activated version {prompt.version}",
        previous_active_version=previous_version,
    )


@router.post("/cache/refresh", response_model=CacheRefreshResponse)
async def refresh_cache(
    db: Session = Depends(get_db),
    admin: dict = Depends(require_admin),
) -> CacheRefreshResponse:
    """Refresh the prompt cache from the database.

    Forces a reload of all prompts into the in-memory cache.
    Use after making direct database changes or to ensure cache consistency.

    Args:
        db: Database session
        admin: Admin user (for authorization)

    Returns:
        Cache status after refresh
    """
    prompt_cache.refresh(db)
    cache_info = prompt_cache.get_cache_info()

    logger.info('Prompt cache manually refreshed by %s', admin.get('email'))

    return CacheRefreshResponse(
        status="refreshed",
        active_prompts=cache_info["active_prompts"],
        total_versions=cache_info["total_versions"],
        refreshed_at=datetime.now(timezone.utc),
    )


@router.get("/cache/status", response_model=CacheStatusResponse)
async def get_cache_status(
    _admin: dict = Depends(require_admin),
) -> CacheStatusResponse:
    """Get the current prompt cache status.

    Returns cache initialization state and statistics.

    Args:
        _admin: Admin user (for authorization)

    Returns:
        Cache status information
    """
    cache_info = prompt_cache.get_cache_info()
    return CacheStatusResponse(**cache_info)


# =============================================================================
# Convenience Endpoints (agent_name/version paths)
# =============================================================================


class PromptHistoryResponse(BaseModel):
    """Response model for prompt version history."""

    agent_name: str
    prompt_type: str
    group_id: Optional[str]
    versions: List[PromptResponse]
    total_versions: int
    active_version: Optional[int]


@router.get("/{agent_name}/history", response_model=PromptHistoryResponse)
async def get_prompt_history(
    agent_name: str,
    prompt_type: str = Query("system", description="Prompt type (default: system)"),
    group_id: Optional[str] = Query(None, description="Group ID (use 'base' or omit for base prompts)"),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> PromptHistoryResponse:
    """Get version history for a specific agent's prompt.

    Returns all versions of a prompt for the given agent/type/group combination,
    ordered by version descending (newest first).

    Args:
        agent_name: Agent ID from catalog (e.g., 'pdf', 'gene')
        prompt_type: Prompt type (e.g., 'system', 'group_rules')
        group_id: Group ID for group-specific rules ('base' or None for base prompts)
        db: Database session
        _admin: Admin user (for authorization)

    Returns:
        Version history with active version highlighted
    """
    # Normalize group_id
    effective_group_id = None if not group_id or group_id.lower() == "base" else group_id

    # Query all versions for this agent/type/group
    query = db.query(PromptTemplate).filter(
        and_(
            PromptTemplate.agent_name == agent_name,
            PromptTemplate.prompt_type == prompt_type,
            PromptTemplate.group_id == effective_group_id
            if effective_group_id
            else PromptTemplate.group_id.is_(None),
        )
    ).order_by(PromptTemplate.version.desc())

    prompts = query.all()

    if not prompts:
        raise HTTPException(
            status_code=404,
            detail=f"No prompts found for {agent_name}:{prompt_type}:{group_id or 'base'}",
        )

    # Find active version
    active_version = next((p.version for p in prompts if p.is_active), None)

    return PromptHistoryResponse(
        agent_name=agent_name,
        prompt_type=prompt_type,
        group_id=effective_group_id,
        versions=[PromptResponse.model_validate(p) for p in prompts],
        total_versions=len(prompts),
        active_version=active_version,
    )


@router.get("/{agent_name}/versions/{version}", response_model=PromptResponse)
async def get_prompt_by_version(
    agent_name: str,
    version: int,
    prompt_type: str = Query("system", description="Prompt type (default: system)"),
    group_id: Optional[str] = Query(None, description="Group ID (use 'base' or omit for base prompts)"),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_admin),
) -> PromptResponse:
    """Get a specific version of an agent's prompt.

    Args:
        agent_name: Agent ID from catalog (e.g., 'pdf', 'gene')
        version: Version number to retrieve
        prompt_type: Prompt type (e.g., 'system', 'group_rules')
        group_id: Group ID for group-specific rules ('base' or None for base prompts)
        db: Database session
        _admin: Admin user (for authorization)

    Returns:
        The specific prompt version

    Raises:
        HTTPException 404: If prompt version not found
    """
    # Normalize group_id
    effective_group_id = None if not group_id or group_id.lower() == "base" else group_id

    prompt = db.query(PromptTemplate).filter(
        and_(
            PromptTemplate.agent_name == agent_name,
            PromptTemplate.prompt_type == prompt_type,
            PromptTemplate.group_id == effective_group_id
            if effective_group_id
            else PromptTemplate.group_id.is_(None),
            PromptTemplate.version == version,
        )
    ).first()

    if not prompt:
        raise HTTPException(
            status_code=404,
            detail=f"Prompt {agent_name}:{prompt_type}:{group_id or 'base'} v{version} not found",
        )

    return PromptResponse.model_validate(prompt)
