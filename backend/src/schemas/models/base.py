"""
Base classes and shared types for schema models.

This module contains foundational types used across all schema models:
- Destination: Routing destinations enum
- RoutingPlan: Dynamic routing plan for supervisor
- StructuredMessageEnvelope: Base class for all envelope schemas
"""

from typing import List, Optional
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Destination(str, Enum):
    """Routing destinations for message envelopes"""
    PDF_EXTRACTION = "pdf_extraction"
    DIRECT_RESPONSE = "direct_response"
    IMMEDIATE_RESPONSE = "immediate_response"
    NO_DOCUMENT_RESPONSE = "no_document_response"
    DISEASE_ONTOLOGY = "disease_ontology"
    PDF_AND_DISEASE = "pdf_and_disease"
    CHEMICAL_ONTOLOGY = "chemical_ontology"
    GENE_CURATION = "gene_curation"
    ALLELE_CURATION = "allele_curation"
    GENE_ONTOLOGY = "gene_ontology"
    GO_ANNOTATIONS = "go_annotations"
    ALLIANCE_ORTHOLOGS = "alliance_orthologs"
    GENE_EXPRESSION = "gene_expression"
    ONTOLOGY_MAPPING = "ontology_mapping"


class RoutingPlan(BaseModel):
    """Dynamic routing plan from supervisor - describes execution sequence"""
    model_config = ConfigDict(extra='forbid')

    needs_pdf: bool = Field(
        default=False,
        description="Whether PDF extraction is required"
    )
    ontologies_needed: List[str] = Field(
        default_factory=list,
        description="List of ontology identifiers needed (disease, gene, phenotype, etc.)"
    )
    entities_to_lookup: List[str] = Field(
        default_factory=list,
        description="Array of entities for batch processing. When query explicitly names discrete items (genes, chemicals, diseases, etc.), populate this array. The execution_order determines which handler processes them. Examples: ['cytidine', 'uridine', 'guanosine'] or ['BRCA1', 'TP53']. Leave empty for exploratory/general queries."
    )
    execution_order: List[str] = Field(
        default_factory=list,
        description="Ordered list of handler names to execute. Valid handlers: pdf_extraction, disease_ontology, chemical_ontology, gene_curation, allele_curation, gene_expression, ontology_mapping, gene_ontology, go_annotations, alliance_orthologs, synthesize"
    )


class ExclusionReasonCode(str, Enum):
    """Canonical reason codes for extractor exclusion decisions."""

    PREVIOUSLY_REPORTED = "previously_reported"
    NON_EXPERIMENTAL_CLAIM = "non_experimental_claim"
    MARKER_ONLY_VISUALIZATION = "marker_only_visualization"
    PROMOTER_DRIVEN_MARKER_LOCALIZATION = "promoter_driven_marker_localization"
    MUTANT_BACKGROUND_ONLY = "mutant_background_only"
    STRUCTURAL_LABEL_OR_FUSION_ONLY = "structural_label_or_fusion_only"
    INSUFFICIENT_EXPERIMENTAL_EVIDENCE = "insufficient_experimental_evidence"
    OUT_OF_SCOPE = "out_of_scope"
    AMBIGUOUS_ENTITY = "ambiguous_entity"
    DUPLICATE_MENTION = "duplicate_mention"
    UNSUPPORTED_ENTITY_TYPE = "unsupported_entity_type"


class EvidenceRecord(BaseModel):
    """Verified evidence record used to support keep/exclude decisions."""

    model_config = ConfigDict(extra='forbid')

    entity: Optional[str] = Field(default=None, description="Human-readable entity label the extractor/tool was evaluating")
    verified_quote: Optional[str] = Field(default=None, description="Verbatim paper text returned by evidence verification")
    page: Optional[int] = Field(default=None, ge=1, description="1-based page number if known")
    section: Optional[str] = Field(default=None, description="Document section containing the evidence")
    subsection: Optional[str] = Field(default=None, description="Subsection heading, if available")
    chunk_id: Optional[str] = Field(default=None, description="Source chunk identifier returned by the evidence tool, if available")
    figure_reference: Optional[str] = Field(default=None, description="Figure or table locator literal, if available")

    @field_validator("page", mode="before")
    @classmethod
    def _validate_page(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("must be an integer")
        return value

    @field_validator(
        "entity",
        "verified_quote",
        "section",
        "subsection",
        "chunk_id",
        "figure_reference",
        mode="before",
    )
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("must be a string")
        normalized = value.strip()
        return normalized or None


class MentionCandidate(BaseModel):
    """Raw mention harvested from the document before normalization."""

    model_config = ConfigDict(extra='forbid')

    mention: str = Field(description="Original mention text from the paper")
    entity_type: Optional[str] = Field(default=None, description="Entity type guess (gene, allele, disease, etc.)")
    evidence: List[EvidenceRecord] = Field(default_factory=list, description="Supporting evidence for the raw mention")


class ExtractionItem(BaseModel):
    """Normalized extractor item retained for downstream curation."""

    model_config = ConfigDict(extra='forbid')

    label: str = Field(description="Canonical label retained by the extractor")
    entity_type: Optional[str] = Field(default=None, description="Entity type (gene, expression, phenotype, etc.)")
    normalized_id: Optional[str] = Field(default=None, description="Normalized CURIE/ontology ID when resolved")
    source_mentions: List[str] = Field(default_factory=list, description="Raw mentions that support this normalized item")
    evidence: List[EvidenceRecord] = Field(default_factory=list, description="Evidence used to retain the item")


class ExclusionRecord(BaseModel):
    """Candidate rejected by extraction policy with explicit reason code."""

    model_config = ConfigDict(extra='forbid')

    mention: str = Field(description="Candidate mention that was excluded")
    reason_code: ExclusionReasonCode = Field(description="Canonical exclusion reason code")
    evidence: List[EvidenceRecord] = Field(default_factory=list, description="Evidence supporting the exclusion")
    details: Optional[str] = Field(default=None, description="Optional free-text explanation")


class AmbiguityRecord(BaseModel):
    """Candidate requiring curator follow-up due to unresolved ambiguity."""

    model_config = ConfigDict(extra='forbid')

    mention: str = Field(description="Ambiguous mention text")
    why_ambiguous: str = Field(description="Why normalization/classification is ambiguous")
    recommended_followup: Optional[str] = Field(default=None, description="Action suggested for curator follow-up")
    evidence: List[EvidenceRecord] = Field(default_factory=list, description="Evidence associated with ambiguity")


class ExtractionRunSummary(BaseModel):
    """Summary counters/warnings for a single extractor run."""

    model_config = ConfigDict(extra='forbid')

    candidate_count: int = Field(default=0, ge=0, description="Number of harvested candidates")
    kept_count: int = Field(default=0, ge=0, description="Number of retained normalized items")
    excluded_count: int = Field(default=0, ge=0, description="Number of excluded candidates")
    ambiguous_count: int = Field(default=0, ge=0, description="Number of ambiguous candidates")
    warnings: List[str] = Field(default_factory=list, description="Run-level warnings and caveats")


class StructuredMessageEnvelope(BaseModel):
    """Base envelope for all structured messages"""
    model_config = ConfigDict(extra='forbid')  # This ensures additionalProperties: false

    actor: str = Field(description="The agent/actor that generated this message")
    destination: Destination = Field(description="Where this message should be routed")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence level 0-1")
    reasoning: str = Field(description="Explanation of the decision or response")

    @classmethod
    def model_json_schema(cls, **kwargs):
        """Generate JSON schema for OpenAI API"""
        # Use mode='serialization' to get the proper schema
        schema = super().model_json_schema(mode='serialization', **kwargs)
        # Ensure all fields are required for OpenAI strict mode
        schema = cls._ensure_required_fields(schema)
        # Ensure additionalProperties is false (handled by ConfigDict now)
        return cls._ensure_strict_schema(schema)

    @classmethod
    def _ensure_required_fields(cls, schema: dict) -> dict:
        """Ensure all fields are marked as required for OpenAI strict mode"""
        if isinstance(schema, dict):
            # If this is an object with properties, make all properties required
            if schema.get("type") == "object" and "properties" in schema:
                # Get all property names
                property_names = list(schema["properties"].keys())
                # Set them all as required
                if property_names:
                    schema["required"] = property_names

                # Recursively process nested properties
                for prop_name, prop_schema in schema["properties"].items():
                    schema["properties"][prop_name] = cls._ensure_required_fields(prop_schema)

            # Process $defs if present (for nested models)
            if "$defs" in schema:
                for def_name, def_schema in schema["$defs"].items():
                    schema["$defs"][def_name] = cls._ensure_required_fields(def_schema)

            # Process items if it's an array
            if schema.get("type") == "array" and "items" in schema:
                schema["items"] = cls._ensure_required_fields(schema["items"])

            # Process all other nested schemas
            for key, value in schema.items():
                if key not in ["properties", "$defs", "items"] and isinstance(value, dict):
                    schema[key] = cls._ensure_required_fields(value)
                elif isinstance(value, list):
                    schema[key] = [cls._ensure_required_fields(item) if isinstance(item, dict) else item for item in value]

        return schema

    @classmethod
    def _ensure_strict_schema(cls, schema: dict) -> dict:
        """Ensure schema is strict for OpenAI (additionalProperties: false everywhere)"""
        if isinstance(schema, dict):
            # If it's an object type, ensure additionalProperties is false
            if schema.get("type") == "object":
                schema["additionalProperties"] = False

                # Fix properties with $ref - they can't have descriptions
                if "properties" in schema:
                    for prop_name, prop_schema in schema["properties"].items():
                        if isinstance(prop_schema, dict) and "$ref" in prop_schema:
                            # Remove all keys except $ref for OpenAI strict mode
                            schema["properties"][prop_name] = {"$ref": prop_schema["$ref"]}

            # Process all nested schemas
            for key, value in schema.items():
                if key != "properties" and isinstance(value, dict):
                    schema[key] = cls._ensure_strict_schema(value)
                elif isinstance(value, list):
                    schema[key] = [cls._ensure_strict_schema(item) if isinstance(item, dict) else item for item in value]

        return schema
