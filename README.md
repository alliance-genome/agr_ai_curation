# Alliance AI-Assisted Curation Interface

A three-panel interface for AI-assisted biocuration, built with React (MUI), FastAPI, and PostgreSQL.

## Features

- **PDF Viewer** (Left Panel): Display and navigate research papers with zoom controls
- **AI Chat Interface** (Middle Panel): Interactive chat with AI models for paper analysis
- **Curation Panel** (Right Panel): Entity extraction, annotations, metadata, and configuration
- **Resizable Panels**: Drag to resize panels for optimal workflow
- **Dark/Light Mode**: Toggle between themes
- **Admin Settings**: Configure API keys and model settings

## Architecture

- **Frontend**: React with Material-UI (MUI), Vite build system
- **Backend**: FastAPI (Python) with SQLAlchemy ORM
- **Database**: PostgreSQL 16
- **Containerization**: Docker Compose with persistent volumes

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Node.js 20+ (for local development)
- Python 3.11+ (for local development)

### Setup

1. **Environment Configuration**

   ```bash
   # Copy .env.example to .env and add your API keys
   cp .env.example .env
   # Edit .env and add your OpenAI API key
   ```

2. **Security Setup (Recommended)**

   ```bash
   # Install pre-commit hooks to protect against secrets
   ./setup-pre-commit.sh
   ```

3. **Start with Docker Compose**

   ```bash
   docker-compose up -d
   ```

4. **Access the Application**
   - Frontend: http://localhost:8080
   - Backend API: http://localhost:8002
   - API Docs: http://localhost:8002/docs

### Local Development

#### Frontend

```bash
cd frontend
npm install
npm run dev
# Access at http://localhost:3000
```

#### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8002
```

## Project Structure

```
├── frontend/               # React frontend application
│   ├── src/
│   │   ├── components/    # React components
│   │   ├── pages/        # Page components
│   │   ├── services/     # API services
│   │   └── utils/        # Utility functions
│   └── package.json
├── backend/               # FastAPI backend application
│   ├── app/
│   │   ├── routers/      # API endpoints
│   │   ├── models.py     # Database models
│   │   ├── database.py   # Database configuration
│   │   └── main.py       # Application entry point
│   └── requirements.txt
├── docker/               # Docker configuration files
│   ├── Dockerfile.frontend
│   ├── Dockerfile.backend
│   ├── nginx.conf
│   └── init.sql
├── docker-compose.yml    # Docker Compose configuration
└── README.md

```

## Persistent Data

Data is stored in your home directory to persist across container restarts:

- PostgreSQL data: `~/ai_curation_data/postgres_data/`
- Uploaded PDFs: `~/ai_curation_data/uploads/`
- Database backups: `~/ai_curation_data/postgres_backups/`

## API Endpoints

- `GET /health` - Health check
- `POST /chat` - Send chat messages
- `GET /entities` - List entities
- `POST /entities` - Create entity
- `DELETE /entities/{id}` - Delete entity
- `GET /settings` - Get settings
- `PUT /settings` - Update settings

## Technologies

- **Frontend**: React, Material-UI, PDF.js, React Router, Axios
- **Backend**: FastAPI, SQLAlchemy, Pydantic, OpenAI SDK
- **Database**: PostgreSQL, Alembic (migrations)
- **DevOps**: Docker, Docker Compose, Nginx

## Security

This project includes comprehensive security measures to protect against accidentally committing sensitive data:

### 🛡️ **Automatic Protection**

- **API Key Detection** - Prevents OpenAI, Anthropic, AWS, and other API keys from being committed
- **Environment File Protection** - Blocks `.env` files (use `.env.example` for templates)
- **Private Key Detection** - Prevents SSH keys, certificates, and other cryptographic material
- **Database Security** - Blocks SQL dumps and database files that might contain sensitive data
- **Large File Protection** - Prevents files over 1MB from being committed

### 🚀 **Quick Security Setup**

```bash
# Install security hooks (one-time setup)
./setup-pre-commit.sh

# Test the protection
pre-commit run --all-files
```

### 🔍 **Security Tools Used**

- **[detect-secrets](https://github.com/Yelp/detect-secrets)** - Comprehensive secret detection
- **[gitleaks](https://github.com/gitleaks/gitleaks)** - Git-focused secret scanning
- **[pre-commit](https://pre-commit.com/)** - Git hook framework
- **Custom Rules** - Tailored patterns for this project

### 📋 **Security Best Practices**

```bash
# ✅ DO: Use environment variables
# .env (never commit this)
OPENAI_API_KEY=sk-real-key-here

# .env.example (safe to commit)
OPENAI_API_KEY=your_openai_key_here

# ❌ DON'T: Hardcode secrets in source files
const API_KEY = "sk-real-key-here";  // NEVER!
```

For detailed security information, see [SECURITY.md](SECURITY.md).

## Development Commands

```bash
# Frontend
npm run dev          # Start development server
npm run build        # Build for production
npm run lint         # Run ESLint
npm run type-check   # TypeScript validation

# Backend
uvicorn app.main:app --reload  # Start with hot reload
pytest                          # Run tests
alembic upgrade head           # Run database migrations

# Docker
docker-compose up -d           # Start all services
docker-compose down            # Stop all services
docker-compose logs -f         # View logs
docker-compose build          # Rebuild images

# Security
pre-commit run --all-files     # Run all security checks
detect-secrets scan .          # Scan for secrets
```

## Sample Data

The application comes with a sample fly publication PDF that loads by default to demonstrate the curation interface. The sample paper is automatically available at startup.

## Contributing

1. **Fork the repository**
2. **Install security hooks**: `./setup-pre-commit.sh`
3. **Create a feature branch**: `git checkout -b feature-name`
4. **Make your changes** (hooks will automatically check for security issues)
5. **Test your changes**: `pre-commit run --all-files`
6. **Submit a pull request**

**Important**: The security hooks will prevent commits containing secrets. This protects both you and the project!

## License

MIT
