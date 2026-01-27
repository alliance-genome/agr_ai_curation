"""Gene Ontology envelope schema.

Used by Gene Ontology specialist.
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class GeneOntologyEnvelope(StructuredMessageEnvelope):
    """Envelope for Gene Ontology specialist responses"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="gene_ontology_specialist", description="The Gene Ontology agent")
    findings: str = Field(description="Gene Ontology term information")
    go_terms: List[str] = Field(
        default_factory=list,
        description="GO term IDs (e.g., GO:0008150)"
    )
    go_names: List[str] = Field(
        default_factory=list,
        description="GO term names"
    )
    go_aspects: List[str] = Field(
        default_factory=list,
        description="GO aspects (biological_process, molecular_function, cellular_component)"
    )
