# Available AI Curation Agents

This document lists all specialized agents available in the AI Curation System and describes what data sources they connect to.

## Agent Overview

The AI Curation System uses multiple specialized agents, each designed to answer specific types of biological questions. A supervisor agent coordinates these specialists to provide comprehensive answers.

## Specialist Agents

| Agent Name | Purpose | Data Source | Key Endpoints/Access | Example Question |
|-----------|---------|-------------|---------------------|------------------|
| **Disease Ontology** | Query disease classifications and hierarchies | PostgreSQL (DOID) | SQL queries against disease ontology database | "What is DOID:4325?" |
| **Chemical Ontology** | Retrieve chemical compound information | ChEBI REST API<br>`ebi.ac.uk/chebi` | `/es_search` - search<br>`/compound/{id}` - details<br>`/ontology/parents` - classifications | "What is cytidine?" |
| **Gene Lookup** | Get gene information across model organisms | Alliance REST API<br>`alliancegenome.org/api` | `/search` - find genes<br>`/gene/{id}` - gene details | "What is WBGene00001234?" |
| **Gene Ontology** | Search GO terms and hierarchies | QuickGO REST API<br>`ebi.ac.uk/QuickGO` | `/ontology/go/search` - find terms<br>`/terms/{id}` - term info<br>`/terms/{id}/children` - hierarchy | "What is GO:0008150?" |
| **GO Annotations** | Get gene GO annotations with evidence codes | GO Consortium API<br>`api.geneontology.org` | `/bioentity/gene/{id}/function` - annotations with evidence (IDA, IMP, IEA) | "What GO terms annotate daf-2?" |
| **Alliance Orthologs** | Find orthology relationships across species | Alliance REST API<br>`alliancegenome.org/api` | `/gene/{id}/orthologs` - orthology with confidence scores | "What are orthologs of TP53?" |
| **PDF Specialist** | Extract facts from research papers | Weaviate vector DB | Semantic search on document embeddings | "What does the paper say about apoptosis?" |
| **Supervisor** | Route questions and coordinate responses | None (orchestrator) | Delegates to specialist agents | Handles all questions via delegation |

## How Agents Work Together

1. **You ask a question** - The system receives your natural language query
2. **Supervisor routes the question** - Determines which specialist agent(s) can answer
3. **Specialists retrieve data** - Each specialist queries its specific data source
4. **Response is synthesized** - Results are combined into a comprehensive answer

## Technical Details

For more information about the implementation, agent configurations, and tool definitions, see the [private prototype repository](https://github.com/alliance-genome/ai_curation_prototype).

### Agent Configuration
- Agent definitions: `backend/src/crew_config/agents.yaml`
- Tool implementations: `backend/src/lib/chat/tools/`
- Database schemas: `backend/schemas/`

## Adding New Agents

The system is designed to be extensible. New specialist agents can be added to support additional data sources or types of biological questions. Contact the development team if you have suggestions for new agents.
