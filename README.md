# Alliance AI-Assisted Curation Interface

An advanced three-panel interface for AI-assisted biocuration, featuring real-time streaming chat with multiple AI models (OpenAI GPT-4o, Gemini), PDF annotation with multi-color highlighting, and comprehensive curation tools.

## 🚀 Key Features

### AI Chat Integration

- **Multiple AI Models**: Support for OpenAI (GPT-4o, GPT-4o-mini, GPT-3.5-turbo) and Google Gemini (2.0 Flash, 1.5 Pro, 1.5 Flash)
- **Real-time Streaming**: Server-Sent Events (SSE) for smooth, token-by-token responses
- **Model Switching**: Seamlessly switch between models during conversation
- **Conversation Persistence**: All chats saved to PostgreSQL with full history
- **Markdown Support**: Rich text formatting with code syntax highlighting

### PDF Annotation System

- **Multi-color Highlighting**: Six color options for categorizing annotations
- **Entity Extraction**: Automatic detection and highlighting of biological entities
- **Zoom Controls**: Smooth zooming and navigation
- **Text Selection**: Select and annotate specific passages

### Curation Tools

- **Entity Management**: Track genes, proteins, diseases, and custom entities
- **Metadata Tracking**: Comprehensive paper metadata management
- **Test Data Generation**: Built-in tools for testing curation workflows
- **Configuration Management**: Flexible settings for different curation needs

## 🛠️ Technology Stack

- **Frontend**: React 18 with Material-UI v5, Vite, TypeScript
- **Backend**: FastAPI (Python 3.11+), SQLAlchemy, Pydantic v2
- **Database**: PostgreSQL 16 with Alembic migrations
- **AI Services**: OpenAI SDK, Google Generative AI (Gemini)
- **Testing**: Vitest (Frontend), Pytest (Backend)
- **Security**: Pre-commit hooks, secret detection, Gitleaks
- **Containerization**: Docker Compose with multi-stage builds

## 📋 Prerequisites

- Docker and Docker Compose (for containerized setup)
- Node.js 20+ and npm (for local frontend development)
- Python 3.11+ (for local backend development)
- OpenAI API key (required for GPT models)
- Google AI API key (optional, for Gemini models)

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/alliance-genome/agr_ai_curation.git
cd agr_ai_curation
```

### 2. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env and add your API keys:
# OPENAI_API_KEY=sk-...
# GEMINI_API_KEY=...  (optional)
```

### 3. Install Security Hooks (Recommended)

```bash
# Prevents accidental commit of secrets
./setup-pre-commit.sh
```

### 4. Start with Docker Compose

```bash
# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f

# Access the application
# Frontend: http://localhost:8080
# Backend API: http://localhost:8002
# API Documentation: http://localhost:8002/docs
```

## 💻 Local Development Setup

### Frontend Development

```bash
cd frontend
npm install
npm run dev

# The frontend will be available at http://localhost:3000
# It proxies API calls to the backend at http://localhost:8002
```

### Backend Development

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the backend server
uvicorn app.main:app --reload --port 8002
```

### Database Setup

The application uses PostgreSQL. With Docker Compose, it's automatically configured. For local development:

```bash
# The Docker Compose setup creates the database automatically
# Connection string: postgresql://ai_curator:secure_password@localhost:5432/ai_curation_db  # pragma: allowlist secret
```

## 🧪 Testing

### Run Backend Tests

```bash
cd backend

# Run all tests
docker exec ai_curation_backend pytest -v

# Run with coverage
docker exec ai_curation_backend pytest --cov=app

# Run specific test categories
docker exec ai_curation_backend pytest tests/contract -v  # Contract tests
docker exec ai_curation_backend pytest tests/integration -v  # Integration tests
```

### Run Frontend Tests

```bash
cd frontend

# Run all tests
npm test

# Run with UI
npm run test:ui

# Run with coverage
npm run test:coverage

# Type checking
npm run type-check

# Linting
npm run lint
```

### Test Status

- **Backend**: 23 tests passing (contract and integration tests)
- **Frontend**: 13 tests passing (component tests)
- Tests requiring API keys are marked with `xfail` and will pass when keys are configured

## 📁 Project Structure

```
agr_ai_curation/
├── frontend/                 # React frontend application
│   ├── src/
│   │   ├── components/      # React components
│   │   │   ├── ChatInterface.tsx        # Main chat UI
│   │   │   ├── StreamingMessage.tsx     # Message display with streaming
│   │   │   ├── ModelSelector.tsx        # AI model selector
│   │   │   └── PdfViewerMultiColorFixed.tsx  # PDF viewer with highlights
│   │   ├── pages/          # Page components
│   │   ├── services/       # API services
│   │   ├── utils/          # Utility functions
│   │   └── test/           # Test setup and utilities
│   └── vitest.config.ts    # Test configuration
├── backend/                 # FastAPI backend application
│   ├── app/
│   │   ├── routers/        # API endpoints
│   │   │   └── chat.py     # Chat endpoints with streaming
│   │   ├── services/       # Business logic
│   │   │   ├── ai_service_factory.py  # AI service manager
│   │   │   ├── openai_service.py      # OpenAI integration
│   │   │   └── gemini_service.py      # Gemini integration
│   │   ├── middleware/     # Custom middleware
│   │   ├── models.py       # SQLAlchemy models
│   │   └── main.py         # Application entry point
│   └── tests/
│       ├── contract/       # API contract tests
│       └── integration/    # Integration tests
├── docker/                  # Docker configuration files
├── docker-compose.yml       # Docker Compose configuration
├── .env.example            # Environment variables template
└── README.md               # This file
```

## 🔧 Configuration

### Environment Variables

Create a `.env` file based on `.env.example`:

```bash
# Required for AI features
OPENAI_API_KEY=sk-...

# Optional - for Gemini models
GEMINI_API_KEY=...

# Optional - for Anthropic Claude (future)
ANTHROPIC_API_KEY=...

# Database (auto-configured with Docker)
DATABASE_URL=postgresql://ai_curator:secure_password@db:5432/ai_curation_db  # pragma: allowlist secret

# Security
SECRET_KEY=your-secret-key-here
```

### API Keys Setup

1. **OpenAI API Key**:
   - Sign up at https://platform.openai.com
   - Generate key at https://platform.openai.com/api-keys
   - Add to `.env` as `OPENAI_API_KEY`

2. **Google Gemini API Key** (Optional):
   - Sign up at https://makersuite.google.com
   - Generate key at https://makersuite.google.com/app/apikey
   - Add to `.env` as `GEMINI_API_KEY`

## 📊 API Endpoints

### Chat Endpoints

- `POST /chat/` - Send message (non-streaming)
- `POST /chat/stream` - Send message (SSE streaming)
- `GET /chat/models` - Get available AI models
- `GET /chat/history/{session_id}` - Get conversation history

### Entity Management

- `GET /entities` - List all entities
- `POST /entities` - Create new entity
- `PUT /entities/{id}` - Update entity
- `DELETE /entities/{id}` - Delete entity

### Document Management

- `POST /documents/upload` - Upload PDF
- `GET /documents/{id}` - Get document
- `POST /documents/{id}/highlight` - Add highlight

### Settings

- `GET /settings` - Get user settings
- `PUT /settings` - Update settings

## 🛡️ Security

### Pre-commit Hooks

The project uses pre-commit hooks to prevent accidental commits of sensitive data:

```bash
# Install hooks (one-time setup)
./setup-pre-commit.sh

# Manual security scan
pre-commit run --all-files

# Check for secrets
detect-secrets scan .
```

### Protected Patterns

- API keys (OpenAI, Gemini, AWS, etc.)
- Private keys and certificates
- Database credentials
- Environment files (except .env.example)
- Large files (>1MB)

## 🐛 Troubleshooting

### Common Issues

1. **Port Already in Use**

   ```bash
   # Find and kill process using port
   lsof -i :8002  # or :3000, :8080
   kill -9 <PID>
   ```

2. **Docker Container Issues**

   ```bash
   # Rebuild containers
   docker-compose down
   docker-compose build --no-cache
   docker-compose up -d
   ```

3. **Database Connection Issues**

   ```bash
   # Reset database
   docker-compose down -v
   docker-compose up -d
   ```

4. **API Key Not Working**
   - Ensure no extra spaces in `.env` file
   - Check key validity on provider's dashboard
   - Restart Docker containers after changing `.env`

5. **Frontend Proxy Issues**
   - Check `vite.config.ts` proxy settings
   - Ensure backend is running on expected port (8002)

## 📈 Performance

- **Streaming Responses**: 100-200ms time to first token
- **Model Switching**: Instant, no reconnection needed
- **Message History**: Paginated, loads 50 messages at a time
- **PDF Rendering**: Optimized with lazy loading
- **Database Queries**: Indexed for session_id and timestamp

## 🚀 Deployment

### Production Build

```bash
# Build frontend
cd frontend
npm run build

# Build Docker images
docker-compose -f docker-compose.prod.yml build

# Deploy
docker-compose -f docker-compose.prod.yml up -d
```

### Environment-specific Configurations

- Development: Hot reload, debug logging, CORS enabled
- Production: Optimized builds, security headers, rate limiting

## 🤝 Contributing

1. Fork the repository
2. Install security hooks: `./setup-pre-commit.sh`
3. Create feature branch: `git checkout -b feature-name`
4. Write tests for new features
5. Ensure tests pass: `npm test` and `pytest`
6. Run security checks: `pre-commit run --all-files`
7. Submit pull request

### Development Guidelines

- Follow TDD approach - write tests first
- Use TypeScript for frontend code
- Follow Python type hints for backend
- Document API changes in OpenAPI schema
- Update this README for significant changes

## 📝 Recent Updates

### Version 2.0.0 (December 2024)

- ✨ Full AI Chat Integration with OpenAI and Gemini
- 🚀 Real-time streaming responses via SSE
- 🎨 Complete UI redesign with Material-UI v5
- 🧪 Comprehensive test suite (Frontend + Backend)
- 🔒 Enhanced security with pre-commit hooks
- 📊 Conversation persistence in PostgreSQL
- 🎯 Model selection and switching
- 🐛 Fixed UI issues (contrast, duplicate text)

## 📜 License

MIT License - See [LICENSE](LICENSE) file for details

## 🙏 Acknowledgments

- Alliance of Genome Resources for project support
- OpenAI for GPT models
- Google for Gemini models
- Material-UI team for component library
- FastAPI team for excellent framework

## 📞 Support

For issues and questions:

- GitHub Issues: https://github.com/alliance-genome/agr_ai_curation/issues
- Documentation: See `/docs` folder
- API Documentation: http://localhost:8002/docs (when running)

---

Built with ❤️ by the Alliance of Genome Resources team
