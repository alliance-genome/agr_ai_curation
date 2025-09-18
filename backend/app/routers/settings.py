from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Dict, Any
from ..database import get_db
from app.models import Settings as SettingsModel
from ..config import get_settings as get_app_settings
from app.services.embedding_service_factory import get_embedding_service
import json

SECRET_MASK = "************"
SECRET_FIELDS = {"openai_api_key", "anthropic_api_key"}
SECRET_PLACEHOLDER_VALUES = {"your_anthropic_api_key_here"}


def _coerce_value(key: str, value: Any) -> Any:
    if value is None:
        return None

    if key in {
        "max_tokens",
        "embedding_dimensions",
        "embedding_max_batch_size",
        "embedding_default_batch_size",
        "ontology_embedding_dimensions",
        "ontology_embedding_max_batch_size",
        "ontology_embedding_batch_size",
        "rag_rerank_top_k",
        "hybrid_vector_k",
        "hybrid_lexical_k",
        "hybrid_max_results",
    }:
        try:
            return int(value)
        except (ValueError, TypeError):
            return value

    if key in {"temperature", "rag_confidence_threshold", "mmr_lambda"}:
        try:
            return float(value)
        except (ValueError, TypeError):
            return value

    if key in {"debug_mode"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes"}
    return value


router = APIRouter()


class SettingsUpdate(BaseModel):
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    default_model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    database_url: str | None = None
    debug_mode: bool | None = None
    embedding_model_name: str | None = None
    embedding_model_version: str | None = None
    embedding_dimensions: int | None = None
    embedding_max_batch_size: int | None = None
    embedding_default_batch_size: int | None = None
    ontology_embedding_model_name: str | None = None
    ontology_embedding_model_version: str | None = None
    ontology_embedding_batch_size: int | None = None
    ontology_embedding_dimensions: int | None = None
    ontology_embedding_max_batch_size: int | None = None
    rag_rerank_top_k: int | None = None
    rag_confidence_threshold: float | None = None
    hybrid_vector_k: int | None = None
    hybrid_lexical_k: int | None = None
    hybrid_max_results: int | None = None
    mmr_lambda: float | None = None
    uploads_dir: str | None = None
    pdf_extraction_strategy: str | None = None


@router.get("/")
async def get_settings(db: Session = Depends(get_db)):
    """Get all settings"""
    settings = db.query(SettingsModel).all()
    settings_dict = {}

    for setting in settings:
        try:
            # Try to parse as JSON first (for complex values)
            settings_dict[setting.key] = json.loads(setting.value)
        except:
            # Otherwise use as string
            settings_dict[setting.key] = setting.value

    config = get_app_settings()

    defaults = {
        "openai_api_key": config.openai_api_key,
        "anthropic_api_key": config.anthropic_api_key,
        "default_model": config.default_model,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "database_url": config.database_url,
        "debug_mode": config.debug_mode,
        "embedding_model_name": getattr(
            config, "embedding_model_name", "text-embedding-3-small"
        ),
        "embedding_model_version": getattr(config, "embedding_model_version", "1.0"),
        "embedding_dimensions": getattr(config, "embedding_dimensions", 1536),
        "embedding_max_batch_size": getattr(config, "embedding_max_batch_size", 128),
        "embedding_default_batch_size": getattr(
            config, "embedding_default_batch_size", 64
        ),
        "ontology_embedding_model_name": (
            getattr(config, "ontology_embedding_model_name", "")
            or getattr(config, "embedding_model_name", "text-embedding-3-small")
        ),
        "ontology_embedding_model_version": (
            getattr(config, "ontology_embedding_model_version", "")
            or getattr(config, "embedding_model_version", "1.0")
        ),
        "ontology_embedding_batch_size": (
            getattr(config, "ontology_embedding_batch_size", 0)
            or getattr(config, "embedding_default_batch_size", 64)
        ),
        "ontology_embedding_dimensions": (
            getattr(config, "ontology_embedding_dimensions", 0)
            or getattr(config, "embedding_dimensions", 1536)
        ),
        "ontology_embedding_max_batch_size": (
            getattr(config, "ontology_embedding_max_batch_size", 0)
            or getattr(config, "embedding_max_batch_size", 128)
        ),
        "rag_rerank_top_k": getattr(config, "rag_rerank_top_k", 5),
        "rag_confidence_threshold": getattr(config, "rag_confidence_threshold", 0.2),
        "hybrid_vector_k": getattr(config, "hybrid_vector_k", 50),
        "hybrid_lexical_k": getattr(config, "hybrid_lexical_k", 50),
        "hybrid_max_results": getattr(config, "hybrid_max_results", 100),
        "mmr_lambda": getattr(config, "mmr_lambda", 0.7),
        "uploads_dir": getattr(config, "uploads_dir", "/tmp/uploads"),
        "pdf_extraction_strategy": getattr(config, "pdf_extraction_strategy", "fast"),
    }

    response: Dict[str, Any] = {}
    for key, default_value in defaults.items():
        raw_value = settings_dict.get(key)
        if raw_value in (None, ""):
            raw_value = default_value

        if key in SECRET_FIELDS:
            if isinstance(raw_value, str) and raw_value in SECRET_PLACEHOLDER_VALUES:
                raw_value = ""

            has_value = bool(raw_value)
            response[key] = SECRET_MASK if has_value else ""
            response[f"{key}_masked"] = has_value
        else:
            response[key] = _coerce_value(key, raw_value)

    return response


@router.put("/")
async def update_settings(settings: SettingsUpdate, db: Session = Depends(get_db)):
    """Update settings"""
    try:
        settings_data = settings.dict(exclude_unset=True)

        for key, value in settings_data.items():
            if key in SECRET_FIELDS and isinstance(value, str) and value == SECRET_MASK:
                # Skip masked secrets when unchanged
                continue

            # Convert non-string values to JSON
            if not isinstance(value, str):
                value = json.dumps(value)

            # Check if setting exists
            existing = db.query(SettingsModel).filter(SettingsModel.key == key).first()

            if existing:
                existing.value = value
            else:
                new_setting = SettingsModel(key=key, value=value)
                db.add(new_setting)

        db.commit()
        try:
            get_embedding_service.cache_clear()  # type: ignore[attr-defined]
        except AttributeError:
            # If cache_clear is unavailable, ignore; service will be rebuilt on restart.
            pass
        return {"message": "Settings updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
