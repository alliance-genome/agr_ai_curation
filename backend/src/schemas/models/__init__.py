"""
Schema models package with explicit registration.

IMPORTANT: When adding a new schema file, you MUST register it in SCHEMA_REGISTRY below.
This prevents accidentally registering helper classes and makes the schema contract explicit.
"""

from typing import Dict, Type
from pydantic import BaseModel

# Import base types (used by other schemas and callers)
from .base import (
    StructuredMessageEnvelope,
    Destination,
    RoutingPlan,
)

# Import envelope schemas
from .supervisor import SupervisorEnvelope
from .direct_response import DirectResponseEnvelope
from .no_document import NoDocumentEnvelope
from .synthesis import SynthesisEnvelope
from .pdf_specialist import PdfSpecialistEnvelope
from .gene_curation import GeneCurationEnvelope
from .gene_expression import GeneExpressionEnvelope
from .ontology_mapping import OntologyMappingEnvelope
from .pdf_extraction import PdfExtractionEnvelope
from .disease_ontology import DiseaseOntologyEnvelope
from .chemical_ontology import ChemicalOntologyEnvelope
from .allele_curation import AlleleCurationEnvelope
from .gene_ontology import GeneOntologyEnvelope
from .go_annotations import GoAnnotationsEnvelope
from .alliance_orthologs import AllianceOrthologsEnvelope

# Import special schemas
from .citation import Citation

# Import domain planning schemas
from .pdf_extraction_plan import PDFExtractionPlan
from .pdf_extraction_task import PDFExtractionTask
from .database_query_plan import DatabaseQueryPlan
from .crew_execution_task import CrewExecutionTask
from .external_api_plan import ExternalAPIPlan

# Import helper classes (for type hints, but NOT registered as schemas)
from .reagent import Reagent
from .expression_pattern import ExpressionPattern
from .expression_evidence import ExpressionEvidence
from .ontology_mapping_item import OntologyMapping


# Explicit registry: schema_name -> Pydantic model class
# This registry is used by schema_loader.py to:
# 1. Generate JSON schemas for OpenAI structured output
# 2. Get Pydantic models for response validation
#
# IMPORTANT: Only register top-level schemas here, not helper classes.
# Helper classes (Reagent, ExpressionPattern, etc.) are NOT schemas themselves.
SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    # Envelope schemas (15 total)
    'supervisor': SupervisorEnvelope,
    'direct_response': DirectResponseEnvelope,
    'no_document': NoDocumentEnvelope,
    'synthesis': SynthesisEnvelope,
    'pdf_specialist': PdfSpecialistEnvelope,
    'gene_curation': GeneCurationEnvelope,
    'gene_expression': GeneExpressionEnvelope,
    'ontology_mapping': OntologyMappingEnvelope,
    'pdf_extraction': PdfExtractionEnvelope,
    'disease_ontology': DiseaseOntologyEnvelope,
    'chemical_ontology': ChemicalOntologyEnvelope,
    'allele_curation': AlleleCurationEnvelope,
    'gene_ontology': GeneOntologyEnvelope,
    'go_annotations': GoAnnotationsEnvelope,
    'alliance_orthologs': AllianceOrthologsEnvelope,

    # Special schemas (1 total)
    'citation': Citation,

    # Domain planning schemas (5 total)
    'pdf_extraction_plan': PDFExtractionPlan,
    'pdf_extraction_task': PDFExtractionTask,
    'database_query_plan': DatabaseQueryPlan,
    'crew_execution_task': CrewExecutionTask,
    'external_api_plan': ExternalAPIPlan,
}

# Re-export all classes for backwards compatibility
__all__ = [
    # Registry
    'SCHEMA_REGISTRY',

    # Base types (used by other schemas and callers)
    'StructuredMessageEnvelope',
    'Destination',
    'RoutingPlan',

    # Envelope schemas
    'SupervisorEnvelope',
    'DirectResponseEnvelope',
    'NoDocumentEnvelope',
    'SynthesisEnvelope',
    'PdfSpecialistEnvelope',
    'GeneCurationEnvelope',
    'GeneExpressionEnvelope',
    'OntologyMappingEnvelope',
    'PdfExtractionEnvelope',
    'DiseaseOntologyEnvelope',
    'ChemicalOntologyEnvelope',
    'AlleleCurationEnvelope',
    'GeneOntologyEnvelope',
    'GoAnnotationsEnvelope',
    'AllianceOrthologsEnvelope',

    # Special schemas
    'Citation',

    # Domain planning schemas
    'PDFExtractionPlan',
    'PDFExtractionTask',
    'DatabaseQueryPlan',
    'CrewExecutionTask',
    'ExternalAPIPlan',

    # Helper classes (not in SCHEMA_REGISTRY)
    'Reagent',
    'ExpressionPattern',
    'ExpressionEvidence',
    'OntologyMapping',
]
