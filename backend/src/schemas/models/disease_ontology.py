"""Disease ontology envelope schema.

Used by disease ontology specialist.
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class DiseaseOntologyEnvelope(StructuredMessageEnvelope):
    """Envelope for disease ontology specialist responses"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="disease_ontology_specialist", description="The disease ontology agent")
    findings: str = Field(description="Disease ontology information retrieved")
    disease_terms: List[str] = Field(
        default_factory=list,
        description="Disease ontology term IDs (e.g., DOID:0014667)"
    )
    matched_names: List[str] = Field(
        default_factory=list,
        description="Disease names matched in the ontology"
    )
