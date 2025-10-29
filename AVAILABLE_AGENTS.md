# Available AI Curation Agents

This document lists all specialized agents available in the AI Curation System and describes what data sources they connect to.

## Agent Overview

The AI Curation System uses multiple specialized agents, each designed to answer specific types of biological questions. A supervisor agent coordinates these specialists to provide comprehensive answers.

## Specialist Agents

### Disease Ontology Agent

**What it does:** Queries disease classifications, hierarchies, and term relationships

**Data source:** PostgreSQL database (Disease Ontology - DOID.OBO format)

**Connection type:** SQL queries

**Example questions:**
- "What is DOID:4325?"
- "Show me child terms of cancer"
- "What diseases are related to diabetes?"

---

### Chemical Ontology Agent

**What it does:** Retrieves chemical compound information and classifications

**Data source:** ChEBI (Chemical Entities of Biological Interest) REST API

**API base URL:** `https://www.ebi.ac.uk/chebi`

**Key endpoints:**
- `/backend/api/public/es_search/?term={query}` - Search for compounds by name, formula, or ID
- `/backend/api/public/compound/{chebi_id}/` - Get detailed compound information
- `/backend/api/public/ontology/parents/{chebi_id}/` - Get parent classifications
- `/backend/api/public/ontology/children/{chebi_id}/` - Get child compounds

**Example questions:**
- "What is cytidine?"
- "Show me chemical properties of aspirin"
- "Find compounds related to glucose"

---

### Gene Lookup Agent

**What it does:** Retrieves gene information and cross-references across model organisms

**Data source:** Alliance of Genome Resources REST API

**API base URL:** `https://www.alliancegenome.org/api`

**Key endpoints:**
- `/search?q={symbol}&category=gene&limit={n}` - Search for genes by symbol or name
- `/gene/{id}` - Get complete gene details with database cross-references

**Example questions:**
- "What is the gene WBGene00001234?"
- "Find information about TP53"
- "Show me details for FBgn0000008"

---

### Gene Ontology Agent

**What it does:** Searches GO terms, hierarchies, and relationships (molecular functions, biological processes, cellular components)

**Data source:** QuickGO REST API at EBI

**API base URL:** `https://www.ebi.ac.uk/QuickGO/services`

**Key endpoints:**
- `/ontology/go/search?query={term}` - Search for GO terms by name
- `/ontology/go/terms/{id}` - Get term information and definition
- `/ontology/go/terms/{id}/children` - Get more specific child terms
- `/ontology/go/terms/{id}/ancestors` - Get broader parent terms

**Example questions:**
- "What is GO:0008150?"
- "Show me child terms of DNA repair"
- "What does biological process mean?"

---

### GO Annotations Agent

**What it does:** Retrieves actual GO annotations for specific genes with evidence codes

**Data source:** Gene Ontology Consortium API

**API base URL:** `https://api.geneontology.org/api`

**Key endpoints:**
- `/bioentity/gene/{id}/function` - Get all GO annotations for a gene with evidence codes (IDA, IMP, IEA, etc.)

**Example questions:**
- "What GO terms are annotated to daf-2?"
- "Show me annotations with IDA evidence for gene X"
- "What biological processes involve this gene?"

---

### Alliance Orthologs Agent

**What it does:** Finds orthology relationships between genes across species with confidence scores

**Data source:** Alliance of Genome Resources Orthology API

**API base URL:** `https://www.alliancegenome.org/api`

**Key endpoints:**
- `/gene/{id}/orthologs` - Get orthology relationships with confidence scores and prediction methods

**Example questions:**
- "What are the orthologs of human TP53?"
- "Show me mouse genes orthologous to daf-2"
- "Find homologs of FBgn0000008 across species"

---

### PDF Specialist Agent

**What it does:** Extracts biological facts from uploaded research papers

**Data source:** Weaviate vector database

**Connection type:** Semantic search on document embeddings

**Example questions:**
- "What does the uploaded paper say about gene regulation?"
- "Find information about apoptosis in the research papers"
- "Summarize the methods section from document X"

---

### Supervisor Agent

**What it does:** Analyzes questions and routes them to appropriate specialist agents

**Data source:** None (orchestrator only)

**Connection type:** Coordinates other agents and synthesizes responses

**Note:** This agent handles all incoming questions by determining which specialists can answer them and combining their responses into comprehensive answers.

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
