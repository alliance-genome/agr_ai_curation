"""Citation schema.

Used to represent a single citation from a document with page number and relevance.
"""

from pydantic import BaseModel, Field, ConfigDict


class Citation(BaseModel):
    """A single citation from document"""
    model_config = ConfigDict(extra='forbid')

    page_number: int = Field(description="Page number of citation")
    relevance_score: float = Field(ge=0.0, le=1.0, description="Relevance score 0-1")
    text: str = Field(description="Citation text excerpt")
