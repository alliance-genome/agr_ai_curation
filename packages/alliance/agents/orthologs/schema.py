"""Orthologs lookup agent schema.

This module defines the envelope schema for the orthologs lookup agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> OrthologsEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
from src.schemas.models.base import StructuredMessageEnvelope


class OrthologsEnvelope(StructuredMessageEnvelope):
    """Envelope for orthologs lookup agent responses.

    Contains ortholog relationships across species from the Alliance Orthology Database.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="orthologs_specialist",
        description="The orthologs lookup agent"
    )
    findings: str = Field(
        description="Ortholog relationship information"
    )
    query_gene: Optional[str] = Field(
        default=None,
        description="Gene CURIE that was queried for orthologs"
    )
    orthologs: List[dict] = Field(
        default_factory=list,
        description="List of ortholog records with species and confidence"
    )
