# AGR AI Curation System

[![Unit Tests](https://github.com/alliance-genome/agr_ai_curation/actions/workflows/test.yml/badge.svg)](https://github.com/alliance-genome/agr_ai_curation/actions/workflows/test.yml)

An AI-powered curation assistant for the [Alliance of Genome Resources](https://www.alliancegenome.org/), helping biocurators extract and validate biological data from research papers.

## Features

- **Config-Driven Agents** - Shipped agents live in package-owned YAML, not hardcoded Python files. Standard installs customize via `~/.agr_ai_curation/runtime/packages/*` and `~/.agr_ai_curation/runtime/config/*`.
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

### Standalone Install

For the published modular runtime, use the installer instead of editing the
repository in place:

1. **Get the installer**
   ```bash
   git clone https://github.com/alliance-genome/agr_ai_curation.git
   cd agr_ai_curation
   ```

2. **Run the standalone installer**
   ```bash
   scripts/install/install.sh
   ```

   To pin a published release, pass `--image-tag vX.Y.Z`.

3. **Review the installed runtime**
   - Secrets and image tags: `~/.agr_ai_curation/.env`
   - Runtime config: `~/.agr_ai_curation/runtime/config/`
   - Shipped and custom packages: `~/.agr_ai_curation/runtime/packages/`
   - Mutable data: `~/.agr_ai_curation/data/`

4. **Access the application**
   - Frontend: http://localhost:3002
   - Backend API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs

See [Modular Packages and Upgrades](docs/deployment/modular-packages.md) for
package authoring, override behavior, standard upgrades, and repo-install
migration.

### Source Development

For local product development in a repository checkout:

1. **Configure environment**
   ```bash
   make setup
   # Edit ~/.agr_ai_curation/.env with your API keys and settings
   ```

   At minimum, set these values in `~/.agr_ai_curation/.env`:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   ```

2. **Start the services**
   ```bash
   make dev-detached
   ```

### Verify Installation

```bash
# Standalone install
docker compose --env-file ~/.agr_ai_curation/.env -f docker-compose.production.yml ps

# Source development
docker compose ps

# Shared health check
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

Agents are no longer hardcoded Python files. The shipped catalog now comes from
the bundled `core` runtime package, while standalone deployments customize
behavior through additional packages under
`~/.agr_ai_curation/runtime/packages/` and deployment YAML overrides under
`~/.agr_ai_curation/runtime/config/`.

```
~/.agr_ai_curation/
├── runtime/config/              # Deployment override YAML
│   ├── models.yaml
│   ├── providers.yaml
│   └── tool_policy_defaults.yaml
└── runtime/packages/
    ├── core/                    # Shipped AGR package
    └── org-custom/              # Your custom package(s)
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

- [Harness Health](docs/developer/HARNESS_HEALTH.md) - Current automation and validation health notes
- [Trace Review](trace_review/README.md) - Langfuse trace analysis tool for debugging agent behavior

### Deployment

- [Independent Deployment](docs/deployment/independent-deployment.md) - Standalone deployment guide
- [Modular Packages and Upgrades](docs/deployment/modular-packages.md) - Runtime layout, package model, overrides, and upgrade paths
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

For a standalone/public install, add agents through a custom runtime package
under `~/.agr_ai_curation/runtime/packages/<your-package>/agents/` rather than
editing the repo-local `config/agents/` directory directly. See
[config/agents/README.md](config/agents/README.md) and
[Modular Packages and Upgrades](docs/deployment/modular-packages.md) for the
package contract.

If you are developing the built-in catalog from a repository checkout, the
repo-local `config/agents/<name>/` folders remain the source tree for the
shipped `core` package.

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
| `~/.agr_ai_curation/runtime/packages/*/agents/*/agent.yaml` | Package-owned agent definitions for standalone installs |
| `~/.agr_ai_curation/runtime/config/models.yaml` | Deployment model overrides |
| `~/.agr_ai_curation/runtime/config/providers.yaml` | Deployment provider overrides |
| `~/.agr_ai_curation/runtime/config/tool_policy_defaults.yaml` | Deployment tool policy overrides |

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
