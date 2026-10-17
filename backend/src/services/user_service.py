"""Authoritative user lookup, sampled activity, and retryable tenant provisioning."""

import logging
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from sqlalchemy import case, or_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from weaviate.classes.tenants import Tenant

from auth_runtime.base import AuthPrincipal
from src.models.sql.agent import Project, ProjectMember
from src.models.sql.user import User
from src.models.sql.database import SessionLocal
from src.lib.weaviate_helpers import get_connection, get_tenant_name
from src.lib.openai_agents.config import get_user_activity_write_interval_seconds
from src.lib.observability.runtime import report_runtime_exception, sanitized_runtime_error
from src.services.tenant_provisioning_cache import tenant_provisioning_cache

logger = logging.getLogger(__name__)


def provision_weaviate_tenants(auth_sub: str) -> bool:
    """Provision both collections; only completed success is cached per backend.

    Duplicate tenant creation is idempotent. A partial failure is retryable on
    the next call, without undoing SQL provisioning or denying authentication.
    """
    try:
        connection = get_connection()
        tenant_name = get_tenant_name(auth_sub)

        def create() -> bool:
            with connection.session() as client:
                for name in ("DocumentChunk", "PDFDocument"):
                    client.collections.get(name).tenants.create(Tenant(name=tenant_name))
            return True

        # The connection object identifies the deployment; lifecycle replacement
        # also invalidates in-flight generations. No credentials are cache keys.
        return tenant_provisioning_cache.ensure((connection, auth_sub, tenant_name), create)
    except Exception:
        report_runtime_exception(
            sanitized_runtime_error("Weaviate tenant provisioning failed"),
            component="user_provisioning", operation="provision_tenants",
        )
        logger.error("Weaviate tenant provisioning failed; next request will retry",
                     extra={"sentry_skip_event": True})
        return False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _activity_due(last_login: Optional[datetime], now: datetime, interval: int) -> bool:
    if last_login is None:
        return True
    # Legacy naive timestamps represent UTC. Future timestamps are retained until
    # wall time catches up; a slower worker must never move activity backwards.
    last_login = last_login.replace(tzinfo=timezone.utc) if last_login.tzinfo is None else last_login
    return (now - last_login).total_seconds() >= interval


def provision_user(db: Session, principal: AuthPrincipal) -> User:
    """Resolve authenticated identity using the caller-owned synchronous Session.

    Writes commit as before; a warm lookup leaves its read transaction for the
    caller to close. Call before starting caller-owned mutations. Async callers
    use provision_user_async, whose worker owns its entire Session lifecycle.
    Profile-only writes also sample activity, but never decrease it. SQL updates
    touch only profile/activity fields, never flush stale User attributes.
    """
    auth_sub = principal.subject
    if not auth_sub:
        raise ValueError("Authenticated principal missing subject")
    user_email = principal.email
    if user_email and '@' not in user_email:
        user_email = None
    display_name = principal.display_name or user_email
    now = _utc_now()
    interval = get_user_activity_write_interval_seconds()
    with db.no_autoflush:
        db_user = db.query(User).filter_by(auth_sub=auth_sub).one_or_none()
        if db_user is None:
            project_id = None
            configured_project = os.getenv("DEFAULT_PROJECT_ID", "").strip()
            if configured_project:
                try:
                    project_id = uuid.UUID(configured_project)
                except ValueError as exc:
                    raise ValueError("DEFAULT_PROJECT_ID must be a project UUID") from exc
                project = db.query(Project).filter_by(id=project_id, is_active=True).one_or_none()
                if project is None:
                    raise ValueError("DEFAULT_PROJECT_ID must identify an active project")

            # Conflict handling is limited to auth_sub uniqueness. PostgreSQL
            # waits for the competing insert; no rollback of caller work occurs.
            created_id = db.execute(
                insert(User).values(auth_sub=auth_sub, email=user_email,
                                    display_name=display_name, created_at=now,
                                    last_login=now, is_active=True)
                .on_conflict_do_nothing(index_elements=[User.auth_sub])
                .returning(User.id)
            ).scalar_one_or_none()
            if created_id is not None:
                if project_id is not None:
                    db.add(ProjectMember(project_id=project_id, user_id=created_id, role="member"))
                db.commit()
            db_user = db.query(User).filter_by(auth_sub=auth_sub).one()

        profile_changed = db_user.email != user_email or db_user.display_name != display_name
        if profile_changed or _activity_due(db_user.last_login, now, interval):
            values = {"last_login": case(
                (or_(User.last_login.is_(None), User.last_login < now), now),
                else_=User.last_login,
            )}
            if profile_changed:
                values.update(email=user_email, display_name=display_name)
            statement = update(User).where(User.id == db_user.id)
            if not profile_changed:
                statement = statement.where(or_(
                    User.last_login.is_(None), User.last_login <= now - timedelta(seconds=interval),
                ))
            result = db.execute(statement.values(**values).execution_options(synchronize_session=False))
            if result.rowcount:
                db.commit()
                db.refresh(db_user)

    provision_weaviate_tenants(auth_sub)
    return db_user


@dataclass(frozen=True)
class ProvisionedUser:
    """Stable scalar snapshot; contains no Session, ORM state, or relationships."""
    id: int
    auth_sub: str
    email: Optional[str]
    display_name: Optional[str]
    created_at: datetime
    last_login: Optional[datetime]
    is_active: bool

    def to_dict(self) -> dict:
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        result["last_login"] = self.last_login.isoformat() if self.last_login else None
        return result


def _provision_owned_user(principal: AuthPrincipal) -> ProvisionedUser:
    with SessionLocal() as db:
        user = provision_user(db, principal)
        return ProvisionedUser(**{field: getattr(user, field) for field in ProvisionedUser.__dataclass_fields__})


async def provision_user_async(principal: AuthPrincipal) -> ProvisionedUser:
    """Await provisioning in one worker; cancellation cannot orphan its Session."""
    return await run_in_threadpool(_provision_owned_user, principal)


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
    "provision_user_async",
    "ProvisionedUser",
    "principal_from_claims",
    "set_global_user_from_cognito",
    "provision_weaviate_tenants",
]
