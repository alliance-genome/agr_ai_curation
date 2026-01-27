"""Chemical ontology envelope schema.

Used by chemical ontology specialist.
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class ChemicalOntologyEnvelope(StructuredMessageEnvelope):
    """Envelope for chemical ontology specialist responses"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="chemical_ontology_specialist", description="The chemical ontology agent")
    findings: str = Field(description="Chemical ontology information retrieved")
    chemical_ids: List[str] = Field(
        default_factory=list,
        description="Chemical IDs (e.g., CHEBI:15422)"
    )
    chemical_names: List[str] = Field(
        default_factory=list,
        description="Chemical names matched"
    )
