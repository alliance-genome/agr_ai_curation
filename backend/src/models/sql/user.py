"""User model for authenticated Cognito users.

This model represents user accounts created via AWS Cognito authentication.
Users are auto-provisioned on first login (FR-005).
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Integer, String, CheckConstraint
from sqlalchemy.sql import func

from .database import Base


class User(Base):
    """User account model linked to AWS Cognito authentication.

    Each user has:
    - A stable auth_sub (unique user ID from JWT token's 'sub' claim)
    - Optional email and display name from Cognito profile
    - Timestamps for account lifecycle tracking
    - Active status flag for soft deletes

    Users are automatically created on first Cognito login via
    set_global_user_from_cognito() in user_service.py
    """

    __tablename__ = "users"

    # Primary key (auto-increment integer)
    id = Column("user_id", Integer, primary_key=True, autoincrement=True)

    # Auth identity (stable identifier from JWT token's 'sub' claim)
    # DB column: 'auth_sub' (renamed from 'okta_id' via migration c1d2e3f4a5b6)
    auth_sub = Column(
        "auth_sub",
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="Auth provider user identifier (sub claim from JWT token)"
    )

    # Profile information from Cognito
    email = Column(
        String(255),
        nullable=True,
        index=True,
        comment="User email address from Cognito token"
    )

    display_name = Column(
        String(255),
        nullable=True,
        comment="User display name from Cognito token"
    )

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Timestamp of first login (account creation)"
    )

    last_login = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of most recent authentication"
    )

    # Status
    is_active = Column(
        Boolean,
        nullable=False,
        server_default="true",
        index=True,
        comment="Soft delete flag (false = deactivated)"
    )

    # Constraints
    __table_args__ = (
        CheckConstraint("auth_sub <> ''", name="ck_users_auth_sub_not_empty"),
    )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<User(id={self.id}, "
            f"auth_sub='{self.auth_sub}', "
            f"email='{self.email}', "
            f"is_active={self.is_active})>"
        )

    def to_dict(self) -> dict:
        """Convert user to dictionary for API responses."""
        return {
            "id": self.id,
            "auth_sub": self.auth_sub,
            "email": self.email,
            "display_name": self.display_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "is_active": self.is_active,
        }
