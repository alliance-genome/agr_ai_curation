# Available AI Curation Agents

This document lists all specialized agents available in the AI Curation System and describes what data sources they connect to.

## Agent Overview

The AI Curation System uses multiple specialized agents, each designed to answer specific types of biological questions. A supervisor agent coordinates these specialists to provide comprehensive answers.

All agents are defined in configuration files (YAML) and stored in the database. This means agents can be updated and new agents can be added without changing application code. You can browse agent configurations in the **Agent Browser** within Agent Studio, and create your own custom agents in the **Agent Workshop**.

## Specialist Agents

| Agent Name | Data Source | What It Does | Example Questions |
|-----------|-------------|--------------|-------------------|
| **Gene Expression Extractor** | Uploaded Papers + AGR Database | Extracts gene expression patterns from research papers. Captures anatomical locations, developmental stages, and subcellular locations. Coordinates with Ontology Mapping Agent for term ID resolution. | "Extract gene expression patterns from this paper" "What anatomical structures show dmd-3 expression?" "Find negative evidence for gene X expression" |
| **Ontology Mapping Agent** | AGR Curation Database | Maps human-readable labels to official ontology term IDs across 45+ ontology types (see [Supported Ontology Types](#supported-ontology-types) below). | "Map 'linker cell' to WBbt term" "Find CURIE for 'L3 larval stage'" "What is the term ID for 'nucleus'?" |
| **Disease Ontology Agent** | Disease Ontology (DOID) | Searches disease classifications, hierarchies, and term relationships. | "What is DOID:4325?" "Show me child terms of cancer" "What diseases are related to diabetes?" |
| **Gene Validation Agent** | AGR Curation Database | Validates gene identifiers against the Alliance Curation Database. Supports lookup by symbol, name, ID, or cross-reference. | "Look up the gene daf-16" "Validate these genes: daf-16, lin-3, unc-54" "Show me gene symbols for WBGene00001345" |
| **Allele Validation Agent** | AGR Curation Database | Validates allele/variant identifiers against the Alliance Curation Database. Supports lookup by symbol, ID, or gene association. | "Find all Ulk1 alleles in mouse" "Look up these alleles: e1370, n765, tm1234" "Tell me about MGI:3689906" |
| **Chemical Ontology Agent** | ChEBI (EBI) | Searches chemical compounds, their properties, classifications, and relationships. | "What is cytidine?" "Show me chemical properties of aspirin" "Find compounds related to glucose" |
| **GO Term Lookup Agent** | QuickGO (EBI) | Searches GO terms, definitions, hierarchies (parent/child terms), and relationships. | "What is GO:0008150?" "Show me child terms of DNA repair" "What does biological process mean?" |
| **Gene GO Annotations Agent** | GO Consortium | Retrieves gene GO annotations with evidence codes (IDA, IMP, IEA, etc.). | "What GO terms are annotated to gene X?" "Show me annotations with IDA evidence" "What biological processes involve this gene?" |
| **Ortholog Lookup Agent** | Alliance Genome | Finds cross-species orthology relationships with confidence scores. | "What are the orthologs of human TP53?" "Show me mouse genes orthologous to fly gene Y" "Find homologs across species" |
| **PDF Extraction Agent** | Uploaded Papers | Extracts text, tables, and data from uploaded PDF documents using semantic search and section-based retrieval. | "What does paper X say about gene regulation?" "Extract the gene expression table from results section" "Read the Methods section" |
| **Supervisor Agent** | Routes to Specialists | Coordinates other agents - analyzes your question and sends it to the right specialist(s). | Handles all questions by delegating to specialist agents. |

## Output Formatter Agents

These agents are used in **[Curation Flows](CURATION_FLOWS.md)** to generate downloadable files from extracted data.

| Agent Name | Output Format | Description | Use Cases |
|-----------|---------------|-------------|-----------|
| **Chat Output Agent** | Chat Message | Sends formatted results to the chat interface for review and discussion. | Quick review, iterative refinement, sharing results in conversation |
| **CSV Formatter Agent** | CSV File | Generates comma-separated value files for spreadsheet applications. | Excel/Google Sheets, database import, data sharing |
| **TSV Formatter Agent** | TSV File | Generates tab-separated value files preferred by many databases. | Database import, AGR data submission, bioinformatics tools |
| **JSON Formatter Agent** | JSON File | Generates structured JSON files preserving complex nested data. | Data with hierarchical structure, sharing with computational biologists |

### File Output Features

When flows generate files, they appear in the chat as downloadable cards showing:
- File name and format
- File size
- Generation timestamp
- Model used for generation
- Download count

Files remain available throughout your session. Download important files before ending your session.

## Supported Ontology Types

The **Ontology Mapping Agent** supports mapping labels to official term IDs across **45 distinct ontology types** in the AGR Curation Database. These ontologies cover anatomy, life stages, phenotypes, diseases, chemicals, experimental conditions, cell types, sequences, evidence codes, and more.

### Complete Ontology Type List

#### ANATOMY ONTOLOGIES (9 types)
- **WBBTTerm** (WBbt:) - C. elegans anatomy
- **DAOTerm** (FBbt:) - D. melanogaster anatomy
- **EMAPATerm** (EMAPA:) - Mouse embryo anatomy
- **MATerm** (MA:) - Mouse adult anatomy
- **UBERONTerm** (UBERON:) - Multi-species anatomy
- **ZFATerm** (ZFA:) - Zebrafish anatomy
- **XBATerm** (XAO:) - Xenopus anatomy
- **XBSTerm** (XAO:) - Xenopus anatomy stage
- **BTOTerm** (BTO:) - BRENDA Tissue Ontology

#### LIFE STAGE/DEVELOPMENT ONTOLOGIES (5 types)
- **WBLSTerm** (WBls:) - C. elegans life stage
- **FBDVTerm** (FBdv:) - D. melanogaster development
- **MMUSDVTerm** (MmusDv:) - Mouse development stage
- **ZFSTerm** (ZFS:) - Zebrafish life stage
- **XBEDTerm** (XBED:) - Xenopus development

#### PHENOTYPE ONTOLOGIES (7 types)
- **WBPhenotypeTerm** (WBPhenotype:) - C. elegans phenotype
- **FBCVTerm** (FBcv:) - D. melanogaster controlled vocabulary
- **MPTerm** (MP:) - Mammalian phenotype
- **HPTerm** (HP:) - Human phenotype
- **XPOTerm** (XPO:) - Xenopus phenotype
- **APOTerm** (APO:) - Ascomycete phenotype
- **VTTerm** (VT:) - Vertebrate trait

#### GENE ONTOLOGY (1 type)
- **GOTerm** (GO:) - Gene Ontology (cellular_component, biological_process, molecular_function)

#### DISEASE ONTOLOGIES (2 types)
- **DOTerm** (DOID:) - Disease Ontology
- **MPATHTerm** (MPATH:) - Mouse pathology

#### CHEMICAL/MOLECULAR ONTOLOGIES (5 types)
- **CHEBITerm** (CHEBI:) - Chemical entities
- **Molecule** (WB:) - WormBase molecules
- **XSMOTerm** (XSMO:) - Xenopus small molecule
- **MODTerm** (MOD:) - Protein modification
- **ATPTerm** (ATP:) - Anatomical therapeutic chemical

#### EXPERIMENTAL/CONDITION ONTOLOGIES (4 types)
- **XCOTerm** (XCO:) - Experimental condition
- **ZECOTerm** (ZECO:) - Zebrafish experimental condition
- **CMOTerm** (CMO:) - Clinical measurement
- **MMOTerm** (MMO:) - Measurement method

#### CELL/TISSUE ONTOLOGIES (2 types)
- **CLTerm** (CL:) - Cell type
- **BSPOTerm** (BSPO:) - Spatial ontology

#### SEQUENCE/GENETICS ONTOLOGIES (5 types)
- **SOTerm** (SO:) - Sequence ontology
- **GENOTerm** (GENO:) - Genotype ontology
- **MITerm** (MI:) - Molecular interaction
- **ROTerm** (RO:) - Relation ontology
- **RSTerm** (RS:) - Rat strain

#### EVIDENCE/QUALITY ONTOLOGIES (3 types)
- **ECOTerm** (ECO:) - Evidence code
- **PATOTerm** (PATO:) - Quality/attribute
- **OBITerm** (OBI:) - Biomedical investigation

#### PATHWAY/PROCESS ONTOLOGIES (1 type)
- **PWTerm** (PW:) - Pathway ontology

#### TAXONOMY ONTOLOGIES (1 type)
- **NCBITaxonTerm** (NCBITaxon:) - NCBI Taxonomy

### Mapping Workflow

When you request ontology mappings, the agent:
1. Identifies the organism from context
2. Selects appropriate ontology types for that organism
3. Queries the AGR Curation Database for term matches across all 45 ontology types
4. Returns CURIEs (e.g., `WBbt:0005062`, `GO:0005634`) with confidence scores

## How Agents Work Together

1. **You ask a question** - The system receives your natural language query
2. **Supervisor routes the question** - Determines which specialist agent(s) can answer
3. **Specialists retrieve data** - Each specialist queries its specific data source
4. **Response is synthesized** - Results are combined into a comprehensive answer

## Custom Agents (Agent Workshop)

You can create your own customized versions of any system agent using the **Agent Workshop** in Agent Studio. Custom agents let you:

- Start from a template, from scratch, or by cloning an existing custom agent
- Edit instructions, choose a model, and attach tools from the tool library
- Add per-group prompt overrides with version history and revert support
- Share custom agents with your project or keep them private
- Use custom agents in Curation Flows alongside system agents

See **[Agent Studio](AGENT_STUDIO.md)** for details on Agent Workshop.

## Suggestions for New Agents

Have an idea for a new agent or data source? Contact the development team - we're always looking to expand the system's capabilities based on curator needs.
