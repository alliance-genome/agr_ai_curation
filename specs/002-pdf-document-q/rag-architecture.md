# Multi-Agent RAG Architecture with Specialized Domain Experts

**Date**: 2025-01-14 | **Version**: 2.0.0

## Overview

This document specifies the complete multi-agent RAG architecture with specialized domain experts. The main orchestrator agent analyzes user intent and streams conversational text. Based on the detected intent (disease, gene, pathway, etc.), it dispatches domain-specific pipelines that prepare data, then passes to specialized sub-agents for expert synthesis.

## Core Architecture Principles

1. **Intent-Based Routing**: Orchestrator analyzes intent and routes to appropriate specialist
2. **Domain Expertise**: Each sub-agent is an expert in its biological domain
3. **Pipeline-First**: Data preparation happens in pipelines before agent synthesis
4. **Clear Hierarchy**: Orchestrator (conversation) → Pipelines (data prep) → Agents (synthesis)

## Example User Interaction

**User**: "What are the main findings about BRCA1 in this paper?"

**Orchestrator** (streaming): "I'll search the document for information about BRCA1. Let me look through the paper to find relevant sections..."

_[Behind the scenes: Tool calls hybrid_search_service, reranking_service, then rag_synthesis_agent]_

**Orchestrator** (continues streaming): "I found several important findings about BRCA1 in the paper. The study identifies three key mutations in the BRCA1 gene that are associated with increased cancer risk. First, the mutation at position 1234..."

The orchestrator maintains the conversation flow while all technical operations happen invisibly in the background.

## Agent Hierarchy

### 1. Main Orchestrator Agent (Conversational Only)

```python
from pydantic_ai import Agent, RunContext

main_agent = Agent(
    'gpt-4o',
    output_type=str,  # ONLY streams conversational text
    system_prompt="""You are a friendly biocuration assistant helping researchers
    analyze PDF documents. You engage in natural conversation with the user,
    explaining what you're doing as you coordinate various tools and agents.

    Your role is purely conversational - you stream text responses to keep
    the user informed while specialized tools handle all actual operations."""
)
```

### 2. Specialized Domain Agents

```python
# Disease Specialist
disease_agent = Agent(
    'gpt-4o',
    output_type=DiseaseAnnotations,
    deps_type=DiseasePipelineOutput,
    system_prompt="""You are a disease annotation specialist with expertise in:
    - Disease Ontology (DO)
    - Human Phenotype Ontology (HPO)
    - Clinical manifestations and symptoms
    - Disease-gene associations

    Validate and structure disease annotations from pre-filtered data."""
)

# Gene/Protein Specialist
gene_agent = Agent(
    'gpt-4o',
    output_type=GeneAnnotations,
    deps_type=GenePipelineOutput,
    system_prompt="""You are a molecular biology specialist with expertise in:
    - Gene nomenclature and identifiers
    - Protein structure and function
    - Mutations and variants
    - Expression patterns

    Validate and structure gene/protein annotations from pre-filtered data."""
)

# Pathway Specialist
pathway_agent = Agent(
    'gpt-4o',
    output_type=PathwayAnnotations,
    deps_type=PathwayPipelineOutput,
    system_prompt="""You are a systems biology specialist with expertise in:
    - Biological pathways (KEGG, Reactome)
    - Protein-protein interactions
    - Regulatory networks
    - Signal transduction

    Validate and structure pathway information from pre-filtered data."""
)
```

## Pipeline Output Models (Dependencies)

```python
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class PDFChunk(BaseModel):
    """A chunk of text from a PDF document"""
    text: str
    page_number: int
    chunk_id: str
    bbox: Optional[List[float]] = None
    relevance_score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DiseasePipelineOutput(BaseModel):
    """Pre-processed data for disease agent"""
    disease_chunks: List[PDFChunk] = Field(
        description="Chunks filtered for disease relevance"
    )
    ontology_matches: List[Dict[str, Any]] = Field(
        description="Matched disease ontology terms"
    )
    disease_mentions: List[str] = Field(
        description="Raw disease mentions found"
    )
    confidence_scores: Dict[str, float] = Field(
        description="Confidence scores for each disease"
    )

class GenePipelineOutput(BaseModel):
    """Pre-processed data for gene agent"""
    gene_chunks: List[PDFChunk] = Field(
        description="Chunks filtered for gene relevance"
    )
    gene_database_matches: List[Dict[str, Any]] = Field(
        description="Matched gene database entries"
    )
    gene_mentions: List[str] = Field(
        description="Raw gene/protein mentions"
    )
    variant_info: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Mutation/variant information"
    )

class PathwayPipelineOutput(BaseModel):
    """Pre-processed data for pathway agent"""
    pathway_chunks: List[PDFChunk] = Field(
        description="Chunks filtered for pathway relevance"
    )
    pathway_database_matches: List[Dict[str, Any]] = Field(
        description="Matched pathway database entries"
    )
    interaction_pairs: List[tuple] = Field(
        description="Protein-protein interaction pairs"
    )
    pathway_mentions: List[str] = Field(
        description="Raw pathway mentions"
    )
```

## RAG Pipeline Implementation

### Main Orchestrator's Intent-Based Dispatch

```python
@main_agent.tool
async def analyze_and_dispatch(ctx: RunContext, query: str) -> str:
    """
    The orchestrator analyzes user intent and dispatches to appropriate specialist.
    Returns a string that the orchestrator streams to the user.
    """

    # Analyze intent to determine which specialist is needed
    intent = analyze_intent(query)

    if intent.type == "disease":
        # Run disease pipeline
        pipeline_output = await disease_pipeline.run(
            document_id=ctx.document_id,
            query=query
        )
        # Dispatch to disease specialist
        result = await disease_agent.run("", deps=pipeline_output)
        return format_disease_results(result.output)

    elif intent.type == "gene":
        # Run gene pipeline
        pipeline_output = await gene_pipeline.run(
            document_id=ctx.document_id,
            query=query
        )
        # Dispatch to gene specialist
        result = await gene_agent.run("", deps=pipeline_output)
        return format_gene_results(result.output)

    elif intent.type == "pathway":
        # Run pathway pipeline
        pipeline_output = await pathway_pipeline.run(
            document_id=ctx.document_id,
            query=query
        )
        # Dispatch to pathway specialist
        result = await pathway_agent.run("", deps=pipeline_output)
        return format_pathway_results(result.output)

    else:
        # General question - use general QA pipeline
        pipeline_output = await general_qa_pipeline.run(
            document_id=ctx.document_id,
            query=query
        )
        result = await general_qa_agent.run("", deps=pipeline_output)
        return result.output.answer
```

### Hybrid Search Service (Not an Agent)

```python
class HybridSearchService:
    """
    A service that performs hybrid search operations.
    This is NOT a PydanticAI agent - it's a regular service
    that handles the retrieval logic.
    """

    async def search(self, query: str, document_id: str) -> List[PDFChunk]:
        """
        Performs hybrid search combining vector and lexical search.
        This runs completely independently of any LLM.
        """

        # STEP 1: Vector Search (No LLM)
        vector_results = await self._vector_search(
            query=query,
            document_id=document_id,
            top_k=50
        )

        # STEP 2: Lexical Search (No LLM)
        lexical_results = await self._lexical_search(
            query=query,
            document_id=document_id,
            top_k=50
        )

        # STEP 3: Merge Results
        merged = self._merge_results(
            vector_results,
            lexical_results,
            alpha=0.6
        )

        # STEP 4: Apply MMR diversification
        diversified = self._apply_mmr(
            chunks=merged[:20],
            lambda_param=0.7
        )

        return diversified
```

### Reranking Service (Also Not an Agent)

```python
class RerankingService:
    """
    A service that reranks search results using a local cross-encoder.
    This is NOT a PydanticAI agent.
    """

    def __init__(self):
        self.model = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-12-v2')

    async def rerank(self, chunks: List[PDFChunk], query: str) -> List[PDFChunk]:
        """
        Reranks chunks using a local cross-encoder model.
        No LLM API calls involved.
        """

        # Score all chunks
        pairs = [[query, chunk.text] for chunk in chunks]
        scores = self.model.predict(pairs)

        # Sort by score
        scored_chunks = [
            (chunk, score)
            for chunk, score in zip(chunks, scores)
        ]
        scored_chunks.sort(key=lambda x: x[1], reverse=True)

        # Return reranked chunks with scores
        for chunk, score in scored_chunks:
            chunk.rerank_score = score

        return [chunk for chunk, _ in scored_chunks]
```

## Hybrid Search Implementation Details

### Vector Search

```python
async def vector_search(query: str, top_k: int, index: str) -> List[PDFChunk]:
    """
    Performs semantic vector search using pgvector HNSW index

    Performance target: <100ms for top-50
    """
    # Generate query embedding
    query_embedding = await generate_embedding(query)

    # Search using HNSW index
    results = await db.execute(
        """
        SELECT chunk_id, text, page_number, bbox,
               embedding <=> %s::vector AS distance
        FROM pdf_embeddings
        WHERE pdf_document_id = %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (query_embedding, document_id, query_embedding, top_k)
    )

    return [PDFChunk(**row) for row in results]
```

### Lexical Search

```python
async def lexical_search(query: str, top_k: int, method: str) -> List[PDFChunk]:
    """
    Performs keyword-based lexical search using PostgreSQL tsvector

    Performance target: <50ms for top-50
    """
    # Process query for text search
    processed_query = process_query_for_tsvector(query)

    # Search using tsvector index
    results = await db.execute(
        """
        SELECT chunk_id, text, page_number, bbox,
               ts_rank(search_vector, query) AS rank
        FROM pdf_chunks,
             to_tsquery('english', %s) query
        WHERE search_vector @@ query
              AND pdf_document_id = %s
        ORDER BY rank DESC
        LIMIT %s
        """,
        (processed_query, document_id, top_k)
    )

    return [PDFChunk(**row) for row in results]
```

### Result Merging

```python
def merge_search_results(
    vector_results: List[PDFChunk],
    lexical_results: List[PDFChunk],
    alpha: float = 0.6
) -> List[PDFChunk]:
    """
    Merges vector and lexical search results using reciprocal rank fusion

    alpha: Weight for vector search (0.6 = 60% vector, 40% lexical)
    """
    # Create rank dictionaries
    vector_ranks = {chunk.chunk_id: i+1 for i, chunk in enumerate(vector_results)}
    lexical_ranks = {chunk.chunk_id: i+1 for i, chunk in enumerate(lexical_results)}

    # Calculate combined scores
    all_chunk_ids = set(vector_ranks.keys()) | set(lexical_ranks.keys())
    scores = {}

    for chunk_id in all_chunk_ids:
        vector_score = 1.0 / (vector_ranks.get(chunk_id, 1000))
        lexical_score = 1.0 / (lexical_ranks.get(chunk_id, 1000))
        scores[chunk_id] = alpha * vector_score + (1 - alpha) * lexical_score

    # Sort by combined score
    sorted_chunks = sorted(
        scores.items(),
        key=lambda x: x[1],
        reverse=True
    )

    # Return chunks in order
    chunk_map = {c.chunk_id: c for c in vector_results + lexical_results}
    return [chunk_map[chunk_id] for chunk_id, _ in sorted_chunks]
```

## Performance Considerations

### Latency Targets

| Operation          | Target    | Notes                        |
| ------------------ | --------- | ---------------------------- |
| Vector Search      | <100ms    | HNSW index with ef_search=64 |
| Lexical Search     | <50ms     | tsvector with GIN index      |
| Reranking          | <200ms    | Local cross-encoder model    |
| MMR                | <10ms     | Pure algorithm               |
| LLM Synthesis      | <2s       | Only top 5 chunks sent       |
| **Total Pipeline** | **<2.5s** | End-to-end                   |

### Token Optimization

1. **Pre-retrieval**: All search/ranking happens before LLM calls
2. **Chunk Limiting**: Only top 5 chunks sent to synthesis (max ~2000 tokens)
3. **Model Selection**: Cheaper models for extraction tasks
4. **Caching**: Cache embeddings and search results when possible

## Testing Strategy

### Unit Tests

```python
# backend/tests/unit/test_rag_dependencies.py
def test_rag_dependencies_validation():
    """Test RAGDependencies model validation"""
    deps = RAGDependencies(
        retrieved_chunks=[...],
        similarity_scores=[0.9, 0.85, 0.8],
        query="What genes are mentioned?"
    )
    assert len(deps.retrieved_chunks) == len(deps.similarity_scores)

# backend/tests/unit/test_hybrid_search.py
def test_merge_search_results():
    """Test result merging with reciprocal rank fusion"""
    vector = [chunk1, chunk2, chunk3]
    lexical = [chunk2, chunk4, chunk1]
    merged = merge_search_results(vector, lexical, alpha=0.6)
    assert merged[0] == chunk2  # Appears in both
```

### Integration Tests

```python
# backend/tests/integration/test_rag_pipeline.py
async def test_full_rag_pipeline():
    """Test complete RAG pipeline with real components"""
    # Setup test document with embeddings
    await setup_test_document()

    # Run pipeline
    result = await search_document(
        ctx=mock_context,
        query="What are the findings about BRCA1?"
    )

    # Verify all steps executed
    assert 'answer' in result
    assert 'citations' in result
    assert result['confidence'] > 0.7
    assert len(result['citations']) > 0
```

## Monitoring & Observability

### Metrics to Track

1. **Search Performance**
   - Vector search latency (p50, p95, p99)
   - Lexical search latency
   - Result counts and overlap

2. **Reranking Performance**
   - Reranking latency
   - Score distributions
   - Diversity metrics (post-MMR)

3. **LLM Usage**
   - Tokens consumed per query
   - Synthesis latency
   - Confidence score distribution

4. **Pipeline Health**
   - End-to-end latency
   - Error rates by component
   - Cache hit rates

### Logging

```python
import structlog

logger = structlog.get_logger()

# In search_document tool
logger.info(
    "rag_pipeline_executed",
    query=query,
    vector_results=len(vector_results),
    lexical_results=len(lexical_results),
    reranked_count=len(reranked_chunks),
    final_chunks=len(diversified_chunks),
    total_time_ms=total_time,
    correlation_id=ctx.correlation_id
)
```

## Future Enhancements

1. **Query Expansion**: Add query expansion agent for synonym generation
2. **Adaptive Reranking**: Adjust reranking based on query type
3. **Dynamic Chunk Selection**: Vary chunk count based on confidence
4. **Feedback Loop**: Use user feedback to improve ranking
5. **Multi-Document**: Extend to search across multiple PDFs simultaneously

---

**Status**: APPROVED
**Version**: 2.0.0
**Last Updated**: 2025-01-14
