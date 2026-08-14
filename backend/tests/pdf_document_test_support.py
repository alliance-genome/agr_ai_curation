"""Shared helpers for persistence tests that need an owned PDF document."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.sql.user import User


def ensure_test_pdf_owner(session: Session, *, auth_sub: str) -> int:
    """Return a durable test user ID for a PDF-document fixture."""

    owner = session.scalar(select(User).where(User.auth_sub == auth_sub))
    if owner is None:
        owner = User(auth_sub=auth_sub, is_active=True)
        session.add(owner)
        session.flush()
    return int(owner.id)
