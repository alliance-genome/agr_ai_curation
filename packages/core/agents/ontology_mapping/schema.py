"""Ontology mapping lookup agent schema.

This module defines the envelope schema for the ontology mapping lookup agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> OntologyMappingEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
from src.schemas.models.base import StructuredMessageEnvelope


class OntologyMappingEnvelope(StructuredMessageEnvelope):
    """Envelope for ontology mapping lookup agent responses.

    Contains mappings from free-text labels to ontology term IDs.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="ontology_mapping_specialist",
        description="The ontology mapping lookup agent"
    )
    findings: str = Field(
        description="Ontology mapping results"
    )
    mappings: List[dict] = Field(
        default_factory=list,
        description="List of term-to-ontology mappings"
    )
    unmapped_terms: Optional[List[str]] = Field(
        default=None,
        description="Terms that could not be mapped"
    )
