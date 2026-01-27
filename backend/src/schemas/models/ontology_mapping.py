"""Ontology mapping envelope schema.

Used for ontology term mapping responses - organism-agnostic.
Maps human-readable labels to ontology term CURIEs.
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope
from .ontology_mapping_item import OntologyMapping


class OntologyMappingEnvelope(StructuredMessageEnvelope):
    """Envelope for ontology term mapping responses - organism-agnostic

    Maps human-readable labels (from gene_expression agent) to ontology term CURIEs.
    Receives labels via wave-based context propagation from prior gene_expression execution.
    """
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="ontology_mapping_specialist", description="The ontology mapping agent")
    organism: str = Field(description="Organism for ontology selection (e.g., 'WB', 'FB', 'ZFIN', 'MGI')")
    mappings: List[OntologyMapping] = Field(description="List of label-to-CURIE mappings")
    unmapped_labels: List[str] = Field(default_factory=list, description="Labels that could not be mapped to ontology terms")
