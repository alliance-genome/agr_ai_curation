"""Chemical validation agent schema.

This module defines the envelope schema for the chemical validation agent.
The envelope class is discovered at startup and registered in the schema registry.

Naming convention: {AgentFunction}Envelope -> ChemicalValidationEnvelope
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

# Import base class from shared schemas location
from backend.src.schemas.models.base import StructuredMessageEnvelope


class ChemicalValidationEnvelope(StructuredMessageEnvelope):
    """Envelope for chemical validation agent responses.

    Contains ChEBI ontology lookup results.
    """
    model_config = ConfigDict(extra='forbid')

    # Required: Marker for schema discovery
    __envelope_class__ = True

    actor: str = Field(
        default="chemical_validation_specialist",
        description="The chemical validation agent"
    )
    findings: str = Field(
        description="Chemical ontology information"
    )
    chebi_ids: List[str] = Field(
        default_factory=list,
        description="List of ChEBI IDs found (e.g., CHEBI:16236)"
    )
    chemical_names: Optional[List[str]] = Field(
        default=None,
        description="Human-readable chemical compound names"
    )
