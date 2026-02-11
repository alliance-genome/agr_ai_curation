# AGR AI Curation System

[![Unit Tests](https://github.com/alliance-genome/agr_ai_curation/actions/workflows/test.yml/badge.svg)](https://github.com/alliance-genome/agr_ai_curation/actions/workflows/test.yml)

An AI-powered curation assistant for the [Alliance of Genome Resources](https://www.alliancegenome.org/), helping biocurators extract and validate biological data from research papers.

## Features

- **Multi-Agent Architecture** - Specialized AI agents for different curation tasks (gene expression, disease ontology, chemical entities, etc.)
- **PDF Processing** - Upload research papers and extract structured data with AI assistance
- **Visual Workflow Builder** - Create reusable curation flows by chaining agents together
- **Batch Processing** - Process multiple documents through saved workflows
- **Real-time Audit Trail** - Full transparency into AI decisions and database queries
- **Agent Studio** - Browse agent prompts, understand AI behavior, and chat with Claude Opus
- **Prompt Workshop** - Clone agent prompts, iterate on custom versions, and compare outputs side-by-side

## Quick Start

### Prerequisites

- Docker and Docker Compose
- OpenAI API key (for embeddings and GPT models)
- Optional: Anthropic API key (for Claude in Agent Studio)

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

### Services

| Service | Port | Description |
|---------|------|-------------|
| frontend | 3002 | React web application |
| backend | 8000 | FastAPI server with AI agents |
| weaviate | 8080 | Vector database for document chunks |
| postgres | 5432 | Metadata storage (users, documents, flows) |
| langfuse | 3000 | Observability and tracing (optional) |
| trace_review | 3001/8001 | Langfuse trace analysis UI (separate service) |

## Documentation

### For Curators

- [Getting Started](docs/curator/GETTING_STARTED.md) - First-time setup and basic usage
- [Best Practices](docs/curator/BEST_PRACTICES.md) - Tips for effective queries
- [Available Agents](docs/curator/AVAILABLE_AGENTS.md) - All specialist agents
- [Curation Flows](docs/curator/CURATION_FLOWS.md) - Visual workflow builder
- [Batch Processing](docs/curator/BATCH_PROCESSING.md) - Process multiple documents
- [Agent Studio](docs/curator/AGENT_STUDIO.md) - Browse prompts and chat with Claude

### For Developers

- [Test Health Report](docs/developer/TEST_HEALTH_REPORT.md) - Current test status and known issues
- [Trace Review](trace_review/README.md) - Langfuse trace analysis tool for debugging agent behavior

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

## Configuration

All configuration is done through environment variables. See `.env.example` for available options.

### Key Configuration Sections

| Section | Variables | Description |
|---------|-----------|-------------|
| API Keys | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` | LLM provider credentials |
| Database | `DATABASE_URL` | PostgreSQL connection |
| Weaviate | `WEAVIATE_HOST`, `WEAVIATE_PORT` | Vector store connection |
| LLM Settings | `DEFAULT_AGENT_MODEL`, `DEFAULT_AGENT_REASONING` | Model defaults |
| Auth | `COGNITO_*` | AWS Cognito authentication |

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
- [Weaviate](https://weaviate.io/) for vector storage
