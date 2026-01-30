"""Gene ontology lookup agent schema.

This module defines the envelope schema for the gene ontology lookup agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> GOTermEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
from src.schemas.models.base import StructuredMessageEnvelope


class GOTermEnvelope(StructuredMessageEnvelope):
    """Envelope for gene ontology lookup agent responses.

    Contains GO term definitions and hierarchy information.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="gene_ontology_specialist",
        description="The gene ontology lookup agent"
    )
    findings: str = Field(
        description="GO term information and definitions"
    )
    go_ids: List[str] = Field(
        default_factory=list,
        description="List of GO IDs found (e.g., GO:0008150)"
    )
    go_terms: Optional[List[dict]] = Field(
        default=None,
        description="GO term details including name, namespace, definition"
    )
