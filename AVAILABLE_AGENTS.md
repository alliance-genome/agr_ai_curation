# Available AI Curation Agents

This document lists all specialized agents available in the AI Curation System and describes what data sources they connect to.

## Agent Overview

The AI Curation System uses multiple specialized agents, each designed to answer specific types of biological questions. A supervisor agent coordinates these specialists to provide comprehensive answers.

## Specialist Agents

| Agent Name | Connects To | Data Source Details | Example Questions |
|-----------|-------------|---------------------|-------------------|
| **Gene Expression Curation Agent** | Vector Database + AGR Curation DB | Extracts gene expression patterns from research papers using Weaviate semantic search. Captures spatial (anatomical), temporal (developmental stages), and subcellular locations with reagent details. Multi-organism support. Coordinates with Ontology Mapping Agent for term ID resolution. | "Extract gene expression patterns from this paper" "What anatomical structures show dmd-3 expression?" "Find negative evidence for gene X expression" |
| **Ontology Mapping Agent** | AGR Curation Database | Maps human-readable labels to official ontology term IDs. Supports multiple ontology types across organisms (see [Supported Ontology Types](#supported-ontology-types) below). Uses `agr_curation_query` tool with organism-specific data providers. | "Map 'linker cell' to WBbt term" "Find CURIE for 'L3 larval stage'" "What is the term ID for 'nucleus'?" |
| **Disease Ontology Agent** | SQL Database | Disease Ontology (DOID.OBO) in database form. Executes SQL queries against PostgreSQL database containing disease classifications, hierarchies, and term relationships. | "What is DOID:4325?" "Show me child terms of cancer" "What diseases are related to diabetes?" |
| **Gene Curation Agent** | AGR Curation Database | Alliance of Genome Resources internal curation database. Queries curated gene data including symbols, names, IDs, genomic locations (chromosome, start, end, strand), and cross-references (NCBI, UniProt, Ensembl). Uses `agr_curation_query` tool from [agr_curation_api_client](https://github.com/alliance-genome/agr_curation_api_client). | "What genes are on chromosome 2?" "Show me gene symbols for WBGene00001345" "Find genes with NCBI cross-reference X" "What is the genomic location of dpy-5?" |
| **Allele Curation Agent** | AGR Curation Database | Alliance of Genome Resources internal curation database. Queries curated allele data including symbols, variant types, references, and associated genes. Uses `agr_curation_query` tool from [agr_curation_api_client](https://github.com/alliance-genome/agr_curation_api_client). | "What alleles are associated with gene X?" "Show me variant information for allele Y" "Find alleles with specific variant types" "What references support allele Z?" |
| **Chemical Ontology Agent** | REST API | ChEBI REST API at EBI (https://www.ebi.ac.uk/chebi)<br>**Endpoints:**<br>• `/backend/api/public/es_search/?term=` - Search compounds<br>• `/backend/api/public/compound/{id}/` - Get compound details<br>• `/backend/api/public/ontology/parents/{id}/` - Get classifications<br>• `/backend/api/public/ontology/children/{id}/` - Get child compounds | "What is cytidine?" "Show me chemical properties of aspirin" "Find compounds related to glucose" |
| **Gene Ontology Agent** | REST API | QuickGO REST API at EBI (https://www.ebi.ac.uk/QuickGO/services)<br>**Endpoints:**<br>• `/ontology/go/search?query=` - Search GO terms<br>• `/ontology/go/terms/{id}` - Get term information<br>• `/ontology/go/terms/{id}/children` - Get child terms<br>• `/ontology/go/terms/{id}/ancestors` - Get parent terms | "What is GO:0008150?" "Show me child terms of DNA repair" "What does biological process mean?" |
| **GO Annotations Agent** | REST API | GO Consortium API (https://api.geneontology.org/api)<br>**Endpoints:**<br>• `/bioentity/gene/{id}/function` - Get gene GO annotations with evidence codes (IDA, IMP, IEA, etc.) | "What GO terms are annotated to gene X?" "Show me annotations with IDA evidence" "What biological processes involve this gene?" |
| **Alliance Orthologs Agent** | REST API | Alliance Genome Orthology API (https://www.alliancegenome.org/api)<br>**Endpoints:**<br>• `/gene/{id}/orthologs` - Get orthology relationships with confidence scores and prediction methods | "What are the orthologs of human TP53?" "Show me mouse genes orthologous to fly gene Y" "Find homologs across species" |
| **PDF Specialist Agent** | Vector Database | Weaviate vector database containing embeddings of uploaded research papers. Enables semantic search across scientific literature. | "What does paper X say about gene regulation?" "Find information about disease Y in the uploaded papers" "Summarize methods from document Z" |
| **Supervisor Agent** | Orchestrator | No external data source - coordinates other agents. Routes questions to appropriate specialists and synthesizes responses. | Handles all questions by delegating to specialist agents. |

## Output Formatter Agents

These agents are used in **[Curation Flows](CURATION_FLOWS.md)** to generate downloadable files from extracted data.

| Agent Name | Output Format | Description | Use Cases |
|-----------|---------------|-------------|-----------|
| **Chat Output Agent** | Chat Message | Sends formatted results to the chat interface for review and discussion. | Quick review, iterative refinement, sharing results in conversation |
| **CSV Formatter Agent** | CSV File | Generates comma-separated value files for spreadsheet applications and database import. | Excel/Google Sheets, bulk database import, data sharing |
| **TSV Formatter Agent** | TSV File | Generates tab-separated value files preferred by many bioinformatics tools and databases. | Database import scripts, bioinformatics pipelines, AGR data submission |
| **JSON Formatter Agent** | JSON File | Generates structured JSON files preserving complex nested data structures. | API integration, custom scripts, preserving hierarchical data |

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

## Technical Details

For more information about the implementation, agent configurations, and tool definitions, see the [private prototype repository](https://github.com/alliance-genome/ai_curation_prototype).

### Agent Configuration
- Agent definitions: [`backend/src/crew_config/agents.yaml`](https://github.com/alliance-genome/ai_curation_prototype/blob/main/backend/src/crew_config/agents.yaml)
- Tool implementations: [`backend/src/lib/chat/tools/`](https://github.com/alliance-genome/ai_curation_prototype/tree/main/backend/src/lib/chat/tools)
- Database schemas: [`backend/schemas/`](https://github.com/alliance-genome/ai_curation_prototype/tree/main/backend/schemas)

## Adding New Agents

The system is designed to be extensible. New specialist agents can be added to support additional data sources or types of biological questions. Contact the development team if you have suggestions for new agents.
