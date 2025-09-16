# Implementation Plan: PDF Document Q&A Chat Interface with RAG

**Branch**: `002-pdf-document-q` | **Date**: 2025-01-14 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-pdf-document-q/spec.md`

## Execution Flow (/plan command scope)

```
1. Load feature spec from Input path
   → If not found: ERROR "No feature spec at {path}"
2. Fill Technical Context (scan for NEEDS CLARIFICATION)
   → Detect Project Type from context (web=frontend+backend, mobile=app+api)
   → Set Structure Decision based on project type
3. Evaluate Constitution Check section below
   → If violations exist: Document in Complexity Tracking
   → If no justification possible: ERROR "Simplify approach first"
   → Update Progress Tracking: Initial Constitution Check
4. Execute Phase 0 → research.md
   → If NEEDS CLARIFICATION remain: ERROR "Resolve unknowns"
5. Execute Phase 1 → contracts, data-model.md, quickstart.md, agent-specific template file (e.g., `CLAUDE.md` for Claude Code, `.github/copilot-instructions.md` for GitHub Copilot, or `GEMINI.md` for Gemini CLI).
6. Re-evaluate Constitution Check section
   → If new violations: Refactor design, return to Phase 1
   → Update Progress Tracking: Post-Design Constitution Check
7. Plan Phase 2 → Describe task generation approach (DO NOT create tasks.md)
8. STOP - Ready for /tasks command
```

**IMPORTANT**: The /plan command STOPS at step 7. Phases 2-4 are executed by other commands:

- Phase 2: /tasks command creates tasks.md
- Phase 3-4: Implementation execution (manual or via tools)

## Summary

Implement a LangGraph-orchestrated multi-agent PDF Q&A system with specialized domain experts. A LangGraph supervisor coordinates PydanticAI agents, maintaining state across nodes and enabling conditional routing. The supervisor analyzes intent and streams conversational text while dispatching specialized pipelines that prepare data, then passes to domain-specific sub-agents for synthesis. Clean separation: LangGraph Supervisor → Pipelines (data prep) → PydanticAI Specialists (expert synthesis).

## Technical Context

**Language/Version**: Python 3.11+ (backend), TypeScript/React 18 (frontend)
**Primary Dependencies**: FastAPI, LangGraph, PydanticAI, pgvector, Unstructured.io, OpenAI SDK, React + MUI
**Storage**: PostgreSQL with pgvector (HNSW) + tsvector for hybrid search, Postgres job queue, local filesystem
**Database Strategy**: Fresh start - no migrations needed, recreate schema from SQLAlchemy models
**Testing**: Pytest (backend), Vitest (frontend), performance benchmarks
**Target Platform**: Docker containerized web application
**Project Type**: web (frontend + backend structure)
**Performance Goals**: <100ms vector search, <50ms lexical search, <200ms reranking, <2s full pipeline
**Constraints**: 12+ concurrent users, 100MB file limit, 500 page limit, confidence thresholds
**Scale/Scope**: 12 users, 100 PDFs/day, 10K queries/day, 90-day retention

## Agent Architecture

### Multi-Agent System Design

#### LangGraph Supervisor Graph

- Top-level orchestrator built with `langgraph.graph.StateGraph`
- Nodes wrap PydanticAI agents and tool functions
- Checkpointer persists graph state per question (Postgres-backed)
- Supports conditional routing + future parallel execution

```python
from langgraph.graph import StateGraph, START, END

workflow = StateGraph(PDFQAState)
workflow.add_node("intent_router", analyze_intent)
workflow.add_node("general_answer", run_general_agent)

workflow.add_edge(START, "intent_router")
workflow.add_conditional_edges(
    "intent_router",
    decide_route,
    {
        "general": "general_answer",
        "specialist_fanout": "specialist_router",
    },
)

workflow.add_edge("general_answer", END)
general_supervisor = workflow.compile(checkpointer=postgres_checkpointer)
```

**Main Orchestrator Agent**:

- `Agent[None, str]` - Streams conversational text only
- Invoked via LangGraph `general_answer` node
- System prompt: Conversational assistant that coordinates specialists and narrates LangGraph progress
- Accesses graph state through injected dependencies for context + citations

**Specialized Domain Agents**:

**Disease Annotation Agent**:

- `Agent[DiseasePipelineOutput, DiseaseAnnotations]`
- Expert in diseases, conditions, phenotypes
- Receives pre-filtered disease data from pipeline
- Triggered by LangGraph node `disease_specialist`
- System prompt: Disease annotation specialist

**Gene/Protein Agent**:

- `Agent[GenePipelineOutput, GeneAnnotations]`
- Expert in genes, proteins, mutations
- Receives pre-filtered gene data from pipeline
- Triggered by LangGraph node `gene_specialist`
- System prompt: Molecular biology specialist

**Pathway Agent**:

- `Agent[PathwayPipelineOutput, PathwayAnnotations]`
- Expert in biological pathways and interactions
- Receives pre-filtered pathway data from pipeline
- Triggered by LangGraph node `pathway_specialist`
- System prompt: Systems biology specialist

**Chemical/Drug Agent**:

- `Agent[ChemicalPipelineOutput, ChemicalAnnotations]`
- Expert in compounds, drugs, treatments
- Receives pre-filtered chemical data from pipeline
- Triggered by LangGraph node `chemical_specialist`
- System prompt: Pharmacology specialist

### Pipeline Architecture

```python
# Example: Disease Pipeline Flow (LangGraph node wrapping PydanticAI agent)
@workflow.node("disease_pipeline")
async def find_disease_annotations(state: PDFQAState) -> str:
    # 1. Run disease pipeline (no LLM)
    pipeline_output = await disease_pipeline.run(
        document_id=state.document_id,
        query=state.query
    )

    # 2. Dispatch to disease specialist agent
    result = await disease_agent.run(
        "",
        deps=pipeline_output
    )

    # 3. Return formatted string for streaming
    return format_disease_results(result.output)

class DiseasePipeline:
    async def run(self, document_id: str, query: str) -> DiseasePipelineOutput:
        # Search with disease-specific strategy
        chunks = await hybrid_search.search(
            query=query,
            boost_terms=["disease", "condition", "syndrome"],
            ontology="disease"
        )

        # Filter and match to ontologies
        filtered = await disease_filter.filter(chunks)
        matched = await ontology_service.match(filtered, ["DO", "HPO"])

        return DiseasePipelineOutput(
            chunks=matched,
            ontology_matches=matched.ontology_ids
        )
```

## Constitution Check

_GATE: Must pass before Phase 0 research. Re-check after Phase 1 design._

**VII. Simplicity & YAGNI**:

- Projects: 3 (backend, frontend, tests) ✓
- Using framework directly? Yes - PydanticAI, FastAPI, React directly ✓
- Single data model? Yes - no unnecessary DTOs ✓
- Avoiding patterns? Yes - no Repository/UoW ✓
- React component nesting <3 levels? Yes - will enforce ✓
- Documenting complexity justification? Yes - pgvector needed for RAG ✓

**I. Library-First Architecture**:

- EVERY feature as library? Yes ✓
- Libraries listed:
  - pdf-processor: Element-based extraction with Unstructured.io
  - chunk-manager: Semantic chunking with element type preservation
  - embedding-service: Multi-model embeddings with versioning
  - hybrid-search: Vector (HNSW) + lexical (tsvector) search
  - reranker: Cross-encoder reranking with MMR diversification
  - query-expander: Ontology-aware synonym expansion
  - rag-orchestrator: PydanticAI agents for full pipeline
  - job-queue: Postgres-based async job processing
- Self-contained with clear interfaces? Yes ✓
- > 80% test coverage planned? Yes ✓
- Documentation includes API reference? Yes ✓

**II. CLI Interface Requirement**:

- CLI per library: Yes ✓
  - pdf-processor: --extract, --method, --ocr, --tables, --help, --format=json/text
  - chunk-manager: --chunk, --size, --overlap, --preserve-layout, --help
  - embedding-service: --embed, --model, --batch, --version, --help
  - hybrid-search: --query, --vector-k, --lexical-k, --help
  - reranker: --rerank, --top-k, --mmr, --lambda, --help
  - query-expander: --expand, --max, --sources, --help
  - rag-orchestrator: --question, --pdf-id, --confidence, --help
  - job-queue: --status, --cancel, --retry, --help
- Text in/out protocol? Yes (stdin → stdout, errors → stderr) ✓
- JSON + human-readable formats? Yes ✓
- Testable via CLI before GUI? Yes ✓

**III. Test-First Development (NON-NEGOTIABLE)**:

- RED-GREEN-Refactor cycle enforced? Yes - tests first ✓
- Git commits show tests before implementation? Yes ✓
- Frontend: Vitest for components, integration for flows? Yes ✓
- Backend: Pytest with fixtures for FastAPI/SQLAlchemy? Yes ✓
- FORBIDDEN: Implementation before test, skipping RED phase ✓

**IV. Integration Testing Priority**:

- AI service integrations tested? Yes - OpenAI embeddings/chat ✓
- Database transactions/migrations tested? Yes - pgvector ✓
- PDF processing/annotation persistence? Yes ✓
- WebSocket/SSE communication? N/A - using standard HTTP ✓
- Docker compose interactions? Yes ✓

**V. Observability & Monitoring**:

- Structured logging with correlation IDs? Yes ✓
- Health check endpoints? Yes (/health, /readiness) ✓
- Performance metrics for AI/DB calls? Yes ✓
- Error tracking with stack traces? Yes ✓
- Debug mode toggles? Yes ✓

**VI. Versioning & Breaking Changes**:

- Version number assigned? 0.1.0 (initial release) ✓
- Breaking changes have migration scripts? N/A (first version) ✓
- Database changes via Alembic only? Yes ✓
- API versioning strategy? /v1/ prefix ✓
- Frontend/Backend compatibility tracked? Yes ✓

**VIII. Tech Stack Integration First**:

- Using existing stack? Yes (React+MUI, FastAPI, PostgreSQL, Docker) ✓
- New tech justified and approved? PydanticAI and pgvector for RAG requirements ✓
- Compatibility with Python 3.11+, Node 20+? Yes ✓
- Security audit passed? Will run before implementation ✓

## Project Structure

### Documentation (this feature)

```
specs/[###-feature]/
├── plan.md              # This file (/plan command output)
├── research.md          # Phase 0 output (/plan command)
├── data-model.md        # Phase 1 output (/plan command)
├── quickstart.md        # Phase 1 output (/plan command)
├── contracts/           # Phase 1 output (/plan command)
└── tasks.md             # Phase 2 output (/tasks command - NOT created by /plan)
```

### Source Code (repository root)

```
# Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure]
```

**Structure Decision**: Option 2 - Web application (frontend + backend detected)

## Phase 0: Outline & Research

1. **Extract unknowns from Technical Context** above:
   - For each NEEDS CLARIFICATION → research task
   - For each dependency → best practices task
   - For each integration → patterns task

2. **Generate and dispatch research agents**:

   ```
   For each unknown in Technical Context:
     Task: "Research {unknown} for {feature context}"
   For each technology choice:
     Task: "Find best practices for {tech} in {domain}"
   ```

3. **Consolidate findings** in `research.md` using format:
   - Decision: [what was chosen]
   - Rationale: [why chosen]
   - Alternatives considered: [what else evaluated]

**Output**: research.md with all NEEDS CLARIFICATION resolved

## Phase 1: Design & Contracts

_Prerequisites: research.md complete_

1. **Extract entities from feature spec** → `data-model.md`:
   - Entity name, fields, relationships
   - Validation rules from requirements
   - State transitions if applicable

2. **Generate API contracts** from functional requirements:
   - For each user action → endpoint
   - Use standard REST/GraphQL patterns
   - Output OpenAPI/GraphQL schema to `/contracts/`

3. **Generate contract tests** from contracts:
   - One test file per endpoint
   - Assert request/response schemas
   - Tests must fail (no implementation yet)

4. **Extract test scenarios** from user stories:
   - Each story → integration test scenario
   - Quickstart test = story validation steps

5. **Update agent file incrementally** (O(1) operation):
   - Run `/scripts/bash/update-agent-context.sh claude` for your AI assistant
   - If exists: Add only NEW tech from current plan
   - Preserve manual additions between markers
   - Update recent changes (keep last 3)
   - Keep under 150 lines for token efficiency
   - Output to repository root

**Output**: data-model.md, /contracts/\*, failing tests, quickstart.md, agent-specific file

## Phase 2: Task Planning Approach

_This section describes what the /tasks command will do - DO NOT execute during /plan_

**PRIORITIZED Task Generation Strategy (Based on "Do Now" List)**:

The /tasks command will generate approximately 50-55 prioritized tasks following TDD principles:

### Priority 1: Core Infrastructure (Tasks 1-15) - Week 1

**Must complete first for everything else to work**

1. **Database Setup** (Tasks 1-5):
   - Install pgvector with HNSW support
   - Create enhanced schema with all entities
   - Setup lexical index (tsvector) for hybrid search
   - Configure LISTEN/NOTIFY for job queue
   - Add configurable embedding dimensions

2. **PDF Processing Foundation** (Tasks 6-10):
   - pdf-processor library with Unstructured.io element extraction
   - Element-aware chunking with type preservation (Title, NarrativeText, Table, etc.)
   - Automatic table/figure extraction with coordinates
   - Content normalization and deduplication
   - Page-level hashing

3. **Job Queue System** (Tasks 11-15):
   - Postgres-based queue implementation
   - Worker pool with rate limiting
   - Progress tracking and retry logic
   - LISTEN/NOTIFY integration
   - Job monitoring CLI

### Priority 2: Hybrid Search & Reranking (Tasks 16-30) - Week 1-2

**Critical for quality - implement immediately after infrastructure**

4. **Embedding Service** (Tasks 16-20):
   - Multi-model support with versioning
   - Batch processing with backpressure
   - Dynamic dimension validation
   - Embedding job creation
   - Status tracking

5. **Hybrid Search Implementation** (Tasks 21-25):
   - HNSW vector search (<100ms target)
   - Lexical search with ts_rank (<50ms target)
   - Query expansion with ontology
   - Result merging logic
   - Search metrics collection

6. **Reranker with MMR** (Tasks 26-30):
   - Cross-encoder scoring (PydanticAI agent)
   - MMR diversification (λ=0.7)
   - Confidence scoring
   - Source attribution
   - Performance optimization

### Priority 3: Multi-Agent System (Tasks 31-45) - Week 2

**Specialized agents and pipelines**

7. **Specialized Domain Agents** (Tasks 31-36):
   - Disease Annotation Agent with expertise
   - Gene/Protein Agent with molecular knowledge
   - Pathway Agent for interactions
   - Chemical/Drug Agent for compounds
   - Each with domain-specific prompts

8. **Data Preparation Pipelines** (Tasks 37-40):
   - Disease Pipeline with ontology matching
   - Gene Pipeline with database cross-refs
   - Domain-specific search strategies
   - Pre-filtering and relevance scoring
   - No LLM costs during preparation

9. **LangGraph Orchestrator Integration** (Tasks 41-45 & 61-65):
   - Define LangGraph state + checkpointer
   - Wrap general orchestrator in `StateGraph`
   - Intent detection and routing through graph edges
   - SSE-friendly runner adapter for streaming
   - Persist run/node telemetry + replay tooling
   - Full pipeline integration tests using `app.ainvoke`

### Priority 4: API & Frontend (Tasks 41-50) - Week 2-3

**User-facing components**

9. **FastAPI Endpoints** (Tasks 41-45):
   - Upload with deduplication
   - Hybrid search endpoint
   - RAG question endpoint
   - Job status endpoint
   - Metrics endpoint

10. **React Components** (Tasks 46-50):
    - Upload with progress
    - Chat with confidence indicator
    - Citation display with bbox
    - Search breakdown view
    - Settings management

### Priority 5: Polish & Monitoring (Tasks 51-55) - Week 3

**Production readiness**

11. **Observability** (Tasks 51-55):
    - Structured logging with correlation IDs
    - Metrics dashboard
    - Cost tracking
    - Performance monitoring
    - Health checks

**Task Naming Convention**:

```
[###]. [Priority-Component]: [Action] - [TDD Phase]
Example: "001. P1-Database: Write HNSW index tests - RED"
Example: "016. P2-HybridSearch: Implement vector retrieval - GREEN"
Example: "031. P3-PydanticAI: Create reranking agent - Implementation"
```

**Critical Path Dependencies**:

```
Database Setup → Embedding Service → Hybrid Search → Reranker → RAG Pipeline → LangGraph Supervisor
                ↘ Job Queue → PDF Processing ↗
```

**Parallel Execution Opportunities**:

- P1: Database setup || PDF processor || Job queue
- P2: Vector search || Lexical search || Query expansion
- P3: PydanticAI agents || LangGraph scaffolding (state + nodes)
- P4: API endpoints || Frontend components

**Risk Mitigation**:

- Start with lexical-only search if vector search delays
- Simple confidence threshold before complex scoring
- Manual reranking before automated cross-encoder
- Progress UI before job queue if needed
- Fallback to direct PydanticAI execution if LangGraph supervisor fails (feature flag)

**Success Metrics for Each Priority**:

- P1: Tables created, PDFs extractable, jobs queueable
- P2: Hybrid search <200ms, reranking working
- P3: Confidence scoring prevents bad answers
- P4: End-to-end flow works
- P5: All metrics visible
- LangGraph: Supervisor graph persists state, exposes telemetry, passes replay tests

**Estimated Output**: 65 tasks with clear priorities and dependencies

**IMPORTANT**: This phase is executed by the /tasks command, NOT by /plan

## Phase 3+: Future Implementation

_These phases are beyond the scope of the /plan command_

**Phase 3**: Task execution (/tasks command creates tasks.md)  
**Phase 4**: Implementation (execute tasks.md following constitutional principles)  
**Phase 5**: Validation (run tests, execute quickstart.md, performance validation)

## Complexity Tracking

_Fill ONLY if Constitution Check has violations that must be justified_

| Violation                | Why Needed                             | Simpler Alternative Rejected Because   |
| ------------------------ | -------------------------------------- | -------------------------------------- |
| 8 libraries instead of 4 | Separation of concerns for complex RAG | Monolithic library would be untestable |
| pgvector + tsvector      | Hybrid search required for accuracy    | Vector-only misses exact matches       |
| Unstructured.io          | Element-based extraction needed        | Plain text loses structure and tables  |
| Cross-encoder reranking  | Quality requirement                    | Raw scores give poor results           |

## Progress Tracking

_This checklist is updated during execution flow_

**Phase Status**:

- [x] Phase 0: Research complete with all enhancements (/plan command)
- [x] Phase 1: Design complete with enhanced schema (/plan command)
- [x] Phase 2: Task planning complete with priorities (/plan command - describe approach only)
- [ ] Phase 3: Tasks generated (/tasks command)
- [ ] Phase 4: Implementation complete
- [ ] Phase 5: Validation passed

**Gate Status**:

- [x] Initial Constitution Check: PASS
- [x] Post-Design Constitution Check: PASS (with justified complexity)
- [x] All NEEDS CLARIFICATION resolved
- [x] Complexity deviations documented and justified

---

_Based on Constitution v1.0.0 - See `/memory/constitution.md`_
