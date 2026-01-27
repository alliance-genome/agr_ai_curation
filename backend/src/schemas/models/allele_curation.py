"""Allele curation envelope schema.

Used by allele curation database specialist.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class AlleleCurationEnvelope(StructuredMessageEnvelope):
    """Envelope for allele curation database specialist responses"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="allele_curation_specialist", description="The allele curation agent")
    findings: str = Field(description="Allele information from curation database")
    allele_curies: List[str] = Field(
        default_factory=list,
        description="List of allele CURIEs found (e.g., WB:WBVar00000001)"
    )
    species: Optional[List[str]] = Field(
        default=None,
        description="Species/taxa mentioned (e.g., NCBITaxon:6239)"
    )
