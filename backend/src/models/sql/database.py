"""Database configuration and session management for all services.

This module provides the application database engine and session factory.
All application data is stored in the unified 'ai_curation' database:
- PDF document metadata
- User accounts
- Feedback reports
- Curation flows
- Audit logs
- Prompt templates
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import QueuePool

from src.config import get_app_database_url
from src.lib.openai_agents.config import get_app_db_pool_size, get_app_db_max_overflow


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# ============================================================================
# Application Database Engine
# ============================================================================

DATABASE_URL = get_app_database_url()

engine = create_engine(
    DATABASE_URL,
    future=True,
    poolclass=QueuePool,
    pool_size=get_app_db_pool_size(),
    max_overflow=get_app_db_max_overflow(),
    pool_pre_ping=True,  # Verify connections before using
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    """Yield an independent application session, closing it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_feedback_db():
    """Yield a database session for feedback operations.

    Used for storing curator feedback reports with trace data.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
