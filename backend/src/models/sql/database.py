"""Database configuration and session management for all services.

This module provides the application database engine and session factories.
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
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # Verify connections before using
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db():
    """Yield a database session for PDF viewer operations.

    Used for PDF document metadata management.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()




# ============================================================================
# Feedback Engine (User Feedback Reports)
# Uses the same application database (ai_curation)
# ============================================================================

FEEDBACK_DATABASE_URL = get_app_database_url()

feedback_engine = create_engine(
    FEEDBACK_DATABASE_URL,
    future=True,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

FeedbackSessionLocal = sessionmaker(
    bind=feedback_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_feedback_db():
    """Yield a database session for feedback operations.

    Used for storing curator feedback reports with trace data.
    """
    db = FeedbackSessionLocal()
    try:
        yield db
    finally:
        db.close()
