# Available AI Curation Agents

This document lists all specialized agents available in the AI Curation System and describes what data sources they connect to.

## Agent Overview

The AI Curation System uses multiple specialized agents, each designed to answer specific types of biological questions. A supervisor agent coordinates these specialists to provide comprehensive answers.

## Specialist Agents

| Agent Name | Connects To | Data Source Details | Example Questions |
|-----------|-------------|---------------------|-------------------|
| **Disease Ontology Agent** | SQL Database | Disease Ontology (DOID.OBO) in database form. Executes SQL queries against PostgreSQL database containing disease classifications, hierarchies, and term relationships. | "What is DOID:4325?" "Show me child terms of cancer" "What diseases are related to diabetes?" |
| **Gene Curation Agent** | AGR Curation Database | Alliance of Genome Resources internal curation database. Queries curated gene data including symbols, names, IDs, genomic locations (chromosome, start, end, strand), and cross-references (NCBI, UniProt, Ensembl). Uses `agr_curation_query` tool from [agr_curation_api_client](https://github.com/alliance-genome/agr_curation_api_client). | "What genes are on chromosome 2?" "Show me gene symbols for WBGene00001345" "Find genes with NCBI cross-reference X" "What is the genomic location of dpy-5?" |
| **Allele Curation Agent** | AGR Curation Database | Alliance of Genome Resources internal curation database. Queries curated allele data including symbols, variant types, references, and associated genes. Uses `agr_curation_query` tool from [agr_curation_api_client](https://github.com/alliance-genome/agr_curation_api_client). | "What alleles are associated with gene X?" "Show me variant information for allele Y" "Find alleles with specific variant types" "What references support allele Z?" |
| **Chemical Ontology Agent** | REST API | ChEBI REST API at EBI (https://www.ebi.ac.uk/chebi)<br>**Endpoints:**<br>• `/backend/api/public/es_search/?term=` - Search compounds<br>• `/backend/api/public/compound/{id}/` - Get compound details<br>• `/backend/api/public/ontology/parents/{id}/` - Get classifications<br>• `/backend/api/public/ontology/children/{id}/` - Get child compounds | "What is cytidine?" "Show me chemical properties of aspirin" "Find compounds related to glucose" |
| **Gene Ontology Agent** | REST API | QuickGO REST API at EBI (https://www.ebi.ac.uk/QuickGO/services)<br>**Endpoints:**<br>• `/ontology/go/search?query=` - Search GO terms<br>• `/ontology/go/terms/{id}` - Get term information<br>• `/ontology/go/terms/{id}/children` - Get child terms<br>• `/ontology/go/terms/{id}/ancestors` - Get parent terms | "What is GO:0008150?" "Show me child terms of DNA repair" "What does biological process mean?" |
| **GO Annotations Agent** | REST API | GO Consortium API (https://api.geneontology.org/api)<br>**Endpoints:**<br>• `/bioentity/gene/{id}/function` - Get gene GO annotations with evidence codes (IDA, IMP, IEA, etc.) | "What GO terms are annotated to gene X?" "Show me annotations with IDA evidence" "What biological processes involve this gene?" |
| **Alliance Orthologs Agent** | REST API | Alliance Genome Orthology API (https://www.alliancegenome.org/api)<br>**Endpoints:**<br>• `/gene/{id}/orthologs` - Get orthology relationships with confidence scores and prediction methods | "What are the orthologs of human TP53?" "Show me mouse genes orthologous to fly gene Y" "Find homologs across species" |
| **PDF Specialist Agent** | Vector Database | Weaviate vector database containing embeddings of uploaded research papers. Enables semantic search across scientific literature. | "What does paper X say about gene regulation?" "Find information about disease Y in the uploaded papers" "Summarize methods from document Z" |
| **Supervisor Agent** | Orchestrator | No external data source - coordinates other agents. Routes questions to appropriate specialists and synthesizes responses. | Handles all questions by delegating to specialist agents. |

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
