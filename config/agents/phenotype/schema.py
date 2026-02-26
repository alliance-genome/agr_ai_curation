"""Phenotype extraction agent schema.

This module defines the envelope schema for the phenotype extraction agent.
The envelope class is discovered at startup and registered in the schema registry.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from src.schemas.models.base import StructuredMessageEnvelope


class PhenotypeExtractionEnvelope(StructuredMessageEnvelope):
    """Envelope for phenotype extraction responses."""

    model_config = ConfigDict(extra='forbid')
    __envelope_class__ = True

    actor: str = Field(
        default="phenotype_extraction_specialist",
        description="The phenotype extraction agent"
    )
    findings: str = Field(
        description="Summary of phenotype assertions extracted from the paper"
    )
    phenotype_terms: List[str] = Field(
        default_factory=list,
        description="Phenotype terms retained by the extractor"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa context if explicitly identified"
    )
