# Trace Review - Langfuse Trace Analysis Tool

A Docker-based, web-accessible developer tool for comprehensive analysis of Langfuse traces from AI curation sessions. Features a beautiful Material-UI interface with multi-dimensional analysis across organized views.

## Features

- **Single Trace ID Input**: Paste a trace ID and instantly view comprehensive analysis
- **4 Core Views** (Phase 1):
  - **Summary**: Quick stats with cards (duration, cost, tokens, observations)
  - **Conversation**: User query + assistant response with copy buttons
  - **Tool Calls**: Chronological list with reasoning, URLs, methods, status
  - **Supervisor Routing**: Routing decision visualization with plans and metadata
- **In-Memory Caching**: Fast switching between views (< 50ms after initial load)
- **Dev Mode Authentication**: Bypass authentication for local development
- **Clean MUI Interface**: Matches existing curation site theme with left sidebar navigation

## Tech Stack

### Frontend
- React 18 + TypeScript
- Vite (build tool)
- Material-UI v5
- Axios (HTTP client)

### Backend
- FastAPI (Python 3.11+)
- Langfuse Python SDK
- In-memory cache with TTL

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Langfuse server running (or access to Langfuse API)

### 1. Setup Environment

Create `.env` file in the `trace_review/` directory:

```bash
# Langfuse Configuration
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx

# IMPORTANT: Use host.docker.internal for Langfuse running on host machine
# This allows the backend container to reach Langfuse on your host
LANGFUSE_HOST=http://host.docker.internal:3000

# For remote Langfuse servers, use the actual URL:
# LANGFUSE_HOST=https://cloud.langfuse.com

# Development Mode (bypass authentication)
DEV_MODE=true

# Cache Configuration
CACHE_TTL_HOURS=1
```

**Important Notes:**
- `host.docker.internal` works on Docker Desktop (Mac/Windows) to access services on the host machine
- On Linux, you may need to use `--add-host=host.docker.internal:host-gateway` in docker-compose.yml or use your host's IP address
- If Langfuse is running in Docker, use the service name from its docker-compose network

### 2. Start Services

```bash
cd trace_review
docker compose up -d
```

This will start:
- **Frontend**: http://localhost:3001
- **Backend**: http://localhost:8001

### 3. Use the Tool

1. Open browser to http://localhost:3001
2. Paste a Langfuse trace ID into the input field
3. Click "Analyze" or press Enter
4. Navigate between views using the left sidebar
5. Copy text using the copy buttons in each view

## Project Structure

```
trace_review/
├── backend/
│   ├── src/
│   │   ├── analyzers/          # Trace analysis logic
│   │   │   ├── conversation.py
│   │   │   ├── tool_calls.py
│   │   │   └── supervisor_routing.py
│   │   ├── api/                # FastAPI endpoints
│   │   │   ├── auth.py
│   │   │   └── traces.py
│   │   ├── models/             # Pydantic models
│   │   ├── services/           # Core services
│   │   │   ├── cache_manager.py
│   │   │   └── trace_extractor.py
│   │   └── main.py             # FastAPI app
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/         # Reusable components
│   │   ├── views/              # View components
│   │   │   ├── SummaryView.tsx
│   │   │   ├── ConversationView.tsx
│   │   │   ├── ToolCallsView.tsx
│   │   │   └── SupervisorRoutingView.tsx
│   │   ├── services/           # API client
│   │   ├── theme/              # MUI theme config
│   │   ├── types/              # TypeScript types
│   │   ├── App.tsx             # Main app component
│   │   └── main.tsx            # Entry point
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   └── vite.config.ts
├── docker-compose.yml
├── .env.example
└── README.md
```

## API Endpoints

### Authentication
- `POST /api/auth/dev-bypass` - Dev mode authentication bypass

### Trace Analysis
- `POST /api/traces/analyze` - Analyze a trace (checks cache, fetches from Langfuse if needed)
- `GET /api/traces/{trace_id}/views/{view_name}` - Get specific view data

**Available Views**: `summary`, `conversation`, `tool_calls`, `supervisor_routing`

## Development

### Local Development (Without Docker)

**Backend:**
```bash
cd trace_review/backend

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables (use localhost for Langfuse on host)
export LANGFUSE_PUBLIC_KEY=pk-lf-xxx
export LANGFUSE_SECRET_KEY=sk-lf-xxx
export LANGFUSE_HOST=http://localhost:3000
export DEV_MODE=true
export CACHE_TTL_HOURS=1

# Run locally
uvicorn src.main:app --reload --port 8001
```

**Frontend:**
```bash
cd trace_review/frontend

# Install dependencies
npm install

# Run dev server (proxy configured for localhost:8001)
npm run dev
```

The frontend dev server will automatically proxy API requests to `http://localhost:8001` when running locally.

### Docker Development

For development with auto-reload in Docker:
```bash
# Backend and frontend will hot-reload on file changes
docker compose up
```

### Build for Production

```bash
# Build both services
docker compose build

# Run in production mode
docker compose up -d
```

## Cache Behavior

- **Cache TTL**: 1 hour (configurable via `CACHE_TTL_HOURS`)
- **Cache Hit**: Instant response (< 50ms)
- **Cache Miss**: Fetches from Langfuse, analyzes, caches (< 2 seconds)
- **Cache Storage**: In-memory (cleared on server restart)

## Performance Targets

- Initial trace analysis: **< 2 seconds** (cache miss)
- View switching: **< 50ms** (cache hit)
- UI response: **< 100ms** (instant visual feedback)

## Troubleshooting

### Backend can't connect to Langfuse
**Symptom:** Error connecting to Langfuse, "Connection refused" or timeout errors

**Solutions:**
- **Docker Desktop (Mac/Windows):** Use `LANGFUSE_HOST=http://host.docker.internal:3000` in `.env`
- **Linux:** Add this to `docker-compose.yml` under the `backend` service:
  ```yaml
  extra_hosts:
    - "host.docker.internal:host-gateway"
  ```
  Then use `LANGFUSE_HOST=http://host.docker.internal:3000`
- **Alternative for Linux:** Use your host's IP address instead: `LANGFUSE_HOST=http://192.168.1.x:3000`
- **Langfuse in Docker:** If Langfuse is also running in Docker, use the service name or create a shared network

### Backend won't start
- Check Langfuse credentials in `.env`
- Verify Langfuse server is accessible from within container
- Check logs: `docker compose logs backend`
- Verify `.env` file exists and is properly formatted

### Frontend can't connect to backend
- Verify backend is running: `curl http://localhost:8001`
- Check browser console for CORS errors
- Ensure ports 3001 and 8001 are not in use
- For local dev: Ensure backend is on `http://localhost:8001`

### Frontend build fails
- Ensure you have Node.js 20+ installed
- Run `npm install` in the frontend directory
- Check `docker compose logs frontend` for build errors

### Trace not found
- Verify trace ID is correct (32-char hex from Langfuse)
- Check Langfuse server connectivity
- Ensure API credentials have read access to traces

## Future Enhancements (Phase 2+)

- **LLM Calls & Costs View**: Token usage charts with Recharts
- **SQL Queries View**: Syntax-highlighted queries
- **Observations View**: Hierarchical tree structure
- **Performance Metrics View**: Latency breakdown charts
- **Raw JSON Viewer**: Collapsible JSON with react-json-view
- **Trace History**: Recently analyzed traces in sidebar
- **Export Functionality**: Download traces as JSON
- **AWS Cognito Authentication**: Production auth (currently dev mode only)

## License

Internal tool for FlyBase/Alliance development use.

## Support

For issues or questions, contact the development team.
