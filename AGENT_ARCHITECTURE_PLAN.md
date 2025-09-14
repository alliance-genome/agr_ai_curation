# AGENT ARCHITECTURE PLAN

## 🚀 IMPLEMENTATION PROGRESS

### ✅ Completed (as of 2025-01-14 18:00 UTC)

#### Phase 1: Core Streaming ✅

- [x] Main agent converted to `output_type=str` for text streaming
- [x] Implemented `stream_text()` with delta support in `_process_stream()`
- [x] Removed BioCurationOutput from main agent streaming path
- [x] All streaming tests passing (7 tests in test_message_history.py)

#### Phase 2: Entity Extraction Tool ✅

- [x] Created `EntityExtractionAgent` class as specialized sub-agent
- [x] Implemented structured output (`EntityExtractionOutput`) for entities
- [x] Added confidence filtering and deduplication methods
- [x] Comprehensive test suite (13 tests, all passing)
- [x] Support for batch extraction and entity grouping

#### Phase 2: Entity Tool Integration ✅

- [x] Add `extract_entities` tool to main BioCurationAgent
- [x] Implement automatic tool invocation logic
- [x] Stream entity events during processing
- [x] Comprehensive test coverage (8 tests for tool integration)
- [x] Fixed all deprecation warnings (Pydantic, SQLAlchemy, Docker Compose)

### 🔄 In Progress

#### Phase 3: Form Filling Tool

- [ ] Create DocumentFields model
- [ ] Implement form-filling sub-agent
- [ ] Add form field streaming events

### 📋 Remaining Work

#### Phase 3: Form Filling Tool

- [ ] Create DocumentFields model
- [ ] Implement form-filling sub-agent
- [ ] Add form field streaming events

#### Phase 4: Annotation Tool

- [ ] Create annotation suggestion sub-agent
- [ ] Stream annotation events
- [ ] Integrate with PDF viewer highlights

#### Phase 5: Frontend Integration

- [ ] Update AgentInterface.tsx for new event types
- [ ] Implement progressive UI updates
- [ ] Add entity and annotation state management

---

## Problem Statement

We need both **streaming text responses** for user experience AND **structured data extraction** for biocuration tasks. PydanticAI doesn't support streaming partial text when using structured output types.

## Solution: Multi-Agent Architecture with Pre-Retrieval

### Core Design Principles

1. **Main orchestrator agent** (`output_type=str`) ONLY streams conversational text
2. **All operations delegated** - Orchestrator doesn't do any actual work
3. **Services handle retrieval** - Hybrid search/reranking are regular services, not agents
4. **Specialized sub-agents** for synthesis - Receive pre-retrieved context via Dependencies
5. **Clear separation** - Conversation (orchestrator) vs Operations (services/agents)

## Agent Hierarchy

### 1. Main Orchestrator Agent

- **Type**: `Agent[None, str]`
- **Output**: Plain text (streamable)
- **Purpose**: Conversational interface that streams responses to user
- **Responsibilities**:
  - Maintain conversation flow
  - Analyze user intent
  - Decide which specialized agent to dispatch
  - Stream results back to user
- **System Prompt**: "You are a conversational assistant. Analyze user intent and dispatch the appropriate specialized agent."

### 2. Specialized Domain Sub-Agents

Each sub-agent is an expert in a specific biological domain:

#### Disease Annotation Agent

- **Type**: `Agent[DiseasePipelineOutput, DiseaseAnnotations]`
- **Expertise**: Diseases, conditions, phenotypes, symptoms
- **Receives**: Pre-filtered disease-relevant chunks from pipeline
- **Returns**: Structured disease annotations with confidence scores

#### Gene/Protein Agent

- **Type**: `Agent[GenePipelineOutput, GeneAnnotations]`
- **Expertise**: Genes, proteins, mutations, expression patterns
- **Receives**: Pre-filtered gene-relevant chunks from pipeline
- **Returns**: Structured gene/protein information

#### Pathway Agent

- **Type**: `Agent[PathwayPipelineOutput, PathwayAnnotations]`
- **Expertise**: Biological pathways, interactions, networks
- **Receives**: Pre-filtered pathway-relevant chunks from pipeline
- **Returns**: Structured pathway information

#### Drug/Chemical Agent

- **Type**: `Agent[ChemicalPipelineOutput, ChemicalAnnotations]`
- **Expertise**: Drugs, compounds, chemical interactions
- **Receives**: Pre-filtered chemical-relevant chunks from pipeline
- **Returns**: Structured chemical/drug information

### 3. Data Preparation Pipelines (Python Services)

Each pipeline prepares data for its corresponding agent:

#### Disease Pipeline

- Searches for disease-related terms
- Filters chunks by disease relevance
- Extracts disease ontology matches
- Prepares structured context

#### Gene Pipeline

- Searches for gene/protein mentions
- Cross-references with gene databases
- Filters by gene relevance
- Prepares structured context

#### Pathway Pipeline

- Searches for pathway terms
- Identifies interaction patterns
- Filters by pathway relevance
- Prepares structured context

#### Chemical Pipeline

- Searches for chemical/drug names
- Identifies SMILES patterns
- Filters by chemical relevance
- Prepares structured context

### 4. Core Services (Python Classes)

- **HybridSearchService**: Domain-agnostic search
- **RerankingService**: Cross-encoder reranking
- **OntologyService**: Ontology matching
- **DatabaseService**: External database queries
- These support all pipelines

## Complete Pipeline Flow

```
User: "Find all disease annotations in this paper"
    ↓
Orchestrator Analyzes Intent → DISEASE_ANNOTATION task
    ↓
Orchestrator Starts Streaming: "I'll search for disease annotations..."
    ↓
Orchestrator Dispatches: run_disease_pipeline()
    ↓
Disease Pipeline Executes (Python, No LLM):
    ├── HybridSearchService.search(disease_terms)
    │   ├── Vector Search with disease-specific embeddings
    │   ├── Lexical Search for disease ontology terms
    │   └── Merge results with disease weighting
    ├── DiseaseFilterService.filter()
    │   ├── Filter chunks by disease relevance score
    │   ├── Extract sentences with disease mentions
    │   └── Group by disease type
    ├── OntologyService.match()
    │   ├── Match to Disease Ontology (DO)
    │   ├── Match to Human Phenotype Ontology (HPO)
    │   └── Add ontology IDs
    └── Prepare DiseasePipelineOutput
    ↓
Disease Agent Receives Prepared Data:
    ├── Pre-filtered disease chunks
    ├── Ontology matches
    ├── Relevance scores
    └── No additional search needed
    ↓
Disease Agent Synthesizes (LLM call):
    ├── Validates disease annotations
    ├── Adds confidence scores
    ├── Formats structured output
    └── Returns DiseaseAnnotations
    ↓
Orchestrator Receives Results
    ↓
Orchestrator Continues Streaming: "I found 5 disease annotations:
1. Breast cancer (DOID:1612) - mentioned on page 3..."
```

**Key Architecture Points**:

- **Orchestrator**: Decides WHAT to run based on intent
- **Pipelines**: Do the HOW - all data preparation
- **Specialized Agents**: Expert synthesis from prepared data
- **Clear Separation**: Intent → Pipeline → Agent → Response

## Event Types for Frontend (Simplified)

### Streaming Updates

```python
{
    "type": "text_delta",      # Streaming conversational text
    "content": "I'm analyzing the paper for gene mentions..."
}

{
    "type": "status",          # Status update (optional)
    "content": "Extracting entities",
    "metadata": {"tool": "entity_extraction"}
}

{
    "type": "tool_complete",   # Tool finished - full results available
    "content": "Entities extracted",
    "metadata": {
        "entities": [...],     # Complete list, not streamed
        "count": 5
    }
}

{
    "type": "complete",        # Everything done
    "metadata": {
        "total_entities": 5,
        "confidence": 0.85
    }
}
```

## Implementation Example

```python
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel
from typing import List, Dict, Any

# Main Orchestrator - only streams text
orchestrator = Agent(
    'gpt-4o',
    output_type=str,
    system_prompt="""You are a conversational biocuration assistant.
    Analyze user intent and dispatch appropriate specialized agents.
    Stream friendly responses while work happens in background."""
)

# Specialized Disease Agent
disease_agent = Agent(
    'gpt-4o',
    output_type=DiseaseAnnotations,
    deps_type=DiseasePipelineOutput,
    system_prompt="""You are a disease annotation specialist.
    You receive pre-filtered disease-relevant data.
    Validate and structure disease annotations with confidence scores."""
)

# Specialized Gene Agent
gene_agent = Agent(
    'gpt-4o',
    output_type=GeneAnnotations,
    deps_type=GenePipelineOutput,
    system_prompt="""You are a gene/protein annotation specialist.
    You receive pre-filtered gene-relevant data.
    Validate and structure gene annotations with database IDs."""
)

@orchestrator.tool
async def find_disease_annotations(ctx: RunContext, query: str) -> str:
    """Orchestrator tool for disease annotation requests"""

    # Run the disease pipeline (Python services, no LLM)
    pipeline_output = await disease_pipeline.run(
        document_id=ctx.document_id,
        query=query
    )

    # Dispatch to specialized disease agent
    result = await disease_agent.run(
        "",
        deps=pipeline_output
    )

    # Format for streaming
    annotations = result.output.annotations
    return f"Found {len(annotations)} disease annotations: {format_diseases(annotations)}"

@orchestrator.tool
async def find_gene_mentions(ctx: RunContext, query: str) -> str:
    """Orchestrator tool for gene annotation requests"""

    # Run the gene pipeline (Python services, no LLM)
    pipeline_output = await gene_pipeline.run(
        document_id=ctx.document_id,
        query=query
    )

    # Dispatch to specialized gene agent
    result = await gene_agent.run(
        "",
        deps=pipeline_output
    )

    # Format for streaming
    genes = result.output.genes
    return f"Found {len(genes)} genes: {format_genes(genes)}"

# Disease Pipeline (Pure Python)
class DiseasePipeline:
    async def run(self, document_id: str, query: str) -> DiseasePipelineOutput:
        # 1. Search with disease-specific strategy
        chunks = await hybrid_search.search(
            query=query,
            boost_terms=["disease", "condition", "syndrome", "disorder"],
            ontology="disease"
        )

        # 2. Filter for disease relevance
        filtered = await disease_filter.filter(chunks)

        # 3. Match to ontologies
        matched = await ontology_service.match(
            filtered,
            ontologies=["DO", "HPO", "MONDO"]
        )

        # 4. Prepare output for agent
        return DiseasePipelineOutput(
            chunks=matched,
            ontology_matches=matched.ontology_ids,
            relevance_scores=matched.scores
        )
```

## Use Cases

### Case 1: Simple Question

**User**: "What genes are mentioned in this paper?"

**Response Flow**:

1. Stream text: "I'll analyze the paper for gene mentions. Let me review the content..."
2. Tool runs in background: extract_entities() [2-3 seconds]
3. Stream continuation: "I found 5 genes in the paper: BRCA1, TP53, EGFR, KRAS, and MYC..."
4. Update UI: Entities sidebar populated with complete list

### Case 2: Document Q&A

**User**: "What are the main findings about BRCA1?"

**Response Flow**:

1. Stream text: "Let me search for information about BRCA1 in this paper..."
2. RAG retrieval runs in background [1-2 seconds]
3. Stream answer: "The paper identifies three key findings about BRCA1: First..."
4. Metadata available: Citations and page references (not streamed, just available)

### Case 3: Complex Analysis

**User**: "Analyze this paper and extract all relevant biological entities"

**Response Flow**:

1. Stream text: "I'll perform a comprehensive analysis of the paper. This will include genes, proteins, diseases, and pathways..."
2. Multiple tools run in background (not shown individually to user)
3. Stream results: "I've completed the analysis. I found 15 genes, 8 proteins, 3 diseases..."
4. Update UI: Complete structured data available in sidebars

## Implementation Steps

### Phase 1: Core Streaming (IMMEDIATE)

1. Convert main agent to `output_type=str`
2. Implement `stream_text()` in `_process_stream()`
3. Remove BioCurationOutput from main agent
4. Test basic streaming

### Phase 2: Entity Extraction Tool

1. Create entity extraction sub-agent
2. Add as tool to main agent
3. Stream entity events during processing
4. Update frontend to handle entity events

### Phase 3: Form Filling Tool

1. Define DocumentFields model
2. Create form-filling sub-agent
3. Add form field streaming events
4. Implement UI form population

### Phase 4: Annotation Tool

1. Create annotation suggestion sub-agent
2. Stream annotation events
3. Integrate with PDF viewer highlights

## Frontend Updates Required

### AgentInterface.tsx

- Handle new event types (tool_call, entity, form_field)
- Maintain entities/annotations state separately from text
- Update UI components based on event types

### Sidebar Components

- EntitiesTab: Update from entity events
- AnnotationsTab: Update from annotation events
- New FormTab: Populate from form_field events

## Benefits

1. **Immediate Text Streaming**: Users see responses right away
2. **Structured Data**: Still get entities, annotations, forms
3. **Progressive Enhancement**: UI updates as data becomes available
4. **Flexible**: Can handle both conversational and structured tasks
5. **Scalable**: Easy to add new specialized tools

## Risks & Mitigations

### Risk 1: Tool Call Latency

**Mitigation**: Stream progress updates during tool execution

### Risk 2: Complex State Management

**Mitigation**: Clear event types and predictable update patterns

### Risk 3: Token Usage

**Mitigation**: Cache tool results, batch similar requests

## Success Metrics

- [ ] Text starts streaming within 1 second
- [ ] Entities extracted with >90% accuracy
- [ ] Forms populated correctly 95% of time
- [ ] Users report improved experience
- [ ] Token usage reasonable (<2x current)

## Next Steps

1. Review and approve this plan
2. Implement Phase 1 (Core Streaming)
3. Test with simple queries
4. Iterate on tool design
5. Deploy incrementally

---

**Status**: DRAFT
**Version**: 1.0.0
**Date**: 2025-01-13
**Author**: Claude + Human
