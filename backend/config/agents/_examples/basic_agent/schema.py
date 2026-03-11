"""
Output schema for the Basic Agent template.

This Pydantic model defines the structured output format that the agent must
return. The agent.yaml references this by class name (output_schema field).

To customize:
    1. Rename the classes to match your domain
    2. Update fields to match your data structure
    3. Add appropriate Field descriptions for documentation
    4. Update the agent.yaml output_schema field to match

Tips:
    - Use Optional[] for fields that may not always be present
    - Add Field(description=...) to document each field's purpose
    - Keep models focused - don't include unnecessary fields
    - Consider using enums for fields with limited valid values
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum


# -----------------------------------------------------------------------------
# Optional: Define enums for fields with limited valid values
# -----------------------------------------------------------------------------
class ConfidenceLevel(str, Enum):
    """Confidence level for results."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# -----------------------------------------------------------------------------
# Individual Result Model
# -----------------------------------------------------------------------------
class BasicResult(BaseModel):
    """
    A single result item returned by the agent.

    Customize this class for your specific domain. For example:
        - GeneResult with symbol, primary_id, species
        - DiseaseResult with name, doid, definition
        - ChemicalResult with name, chebi_id, formula
    """

    # Unique identifier for this result
    id: str = Field(
        description="Unique identifier for this result"
    )

    # Human-readable name or label
    name: str = Field(
        description="Human-readable name or label"
    )

    # Optional description or additional details
    description: Optional[str] = Field(
        default=None,
        description="Additional details about this result"
    )

    # Optional metadata as key-value pairs
    metadata: Optional[dict] = Field(
        default=None,
        description="Additional metadata as key-value pairs"
    )


# -----------------------------------------------------------------------------
# Envelope/Container Model (Referenced in agent.yaml)
# -----------------------------------------------------------------------------
class BasicResultEnvelope(BaseModel):
    """
    Container for agent results.

    This is the top-level model referenced in agent.yaml's output_schema field.
    It wraps the individual results with metadata about the query.
    """

    # List of results found
    results: List[BasicResult] = Field(
        description="List of results matching the query"
    )

    # Confidence score for the overall response (0.0 to 1.0)
    confidence: float = Field(
        description="Confidence score for the results (0.0 to 1.0)",
        ge=0.0,
        le=1.0
    )

    # Sources consulted to generate these results
    sources: List[str] = Field(
        description="Data sources consulted to generate these results"
    )

    # Optional: Total count if pagination is supported
    total_count: Optional[int] = Field(
        default=None,
        description="Total number of results available (for pagination)"
    )

    # Optional: Query that was executed
    query_executed: Optional[str] = Field(
        default=None,
        description="The actual query that was executed"
    )
