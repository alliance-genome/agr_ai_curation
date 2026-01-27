# AI Curation Platform - Backend API

## Overview

Unified FastAPI backend serving both AI Chat and Weaviate Control Panel APIs. This service provides:
- AI-powered chat functionality using OpenAI Agents SDK
- Vector database management via Weaviate
- PDF document processing and chunking
- OpenTelemetry tracing with Langfuse

## Architecture

```
backend/
├── src/
│   ├── api/                    # API endpoints
│   │   ├── chat.py             # AI chat endpoints
│   │   ├── documents.py        # Document management
│   │   ├── chunks.py           # Document chunking
│   │   ├── processing.py       # PDF processing
│   │   ├── schema.py           # Schema management
│   │   ├── settings.py         # Settings endpoints
│   │   ├── strategies.py       # Processing strategies
│   │   └── health.py           # Health checks
│   ├── lib/                    # Core libraries
│   │   ├── weaviate_client/    # Weaviate integration
│   │   └── pipeline/           # Processing pipeline
│   └── models/                 # Data models
├── tests/                      # Test suite
├── main.py                     # FastAPI application
├── requirements.txt            # Python dependencies
└── Dockerfile                  # Container definition
```

## API Endpoints

### Chat API (`/api`)
- `POST /api/chat` - Send a message and get a response
- `POST /api/chat/stream` - Stream responses via Server-Sent Events
- `GET /api/chat/status` - Check chat service status

### Weaviate Control Panel (`/weaviate`)
- `GET /weaviate/documents` - List all documents
- `POST /weaviate/documents` - Upload a new document
- `DELETE /weaviate/documents/{id}` - Delete a document
- `GET /weaviate/chunks` - Get document chunks
- `POST /weaviate/processing/start` - Start processing pipeline
- `GET /weaviate/schema` - Get Weaviate schema
- `POST /weaviate/settings` - Update settings

### Health & Monitoring
- `GET /` - API information
- `GET /health` - Comprehensive health check
- `GET /docs` - Swagger UI documentation
- `GET /openapi.json` - OpenAPI specification

## Environment Variables

### Required
- `OPENAI_API_KEY` - OpenAI API key for AI agents

### Optional
- `LANGFUSE_PUBLIC_KEY` - Langfuse public key for tracing
- `LANGFUSE_SECRET_KEY` - Langfuse secret key for tracing
- `WEAVIATE_HOST` - Weaviate host (default: `weaviate`)
- `WEAVIATE_PORT` - Weaviate port (default: `8080`)
- `WEAVIATE_SCHEME` - Weaviate scheme (default: `http`)
- `PDF_STORAGE_PATH` - Path for PDF storage (default: `/app/pdf_storage`)
- `UNSTRUCTURED_API_URL` - Unstructured API URL for PDF processing
- `UNSTRUCTURED_API_KEY` - Unstructured API key
- `DEBUG` - Enable debug mode (default: `false`)

## Development Setup

### Local Development

1. Create virtual environment:
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set environment variables:
```bash
export OPENAI_API_KEY="your-api-key"
export WEAVIATE_HOST="localhost"
export WEAVIATE_PORT="8080"
```

4. Run the development server:
```bash
uvicorn main:app --reload --port 8000
```

### Docker Development

Build and run with Docker Compose from the root directory:
```bash
docker-compose up backend
```

The API will be available at `http://localhost:8000`

## Testing

Run the test suite:
```bash
# Unit tests
pytest tests/unit/

# Integration tests (requires Weaviate)
pytest tests/integration/

# Contract tests
pytest tests/contract/

# All tests with coverage
pytest --cov=src tests/
```

## API Documentation

When the server is running, interactive API documentation is available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Tracing & Monitoring

The backend integrates with Langfuse for distributed tracing of agent operations. When configured with Langfuse credentials, you can monitor:
- Agent execution traces
- Task completion times
- LLM API calls
- Error tracking

View traces at: `http://localhost:3000` (when Langfuse is running)

## Dependencies

### Core
- **FastAPI** - Web framework
- **Uvicorn** - ASGI server
- **Pydantic** - Data validation

### AI & Chat
- **OpenAI Agents SDK** - AI agent framework
- **OpenAI** - LLM provider
- **Langfuse** - Observability
- **OpenInference** - Instrumentation

### Document Processing
- **Weaviate-Client** - Vector database client
- **Unstructured** - PDF processing
- **Pillow** - Image processing
- **PyTesseract** - OCR

## Docker Configuration

The backend runs on port 8000 inside the container. The Dockerfile includes:
- Python 3.11 slim base image
- OCR dependencies (Tesseract)
- PDF processing tools (Poppler)
- Health check endpoint

## Troubleshooting

### Common Issues

1. **Weaviate connection failed**: Ensure Weaviate is running and accessible
2. **OpenAI API errors**: Check your API key is valid
3. **PDF processing fails**: Verify Tesseract and Poppler are installed
4. **Langfuse tracing not working**: Check credentials and network connectivity

### Debug Mode

Enable debug logging:
```bash
export DEBUG=true
```

View logs:
```bash
docker-compose logs -f backend
```

## Contributing

1. Follow the existing code structure
2. Add tests for new features
3. Update API documentation
4. Run linters before committing:
   ```bash
   black src/
   flake8 src/
   mypy src/
   ```