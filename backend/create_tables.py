#!/usr/bin/env python3
"""
Create database tables from SQLAlchemy models
This replaces the need for Alembic migrations during development
"""

import os
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import create_engine, text
from app.models import Base


def create_tables(drop_existing=True):
    """Create all tables from SQLAlchemy models"""

    # Get database URL from environment or use default
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://curation_user:curation_pass@localhost:5432/ai_curation_db",  # pragma: allowlist secret
    )

    print(f"Connecting to database: {database_url.split('@')[1]}")
    engine = create_engine(database_url)

    # Ensure extensions are enabled
    with engine.connect() as conn:
        print("Ensuring PostgreSQL extensions are enabled...")
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        conn.commit()
        print("✓ Extensions ready")

    if drop_existing:
        print("Dropping existing tables...")
        Base.metadata.drop_all(engine)
        print("✓ Tables dropped")

    print("Creating tables from models...")
    Base.metadata.create_all(engine)
    print("✓ Tables created")

    # List created tables
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
        """
            )
        )
        tables = [row[0] for row in result]

    print(f"\nCreated {len(tables)} tables:")
    for table in tables:
        print(f"  - {table}")

    print("\n✓ Database schema created successfully!")
    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Create database tables from models")
    parser.add_argument(
        "--no-drop",
        action="store_true",
        help="Don't drop existing tables (default: drop and recreate)",
    )

    args = parser.parse_args()

    create_tables(drop_existing=not args.no_drop)
