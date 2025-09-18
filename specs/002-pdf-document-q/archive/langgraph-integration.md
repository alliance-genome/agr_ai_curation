# LangGraph Integration Architecture for Multi-Agent PDF Q&A

**Date**: 2025-01-16 | **Version**: 1.0.0 | **Status**: Proposal

## Executive Summary

This document outlines the integration of LangGraph with the existing PydanticAI-based PDF Q&A system to enable sophisticated multi-agent orchestration, autonomous agent workflows, and inter-agent communication. LangGraph will provide the graph-based orchestration layer while PydanticAI continues to handle structured agent outputs and validation.

## Why LangGraph?

### Current Architecture Limitations

1. **Linear Orchestration**: Current flow is `Query → Pipeline → LLM → Answer` with no branching or parallelism
2. **No Agent Communication**: Agents work in isolation without ability to share findings
3. **Static Routing**: No dynamic decision-making based on intermediate results
4. **Limited State Management**: Each request is stateless, no persistent context across agent interactions
5. **Missing Specialization**: Domain agents (Disease, Gene, Pathway) not yet implemented with proper coordination

### LangGraph Solutions

1. **Graph-Based Workflows**: Define complex agent interactions as directed acyclic graphs (DAGs)
2. **Stateful Execution**: Maintain conversation and decision state across agent interactions
3. **Parallel Processing**: Multiple agents can work simultaneously on different aspects
4. **Supervisor Patterns**: Orchestrator can dispatch tasks and collect results from multiple specialists
5. **Human-in-the-Loop**: Built-in support for curator review before critical decisions
6. **Time-Travel Debugging**: Debug complex multi-agent interactions with state replay

## Proposed Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                    LangGraph Supervisor                      │
│                  (Orchestration & Routing)                   │
└──────────┬────────────────────────────────────┬─────────────┘
           │                                    │
           ▼                                    ▼
    ┌──────────────┐                    ┌──────────────┐
    │ Intent Router│                    │State Manager │
    └──────┬───────┘                    └──────┬───────┘
           │                                    │
    ┌──────▼────────────────────────────────────▼──────┐
    │              LangGraph StateGraph                  │
    │                                                    │
    │  ┌─────────┐  ┌─────────┐  ┌─────────┐          │
    │  │Disease  │  │  Gene   │  │Pathway  │          │
    │  │  Node   │  │  Node   │  │  Node   │          │
    │  └────┬────┘  └────┬────┘  └────┬────┘          │
    │       │            │            │                 │
    │  ┌────▼────┐  ┌────▼────┐  ┌────▼────┐          │
    │  │PydanticAI│  │PydanticAI│  │PydanticAI│        │
    │  │  Agent  │  │  Agent  │  │  Agent  │          │
    │  └─────────┘  └─────────┘  └─────────┘          │
    └────────────────────────────────────────────────────┘
                           │
                    ┌──────▼──────┐
                    │   Pipeline   │
                    │   Services   │
                    └──────────────┘
```

### Integration Approach

**Phase 1: Wrap Current Orchestrator**

- Wrap existing `GeneralOrchestrator` in a LangGraph node
- Maintain backward compatibility with current API endpoints
- Add basic state management for conversation context

**Phase 2: Add Conditional Routing**

- Implement intent detection node
- Create conditional edges based on query type
- Route to specialized workflows dynamically

**Phase 3: Implement Specialized Agents**

- Create Disease, Gene, Pathway agents as graph nodes
- Each node wraps a PydanticAI agent for structured outputs
- Enable parallel execution for multi-domain queries

**Phase 4: Advanced Features**

- Add human-in-the-loop checkpoints
- Implement agent collaboration through shared state
- Enable autonomous sub-workflows

## Implementation Design

### 1. Core State Definition

```python
from typing import TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, MessagesState
from dataclasses import dataclass
from uuid import UUID

class RAGState(TypedDict):
    """Central state for the RAG workflow"""
    # Input
    query: str
    pdf_id: UUID
    session_id: UUID

    # Intent & Routing
    intent: Optional[str]  # "disease", "gene", "pathway", "general"
    confidence: float
    requires_specialists: List[str]

    # Retrieved Context
    chunks: List[Dict[str, Any]]
    vector_results: List[Dict[str, Any]]
    lexical_results: List[Dict[str, Any]]
    reranked_chunks: List[Dict[str, Any]]

    # Agent Outputs
    disease_findings: Optional[Dict[str, Any]]
    gene_findings: Optional[Dict[str, Any]]
    pathway_findings: Optional[Dict[str, Any]]
    general_answer: Optional[str]

    # Final Output
    final_answer: str
    citations: List[Dict[str, Any]]
    metadata: Dict[str, Any]

    # Workflow Control
    next_step: str
    error: Optional[str]
    requires_human_review: bool
```

### 2. Supervisor Agent with LangGraph

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint import MemorySaver
from pydantic_ai import Agent

class PDFQASupervisor:
    """Main supervisor that orchestrates the multi-agent workflow"""

    def __init__(self):
        # Initialize workflow graph
        self.workflow = StateGraph(RAGState)

        # Add nodes for each step
        self.workflow.add_node("analyze_intent", self.analyze_intent)
        self.workflow.add_node("retrieve_context", self.retrieve_context)
        self.workflow.add_node("disease_specialist", self.run_disease_agent)
        self.workflow.add_node("gene_specialist", self.run_gene_agent)
        self.workflow.add_node("pathway_specialist", self.run_pathway_agent)
        self.workflow.add_node("general_qa", self.run_general_qa)
        self.workflow.add_node("synthesize_answer", self.synthesize_answer)
        self.workflow.add_node("human_review", self.human_review)

        # Define edges
        self.workflow.set_entry_point("analyze_intent")

        # Conditional routing based on intent
        self.workflow.add_conditional_edges(
            "analyze_intent",
            self.route_by_intent,
            {
                "retrieve": "retrieve_context",
                "error": END
            }
        )

        # After retrieval, route to specialists
        self.workflow.add_conditional_edges(
            "retrieve_context",
            self.route_to_specialists,
            {
                "disease": "disease_specialist",
                "gene": "gene_specialist",
                "pathway": "pathway_specialist",
                "general": "general_qa",
                "parallel": ["disease_specialist", "gene_specialist", "pathway_specialist"]
            }
        )

        # All specialist nodes lead to synthesis
        self.workflow.add_edge("disease_specialist", "synthesize_answer")
        self.workflow.add_edge("gene_specialist", "synthesize_answer")
        self.workflow.add_edge("pathway_specialist", "synthesize_answer")
        self.workflow.add_edge("general_qa", "synthesize_answer")

        # Synthesis may require human review
        self.workflow.add_conditional_edges(
            "synthesize_answer",
            lambda x: "human_review" if x["requires_human_review"] else END
        )

        # Compile with memory for state persistence
        self.memory = MemorySaver()
        self.app = self.workflow.compile(checkpointer=self.memory)
```

### 3. Intent Analysis Node

```python
async def analyze_intent(self, state: RAGState) -> RAGState:
    """Analyzes user query to determine intent and routing"""

    # Use a lightweight PydanticAI agent for intent classification
    intent_agent = Agent(
        "gpt-4o-mini",
        output_type=IntentClassification,
        system_prompt="""Classify the biological query intent.
        Categories: disease, gene, pathway, chemical, general.
        Identify if multiple specialists are needed."""
    )

    result = await intent_agent.run(state["query"])

    state["intent"] = result.output.primary_intent
    state["confidence"] = result.output.confidence
    state["requires_specialists"] = result.output.required_specialists

    # Determine next step
    if result.output.confidence < 0.5:
        state["next_step"] = "general"
    elif len(result.output.required_specialists) > 1:
        state["next_step"] = "parallel"
    else:
        state["next_step"] = result.output.primary_intent

    return state
```

### 4. Parallel Specialist Execution

```python
async def route_to_specialists(self, state: RAGState) -> List[str]:
    """Routes to one or more specialist nodes based on intent"""

    if state["next_step"] == "parallel":
        # Return multiple nodes for parallel execution
        return state["requires_specialists"]
    else:
        # Single specialist
        return state["next_step"]

async def run_disease_agent(self, state: RAGState) -> RAGState:
    """Runs the disease specialist PydanticAI agent"""

    # Prepare filtered chunks for disease relevance
    disease_chunks = self._filter_chunks_for_domain(
        state["reranked_chunks"],
        domain="disease"
    )

    # Use existing PydanticAI disease agent
    disease_agent = Agent(
        "gpt-4o",
        output_type=DiseaseAnnotations,
        deps_type=DiseasePipelineOutput,
        system_prompt=DISEASE_SPECIALIST_PROMPT
    )

    # Create pipeline output
    pipeline_output = DiseasePipelineOutput(
        disease_chunks=disease_chunks,
        ontology_matches=await self._match_ontologies(disease_chunks, "disease"),
        disease_mentions=self._extract_mentions(disease_chunks, "disease")
    )

    # Run agent
    result = await disease_agent.run("", deps=pipeline_output)

    # Store in state
    state["disease_findings"] = result.output.dict()

    return state
```

### 5. Answer Synthesis with Collaboration

```python
async def synthesize_answer(self, state: RAGState) -> RAGState:
    """Synthesizes final answer from all specialist outputs"""

    # Collect all specialist findings
    findings = []
    if state.get("disease_findings"):
        findings.append(("disease", state["disease_findings"]))
    if state.get("gene_findings"):
        findings.append(("gene", state["gene_findings"]))
    if state.get("pathway_findings"):
        findings.append(("pathway", state["pathway_findings"]))
    if state.get("general_answer"):
        findings.append(("general", state["general_answer"]))

    # Use synthesis agent to combine findings
    synthesis_agent = Agent(
        "gpt-4o",
        output_type=SynthesizedAnswer,
        system_prompt="""You are a scientific synthesis expert.
        Combine findings from multiple specialist agents into a coherent answer.
        Ensure all claims are supported by the provided evidence.
        Highlight any contradictions or uncertainties."""
    )

    synthesis_input = {
        "query": state["query"],
        "findings": findings,
        "chunks": state["reranked_chunks"][:5]  # Top chunks for context
    }

    result = await synthesis_agent.run(str(synthesis_input))

    state["final_answer"] = result.output.answer
    state["citations"] = result.output.citations
    state["metadata"] = {
        "specialists_used": [f[0] for f in findings],
        "confidence": result.output.confidence,
        "processing_time": result.output.processing_time
    }

    # Check if human review needed
    state["requires_human_review"] = (
        result.output.confidence < 0.7 or
        result.output.has_contradictions or
        "NEEDS_REVIEW" in result.output.flags
    )

    return state
```

### 6. Human-in-the-Loop Checkpoint

```python
async def human_review(self, state: RAGState) -> RAGState:
    """Checkpoint for human curator review"""

    # Create review request
    review_request = {
        "session_id": state["session_id"],
        "query": state["query"],
        "draft_answer": state["final_answer"],
        "specialist_findings": {
            "disease": state.get("disease_findings"),
            "gene": state.get("gene_findings"),
            "pathway": state.get("pathway_findings")
        },
        "confidence": state["metadata"]["confidence"],
        "reason_for_review": self._get_review_reason(state)
    }

    # Wait for human input (would integrate with UI)
    human_feedback = await self.wait_for_human_input(review_request)

    if human_feedback.approved:
        state["final_answer"] = human_feedback.edited_answer or state["final_answer"]
        state["metadata"]["human_reviewed"] = True
    else:
        # Route back to specialists with feedback
        state["next_step"] = "refine"
        state["human_feedback"] = human_feedback.feedback

    return state
```

## Integration with Existing System

### 1. Modify RAG Endpoints

```python
# backend/app/routers/rag_endpoints.py
from app.services.langgraph_supervisor import PDFQASupervisor

@router.post("/sessions/{session_id}/question", response_model=QuestionResponse)
async def ask_question(
    session_id: UUID,
    request: QuestionRequest,
    db: Session = Depends(get_db),
) -> QuestionResponse:
    """Enhanced endpoint using LangGraph supervisor"""

    # Get session
    session_obj = db.get(ChatSession, session_id)
    if session_obj is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Initialize supervisor
    supervisor = PDFQASupervisor()

    # Prepare initial state
    initial_state = {
        "query": request.question,
        "pdf_id": session_obj.pdf_id,
        "session_id": session_id,
        "intent": None,
        "requires_specialists": [],
        "chunks": [],
        "final_answer": "",
        "citations": [],
        "metadata": {},
        "requires_human_review": False
    }

    # Run workflow with thread ID for persistence
    thread = {"configurable": {"thread_id": str(session_id)}}
    final_state = await supervisor.app.ainvoke(initial_state, thread)

    # Store messages in database
    _store_messages(db, session_obj, request.question, final_state)

    return QuestionResponse(
        answer=final_state["final_answer"],
        citations=final_state["citations"],
        metadata=final_state["metadata"]
    )
```

### 2. Streaming Support with LangGraph

```python
@router.post("/sessions/{session_id}/question/stream")
async def ask_question_stream(
    session_id: UUID,
    request: QuestionRequest,
    db: Session = Depends(get_db),
):
    """Streaming endpoint with LangGraph state updates"""

    async def generate():
        supervisor = PDFQASupervisor()

        # Stream state updates as they happen
        async for state_update in supervisor.app.astream(initial_state, thread):
            # Send intermediate results to client
            if "intent" in state_update:
                yield f"data: {json.dumps({'type': 'intent', 'intent': state_update['intent']})}\n\n"

            if "chunks" in state_update and state_update["chunks"]:
                yield f"data: {json.dumps({'type': 'retrieval', 'count': len(state_update['chunks'])})}\n\n"

            if "disease_findings" in state_update:
                yield f"data: {json.dumps({'type': 'specialist', 'specialist': 'disease', 'status': 'complete'})}\n\n"

            if "final_answer" in state_update:
                # Stream the answer token by token if available
                for token in state_update["final_answer"].split():
                    yield f"data: {json.dumps({'type': 'answer', 'token': token + ' '})}\n\n"
                    await asyncio.sleep(0.01)  # Small delay for streaming effect

    return StreamingResponse(generate(), media_type="text/event-stream")
```

## Benefits of This Integration

### 1. Autonomous Agent Workflows

- Agents can "run off" to perform research independently
- Parallel execution of domain specialists
- Agents can spawn sub-tasks dynamically

### 2. Inter-Agent Communication

- Shared state allows agents to see each other's findings
- Synthesis agent can identify contradictions
- Agents can request clarification from each other

### 3. Advanced Orchestration

- Dynamic routing based on confidence and intent
- Fallback strategies when specialists fail
- Progressive enhancement of answers

### 4. Debugging & Observability

- LangGraph's built-in visualization tools
- Time-travel debugging to replay agent decisions
- State checkpointing for error recovery

### 5. Human-in-the-Loop

- Curators can review low-confidence answers
- Ability to provide feedback and request refinement
- Audit trail of human interventions

## Implementation Roadmap

### Phase 1: Foundation (Week 1)

- [ ] Install LangGraph: `pip install langgraph`
- [ ] Create `RAGState` TypedDict
- [ ] Wrap existing `GeneralOrchestrator` in a LangGraph node
- [ ] Implement basic StateGraph with linear flow
- [ ] Test backward compatibility

### Phase 2: Intent Routing (Week 2)

- [ ] Implement intent analysis node
- [ ] Add conditional routing logic
- [ ] Create placeholder specialist nodes
- [ ] Test multi-path execution

### Phase 3: Specialist Agents (Week 3-4)

- [ ] Implement Disease specialist node
- [ ] Implement Gene specialist node
- [ ] Implement Pathway specialist node
- [ ] Enable parallel execution
- [ ] Add synthesis node

### Phase 4: Advanced Features (Week 5)

- [ ] Add human-in-the-loop checkpoints
- [ ] Implement state persistence
- [ ] Add streaming support
- [ ] Create debugging tools

### Phase 5: Production (Week 6)

- [ ] Performance optimization
- [ ] Add monitoring and metrics
- [ ] Create operational dashboards
- [ ] Documentation and training

## Code Examples

### Example 1: Simple Query Flow

```python
# User asks: "What mutations in BRCA1 are mentioned?"

# State progression:
state = {
    "query": "What mutations in BRCA1 are mentioned?",
    "intent": "gene",  # After intent analysis
    "requires_specialists": ["gene"],
    "chunks": [...],  # After retrieval
    "gene_findings": {
        "genes": [{"name": "BRCA1", "mutations": ["C61G", "185delAG"]}],
        "confidence": 0.95
    },
    "final_answer": "The paper mentions two BRCA1 mutations: C61G and 185delAG..."
}
```

### Example 2: Multi-Domain Query

```python
# User asks: "How does the BRCA1 mutation affect cancer pathways?"

# Parallel execution of multiple specialists:
state = {
    "query": "How does the BRCA1 mutation affect cancer pathways?",
    "intent": "complex",
    "requires_specialists": ["gene", "pathway", "disease"],
    # All three specialists run in parallel
    "gene_findings": {...},
    "pathway_findings": {...},
    "disease_findings": {...},
    # Synthesis combines all findings
    "final_answer": "BRCA1 mutations disrupt DNA repair pathways..."
}
```

### Example 3: Low Confidence with Human Review

```python
# Complex query with uncertain answer

state = {
    "query": "Is there evidence of novel BRCA1 interactions?",
    "confidence": 0.45,  # Low confidence
    "requires_human_review": True,
    "draft_answer": "Possible novel interaction suggested but uncertain...",
    # Waits for curator input
    "human_feedback": {
        "approved": False,
        "feedback": "Check supplementary data for interaction details"
    },
    # Routes back for refinement
    "refined_answer": "In supplementary table S3, a novel interaction..."
}
```

## Testing Strategy

### Unit Tests

```python
# tests/unit/test_langgraph_nodes.py
async def test_intent_analysis_node():
    """Test intent classification"""
    state = {"query": "What genes cause this disease?"}
    updated_state = await analyze_intent(state)
    assert updated_state["intent"] in ["gene", "disease", "complex"]
    assert updated_state["confidence"] > 0

async def test_parallel_specialist_routing():
    """Test parallel execution routing"""
    state = {
        "next_step": "parallel",
        "requires_specialists": ["gene", "disease"]
    }
    routes = await route_to_specialists(state)
    assert len(routes) == 2
    assert "gene" in routes
```

### Integration Tests

```python
# tests/integration/test_langgraph_workflow.py
async def test_full_workflow_simple_query():
    """Test complete workflow for simple query"""
    supervisor = PDFQASupervisor()
    initial_state = {
        "query": "What is the main conclusion?",
        "pdf_id": test_pdf_id
    }

    final_state = await supervisor.app.ainvoke(initial_state)

    assert final_state["final_answer"] != ""
    assert len(final_state["citations"]) > 0
    assert final_state["metadata"]["specialists_used"] == ["general"]

async def test_parallel_specialist_execution():
    """Test parallel execution of multiple specialists"""
    supervisor = PDFQASupervisor()
    initial_state = {
        "query": "How do BRCA1 mutations affect disease pathways?",
        "pdf_id": test_pdf_id
    }

    final_state = await supervisor.app.ainvoke(initial_state)

    assert "gene" in final_state["metadata"]["specialists_used"]
    assert "disease" in final_state["metadata"]["specialists_used"]
    assert "pathway" in final_state["metadata"]["specialists_used"]
```

## Monitoring & Observability

### LangGraph-Specific Metrics

```python
# Track workflow execution
workflow_execution_time = Histogram(
    "langgraph_workflow_duration_seconds",
    "Time to complete full workflow",
    ["workflow_type", "num_specialists"]
)

# Track node execution
node_execution_time = Histogram(
    "langgraph_node_duration_seconds",
    "Time spent in each node",
    ["node_name", "specialist_type"]
)

# Track routing decisions
routing_decisions = Counter(
    "langgraph_routing_total",
    "Routing decisions made",
    ["from_node", "to_node", "reason"]
)

# Track human interventions
human_review_required = Counter(
    "langgraph_human_review_total",
    "Times human review was required",
    ["reason", "outcome"]
)
```

### Debugging Without External Services

```python
import json
from pathlib import Path

# Enable debug mode for development (writes verbose logs locally)
app = workflow.compile(
    checkpointer=memory,
    debug=True,  # Enables detailed logging to stdout/logger
    interrupt_before=["human_review"],  # Set breakpoints for local inspection
)

# Capture state transitions for replay without external services
state = await app.ainvoke(initial_state, debug=True)
trace_path = Path(".langgraph_traces/last_run.json")
trace_path.parent.mkdir(parents=True, exist_ok=True)
trace_path.write_text(json.dumps(state, indent=2))

# Replay locally for time-travel debugging
replay = app.get_state(at="intent_router")
print(replay)
```

## Conclusion

Integrating LangGraph with the existing PydanticAI system provides:

1. **Sophisticated Orchestration**: Move from linear to graph-based workflows
2. **Autonomous Agents**: Specialists can work independently and in parallel
3. **Better Collaboration**: Agents can share findings through state
4. **Production Features**: Human-in-the-loop, debugging, state persistence
5. **Backward Compatibility**: Preserve existing PydanticAI investments

The phased implementation approach ensures minimal disruption while progressively adding advanced capabilities. The combination of LangGraph's orchestration with PydanticAI's structured outputs creates a powerful, maintainable system for complex multi-agent PDF Q&A.

---

**Next Steps**:

1. Review and approve this integration plan
2. Set up development environment with LangGraph
3. Begin Phase 1 implementation
4. Create proof-of-concept for stakeholder review
