# Available AI Curation Agents

This document lists all specialized agents available in the AI Curation System and describes what data sources they connect to.

## Agent Overview

The AI Curation System uses multiple specialized agents, each designed to answer specific types of biological questions. A supervisor agent coordinates these specialists to provide comprehensive answers.

## Specialist Agents

| Agent Name | Connects To | Data Source Details | Example Questions |
|-----------|-------------|---------------------|-------------------|
| **Disease Ontology Agent** | SQL Database | Disease Ontology (DOID.OBO) in database form. Contains disease classifications, hierarchies, and term relationships. | "What is DOID:4325?" "Show me child terms of cancer" "What diseases are related to diabetes?" |
| **Chemical Ontology Agent** | REST API | ChEBI (Chemical Entities of Biological Interest) REST API at EBI. Provides chemical compound information and classifications. | "What is cytidine?" "Show me chemical properties of aspirin" "Find compounds related to glucose" |
| **Gene Lookup Agent** | REST API | Alliance of Genome Resources REST API. Provides gene information across multiple model organisms. | "What is the gene WBGene00001234?" "Find information about TP53" "Show me details for FBgn0000008" |
| **Gene Ontology Agent** | REST API | Gene Ontology REST API (QuickGO) at EBI. Provides GO terms, hierarchies, and biological process information. | "What is GO:0008150?" "Show me child terms of DNA repair" "What does biological process mean?" |
| **GO Annotations Agent** | REST API | Gene Ontology Consortium API. Retrieves gene annotations with evidence codes and supporting references. | "What GO terms are annotated to gene X?" "Show me annotations with IDA evidence" "What biological processes involve this gene?" |
| **Alliance Orthologs Agent** | REST API | Alliance of Genome Resources Orthology API. Provides cross-species gene relationships and homology information. | "What are the orthologs of human TP53?" "Show me mouse genes orthologous to fly gene Y" "Find homologs across species" |
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
- Agent definitions: `backend/src/crew_config/agents.yaml`
- Tool implementations: `backend/src/lib/chat/tools/`
- Database schemas: `backend/schemas/`

## Adding New Agents

The system is designed to be extensible. New specialist agents can be added to support additional data sources or types of biological questions. Contact the development team if you have suggestions for new agents.
