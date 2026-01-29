"""
Example Database Query Tool Template.

This template shows how to create a tool that queries a SQL database.
Copy this file to ../custom/ and customize for your database.

To use this template:
    1. Copy to ../custom/my_db_tool.py
    2. Update the function name and decorator
    3. Configure your database connection
    4. Implement your query logic
    5. Reference the tool name in your agent's agent.yaml

Key patterns demonstrated:
    - Using @function_tool decorator for registration
    - Async database operations with SQLAlchemy
    - Parameterized queries to prevent SQL injection
    - Connection pooling and session management
    - Data transformation before returning

Security notes:
    - NEVER construct SQL queries with string concatenation
    - ALWAYS use parameterized queries
    - NEVER expose raw database errors to users
"""

import os
from typing import Optional, List

from agents import function_tool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


# -----------------------------------------------------------------------------
# Database Configuration
# -----------------------------------------------------------------------------
# Load database URL from environment variable
# Format: postgresql+asyncpg://user:password@host:port/database
DATABASE_URL = os.getenv(
    "EXAMPLE_DATABASE_URL",
    "postgresql+asyncpg://user:password@localhost:5432/example_db"
)

# Create async engine with connection pooling
engine = create_async_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,  # Verify connections before use
)

# Create session factory
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


# -----------------------------------------------------------------------------
# Tool Implementation
# -----------------------------------------------------------------------------
@function_tool(
    name_override="example_db_query",
    description_override="Query the example database for records by name or ID"
)
async def example_db_query(
    search_term: str,
    table: str = "items",
    limit: int = 10
) -> dict:
    """
    Query the example database for matching records.

    This tool performs a search against the configured database and returns
    matching records. It uses parameterized queries to prevent SQL injection.

    Args:
        search_term: Text to search for (searches name and description fields)
        table: Table to query (default: "items") - must be in allowed list
        limit: Maximum number of results (default: 10, max: 100)

    Returns:
        Dict containing:
            - results: List of matching records
            - total: Count of matches
            - query_info: Information about the executed query
            - error: Error message if query failed (None if successful)
    """
    # Validate inputs
    allowed_tables = {"items", "categories", "metadata"}
    if table not in allowed_tables:
        return {
            "results": [],
            "total": 0,
            "query_info": {"table": table, "search_term": search_term},
            "error": f"Invalid table. Allowed: {', '.join(allowed_tables)}",
        }

    # Clamp limit to reasonable range
    limit = max(1, min(limit, 100))

    try:
        async with AsyncSessionLocal() as session:
            # Use parameterized query to prevent SQL injection
            # NEVER use f-strings or string concatenation for SQL!
            query = text(f"""
                SELECT id, name, description, created_at
                FROM {table}
                WHERE name ILIKE :search_pattern
                   OR description ILIKE :search_pattern
                ORDER BY name
                LIMIT :limit
            """)

            # Note: table name can't be parameterized in most SQL databases,
            # so we validate it against an allowlist above

            result = await session.execute(
                query,
                {
                    "search_pattern": f"%{search_term}%",
                    "limit": limit,
                }
            )
            rows = result.fetchall()

            # Get total count (separate query)
            count_query = text(f"""
                SELECT COUNT(*)
                FROM {table}
                WHERE name ILIKE :search_pattern
                   OR description ILIKE :search_pattern
            """)
            count_result = await session.execute(
                count_query,
                {"search_pattern": f"%{search_term}%"}
            )
            total = count_result.scalar()

            # Transform to clean output structure
            results = [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "description": row.description,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]

            return {
                "results": results,
                "total": total,
                "query_info": {
                    "table": table,
                    "search_term": search_term,
                    "limit": limit,
                },
                "error": None,
            }

    except Exception as e:
        # Log the actual error for debugging but return a safe message
        # In production, use proper logging: logger.exception("DB query failed")
        return {
            "results": [],
            "total": 0,
            "query_info": {"table": table, "search_term": search_term},
            "error": "Database query failed. Please try again later.",
        }
