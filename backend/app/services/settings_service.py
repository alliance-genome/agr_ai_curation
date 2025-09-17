from ..database import SessionLocal
from ..models import Settings
from ..config import get_settings
from sqlalchemy import text
import json


ENUM_VALUES = [
    "PYMUPDF",
    "PDFMINER",
    "OCR",
    "UNSTRUCTURED_FAST",
    "UNSTRUCTURED_HI_RES",
    "UNSTRUCTURED_OCR_ONLY",
]

PDF_CHUNK_COLUMNS = [
    "element_type VARCHAR(50)",
    "is_table BOOLEAN",
    "is_figure BOOLEAN",
]


def ensure_extraction_enum_values(db) -> None:
    for value in ENUM_VALUES:
        db.execute(
            text(
                f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum
        WHERE enumlabel = '{value}'
          AND enumtypid = 'extractionmethod'::regtype
    ) THEN
        ALTER TYPE extractionmethod ADD VALUE '{value}';
    END IF;
END;
$$;
"""
            )
        )
    db.commit()


def ensure_pdf_chunk_columns(db) -> None:
    for column_definition in PDF_CHUNK_COLUMNS:
        db.execute(
            text(
                f"ALTER TABLE pdf_chunks ADD COLUMN IF NOT EXISTS {column_definition};"
            )
        )
    db.commit()


async def initialize_settings():
    """Initialize database settings from .env file on first run"""
    db = SessionLocal()
    try:
        ensure_extraction_enum_values(db)
        ensure_pdf_chunk_columns(db)

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
            "rag_rerank_top_k": str(config.rag_rerank_top_k),
            "rag_confidence_threshold": str(config.rag_confidence_threshold),
            "hybrid_vector_k": str(config.hybrid_vector_k),
            "hybrid_lexical_k": str(config.hybrid_lexical_k),
            "hybrid_max_results": str(config.hybrid_max_results),
            "mmr_lambda": str(config.mmr_lambda),
            "uploads_dir": config.uploads_dir,
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
