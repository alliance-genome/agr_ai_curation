from ..database import SessionLocal
from ..models import Settings
from ..config import get_settings
import json


async def initialize_settings():
    """Initialize database settings from .env file on first run"""
    db = SessionLocal()
    try:
        # Check if settings already exist
        existing_settings = db.query(Settings).first()
        if existing_settings:
            return  # Settings already initialized

        # Get settings from .env
        config = get_settings()

        # Define initial settings
        initial_settings = {
            "openai_api_key": config.openai_api_key,
            "anthropic_api_key": config.anthropic_api_key,
            "default_model": config.default_model,
            "max_tokens": str(config.max_tokens),
            "temperature": str(config.temperature),
            "database_url": config.database_url,
            "debug_mode": json.dumps(config.debug_mode),
            "embedding_model_name": config.embedding_model_name,
            "embedding_model_version": config.embedding_model_version,
            "embedding_dimensions": str(config.embedding_dimensions),
            "embedding_max_batch_size": str(config.embedding_max_batch_size),
            "embedding_default_batch_size": str(config.embedding_default_batch_size),
        }

        # Add settings to database
        for key, value in initial_settings.items():
            setting = Settings(key=key, value=value)
            db.add(setting)

        db.commit()
        print("Settings initialized from .env file")
    except Exception as e:
        print(f"Error initializing settings: {e}")
        db.rollback()
    finally:
        db.close()
