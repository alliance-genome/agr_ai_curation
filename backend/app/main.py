from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from pathlib import Path

from .routers import chat, entities, settings, health, test_highlights
from .database import engine, Base
from .config import get_settings

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Alliance AI Curation API", version="0.1.0")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount uploads directory for serving PDFs
uploads_path = Path("/home/ctabone/ai_curation_data/uploads")
if uploads_path.exists():
    app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")

# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(entities.router, prefix="/entities", tags=["entities"])
app.include_router(settings.router, prefix="/settings", tags=["settings"])
app.include_router(test_highlights.router, prefix="/test", tags=["testing"])

@app.on_event("startup")
async def startup_event():
    """Initialize database with .env values on first run"""
    from .services.settings_service import initialize_settings
    await initialize_settings()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)