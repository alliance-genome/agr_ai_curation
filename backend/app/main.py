from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import logging
import os
from pathlib import Path

from .routers import (
    agents,
    entities,
    settings,
    health,
    test_highlights,
    rag_endpoints,
    pdf_endpoints,
    pdf_data,
)
from .database import engine
from .models import Base  # Import Base from models.py now
from .config import get_settings
from .middleware.error_handler import (
    ErrorHandlingMiddleware,
    RateLimitMiddleware,
    APIKeyValidationMiddleware,
)

logger = logging.getLogger(__name__)

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Alliance AI Curation API", version="0.1.0")

# Add error handling middleware (order matters - error handler should be first)
app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(APIKeyValidationMiddleware)
app.add_middleware(RateLimitMiddleware, max_requests=60, window_seconds=60)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount uploads directory for serving PDFs
uploads_path = Path("uploads")
if uploads_path.exists():
    default_pdf = Path("test_paper.pdf")
    target_pdf = uploads_path / "test_paper.pdf"
    if default_pdf.exists() and not target_pdf.exists():
        try:
            target_pdf.write_bytes(default_pdf.read_bytes())
        except Exception as exc:
            # Log but don't crash app if default copy fails
            logger.warning("Failed to copy default PDF to uploads directory: %s", exc)
    app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(
    agents.router, prefix="/agents", tags=["agents"]
)  # PydanticAI agents
app.include_router(entities.router, prefix="/entities", tags=["entities"])
app.include_router(settings.router, prefix="/settings", tags=["settings"])
app.include_router(test_highlights.router, prefix="/test", tags=["testing"])
app.include_router(rag_endpoints.router)
app.include_router(pdf_endpoints.router)
app.include_router(pdf_data.router)


@app.on_event("startup")
async def startup_event():
    """Initialize database with .env values on first run"""
    from .services.settings_service import initialize_settings

    await initialize_settings()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
