"""Chemical extraction agent schema.

This module defines the envelope schema for the chemical extraction agent.
The envelope class is discovered at startup and registered in the schema registry.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from src.schemas.models.base import StructuredMessageEnvelope


class ChemicalExtractionResultEnvelope(StructuredMessageEnvelope):
    """Envelope for chemical extraction responses."""

    model_config = ConfigDict(extra='forbid')
    __envelope_class__ = True

    actor: str = Field(
        default="chemical_extraction_specialist",
        description="The chemical extraction agent"
    )
    findings: str = Field(
        description="Summary of chemical assertions extracted from the paper"
    )
    chemical_terms: List[str] = Field(
        default_factory=list,
        description="Chemical terms retained by the extractor"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa context if explicitly identified"
    )
