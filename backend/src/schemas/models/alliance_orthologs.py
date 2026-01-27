"""Alliance orthologs envelope schema.

Used by Alliance orthologs specialist.
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class AllianceOrthologsEnvelope(StructuredMessageEnvelope):
    """Envelope for Alliance orthologs specialist responses"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="alliance_orthologs_specialist", description="The Alliance orthologs agent")
    findings: str = Field(description="Ortholog information from Alliance database")
    ortholog_pairs: List[str] = Field(
        default_factory=list,
        description="Ortholog gene pairs found"
    )
    species_compared: List[str] = Field(
        default_factory=list,
        description="Species included in orthology comparisons"
    )
