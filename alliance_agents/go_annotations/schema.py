"""GO annotations lookup agent schema.

This module defines the envelope schema for the GO annotations lookup agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> GOAnnotationsEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
from src.schemas.models.base import StructuredMessageEnvelope


class GOAnnotationsEnvelope(StructuredMessageEnvelope):
    """Envelope for GO annotations lookup agent responses.

    Contains existing GO annotations for genes from QuickGO.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="go_annotations_specialist",
        description="The GO annotations lookup agent"
    )
    findings: str = Field(
        description="GO annotations for queried genes"
    )
    gene_id: Optional[str] = Field(
        default=None,
        description="Gene CURIE that was queried"
    )
    annotations: List[dict] = Field(
        default_factory=list,
        description="List of GO annotations with evidence codes"
    )
