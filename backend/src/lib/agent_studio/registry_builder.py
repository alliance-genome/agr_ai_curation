"""
Registry Builder - Builds AGENT_REGISTRY from YAML configurations.

This module provides the bridge between config-driven agent definitions
and the AGENT_REGISTRY used by catalog_service.py.

YAML files (config/agents/*/agent.yaml) are the source of truth.
This module builds the registry dynamically at startup.
"""

import logging
from typing import Any, Callable, Dict, Optional

from src.lib.config.agent_loader import (
    AgentDefinition,
    ModelConfig,
    load_agent_definitions,
    get_agent_definition,
    get_agent_by_folder,
)
from src.lib.config.agent_factory import get_agent_factory

logger = logging.getLogger(__name__)


def _build_config_defaults(model_config: ModelConfig) -> Dict[str, Any]:
    """
    Build config_defaults dict from YAML model_config.

    Only includes non-default values to avoid overriding env var behavior.
    These values become the fallback when no env var is set.

    Priority in get_agent_config():
    1. Environment variable (highest)
    2. config_defaults from YAML (this)
    3. Global fallback defaults (lowest)

    Args:
        model_config: ModelConfig from agent.yaml

    Returns:
        Dictionary with model, temperature, reasoning (only non-default values)
    """
    # Compare against ModelConfig defaults to avoid hardcoding values here.
    # This ensures changes to ModelConfig defaults automatically propagate.
    default_config = ModelConfig()
    defaults = {}

    # Include model if not the default
    if model_config.model != default_config.model:
        defaults["model"] = model_config.model

    # Include temperature if not the default
    if model_config.temperature != default_config.temperature:
        defaults["temperature"] = model_config.temperature

    # Include reasoning if not the default
    if model_config.reasoning != default_config.reasoning:
        defaults["reasoning"] = model_config.reasoning

    return defaults

# Static documentation for agents (help text for frontend)
#
# NOTE: This is intentionally separate from agent.yaml files because:
# 1. It's verbose UI content (examples, capabilities, limitations) that would
#    bloat the YAML files and make them harder to maintain
# 2. Not all agents need extensive documentation - many just need the brief
#    description from YAML
# 3. Documentation is presentation-layer concern, not agent configuration
# 4. Allows documentation to be updated without touching agent configs
#
# If YAML-based documentation is desired in the future, consider a separate
# docs.yaml file per agent or a dedicated documentation directory.
AGENT_DOCUMENTATION: Dict[str, Dict[str, Any]] = {
    "task_input": {
        "summary": "The starting point for curation workflows - defines what task the AI should perform.",
        "capabilities": [
            {
                "name": "Define extraction tasks",
                "description": "Tell the AI what data to extract from the paper",
                "example_query": "Extract all gene names and their expression patterns from this paper",
                "example_result": "The flow begins processing your request through the configured agents",
            },
            {
                "name": "Set curation context",
                "description": "Provide background information about what you're looking for",
                "example_query": "This paper describes insulin signaling in C. elegans. Find all relevant genes.",
                "example_result": "Context is passed to downstream agents to improve accuracy",
            },
        ],
        "data_sources": [],
        "limitations": [
            "Instructions should be clear and specific for best results",
            "Complex multi-part requests may need to be broken into separate flows",
        ],
    },
    "supervisor": {
        "summary": "Routes curator queries to the appropriate specialist agent - works behind the scenes.",
        "capabilities": [
            {
                "name": "Query routing",
                "description": "Automatically determines which specialist agent should handle your question",
                "example_query": "Find the gene daf-2",
                "example_result": "Routes to Gene Validation Agent",
            },
            {
                "name": "Multi-step coordination",
                "description": "Coordinates complex queries that need multiple agents",
                "example_query": "Find all genes mentioned in the paper and validate them",
                "example_result": "Routes to PDF Specialist first, then Gene Validation Agent",
            },
        ],
        "data_sources": [],
        "limitations": [
            "Routing decisions are based on query keywords and context",
            "Very ambiguous queries may be routed to a general agent first",
            "Not available in Flow Builder - works automatically in chat",
        ],
    },
    "pdf": {
        "summary": "Extracts text, tables, and structured data from scientific papers using hybrid search.",
        "capabilities": [
            {
                "name": "Full-text search",
                "description": "Find specific content within the PDF using semantic and keyword search",
                "example_query": "Find all mentions of insulin signaling pathway",
                "example_result": "Returns relevant passages with page numbers",
            },
            {
                "name": "Table extraction",
                "description": "Extract data tables and convert to structured format",
                "example_query": "Extract the gene expression table from results section",
                "example_result": "Returns structured data with headers and values",
            },
            {
                "name": "Section navigation",
                "description": "Read specific sections of the paper",
                "example_query": "Read the Methods section",
                "example_result": "Returns full text of the specified section",
            },
        ],
        "data_sources": [
            {
                "name": "PDF Documents",
                "description": "Scientific papers indexed in Weaviate",
                "species_supported": None,
                "data_types": ["text", "tables", "figures", "references"],
            },
        ],
        "limitations": [
            "Document must be loaded in Weaviate before use",
            "Very large tables may be truncated",
            "Figure analysis is limited to captions only",
        ],
    },
    "gene_validation": {
        "summary": "Validates gene identifiers against the Alliance Curation Database.",
        "capabilities": [
            {
                "name": "Gene lookup",
                "description": "Find genes by symbol, name, ID, or cross-reference",
                "example_query": "Look up the gene daf-16",
                "example_result": "Returns gene ID, symbol, name, species, and synonyms",
            },
            {
                "name": "Batch validation",
                "description": "Validate multiple genes at once",
                "example_query": "Look up these genes: daf-16, lin-3, unc-54, act-1",
                "example_result": "Returns validation results for each gene",
            },
        ],
        "data_sources": [
            {
                "name": "Alliance Curation Database",
                "description": "Comprehensive gene data from all MODs",
                "species_supported": [
                    "C. elegans", "D. melanogaster", "D. rerio",
                    "H. sapiens", "M. musculus", "R. norvegicus", "S. cerevisiae"
                ],
                "data_types": ["genes", "symbols", "synonyms", "cross-references"],
            },
        ],
        "limitations": [
            "Only validates against Alliance MOD data",
            "Some newly published genes may not be in the database yet",
        ],
    },
    "gene_ontology_lookup": {
        "summary": "Queries the Gene Ontology (GO) database via the QuickGO REST API to retrieve GO term information, hierarchy, and relationships across the three aspects: Molecular Function (MF), Biological Process (BP), and Cellular Component (CC).",
        "capabilities": [
            {
                "name": "Search GO terms by name or keyword",
                "description": "Search for GO terms using name keywords or partial matches. Results are ranked by relevance with obsolete terms flagged.",
                "example_query": "Find the GO term for 'DNA binding'",
                "example_result": "GO:0003677 (DNA binding) - molecular_function",
            },
            {
                "name": "Get GO term information by ID",
                "description": "Retrieve detailed information about a GO term including name, aspect, definition, and obsolete status.",
                "example_query": "What is GO:0003677?",
                "example_result": "GO:0003677 - DNA binding (molecular_function): Interacting selectively and non-covalently with DNA.",
            },
            {
                "name": "Navigate GO term hierarchy (children)",
                "description": "Get direct child terms (more specific) of a GO term with relationship types (is_a, part_of, regulates).",
                "example_query": "What are the children of GO:0003677 (DNA binding)?",
                "example_result": "Children include: GO:0003690 (double-stranded DNA binding) - is_a, GO:0003697 (single-stranded DNA binding) - is_a",
            },
            {
                "name": "Navigate GO term hierarchy (ancestors)",
                "description": "Get all ancestor terms (broader/more general) of a GO term, showing the path to root terms.",
                "example_query": "What are the ancestors of GO:0003677?",
                "example_result": "Ancestors include: GO:0003676 (nucleic acid binding), GO:0005488 (binding), GO:0003674 (molecular_function)",
            },
            {
                "name": "Batch GO term lookups",
                "description": "Look up multiple GO terms in a single request for efficient batch processing.",
                "example_query": "Look up these GO terms: apoptotic process, kinase activity, cell division",
                "example_result": "Returns structured results for all requested terms with not_found list for any missing",
            },
        ],
        "data_sources": [
            {
                "name": "QuickGO REST API",
                "description": "EMBL-EBI's QuickGO service providing programmatic access to the Gene Ontology database with term information, hierarchy, and relationships.",
                "species_supported": ["All species (GO is species-independent ontology)"],
                "data_types": ["GO term definitions and metadata", "GO hierarchy relationships (is_a, part_of, regulates)", "GO term synonyms and secondary IDs", "Obsolete term status"],
            },
        ],
        "limitations": [
            "Cannot retrieve gene-to-GO annotations (use GO Annotations Agent instead)",
            "Cannot determine which genes have a specific GO term (use GO Annotations Agent instead)",
            "Does not perform GO enrichment analysis",
            "All responses must come from live QuickGO API queries - does not use cached/trained knowledge",
            "API requests are restricted to ebi.ac.uk domain only",
        ],
    },
    "go_annotations_lookup": {
        "summary": "Retrieves existing GO annotations for specific genes from the Gene Ontology Consortium API, including evidence codes, curation sources, and annotation counts.",
        "capabilities": [
            {
                "name": "Gene annotation lookup",
                "description": "Retrieve all GO annotations for a specific gene, showing the functions, processes, and cellular locations associated with that gene",
                "example_query": "What GO annotations exist for WB:WBGene00000898?",
                "example_result": "Returns annotations like GO:0003677 (DNA binding) with evidence code IDA, assigned by WB",
            },
            {
                "name": "Evidence code analysis",
                "description": "Distinguish between manually curated annotations (IDA, IMP, IPI) and automatic/electronic annotations (IEA, IBA)",
                "example_query": "What manually curated annotations does daf-2 have?",
                "example_result": "Returns annotations filtered by is_manual=True with counts of manual vs automatic annotations",
            },
            {
                "name": "Batch annotation retrieval",
                "description": "Retrieve GO annotations for multiple genes in a single request",
                "example_query": "Get GO annotations for: WB:WBGene00000912, WB:WBGene00001234",
                "example_result": "Returns annotations for each gene with evidence codes and sources",
            },
            {
                "name": "GO aspect classification",
                "description": "Categorize annotations by GO aspect: Molecular Function (MF), Biological Process (BP), or Cellular Component (CC)",
                "example_query": "What molecular functions does this gene have?",
                "example_result": "Returns annotations filtered by aspect with go_id, go_name, and evidence",
            },
        ],
        "data_sources": [
            {
                "name": "Gene Ontology Consortium API",
                "description": "Official GO API at api.geneontology.org providing gene annotations with evidence codes",
                "species_supported": ["C. elegans (WB:)", "D. melanogaster (FB:)", "D. rerio (ZFIN:)", "H. sapiens (HGNC:)", "M. musculus (MGI:)", "R. norvegicus (RGD:)", "S. cerevisiae (SGD:)"],
                "data_types": ["GO annotations", "evidence codes", "curation sources", "qualifiers"],
            },
        ],
        "limitations": [
            "Gene IDs must be in Alliance format (e.g., WB:WBGene00000898, HGNC:11998)",
            "Cannot search for GO terms by keyword - use Gene Ontology Lookup Agent for that",
            "Cannot find genes by GO term (e.g., 'what genes have kinase activity')",
            "Cannot explore GO term hierarchy or parent/child relationships",
            "Heavily annotated genes may have 500+ IEA annotations - manual annotations are prioritized when summarizing",
        ],
    },
    "allele_validation": {
        "summary": "Validates allele/variant identifiers against the Alliance Curation Database by symbol, ID, or gene association.",
        "capabilities": [
            {
                "name": "Allele search (partial match)",
                "description": "Search for alleles by symbol using LIKE matching - supports partial matches, synonyms, and case-insensitive search",
                "example_query": "Find all Ulk1 alleles in mouse",
                "example_result": "Returns all alleles matching 'Ulk1' (e.g., Ulk1<sup>tm1Thsn</sup>) with IDs, symbols, names, and species",
            },
            {
                "name": "Exact symbol lookup",
                "description": "Find an allele by its exact official symbol, with automatic conversion from paper notation (Gene<allele>) to database format (Gene<sup>allele</sup>)",
                "example_query": "Find the allele Ulk1<tm1Thsn>",
                "example_result": "Returns the exact matching allele with full details including MGI:3689906",
            },
            {
                "name": "Allele ID lookup",
                "description": "Retrieve allele details directly by Alliance CURIE",
                "example_query": "Tell me about MGI:3689906",
                "example_result": "Returns allele symbol, name, species, and status for the given ID",
            },
            {
                "name": "Batch validation",
                "description": "Look up multiple alleles in a single request",
                "example_query": "Look up these alleles: e1370, n765, tm1234",
                "example_result": "Returns validation results for each allele, with not_found list for any missing",
            },
        ],
        "data_sources": [
            {
                "name": "Alliance Curation Database (AGR)",
                "description": "Comprehensive allele/variant data from all Model Organism Databases, including symbols, names, synonyms, obsolete status, and extinction status",
                "species_supported": ["C. elegans (WB)", "D. melanogaster (FB)", "D. rerio (ZFIN)", "H. sapiens (HGNC)", "M. musculus (MGI)", "R. norvegicus (RGD)", "S. cerevisiae (SGD)"],
                "data_types": ["allele symbols", "allele IDs (CURIEs)", "full names", "synonyms", "gene associations", "obsolete/extinct status"],
            },
        ],
        "limitations": [
            "Only validates against Alliance MOD data - newly published alleles may not be in the database yet",
            "Searches alleles only - genes, diseases, and phenotypes are handled by separate agents",
            "Angle bracket notation in symbols must be converted to <sup> format for exact matches (handled automatically)",
            "Very large result sets are capped at 500 results (default 100)",
        ],
    },
    "orthologs_lookup": {
        "summary": "Retrieves ortholog relationships across species using the Alliance of Genome Resources API with DIOPT-based confidence scoring.",
        "capabilities": [
            {
                "name": "Find gene orthologs",
                "description": "Query orthologs for a given gene ID across all supported species (human, mouse, fly, worm, zebrafish, yeast, rat)",
                "example_query": "Find orthologs for WB:WBGene00000898 (daf-16)",
                "example_result": "Returns human FOXO3 (HGNC:3821) with high confidence, mouse Foxo3 (MGI:1890077) with high confidence, plus orthologs in other species",
            },
            {
                "name": "Get orthology confidence scores",
                "description": "Retrieve confidence levels (high, moderate, low) based on agreement between multiple prediction algorithms",
                "example_query": "What is the confidence for the daf-16 to FOXO3 orthology?",
                "example_result": "High confidence - 8 of 10 algorithms agree (Ensembl Compara, InParanoid, OMA, OrthoFinder, etc.)",
            },
            {
                "name": "Identify best-scoring orthologs",
                "description": "Find the best-scoring ortholog in each species using the isBestScore flag",
                "example_query": "What is the best human ortholog for daf-2?",
                "example_result": "INSR (HGNC:6091) - isBestScore: Yes",
            },
            {
                "name": "List prediction methods",
                "description": "Show which orthology prediction algorithms support or do not support each relationship",
                "example_query": "Which algorithms predict the daf-16/FOXO3 orthology?",
                "example_result": "Matched: Ensembl Compara, InParanoid, OMA, PANTHER; Not matched: Xenbase, Hieranoid",
            },
        ],
        "data_sources": [
            {
                "name": "Alliance of Genome Resources Orthology API",
                "description": "Public REST API providing DIOPT-aggregated orthology predictions from multiple algorithms",
                "species_supported": ["C. elegans (WB)", "D. melanogaster (FB)", "M. musculus (MGI)", "H. sapiens (HGNC)", "D. rerio (ZFIN)", "S. cerevisiae (SGD)", "R. norvegicus (RGD)"],
                "data_types": ["ortholog relationships", "confidence scores", "prediction methods", "gene identifiers"],
            },
        ],
        "limitations": [
            "Gene ID must be in Alliance format with prefix (e.g., WB:WBGene00000898, not just WBGene00000898)",
            "Cannot search by gene symbol alone - requires resolved gene ID from Gene Validation Agent first",
            "Does not find paralogs (within-species gene duplications)",
            "Some genes have no orthologs in certain species (this is valid biological data, not an error)",
        ],
    },
    "disease_validation": {
        "summary": "Maps disease terms to Disease Ontology (DOID) identifiers by querying the Alliance Curation Database's ontologyterm tables.",
        "capabilities": [
            {
                "name": "Disease name lookup",
                "description": "Find DOID identifiers for disease names using case-insensitive search",
                "example_query": "Look up Alzheimer's disease",
                "example_result": "Returns DOID:10652, name, definition, and synonyms",
            },
            {
                "name": "DOID lookup",
                "description": "Retrieve disease information by DOID identifier",
                "example_query": "What is DOID:14330?",
                "example_result": "Returns Parkinson's disease with definition and relationships",
            },
            {
                "name": "Synonym search",
                "description": "Find diseases by synonym names when the exact term is not found",
                "example_query": "Look up Alzheimers dementia",
                "example_result": "Finds DOID:10652 via synonym match",
            },
            {
                "name": "Hierarchy exploration",
                "description": "Find parent (ancestor) or child (descendant) terms in the disease ontology",
                "example_query": "What are the parent terms of Alzheimer's disease?",
                "example_result": "Returns tauopathy, neurodegenerative disease, CNS disease, etc. with distance",
            },
        ],
        "data_sources": [
            {
                "name": "Alliance Curation Database (Disease Ontology)",
                "description": "Contains 14,500+ disease terms from the Disease Ontology (DOID) with full hierarchy and synonym support",
                "species_supported": None,  # Species-independent ontology
                "data_types": ["disease terms", "DOIDs", "definitions", "synonyms", "hierarchical relationships"],
            },
        ],
        "limitations": [
            "Only queries Disease Ontology (DOID) terms - does not include other disease vocabularies",
            "Does not provide gene-disease associations (use Gene Specialist for that)",
            "Disease prevalence and statistics are not available in this database",
            "All responses must come from database queries - cannot answer from general knowledge",
        ],
    },
    "chemical_validation": {
        "summary": "Maps chemical compound names to ChEBI (Chemical Entities of Biological Interest) ontology identifiers via the ChEBI REST API at EBI.",
        "capabilities": [
            {
                "name": "Chemical Name to ChEBI ID Lookup",
                "description": "Search for chemical compounds by name and return their ChEBI identifiers. Uses Elasticsearch-powered text search that supports partial matching and synonyms.",
                "example_query": "Look up the ChEBI ID for glucose",
                "example_result": "CHEBI:17234 (D-glucose) with formula C6H12O6",
            },
            {
                "name": "Compound Detail Retrieval",
                "description": "Get detailed information about a compound including definition, molecular formula, InChI, SMILES structure, and synonyms.",
                "example_query": "Get details for CHEBI:17234",
                "example_result": "D-glucose - An aldohexose used as a source of energy and metabolic intermediate. Formula: C6H12O6",
            },
            {
                "name": "Ontology Classification Navigation",
                "description": "Retrieve parent classifications (is_a relationships) and child terms for a chemical compound in the ChEBI ontology hierarchy.",
                "example_query": "What are the parent classifications of glucose?",
                "example_result": "is_a aldohexose (CHEBI:33917), is_a hexose (CHEBI:18133), is_a monosaccharide (CHEBI:35381)",
            },
            {
                "name": "Batch Chemical Lookup",
                "description": "Look up multiple chemicals in a single request. Returns results for each found compound and lists any terms not found.",
                "example_query": "Look up these chemicals: glucose, ATP, ethanol",
                "example_result": "Found: CHEBI:17234 (D-glucose), CHEBI:15422 (ATP), CHEBI:16236 (ethanol)",
            },
        ],
        "data_sources": [
            {
                "name": "ChEBI (Chemical Entities of Biological Interest)",
                "description": "EBI-hosted curated ontology of molecular entities focused on small chemical compounds involved in biological processes, including drugs, metabolites, cofactors, and toxins.",
                "species_supported": None,  # Not species-specific
                "data_types": ["Chemical identifiers (ChEBI IDs)", "Molecular formulas", "InChI/SMILES structures", "Chemical definitions", "Ontology classifications", "Synonyms"],
            },
        ],
        "limitations": [
            "Cannot provide chemical-gene interactions (requires gene specialist agent)",
            "Cannot provide drug targets or mechanisms of action",
            "Cannot provide pathway information (pathways not in ChEBI)",
            "Multiple results for common names (e.g., 'glucose' matches many stereoisomers) - selects most biologically relevant form",
            "Requires API call before every response - never answers from training data alone",
        ],
    },
    "gene_expression_extraction": {
        "summary": "Extracts structured gene expression data from research PDFs, capturing anatomical locations, developmental stages, sub-cellular localization, reagent details, and evidence supporting expression patterns.",
        "capabilities": [
            {
                "name": "Expression Pattern Extraction",
                "description": "Identifies and extracts gene expression patterns from PDFs including anatomical locations (tissues, cell types, organs), developmental stages, temporal qualifiers, and sex-specific expression.",
                "example_query": "Extract all gene expression patterns from this paper",
                "example_result": "Returns structured annotations with gene symbol, anatomy label, life stage label, GO cellular component, and evidence text with page numbers",
            },
            {
                "name": "Reagent Information Capture",
                "description": "Extracts detailed reagent information used for expression detection including reporter fusions, CRISPR knock-ins, antibodies, in situ probes, and transgenic constructs.",
                "example_query": "What reagents were used to detect gene expression?",
                "example_result": "Returns reagent details like type ('CRISPR_knockin'), name ('dmd-3::YFP'), genotype, and strain",
            },
            {
                "name": "Negative Evidence Capture",
                "description": "Identifies and extracts negative expression evidence where genes are NOT expressed in specific locations or stages.",
                "example_query": "Find tissues where the gene is not expressed",
                "example_result": "Returns annotations with is_negative=true for statements like 'not detected in neurons'",
            },
            {
                "name": "Gene ID Validation",
                "description": "Validates gene symbols found in papers against the Alliance Curation Database using exact symbol matching.",
                "example_query": "Validate gene daf-16 for C. elegans",
                "example_result": "Returns validated gene ID 'WB:WBGene00000912' or 'not validated' if gene symbol cannot be matched",
            },
        ],
        "data_sources": [
            {
                "name": "PDF Document Search (Weaviate)",
                "description": "Hybrid semantic and keyword search over uploaded PDF documents. Supports searching by query terms, filtering by section, and reading full sections or subsections.",
                "species_supported": None,  # Document-based, not species-specific
                "data_types": ["PDF text chunks", "Section content", "Subsection content"],
            },
            {
                "name": "Alliance Curation Database",
                "description": "Validates gene symbols and retrieves gene CURIEs from the Alliance of Genome Resources curation database.",
                "species_supported": ["C. elegans (WB)", "D. melanogaster (FB)", "M. musculus (MGI)", "D. rerio (ZFIN)", "R. norvegicus (RGD)", "S. cerevisiae (SGD)", "H. sapiens (HGNC)"],
                "data_types": ["Gene symbols", "Gene CURIEs", "Gene synonyms"],
            },
        ],
        "limitations": [
            "Returns plain text output only - JSON conversion is handled by a separate Formatter Agent",
            "Does NOT extract expression data from mutant phenotypes or experimental perturbations (heat shock, RNAi knockdown, drug treatment) - only baseline/wild-type expression",
            "Does NOT annotate transgenic markers used solely for strain identification (e.g., rol-6, myo-2::GFP co-injection markers)",
            "Ontology term ID mapping (WBbt, FBbt, GO:CC IDs) is NOT performed - only human-readable labels are extracted; a separate ontology_mapping agent handles ID resolution",
            "Requires at least one document search or read tool call before returning results",
        ],
    },
    "ontology_mapping_lookup": {
        "summary": "Maps free-text labels (anatomy, life stage, cellular component) to standardized ontology term CURIEs by querying the AGR Curation Database.",
        "capabilities": [
            {
                "name": "Anatomy term mapping",
                "description": "Map anatomical location labels to species-specific ontology term CURIEs (WBbt, FBbt, MA, EMAPA, ZFA, UBERON)",
                "example_query": "Map 'linker cell' for C. elegans",
                "example_result": "Returns WBbt:0005062 with confidence='high'",
            },
            {
                "name": "Life stage term mapping",
                "description": "Map developmental stage labels to species-specific ontology term CURIEs (WBls, FBdv, MMUSDV, ZFS, HsapDv)",
                "example_query": "Map 'L3 larval stage' for C. elegans",
                "example_result": "Returns WBls:0000035 with confidence='high'",
            },
            {
                "name": "GO Cellular Component mapping",
                "description": "Map cellular component labels to GO term CURIEs (species-independent)",
                "example_query": "Map 'nucleus' to GO term",
                "example_result": "Returns GO:0005634 with confidence='high'",
            },
            {
                "name": "Batch label mapping",
                "description": "Process multiple labels in a single request from prior agent output",
                "example_query": "Map these labels: pharynx, L3 larval stage, nucleus",
                "example_result": "Returns mappings for all labels with confidence scores and unmapped_labels list",
            },
        ],
        "data_sources": [
            {
                "name": "AGR Curation Database",
                "description": "PostgreSQL database containing ontology terms, synonyms, and relationships from all Alliance MODs",
                "species_supported": ["C. elegans (WB)", "D. melanogaster (FB)", "M. musculus (MGI)", "D. rerio (ZFIN)", "R. norvegicus (RGD)", "S. cerevisiae (SGD)", "X. laevis (XB)", "H. sapiens (HGNC)"],
                "data_types": ["anatomy ontology terms", "life stage ontology terms", "GO Cellular Component terms", "term synonyms", "ontology relationships"],
            },
        ],
        "limitations": [
            "Cannot extract labels from PDFs - receives labels from prior agent output (e.g., Gene Expression agent)",
            "Cannot validate gene symbols - use Gene Validation Agent instead",
            "Cannot look up disease terms - use Disease Agent instead",
            "Cannot create new ontology terms - only maps to existing terms",
            "Requires organism context to select correct species-specific ontology",
            "May return parent term instead of exact term when specificity is unavailable",
        ],
    },
    # Additional documentation can be added here as needed
    # For agents without custom documentation, defaults will be used
}


def _agent_definition_to_registry_entry(
    agent_def: AgentDefinition,
    factory: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Convert an AgentDefinition to an AGENT_REGISTRY entry.

    Args:
        agent_def: AgentDefinition from YAML
        factory: Factory function (from convention-based discovery)

    Returns:
        Dictionary in AGENT_REGISTRY format
    """
    # Get documentation if available
    doc = AGENT_DOCUMENTATION.get(agent_def.agent_id, {})

    # Build batching config if agent is batchable
    batching = None
    if agent_def.supervisor_routing.batchable:
        entity = agent_def.supervisor_routing.batching_entity
        tool_name = agent_def.tool_name
        # Generate example: ask_gene_specialist("Look up these genes: ...")
        batching = {
            "entity": entity,
            "example": f'{tool_name}("Look up these {entity}: ...")',
        }

    return {
        "name": agent_def.name,
        "description": agent_def.description,
        "category": agent_def.category,
        "subcategory": agent_def.subcategory,
        "has_mod_rules": agent_def.group_rules_enabled,
        "tools": agent_def.tools,
        "factory": factory,
        "requires_document": agent_def.requires_document,
        "required_params": agent_def.required_params,
        "batch_capabilities": agent_def.batch_capabilities,
        "config_defaults": _build_config_defaults(agent_def.model_config),
        "supervisor": {
            "enabled": agent_def.supervisor_routing.enabled,
            "tool_name": agent_def.tool_name,
        },
        "batching": batching,
        "frontend": {
            "icon": agent_def.frontend.icon,
            "show_in_palette": agent_def.frontend.show_in_palette,
        },
        "documentation": doc if doc else None,
    }


def build_agent_registry() -> Dict[str, Dict[str, Any]]:
    """
    Build AGENT_REGISTRY from YAML configurations.

    Loads all agent definitions from config/agents/*/agent.yaml and
    converts them to AGENT_REGISTRY format. Uses convention-based
    factory discovery for agent creation.

    Returns:
        Dictionary mapping agent_id to registry entry

    Note:
        This function builds the registry fresh each time. For caching,
        use the AGENT_REGISTRY constant in catalog_service.py which calls
        this once at module load time.
    """
    registry: Dict[str, Dict[str, Any]] = {}

    # Add task_input as a special non-agent entry
    registry["task_input"] = {
        "name": "Initial Instructions",
        "description": "Define the curator's task that starts the flow",
        "category": "Input",
        "subcategory": "Input",
        "has_mod_rules": False,
        "tools": [],
        "factory": None,  # Not an executable agent
        "requires_document": False,
        "required_params": [],
        "batch_capabilities": [],
        "frontend": {
            "icon": "📋",
            "show_in_palette": False,
        },
        "documentation": AGENT_DOCUMENTATION.get("task_input"),
    }

    # Load all agent definitions from YAML
    try:
        agent_defs = load_agent_definitions()
    except FileNotFoundError:
        logger.warning(
            "Agent definitions not found. AGENT_REGISTRY will be minimal."
        )
        return registry

    # Convert each agent definition to registry format
    for agent_id, agent_def in agent_defs.items():
        # Get factory via convention-based discovery
        factory = get_agent_factory(agent_def.folder_name)

        if factory is None:
            logger.warning(
                f"No factory found for agent {agent_id} "
                f"(folder: {agent_def.folder_name}). "
                "Agent will be display-only."
            )

        entry = _agent_definition_to_registry_entry(agent_def, factory)
        registry[agent_id] = entry

        # Also add folder_name as an alias for backwards compatibility
        # This allows both AGENT_REGISTRY.get("pdf") and get("pdf_extraction")
        if agent_def.folder_name != agent_id and agent_def.folder_name not in registry:
            registry[agent_def.folder_name] = entry

        logger.debug(
            f"Added to registry: {agent_id} "
            f"(folder={agent_def.folder_name}, factory={'present' if factory else 'missing'})"
        )

    logger.info(
        f"Built AGENT_REGISTRY with {len(registry)} entries "
        f"({sum(1 for e in registry.values() if e.get('factory'))} with factories)"
    )

    return registry


def get_registry_entry(agent_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a single registry entry, building from YAML if needed.

    This is a convenience function that doesn't require loading
    the full registry.

    Args:
        agent_id: The agent identifier

    Returns:
        Registry entry dict or None if not found
    """
    # Special case for task_input
    if agent_id == "task_input":
        return {
            "name": "Initial Instructions",
            "description": "Define the curator's task that starts the flow",
            "category": "Input",
            "subcategory": "Input",
            "has_mod_rules": False,
            "tools": [],
            "factory": None,
            "requires_document": False,
            "required_params": [],
            "batch_capabilities": [],
            "frontend": {
                "icon": "📋",
                "show_in_palette": False,
            },
            "documentation": AGENT_DOCUMENTATION.get("task_input"),
        }

    # Get agent definition from YAML
    agent_def = get_agent_definition(agent_id)
    if agent_def is None:
        return None

    # Get factory
    factory = get_agent_factory(agent_def.folder_name)

    return _agent_definition_to_registry_entry(agent_def, factory)
