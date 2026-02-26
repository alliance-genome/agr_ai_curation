"""Allele/variant extraction agent schema.

This module defines the envelope schema for the allele extraction agent.
The envelope class is discovered at startup and registered in the schema registry.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from src.schemas.models.base import StructuredMessageEnvelope


class AlleleExtractionResultEnvelope(StructuredMessageEnvelope):
    """Envelope for allele extraction responses."""

    model_config = ConfigDict(extra='forbid')
    __envelope_class__ = True

    actor: str = Field(
        default="allele_variant_extraction_specialist",
        description="The allele/variant extraction agent"
    )
    findings: str = Field(
        description="Summary of allele/variant assertions extracted from the paper"
    )
    allele_terms: List[str] = Field(
        default_factory=list,
        description="Allele/variant terms retained by the extractor"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa context if explicitly identified"
    )


# Backward-compatible alias for early draft references.
AlleleVariantExtractionEnvelope = AlleleExtractionResultEnvelope
