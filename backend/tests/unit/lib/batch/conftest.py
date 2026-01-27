"""Test fixtures for batch unit tests."""
import pytest
from src.models.sql.database import SessionLocal


@pytest.fixture
def test_db():
    """Provide a database session for tests."""
    db = SessionLocal()
    yield db
    db.rollback()
    db.close()
