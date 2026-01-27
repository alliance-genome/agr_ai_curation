"""
Shared Pydantic models for OpenAI Agents structured outputs.
"""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


# ============================================================================
# File Output Models
# ============================================================================

class FileInfo(BaseModel):
    """Information about a generated file output.

    This model is returned by file output tools (CSV, TSV, JSON formatters)
    and contains metadata needed for the frontend to render download links.

    Attributes:
        file_id: UUID for tracking and API endpoints
        filename: Full filename with extension (e.g., "genes_20250107T123456Z.csv")
        format: File format identifier ("csv", "tsv", "json")
        size_bytes: File size in bytes
        hash_sha256: SHA-256 hash for integrity verification
        mime_type: MIME type for download headers
        download_url: API endpoint for downloading the file
        created_at: Generation timestamp (UTC)
        trace_id: Langfuse trace ID for correlation
        session_id: Chat session ID
        curator_id: User who requested the file
    """
    file_id: str = Field(..., description="UUID for tracking and API endpoints")
    filename: str = Field(..., description="Full filename with extension")
    format: str = Field(..., description="File format: csv, tsv, or json")
    size_bytes: int = Field(..., description="File size in bytes")
    hash_sha256: str = Field(..., description="SHA-256 hash for integrity")
    mime_type: str = Field(..., description="MIME type for download")
    download_url: str = Field(..., description="API endpoint for download")
    created_at: datetime = Field(..., description="Generation timestamp (UTC)")
    trace_id: Optional[str] = Field(None, description="Langfuse trace ID")
    session_id: Optional[str] = Field(None, description="Chat session ID")
    curator_id: Optional[str] = Field(None, description="User who requested the file")


class Citation(BaseModel):
    chunk_id: Optional[str] = None
    section_title: Optional[str] = None
    page_number: Optional[int] = None
    source: Optional[str] = None  # tool or agent that produced the citation


class Answer(BaseModel):
    answer: str
    citations: List[Citation] = []
    sources: List[str] = []  # tools or agents involved in the answer


# ============================================================================
# Gene Expression Structured Output Models
# ============================================================================

class Reagent(BaseModel):
    """Reagent information for expression pattern annotation."""
    type: Optional[str] = Field(
        None,
        description="Type of reagent (e.g., 'transcriptional_fusion', 'translational_fusion', "
                    "'CRISPR_knockin', 'antibody')"
    )
    name: Optional[str] = Field(
        None,
        description="Name or construct ID (e.g., 'dmd-3::YFP', 'fsIs2')"
    )
    genotype: Optional[str] = Field(
        None,
        description="Full genotype syntax (e.g., 'fsIs2 [dmd-3::YFP, cc::GFP]')"
    )
    strain: Optional[str] = Field(
        None,
        description="Strain identifier (e.g., 'DF268', 'OH15732')"
    )


class ExpressionPattern(BaseModel):
    """A single expression pattern observation."""
    anatomy_label: Optional[str] = Field(
        None,
        description="Anatomical location label (e.g., 'linker cell', 'pharynx'). "
                    "Do NOT include ontology IDs - just the label."
    )
    life_stage_label: Optional[str] = Field(
        None,
        description="Developmental/life stage label (e.g., 'L3 larval stage', 'embryonic'). "
                    "Do NOT include ontology IDs - just the label."
    )
    go_cc_label: Optional[str] = Field(
        None,
        description="GO Cellular Component label (e.g., 'nucleus', 'cytoplasm'). "
                    "Do NOT include ontology IDs - just the label."
    )
    temporal_qualifier: Optional[str] = Field(
        None,
        description="Temporal qualifier (e.g., 'mid-stage II', 'early L4', 'during gastrulation')"
    )
    sex_specificity: Optional[str] = Field(
        None,
        description="Sex-specific expression (e.g., 'male only', 'hermaphrodite only')"
    )
    is_negative: bool = Field(
        False,
        description="True if this is negative evidence ('NOT expressed in...')"
    )


class ExpressionEvidence(BaseModel):
    """Evidence supporting the expression pattern."""
    text: Optional[str] = Field(
        None,
        description="Full evidence paragraph(s) from the paper"
    )
    page_numbers: List[int] = Field(
        default_factory=list,
        description="Page numbers where the evidence was found"
    )
    figure_references: List[str] = Field(
        default_factory=list,
        description="Figure references (e.g., ['Fig 2A', 'Figure 3B-D'])"
    )
    internal_citations: List[str] = Field(
        default_factory=list,
        description="Internal citations (e.g., ['Mason et al., 2008', 'Pereira et al., 2019'])"
    )


class GeneExpressionAnnotation(BaseModel):
    """
    A single gene expression annotation - one row per expression pattern.

    COMPLETELY FLAT structure (no nested lists) for strict JSON schema compatibility.
    Each annotation represents exactly ONE expression pattern observation.
    Reagent, Pattern, and Evidence fields are all inlined.

    If a gene has 3 expression patterns, create 3 separate annotations.
    """
    # Gene identification
    gene_symbol: str = Field(
        ...,
        description="Gene symbol (e.g., 'dmd-3', 'daf-16')"
    )
    gene_id: Optional[str] = Field(
        None,
        description="Gene identifier (e.g., 'WBGene00000915')"
    )

    # Inlined Reagent fields
    reagent_type: Optional[str] = Field(
        None,
        description="Type of reagent (e.g., 'transcriptional_fusion', 'translational_fusion', "
                    "'CRISPR_knockin', 'antibody')"
    )
    reagent_name: Optional[str] = Field(
        None,
        description="Name or construct ID (e.g., 'dmd-3::YFP', 'fsIs2')"
    )
    reagent_genotype: Optional[str] = Field(
        None,
        description="Full genotype syntax (e.g., 'fsIs2 [dmd-3::YFP, cc::GFP]')"
    )
    reagent_strain: Optional[str] = Field(
        None,
        description="Strain identifier (e.g., 'DF268', 'OH15732')"
    )

    # Inlined Expression Pattern fields (was nested ExpressionPattern object)
    anatomy_label: Optional[str] = Field(
        None,
        description="Anatomical location label (e.g., 'linker cell', 'pharynx'). "
                    "Do NOT include ontology IDs - just the label."
    )
    life_stage_label: Optional[str] = Field(
        None,
        description="Developmental/life stage label (e.g., 'L3 larval stage', 'embryonic'). "
                    "Do NOT include ontology IDs - just the label."
    )
    go_cc_label: Optional[str] = Field(
        None,
        description="GO Cellular Component label (e.g., 'nucleus', 'cytoplasm'). "
                    "Do NOT include ontology IDs - just the label."
    )
    temporal_qualifier: Optional[str] = Field(
        None,
        description="Temporal qualifier (e.g., 'mid-stage II', 'early L4', 'during gastrulation')"
    )
    sex_specificity: Optional[str] = Field(
        None,
        description="Sex-specific expression (e.g., 'male only', 'hermaphrodite only')"
    )
    is_negative: bool = Field(
        False,
        description="True if this is negative evidence ('NOT expressed in...')"
    )

    # Inlined Evidence fields
    evidence_text: Optional[str] = Field(
        None,
        description="Full evidence paragraph(s) from the paper"
    )
    evidence_page_numbers: List[int] = Field(
        default_factory=list,
        description="Page numbers where the evidence was found"
    )
    evidence_figure_references: List[str] = Field(
        default_factory=list,
        description="Figure references (e.g., ['Fig 2A', 'Figure 3B-D'])"
    )
    evidence_internal_citations: List[str] = Field(
        default_factory=list,
        description="Internal citations (e.g., ['Mason et al., 2008', 'Pereira et al., 2019'])"
    )


class GeneExpressionEnvelope(BaseModel):
    """
    Structured output for comprehensive Gene Expression extraction.

    Contains ALL gene expression annotations found in the document.
    Each annotation represents ONE expression pattern (flat, spreadsheet-like rows).
    If a gene has 3 expression patterns, there will be 3 annotations.
    """
    organism: Optional[str] = Field(
        None,
        description="Organism name (e.g., 'C. elegans', 'D. melanogaster'). "
                    "Set to None if no gene expression data was found in the document."
    )
    annotations: List[GeneExpressionAnnotation] = Field(
        default_factory=list,
        description="List of gene expression annotations, one per gene+reagent combination"
    )
    genes_found: List[str] = Field(
        default_factory=list,
        description="List of all gene symbols with expression data in this document"
    )
    summary: Optional[str] = Field(
        None,
        description="Brief summary of expression findings"
    )


# ============================================================================
# Gene Curation Structured Output Models
# ============================================================================

class GenomicLocation(BaseModel):
    """Genomic location information for a gene."""
    chromosome: Optional[str] = Field(None, description="Chromosome name")
    start: Optional[int] = Field(None, description="Start position")
    end: Optional[int] = Field(None, description="End position")
    strand: Optional[str] = Field(None, description="Strand (+ or -)")


class CrossReference(BaseModel):
    """Cross-reference to external database."""
    database: str = Field(..., description="Database name (e.g., 'NCBI', 'UniProt', 'Ensembl')")
    identifier: str = Field(..., description="Identifier in that database")
    url: Optional[str] = Field(None, description="URL to the resource")


class GeneResult(BaseModel):
    """
    Structured output for Gene Curation queries.

    Contains comprehensive gene information from the AGR Curation Database.
    """
    gene_id: str = Field(
        ...,
        description="Gene CURIE/ID in Alliance format (e.g., 'WB:WBGene00001234')"
    )
    symbol: str = Field(
        ...,
        description="Gene symbol (e.g., 'daf-16', 'FOXO3')"
    )
    name: Optional[str] = Field(
        None,
        description="Full gene name"
    )
    species: str = Field(
        ...,
        description="Species name (e.g., 'Caenorhabditis elegans')"
    )
    data_provider: str = Field(
        ...,
        description="Data provider code (WB, FB, MGI, HGNC, ZFIN, RGD, SGD)"
    )
    gene_type: Optional[str] = Field(
        None,
        description="Gene type (e.g., 'protein_coding', 'ncRNA')"
    )
    genomic_location: Optional[GenomicLocation] = Field(
        None,
        description="Genomic location information"
    )
    cross_references: List[CrossReference] = Field(
        default_factory=list,
        description="Cross-references to external databases"
    )
    synonyms: List[str] = Field(
        default_factory=list,
        description="Alternative gene symbols/names"
    )


class GeneResultEnvelope(BaseModel):
    """
    Envelope for returning multiple gene results.

    Use this when a query may return multiple genes (e.g., batch lookups,
    partial matches, or queries for multiple symbols).
    """
    results: List[GeneResult] = Field(
        default_factory=list,
        description="List of gene results"
    )
    query_summary: Optional[str] = Field(
        None,
        description="Brief summary of what was queried and found"
    )
    not_found: List[str] = Field(
        default_factory=list,
        description="List of symbols/IDs that were not found in the database"
    )


# ============================================================================
# Allele Curation Structured Output Models
# ============================================================================

class FullnameAttribution(BaseModel):
    """
    Attribution information extracted from allele fullname suffix.

    IMPORTANT: This is HEURISTIC extraction based on naming conventions.
    The extracted value is the text after the last comma in the fullname,
    which TYPICALLY contains creator or institution info for MGI/RGD alleles,
    but this is not guaranteed.

    The LLM should use this as a strong indicator but not definitive proof.
    Always cross-reference with the full allele name field.
    """
    value: str = Field(
        ...,
        description="The extracted text from the fullname suffix (e.g., 'Joshua Scallan', "
                    "'Centre for Modeling Human Disease')"
    )
    confidence: Literal["probable", "uncertain"] = Field(
        ...,
        description="Extraction confidence: 'probable' means the pattern matches expected "
                    "creator/institution format (2+ words), 'uncertain' means the regex matched "
                    "but the pattern is atypical (single word or unusual format)"
    )
    source: Literal["fullname_suffix"] = Field(
        "fullname_suffix",
        description="Data provenance - always 'fullname_suffix' indicating this was extracted "
                    "via regex from the allele's fullname field, not a verified author field"
    )


class AlleleResult(BaseModel):
    """
    Structured output for Allele Curation queries.

    Contains comprehensive allele information from the AGR Curation Database.
    """
    allele_id: str = Field(
        ...,
        description="Allele CURIE/ID in Alliance format (e.g., 'WB:WBVar00012345')"
    )
    symbol: str = Field(
        ...,
        description="Allele symbol (e.g., 'e1370', 'tm1978')"
    )
    name: Optional[str] = Field(
        None,
        description="Full allele name"
    )
    species: str = Field(
        ...,
        description="Species name (e.g., 'Caenorhabditis elegans')"
    )
    data_provider: str = Field(
        ...,
        description="Data provider code (WB, FB, MGI, HGNC, ZFIN, RGD, SGD)"
    )
    associated_gene: Optional[str] = Field(
        None,
        description="Associated gene symbol or ID"
    )
    is_obsolete: bool = Field(
        False,
        description="True if this allele is obsolete"
    )
    is_extinct: bool = Field(
        False,
        description="True if this allele is extinct"
    )
    synonyms: List[str] = Field(
        default_factory=list,
        description="Alternative allele symbols/names"
    )
    fullname_attribution: Optional[FullnameAttribution] = Field(
        None,
        description="Attribution info extracted from allele fullname suffix (MGI/RGD only). "
                    "HEURISTIC extraction - use as strong indicator, not definitive proof. "
                    "Null for WB/SGD (no fullnames), ZFIN (fullnames are IDs), FB (descriptive names), "
                    "or when extraction pattern doesn't match."
    )


class AlleleResultEnvelope(BaseModel):
    """
    Envelope for returning multiple allele results.

    Use this when a query may return multiple alleles (e.g., batch lookups,
    partial matches, or queries for multiple symbols).
    """
    results: List[AlleleResult] = Field(
        default_factory=list,
        description="List of allele results"
    )
    query_summary: Optional[str] = Field(
        None,
        description="Brief summary of what was queried and found"
    )
    not_found: List[str] = Field(
        default_factory=list,
        description="List of symbols/IDs that were not found in the database"
    )


# ============================================================================
# Disease Ontology Structured Output Models
# ============================================================================

class DiseaseRelationship(BaseModel):
    """A relationship to another disease term."""
    doid: str = Field(..., description="DOID of the related term (e.g., 'DOID:14330')")
    name: str = Field(..., description="Name of the related term")
    relationship_type: str = Field(..., description="Relationship type (e.g., 'is_a', 'part_of')")


class DiseaseResult(BaseModel):
    """
    Structured output for Disease Ontology queries.

    Contains disease information from the Disease Ontology database.
    """
    doid: str = Field(
        ...,
        description="Disease Ontology ID (e.g., 'DOID:14330')"
    )
    name: str = Field(
        ...,
        description="Disease name"
    )
    definition: Optional[str] = Field(
        None,
        description="Disease definition"
    )
    is_obsolete: bool = Field(
        False,
        description="True if this term is obsolete"
    )
    parents: List[DiseaseRelationship] = Field(
        default_factory=list,
        description="Parent disease terms"
    )
    children: List[DiseaseRelationship] = Field(
        default_factory=list,
        description="Child disease terms"
    )
    synonyms: List[str] = Field(
        default_factory=list,
        description="Disease synonyms"
    )
    xrefs: List[str] = Field(
        default_factory=list,
        description="Cross-references to other databases"
    )


class DiseaseResultEnvelope(BaseModel):
    """
    Envelope for returning multiple disease results.

    Use this when a query may return multiple diseases (e.g., batch lookups,
    partial matches, or queries for multiple terms).
    """
    results: List[DiseaseResult] = Field(
        default_factory=list,
        description="List of disease results"
    )
    query_summary: Optional[str] = Field(
        None,
        description="Brief summary of what was queried and found"
    )
    not_found: List[str] = Field(
        default_factory=list,
        description="List of terms/IDs that were not found in the database"
    )


# ============================================================================
# Chemical Ontology (ChEBI) Structured Output Models
# ============================================================================

class ChemicalClassification(BaseModel):
    """A classification/parent in the ChEBI ontology."""
    chebi_id: str = Field(..., description="ChEBI ID of the parent (e.g., 'CHEBI:24651')")
    name: str = Field(..., description="Name of the parent term")
    relationship: str = Field(
        "is_a",
        description="Relationship type (e.g., 'is_a', 'has_role')"
    )


class ChemicalResult(BaseModel):
    """
    Structured output for Chemical Ontology (ChEBI) queries.

    Contains chemical entity information from the ChEBI database.
    """
    chebi_id: str = Field(
        ...,
        description="ChEBI ID (e.g., 'CHEBI:17234')"
    )
    name: str = Field(
        ...,
        description="Chemical name"
    )
    definition: Optional[str] = Field(
        None,
        description="Chemical definition"
    )
    formula: Optional[str] = Field(
        None,
        description="Molecular formula (e.g., 'C6H12O6')"
    )
    inchi: Optional[str] = Field(
        None,
        description="InChI identifier"
    )
    smiles: Optional[str] = Field(
        None,
        description="SMILES structure"
    )
    classifications: List[ChemicalClassification] = Field(
        default_factory=list,
        description="Parent classifications in the ontology"
    )
    synonyms: List[str] = Field(
        default_factory=list,
        description="Chemical synonyms"
    )


class ChemicalResultEnvelope(BaseModel):
    """
    Envelope for returning multiple chemical results.

    Use this when a query may return multiple chemicals (e.g., batch lookups,
    partial matches, or queries for multiple terms).
    """
    results: List[ChemicalResult] = Field(
        default_factory=list,
        description="List of chemical results"
    )
    query_summary: Optional[str] = Field(
        None,
        description="Brief summary of what was queried and found"
    )
    not_found: List[str] = Field(
        default_factory=list,
        description="List of terms/IDs that were not found in the database"
    )


# ============================================================================
# Gene Ontology (GO) Structured Output Models
# ============================================================================

class GORelationship(BaseModel):
    """A relationship to another GO term."""
    go_id: str = Field(..., description="GO ID (e.g., 'GO:0003677')")
    name: str = Field(..., description="GO term name")
    relationship_type: str = Field(..., description="Relationship type (e.g., 'is_a', 'part_of')")


class GOTermResult(BaseModel):
    """
    Structured output for Gene Ontology term queries.

    Contains GO term information from the QuickGO API.
    """
    go_id: str = Field(
        ...,
        description="GO ID (e.g., 'GO:0003677')"
    )
    name: str = Field(
        ...,
        description="GO term name"
    )
    aspect: str = Field(
        ...,
        description="GO aspect: 'molecular_function', 'biological_process', or 'cellular_component'"
    )
    definition: Optional[str] = Field(
        None,
        description="GO term definition"
    )
    is_obsolete: bool = Field(
        False,
        description="True if this term is obsolete"
    )
    children: List[GORelationship] = Field(
        default_factory=list,
        description="Direct child terms (more specific)"
    )
    ancestors: List[GORelationship] = Field(
        default_factory=list,
        description="Ancestor terms (more general)"
    )
    synonyms: List[str] = Field(
        default_factory=list,
        description="GO term synonyms"
    )


class GOTermResultEnvelope(BaseModel):
    """
    Envelope for returning multiple GO term results.

    Use this when a query may return multiple GO terms (e.g., batch lookups,
    partial matches, or queries for multiple terms).
    """
    results: List[GOTermResult] = Field(
        default_factory=list,
        description="List of GO term results"
    )
    query_summary: Optional[str] = Field(
        None,
        description="Brief summary of what was queried and found"
    )
    not_found: List[str] = Field(
        default_factory=list,
        description="List of terms/IDs that were not found"
    )


# ============================================================================
# GO Annotations Structured Output Models
# ============================================================================

class GOAnnotation(BaseModel):
    """A single GO annotation for a gene."""
    go_id: str = Field(..., description="GO term ID (e.g., 'GO:0003677')")
    go_name: str = Field(..., description="GO term name")
    aspect: str = Field(..., description="GO aspect (MF, BP, CC)")
    evidence_code: str = Field(..., description="Evidence code (e.g., 'IDA', 'IMP', 'IEA')")
    evidence_label: Optional[str] = Field(None, description="Evidence code description")
    assigned_by: Optional[str] = Field(None, description="Curation source")
    is_manual: bool = Field(
        False,
        description="True if this is a manually curated annotation"
    )
    qualifier: Optional[str] = Field(
        None,
        description="Qualifier (e.g., 'NOT', 'contributes_to')"
    )


class GOAnnotationsResult(BaseModel):
    """
    Structured output for GO Annotations queries.

    Contains GO annotations for a specific gene.
    """
    gene_id: str = Field(
        ...,
        description="Gene ID in Alliance format (e.g., 'WB:WBGene00000898')"
    )
    gene_symbol: Optional[str] = Field(
        None,
        description="Gene symbol"
    )
    annotations: List[GOAnnotation] = Field(
        default_factory=list,
        description="List of GO annotations"
    )
    manual_count: int = Field(
        0,
        description="Number of manually curated annotations"
    )
    automatic_count: int = Field(
        0,
        description="Number of automatic/electronic annotations"
    )


# ============================================================================
# Orthologs Structured Output Models
# ============================================================================

class OrthologGene(BaseModel):
    """Information about an ortholog gene."""
    gene_id: str = Field(..., description="Gene ID in Alliance format")
    symbol: str = Field(..., description="Gene symbol")
    species: str = Field(..., description="Species name")
    data_provider: str = Field(..., description="Data provider (WB, FB, MGI, HGNC, etc.)")


class OrthologRelationship(BaseModel):
    """A single orthology relationship."""
    ortholog: OrthologGene = Field(..., description="The ortholog gene")
    confidence: str = Field(..., description="Confidence level (high, moderate, low)")
    is_best_score: bool = Field(False, description="Whether this is the best scoring ortholog")
    methods_matched: List[str] = Field(
        default_factory=list,
        description="Prediction methods that support this orthology"
    )
    methods_not_matched: List[str] = Field(
        default_factory=list,
        description="Prediction methods that don't support this orthology"
    )


class OrthologsResult(BaseModel):
    """
    Structured output for Orthologs queries.

    Contains orthology relationships for a gene.
    """
    query_gene: OrthologGene = Field(
        ...,
        description="The gene that was queried"
    )
    orthologs: List[OrthologRelationship] = Field(
        default_factory=list,
        description="List of ortholog relationships"
    )
    high_confidence_count: int = Field(
        0,
        description="Number of high-confidence orthologs"
    )
    species_represented: List[str] = Field(
        default_factory=list,
        description="List of species with orthologs found"
    )


# ============================================================================
# Ontology Mapping Structured Output Models
# ============================================================================

class OntologyMapping(BaseModel):
    """A mapping from a label to an ontology term."""
    label: str = Field(..., description="The input label that was mapped")
    curie: Optional[str] = Field(
        None,
        description="The mapped ontology CURIE (e.g., 'WBbt:0005062', 'GO:0005634')"
    )
    name: Optional[str] = Field(
        None,
        description="The official ontology term name"
    )
    ontology_type: Optional[str] = Field(
        None,
        description="Type of ontology (e.g., 'WBBTTerm', 'WBLSTerm', 'GOTerm')"
    )
    confidence: str = Field(
        "low",
        description="Mapping confidence: 'high' (exact), 'medium' (fuzzy), 'low' (no match)"
    )
    alternatives: List[str] = Field(
        default_factory=list,
        description="Alternative CURIEs if multiple matches found"
    )


class OntologyMappingEnvelope(BaseModel):
    """
    Structured output for Ontology Mapping queries.

    Contains mappings from labels to ontology term IDs.
    """
    organism: str = Field(
        ...,
        description="Data provider code (WB, FB, MGI, etc.)"
    )
    mappings: List[OntologyMapping] = Field(
        default_factory=list,
        description="List of label-to-term mappings"
    )
    unmapped_labels: List[str] = Field(
        default_factory=list,
        description="Labels that could not be mapped"
    )
    reasoning: Optional[str] = Field(
        None,
        description="Explanation of mapping process and decisions"
    )
