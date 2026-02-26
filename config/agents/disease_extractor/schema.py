"""Disease extraction agent schema.

This module defines the envelope schema for the disease extraction agent.
The envelope class is discovered at startup and registered in the schema registry.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from src.schemas.models.base import StructuredMessageEnvelope


class DiseaseExtractionResultEnvelope(StructuredMessageEnvelope):
    """Envelope for disease extraction responses."""

    model_config = ConfigDict(extra='forbid')
    __envelope_class__ = True

    actor: str = Field(
        default="disease_extraction_specialist",
        description="The disease extraction agent"
    )
    findings: str = Field(
        description="Summary of disease assertions extracted from the paper"
    )
    disease_terms: List[str] = Field(
        default_factory=list,
        description="Disease terms retained by the extractor"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa context if explicitly identified"
    )
