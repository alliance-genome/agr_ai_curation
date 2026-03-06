# AGR AI Curation System

[![Unit Tests](https://github.com/alliance-genome/agr_ai_curation/actions/workflows/test.yml/badge.svg)](https://github.com/alliance-genome/agr_ai_curation/actions/workflows/test.yml)

An AI-powered curation assistant for the [Alliance of Genome Resources](https://www.alliancegenome.org/), helping biocurators extract and validate biological data from research papers.

## Features

- **Config-Driven Agents** - All agents defined in YAML (`config/agents/*/agent.yaml`), not code. Add or modify agents by editing configuration files.
- **Agent Studio** - Browse agents, inspect prompts, discuss behavior with Claude, and submit improvement suggestions.
- **Agent Workshop** - Clone any agent, customize its prompt, select a model, attach tools, and test it against live documents -- all without writing code.
- **Visual Workflow Builder** - Create reusable curation flows by chaining agents together in a drag-and-drop interface.
- **Multi-Provider LLM Support** - Pluggable provider system (`config/providers.yaml`) supporting OpenAI, Gemini, and Groq out of the box. Models are declared in `config/models.yaml`.
- **PDF Processing** - Upload research papers and extract structured data with AI assistance.
- **Batch Processing** - Process multiple documents through saved workflows.
- **Real-time Audit Trail** - Full transparency into AI decisions, database queries, and tool calls.
- **Tool Policy System** - Centralized YAML-based policies (`config/tool_policy_defaults.yaml`) governing which tools are available to curators and agents.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- OpenAI API key (for embeddings and GPT models)
- Optional: Anthropic API key (for Claude in Agent Studio chat)
- Optional: Groq API key, Gemini API key (additional LLM providers)

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/alliance-genome/agr_ai_curation.git
   cd agr_ai_curation
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys and settings
   ```

   At minimum, set these values in `.env`:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   ```

3. **Start the services**
   ```bash
   docker compose up -d
   ```

4. **Access the application**
   - Frontend: http://localhost:3002
   - Backend API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs

### Verify Installation

```bash
# Check all services are running
docker compose ps

# View backend logs
docker compose logs -f backend

# Run health check
curl http://localhost:8000/health
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│    Frontend     │────▶│    Backend      │────▶│   Weaviate      │
│   (React/MUI)   │     │   (FastAPI)     │     │ (Vector Store)  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │   PostgreSQL    │
                        │   (Metadata)    │
                        └─────────────────┘
```

### Config-Driven Agent System

Agents are no longer hardcoded Python files. Each agent is defined by a YAML file under `config/agents/<name>/agent.yaml` that declares its identity, tools, model settings, supervisor routing, and frontend display properties. A registry builder reads these definitions at startup and constructs the runtime agent registry automatically.

```
config/
├── agents/
│   ├── gene/agent.yaml          # Gene validation agent
│   ├── disease/agent.yaml       # Disease ontology agent
│   ├── chemical/agent.yaml      # Chemical entity agent
│   ├── supervisor/agent.yaml    # Supervisor (routing) agent
│   └── ...                      # 15 agent definitions total
├── models.yaml                  # LLM model catalog (GPT-5.4, GPT-5.4 Mini, etc.)
├── providers.yaml               # LLM provider drivers (OpenAI, Gemini, Groq)
└── tool_policy_defaults.yaml    # Tool visibility and permissions
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| frontend | 3002 | React web application |
| backend | 8000 | FastAPI server with config-driven AI agents |
| weaviate | 8080 | Vector database for document chunks |
| postgres | 5432 | Metadata storage (users, documents, flows, custom agents) |
| langfuse | 3000 | Observability and tracing (optional) |
| trace_review | 3001/8001 | Langfuse trace analysis UI (separate service) |

## Documentation

### For Curators

- [Getting Started](docs/curator/GETTING_STARTED.md) - First-time setup and basic usage
- [Best Practices](docs/curator/BEST_PRACTICES.md) - Tips for effective queries
- [Available Agents](docs/curator/AVAILABLE_AGENTS.md) - All specialist agents
- [Curation Flows](docs/curator/CURATION_FLOWS.md) - Visual workflow builder
- [Batch Processing](docs/curator/BATCH_PROCESSING.md) - Process multiple documents
- [Agent Studio](docs/curator/AGENT_STUDIO.md) - Browse prompts, Agent Workshop, and chat with Claude

### For Developers

- [Test Health Report](docs/developer/TEST_HEALTH_REPORT.md) - Current test status and known issues
- [Trace Review](trace_review/README.md) - Langfuse trace analysis tool for debugging agent behavior

### Deployment

- [Independent Deployment](docs/deployment/independent-deployment.md) - Standalone deployment guide
- [LLM Provider Rollout Runbook](docs/deployment/llm-provider-rollout-runbook.md) - Adding new LLM providers
- [LLM Provider Smoke Test Matrix](docs/deployment/llm-provider-smoke-test-matrix.md) - Provider validation checklist

## Development

### Running Tests

```bash
# Run all healthy tests
docker compose exec backend pytest tests/unit/ -v

# Run specific test file
docker compose exec backend pytest tests/unit/test_config.py -v

# Run with coverage
docker compose exec backend pytest tests/unit/ --cov=src --cov-report=html
```

### Code Quality

```bash
# Lint with ruff
docker compose exec backend ruff check .

# Format code
docker compose exec backend ruff format .
```

### Database Migrations

```bash
# Create a new migration
docker compose exec backend alembic revision --autogenerate -m "description"

# Apply migrations
docker compose exec backend alembic upgrade head
```

### Adding a New Agent

To add a new curation agent, create a YAML definition -- no Python code is needed:

1. Create `config/agents/<name>/agent.yaml` following the structure of an existing agent (e.g., `config/agents/gene/agent.yaml`).
2. Optionally add a base prompt file at `config/agents/<name>/prompts/base.md` and MOD-specific rules under `config/agents/<name>/prompts/mods/`.
3. Restart the backend. The registry builder picks up the new definition automatically.
4. The agent appears in Agent Studio and is available for curation flows.

## Configuration

### Environment Variables

All runtime configuration is done through environment variables. See `.env.example` for available options.

| Section | Variables | Description |
|---------|-----------|-------------|
| API Keys | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY` | LLM provider credentials |
| Database | `DATABASE_URL` | PostgreSQL connection |
| Weaviate | `WEAVIATE_HOST`, `WEAVIATE_PORT` | Vector store connection |
| LLM Settings | `DEFAULT_AGENT_MODEL`, `DEFAULT_AGENT_REASONING` | Global model defaults |
| Auth | `COGNITO_*` | AWS Cognito authentication |

### YAML Configuration Files

| File | Description |
|------|-------------|
| `config/agents/*/agent.yaml` | Agent definitions (identity, tools, model, routing) |
| `config/models.yaml` | Available LLM models with guidance for curators |
| `config/providers.yaml` | LLM provider drivers and connection settings |
| `config/tool_policy_defaults.yaml` | Tool visibility and execution permissions |

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Alliance of Genome Resources](https://www.alliancegenome.org/)
- [OpenAI](https://openai.com/) for GPT models
- [Anthropic](https://www.anthropic.com/) for Claude
- [Groq](https://groq.com/) for high-throughput inference
- [Weaviate](https://weaviate.io/) for vector storage
