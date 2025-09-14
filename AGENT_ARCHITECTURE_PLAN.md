# AGENT ARCHITECTURE PLAN

## 🚀 IMPLEMENTATION PROGRESS

### ✅ Completed (as of 2025-01-14)

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

### 🔄 In Progress

#### Phase 2: Entity Tool Integration

- [ ] Add `extract_entities` tool to main BioCurationAgent
- [ ] Implement automatic tool invocation logic
- [ ] Stream entity events during processing

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

## Solution: Hybrid Agent Architecture

### Core Design Principles

1. **Main agent returns plain text** (`output_type=str`) for streaming capability
2. **Specialized sub-agents** handle structured data extraction via tools
3. **Stream text immediately** while structured data processes in background
4. **Tool events** provide real-time updates about extraction progress

## Agent Hierarchy

### 1. Main Conversational Agent

- **Type**: `Agent[BioCurationDependencies, str]`
- **Output**: Plain text (streamable)
- **Purpose**: Handle all user conversations with streaming responses
- **System Prompt**: General biocuration assistant instructions

### 2. Entity Extraction Tool (Sub-Agent)

- **Type**: `Agent[None, EntityExtractionOutput]`
- **Output**: Structured list of biological entities
- **Called By**: Main agent via `@agent.tool`
- **When Used**: Automatically when entities mentioned or explicitly requested

### 3. Document Form Filler Tool (Sub-Agent)

- **Type**: `Agent[None, DocumentFields]`
- **Output**: Structured form data
- **Called By**: Main agent via `@agent.tool`
- **When Used**: When user requests form population or structured output

### 4. Annotation Suggester Tool (Sub-Agent)

- **Type**: `Agent[None, List[AnnotationSuggestion]]`
- **Output**: List of annotation suggestions
- **Called By**: Main agent via `@agent.tool`
- **When Used**: When analyzing documents for highlights

## Streaming Event Flow

```
User Message
    ↓
Main Agent Processes
    ↓
Stream Text Response (immediate)
    ↓
Tool Calls (if needed)
    ├── Entity Extraction (async)
    ├── Form Filling (async)
    └── Annotation Suggestions (async)
    ↓
Stream Tool Results as Updates
    ↓
Complete Response
```

## Event Types for Frontend

### Streaming Updates (`StreamingUpdate`)

```python
{
    "type": "text_delta",     # Incremental text
    "content": "Looking at..."
}

{
    "type": "tool_call",       # Tool being invoked
    "content": "extract_entities",
    "metadata": {"status": "started"}
}

{
    "type": "entity",          # Entity found
    "content": "BRCA1",
    "metadata": {
        "type": "gene",
        "database_id": "672",
        "confidence": 0.95
    }
}

{
    "type": "form_field",      # Form field populated
    "content": "gene_name",
    "metadata": {
        "value": "BRCA1",
        "field_type": "string"
    }
}

{
    "type": "annotation",      # Annotation suggestion
    "content": "important finding",
    "metadata": {
        "color": "yellow",
        "start": 100,
        "end": 150
    }
}

{
    "type": "complete",        # Stream finished
    "metadata": {
        "entities_count": 5,
        "annotations_count": 3
    }
}
```

## Use Cases

### Case 1: Simple Question

**User**: "What is gene X in this paper?"

**Response Flow**:

1. Stream text: "Looking at the paper, gene X refers to BRCA1, which is..."
2. Tool call: extract_entities("BRCA1...")
3. Entity update: {type: "entity", content: "BRCA1", metadata: {...}}

### Case 2: Document Population

**User**: "Please fill in the gene curation form"

**Response Flow**:

1. Stream text: "I'll analyze the paper and populate the form. Starting with gene identification..."
2. Tool call: fill_document_fields(paper_text, form_schema)
3. Form updates: Multiple {type: "form_field"} events
4. Stream text: "I've populated 12 fields. The main gene is..."

### Case 3: Complex Analysis

**User**: "Analyze this paper for all genes and their interactions"

**Response Flow**:

1. Stream text: "I'll perform a comprehensive analysis..."
2. Multiple tool calls in parallel:
   - extract_entities(full_text)
   - find_interactions(full_text)
   - suggest_annotations(full_text)
3. Stream updates as each tool completes
4. Stream summary text: "Found 15 genes with 8 documented interactions..."

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
