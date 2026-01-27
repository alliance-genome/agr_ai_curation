# Trace Review - Comprehensive Specification

**Version**: 1.0
**Created**: 2025-11-18
**Status**: Planning Phase

---

## Executive Summary

The Trace Review is a Docker-based, web-accessible developer tool for comprehensive analysis of Langfuse traces from AI curation sessions. It provides a beautiful, single-page MUI interface where developers can paste trace IDs and instantly view multi-dimensional analysis across organized tabs/panels. All trace data is stored as flat JSON files in mounted volumes, with no database overhead.

**Key Features**:
- Single trace ID input → comprehensive multi-view analysis
- AWS Cognito authentication with dev bypass mode
- MUI/React/Vite frontend matching main curation site theme
- Docker-based with mounted volume for JSON storage
- Exhaustive Python analysis engine based on existing extraction script
- Token usage/cost tracking and visualization with Recharts
- Left sidebar navigation for maximum content space
- Developer-focused but production-quality UI
- Initial release: 4 core views (Summary, Conversation, Tool Calls, Supervisor Routing)

---

## Technology Stack

### Frontend
- **Framework**: React 18 + Vite
- **UI Library**: Material-UI (MUI v5)
- **Charts**: Recharts (for token usage/cost visualization)
- **State Management**: React Context API / Zustand
- **HTTP Client**: Axios
- **JSON Viewer**: react-json-view (read-only, collapsible)
- **Theme**: Match existing curation site color palette

### Backend
- **Framework**: FastAPI (Python 3.11+)
- **Analysis Engine**: Enhanced version of `extract_langfuse_traces_v3.py`
- **Langfuse SDK**: Official Python client
- **Data Storage**: In-memory cache (Python dict) - no persistent storage in Phase 1

### Authentication
- **Production**: AWS Cognito (same setup as main curation app)
- **Development**: Bypass mode via environment variable `DEV_MODE=true`

### Infrastructure
- **Containerization**: Docker Compose
- **No Volume Mounts**: All data cached in-memory (no persistent storage)

### Environment Variables
```bash
# Langfuse credentials
LANGFUSE_PUBLIC_KEY=pk-xxx
LANGFUSE_SECRET_KEY=sk-xxx
LANGFUSE_HOST=http://localhost:3000

# AWS Cognito (production)
COGNITO_USER_POOL_ID=us-east-1_xxx
COGNITO_CLIENT_ID=xxx
COGNITO_REGION=us-east-1

# Dev mode bypass
DEV_MODE=false
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Trace Review UI                         │
│                   (React + MUI + Vite)                      │
│                                                             │
│  ┌────────────┐  ┌──────────────────────────────────────┐  │
│  │            │  │                                      │  │
│  │  Left Nav  │  │         Main Content Area           │  │
│  │  Sidebar   │  │    (Selected View Rendered)          │  │
│  │            │  │                                      │  │
│  │  Phase 1:  │  │  Phase 1 Views:                      │  │
│  │  --------  │  │  - Summary                           │  │
│  │  Summary   │  │  - Conversation                      │  │
│  │  Convo     │  │  - Tool Calls                        │  │
│  │  Tools     │  │  - Supervisor Routing                │  │
│  │  Routing   │  │                                      │  │
│  │            │  │  Future Views:                       │  │
│  │  Future:   │  │  - LLM Calls & Costs                 │  │
│  │  --------  │  │  - SQL Queries                       │  │
│  │  LLM Calls │  │  - Observations                      │  │
│  │  SQL       │  │  - Performance Metrics               │  │
│  │  Obs       │  │  - Raw JSON Viewer                   │  │
│  │  Perf      │  │                                      │  │
│  │  Raw JSON  │  │                                      │  │
│  └────────────┘  └──────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            ↓ API Calls
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Backend                          │
│                                                             │
│  POST /api/traces/analyze                                   │
│  GET  /api/traces/{trace_id}                                │
│  GET  /api/traces/{trace_id}/views/{view_name}             │
│  POST /api/auth/verify-cognito (production)                │
│  POST /api/auth/dev-bypass (dev mode)                      │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         Enhanced Trace Analysis Engine               │  │
│  │  (Based on extract_langfuse_traces_v3.py)            │  │
│  │                                                       │  │
│  │  - TraceExtractor                                     │  │
│  │  - ConversationAnalyzer                               │  │
│  │  - ToolCallAnalyzer                                   │  │
│  │  - SupervisorRoutingAnalyzer                          │  │
│  │  - CostCalculator                                     │  │
│  │  - PerformanceAnalyzer                                │  │
│  │  - SQLQueryAnalyzer                                   │  │
│  │  - ObservationAnalyzer                                │  │
│  └──────────────────────────────────────────────────────┘  │
│                            ↓                                │
│                     Langfuse SDK                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
                   Langfuse Server
```

---

## Data Storage Architecture

**Phase 1: In-Memory Cache** (Simplified)

```python
# Backend storage: Python dictionary
trace_cache = {
    "fdeac438c90617c73497d298490b6db1": {
        "raw_trace": {...},           # Complete Langfuse trace
        "observations": [...],         # All observations
        "scores": [...],              # All scores
        "analysis": {                 # Pre-computed views
            "summary": {...},
            "conversation": {...},
            "tool_calls": {...},
            "supervisor_routing": {...}
        },
        "cached_at": "2025-11-18T14:30:00Z",
        "expires_at": "2025-11-18T15:30:00Z"  # 1 hour TTL
    }
}
```

**How It Works**:
1. User pastes trace ID
2. Backend checks cache → hit or miss
3. **Cache Miss**: Fetch from Langfuse, analyze, store in memory
4. **Cache Hit**: Return cached data instantly
5. All views read from same cached trace (fast switching)
6. Cache expires after 1 hour or server restart

**Benefits**:
- ✅ Simple: No file system management
- ✅ Fast: Sub-second view switching after initial load
- ✅ Fresh: Always fetches latest data on first load
- ✅ Lightweight: No disk I/O overhead
- ✅ Stateless: Clean slate on restart

**Tradeoffs Accepted**:
- ❌ No offline mode (requires Langfuse running)
- ❌ No trace history (Phase 2 feature if needed)
- ❌ Cache lost on server restart (acceptable for dev tool)

**Future Enhancement** (Phase 2+):
- Optional persistent storage for important traces
- Trace history sidebar
- Export/download functionality

---

## Frontend UI Design

### Color Theme
Match the existing curation site:
- Primary: `#1976d2` (blue)
- Secondary: `#dc004e` (pink/red)
- Background: `#f5f5f5` (light gray)
- Paper: `#ffffff`
- Text: `#212121` / `#757575`

### Layout Design

**Left Navigation Sidebar** (Selected)
```
┌─────────┬────────────────────────────────────────────┐
│ Summary │                                            │
│ Convo   │         Main Content Area                  │
│ Tools   │      (Selected View Rendered Here)         │
│ Routing │                                            │
│         │                                            │
│ [Future]│                                            │
│ LLM     │                                            │
│ SQL     │                                            │
│ Obs     │                                            │
│ Perf    │                                            │
│ Raw JSON│                                            │
└─────────┴────────────────────────────────────────────┘
```

**Benefits**:
- Maximum horizontal space for content
- Easy scanning of available views
- Room for future view expansion
- Persistent navigation context

### Main Header
```
┌─────────────────────────────────────────────────────────┐
│  🔍 Trace Review              [Dev Mode] [Logout]    │
│                                                         │
│  Trace ID: [paste trace ID here____________________] ⬅  │
│                                                         │
│  Status: ✅ Loaded | ⏳ Processing | ❌ Error           │
└─────────────────────────────────────────────────────────┘
```

### View Definitions

**Phase 1 Views** (Initial Release):
1. Summary
2. Conversation
3. Tool Calls
4. Supervisor Routing

**Future Views** (Phase 2+):
5. LLM Calls & Costs
6. SQL Queries
7. Observations
8. Performance Metrics
9. Raw JSON Viewer

---

#### 1. **Summary View** (Default Landing) ✅ Phase 1
- Trace ID (full + short)
- Extraction timestamp
- Trace name
- Duration (ms)
- Total cost (USD)
- Total tokens (input/output/total)
- Observation count
- Score count
- Quick stats card layout (Material-UI Cards)

#### 2. **Conversation View** ✅ Phase 1
- User Query section (blockquote style)
- Assistant Response section (formatted markdown)
- Trace metadata (session ID, timestamp)
- Copy buttons for each section

#### 3. **Tool Calls View** ✅ Phase 1
- Chronological list of all tool calls
- Each call shows:
  - Timestamp
  - Thought/Reasoning (extracted from input)
  - Tool name
  - HTTP method + URL
  - Status + Status code
  - Collapsible raw input/output
- Search/filter by tool name or URL pattern

#### 4. **Supervisor Routing View** ✅ Phase 1
- Routing decision visualization
- Reasoning text (large, readable)
- Routing plan:
  - needs_pdf (badge)
  - ontologies_needed (chips)
  - genes_to_lookup (chips)
  - execution_order (numbered list)
- Metadata: destination, confidence, query_type
- Immediate response (if present)

#### 5. **LLM Calls & Costs View** 🔮 Future (Phase 2)
- Table of all LLM generations:
  - Model name
  - Input tokens
  - Output tokens
  - Cost (USD)
  - Observation ID
- Total cost summary (top banner)
- Cost breakdown by model (pie chart)
- Token usage over time (if multiple calls)

#### 6. **SQL Queries View** 🔮 Future (Phase 2)
- List of all detected SQL queries
- Syntax highlighting (Monaco Editor or similar)
- Query type badges (SELECT, INSERT, UPDATE)
- Observation context (which observation ran this)
- Collapsible result data

#### 7. **Observations View** 🔮 Future (Phase 2)
- Grouped by type (GENERATION, TOOL, AGENT, CHAIN, SPAN)
- Collapsible tree structure
- Each observation shows:
  - ID
  - Name
  - Type
  - Start time
  - Duration
  - Input/Output (collapsible)

#### 8. **Performance Metrics View** 🔮 Future (Phase 2)
- Duration histogram (if multiple observations)
- Latency breakdown by observation type
- Token usage over time
- Cost accumulation chart
- Bottleneck identification (longest observations)

#### 9. **Raw JSON Viewer** 🔮 Future (Phase 2)
- Read-only JSON viewer (react-json-view)
- Collapsible sections (metadata, trace, observations, scores)
- Search within JSON (built-in to react-json-view)
- Copy entire JSON or sections
- Download as file
- **Note**: No editing capability needed, just visualization

---

## API Endpoints

### Authentication Endpoints

#### POST `/api/auth/verify-cognito`
Verify AWS Cognito token (production mode).

**Request**:
```json
{
  "id_token": "eyJraWQiOiJ..."
}
```

**Response**:
```json
{
  "status": "authenticated",
  "user": {
    "email": "developer@example.com",
    "name": "Developer Name"
  }
}
```

#### POST `/api/auth/dev-bypass`
Bypass authentication in dev mode.

**Request**:
```json
{
  "dev_key": "dev"
}
```

**Response**:
```json
{
  "status": "authenticated",
  "user": {
    "email": "dev@localhost",
    "name": "Dev User"
  }
}
```

**Note**: Only works when `DEV_MODE=true` in environment.

---

### Trace Analysis Endpoints

#### POST `/api/traces/analyze`
Submit a trace ID for analysis. Backend checks cache, fetches from Langfuse if needed, runs analyzers, stores in memory.

**Request**:
```json
{
  "trace_id": "fdeac438c90617c73497d298490b6db1"
}
```

**Response** (Cache Miss - Fresh Analysis):
```json
{
  "status": "success",
  "trace_id": "fdeac438c90617c73497d298490b6db1",
  "trace_id_short": "fdeac438",
  "message": "Trace analyzed successfully",
  "cache_status": "miss",
  "available_views": [
    "summary",
    "conversation",
    "tool_calls",
    "supervisor_routing"
  ]
}
```

**Response** (Cache Hit - Instant):
```json
{
  "status": "success",
  "trace_id": "fdeac438c90617c73497d298490b6db1",
  "trace_id_short": "fdeac438",
  "message": "Trace loaded from cache",
  "cache_status": "hit",
  "cached_at": "2025-11-18T14:30:00Z",
  "available_views": ["summary", "conversation", "tool_calls", "supervisor_routing"]
}
```

**Error Response**:
```json
{
  "status": "error",
  "error_code": "TRACE_NOT_FOUND",
  "message": "Trace fdeac438... not found in Langfuse"
}
```

**Performance**:
- Cache hit: < 50ms (instant)
- Cache miss: < 2s (Langfuse fetch + analysis)

---

#### GET `/api/traces/{trace_id}/views/{view_name}`
Get specific view data for a trace. Always reads from in-memory cache (must call `/analyze` first).

**View Names** (Phase 1): `summary`, `conversation`, `tool_calls`, `supervisor_routing`

**Example**: `/api/traces/fdeac438/views/tool_calls`

**Response**:
```json
{
  "view": "tool_calls",
  "trace_id": "fdeac438c90617c73497d298490b6db1",
  "cached_at": "2025-11-18T14:30:00Z",
  "data": {
    "total_count": 14,
    "tool_calls": [
      {
        "time": "2025-10-10T00:24:57.732000+00:00",
        "id": "482ed9574bfd2831",
        "name": "rest_api_call._use",
        "url": "https://www.ebi.ac.uk/chebi/backend/api/public/es_search/?term=cytidine",
        "method": "GET",
        "thought": "Looking up cytidine in ChEBI database",
        "status": "ok",
        "status_code": 200
      },
      ...
    ]
  }
}
```

**Error Response** (Not in Cache):
```json
{
  "status": "error",
  "error_code": "TRACE_NOT_CACHED",
  "message": "Trace not found in cache. Call /api/traces/analyze first."
}
```

**Performance**: < 50ms (reads from memory)

---

## Enhanced Analysis Engine

### Base Class: `LangfuseTraceExtractor` (from existing script)
Keep the existing extraction logic but enhance with new specialized analyzers.

### New Analyzer Classes

#### `ConversationAnalyzer`
**Purpose**: Extract clean user input and assistant response pairs.

**Methods**:
- `extract_conversation(trace, observations) -> ConversationData`

**Output Structure**:
```json
{
  "user_input": "What is the gene ent-1?",
  "assistant_response": "ENT-1 is a C. elegans gene...",
  "trace_id": "fdeac438...",
  "trace_name": "chat-flow-4e2706ab",
  "session_id": "3232a193-990c-4369...",
  "timestamp": "2025-10-10T00:24:57.732000+00:00"
}
```

---

#### `ToolCallAnalyzer`
**Purpose**: Extract all tool calls with reasoning, URLs, methods, status.

**Methods**:
- `extract_tool_calls(observations) -> List[ToolCall]`
- `group_by_tool_name() -> Dict[str, List[ToolCall]]`
- `filter_by_status(status: str) -> List[ToolCall]`

**Output Structure**:
```json
{
  "total_count": 14,
  "unique_tools": ["rest_api_call._use"],
  "tool_calls": [
    {
      "time": "2025-10-10T00:24:57.732000+00:00",
      "id": "482ed9574bfd2831",
      "name": "rest_api_call._use",
      "url": "https://www.ebi.ac.uk/chebi/...",
      "method": "GET",
      "thought": "Looking up cytidine in ChEBI",
      "status": "ok",
      "status_code": 200,
      "input": {...},
      "output": {...}
    },
    ...
  ],
  "by_tool_name": {
    "rest_api_call._use": [...]
  },
  "by_status": {
    "ok": [...],
    "error": [...]
  }
}
```

---

#### `SupervisorRoutingAnalyzer`
**Purpose**: Extract and visualize supervisor routing decisions.

**Methods**:
- `find_supervisor_observation(observations) -> Optional[Observation]`
- `extract_routing_decision(observation) -> RoutingDecision`

**Output Structure**:
```json
{
  "found": true,
  "reasoning": "The query asks about a gene (ent-1)...",
  "routing_plan": {
    "needs_pdf": false,
    "ontologies_needed": ["WormBase"],
    "genes_to_lookup": ["ent-1"],
    "execution_order": ["gene_lookup", "ontology_mapping"]
  },
  "metadata": {
    "destination": "gene_specialist",
    "confidence": "high",
    "query_type": "gene_lookup"
  },
  "immediate_response": null
}
```

---

#### `CostCalculator`
**Purpose**: Calculate detailed token usage and costs by model.

**Methods**:
- `calculate_total_cost(observations) -> float`
- `breakdown_by_model() -> Dict[str, ModelCost]`
- `calculate_token_usage() -> TokenUsage`

**Output Structure**:
```json
{
  "total_cost_usd": 0.00030435,
  "total_input_tokens": 1257,
  "total_output_tokens": 193,
  "total_tokens": 1450,
  "by_model": {
    "gpt-4o-mini": {
      "calls": 4,
      "input_tokens": 1257,
      "output_tokens": 193,
      "cost_usd": 0.00030435
    }
  },
  "cost_breakdown": [
    {
      "observation_id": "482ed9574bfd2831",
      "model": "gpt-4o-mini",
      "cost_usd": 0.00007608
    },
    ...
  ]
}
```

---

#### `PerformanceAnalyzer`
**Purpose**: Analyze timing, latency, bottlenecks.

**Methods**:
- `calculate_latency_breakdown(observations) -> LatencyBreakdown`
- `identify_bottlenecks(threshold_ms: float) -> List[Bottleneck]`
- `create_timeline() -> Timeline`

**Output Structure**:
```json
{
  "total_duration_ms": 17.578,
  "observation_count": 9,
  "latency_by_type": {
    "GENERATION": 12.345,
    "TOOL": 3.456,
    "AGENT": 1.234,
    "CHAIN": 0.543
  },
  "bottlenecks": [
    {
      "observation_id": "abc123",
      "observation_name": "gene_lookup_crew",
      "duration_ms": 8.5,
      "percentage_of_total": 48.3
    }
  ],
  "timeline": [
    {
      "observation_id": "abc123",
      "start_time": "2025-10-10T00:24:57.732000+00:00",
      "end_time": "2025-10-10T00:24:58.232000+00:00",
      "duration_ms": 500,
      "type": "GENERATION"
    },
    ...
  ]
}
```

---

#### `SQLQueryAnalyzer`
**Purpose**: Extract and categorize SQL queries.

**Methods**:
- `extract_sql_queries(observations) -> List[SQLQuery]`
- `categorize_by_type() -> Dict[str, List[SQLQuery]]`

**Output Structure**:
```json
{
  "total_count": 3,
  "by_type": {
    "SELECT": 2,
    "INSERT": 1
  },
  "queries": [
    {
      "observation_id": "abc123",
      "observation_name": "completion",
      "query_type": "SELECT",
      "query_text": "SELECT parent.term_id FROM ontology_terms...",
      "output": {...}
    },
    ...
  ]
}
```

---

#### `ObservationAnalyzer`
**Purpose**: Organize observations into hierarchical structure.

**Methods**:
- `group_by_type(observations) -> Dict[str, List[Observation]]`
- `build_hierarchy(observations) -> ObservationTree`

**Output Structure**:
```json
{
  "total_count": 9,
  "by_type": {
    "GENERATION": 4,
    "TOOL": 1,
    "AGENT": 1,
    "CHAIN": 2,
    "SPAN": 1
  },
  "hierarchy": [
    {
      "id": "root",
      "type": "CHAIN",
      "name": "supervisor_chain",
      "children": [
        {
          "id": "child1",
          "type": "GENERATION",
          "name": "routing_decision",
          "children": []
        },
        ...
      ]
    }
  ],
  "flat_list": [...]
}
```

---

## Frontend Components

### Component Tree
```
App.tsx
├── AuthProvider.tsx                 # AWS Cognito auth wrapper
│   └── CognitoAuthGuard.tsx         # Auth guard component
│
├── Layout/
│   ├── AppHeader.tsx                # Top header with title + auth status
│   ├── LeftNavigation.tsx           # Left sidebar with view links
│   └── MainContent.tsx              # Main content area
│
├── TraceInput/
│   ├── TraceInputBox.tsx            # Text input for trace ID
│   └── TraceLoadingStatus.tsx       # Loading/error/success indicator
│
├── Views/
│   ├── SummaryView.tsx              # Summary cards
│   ├── ConversationView.tsx         # User query + response
│   ├── ToolCallsView.tsx            # Tool calls list
│   ├── SupervisorRoutingView.tsx   # Routing decision viz
│   ├── LLMCallsView.tsx             # LLM costs table
│   ├── SQLQueriesView.tsx           # SQL queries list
│   ├── ObservationsView.tsx         # Observations tree
│   ├── PerformanceView.tsx          # Performance charts
│   └── RawJSONView.tsx              # Monaco editor
│
└── Shared/
    ├── CopyButton.tsx               # Copy to clipboard button
    ├── ExpandableSection.tsx        # Collapsible section
    ├── CodeBlock.tsx                # Syntax highlighted code
    └── StatCard.tsx                 # Summary stat card
```

---

### Key Frontend Features

#### Auto-Loading on Paste
When user pastes a trace ID:
1. Frontend validates format (32-char hex or similar)
2. Immediately calls `POST /api/traces/analyze`
3. Shows loading spinner
4. On success, loads Summary view and enables all tabs
5. On error, shows error message with retry button

#### View Caching
- After loading a view once, cache the JSON response
- Only re-fetch if user clicks "Refresh" button
- Improves navigation speed

#### Keyboard Shortcuts
- `Ctrl+V` / `Cmd+V` in trace input → auto-submit
- `Ctrl+K` / `Cmd+K` → focus trace input
- Number keys `1-9` → quick navigate to views
- `Ctrl+C` / `Cmd+C` over code blocks → copy

#### Copy Buttons
Every section should have a copy button:
- Copy full conversation
- Copy tool calls (formatted)
- Copy supervisor routing (formatted)
- Copy raw JSON sections

#### Dark Mode (Optional Future Enhancement)
MUI provides easy dark mode toggle - consider adding this for developer comfort.

---

## Docker Configuration

### Dockerfile (Frontend)
```dockerfile
# Build stage
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### Dockerfile (Backend)
```dockerfile
FROM python:3.11-slim
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose.yml
```yaml
version: '3.8'

services:
  frontend:
    build: ./frontend
    ports:
      - "3001:80"
    environment:
      - VITE_API_URL=http://localhost:8001
      - VITE_COGNITO_USER_POOL_ID=${COGNITO_USER_POOL_ID}
      - VITE_COGNITO_CLIENT_ID=${COGNITO_CLIENT_ID}
      - VITE_COGNITO_REGION=${COGNITO_REGION}
      - VITE_DEV_MODE=${DEV_MODE:-false}
    depends_on:
      - backend

  backend:
    build: ./backend
    ports:
      - "8001:8000"
    environment:
      - LANGFUSE_PUBLIC_KEY=${LANGFUSE_PUBLIC_KEY}
      - LANGFUSE_SECRET_KEY=${LANGFUSE_SECRET_KEY}
      - LANGFUSE_HOST=${LANGFUSE_HOST}
      - COGNITO_USER_POOL_ID=${COGNITO_USER_POOL_ID}
      - COGNITO_CLIENT_ID=${COGNITO_CLIENT_ID}
      - COGNITO_REGION=${COGNITO_REGION}
      - DEV_MODE=${DEV_MODE:-false}
      - CACHE_TTL_HOURS=${CACHE_TTL_HOURS:-1}
```

### .env.example
```bash
# Langfuse Configuration
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_HOST=http://localhost:3000

# AWS Cognito Configuration (Production)
COGNITO_USER_POOL_ID=us-east-1_xxx
COGNITO_CLIENT_ID=xxx
COGNITO_REGION=us-east-1

# Development Mode (bypass authentication)
DEV_MODE=true

# Cache Configuration
CACHE_TTL_HOURS=1

# Port Configuration
FRONTEND_PORT=3001
BACKEND_PORT=8001
```

---

## Authentication Implementation

### Production Mode (AWS Cognito)

#### Frontend Flow:
1. User lands on `/login`
2. Clicks "Login with AWS Cognito"
3. Redirected to Cognito hosted UI
4. After successful login, Cognito redirects back with `id_token`
5. Frontend stores token in localStorage
6. All API calls include `Authorization: Bearer <id_token>` header

#### Backend Flow:
1. Backend receives request with `Authorization` header
2. Extracts `id_token`
3. Validates token with AWS Cognito:
   - Uses `cognitojwt` library
   - Verifies signature, expiration, issuer
4. If valid, allows request
5. If invalid, returns 401 Unauthorized

**Backend Dependency**: `pip install cognitojwt`

**Validation Code**:
```python
from cognitojwt import CognitoJWTException
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer()

async def verify_cognito_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        # Validate token
        verified_claims = cognitojwt.verify(
            token,
            COGNITO_REGION,
            COGNITO_USER_POOL_ID,
            app_client_id=COGNITO_CLIENT_ID
        )
        return verified_claims
    except CognitoJWTException as e:
        raise HTTPException(status_code=401, detail="Invalid token")
```

---

### Dev Mode (Bypass Authentication)

**Environment Variable**: `DEV_MODE=true`

#### Frontend Flow:
1. User lands on `/login`
2. Sees "Dev Mode" banner
3. Clicks "Continue as Dev User" button
4. Frontend sends request to `/api/auth/dev-bypass`
5. Backend returns mock auth token
6. Frontend proceeds as authenticated

#### Backend Flow:
1. Check `DEV_MODE` environment variable
2. If `true`, skip Cognito validation
3. Return mock user data
4. Log warning about dev mode usage

**Security Note**: Dev mode should NEVER be enabled in production. Add explicit checks and warnings.

---

## Additional Features

### 1. Token Usage Visualization (Phase 2+)
**Chart Library**: Recharts (selected for Phase 2)

**Chart Types**:
- **Pie Chart**: Cost breakdown by model
- **Bar Chart**: Token usage by observation
- **Line Chart**: Token accumulation over time (if multiple observations)

**Note**: Charts will be added in Phase 2 with LLM Calls & Costs view

### 2. Search/Filter in Tool Calls
**Implementation**: Use MUI TextField with debounced search

**Filters**:
- By tool name
- By HTTP method
- By status (success/error)
- By URL pattern (regex)

### 3. Export Functionality (Phase 2+)
**Future Enhancement**: Export/download capabilities

**Planned Options**:
- Export current view as JSON (download)
- Export entire trace as JSON file
- Share trace via URL (with cached data)

**Note**: Not included in Phase 1 for simplicity

### 4. Trace History (Optional Future Enhancement)
**Feature**: Left sidebar shows recently analyzed traces

**Implementation**:
- Backend maintains `index.json` with all analyzed traces
- Frontend fetches list on load
- Click trace in history to load it
- Pagination for large lists

### 5. Comparison Mode (Optional Future Enhancement)
**Feature**: Load two traces side-by-side for comparison

**Use Case**: Compare behavior of same query with different routing decisions

---

## Development Roadmap

### Phase 1: Foundation (Week 1)
- [ ] Setup Docker Compose configuration
- [ ] Create basic FastAPI backend structure
- [ ] Implement Langfuse connection and basic extraction
- [ ] Create React + Vite + MUI frontend boilerplate
- [ ] Implement dev mode authentication bypass
- [ ] Create basic trace input component

### Phase 2: Core Analysis Engine (Week 1-2)
- [ ] Migrate `LangfuseTraceExtractor` from existing script
- [ ] Implement `ConversationAnalyzer`
- [ ] Implement `ToolCallAnalyzer`
- [ ] Implement `SupervisorRoutingAnalyzer`
- [ ] Implement in-memory cache with TTL (1 hour expiry)
- [ ] Create cache manager (hit/miss logic)

### Phase 3: API Endpoints (Week 2)
- [ ] POST `/api/traces/analyze`
- [ ] GET `/api/traces/{trace_id}`
- [ ] GET `/api/traces/{trace_id}/views/{view_name}`
- [ ] POST `/api/auth/verify-cognito`
- [ ] POST `/api/auth/dev-bypass`
- [ ] Error handling and validation

### Phase 4: Frontend Views - Phase 1 (Week 2-3)
**Priority Views** (Initial Release):
- [ ] Summary View (cards with stats)
- [ ] Conversation View (formatted Q&A)
- [ ] Tool Calls View (chronological list)
- [ ] Supervisor Routing View (routing decision viz)

**Future Views** (Phase 2+):
- [ ] LLM Calls & Costs View (table + Recharts)
- [ ] SQL Queries View (syntax highlighted)
- [ ] Observations View (tree structure)
- [ ] Performance View (charts)
- [ ] Raw JSON View (react-json-view, read-only)

### Phase 5: Polish & Features - Phase 1 (Week 3-4)
- [ ] Left navigation sidebar (confirmed)
- [ ] Copy buttons for all sections
- [ ] Search/filter in tool calls
- [ ] Loading states and error handling
- [ ] Responsive design
- [ ] Keyboard shortcuts
- [ ] Cache status indicator (hit/miss, cached_at timestamp)
- [ ] Clean, professional styling matching curation site

### Phase 6: Production Features (Week 4)
- [ ] AWS Cognito integration (production auth)
- [ ] Environment-based configuration
- [ ] Deployment documentation
- [ ] Security hardening
- [ ] Performance optimization
- [ ] Testing (unit + integration)

### Phase 7: Additional Views (Phase 2)
- [ ] LLM Calls & Costs view with Recharts
- [ ] SQL Queries view
- [ ] Observations tree view
- [ ] Performance metrics view
- [ ] Raw JSON viewer (react-json-view)

### Phase 8: Optional Enhancements (Future)
- [ ] Trace history sidebar
- [ ] Comparison mode (side-by-side)
- [ ] Dark mode toggle
- [ ] Advanced filtering/search
- [ ] Auto-refresh for long-running traces
- [ ] Collaborative features (share trace links)

---

## Testing Strategy

### Backend Tests
- **Unit Tests**: Each analyzer class independently
- **Integration Tests**: Full trace extraction pipeline
- **API Tests**: All endpoints with various inputs
- **Error Tests**: Invalid trace IDs, missing data, Langfuse errors

### Frontend Tests
- **Component Tests**: React Testing Library for all views
- **Integration Tests**: Full user flow (paste trace → view analysis)
- **Visual Tests**: Storybook for component documentation
- **E2E Tests**: Playwright for critical user paths

### Test Coverage Goal: 80%+

---

## Security Considerations

### Production Deployment
1. **HTTPS Only**: All traffic over TLS
2. **Cognito Token Validation**: Strict signature verification
3. **CORS Configuration**: Whitelist allowed origins
4. **Rate Limiting**: Prevent abuse of trace extraction API
5. **Input Validation**: Sanitize all trace IDs and user inputs
6. **Secrets Management**: Use AWS Secrets Manager for credentials
7. **Dev Mode Disabled**: Ensure `DEV_MODE=false` in production

### Data Privacy
1. **Trace Data Sensitivity**: Traces may contain sensitive curator queries
2. **Access Control**: Only authenticated developers can access
3. **In-Memory Only**: No persistent storage reduces data exposure
4. **Cache Expiry**: Automatic cleanup after 1 hour or server restart

---

## Performance Optimization

### Backend
- **In-Memory Caching**: Cache entire trace after first fetch (1 hour TTL)
- **Eager Analysis**: Analyze all Phase 1 views on first load (fast subsequent views)
- **Async Operations**: Use `asyncio` for parallel Langfuse calls
- **Simple Eviction**: LRU or time-based cache eviction

### Frontend
- **Code Splitting**: Lazy load each view component
- **API Response Caching**: Cache view responses in React state
- **Virtualization**: For large lists (tool calls with 50+ items)
- **Debounced Search**: Avoid excessive re-renders during search

---

## Future Enhancements

### Advanced Analytics
- **Session Analysis**: Analyze multiple traces from same session
- **Trend Analysis**: Track routing decisions over time
- **Cost Tracking**: Daily/weekly cost reports
- **Performance Benchmarks**: Compare trace performance against baselines

### Collaboration Features
- **Share Links**: Generate shareable links to specific traces
- **Comments**: Add notes/comments to traces
- **Annotations**: Highlight specific tool calls or observations

### Integration with Main App
- **Deep Linking**: Link from main curation app to trace inspector
- **Embedded View**: Show mini trace inspector in main app
- **Feedback Loop**: Report issues from trace inspector to main app

---

## Documentation Plan

### User Documentation
1. **Getting Started Guide**: How to run locally, paste trace ID, navigate views
2. **View Reference**: Detailed explanation of each view
3. **Authentication Setup**: AWS Cognito configuration instructions
4. **Troubleshooting**: Common errors and solutions

### Developer Documentation
1. **Architecture Overview**: System design, component interaction
2. **API Reference**: Complete endpoint documentation with examples
3. **Analyzer Reference**: How to add new analyzers
4. **Deployment Guide**: Production deployment instructions
5. **Contributing Guide**: How to contribute new features

### Operational Documentation
1. **Monitoring**: How to monitor trace inspector health
2. **Backup/Restore**: Data backup procedures
3. **Scaling**: How to scale for multiple concurrent users
4. **Maintenance**: Routine maintenance tasks

---

## Design Decisions (Confirmed)

### UI Layout ✅
**Decision**: Left navigation sidebar
**Rationale**: Maximizes horizontal space for content, easy scanning of views

### View Priority ✅
**Decision**: Phase 1 includes 4 core views
- Summary
- Conversation
- Tool Calls
- Supervisor Routing

**Rationale**: These are the most frequently needed views for trace analysis

### Chart Library ✅
**Decision**: Recharts
**Rationale**: Popular, good balance of features and simplicity
**Implementation**: Phase 2 (with LLM Calls & Costs view)

### JSON Viewer ✅
**Decision**: react-json-view (read-only)
**Rationale**: Lightweight, no editing needed, just visualization
**Implementation**: Phase 2 (Raw JSON view)

### Trace History Feature ✅
**Decision**: Phase 8 (optional enhancement)
**Rationale**: Not critical for MVP, can add after core features proven

---

## Success Metrics

### Developer Experience
- **Time to Insight**: < 10 seconds from paste trace ID to viewing analysis
- **Navigation Speed**: < 1 second to switch between views
- **Error Rate**: < 1% of trace extractions fail
- **User Satisfaction**: Positive feedback from FlyBase/Alliance developers

### Technical Metrics
- **API Response Time**: < 2 seconds for trace analysis
- **Frontend Load Time**: < 3 seconds initial page load
- **Storage Efficiency**: < 1MB per trace (JSON files)
- **Uptime**: 99%+ availability

---

## Conclusion

This specification provides a comprehensive blueprint for building a production-quality, developer-focused Trace Review tool. The system is designed to be:

- **Fast**: Sub-second navigation, < 2s trace analysis
- **Beautiful**: MUI-based UI matching main curation site theme with left sidebar navigation
- **Focused**: Phase 1 delivers 4 core views (Summary, Conversation, Tool Calls, Supervisor Routing)
- **Secure**: AWS Cognito authentication with dev mode for local development
- **Scalable**: Flat file storage, no database overhead
- **Maintainable**: Clean architecture, well-documented APIs, testable components
- **Extensible**: Clear roadmap for Phase 2 views (LLM Costs with Recharts, SQL, Observations, Performance, Raw JSON)

**Phase 1 Scope** (Initial Release):
- 4 core analysis views
- Left sidebar navigation
- In-memory caching (1 hour TTL)
- Copy functionality for text sections
- Basic search/filter in tool calls
- Production-ready auth (AWS Cognito + dev bypass)
- No persistent storage (simplified)

**Phase 2 Scope** (Future Enhancement):
- 5 additional views including Recharts visualizations
- Read-only JSON viewer (react-json-view)
- Export/download functionality
- Optional persistent storage
- Trace history
- Advanced filtering and analytics

**Next Steps**:
1. ✅ Specification approved by Chris
2. Create new directory: `trace_review/` (or similar)
3. Create new branch: `feature/trace-review`
4. Clear context and begin Phase 1 implementation

---

**Document Status**: ✅ APPROVED FOR IMPLEMENTATION
**Author**: Claude (via speech-to-text transcription)
**Reviewer**: Chris Tabone
**Approval Date**: 2025-11-18
**Implementation Ready**: YES
