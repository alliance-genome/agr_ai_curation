"""External API plan schema.

Used by external API orchestrator to plan crew-based API queries.
"""

from typing import List
from pydantic import BaseModel, Field, ConfigDict

from .crew_execution_task import CrewExecutionTask


class ExternalAPIPlan(BaseModel):
    """Plan describing which external API crews to run."""

    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={
            "required": ["tasks", "reasoning"],
        },
    )

    tasks: List[CrewExecutionTask] = Field(
        default_factory=list,
        description="Ordered list of external API crew tasks"
    )
    reasoning: str = Field(
        default="",
        description="High-level reasoning for API task selection"
    )
