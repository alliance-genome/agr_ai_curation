"""
SQL Query tool for OpenAI Agents SDK.

This tool allows agents to execute read-only SQL queries against any
SQLAlchemy-supported database, returning structured results.
"""

import logging
from typing import Optional, Any, List, Dict

import sqlalchemy as sa
from sqlalchemy.pool import QueuePool
from pydantic import BaseModel

from agents import function_tool

logger = logging.getLogger(__name__)


class SqlQueryResult(BaseModel):
    status: str
    rows: Optional[List[Dict[str, Any]]] = None
    count: Optional[int] = None
    message: Optional[str] = None


def create_sql_query_tool(database_url: str, tool_name: str = "sql_query"):
    """
    Create a SQL query tool bound to a specific database.

    Returns a function_tool that emits SqlQueryResult objects.
    """
    engine = sa.create_engine(
        database_url,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )

    def mask_url(url: str) -> str:
        try:
            parsed = sa.engine.url.make_url(url)
            if parsed.password:
                return str(parsed.set(password="***"))
            return url
        except Exception:
            return "***"

    logger.info("SQL tool '%s' initialized: %s", tool_name, mask_url(database_url))

    @function_tool(name_override=tool_name)
    def sql_query(query: str) -> SqlQueryResult:
        """
        Execute a read-only SQL SELECT query against the database.
        """
        if not query or not query.strip():
            return SqlQueryResult(status="error", message="Query string must not be empty")

        cleaned = query.strip().upper()
        if not cleaned.startswith('SELECT'):
            return SqlQueryResult(
                status="error",
                message="Only SELECT queries are allowed. This is a read-only tool."
            )

        try:
            with engine.connect() as conn:
                result = conn.execute(sa.text(query))

                if result.returns_rows:
                    rows = [dict(row._mapping) for row in result]
                    logger.debug("SQL query returned %s rows", len(rows))
                    return SqlQueryResult(status="ok", rows=rows, count=len(rows))
                else:
                    return SqlQueryResult(
                        status="ok",
                        message="Query executed successfully (no rows returned)"
                    )

        except sa.exc.DBAPIError as e:
            logger.error("Database error: %s", e)
            # Sanitize: don't expose raw database error details to agent
            return SqlQueryResult(status="error", message="Database error: query failed. Check syntax and table/column names.")
        except sa.exc.SQLAlchemyError as e:
            logger.error("SQLAlchemy error: %s", e)
            # Sanitize: generic message for query errors
            return SqlQueryResult(status="error", message="Query error: unable to execute query. Verify SQL syntax.")
        except Exception as e:
            logger.error("Unexpected SQL error: %s", e, exc_info=True)
            # Sanitize: never expose internal error details
            return SqlQueryResult(status="error", message="An unexpected error occurred while executing the query.")

    return sql_query
