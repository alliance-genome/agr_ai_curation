"""Crew execution task schema.

Generic crew execution task specification used by database and external API plans.
"""

from typing import List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class CrewExecutionTask(BaseModel):
    """Generic crew execution task specification.

    Note: All fields must be in 'required' array for OpenAI strict mode,
    even those with defaults.
    """

    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={
            "required": [
                "identifier",
                "agent",
                "task",
                "objective",
                "instructions",
                "inputs",
                "depends_on",
            ]
        },
    )

    identifier: str = Field(
        description="Unique identifier (slug) for this crew task"
    )
    agent: str = Field(
        description="Agent name from agents.yaml"
    )
    task: str = Field(
        description="Task name from tasks.yaml"
    )
    objective: str = Field(
        description="Objective for this crew to accomplish"
    )
    instructions: str = Field(
        default="",
        description="Additional guidance for the crew (empty string if none)"
    )
    inputs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary of additional key/value pairs to pass into task context"
    )
    depends_on: List[str] = Field(
        default_factory=list,
        description="List of task identifiers that must complete before this task can run (empty list if no dependencies)"
    )
