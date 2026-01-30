# Trace Review API Documentation

**Service**: Standalone Langfuse trace analysis tool
**Purpose**: Comprehensive analysis of AI Curation chat interactions, tool calls, routing decisions, and PDF citations
**Version**: 1.0

---

## Table of Contents
1. [Overview](#overview)
2. [Service Details](#service-details)
3. [Quick Start](#quick-start)
4. [API Endpoints](#api-endpoints)
5. [Finding Trace IDs](#finding-trace-ids)
6. [Performance Characteristics](#performance-characteristics)
7. [Example Workflows](#example-workflows)
8. [Authentication](#authentication)
9. [Cache Management](#cache-management)
10. [Troubleshooting](#troubleshooting)
11. [Retrieving PDF Documents](#retrieving-pdf-documents-associated-with-traces)
12. [Retrieving All Traces in a Session](#retrieving-all-traces-in-a-session)

---

## Overview

The Trace Review tool is a separate Docker-based service for comprehensive Langfuse trace analysis. It provides a web UI and REST API for examining chat interactions, tool calls, routing decisions, and PDF citations from the AI Curation Platform.

**Key Features**:
- ✅ Real-time trace analysis with in-memory caching
- ✅ Support for both local (Docker) and remote (EC2) Langfuse instances
- ✅ Multiple analysis views: summary, conversation, tool calls, PDF citations, tokens, agents, full summary
- ✅ JSON export for batch processing and archival
- ✅ Web UI for interactive exploration

---

## Service Details

| Property | Value |
|----------|-------|
| **Frontend URL** | http://localhost:3001 |
| **Backend API** | http://localhost:8001 |
| **Base API Path** | `/api/traces` |
| **Authentication** | Dev mode bypass (Cognito support planned) |
| **Technology** | FastAPI backend, React + MUI frontend |
| **Deployment** | Standalone docker-compose in `trace_review/` |
| **Cache** | In-memory with configurable TTL |

---

## Quick Start

### 1. Setup Environment

Create `.env` file in `trace_review/` directory:

```bash
cd trace_review
cat > .env << 'EOF'
# Local Langfuse (Docker on host)
LANGFUSE_HOST=http://host.docker.internal:3000
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx

# Remote Langfuse (EC2)
LANGFUSE_REMOTE_HOST=http://ec2-instance:3000
LANGFUSE_REMOTE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_REMOTE_SECRET_KEY=sk-lf-xxx

# Service configuration
DEV_MODE=true
CACHE_TTL_HOURS=1
EOF
```

### 2. Start Services

```bash
docker compose up -d
```

### 3. Verify Services

```bash
# Check backend API
curl http://localhost:8001/health

# Open web UI
open http://localhost:3001
```

---

## API Endpoints

### 1. Analyze Trace

**POST** `/api/traces/analyze`

Fetch and analyze a trace from Langfuse. Checks in-memory cache first; if not cached, fetches from Langfuse, runs all analyzers, and caches the result.

#### Request

```bash
curl -X POST http://localhost:8001/api/traces/analyze \
  -H 'Content-Type: application/json' \
  -d '{
        "trace_id": "70a0a9be91eb4962af80bc4f9972c9b1",
        "source": "remote"
      }' | jq
```

**Request Body** (`AnalyzeTraceRequest`):
```json
{
  "trace_id": "70a0a9be91eb4962af80bc4f9972c9b1",
  "source": "remote"  // "remote" (EC2 Langfuse) or "local" (Docker)
}
```

#### Response (200 OK)

```json
{
  "status": "success",
  "trace_id": "70a0a9be91eb4962af80bc4f9972c9b1",
  "trace_id_short": "70a0a9be",
  "message": "Trace analyzed successfully",
  "cache_status": "miss",
  "available_views": [
    "summary",
    "conversation",
    "tool_calls",
    "pdf_citations",
    "token_analysis",
    "agent_context",
    "trace_summary",
    "document_hierarchy",
    "agent_configs",
    "group_context"
  ]
}
```

#### Cache Behavior

- **Cache hit**: `"cache_status": "hit"`, response < 50ms
- **Cache miss**: Fetches from Langfuse, runs analyzers, caches result (~1-2s)

---

### 2. Export Full Trace Analysis

**GET** `/api/traces/{trace_id}/export?source=remote`

Export complete trace data including raw trace, observations, scores, and all analysis views as a single JSON payload.

#### Request

```bash
curl "http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/export?source=remote" | jq > trace_export.json
```

**Query Parameters**:
- `source`: `"remote"` (default) or `"local"`

#### Response Structure

```json
{
  "raw_trace": {
    /* Full Langfuse trace object */
  },
  "observations": [
    /* Array of observation objects */
  ],
  "scores": [
    /* Array of score objects */
  ],
  "analysis": {
    "summary": {
      "trace_id": "70a0a9be91eb4962af80bc4f9972c9b1",
      "trace_id_short": "70a0a9be",
      "trace_name": "chat-flow-abc123",
      "duration_ms": 1245.5,
      "total_cost": 0.00234,
      "total_tokens": 3457,
      "observation_count": 12,
      "score_count": 0,
      "timestamp": "2025-01-19T14:30:00Z",
      "system_domain": "external_api"
    },
    "conversation": {
      "user_input": "What is the gene dmd-3?",
      "assistant_response": "dmd-3 is a C. elegans gene...",
      "trace_id": "70a0a9be91eb4962af80bc4f9972c9b1"
    },
    "tool_calls": [
      {
        "time": "2025-01-19T14:30:01.234Z",
        "duration": "1.23s",
        "model": "gpt-4o-mini",
        "name": "search_document",
        "status": "completed",
        "input": { "query": "dmd-3 gene" },
        "call_id": "call_abc123"
      }
    ],
    "pdf_citations": {
      "found": true,
      "total_citations": 3,
      "search_queries": ["dmd-3 gene"],
      "extracted_content": "...",
      "citations": [
        { "chunk_id": "...", "section_title": "Results", "page_number": 5 }
      ],
      "total_chunks_found": 3,
      "tool_calls": [ /* PDF tool call metadata */ ]
    },
    "token_analysis": {
      "found": true,
      "total_cost": 0.00234,
      "total_latency": 1.5,
      "total_generations": 5,
      "total_prompt_tokens": 2500,
      "total_completion_tokens": 957,
      "generations": [ /* Per-generation breakdown */ ],
      "model_breakdown": { /* Cost/tokens per model */ }
    },
    "agent_context": {
      "found": true,
      "supervisor": { /* Supervisor agent config */ },
      "specialists": [ /* Specialist agent configs */ ],
      "all_tools": [ /* Available tools */ ]
    },
    "trace_summary": {
      "trace_info": { /* Trace metadata */ },
      "query": "What is dmd-3?",
      "response_preview": "dmd-3 is a C. elegans gene...",
      "response_length": 1234,
      "timing": { /* Latency info */ },
      "cost": { /* Cost breakdown */ },
      "generation_stats": { /* Token stats */ },
      "tool_summary": { /* Tool call counts */ },
      "errors": [],
      "has_errors": false
    },
    "document_hierarchy": {
      "found": true,
      "document_name": "paper.pdf",
      "structure_type": "hierarchy",
      "top_level_sections": ["Introduction", "Methods", "Results"],
      "sections": [ /* Detailed section breakdown */ ]
    },
    "agent_configs": {
      "agents": [ /* List of agent configurations */ ],
      "agent_count": 5,
      "models_used": ["gpt-4o-mini"],
      "tools_available": ["search_document", "read_section"]
    },
    "group_context": {
      "active_groups": ["FB"],
      "injection_active": true,
      "group_count": 1,
      "group_details": [ { "group_id": "FB", "description": "FlyBase (Drosophila melanogaster)" } ]
    }
  }
}
```

#### Use Cases

- **Batch processing**: Analyze multiple traces in scripts
- **Debugging**: Deep dive into complex trace flows
- **Archival**: Save complete trace data for later review
- **Reporting**: Generate comprehensive trace reports

---

### 3. Get Specific View

**GET** `/api/traces/{trace_id}/views/{view_name}`

Retrieve a single analysis view for a previously analyzed trace.

**⚠️ Important**: Must call `/analyze` first to populate the cache.

#### Available Views

| View Name | Description |
|-----------|-------------|
| `summary` | Quick stats: duration, cost, tokens, observation counts |
| `conversation` | User query + clean assistant response text |
| `tool_calls` | Chronological list with timing, models, status |
| `pdf_citations` | PDF search queries, extracted content, citations with page numbers |
| `token_analysis` | Detailed token usage, cost breakdown by model |
| `agent_context` | Agent configurations, tools, instructions |
| `trace_summary` | Comprehensive overview: query, response preview, errors, timing, cost |
| `document_hierarchy` | Document section structure extracted from PDF specialist |
| `agent_configs` | Detailed agent configuration events (models, tools, system prompts) |
| `group_context` | Organization group context (active groups for rule injection) |

#### Request Examples

```bash
# Get summary view
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/summary | jq

# Get conversation view (user query + assistant response)
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/conversation | jq

# Get tool calls view
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/tool_calls | jq

# Get PDF citations view
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/pdf_citations | jq

# Get token analysis view
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/token_analysis | jq

# Get agent context view
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/agent_context | jq

# Get full trace summary view
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/trace_summary | jq

# Get document hierarchy view (PDF structure)
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/document_hierarchy | jq

# Get agent configs view (detailed agent configurations)
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/agent_configs | jq

# Get group context view (organization group context)
curl http://localhost:8001/api/traces/70a0a9be91eb4962af80bc4f9972c9b1/views/group_context | jq
```

#### Response (200 OK)

```json
{
  "view": "conversation",
  "trace_id": "70a0a9be91eb4962af80bc4f9972c9b1",
  "cached_at": "2025-01-19T14:35:00Z",
  "data": {
    "user_input": "What is the gene dmd-3?",
    "assistant_response": "dmd-3 is a C. elegans gene...",
    "trace_id": "70a0a9be91eb4962af80bc4f9972c9b1"
  }
}
```

#### Error (404 Not Found)

```json
{
  "detail": "Trace not found in cache. Call /api/traces/analyze first."
}
```

---

### 4. Clear Cache

**POST** `/api/traces/cache/clear`

Clear all cached trace analyses from memory. Useful for forcing fresh fetches or freeing memory.

#### Request

```bash
curl -X POST http://localhost:8001/api/traces/cache/clear | jq
```

#### Response

```json
{
  "status": "success",
  "message": "Cache cleared: 15 traces removed",
  "cleared_count": 15
}
```

---

## Finding Trace IDs

Trace IDs are emitted in multiple places:

### 1. Backend Logs

```bash
# Main application logs
docker compose logs backend | grep "Trace:"

# Example output
🔍 [Trace: 70a0a9be91eb4962af80bc4f9972c9b1] Started supervisor flow
```

### 2. Chat Stream Events

Trace IDs are included in SSE events from `/api/chat/stream`:

```json
{
  "type": "TEXT_MESSAGE_START",
  "messageId": "msg-abc123",
  "session_id": "session-xyz789",
  "trace_id": "70a0a9be91eb4962af80bc4f9972c9b1"
}
```

### 3. Langfuse Dashboard

Visit the Langfuse UI to browse all traces:
- **Local**: http://localhost:3000
- **Remote**: http://<ec2-instance>:3000

---

## Performance Characteristics

| Operation | Cache Hit | Cache Miss |
|-----------|-----------|------------|
| `/analyze` | < 50ms | 1-2 seconds |
| `/export` | < 100ms | 1-2 seconds |
| `/views/{view_name}` | < 50ms | N/A (requires analyze first) |

**Cache Configuration**:
- **Default TTL**: 1 hour (configurable via `CACHE_TTL_HOURS`)
- **Storage**: In-memory (cleared on service restart)
- **Eviction**: Automatic after TTL expires

---

## Example Workflows

### Workflow 1: Debug a Chat Session

```bash
# Step 1: Run chat query in main app (localhost:8000)
SESSION=$(curl -s -X POST http://localhost:8000/api/chat/session | jq -r '.session_id')
curl -N -X POST http://localhost:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d "{\"message\": \"What reagents were used?\", \"session_id\": \"$SESSION\"}"

# Step 2: Extract trace ID from SSE events (look for trace_id field)
TRACE_ID="70a0a9be91eb4962af80bc4f9972c9b1"

# Step 3: Analyze in trace_review tool
curl -X POST http://localhost:8001/api/traces/analyze \
  -H 'Content-Type: application/json' \
  -d "{\"trace_id\": \"$TRACE_ID\", \"source\": \"local\"}" | jq

# Step 4: View specific analysis
curl http://localhost:8001/api/traces/$TRACE_ID/views/tool_calls | jq
curl http://localhost:8001/api/traces/$TRACE_ID/views/trace_summary | jq

# Step 5: Export complete analysis for archival
curl http://localhost:8001/api/traces/$TRACE_ID/export?source=local > debug_trace.json
```

### Workflow 2: Batch Analysis Script

```bash
#!/bin/bash
# Analyze multiple traces and export to files

TRACE_IDS=(
  "70a0a9be91eb4962af80bc4f9972c9b1"
  "a8c3f1d2e4b6c9a7f3e1d5b8c2a4f6e9"
  "f3e7c1b4a2d8e6f9c3a1b5d7e9f2c4a6"
)

for TRACE_ID in "${TRACE_IDS[@]}"; do
  echo "Analyzing trace: $TRACE_ID"

  # Analyze
  curl -s -X POST http://localhost:8001/api/traces/analyze \
    -H 'Content-Type: application/json' \
    -d "{\"trace_id\": \"$TRACE_ID\", \"source\": \"remote\"}" | jq

  # Export to file
  curl -s "http://localhost:8001/api/traces/$TRACE_ID/export?source=remote" \
    > "trace_${TRACE_ID:0:8}.json"

  echo "Exported to trace_${TRACE_ID:0:8}.json"
done
```

### Workflow 3: Compare Token Usage

```bash
# Analyze multiple traces and compare token usage and costs

for TRACE_ID in trace1 trace2 trace3; do
  curl -s -X POST http://localhost:8001/api/traces/analyze \
    -H 'Content-Type: application/json' \
    -d "{\"trace_id\": \"$TRACE_ID\", \"source\": \"local\"}" > /dev/null

  curl -s http://localhost:8001/api/traces/$TRACE_ID/views/token_analysis \
    | jq -r '.data | "Tokens: \(.total_prompt_tokens + .total_completion_tokens) | Cost: $\(.total_cost)"'
done
```

---

## Authentication

### Development Mode (Current)

When `DEV_MODE=true`, authentication is completely bypassed:
- ✅ No cookies or headers required
- ✅ All endpoints accessible without auth
- ✅ Mock user injected automatically

### Production Mode (Planned)

For Cognito-protected deployments:

1. Set `DEV_MODE=false` in `.env`
2. Configure `COGNITO_*` environment variables
3. Visit `/api/auth/login` to authenticate (redirects to Cognito Hosted UI)
4. After login, cookie is set automatically
5. Reuse cookie for all API calls

**Example with Cognito**:
```bash
# Login (follow browser flow)
curl -L -c cookies.txt http://localhost:8001/api/auth/login

# Use authenticated cookie
curl -b cookies.txt http://localhost:8001/api/traces/analyze \
  -H 'Content-Type: application/json' \
  -d '{"trace_id": "...", "source": "remote"}'
```

---

## Cache Management

### Cache Lifecycle

1. **Population**: Automatic on first `/analyze` or `/export` call
2. **Access**: Subsequent calls return cached data (< 50ms)
3. **Expiration**: Automatic after TTL (default: 1 hour)
4. **Clearing**: Manual via `/cache/clear` or service restart

### Cache Statistics

```bash
# View cache status (not yet implemented)
# Future endpoint: GET /api/traces/cache/stats
```

### Best Practices

- **Long analysis sessions**: Keep service running to benefit from cache
- **Memory constraints**: Clear cache periodically with `/cache/clear`
- **Fresh data required**: Force refresh by clearing cache before analysis

---

## Troubleshooting

### Issue: Trace Not Found (404)

**Symptoms**:
```json
{
  "detail": "Trace 70a0a9be... not found in Langfuse (remote): ..."
}
```

**Solutions**:
1. Verify trace ID is correct (check backend logs or Langfuse UI)
2. Check `source` parameter matches where trace exists (`local` vs `remote`)
3. Verify Langfuse credentials in `.env` file
4. Test Langfuse connection: `curl http://localhost:3000` (local) or EC2 instance (remote)

### Issue: Cache Miss on Second Request

**Symptoms**: `"cache_status": "miss"` on repeated requests

**Possible Causes**:
1. Service was restarted (cache is in-memory only)
2. Cache TTL expired (default: 1 hour)
3. Different trace ID being requested

**Solutions**:
- Check service uptime: `docker compose ps`
- Increase `CACHE_TTL_HOURS` in `.env` if needed
- Verify trace ID is exactly the same

### Issue: Slow Response Times

**Symptoms**: Requests taking > 5 seconds

**Possible Causes**:
1. Cache miss (first request for trace)
2. Large trace with many observations
3. Slow Langfuse connection (remote)

**Solutions**:
- Pre-populate cache with `/analyze` calls
- Use local Langfuse for faster development
- Check network latency to remote Langfuse instance

### Issue: Service Not Starting

**Symptoms**: `docker compose up -d` fails or containers crash

**Checklist**:
1. ✅ `.env` file exists in `trace_review/` directory
2. ✅ All required env vars set (`LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`)
3. ✅ Ports 3001 and 8001 are not already in use
4. ✅ Docker daemon is running

**Debug commands**:
```bash
# Check container logs
docker compose logs backend
docker compose logs frontend

# Verify environment
docker compose exec backend env | grep LANGFUSE

# Test port availability
lsof -i :8001
lsof -i :3001
```

---

## API Reference Summary

| Endpoint | Method | Purpose | Cache Required |
|----------|--------|---------|----------------|
| `/api/traces/analyze` | POST | Analyze trace and populate cache | No |
| `/api/traces/{id}/export` | GET | Export full trace analysis | No (auto-populates) |
| `/api/traces/{id}/views/{view}` | GET | Get specific analysis view | Yes |
| `/api/traces/cache/clear` | POST | Clear all cached traces | N/A |

---

## Related Resources

- **Main API Documentation**: `docs/api/API_USAGE.md`
- **Langfuse Documentation**: https://langfuse.com/docs
- **Trace Review Source**: `trace_review/backend/src/`
- **Analyzers**: `trace_review/backend/src/analyzers/`

---

## Retrieving PDF Documents Associated with Traces

When analyzing traces from chat sessions that involve PDF documents, you may need to retrieve the actual PDF file that was being discussed. This section explains how to find and download PDFs associated with specific traces.

### Prerequisites

- SSH access to EC2 instance running the AI Curation Platform
- SSH key file (e.g., `~/pem_certs/AGR-ssl3.pem`)
- Trace ID from Langfuse or backend logs

### Understanding the Document Storage Structure

PDFs are stored on EC2 in a multi-tenant directory structure:

```
pdf_storage/
  ├── <user_id>/
  │   ├── <document_id>/
  │   │   ├── <filename>.pdf
  │   │   ├── docling_json/
  │   │   └── processed_json/
```

**Example path**:
```
pdf_storage/a4d89458-b061-703e-6242-7bbb12202a5d/9ced2d60-ef56-4947-9cc4-a22aec3336cc/6705416_J306019.pdf
```

### Step 1: Find Trace Metadata

#### Option A: From Backend Logs (Quickest)

SSH to EC2 and grep the backend logs for your trace ID:

```bash
ssh <ec2-instance>

cd ai_curation_prototype

# Search for trace with document information
docker compose logs backend | grep -A 5 -B 5 "Trace: <trace_id>" | grep -E "document_id|user_id"
```

**Example Output**:
```
document_id: '9ced2d60-ef56-4947-9cc4-a22aec3336cc', user_id: 'a4d89458-b061-703e-6242-7bbb12202a5d'
```

#### Option B: From Recent Logs (Find Latest Traces)

```bash
ssh <ec2-instance>

cd ai_curation_prototype

# Get recent traces with document info
docker compose logs backend | grep -E "Trace:|document_id" | tail -50
```

Look for log lines containing both trace ID and document metadata:
```
🔍 [Trace: 2ad79d7db4bf653b5035108f2d6b73bf] [Session: 7736d836-7a42-4f62-8ff0-1068df819a00] Processing message...
🔍 Weaviate search tool called with query: '...', document_id: '9ced2d60-ef56-4947-9cc4-a22aec3336cc', user_id: 'a4d89458-b061-703e-6242-7bbb12202a5d'
```

#### Option C: Via Trace Review API

If the trace has been analyzed, you can extract metadata from the trace export:

```bash
# Get trace analysis
curl "http://localhost:8001/api/traces/<trace_id>/export?source=remote" | jq '.raw_trace.metadata'
```

### Step 2: Locate the PDF File

Once you have `user_id` and `document_id`, locate the PDF on EC2:

```bash
ssh <ec2-instance>

USER_ID="a4d89458-b061-703e-6242-7bbb12202a5d"
DOCUMENT_ID="9ced2d60-ef56-4947-9cc4-a22aec3336cc"

# List files in document directory
ls -la ai_curation_prototype/pdf_storage/$USER_ID/$DOCUMENT_ID/
```

**Example Output**:
```
-rw-r--r-- 1 root root 1813303 Nov 21 18:17 6705416_J306019.pdf
```

### Step 3: Download the PDF

Use `scp` to download the PDF to your local machine:

```bash
USER_ID="a4d89458-b061-703e-6242-7bbb12202a5d"
DOCUMENT_ID="9ced2d60-ef56-4947-9cc4-a22aec3336cc"
FILENAME="6705416_J306019.pdf"

scp <ec2-instance>:ai_curation_prototype/pdf_storage/$USER_ID/$DOCUMENT_ID/$FILENAME \
  ~/Downloads/trace_$DOCUMENT_ID.pdf
```

### Complete Example Workflow

```bash
# 1. Find trace with document information
ssh <ec2-instance> "cd ai_curation_prototype && docker compose logs backend | grep -E 'Trace:|document_id' | tail -20"

# Example output shows:
# Trace: 2ad79d7db4bf653b5035108f2d6b73bf
# document_id: '9ced2d60-ef56-4947-9cc4-a22aec3336cc'
# user_id: 'a4d89458-b061-703e-6242-7bbb12202a5d'

# 2. Set variables
TRACE_ID="2ad79d7db4bf653b5035108f2d6b73bf"
USER_ID="a4d89458-b061-703e-6242-7bbb12202a5d"
DOCUMENT_ID="9ced2d60-ef56-4947-9cc4-a22aec3336cc"

# 3. List files in document directory
ssh <ec2-instance> \
  "ls -la ai_curation_prototype/pdf_storage/$USER_ID/$DOCUMENT_ID/"

# Output: 6705416_J306019.pdf

# 4. Download PDF
scp <ec2-instance>:ai_curation_prototype/pdf_storage/$USER_ID/$DOCUMENT_ID/6705416_J306019.pdf \
  ~/Downloads/trace_${TRACE_ID}_document.pdf

# 5. Verify download
ls -lh ~/Downloads/trace_${TRACE_ID}_document.pdf
```

### Automated Script

Create a helper script `get_trace_pdf.sh` for repeated use:

```bash
#!/bin/bash
# get_trace_pdf.sh - Download PDF associated with a trace ID

TRACE_ID="$1"
OUTPUT_DIR="${2:-~/Downloads}"

if [ -z "$TRACE_ID" ]; then
  echo "Usage: $0 <trace_id> [output_dir]"
  exit 1
fi

echo "🔍 Finding document for trace: $TRACE_ID"

# Search logs for document metadata
METADATA=$(ssh <ec2-instance> \
  "cd ai_curation_prototype && docker compose logs backend 2>/dev/null | grep -B 5 -A 5 'Trace: $TRACE_ID' | grep -oP \"document_id: '\K[^']+|user_id: '\K[^']+\" | head -2")

if [ -z "$METADATA" ]; then
  echo "❌ No document found for trace $TRACE_ID"
  exit 1
fi

DOCUMENT_ID=$(echo "$METADATA" | sed -n '1p')
USER_ID=$(echo "$METADATA" | sed -n '2p')

echo "📄 Found document:"
echo "   User ID: $USER_ID"
echo "   Document ID: $DOCUMENT_ID"

# Find PDF filename
FILENAME=$(ssh <ec2-instance> \
  "ls ai_curation_prototype/pdf_storage/$USER_ID/$DOCUMENT_ID/*.pdf 2>/dev/null | head -1 | xargs basename")

if [ -z "$FILENAME" ]; then
  echo "❌ PDF file not found"
  exit 1
fi

echo "📥 Downloading: $FILENAME"

# Download PDF
scp <ec2-instance>:ai_curation_prototype/pdf_storage/$USER_ID/$DOCUMENT_ID/$FILENAME \
  "$OUTPUT_DIR/trace_${TRACE_ID}_${FILENAME}"

if [ $? -eq 0 ]; then
  echo "✅ Downloaded to: $OUTPUT_DIR/trace_${TRACE_ID}_${FILENAME}"
else
  echo "❌ Download failed"
  exit 1
fi
```

**Usage**:
```bash
chmod +x get_trace_pdf.sh

# Download PDF for specific trace
./get_trace_pdf.sh 2ad79d7db4bf653b5035108f2d6b73bf

# Specify output directory
./get_trace_pdf.sh 2ad79d7db4bf653b5035108f2d6b73bf ~/Documents/traces/
```

### Troubleshooting

**Issue**: `No such file or directory` error

**Possible Causes**:
1. Document ID not found in logs (trace may not have used a PDF)
2. PDF was deleted or moved
3. Incorrect user_id or document_id

**Solutions**:
- Verify trace actually involved a PDF: check for "active_document" in logs
- Check if user_id folder exists: `ls pdf_storage/`
- Verify document_id folder exists: `ls pdf_storage/<user_id>/`

**Issue**: Permission denied when downloading

**Solutions**:
- Verify SSH key has correct permissions: `chmod 600 ~/pem_certs/AGR-ssl3.pem`
- Check you have read access to the file
- Contact admin if file ownership is restricted

---

## Retrieving All Traces in a Session

When analyzing a single trace, you often want to see the full conversation context - all the queries the curator made in that session.

### Understanding Session IDs

- **Langfuse Session ID** = Chat Session UUID (e.g., `2f30b76d-8abf-4b66-ab7a-3de4d8e8fdb2`)
- **Feedback Session ID** = Same as Langfuse Session ID (they now match correctly)
- Each chat session creates multiple traces (one per query)

> **Note**: Prior to 2025-12-10, there was a bug where Langfuse sessionId was incorrectly set to the document_id. Historical traces before this fix will have document IDs as their sessionId instead of chat session IDs.

### Step 1: Get Session ID from a Trace

When you analyze a trace, extract the session ID from the raw trace:

```bash
# Analyze the trace first
curl -s -X POST http://localhost:8001/api/traces/analyze \
  -H 'Content-Type: application/json' \
  -d '{"trace_id": "c2d8d2174f9eb474181bb0d9e7a9930b", "source": "remote"}'

# Export and extract session ID
curl -s "http://localhost:8001/api/traces/c2d8d2174f9eb474181bb0d9e7a9930b/export?source=remote" \
  | jq -r '.raw_trace.sessionId'
# Output: ef55be6a-a67a-4258-9430-bf31f42b2662
```

### Step 2: Query Langfuse for All Session Traces

**Important**: Query Langfuse directly from EC2 (not through trace_review) to list all traces in a session:

```bash
# SSH to EC2 and query Langfuse API
SESSION_ID="ef55be6a-a67a-4258-9430-bf31f42b2662"

ssh <ec2-instance> \
  "curl -s 'http://localhost:3000/api/public/traces?sessionId=$SESSION_ID&limit=50' \
    -u '$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY' \
    | jq '.data | length'"
# Output: 14  (number of traces in session)
```

### Step 3: List All Traces with Timestamps

Get a chronological view of all traces in the session:

```bash
SESSION_ID="ef55be6a-a67a-4258-9430-bf31f42b2662"

ssh <ec2-instance> \
  "curl -s 'http://localhost:3000/api/public/traces?sessionId=$SESSION_ID&limit=50' \
    -u '$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY' \
    | jq '.data[] | {id, name, timestamp}'"
```

**Example Output**:
```json
{"id": "d6006a2407ddeabac11bde2e645d6ef8", "name": "ontology_mapping_specialist_config", "timestamp": "2025-12-10T16:39:04.804Z"}
{"id": "ca37e2ff4ad9c0ed3e2cca060cacccf8", "name": "query_supervisor_config", "timestamp": "2025-12-10T16:37:59.067Z"}
{"id": "c2d8d2174f9eb474181bb0d9e7a9930b", "name": "ontology_mapping_specialist_config", "timestamp": "2025-12-10T15:01:42.956Z"}
...
```

### Step 4: Analyze Conversation Flow

Filter for `query_supervisor_config` traces (these contain user queries) and analyze each one:

```bash
SESSION_ID="ef55be6a-a67a-4258-9430-bf31f42b2662"

# Get list of supervisor traces (user queries)
SUPERVISOR_TRACES=$(ssh <ec2-instance> \
  "curl -s 'http://localhost:3000/api/public/traces?sessionId=$SESSION_ID&limit=50' \
    -u '$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY' \
    | jq -r '.data[] | select(.name == \"query_supervisor_config\") | .id'")

# Analyze each trace and get conversation
for trace_id in $SUPERVISOR_TRACES; do
  echo "=== Trace: $trace_id ==="

  # Analyze trace
  curl -s -X POST http://localhost:8001/api/traces/analyze \
    -H 'Content-Type: application/json' \
    -d "{\"trace_id\": \"$trace_id\", \"source\": \"remote\"}" > /dev/null

  # Get conversation
  curl -s "http://localhost:8001/api/traces/$trace_id/views/conversation" \
    | jq -r '.data | "User: " + .user_input + "\n\nAssistant: " + (.assistant_response | .[0:500]) + "..."'

  echo -e "\n"
done
```

### Understanding Trace Types

| Trace Name | Contains | Purpose |
|------------|----------|---------|
| `query_supervisor_config` | User query + AI response | Main conversation turns |
| `ontology_mapping_specialist_config` | Ontology lookups | Specialist agent work |
| `allele_specialist_config` | Allele database queries | Specialist agent work |
| `pdf_specialist_config` | PDF extraction | Specialist agent work |

### Example: Full Session Analysis

```bash
# Complete workflow to analyze a curator's full session

# 1. Start with a known trace ID (from feedback email)
TRACE_ID="c2d8d2174f9eb474181bb0d9e7a9930b"

# 2. Get session ID
SESSION_ID=$(curl -s "http://localhost:8001/api/traces/$TRACE_ID/export?source=remote" \
  | jq -r '.raw_trace.sessionId')
echo "Session ID: $SESSION_ID"

# 3. Count traces in session
ssh <ec2-instance> \
  "curl -s 'http://localhost:3000/api/public/traces?sessionId=$SESSION_ID&limit=50' \
    -u '$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY' \
    | jq '.data | length'"

# 4. List all queries chronologically
ssh <ec2-instance> \
  "curl -s 'http://localhost:3000/api/public/traces?sessionId=$SESSION_ID&limit=50' \
    -u '$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY' \
    | jq -r '.data[] | select(.name == \"query_supervisor_config\") | \"\(.timestamp) | \(.id[:8])\"'" \
  | sort
```

### Use Cases

- **Understanding feedback context**: See what questions led up to the feedback
- **Debugging multi-turn issues**: Track how AI responses evolved across queries
- **Identifying patterns**: Find where the AI started going wrong in a conversation
- **Session reconstruction**: Rebuild the full curator interaction for analysis

### Notes

- Langfuse credentials are stored in `trace_review/backend/.env`
- VPN connection required to query EC2 Langfuse directly
- Session queries must be run from EC2 (localhost:3000) not through VPN tunnel
- The trace_review service doesn't have a session listing endpoint (yet)

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-01-22 | Initial documentation (extracted from API_USAGE.md) |
| 1.1 | 2025-11-22 | Added comprehensive PDF retrieval instructions with EC2 SSH workflow |
| 1.2 | 2025-11-26 | Removed supervisor_routing (deprecated). Added token_analysis, agent_context, trace_summary views. Updated response format documentation. |
| 1.3 | 2025-12-10 | Added "Retrieving All Traces in a Session" section for analyzing full conversation context from a single trace ID. |
| 1.4 | 2025-12-11 | Added document_hierarchy, agent_configs, and mod_context views to Available Views table and examples. |
