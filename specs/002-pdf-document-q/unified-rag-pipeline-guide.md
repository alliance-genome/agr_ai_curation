# Unified RAG Pipeline Implementation Guide

> **Document family note**: This guide captures the original RAG refactor design. Refer to `multi-agent-implementation-guide.md` for the complementary routing architecture, and to `final-implementation-guide.md` for the authoritative merged plan (takes precedence when instructions conflict).

**Version**: 1.0.0
**Date**: 2025-01-17
**Status**: Implementation Ready
**Priority**: HIGH - Simplifies entire multi-agent architecture

## Executive Summary

Instead of building separate search systems for PDFs, disease ontologies, gene databases, and other sources, we'll **extend our existing RAG pipeline** to handle ANY document type. This dramatically simplifies our multi-agent system since every specialist agent can use the same battle-tested pipeline.

**Core Insight**: "Why rebuild what we already have? Our sophisticated RAG pipeline (hybrid search + cross-encoder reranking + MMR) should work for EVERYTHING."

### Key Benefits

1. **Code Reuse**: One pipeline handles PDFs, OBO files, CSVs, databases
2. **Consistent Quality**: Same search quality across all document types
3. **Simplified Agents**: Disease/Gene agents just call the unified pipeline
4. **Easier Maintenance**: Fix once, improve everywhere
5. **Proven Technology**: Our RAG pipeline already works great for PDFs

### Architecture Change

```
BEFORE: Multiple Search Systems
- PDF Pipeline (hybrid search + reranking)
- Disease Ontology Search (custom implementation)
- Gene Ontology Search (another custom implementation)
- CSV Search (yet another implementation)

AFTER: One Unified Pipeline
- Unified RAG Pipeline
  ├── PDF Source Adapter
  ├── OBO Source Adapter
  ├── CSV Source Adapter
  └── Database Source Adapter
```

---

## Current State Analysis

### What We Have (Works Great!)

```python
# backend/lib/pipelines/general_pipeline.py

class GeneralPipeline:
    """Currently handles PDFs only"""

    async def run(self, *, pdf_id: UUID, query: str) -> GeneralPipelineOutput:
        # 1. Embed query
        embedding = self._query_embedder(query)

        # 2. Hybrid search (vector + lexical)
        search_response = self._hybrid_search.query(
            pdf_id=pdf_id,  # ← Hardcoded to PDFs!
            embedding=embedding,
            query=query
        )

        # 3. Cross-encoder reranking
        reranked = self._reranker.rerank(
            query=query,
            candidates=candidates
        )

        # 4. MMR diversification
        final = apply_mmr(reranked)

        return results
```

### The Problem

Our multi-agent system needs to search:

- **Disease Ontology**: 15,000+ disease terms with definitions
- **Gene Ontology**: 45,000+ gene function terms
- **FlyBase Vocabulary**: Controlled vocabularies
- **Protein Databases**: UniProt entries
- **Literature**: PubMed abstracts

Building separate search systems for each would be:

- 🔴 Massive code duplication
- 🔴 Inconsistent search quality
- 🔴 Maintenance nightmare
- 🔴 No shared improvements

---

## Unified Pipeline Architecture

### Important: Ingestion vs Query Time

**Critical Design Decision**: Following Codex's feedback, we separate **ingestion** (one-time, background) from **query-time** operations:

1. **Ingestion Phase** (happens once, stores in PostgreSQL):
   - Parse source files (OBO, CSV, etc.)
   - Generate embeddings
   - Store in unified PostgreSQL tables
   - Signal "index ready" status

2. **Query Phase** (happens per search):
   - Query pre-indexed PostgreSQL data
   - No recomputation of embeddings
   - Fast retrieval from existing indices

### Core Abstraction: Document Sources

```python
# backend/lib/pipelines/document_source.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List


class IndexStatus(Enum):
    """Track ingestion lifecycle for each external source."""

    NOT_INDEXED = "not_indexed"
    INDEXING = "indexing"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class SourceRegistration:
    """Lightweight descriptor used during pipeline registration."""

    source_type: str  # e.g. "pdf", "ontology_disease"
    default_source_id: str  # e.g. PDF UUID or "all"


class DocumentSource(ABC):
    """Adapter contract for making a data source searchable via RAG."""

    @abstractmethod
    async def ingest(self, *, source_id: str) -> IndexStatus:
        """Parse raw data, write unified chunks + embeddings to Postgres."""

    @abstractmethod
    async def index_status(self, *, source_id: str) -> IndexStatus:
        """Report whether the unified index is ready to serve queries."""

    @abstractmethod
    def registration(self) -> SourceRegistration:
        """Return metadata used when registering the source with the pipeline."""

    @abstractmethod
    def format_citation(self, chunk_row) -> Dict[str, Any]:
        """Convert a unified chunk row into a citation payload for the UI."""
```

> In practice each adapter runs inside an ingestion job (CLI, Celery worker, etc.) that writes rows into a shared `unified_chunks` table and then invokes the existing `EmbeddingService`. Because `HybridSearch` already filters on `source_type`/`source_id`, once an adapter returns `IndexStatus.READY` the query-time path requires no further changes.

### Refactored Unified Pipeline

```python
# backend/lib/pipelines/unified_pipeline.py

from typing import Optional, Union
from uuid import UUID

class UnifiedRAGPipeline:
    """
    ONE pipeline for ALL document types!
    Replaces GeneralPipeline with source-agnostic design.
    """

    def __init__(
        self,
        *,
        hybrid_search: HybridSearch,
        reranker: Reranker,
        query_embedder: QueryEmbedderProtocol,
        config: Dict[str, Any]
    ):
        self._hybrid_search = hybrid_search
        self._reranker = reranker
        self._query_embedder = query_embedder
        self._config = config

        # Registry of document sources
        self._sources: Dict[str, DocumentSource] = {}

    def register_source(self, source: DocumentSource) -> None:
        """Register a new document source"""
        self._sources[source.get_source_type()] = source

    async def search(
        self,
        *,
        source_type: str,
        source_id: str,
        query: str,
        context: Optional[str] = None,
        config_overrides: Optional[Dict] = None
    ) -> UnifiedPipelineOutput:
        """
        Universal search across any document type.

        Args:
            source_type: Type of source ('pdf', 'disease_ontology', etc.)
            source_id: Identifier within that source
            query: User's search query
            context: Optional context for boosting
            config_overrides: Source-specific config overrides
        """

        # Get source adapter
        if source_type not in self._sources:
            raise ValueError(f"Unknown source type: {source_type}")
        source = self._sources[source_type]

        # Apply config overrides for this source type
        config = {**self._config}
        if source_type in config.get('source_overrides', {}):
            config.update(config['source_overrides'][source_type])
        if config_overrides:
            config.update(config_overrides)

        # 1. Embed query (same as before!)
        query_embedding = self._query_embedder(query)

        # 2. Run hybrid search (now source-agnostic!)
        search_response = await self._hybrid_search.query(
            source_type=source_type,
            source_id=source_id,
            embedding=query_embedding,
            query=query,
            vector_top_k=config['vector_top_k'],
            lexical_top_k=config['lexical_top_k'],
            max_results=config['max_results']
        )

        # 3. Prepare candidates for reranking
        candidates = [
            RerankerCandidate(
                chunk_id=result.chunk_id,
                text=result.text,
                retriever_score=result.score,
                metadata=result.metadata
            )
            for result in search_response.results
        ]

        # 4. Context-aware boosting (NEW!)
        if context:
            candidates = self._apply_context_boost(candidates, context)

        # 5. Cross-encoder reranking (same as before!)
        reranked = await self._reranker.rerank(
            query=query,
            candidates=candidates,
            top_k=config['rerank_top_k']
        )

        # 6. MMR diversification (same as before!)
        if config.get('apply_mmr', True):
            final = self._apply_mmr(
                reranked,
                lambda_param=config['mmr_lambda']
            )
        else:
            final = reranked

        # 7. Format output with citations
        return UnifiedPipelineOutput(
            source_type=source_type,
            source_id=source_id,
            query=query,
            chunks=[
                UnifiedChunk(
                    chunk_id=r.chunk_id,
                    text=r.text,
                    score=r.score,
                    citation=source.format_citation(r)
                )
                for r in final
            ],
            metadata={
                'source_type': source_type,
                'config': config,
                'total_candidates': len(candidates),
                'final_results': len(final)
            }
        )

    def _apply_context_boost(
        self,
        candidates: List[RerankerCandidate],
        context: str
    ) -> List[RerankerCandidate]:
        """Boost candidates that appear in context"""
        context_lower = context.lower()

        for candidate in candidates:
            # Simple heuristic: boost if mentioned in context
            if any(term in context_lower for term in
                   candidate.text.lower().split()[:10]):
                candidate.retriever_score *= 1.5

        return candidates
```

---

## Document Source Implementations

### 1. PDF Source (Existing, Refactored)

```python
# backend/app/services/document_sources/pdf_source.py

class PDFDocumentSource(DocumentSource):
    """Thin wrapper because PDFs already follow the unified schema."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def registration(self) -> SourceRegistration:
        return SourceRegistration(source_type="pdf", default_source_id="<pdf-id>")

    async def ingest(self, *, source_id: str) -> IndexStatus:
        """No-op: PDFs are ingested via the existing upload pipeline."""
        return IndexStatus.READY

    async def index_status(self, *, source_id: str) -> IndexStatus:
        with self._session_factory() as session:
            exists = (
                session.query(PDFChunk.id)
                .filter(PDFChunk.pdf_id == source_id)
                .limit(1)
                .first()
            )
            return IndexStatus.READY if exists else IndexStatus.NOT_INDEXED

    def format_citation(self, chunk_row: PDFChunk) -> Dict[str, Any]:
        return {
            "type": "pdf",
            "page": chunk_row.page,
            "section": chunk_row.section,
        }
```

### 2. Disease Ontology Source (PostgreSQL-backed)

```python
# backend/app/services/document_sources/ontology_source.py

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable

from sqlalchemy.orm import Session

from app.models import UnifiedChunk  # see schema below
from lib.embedding_service import EmbeddingService


class OntologyDocumentSource(DocumentSource):
    """Persist ontology terms into the unified chunks table."""

    def __init__(
        self,
        *,
        ontology_type: str,
        data_path: Path,
        session_factory,
        embedding_service: EmbeddingService,
        embedding_model: str,
    ) -> None:
        self._ontology_type = ontology_type
        self._data_path = data_path
        self._session_factory = session_factory
        self._embedding_service = embedding_service
        self._embedding_model = embedding_model

    def registration(self) -> SourceRegistration:
        return SourceRegistration(
            source_type=f"ontology_{self._ontology_type}",
            default_source_id="all",
        )

    async def ingest(self, *, source_id: str) -> IndexStatus:
        try:
            with self._session_factory() as session:
                if self._already_indexed(session, source_id):
                    return IndexStatus.READY

                terms = list(self._parse_obo(self._data_path))
                rows = [
                    UnifiedChunk(
                        source_type=self.registration().source_type,
                        source_id=source_id,
                        chunk_id=term["id"],
                        text=self._format_as_narrative(term),
                        metadata={
                            "term_id": term["id"],
                            "name": term["name"],
                            "definition": term.get("definition", ""),
                            "synonyms": term.get("synonyms", []),
                            "parents": term.get("parents", []),
                            "xrefs": term.get("xrefs", []),
                        },
                    )
                    for term in terms
                ]

                session.bulk_save_objects(rows)
                session.commit()

                self._embedding_service.embed_unified_chunks(
                    source_type=self.registration().source_type,
                    source_id=source_id,
                    model_name=self._embedding_model,
                )

                return IndexStatus.READY
        except Exception:  # pragma: no cover - logged by ingestion runner
            return IndexStatus.ERROR

    async def index_status(self, *, source_id: str) -> IndexStatus:
        with self._session_factory() as session:
            return (
                IndexStatus.READY
                if self._already_indexed(session, source_id)
                else IndexStatus.NOT_INDEXED
            )

    def format_citation(self, chunk_row: UnifiedChunk) -> Dict[str, Any]:
        meta = chunk_row.metadata or {}
        return {
            "type": "ontology",
            "ontology": self._ontology_type,
            "term_id": meta.get("term_id"),
            "term_name": meta.get("name"),
        }

    def _already_indexed(self, session: Session, source_id: str) -> bool:
        return (
            session.query(UnifiedChunk.id)
            .filter(
                UnifiedChunk.source_type == self.registration().source_type,
                UnifiedChunk.source_id == source_id,
            )
            .limit(1)
            .first()
            is not None
        )

    def _parse_obo(self, filepath: Path) -> Iterable[Dict]:
        # Minimal parser shown earlier; implementation omitted for brevity.
        ...

    def _format_as_narrative(self, term: Dict) -> str:
        lines = [f"{term['name']} ({term['id']})"]
        if term.get("definition"):
            lines.append(f"Definition: {term['definition']}")
        if term.get("synonyms"):
            lines.append(f"Synonyms: {', '.join(term['synonyms'])}")
        if term.get("subset"):
            lines.append(f"Subset: {', '.join(term['subset'])}")
        if term.get("parents"):
            parents = [f"{p['id']} ({p.get('label', 'unknown')})" for p in term["parents"]]
            lines.append(f"Parents: {', '.join(parents)}")
        if term.get("xrefs"):
            lines.append(f"Cross references: {'; '.join(term['xrefs'])}")
        return "\n".join(lines)
```

**UnifiedChunk schema (proposed)**

```python
class UnifiedChunk(Base):
    __tablename__ = "unified_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_type = Column(String, index=True)
    source_id = Column(String, index=True)
    chunk_id = Column(String, index=True)
    text = Column(Text)
    metadata = Column(JSONB)
    embedding = Column(Vector(settings.embedding_dimensions))
```

> **Implementation note**: `EmbeddingService.embed_unified_chunks` is a thin helper you can add alongside `embed_pdf` that queries `UnifiedChunk` rows by `source_type/source_id`, calls the existing embedding client, and stores vectors in the same table (or a companion `unified_embeddings` table if you prefer normalised storage).

### Important: Ontology Isolation in RAG

### Important: Ontology Isolation in RAG

**Per Codex's feedback**: When RAG'ing ontologies, we need to ensure they're searched "in isolation" - meaning disease ontology searches shouldn't return gene ontology results. This is handled by the `source_type` and `source_id` fields:

```python
# Each source is isolated by source_type + source_id combination
await pipeline.search(
    source_type="ontology_disease",  # Only searches disease ontology
    source_id="all",                  # Within that, search all terms
    query="lung cancer"
)

# This will NEVER return disease terms:
await pipeline.search(
    source_type="ontology_gene",      # Completely separate namespace
    source_id="all",
    query="lung cancer"                # Even with same query!
)

# The PostgreSQL query enforces isolation:
WHERE source_type = $1 AND source_id = $2  # Hard filter
```

This ensures ontologies remain separate search spaces while sharing the same infrastructure.

### 3. CSV/Database Source (NEW)

```python
# backend/app/services/document_sources/csv_source.py

import pandas as pd

class CSVDocumentSource(DocumentSource):
    """Make CSV files searchable via RAG"""

    def __init__(self, csv_path: str, text_columns: List[str]):
        self.csv_path = csv_path
        self.text_columns = text_columns
        self.df = pd.read_csv(csv_path)

    async def get_chunks(self, source_id: str) -> List[DocumentChunk]:
        """Each row becomes a searchable chunk"""
        chunks = []

        for idx, row in self.df.iterrows():
            # Combine specified columns into searchable text
            text_parts = []
            for col in self.text_columns:
                if col in row and pd.notna(row[col]):
                    text_parts.append(f"{col}: {row[col]}")

            chunk_text = "\n".join(text_parts)

            chunks.append(DocumentChunk(
                chunk_id=f"row_{idx}",
                text=chunk_text,
                source_type="csv",
                source_id=source_id,
                metadata=row.to_dict()
            ))

        return chunks

    def get_source_type(self) -> str:
        return "csv"

    def format_citation(self, chunk: DocumentChunk) -> Dict[str, Any]:
        return {
            "type": "csv",
            "row": int(chunk.chunk_id.split('_')[1]),
            "data": chunk.metadata
        }
```

---

## Database Schema Updates

### Unified Storage for All Sources (PostgreSQL)

**Important**: Per Codex's feedback, we use our existing PostgreSQL RAG schema for everything, not a separate system.

```sql
-- migrations/create_unified_rag_tables.sql

-- Single table for ALL document chunks (extends existing pdf_chunks pattern)
CREATE TABLE unified_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Source identification
    source_type VARCHAR(50) NOT NULL,  -- 'pdf', 'ontology_disease', etc.
    source_id VARCHAR(255) NOT NULL,   -- PDF UUID, 'all', namespace, etc.
    chunk_id VARCHAR(255) NOT NULL,    -- Unique within source

    -- Content
    chunk_text TEXT NOT NULL,
    chunk_metadata JSONB,

    -- Embeddings for vector search
    embedding vector(768),

    -- Lexical search
    search_vector tsvector GENERATED ALWAYS AS (
        to_tsvector('english', chunk_text)
    ) STORED,

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Constraints
    UNIQUE(source_type, source_id, chunk_id)
);

-- Indices for fast search
CREATE INDEX idx_unified_embedding ON unified_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_unified_lexical ON unified_chunks
    USING GIN(search_vector);

CREATE INDEX idx_unified_source ON unified_chunks(source_type, source_id);

-- Update the hybrid search view/function
CREATE OR REPLACE FUNCTION unified_hybrid_search(
    p_source_type TEXT,
    p_source_id TEXT,
    p_query_embedding vector,
    p_query_text TEXT,
    p_vector_limit INT,
    p_lexical_limit INT
) RETURNS TABLE (
    chunk_id TEXT,
    chunk_text TEXT,
    chunk_metadata JSONB,
    vector_score FLOAT,
    lexical_score FLOAT,
    combined_score FLOAT
) AS $$
BEGIN
    RETURN QUERY
    WITH vector_search AS (
        SELECT
            uc.chunk_id,
            uc.chunk_text,
            uc.chunk_metadata,
            1 - (uc.embedding <=> p_query_embedding) AS score
        FROM unified_chunks uc
        WHERE
            uc.source_type = p_source_type
            AND uc.source_id = p_source_id
        ORDER BY uc.embedding <=> p_query_embedding
        LIMIT p_vector_limit
    ),
    lexical_search AS (
        SELECT
            uc.chunk_id,
            uc.chunk_text,
            uc.chunk_metadata,
            ts_rank(uc.search_vector, plainto_tsquery(p_query_text)) AS score
        FROM unified_chunks uc
        WHERE
            uc.source_type = p_source_type
            AND uc.source_id = p_source_id
            AND uc.search_vector @@ plainto_tsquery(p_query_text)
        ORDER BY score DESC
        LIMIT p_lexical_limit
    )
    SELECT
        COALESCE(v.chunk_id, l.chunk_id) AS chunk_id,
        COALESCE(v.chunk_text, l.chunk_text) AS chunk_text,
        COALESCE(v.chunk_metadata, l.chunk_metadata) AS chunk_metadata,
        COALESCE(v.score, 0) AS vector_score,
        COALESCE(l.score, 0) AS lexical_score,
        -- Reciprocal Rank Fusion
        (COALESCE(1.0 / (60 + RANK() OVER (ORDER BY v.score DESC NULLS LAST)), 0) +
         COALESCE(1.0 / (60 + RANK() OVER (ORDER BY l.score DESC NULLS LAST)), 0)) AS combined_score
    FROM vector_search v
    FULL OUTER JOIN lexical_search l ON v.chunk_id = l.chunk_id
    ORDER BY combined_score DESC;
END;
$$ LANGUAGE plpgsql;
```

---

## Integration with Multi-Agent System

### Fixed Field Access Issues

**Important**: Codex noted that `GeneralOrchestrator.prepare()` doesn't expose `eligible_chunks` or `embeddings`. Here's the corrected approach:

```python
# backend/app/agents/main_orchestrator.py

@dataclass
class PreparedRequest:
    """Extended to include fields needed by routing"""
    prompt: str
    deps: OrchestratorDeps
    citations: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    # NEW fields for multi-agent routing
    context: str  # The formatted context text
    chunk_texts: List[str]  # Raw chunk texts for analysis
    chunk_count: int  # Number of chunks retrieved

async def prepare(self, *, pdf_id: UUID, query: str) -> PreparedRequest:
    """Enhanced prepare that exposes needed fields"""
    pipeline_output = await self._pipeline.run(pdf_id=pdf_id, query=query)

    eligible_chunks = [
        chunk for chunk in pipeline_output.sorted_chunks[: self._config.top_k]
        if chunk.score >= self._config.confidence_threshold
    ]

    context = self._format_context(eligible_chunks)

    return PreparedRequest(
        prompt=self._build_prompt(query=query, chunks=eligible_chunks),
        deps=OrchestratorDeps(query=query, context=context),
        citations=[chunk.citation for chunk in eligible_chunks if chunk.citation],
        metadata={...},
        # NEW: Expose these for routing logic
        context=context,
        chunk_texts=[chunk.text for chunk in eligible_chunks],
        chunk_count=len(eligible_chunks)
    )
```

### Updated Disease Agent Using Unified Pipeline

```python
# backend/app/agents/disease_ontology_agent.py

from app.services.unified_pipeline import get_unified_pipeline
from app.services.document_sources import OntologyDocumentSource

class DiseaseOntologyAgent:
    """
    Disease specialist using the SAME RAG pipeline as PDFs!
    """

    def __init__(self):
        # Register disease ontology as a source
        self.pipeline = get_unified_pipeline()
        self.source = OntologyDocumentSource("disease")
        self.pipeline.register_source(self.source)

    async def lookup_diseases(
        self,
        question: str,
        document_context: str,
        detected_entities: List[str]
    ) -> Dict[str, Any]:
        """
        Search disease ontology using unified RAG pipeline
        """

        # Build enhanced query with context
        enhanced_query = f"""
        Question: {question}

        Entities found in document: {', '.join(detected_entities)}

        Document context: {document_context[:500]}
        """

        # Use the SAME pipeline as PDFs!
        results = await self.pipeline.search(
            source_type="ontology_disease",
            source_id="all",
            query=enhanced_query,
            context=document_context,
            config_overrides={
                "rerank_top_k": 5,  # Fewer results for ontologies
                "apply_mmr": False   # No diversity needed
            }
        )

        # Extract disease information
        diseases = []
        for chunk in results.chunks:
            diseases.append({
                "term_id": chunk.metadata['term_id'],
                "name": chunk.metadata['name'],
                "definition": chunk.metadata['definition'],
                "score": chunk.score,
                "synonyms": chunk.metadata.get('synonyms', [])
            })

        # Generate answer
        answer = self._format_answer(question, diseases)

        return {
            "answer": answer,
            "diseases": diseases,
            "citations": [chunk.citation for chunk in results.chunks]
        }

    def _format_answer(self, question: str, diseases: List[Dict]) -> str:
        """Format diseases into natural language answer"""
        if not diseases:
            return "No matching disease ontology terms found."

        if "identifier" in question.lower() or "doid" in question.lower():
            # User wants identifiers
            terms = [f"{d['name']} ({d['term_id']})" for d in diseases[:3]]
            return f"The disease ontology terms are: {', '.join(terms)}"
        else:
            # General disease information
            parts = []
            for d in diseases[:2]:
                parts.append(
                    f"{d['name']} ({d['term_id']}): {d['definition'][:200]}..."
                )
            return "\n\n".join(parts)
```

### Simplified Multi-Agent Supervisor

```python
# backend/app/orchestration/general_supervisor.py

async def disease_specialist_node(state: PDFQAState) -> Dict[str, Any]:
    """
    Disease specialist now uses unified pipeline!
    Much simpler than building custom search.
    """

    agent = DiseaseOntologyAgent()

    # Agent uses the SAME pipeline infrastructure
    result = await agent.lookup_diseases(
        question=state.question,
        document_context=state.retrieved_context,
        detected_entities=state.metadata.get('detected_entities', {}).get('diseases', [])
    )

    return {
        "specialist_results": {"disease_ontology": result},
        "citations": state.citations + result['citations'],
        "specialists_invoked": state.specialists_invoked + ["disease_ontology"]
    }
```

---

## Implementation Plan

### Phase 1: Core Refactoring (Week 1)

```python
# 1. Create abstract DocumentSource interface with ingestion/query separation
✓ backend/lib/pipelines/document_source.py

# 2. Refactor GeneralPipeline to UnifiedRAGPipeline
✓ backend/lib/pipelines/unified_pipeline.py

# 3. Extend existing PostgreSQL schema (not new tables)
✓ migrations/extend_unified_rag_tables.sql

# 4. Create PDF source adapter
✓ backend/app/services/document_sources/pdf_source.py

# 5. Add ingestion status tracking
✓ backend/app/models/ingestion_status.py
```

### Phase 2: Add Ontology Support (Week 2)

```python
# 1. Build ontology source adapter with PostgreSQL backend
✓ backend/app/services/document_sources/ontology_source.py

# 2. Create background ingestion job for disease ontology
✓ backend/app/jobs/ingest_ontology.py
✓ python -m app.jobs.ingest_ontology --type disease --source-id all

# 3. Wait for ingestion to complete (check status)
✓ python -m app.jobs.check_ingestion_status --type ontology_disease

# 4. Update disease agent to use unified pipeline
✓ backend/app/agents/disease_ontology_agent.py

# 5. Test with sample queries
✓ tests/test_unified_disease_search.py
```

### Phase 3: Expand Sources (Week 3)

```python
# Add more sources as needed:
- GeneOntologySource
- UniProtSource
- PubMedSource
- FlyBaseVocabularySource
```

### Phase 4: Performance Optimization (Week 4)

```python
# 1. Add caching layer
@lru_cache(maxsize=1000)
async def cached_search(source_type, source_id, query_hash):
    return await pipeline.search(...)

# 2. Optimize embeddings
- Batch embedding generation
- Use smaller models for ontologies

# 3. Index optimization
- Partial indices per source type
- Materialized views for common queries
```

---

## Testing Strategy

### Unit Tests

```python
# tests/unit/test_unified_pipeline.py

async def test_pipeline_handles_multiple_sources():
    """Pipeline should work with any DocumentSource"""
    pipeline = UnifiedRAGPipeline(...)

    # Register sources
    pipeline.register_source(PDFDocumentSource())
    pipeline.register_source(OntologyDocumentSource("disease"))

    # Search PDF
    pdf_results = await pipeline.search(
        source_type="pdf",
        source_id="uuid-here",
        query="BRCA1 mutations"
    )
    assert len(pdf_results.chunks) > 0

    # Search ontology with SAME pipeline
    onto_results = await pipeline.search(
        source_type="ontology_disease",
        source_id="all",
        query="lung cancer"
    )
    assert len(onto_results.chunks) > 0
    assert onto_results.chunks[0].metadata['term_id'].startswith('DOID:')


async def test_context_boosting():
    """Context should boost relevant results"""
    results_no_context = await pipeline.search(
        source_type="ontology_disease",
        source_id="all",
        query="cancer"
    )

    results_with_context = await pipeline.search(
        source_type="ontology_disease",
        source_id="all",
        query="cancer",
        context="The patient has lung adenocarcinoma"
    )

    # Lung cancer should rank higher with context
    lung_cancer_rank_no_context = _find_rank(results_no_context, "lung")
    lung_cancer_rank_with_context = _find_rank(results_with_context, "lung")

    assert lung_cancer_rank_with_context < lung_cancer_rank_no_context
```

### Integration Tests

```python
# tests/integration/test_disease_agent_unified.py

async def test_disease_agent_with_unified_pipeline():
    """Disease agent should use unified pipeline successfully"""

    # Setup
    agent = DiseaseOntologyAgent()

    # Search with context
    result = await agent.lookup_diseases(
        question="What are the disease ontology terms for the cancers mentioned?",
        document_context="The study focused on lung cancer and breast cancer patients",
        detected_entities=["lung cancer", "breast cancer"]
    )

    # Should find correct terms
    assert any("DOID:1324" in d['term_id'] for d in result['diseases'])  # lung cancer
    assert any("DOID:1612" in d['term_id'] for d in result['diseases'])  # breast cancer

    # Should use unified pipeline features
    assert result['citations']  # Has citations
    assert all(c['type'] == 'ontology' for c in result['citations'])
```

### Performance Tests

```python
# tests/performance/test_unified_performance.py

async def test_unified_pipeline_performance():
    """Unified pipeline should maintain good performance"""

    pipeline = get_unified_pipeline()

    # Time PDF search
    start = time.perf_counter()
    await pipeline.search(
        source_type="pdf",
        source_id="test-pdf",
        query="test query"
    )
    pdf_time = time.perf_counter() - start

    # Time ontology search
    start = time.perf_counter()
    await pipeline.search(
        source_type="ontology_disease",
        source_id="all",
        query="test query"
    )
    onto_time = time.perf_counter() - start

    # Both should be fast
    assert pdf_time < 0.2  # 200ms
    assert onto_time < 0.2  # 200ms
```

---

## Configuration

### Application Configuration

```python
# backend/app/config.py

# Unified pipeline configuration
UNIFIED_RAG_CONFIG = {
    # Default settings (work well for PDFs)
    "vector_top_k": 50,
    "lexical_top_k": 50,
    "max_results": 100,
    "rerank_top_k": 10,
    "mmr_lambda": 0.7,
    "apply_mmr": True,

    # Source-specific overrides
    "source_overrides": {
        "ontology_disease": {
            "vector_top_k": 20,   # Fewer candidates
            "rerank_top_k": 5,    # Top 5 diseases
            "apply_mmr": False    # No diversity needed
        },
        "ontology_gene": {
            "vector_top_k": 30,
            "rerank_top_k": 10,
            "apply_mmr": True     # Want diverse gene functions
        },
        "csv": {
            "lexical_top_k": 100,  # Rely more on exact matches
            "vector_top_k": 20
        }
    }
}

# Source registration
DOCUMENT_SOURCES = [
    ("pdf", "PDFDocumentSource"),
    ("ontology_disease", "OntologyDocumentSource:disease"),
    ("ontology_gene", "OntologyDocumentSource:gene"),
    ("csv_proteins", "CSVDocumentSource:/data/proteins.csv"),
]
```

### Docker Configuration

```yaml
# docker-compose.yml

services:
  backend:
    volumes:
      # Persistent cache for indexed ontologies
      - ontology_cache:/app/ontology_cache

      # Source data files
      - ./data/doid.obo:/app/data/disease.obo:ro
      - ./data/go.obo:/app/data/gene.obo:ro
      - ./data/proteins.csv:/app/data/proteins.csv:ro

volumes:
  ontology_cache:
    driver: local
```

---

## Benefits Summary

### Code Reduction

```python
# BEFORE: ~5000 lines
- 1000 lines: PDF pipeline
- 1000 lines: Disease search implementation
- 1000 lines: Gene search implementation
- 1000 lines: CSV search
- 1000 lines: Other sources

# AFTER: ~1500 lines
- 500 lines: Unified pipeline (handles everything!)
- 200 lines: PDF source adapter
- 200 lines: Ontology source adapter
- 200 lines: CSV source adapter
- 400 lines: Other adapters
```

### Quality Improvements

| Feature                 | Before       | After       |
| ----------------------- | ------------ | ----------- |
| Cross-encoder reranking | PDF only     | All sources |
| MMR diversification     | PDF only     | All sources |
| Hybrid search           | PDF only     | All sources |
| Context boosting        | None         | All sources |
| Embeddings              | Inconsistent | Consistent  |
| Caching                 | Per-source   | Unified     |

### Maintenance Benefits

1. **Single Point of Improvement**: Optimize once, benefit everywhere
2. **Consistent API**: Same search interface for all sources
3. **Shared Infrastructure**: One database schema, one index
4. **Easier Testing**: Test the pipeline once, not N times
5. **Better Monitoring**: One set of metrics for everything

---

## Migration Checklist

### Week 1

- [ ] Create DocumentSource interface
- [ ] Refactor GeneralPipeline to UnifiedRAGPipeline
- [ ] Update database schema
- [ ] Create PDFDocumentSource adapter
- [ ] Update existing PDF search to use unified pipeline
- [ ] Run regression tests

### Week 2

- [ ] Implement OntologyDocumentSource
- [ ] Index disease ontology
- [ ] Update DiseaseOntologyAgent
- [ ] Test disease search via unified pipeline
- [ ] Add integration tests

### Week 3

- [ ] Add GeneOntologySource
- [ ] Add CSVDocumentSource
- [ ] Update remaining agents
- [ ] Performance testing
- [ ] Documentation

### Week 4

- [ ] Deploy to staging
- [ ] Monitor performance metrics
- [ ] Gather feedback
- [ ] Final optimizations
- [ ] Production deployment

---

## Conclusion

By unifying our RAG pipeline to handle all document types, we:

1. **Eliminate code duplication** - One pipeline instead of many
2. **Ensure consistent quality** - All sources get advanced search features
3. **Simplify the multi-agent system** - Agents just call the unified pipeline
4. **Reduce maintenance burden** - Fix/improve in one place
5. **Enable rapid expansion** - New sources just need an adapter

The unified pipeline turns our sophisticated PDF search into a **universal search engine** for any structured or unstructured data source. This is the foundation that makes our multi-agent system actually feasible to build and maintain.

**Next Step**: Begin Phase 1 implementation by creating the DocumentSource interface and refactoring GeneralPipeline.

---

**Document Version**: 1.0.0
**Last Updated**: 2025-01-17
**Status**: Ready for Implementation
