"""Disease validation agent schema.

This module defines the envelope schema for the disease validation agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> DiseaseValidationEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
from backend.src.schemas.models.base import StructuredMessageEnvelope


class DiseaseValidationEnvelope(StructuredMessageEnvelope):
    """Envelope for disease validation agent responses.

    Contains disease ontology lookup results from DOID.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="disease_validation_specialist",
        description="The disease validation agent"
    )
    findings: str = Field(
        description="Disease ontology information"
    )
    disease_ids: List[str] = Field(
        default_factory=list,
        description="List of Disease Ontology IDs found (e.g., DOID:0050686)"
    )
    disease_names: Optional[List[str]] = Field(
        default=None,
        description="Human-readable disease names"
    )
