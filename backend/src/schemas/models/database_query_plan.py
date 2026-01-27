"""Database query plan schema.

Used by internal database orchestrator to plan crew-based database queries.
"""

from typing import List
from pydantic import BaseModel, Field, ConfigDict

from .crew_execution_task import CrewExecutionTask


class DatabaseQueryPlan(BaseModel):
    """Plan outlining the set of internal database crews to execute."""

    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={
            "required": ["tasks", "reasoning"],
        },
    )

    tasks: List[CrewExecutionTask] = Field(
        default_factory=list,
        description="Ordered list of database-oriented crew tasks"
    )
    reasoning: str = Field(
        default="",
        description="High-level reasoning for database task selection"
    )
