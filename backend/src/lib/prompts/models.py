"""Re-export prompt models for import compatibility.

The actual SQLAlchemy models live in src/models/sql/prompts.py (for Alembic).
This file provides a convenient import path for the rest of the codebase.

Usage:
    from src.lib.prompts.models import PromptTemplate, PromptExecutionLog
"""

from src.models.sql.prompts import PromptTemplate, PromptExecutionLog

__all__ = ["PromptTemplate", "PromptExecutionLog"]
