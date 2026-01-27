"""PDF extraction plan schema.

Used by PDF domain orchestrator to plan multi-step extraction tasks.
"""

from typing import List
from pydantic import BaseModel, Field, ConfigDict, field_validator

from .pdf_extraction_task import PDFExtractionTask


class PDFExtractionPlan(BaseModel):
    """Plan describing which PDF extraction tasks to execute.

    Note: All fields must be in 'required' array for OpenAI strict mode,
    even those with defaults.
    """

    model_config = ConfigDict(
        extra='forbid',
        json_schema_extra={
            "required": ["tasks", "reasoning"]
        },
    )

    tasks: List[PDFExtractionTask] = Field(
        default_factory=list,
        description="Ordered list of extraction tasks to execute"
    )
    reasoning: str = Field(
        default="",
        description="High-level reasoning for the selected tasks"
    )

    @field_validator("reasoning", mode="before")
    @classmethod
    def _coerce_reasoning(cls, value):
        """LLMs sometimes return reasoning as a list; coerce to string to avoid plan crashes."""
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(str(item) for item in value if item is not None)
        return str(value)
