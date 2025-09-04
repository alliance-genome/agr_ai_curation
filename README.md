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
   ```

2. **Start with Docker Compose**
   ```bash
   docker-compose up -d
   ```

3. **Access the Application**
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
```

## License

MIT